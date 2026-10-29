// Offline: injected requests and clocks only; no HTTP, device or backend startup.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive } from 'vue'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { createRobotPlayController, robotPlayRequest, tcpWaitPose, playPointTab } from '../src/robotPlayProgram.js'

const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const flush=()=>new Promise(resolve=>setImmediate(resolve))
const parameters={group:'pieces',action:'sorting',clearance:120,speed:15}
const plan={ok:true,plan_id:'offline-plan',source:'SPEED 15',points:[]}
function fixture(t){
  const calls=[],queues=new Map(),timers=new Map()
  let id=0
  const w=reactive({connected:true,snap:{session_id:'offline',mode:'sim',moving:false,exec:{state:'idle'},objects:[{task:{group:'pieces'}}]}})
  const c=createRobotPlayController(w,{request:(action,payload,options)=>{
    const task=deferred();calls.push({action,payload,options,task})
    if(!queues.has(action))queues.set(action,[])
    queues.get(action).push(task);return task.promise
  },schedule:fn=>{timers.set(++id,fn);return id},cancel:id=>timers.delete(id)})
  let unsubscribe
  t.after(()=>unsubscribe?.())
  return {c,w,calls,timers,
    take:action=>{const task=queues.get(action)?.shift();assert.ok(task,action+' request missing');return task},
    subscribe(){unsubscribe=c.subscribe();return unsubscribe},
    async tick(){const [key,fn]=[...timers][0] || [];assert.ok(fn);timers.delete(key);fn();await flush()},
  }
}

test('cancel uses server cancellation, blocks reuse until readback, and ignores late plans',async t=>{
  const h=fixture(t),pending=h.c.generate(parameters),generation=h.take('generate')
  const cancellation=h.c.cancelWaiting(),cancel=h.take('cancel')
  assert.equal(h.calls[0].options.signal.aborted,true)
  assert.equal(h.c.state.planning,false);assert.equal(h.c.busy.value,true)
  generation.resolve(plan);assert.equal(await pending,false);assert.equal(h.c.state.plan,null)
  cancel.resolve({ok:true});assert.equal(await cancellation,true)
  assert.equal(h.c.state.cancelPending,true);assert.equal(h.c.busy.value,true)
  assert.equal(await h.c.generate(parameters),false);assert.equal(await h.c.execute(()=>true),false)
  h.subscribe();h.take('state').resolve({ok:true,planning:false,active:false,message:'预检已取消'})
  await flush();assert.equal(h.c.busy.value,false);assert.equal(h.c.state.cancelPending,false)
  assert.deepEqual(h.calls.map(c=>c.action),['generate','cancel','state'])
})

test('failed cancel stays pending and busy; retry cannot stop or execute motion',async t=>{
  const h=fixture(t);h.subscribe()
  h.take('state').resolve({ok:true,planning:true,can_cancel:true});await flush()
  const request=h.c.cancelWaiting();h.take('cancel').reject(new Error('取消接口超时'))
  assert.equal(await request,false);assert.equal(h.c.busy.value,true)
  assert.match(h.c.state.message,/取消接口超时.*待确认/)
  await h.tick();h.take('state').resolve({ok:true,planning:true});await flush()
  assert.equal(h.c.state.cancelPending,true)
  const retry=h.c.cancelWaiting();h.take('cancel').resolve({ok:true});await retry
  await h.tick();h.take('state').resolve({ok:true,planning:false});await flush()
  assert.equal(h.c.busy.value,false)
  assert.ok(h.calls.every(call=>['state','cancel'].includes(call.action)))
})

test('restored server planning shows progress and never reissues generation',async t=>{
  const h=fixture(t);h.subscribe()
  h.take('state').resolve({ok:true,planning:true,progress:{phase:'planning',step:3,total:12,line:8,kind:'MOVEL'},elapsed_s:4.25,timeout_s:90,can_cancel:true,message:'校验第 8 行'})
  await flush()
  assert.deepEqual(h.c.state.progress,{phase:'planning',step:3,total:12,line:8,kind:'MOVEL'})
  assert.equal(h.c.state.elapsed_s,4.25);assert.equal(h.c.state.timeout_s,90)
  assert.equal(h.c.state.serverMessage,'校验第 8 行');assert.equal(h.c.prechecking.value,true)
  assert.equal(await h.c.generate(parameters),false)
  await h.tick();h.take('state').resolve({ok:true,planning:true,elapsed_s:5.1,progress:{phase:'validating',step:4,total:12}});await flush()
  assert.equal(h.c.state.elapsed_s,5.1)
  assert.deepEqual(h.calls.map(call=>call.action),['state','state'])
})

test('old state fields remain compatible but absent planning cannot confirm cancellation',async t=>{
  const h=fixture(t);h.subscribe()
  h.take('state').resolve({ok:true,active:false});await flush()
  assert.equal(h.c.busy.value,false);assert.equal(h.c.state.progress,null)
  assert.equal(h.c.state.can_cancel,null);assert.equal(h.c.state.elapsed_s,null)
  h.c.state.serverPlanning=true
  const request=h.c.cancelWaiting();h.take('cancel').resolve({ok:true});await request
  await h.tick();h.take('state').resolve({ok:true,active:false});await flush()
  assert.equal(h.c.busy.value,true);assert.equal(h.c.state.cancelPending,true)
  await h.tick();h.take('state').resolve({ok:true,planning:false,active:true,held:'part',vacuum:{vacuum:true},execution:{state:'paused'}});await flush()
  assert.equal(h.c.state.cancelPending,false);assert.equal(h.c.state.held,'part')
  assert.equal(h.c.state.vacuum.vacuum,true);assert.equal(h.c.running.value,true);assert.equal(h.c.busy.value,true)
})

test('pre-cancel readback and readback during cancellation cannot falsely unlock',async t=>{
  const h=fixture(t);h.c.state.serverPlanning=true;h.subscribe()
  const stale=h.take('state'),cancellation=h.c.cancelWaiting()
  stale.resolve({ok:true,planning:false});await flush()
  assert.equal(h.c.state.cancelPending,true)
  await h.tick();h.take('state').resolve({ok:true,planning:false});await flush()
  assert.equal(h.c.state.cancelPending,true)
  h.take('cancel').resolve({ok:true});await cancellation
  await h.tick();h.take('state').resolve({ok:true,planning:false});await flush()
  assert.equal(h.c.busy.value,false)
})

test('generation transport timeout keeps backend busy pending confirmation',async t=>{
  const h=fixture(t),pending=h.c.generate(parameters)
  h.take('generate').reject(new Error('预检等待超时'));assert.equal(await pending,false)
  assert.equal(h.c.busy.value,true);assert.equal(h.c.state.planningUncertain,true)
  h.subscribe();h.take('state').reject(new Error('状态网络故障'));await flush()
  assert.equal(h.c.busy.value,true)
  await h.tick();h.take('state').resolve({ok:true,planning:false});await flush()
  assert.equal(h.c.busy.value,false)
})

test('can_cancel false prevents request but invalidation still rejects a late plan',async t=>{
  const h=fixture(t),pending=h.c.generate(parameters)
  h.c.state.can_cancel=false
  assert.equal(await h.c.cancelWaiting(),false)
  h.c.invalidate();h.take('generate').resolve(plan)
  assert.equal(await pending,false);assert.equal(h.c.state.plan,null);assert.equal(h.c.state.source,'')
  assert.equal(h.c.busy.value,true);assert.deepEqual(h.calls.map(call=>call.action),['generate'])
})

test('cancel request whitelist and detail errors propagate without a motion stop endpoint',async()=>{
  const calls=[]
  await robotPlayRequest('cancel',{}, {fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({ok:true})}}})
  assert.equal(calls[0].url,'/api/play/program/cancel');assert.equal(calls[0].options.method,'POST')
  assert.equal(calls[0].options.body,'{}')
  for(const detail of ['预检不能取消',{reason:'取消被拒绝'}]){
    await assert.rejects(robotPlayRequest('cancel',{}, {fetcher:async()=>({ok:false,json:async()=>({detail})})}),error=>error.message===(typeof detail==='string'?detail:JSON.stringify(detail)) && error.serverRejected===true)
  }
  await assert.rejects(robotPlayRequest('other',{}),/接口无效/)
})

test('progress component compiles, offers independent cancellation and renders unknown duration honestly',()=>{
  const source=readFileSync(new URL('../src/components/RobotPlayProgram.vue',import.meta.url),'utf8')
  const {descriptor,errors}=parse(source);assert.deepEqual(errors,[])
  const script=compileScript(descriptor,{id:'play-progress'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'RobotPlayProgram.vue',id:'play-progress',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'play-progress',scoped:style.scoped}).errors,[])
  assert.match(source,/@click="controller.cancelWaiting"/);assert.match(source,/@click="controller.stop">取消执行/)
  assert.match(source,/<progress /);assert.match(source,/等待服务端报告耗时/);assert.match(source,/取消预检待确认/)
  assert.doesNotMatch(source,/取消等待（非停止）/)
})

test('actual component renders progress, elapsed time, cancel failure and separate execution control',async t=>{
  const h=fixture(t)
  Object.assign(h.c.state,{serverPlanning:true,progress:{phase:'planning',step:3,total:12,line:8,kind:'MOVEL'},elapsed_s:4.25,timeout_s:90,serverMessage:'正在校验可达性'})
  const source=readFileSync(new URL('../src/components/RobotPlayProgram.vue',import.meta.url),'utf8')
  const {descriptor}=parse(source),script=compileScript(descriptor,{id:'render-progress'})
  const code=script.content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
  const dependencies={...Vue,useWorld:()=>h.w,robotPlayFor:()=>h.c,tcpWaitPose,playPointTab,
    TaskTabs:{render:()=>null},onMounted(){},onBeforeUnmount(){},onActivated(){},onDeactivated(){}}
  const names=Object.keys(dependencies).filter(name=>name!=='default' && /^[A-Za-z_$][\w$]*$/.test(name))
  const component=new Function(...names,code)(...names.map(name=>dependencies[name]))
  const template=compileTemplate({source:descriptor.template.content,filename:'RobotPlayProgram.vue',id:'render-progress',compilerOptions:{mode:'function',bindingMetadata:script.bindings}})
  component.render=new Function('Vue',template.code)(Vue)
  const render=()=>renderToString(Vue.createSSRApp(component,{group:'pieces',action:'sorting'}))
  let html=await render()
  for(const expected of ['规划路径','步骤 3 / 12','第 8 行','MOVEL','已耗时 4.3 秒','预检时限 90 秒','正在校验可达性','取消预检','取消执行'])assert.ok(html.includes(expected),expected)
  assert.match(html,/<progress[^>]*value="3"[^>]*max="12"/)
  const cancellation=h.c.cancelWaiting();h.take('cancel').reject(new Error('网络断开'));await cancellation
  html=await render();assert.ok(html.includes('取消预检待确认'));assert.ok(html.includes('网络断开'));assert.ok(html.includes('重试取消预检'))
  assert.match(html,/<button[^>]*disabled[^>]*>执行程序<\/button>/)
  assert.ok(!h.calls.some(call=>['run','stop'].includes(call.action)))
})
