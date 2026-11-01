import * as numberDisplay from '../src/numberDisplay.js'
// Compiled components with fake feedback/HTTP/worker only; no service or device.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import * as drawing from '../src/drawingTask.js'
import * as contours from '../src/textContours.js'
import * as view from '../src/textWritingView.js'
import * as writing from '../src/writingConfig.js'

function fixture(mode='real'){
 const world=Vue.reactive({connected:true,stateFresh:true,snap:{mode,session_id:'s1',motion_granted:true,
  real_speed_pct:5,sim_speed_pct:100,real:mode==='real'?{cmd_up:true,stream_up:true,quality:'valid',connection_epoch:7}:null,
  tools:{tool:{type:'pen'},visual:{kind:'pen'}},joints:{known:true},tcp:{xyz_mm:[400,0,120],rpy_deg:[180,0,0]}}})
 const requests=[],toolCalls=[],views=[];let controller,phase='idle',config
 const state=()=>({ok:true,phase,mode,session_id:'s1',...(phase!=='idle'?{plan_id:'text-real'}:{}),config,
  trace_tcp:{valid:true,offset_in_snapshot_tcp_mm:[0,0,0]},path:[[400,0,120,180,0,0],[401,0,120,180,0,0]],
  program:'MOVEL 400,0,120,180,0,0',expires_in_s:120,can_run:true})
 const imports={'../numberDisplay.js':numberDisplay,vue:{...Vue,onMounted(){},onActivated(){},onBeforeUnmount(){},onDeactivated(){}},
  '../store':{useWorld:()=>world},'../drawingTask.js':{...drawing,createDrawingTask:w=>controller=drawing.createDrawingTask(w,{request:async(action,payload)=>{
   requests.push({action,payload});if(action==='preview'){phase='ready';config=payload}if(action==='run')phase='running';if(action==='cancel')phase='cancelled';return state()
  }})},'../textContours.js':contours,'../textWritingView.js':view,'../writingConfig.js':writing,
  '../simTaskPen.js':{ensureSimTaskPen:async()=>toolCalls.push('pen')}}
 const source=readFileSync(new URL('../src/components/TextWritingTask.vue',import.meta.url),'utf8')
 const descriptor=parse(source).descriptor
 const code=compileScript(descriptor,{id:'real-writing'}).content
  .replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,path)=>`const ${bindings.replace(/\bas\b/g,':')}=imports[${JSON.stringify(path)}];`)
  .replace("new URL('../textWorker.js',import.meta.url)","'fake-worker'").replace('export default','return')
 class Worker{postMessage(value){queueMicrotask(()=>this.onmessage({data:{ok:true,text:value.text,em_height:1,em_width:3,strokes:[[[0,0],[.2,.3],[.4,0]]]}}))}terminate(){}}
 const component=new Function('imports','Worker','globalThis',code)(imports,Worker,{window:{__elfinScene:{setView:value=>views.push(value),clearDrawing(){}}}})
 const scope=Vue.effectScope(),api=scope.run(()=>component.setup({},{expose(){}}))
 return {world,api,requests,toolCalls,views,controller,close(){controller.dispose();scope.stop()}}
}

test('interaction components compile with real-mode entry points',()=>{
 for(const path of ['components/TextWritingTask.vue','components/TrackPanel.vue','panels/FunctionsPanel.vue']){
  const descriptor=parse(readFileSync(new URL('../src/'+path,import.meta.url),'utf8')).descriptor
  const script=compileScript(descriptor,{id:'interaction-real'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'interaction-real',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'interaction-real',scoped:true}).errors,[])
 }
})
test('actual real feedback schema yields qualified contact pose and fails closed for missing feedback',()=>{
 const h=fixture();try{
  const feedback={session_id:'s1',mode:'real',trace_tcp:{valid:true,offset_in_snapshot_tcp_mm:[0,0,0]}}
  assert.equal(drawing.drawingFeedbackIssue(h.world,feedback),'')
  assert.equal('connected' in h.world.snap.real,false)
  h.world.snap.real.quality='stale';assert.match(drawing.drawingFeedbackIssue(h.world,feedback),/反馈无效/)
  h.world.snap.real.quality='valid';h.world.snap.real.cmd_up=false;assert.match(drawing.drawingFeedbackIssue(h.world,feedback),/连接/)
 }finally{h.close()}
})
test('real writing defaults never fabricate TCP, explicit base/preview/confirmation run uses only drawing API',async()=>{
 const h=fixture();try{
  assert.equal(h.api.form.size,5);assert.equal(h.api.form.z,null);assert.equal(h.requests.length,0);assert.equal(h.views.length,0)
  await h.controller.read();h.api.takeBase()
  assert.deepEqual([h.api.form.x,h.api.form.y,h.api.form.z],[400,0,105])
  assert.equal(h.controller.state.baseAccepted,true)
  assert.equal(await h.api.preview(),true)
  const payload=h.requests.find(row=>row.action==='preview').payload
  assert.deepEqual(payload.center,[400,0,120,180,0,0]);assert.deepEqual(payload.range_center_mm,[400,0,120])
  assert.deepEqual(payload.range_size_mm,[29,39,50]);assert.equal(payload.writing_z_mm,105)
  assert.equal(payload.speed_pct,5);assert.equal(payload.font_size_mm,5);assert.equal(payload.plane,'xy')
  assert.equal(await h.api.execute(),false);assert.equal(h.requests.some(row=>row.action==='run'),false)
  h.controller.state.confirmedReal=true;assert.equal(await h.api.execute(),true)
  assert.deepEqual(h.requests.find(row=>row.action==='run').payload,{plan_id:'text-real',confirm_real:true})
  assert.deepEqual(h.toolCalls,[]);assert.deepEqual(h.views,[])
 }finally{h.close()}
})
test('real writing retains actual speed and refuses unqualified tools or oversize text without submitting a plan',async()=>{
 const h=fixture();try{
  await h.controller.read();h.api.takeBase()
  h.world.snap.real_speed_pct=10;assert.equal(await h.api.preview(),false);assert.match(h.api.message.value,/1～5%/)
  assert.equal(h.world.snap.real_speed_pct,10)
  h.world.snap.real_speed_pct=5;h.world.snap.tools.tool.type='vacuum';h.controller.adoptBase()
  assert.equal(await h.api.preview(),false);assert.match(h.api.message.value,/真实笔工具/)
  h.world.snap.tools.tool.type='pen';h.api.form.size=40;h.controller.adoptBase()
  await h.api.preview();assert.match(h.api.message.value,/50毫米范围/)
  assert.equal(h.requests.filter(row=>row.action==='preview').length,0);assert.deepEqual(h.toolCalls,[])
 }finally{h.close()}
})
test('sim writing keeps requested 90mm default while real text validation accepts only bounded plans',()=>{
 const h=fixture('sim');try{assert.equal(h.api.form.z,90);assert.equal(h.api.form.size,90)}finally{h.close()}
 const base={shape:'text',text:'A',strokes:[[[0,0],[1,0]]],font_size_mm:5,center:[400,0,120,180,0,0],range_center_mm:[400,0,120],range_size_mm:[40,40,50],plane:'xy',direction:0,center_policy:'fixed',width_mm:10,height_mm:10,angle_deg:0,speed_pct:5,repeats:1}
 assert.equal(drawing.drawingConfig(base,'real').writing_z_mm,90)
 for(const patch of [{font_size_mm:41},{repeats:2},{plane:'xz'},{speed_pct:10},{range_size_mm:[51,40,50]}])assert.throws(()=>drawing.drawingConfig({...base,...patch},'real'))
})
