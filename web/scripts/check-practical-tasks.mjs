import {renderToString} from '@vue/server-renderer'
import test from 'node:test'
import assert from 'node:assert/strict'
import {assemblyPayload,assemblyRecipe,patternCells,createPracticalTask} from '../src/practicalTasks.js'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {taskKinds} from '../src/practicalTasks.js'
import {scenePlay,playAllowed} from '../src/functionPlay.js'
const component=readFileSync(new URL('../src/components/PracticalTask.vue',import.meta.url),'utf8')
function fixture(kind,fetcher,confirm=()=>true){
  const world=Vue.reactive({connected:true,snap:{mode:'sim',session_id:'session',moving:false,real:null,exec:{state:'idle'},objects:[]}})
  const shared=Vue.reactive({connected:true,stale:false,result:{source_key:'source'}}),hooks=[]
  const deps={...Vue,useWorld:()=>world,scenePlay,playAllowed,assemblyPayload,assemblyRecipe,patternCells,taskKinds,visionWorkspace:shared,window:{confirm},
    createPracticalTask:options=>createPracticalTask({...options,fetcher}),onMounted:()=>{},onBeforeUnmount:fn=>hooks.push(fn),setInterval:()=>0,clearInterval:()=>{}}
  const code=compileScript(parse(component).descriptor,{id:'practical'}).content.replace(/^import[^\n]*\n/gm,'').replace('export default','return')
  const keys=Object.keys(deps).filter(key=>key!=='default'&&/^[\w$]+$/.test(key)),scope=Vue.effectScope()
  const api=scope.run(()=>new Function(...keys,code)(...keys.map(key=>deps[key])).setup({kind},{expose(){}}))
  return {api,world,shared,close(){hooks.forEach(fn=>fn());scope.stop()}}
}
test('unified task component compiles script, template and styles',()=>{
  const {descriptor}=parse(component),script=compileScript(descriptor,{id:'practical'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'practical',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'practical',scoped:true}).errors,[])
})
test('successful state without task is ready but not started; another mode never claims this task completed',()=>{
  for(const kind of ['find','count','pattern']){
    const ctx=fixture(kind,async()=>reply({}))
    try{assert.equal(ctx.api.phase.value,'等待读取')
      ctx.api.state.snapshot=kind==='pattern'?{ok:true,mode:'kit',phase:'completed',completed:1,total:1}:{ok:true,available:true,task:null}
      assert.equal(ctx.api.phase.value,'尚未操作')
    }finally{ctx.close()}
  }
})
test('find pointer uses exact measured image and disappears on TTL, source change or invalid result',()=>{
  const ctx=fixture('find',async()=>reply({})),api=ctx.api
  try{
    api.state.snapshot={ok:true,available:true,context_id:'context',task:{phase:'completed',result:{valid:true,context_id:'context',frame:{source_key:'source'},expires_at:Date.now()/1000+1,image:{width:640,height:480,base64:'measured'},selected:{name:'V1',center_normalized:[.5,.5],corners:[[0,0],[1,0],[1,1],[0,1]]}}}}
    assert.equal(api.pointerImage.value.base64,'measured')
    ctx.shared.result={source_key:'changed'};assert.equal(api.pointer.value,null)
    ctx.shared.result={source_key:'source'};api.now.value=Date.now()+2000;assert.equal(api.pointer.value,null)
    api.now.value=Date.now();api.state.snapshot.task.result.valid=false;assert.equal(api.pointer.value,null)
  }finally{ctx.close()}
})
test('invalid dimensions never allocate grid and missing group never sends preview',async()=>{
  const calls=[],ctx=fixture('kit',async(...args)=>{calls.push(args);return reply({})})
  try{ctx.api.columns.value=100000;ctx.api.resize();assert.equal(ctx.api.cells.value.length,6);assert.match(ctx.api.inputError.value,/行列/)
    ctx.api.columns.value=3;await ctx.api.act('preview');assert.equal(calls.length,0);assert.match(ctx.api.inputError.value,/物料场景正在同步/)
  }finally{ctx.close()}
})
test('simulation recipe setup rejects invalid cells then creates materials without extra confirmation',async()=>{
  const calls=[];let confirmations=0
  const ctx=fixture('pattern',async(...args)=>{calls.push(args);return reply({})},()=>{confirmations++;return false})
  try{
    ctx.api.cells.value=Array(6).fill(null);await ctx.api.setupMaterials();assert.equal(confirmations,0);assert.equal(calls.length,0);assert.match(ctx.api.inputError.value,/有效格位/)
    ctx.api.cells.value=['red',null,null,null,null,null];await ctx.api.setupMaterials();assert.equal(confirmations,0);assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/assembly/setup')
  }finally{ctx.close()}
})
test('letter recipe setup consumes exact occupied pixels without group or implicit camera calls',async()=>{
  const calls=[],ctx=fixture('pattern',async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return reply({phase:'ready',mode:'pattern',group:'new-group'})})
  try{ctx.api.glyph();await ctx.api.setupMaterials()
    assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/assembly/setup');assert.deepEqual(calls[0].body,{mode:'pattern',columns:3,rows:5,cells:patternCells('A','red')})
    assert.equal(ctx.api.groupReady.value,false)
    await ctx.api.act('preview');assert.equal(calls.length,2);assert.equal(calls[1].body.group,'new-group')
  }finally{ctx.close()}
})
test('one read-only action follows current preview and checked tokens without motion confirmation',async()=>{
  const calls=[],ctx=fixture('find',async(path,options)=>{
    calls.push({path,body:JSON.parse(options.body)})
    return reply({available:true,context_id:'context',task:{mode:'find',preview_id:'fresh-preview',plan_id:path.endsWith('/preview')?null:'fresh-plan',phase:path.endsWith('/run')?'completed':path.endsWith('/inspect')?'checked':'preview'}})
  })
  try{ctx.api.state.snapshot={available:true,context_id:'context'};await ctx.api.analyze()
    assert.deepEqual(calls.map(call=>call.path),['/api/vision/tasks/preview','/api/vision/tasks/inspect','/api/vision/tasks/run'])
    assert.deepEqual(calls[1].body,{preview_id:'fresh-preview'});assert.deepEqual(calls[2].body,{plan_id:'fresh-plan'})
    assert.equal(ctx.api.state.confirmed,false);assert.equal(ctx.api.task.value.phase,'completed')
  }finally{ctx.close()}
})
test('read-only source expiry during inspect stops the sequence without run or false completion',async()=>{
  const calls=[],ctx=fixture('count',async(path)=>{calls.push(path);return reply(path.endsWith('/inspect')?{ok:false,why:'来源已过期'}:{available:true,context_id:'context',task:{mode:'inspection',preview_id:'p',phase:'preview'}})})
  try{ctx.api.state.snapshot={available:true,context_id:'context'};await ctx.api.analyze()
    assert.equal(calls.length,2);assert.match(ctx.api.state.error,/来源已过期/);assert.equal(ctx.api.task.value.phase,'preview')
  }finally{ctx.close()}
})
const reply=value=>({ok:true,status:200,json:async()=>({ok:true,...value})})
test('assembly preserves exact recipe, empty pixels and rejects missing material group',()=>{
  assert.deepEqual(assemblyPayload('kit','g',2,1,['red',null]),{mode:'kit',group:'g',columns:2,rows:1,cells:['red',null]})
  assert.throws(()=>assemblyPayload('kit','',1,1,['red']),/布置/)
  assert.throws(()=>assemblyPayload('kit','g',1,1,[null]),/有效格位/)
  assert.equal(patternCells('A','blue').length,15);assert.ok(patternCells('A','blue').includes(null))
  assert.throws(()=>patternCells('unknown'),/图案/)
})
test('HTTP success cannot disguise application rejection as successful preview',async()=>{
  const task=createPracticalTask({prefix:'/api/assembly',fetcher:async()=>reply({ok:false,why:'物料不足'})})
  assert.equal(await task.act('preview',{}),false);assert.equal(task.state.snapshot,null);assert.match(task.state.error,/物料不足/);task.leave()
})
test('run needs explicit confirmation; cancel defeats a late successful inspect',async()=>{
  let resolve;const waiting=new Promise(done=>resolve=done),calls=[]
  const task=createPracticalTask({prefix:'/api/assembly',fetcher:async(path,options)=>{calls.push({path,body:JSON.parse(options.body||'null')});return path.endsWith('inspect')?waiting:reply({phase:'cancelled',preview_id:'p'})}})
  await task.act('run',{preview_id:'p',plan_id:'plan'});assert.equal(calls.length,0)
  const checking=task.act('inspect',{preview_id:'p'});await task.act('cancel',{preview_id:'p'})
  resolve(reply({phase:'checked',plan_id:'old'}));await checking
  assert.equal(task.state.snapshot.phase,'cancelled');assert.equal(task.state.pending,'')
  task.leave()
})
test('old polling cannot overwrite new preview and server failure remains visible',async()=>{
  let resolve;const waiting=new Promise(done=>resolve=done)
  const task=createPracticalTask({prefix:'/api/assembly',fetcher:async path=>path.endsWith('/state')?waiting:path.endsWith('/preview')?reply({phase:'preview',preview_id:'p'}):{ok:false,status:409,json:async()=>({why:'视觉已过期'})}})
  const poll=task.read();await task.act('preview',{});resolve(reply({phase:'idle'}));await poll
  assert.equal(task.state.snapshot.preview_id,'p');await task.act('inspect',{});assert.match(task.state.error,/视觉已过期/);task.leave()
})


test('simulation assembly runs a checked current plan without redundant checkbox and retains server expiry rejection',async()=>{
 const calls=[],ctx=fixture('kit',async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return path.endsWith('/run')?{ok:false,status:409,json:async()=>({why:'计划已过期'})}:reply({})})
 try {
  ctx.api.state.snapshot={ok:true,mode:'kit',phase:'checked',preview_id:'p',plan_id:'checked'}
  await ctx.api.act('run');assert.deepEqual(calls[0].body,{preview_id:'p',plan_id:'checked'})
  assert.match(ctx.api.state.error,/计划已过期/)
  ctx.api.state.snapshot={ok:true,mode:'kit',phase:'preview',preview_id:'p'}
  await ctx.api.act('run');assert.equal(calls.length,1)
 }finally{ctx.close()}
})

for(const kind of ['kit','pattern'])test(`${kind} starts with one action through server layout, preview, inspect and actual run`,async()=>{
 const calls=[],ctx=fixture(kind,async(path,options)=>{
  calls.push({path,body:JSON.parse(options.body)})
  const action=path.split('/').at(-1)
  return reply({mode:kind,group:'server-new',preview_id:action==='setup'?null:'preview-new',plan_id:action==='inspect'?'plan-new':null,phase:({setup:'idle',preview:'preview',inspect:'checked',run:'running'})[action]})
 })
 try{await ctx.api.startAssembly();assert.deepEqual(calls.map(c=>c.path.split('/').at(-1)),['setup','preview','inspect','run'])
  assert.equal(calls[1].body.group,'server-new','fresh server group is consumed without waiting for a websocket snapshot')
  assert.deepEqual(calls[3].body,{preview_id:'preview-new',plan_id:'plan-new'})
  assert.equal(ctx.api.task.value.phase,'running');assert.deepEqual(ctx.world.snap.objects,[],'UI does not invent object positions')
 }finally{ctx.close()}
})
test('assembly inspect failure never submits a run',async()=>{
 const calls=[],ctx=fixture('kit',async(path)=>{calls.push(path);return reply(path.endsWith('/inspect')?{ok:false,why:'target unreachable'}:{group:'new',mode:'kit',preview_id:'p',phase:path.endsWith('/setup')?'idle':'preview'})})
 try{await ctx.api.startAssembly();assert.deepEqual(calls.map(p=>p.split('/').at(-1)),['setup','preview','inspect']);assert.match(ctx.api.state.error,/unreachable/)}finally{ctx.close()}
})
test('running assembly exposes exactly one enabled stop even while robot motion makes new starts unavailable',async()=>{
 const calls=[],ctx=fixture('kit',async(path)=>{calls.push(path);return reply({phase:'cancelled',mode:'kit',preview_id:'p',group:'g'})})
 try{ctx.world.snap.moving=true;ctx.world.snap.exec.state='running';ctx.api.state.snapshot={phase:'running',mode:'kit',preview_id:'p',group:'g'}
  const descriptor=parse(component).descriptor,script=compileScript(descriptor,{id:'assembly-stop'})
  const compiled=compileTemplate({source:descriptor.template.content,id:'assembly-stop',compilerOptions:{mode:'function',bindingMetadata:script.bindings}})
  const render=new Function('Vue',compiled.code)(Vue),html=await renderToString(Vue.createSSRApp({props:{kind:String},setup:()=>ctx.api,render},{kind:'kit'}))
  const buttons=[...html.matchAll(/<button([^>]*)>([^<]*)<\/button>/g)],stop=buttons.filter(b=>b[2]==='停止')
  assert.equal(stop.length,1);assert.doesNotMatch(stop[0][1],/disabled/)
  await ctx.api.startAssembly();assert.deepEqual(calls,['/api/assembly/cancel']);assert.equal(ctx.api.task.value.phase,'cancelled')
 }finally{ctx.close()}
})
