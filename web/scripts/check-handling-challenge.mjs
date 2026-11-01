import {renderToString} from '@vue/server-renderer'
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {parseResponse} from '../src/connectionState.js'
import {playAllowed} from '../src/functionPlay.js'
const source=readFileSync(new URL('../src/components/HandlingChallenge.vue',import.meta.url),'utf8')
const reply=value=>({ok:true,status:200,json:async()=>value})
const state=phase=>({ok:true,phase,challenge_id:'course-current',plan_id:phase==='checked'?'checked-current':null,group:'g',level:1,completed:0,total:1,elapsed_s:0,score:0,held:null,levels:[{id:1,name:'基础搬运',pieces:1,obstacles:0}],results:[]})
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function fixture(fetch) {
  const hooks=[],mounted=[],timers=new Map()
  const world=Vue.reactive({connected:true,error:'',snap:{mode:'sim',session_id:'offline-handling',real:null,moving:false,exec:{state:'idle'},hand_follow:{active:false}}})
  const deps={...Vue,useWorld:()=>world,parseResponse,playAllowed,window:{confirm:()=>true},fetch,
    onMounted:fn=>mounted.push(fn),onBeforeUnmount:fn=>hooks.push(fn),onDeactivated:fn=>hooks.push(fn),
    setTimeout:fn=>{const id=timers.size+1;timers.set(id,fn);return id},clearTimeout:id=>timers.delete(id)}
  const code=compileScript(parse(source).descriptor,{id:'handling'}).content.replace(/^import[^\n]*\n/gm,'').replace('export default','return')
  const keys=Object.keys(deps).filter(key=>key!=='default'&&/^[\w$]+$/.test(key)),scope=Vue.effectScope()
  const api=scope.run(()=>new Function(...keys,code)(...keys.map(key=>deps[key])).setup({},{expose(){}}))
  return {api,world,mounted,timers,close(){hooks.forEach(fn=>fn());scope.stop()}}
}
test('challenge requests preserve exact course and plan; simulation executes the checked ticket directly',async()=>{
  const calls=[],ctx=fixture(async(path,options)=>{calls.push({path,body:options.body&&JSON.parse(options.body)});return reply(state(path.endsWith('/setup')?'ready':path.endsWith('/inspect')?'checked':path.endsWith('/run')?'running':'cancelled'))})
  try {
    await ctx.api.act('setup');assert.equal(calls[0].path,'/api/challenge/handling/setup');assert.deepEqual(calls[0].body,{level:1})
    await ctx.api.act('inspect');assert.deepEqual(calls[1].body,{challenge_id:'course-current'})
    await ctx.api.act('run')
    assert.deepEqual(calls[2].body,{challenge_id:'course-current',plan_id:'checked-current'})
    assert.equal(ctx.api.state.value.score,0)
  }finally{ctx.close()}
})
test('cancel stays reachable during preflight and late success cannot restore the old plan',async()=>{
  const waiting=deferred(),calls=[]
  const ctx=fixture(async(path)=>{calls.push(path);return path.endsWith('/inspect')?waiting.promise:reply(state('cancelled'))})
  try {
    ctx.api.state.value=state('ready');const pending=ctx.api.act('inspect')
    assert.equal(ctx.api.pending.value,'inspect');await ctx.api.act('cancel')
    waiting.resolve(reply(state('checked')));await pending
    assert.equal(ctx.api.state.value.phase,'cancelled');assert.equal(ctx.api.pending.value,'')
    assert.equal(calls.filter(path=>path.endsWith('/cancel')).length,1)
  }finally{ctx.close()}
})
test('old status read cannot overwrite a new mutation and leaving reports cancellation failure',async()=>{
  const waiting=deferred()
  const ctx=fixture(async(path)=>path==='/api/challenge/handling'?waiting.promise:path.endsWith('/cancel')?Promise.reject(new Error('synthetic offline')):reply(state('ready')))
  try {
    const poll=ctx.api.read();await ctx.api.act('setup');waiting.resolve(reply(state('idle')));await poll
    assert.equal(ctx.api.state.value.phase,'ready')
    ctx.api.state.value=state('running');ctx.close();await new Promise(resolve=>setImmediate(resolve))
    assert.match(ctx.world.error,/取消未确认/)
  }finally{ctx.close()}
})
test('handling component script/template/style compiles and stays inside existing functions navigation',()=>{
  const {descriptor}=parse(source),script=compileScript(descriptor,{id:'handling'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'handling',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'handling',scoped:true}).errors,[])
  const functions=readFileSync(new URL('../src/panels/FunctionsPanel.vue',import.meta.url),'utf8')
  assert.match(functions,/id:'handling',label:'搬运闯关'/);assert.match(functions,/<HandlingChallenge v-if="tab==='handling'"/)
})

for(const terminal of ['completed','cancelled','failed'])test(`polling ${terminal} replaces the successful running notice with the current backend message`,async()=>{
  const ctx=fixture(async path=>reply({...state(path.endsWith('/run')?'running':terminal),message:path.endsWith('/run')?'机器人正在执行本关取放':`当前状态：${terminal}`}))
  try {
    ctx.api.state.value=state('checked');await ctx.api.act('run')
    assert.equal(ctx.api.message.value,'机器人正在执行本关取放')
    await ctx.api.read()
    assert.equal(ctx.api.state.value.phase,terminal)
    assert.equal(ctx.api.message.value || ctx.api.state.value.message,`当前状态：${terminal}`)
  }finally{ctx.close()}
})

test('a status phase change preserves newer action and network failures',async()=>{
  let rejectRead=false
  const ctx=fixture(async path=>{if(path.endsWith('/run'))throw new Error('执行请求失败');if(rejectRead)throw new Error('状态网络失败');return reply({...state('completed'),message:'已完成'})})
  try {
    ctx.api.state.value=state('checked');await ctx.api.act('run');await ctx.api.read()
    assert.equal(ctx.api.message.value,'执行请求失败');assert.equal(ctx.api.failed.value,true)
    rejectRead=true;await ctx.api.read();rejectRead=false;ctx.api.state.value=state('running');await ctx.api.read()
    assert.equal(ctx.api.message.value,'状态网络失败');assert.equal(ctx.api.failed.value,true)
  }finally{ctx.close()}
})

test('a late old poll failure cannot overwrite a new successful action',async()=>{
  const old=deferred(),ctx=fixture(async path=>path==='/api/challenge/handling'?old.promise:reply({...state('ready'),message:'关卡已布置'}))
  try {
    const poll=ctx.api.read();await ctx.api.act('setup');old.reject(new Error('旧状态请求失败'));await poll
    assert.equal(ctx.api.message.value,'关卡已布置');assert.equal(ctx.api.failed.value,false)
  }finally{ctx.close()}
})


test('direct execution still rejects missing ticket, wrong phase and non-simulation mode',async()=>{
 const calls=[],ctx=fixture(async(path)=>{calls.push(path);return reply(state('running'))})
 try{
  ctx.api.state.value=state('ready');await ctx.api.act('run');assert.equal(calls.length,0)
  ctx.api.state.value={...state('checked'),plan_id:null};await ctx.api.act('run');assert.equal(calls.length,0)
  ctx.api.state.value=state('checked');ctx.world.snap.mode='real';await ctx.api.act('run');assert.equal(calls.length,0)
 }finally{ctx.close()}
})

test('handling stop remains enabled during movement and cancels the owned course',async()=>{
 const calls=[],ctx=fixture(async(path)=>{calls.push(path);return reply(state('cancelled'))})
 try{ctx.api.state.value=state('running');ctx.world.snap.moving=true;ctx.world.snap.exec.state='running'
  const descriptor=parse(source).descriptor,script=compileScript(descriptor,{id:'handling-stop'})
  const compiled=compileTemplate({source:descriptor.template.content,id:'handling-stop',compilerOptions:{mode:'function',bindingMetadata:script.bindings}})
  const render=new Function('Vue',compiled.code)(Vue),html=await renderToString(Vue.createSSRApp({setup:()=>ctx.api,render}))
  const stop=[...html.matchAll(/<button([^>]*)>([^<]*)<\/button>/g)].find(b=>b[2]==='停止')
  assert.ok(stop);assert.doesNotMatch(stop[1],/disabled/)
  await ctx.api.startStop();assert.deepEqual(calls,['/api/challenge/handling/cancel'])
 }finally{ctx.close()}
})
