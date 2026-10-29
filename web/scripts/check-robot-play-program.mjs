import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive } from 'vue'
import { readFileSync } from 'node:fs'
import { parse,compileScript,compileTemplate,compileStyle } from '@vue/compiler-sfc'
import { createRobotPlayController,robotPlayRequest,tcpWaitPose,playPointTab,robotPlayFor } from '../src/robotPlayProgram.js'
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const flush=()=>new Promise(resolve=>setImmediate(resolve))
const world=()=>reactive({connected:true,snap:{session_id:'s1',mode:'sim',real:false,moving:false,exec:{state:'idle'},objects:[{id:'piece1',task:{group:'group1'}}]}})
const params={group:'group1',action:'sorting',clearance:100,speed:10}
const plan=()=>({ok:true,plan_id:'validated-1',source:'SPEED 10\nWAIT 0.5',points:[{name:'WAIT',pose:[0,0,700,0,0,0],role:'wait'}]})
function fixture(options={}){
  const w=world(),calls=[]
  const request=async(action,payload,opts)=>{calls.push([action,payload,opts]);return action==='generate'?{...plan(),...(payload.source?{plan_id:'validated-edit',source:payload.source}:{})}:{ok:true,active:false}}
  const c=createRobotPlayController(w,{request,...options});return {w,c,calls}
}
test('generating never runs or changes scene; running requires explicit affirmative confirmation',async()=>{
  const {w,c,calls}=fixture(),before=JSON.stringify(w.snap)
  assert.equal(await c.generate(params),true);assert.equal(c.state.plan.plan_id,'validated-1');assert.equal(JSON.stringify(w.snap),before)
  assert.deepEqual(calls.map(x=>x[0]),['generate'])
  assert.equal(await c.execute(()=>false),false);assert.equal(calls.length,1)
  c.state.source+='\nWAIT 1';assert.equal(await c.execute(()=>true),false)
  await c.recheck();assert.equal(c.state.plan.plan_id,'validated-edit');assert.equal(await c.execute(()=>true),true)
  assert.deepEqual(calls[2].slice(0,2),['run',{plan_id:'validated-edit',source:'SPEED 10\nWAIT 0.5\nWAIT 1'}]);assert.equal(c.running.value,true)
  assert.equal(await c.execute(()=>true),false);assert.equal(calls.length,3)
})
test('real, disconnected, moving or following sessions do not plan or run',async()=>{
  for(const patch of [{mode:'real'},{real:true},{moving:true},{hand_follow:{active:true}},{exec:{state:'paused'}}]){
    const {w,c,calls}=fixture();Object.assign(w.snap,patch);assert.equal(await c.generate(params),false);assert.deepEqual(calls,[])
  }
  const {w,c,calls}=fixture();w.connected=false;assert.equal(await c.generate(params),false);assert.deepEqual(calls,[])
})
test('invalid chess slots never call planner; all legal slots use planning only',async()=>{
  for(const slot of [-1,9,1.5,undefined]){const {c,calls}=fixture();assert.equal(await c.generate({...params,action:'chess',slot}),false);assert.deepEqual(calls,[])}
  for(let slot=0;slot<9;slot++){const {c,calls}=fixture();await c.generate({...params,action:'chess',slot});assert.equal(calls[0][1].slot,slot);assert.equal(calls[0][0],'generate')}
})
test('failed or malformed planning invalidates old executable plan and exposes server why',async()=>{
  for(const response of [{ok:true,source:'',plan_id:'x',points:[]},{ok:true,source:'MOVEJ',points:[]},null]){
    const {w}=fixture();const c=createRobotPlayController(w,{request:async()=>response});assert.equal(await c.generate(params),false);assert.equal(c.state.plan,null)
  }
  const {w}=fixture();const c=createRobotPlayController(w,{request:async()=>{throw Error('A 上位不可达')}});await c.generate(params);assert.match(c.state.message,/A 上位不可达/)
})
test('cancel precheck calls its dedicated API, rejects late plans and waits for confirmed idle without motion stop or run',async t=>{
  const task=deferred(),cancellation=deferred(),readbacks=[],calls=[],w=world()
  const c=createRobotPlayController(w,{request:(...args)=>{
    calls.push(args)
    if(args[0]==='generate')return task.promise
    if(args[0]==='cancel')return cancellation.promise
    assert.equal(args[0],'state','precheck cancellation must never stop or execute motion')
    const readback=deferred();readbacks.push(readback);return readback.promise
  },schedule:()=>1,cancel:()=>{}})
  const pending=c.generate(params);assert.equal(await c.generate(params),false)
  const cancelling=c.cancelWaiting()
  assert.equal(calls[0][2].signal.aborted,true);assert.equal(c.state.planning,false)
  assert.deepEqual(calls[1].slice(0,2),['cancel',{}])
  assert.equal(c.state.cancelPending,true);assert.equal(c.busy.value,true)
  cancellation.resolve({ok:true,planning:true,message:'已请求取消预检'})
  assert.equal(await cancelling,true);assert.equal(c.busy.value,true)
  task.resolve(plan());assert.equal(await pending,false);assert.equal(c.state.plan,null)
  assert.equal(await c.execute(()=>true),false);assert.equal(await c.generate(params),false)
  const unsubscribe=c.subscribe();t.after(unsubscribe)
  readbacks[0].resolve({ok:true,planning:false,active:false,message:'预检已取消'});await flush()
  assert.equal(c.state.cancelPending,false);assert.equal(c.busy.value,false);assert.equal(c.state.plan,null)
  assert.deepEqual(calls.map(x=>x[0]),['generate','cancel','state'])
})
test('session changes during planning or before execution require regeneration',async()=>{
  const task=deferred(),w=world(),c=createRobotPlayController(w,{request:()=>task.promise})
  const pending=c.generate(params);w.snap.session_id='new';task.resolve(plan());await pending;assert.equal(c.state.plan,null)
  const f=fixture();await f.c.generate(params);f.w.snap.session_id='new';assert.equal(await f.c.execute(()=>true),false);assert.equal(f.calls.length,1)
})
test('deleted group and condition change during confirmation cannot execute',async()=>{
  const f=fixture();await f.c.generate(params);f.w.snap.objects=[];assert.equal(await f.c.execute(()=>true),false)
  f.w.snap.objects=[{task:{group:'group1'}}]
  assert.equal(await f.c.execute(()=>{f.w.snap.mode='real';return true}),false);assert.equal(f.calls.length,1)
})
test('run failure retains uncertain status to prevent repeated submission until readback',async()=>{
  const w=world();let runCount=0
  const c=createRobotPlayController(w,{request:async action=>{if(action==='generate')return plan();runCount++;throw new Error('超时')}})
  await c.generate(params);assert.equal(await c.execute(()=>true),false);assert.equal(c.busy.value,true)
  assert.equal(await c.execute(()=>true),false);assert.equal(runCount,1);assert.match(c.state.message,/勿重复提交/)
})
test('stop is explicit and remains available while running, without claiming immediate stop',async()=>{
  const {c,calls}=fixture();c.state.active=true;await c.stop();assert.deepEqual(calls[0].slice(0,2),['stop',{}]);assert.equal(c.running.value,true);assert.match(c.state.message,/等待服务端确认/)
})

test('pause and continue share the same control and cancel permanently invalidates the old plan',async()=>{
  const {c,calls}=fixture();c.state.active=true;c.state.plan=plan()
  assert.equal(await c.togglePause(),true);assert.equal(c.state.execution.state,'paused')
  assert.equal(await c.togglePause(),true);assert.equal(c.state.execution.state,'running')
  await c.stop();assert.equal(c.state.plan,null);assert.equal(c.state.cancelled,true)
  const count=calls.length;assert.equal(await c.togglePause(),false);assert.equal(calls.length,count)
  assert.deepEqual(calls.map(row=>row[0]),['pause','resume','stop'])
})
test('polling is serial, stale pre-run readback cannot unlock controls, and teardown aborts/cleans timers',async()=>{
  const w=world(),pending=[],timers=new Map();let n=0
  const request=(action)=>{if(action==='state'){const task=deferred();pending.push(task);return task.promise}return Promise.resolve(action==='generate'?plan():{ok:true})}
  const c=createRobotPlayController(w,{request,schedule:fn=>{timers.set(++n,fn);return n},cancel:id=>timers.delete(id)})
  const remove=c.subscribe();assert.equal(pending.length,1)
  await c.generate(params);await c.execute(()=>true);assert.equal(c.state.active,true)
  pending[0].resolve({ok:true,active:false});await flush();assert.equal(c.state.active,true,'old readback must not clear newer run')
  assert.equal(timers.size,1);const [id,fn]=[...timers][0];timers.delete(id);const tick=fn();assert.equal(pending.length,2)
  pending[1].resolve({ok:true,active:false});await tick;assert.equal(c.running.value,false)
  remove();assert.equal(timers.size,0);await fn();assert.equal(pending.length,2)
})
test('subscriber cleanup ignores late readback and controller is reused across remount',async()=>{
  const task=deferred(),w=world(),calls=[]
  const c=createRobotPlayController(w,{request:(...args)=>{calls.push(args);return task.promise}})
  const remove=c.subscribe();remove();assert.equal(calls[0][2].signal.aborted,true)
  task.resolve({ok:true,active:true});await flush();assert.equal(c.state.active,false)
  assert.equal(robotPlayFor(w),robotPlayFor(w));assert.notEqual(robotPlayFor(w),robotPlayFor(world()))
})
test('TCP wait pose follows actual xyz_mm/rpy_deg contract without invented coordinates',()=>{
  const tcp={xyz_mm:[1,2,3],rpy_deg:[4,5,6]};assert.deepEqual(tcpWaitPose(tcp),[1,2,3,4,5,6])
  const copied=tcpWaitPose(tcp);copied[0]=99;assert.equal(tcp.xyz_mm[0],1)
  for(const invalid of [null,{},[1,2,3,4,5,6],{xyz_mm:[1,2,NaN],rpy_deg:[4,5,6]}])assert.throws(()=>tcpWaitPose(invalid))
})
test('point roles retain separate wait, material, placement and upper positions; unknown roles stay visible',()=>{
  for(const role of ['wait','a_pick','a_up','b_place','b_up'])assert.equal(playPointTab(role),role)
  assert.equal(playPointTab('pick_above'),'a_up');assert.equal(playPointTab('place_approach'),'b_up');assert.equal(playPointTab('custom'),'other')
  assert.deepEqual(['wait','pick_up','pick','place_up','place'].map(playPointTab),['wait','a_up','a_pick','b_up','b_place'])
})
test('only fixed program endpoints are used; generate timeout supports full 60s planning and external abort',async()=>{
  const calls=[],fetcher=async(...args)=>{calls.push(args);return {ok:true,json:async()=>({ok:true})}}
  await robotPlayRequest('generate',params,{fetcher});await robotPlayRequest('state',undefined,{fetcher});await robotPlayRequest('stop',{}, {fetcher})
  assert.deepEqual(calls.map(x=>x[0]),['/api/play/program','/api/play/program/state','/api/motion/cancel'])
  assert.equal(calls[1][1].method,'GET');assert.equal(calls[1][1].body,undefined)
  await assert.rejects(robotPlayRequest('https://evil',{}, {fetcher}),/无效/)
  await assert.rejects(robotPlayRequest('generate',params,{fetcher:async()=>({ok:false,json:async()=>({ok:false,why:'不可达'})})}),/不可达/)
  const abort=new AbortController();abort.abort()
  await assert.rejects(robotPlayRequest('generate',params,{signal:abort.signal,fetcher:async(url,options)=>{options.signal.throwIfAborted()}}),error=>error.name==='AbortError')
})
test('planning has 105s budget but status, stop and run retain short bounded waits',async t=>{
  const timeouts=[];t.mock.method(AbortSignal,'timeout',ms=>{timeouts.push(ms);return new AbortController().signal})
  const fetcher=async()=>({ok:true,json:async()=>({ok:true})})
  for(const action of ['generate','state','stop','run'])await robotPlayRequest(action,{}, {fetcher})
  assert.deepEqual(timeouts,[105000,5000,10000,15000])
})
test('recheck submits unchanged original parameters plus edited code and never executes automatically',async()=>{
  const {c,calls}=fixture(),parameters={...params,action:'chess',slot:4,wait:[400,0,450,180,0,0]}
  await c.generate(parameters);c.state.source='SPEED 15\nWAIT 2';await c.recheck()
  assert.deepEqual(calls[1].slice(0,2),['generate',{...parameters,source:'SPEED 15\nWAIT 2'}])
  assert.equal(c.state.plan.plan_id,'validated-edit');assert.equal(c.state.source,'SPEED 15\nWAIT 2');assert.equal(calls.some(value=>value[0]==='run'),false)
})
test('failed recheck preserves edited code for repair and removes executable old plan',async()=>{
  const w=world();let count=0
  const c=createRobotPlayController(w,{request:async()=>{if(count++)throw new Error('修改程序不可达');return plan()}})
  await c.generate(params);c.state.source='BROKEN';assert.equal(await c.recheck(),false)
  assert.equal(c.state.source,'BROKEN');assert.equal(c.state.plan,null);assert.match(c.state.message,/不可达/)
  assert.equal(await c.execute(()=>true),false)
})
test('state readback retains backend planning, held object, vacuum and execution without replacing edited plan',async()=>{
  const w=world(),c=createRobotPlayController(w,{request:async action=>action==='generate'?plan():{ok:true,planning:true,active:false,held:'piece1',vacuum:{vacuum:true,blow:false},message:'处理中',execution:{state:'waiting'}},schedule:()=>1,cancel:()=>{}})
  await c.generate(params);c.state.source='edited';const unsubscribe=c.subscribe();await flush()
  assert.equal(c.state.serverPlanning,true);assert.equal(c.busy.value,true);assert.equal(c.state.held,'piece1');assert.equal(c.state.vacuum.vacuum,true)
  assert.equal(c.state.execution.state,'waiting');assert.equal(c.state.plan.plan_id,'validated-1');assert.equal(c.state.source,'edited');unsubscribe()
})
test('RobotPlayProgram compiles all template/style and includes editable source and explicit execution',()=>{
  const source=readFileSync(new URL('../src/components/RobotPlayProgram.vue',import.meta.url),'utf8'),{descriptor,errors}=parse(source)
  assert.deepEqual(errors,[]);const script=compileScript(descriptor,{id:'robot-play-program'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'RobotPlayProgram.vue',id:'robot-play-program',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'robot-play-program',scoped:style.scoped}).errors,[])
  assert.match(source,/<textarea v-model="s.source"/);assert.match(source,/执行程序/)
  assert.match(source,/@click="controller.cancelWaiting"[^>]*>[^<]*取消预检/)
  assert.match(source,/@click="controller.stop">取消执行/)
  assert.doesNotMatch(source,/取消等待（非停止）/)
})
