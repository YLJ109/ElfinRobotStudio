import * as numberDisplay from '../src/numberDisplay.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import {localDrawingPreview,frontDrawingView,drawingToolFrame} from '../src/drawingPreview.js'
import {Vector3,Euler} from 'three'
import {ensureSimTaskPen} from '../src/simTaskPen.js'
import * as writingConfig from '../src/writingConfig.js'
import * as textContours from '../src/textContours.js'
import * as textView from '../src/textWritingView.js'

test('local geometry preserves the original front-facing range and shape proportions without motion authority',()=>{
 const base={shape:'heart',center:[-500,0,700,90,0,0],plane:'xz',width_mm:280,height_mm:240,angle_deg:5,speed_pct:100,repeats:1,direction:270,range_center_mm:[-500,0,700],range_size_mm:[900,300,500],center_policy:'range_lower'}
 for(const shape of ['heart','square','rectangle','circle','nod','shake']){
  const value=localDrawingPreview({...base,shape})
  assert.ok(value.path.length>=4);assert.ok(value.path.every(row=>row.every(Number.isFinite)))
  assert.deepEqual(value.config.center,base.center);assert.equal(value.config.direction,270)
  assert.deepEqual(value.bounds,{min:[-650,-450,450],max:[-350,450,950]})
  assert.equal(frontDrawingView(value.bounds,'xz').theta,Math.PI)
 }
 const heart=localDrawingPreview(base).path
 assert.ok(heart.every(point=>Math.abs(point[0]+500)<1e-8),'original 270-degree drawing faces the human at negative X')
 assert.ok(Math.abs(Math.max(...heart.map(p=>p[1]))-Math.min(...heart.map(p=>p[1]))-280)<1e-8)
 assert.ok(Math.abs(Math.max(...heart.map(p=>p[2]))-Math.min(...heart.map(p=>p[2]))-240)<1e-8)
})

test('simulation pen frame rotates with the drawing plane and faces negative X for the original front range',()=>{
 const original=[-500,0,700,90,0,0]
 const pose=drawingToolFrame(original,270)
 assert.deepEqual(pose,[-500,0,700,90,0,-90]);assert.deepEqual(original,[-500,0,700,90,0,0])
 const axis=new Vector3(0,0,1).applyEuler(new Euler(...pose.slice(3).map(v=>v*Math.PI/180),'ZYX'))
 assert.ok(Math.abs(axis.x+1)<1e-10&&Math.abs(axis.y)<1e-10&&Math.abs(axis.z)<1e-10,'pen axis is normal to the front YZ drawing plane')
 for(const [direction,yaw] of [[0,0],[90,90],[180,-180],[270,-90]])assert.equal(drawingToolFrame(original,direction)[5],yaw)
 assert.deepEqual(drawingToolFrame(original,270,'real'),original,'actual real TCP base is never automatically rotated')
 assert.equal(drawingToolFrame([0,0,0,90,0,null],270)[5],null,'unknown angles are not fabricated')
 const preview=localDrawingPreview({shape:'heart',center:pose,plane:'xz',width_mm:280,height_mm:240,angle_deg:5,speed_pct:100,repeats:1,direction:270,range_center_mm:original.slice(0,3),range_size_mm:[900,300,500]})
 assert.ok(preview.path.every(p=>p.slice(3).every((v,i)=>v===pose[i+3])))
})

test('automatic pen switching rejects real mode, failed switches, and stale sessions',async()=>{
 let calls=0
 const w=Vue.reactive({snap:{mode:'real',session_id:'s'},call:async()=>{calls++;return {ok:true}}})
 await assert.rejects(ensureSimTaskPen(w,'drawing'));assert.equal(calls,0)
 w.snap.mode='sim';w.call=async()=>({ok:false,why:'工具被占用'})
 await assert.rejects(ensureSimTaskPen(w,'drawing'),/工具被占用/)
 w.call=async()=>{w.snap.session_id='replacement';w.snap.tools={visual:{kind:'pen'}};return {ok:true}}
 await assert.rejects(ensureSimTaskPen(w,'drawing'),/连接已变化/)
})

test('typing text automatically coalesces local worker previews without tool, camera or planning requests',async()=>{
 const source=readFileSync(new URL('../src/components/TextWritingTask.vue',import.meta.url),'utf8'),descriptor=parse(source).descriptor
 const hooks={start:[],stop:[]},jobs=[],visuals=[],calls=[]
 const world=Vue.reactive({connected:true,stateFresh:true,snap:{mode:'sim',session_id:'s',sim_speed_pct:100,tools:{visual:{kind:'vacuum-reference'}}},call:async(...args)=>{calls.push(args);return {ok:true}}})
 const state=Vue.reactive({snapshot:null,pending:'',runQueued:false}),controller={owner:'text-test',state,owned:()=>false,edit(){},start(){},refreshValidity(){},visualSnapshot:()=>null,cancel:async()=>{},leave(){},dispose(){},requestRun:async()=>{calls.push('run')}}
 class Worker {
  constructor(){this.stopped=false}
  postMessage(value){jobs.push(value);queueMicrotask(()=>{if(!this.stopped)this.onmessage({data:{ok:true,text:value.text,strokes:[[[0,0],[.5,0],[.5,.5],[0,0]]],em_width:.5,em_height:.5}})})}
  terminate(){this.stopped=true}
 }
 const runtime={...Vue,onMounted:fn=>hooks.start.push(fn),onActivated:()=>{},onDeactivated:fn=>hooks.stop.push(fn),onBeforeUnmount:fn=>hooks.stop.push(fn)}
 const imports={'../numberDisplay.js':numberDisplay,vue:runtime,'../store':{useWorld:()=>world},'../drawingTask.js':{createDrawingTask:()=>controller,drawingToolPose:()=>null},'../textContours.js':textContours,'../textWritingView.js':textView,'../writingConfig.js':writingConfig,'../simTaskPen.js':{ensureSimTaskPen}}
 const code=compileScript(descriptor,{id:'automatic-text'}).content.replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,path)=>`const ${bindings.replace(/\bas\b/g,':')}=imports[${JSON.stringify(path)}];`).replace('export default','return').replace("new URL('../textWorker.js',import.meta.url)","'local-worker'")
 const component=new Function('imports','globalThis','Worker',code)(imports,{window:{__elfinScene:{setView(){},setDrawingState:value=>visuals.push(value),clearDrawing(){}}}},Worker)
 const scope=Vue.effectScope(),api=scope.run(()=>component.setup({},{expose(){}}))
 try{
  hooks.start[0]();api.form.text='一';api.form.text='一零';api.form.text='一零九';api.form.z=80
  await new Promise(resolve=>setTimeout(resolve,170));await Vue.nextTick()
  assert.equal(jobs.length,1,'rapid edits create only the latest worker job')
  assert.equal(jobs[0].text,'一零九');assert.ok(visuals.at(-1).path.every(point=>point[2]===80))
  assert.equal(visuals.at(-1).valid,false);assert.deepEqual(calls,[],'draft never switches a device or plans robot motion')
  api.form.text='A';hooks.stop[0]();await new Promise(resolve=>setTimeout(resolve,150));assert.equal(jobs.length,1,'leaving cancels the pending preview')
 }finally{for(const stop of hooks.stop)await stop();scope.stop()}
})
