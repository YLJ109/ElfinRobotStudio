// 回归测试：覆盖本轮代码审查修复的 5 个前端缺陷。
// 这些用例在修复前必须失败（锁死/吞字段/假装就绪/脏帧），修复后必须通过。
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {createPinia} from 'pinia'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createHandPipelineClient} from '../src/handPipeline.js'
import {publishHandFollowInput, revokeHandFollowInput, getHandFollowInput, subscribeHandFollowInput} from '../src/handFollowInput.js'
import {createCameraSource} from '../src/cameraSource.js'
import {useWorld} from '../src/store.js'
import * as writingConfig from '../src/writingConfig.js'
import * as textContours from '../src/textContours.js'
import * as textView from '../src/textWritingView.js'

// ---------------------------------------------------------------- 检测器字段不被短路吞掉
test('the unchanged short-circuit never swallows a changed detector error or readiness',async()=>{
  const snapshot={enabled:true,source:'local',session:'cam',epoch:2,server_now:5,result:null,error:'',
    detector_state:'warming',detector_error:'',detector_ready:false}
  const state={enabled:false},jobs=new Map();let id=0
  const client=createHandPipelineClient({state,now:()=>1000,
    schedule:(fn,ms)=>{jobs.set(++id,{fn,ms});return id},cancel:key=>jobs.delete(key),
    fetcher:async()=>({ok:true,json:async()=>snapshot})})
  const off=client.subscribe(()=>{})
  const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve()}
  const poll=async()=>{
    await settle()
    const job=[...jobs.values()][0]
    jobs.clear()
    job.fn()
    await settle()
  }
  try{
    await settle()
    assert.equal(state.detectorState,'warming');assert.equal(state.detectorError,'');assert.equal(state.detectorReady,false)
    // 只改检测器原因：状态必须跟着走，否则界面会一直停在"检测中"的假状态。
    snapshot.detector_error='手掌检测器启动失败：No module named mediapipe'
    await poll()
    assert.equal(state.detectorError,'手掌检测器启动失败：No module named mediapipe')
    snapshot.detector_state='failed'
    await poll()
    assert.equal(state.detectorState,'failed');assert.equal(state.detectorReady,false)
    snapshot.detector_state='ready';snapshot.detector_ready=true;snapshot.detector_error=''
    await poll()
    assert.equal(state.detectorState,'ready');assert.equal(state.detectorReady,true)
  }finally{off();client.dispose()}
})

// ---------------------------------------------------------------- 控制方撤销可以清掉总线
test('a controller-side revoke clears the shared input while a stale producer still cannot',()=>{
  const a=Symbol(),b=Symbol()
  const frame=(stamp)=>({session:'cam',stream:'color',fresh:true,width:640,height:480,frameSeq:stamp,
    timestamp:stamp,point:{x:.5,y:.5,palm_scale:.1,dx:1,dy:0},hands:true,trackingState:'tracking'})
  const off=subscribeHandFollowInput(()=>{})
  try{
    publishHandFollowInput(frame(1000),a)
    publishHandFollowInput(frame(1100),b)
    publishHandFollowInput(null,a)
    assert.ok(getHandFollowInput(),'a stale producer must not clear the newer producer')
    assert.equal(getHandFollowInput().timestamp,1100)
    revokeHandFollowInput()
    assert.equal(getHandFollowInput(),null,'a source switch must really revoke the old camera input')
    // 撤销后总线仍然接受新生产者的数据
    publishHandFollowInput(frame(1200),b)
    assert.equal(getHandFollowInput().timestamp,1200)
  }finally{off();publishHandFollowInput(null,b);revokeHandFollowInput()}
})

// ---------------------------------------------------------------- 检测器没就绪绝不能报成功
function cameraFixture({detectorState='idle',detectorError='',wait={ok:true},enabledAfterConfigure=true}={}){
  const state=Vue.reactive({instance_id:'engine-a',revision:0,config:{input:'camera',enabled:false},result:null,stale:false})
  const storage={getItem:()=>null,setItem:()=>{}}
  const w=Vue.reactive({mode:'sim',snap:{mode:'sim'},cameras:{active:{id:'usb:1',session:'cam-1',state:'streaming'},devices:[{id:'usb:1',name:'USB 1',streams:['color']}]},
    async closeCamera(){return {ok:true}},async openCamera(){return {ok:true,active:{session:'cam-1'}}},async refreshCameras(){return {devices:[]}}})
  const calls=[]
  const pipeline={state:Vue.reactive({enabled:false,source:null,session:null,detectorState,detectorError,detectorReady:false}),
    async configure(enabled,source,session){calls.push([enabled,source,session]);
      Object.assign(this.state,{enabled:enabledAfterConfigure,source,session});return true},
    async waitForDetector(){return wait}}
  const request=async(path,patch)=>({ok:true,instance_id:'engine-a',revision:1,config:{...state.config,...patch}})
  return {c:createCameraSource(w,{state,storage,request,pipeline}),calls,pipeline}
}
test('hand detection never claims success while the backend detector is idle or failed',async()=>{
  const idle=await cameraFixture({detectorState:'idle',wait:{ok:false,error:'检测器启动超时'}}).c.setHandDetection(true)
  assert.equal(idle,false,'an idle detector must not be reported as ready')
  const failed=await cameraFixture({detectorState:'failed',detectorError:'手掌检测器启动失败：No module named mediapipe'}).c.setHandDetection(true)
  assert.equal(failed,false,'a failed detector must not be reported as ready')
  const ready=await cameraFixture({detectorState:'ready'}).c.setHandDetection(true)
  assert.equal(ready,true,'a ready detector is the only success')
})
test('hand detection re-checks the real switch after waiting for the detector',async()=>{
  const {c}=cameraFixture({detectorState:'warming',wait:{ok:true},enabledAfterConfigure:false})
  assert.equal(await c.setHandDetection(true),false,'a detector disabled during the wait is not a success')
  assert.match(c.message.value,/已被关闭/)
})

// ---------------------------------------------------------------- 写字面板不会被永久锁死
test('editing the text while an execute preview is pending releases the writing panel lock',async()=>{
  const source=readFileSync(new URL('../src/components/TextWritingTask.vue',import.meta.url),'utf8')
  const descriptor=parse(source).descriptor
  const hooks={start:[],stop:[]},jobs=[]
  const world=Vue.reactive({connected:true,stateFresh:true,snap:{mode:'sim',session_id:'s',sim_speed_pct:100,tools:{visual:{kind:'vacuum-reference'}}},call:async()=>({ok:true})})
  const state=Vue.reactive({snapshot:null,pending:'',runQueued:false})
  const controller={owner:'text-test',state,owned:()=>false,edit(){},start(){},refreshValidity(){},visualSnapshot:()=>null,
    cancel:async()=>{},leave(){},dispose(){},requestRun:async()=>{},preview:async()=>{}}
  class Worker{constructor(){this.stopped=false}postMessage(value){jobs.push(value)}terminate(){this.stopped=true}}
  const runtime={...Vue,onMounted:fn=>hooks.start.push(fn),onActivated:()=>{},onDeactivated:fn=>hooks.stop.push(fn),onBeforeUnmount:fn=>hooks.stop.push(fn)}
  const imports={vue:runtime,'../store':{useWorld:()=>world},
    '../drawingTask.js':{createDrawingTask:()=>controller,drawingToolPose:()=>null},
    '../textContours.js':textContours,'../textWritingView.js':textView,
    '../writingConfig.js':writingConfig,'../simTaskPen.js':{ensureSimTaskPen:async()=>{}}}
  const code=compileScript(descriptor,{id:'writing-lock'}).content
    .replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,path)=>`const ${bindings.replace(/\bas\b/g,':')}=imports[${JSON.stringify(path)}];`)
    .replace('export default','return').replace("new URL('../textWorker.js',import.meta.url)","'local-worker'")
  const component=new Function('imports','globalThis','Worker',code)(imports,{window:{__elfinScene:{setView(){},setDrawingState(){},clearDrawing(){}}}},Worker)
  const scope=Vue.effectScope(),api=scope.run(()=>component.setup({},{expose(){}}))
  try{
    hooks.start[0]()
    const pending=api.preview(true)
    assert.equal(api.preparing.value,true,'the execute preview must lock the panel while it runs')
    api.form.text='改'
    assert.equal(api.preparing.value,false,'editing the text must release the lock instead of freezing the panel')
    await pending
    assert.equal(api.preparing.value,false)
    const before=jobs.length
    void api.preview(false)
    assert.ok(jobs.length>before,'the preview button must work again after the lock was released')
  }finally{for(const stop of hooks.stop)await stop();scope.stop()}
})

// ---------------------------------------------------------------- 回放脏数据不拼出假帧
test('playback frames are only returned as complete finite six-axis values',()=>{
  const world=useWorld(createPinia())
  const columns=()=>[[1,2,3],[4,5,6],[7,8,9],[10,11,12],[13,14,15],[16,17,18]]
  world.play.n=3;world.play.cols=columns();world.play.idx=0
  assert.deepEqual(world.playFrame(),[1,4,7,10,13,16])
  world.play.idx=2
  assert.deepEqual(world.playFrame(),[3,6,9,12,15,18])
  world.play.idx=99
  assert.deepEqual(world.playFrame(),[3,6,9,12,15,18],'an out-of-range cursor is clamped to the last frame')
  world.play.idx=0;world.play.cols=columns().slice(0,4)
  assert.equal(world.playFrame(),null,'a short column set is never padded with zeros')
  world.play.cols=columns();world.play.cols[5]=[NaN,17,18]
  assert.equal(world.playFrame(),null,'a NaN joint is not a pose')
  world.play.cols=columns();world.play.n=0
  assert.equal(world.playFrame(),null)
})
