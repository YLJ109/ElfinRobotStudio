import * as numberDisplay from '../src/numberDisplay.js'
// Production CameraFrame consumes workstation observations; no browser inference.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {computed,ref,reactive,watch,effectScope,nextTick} from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import * as geometry from '../src/handGeometry.js'
import * as follow from '../src/handFollowInput.js'
import {createHandDisplayHold} from '../src/handDisplayHold.js'
import {handPipelineObservation} from '../src/handPipeline.js'
const source=readFileSync(new URL('../src/components/CameraFrame.vue',import.meta.url),'utf8')
const script=compileScript(parse(source).descriptor,{id:'pipeline-camera'}).content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await nextTick()}
function landmarks(){const points=Array.from({length:21},()=>({x:.5,y:.1}));for(const [i,x,y] of [[0,.5,.8],[5,.3,.4],[9,.45,.3],[13,.6,.35],[17,.7,.45]])points[i]={x,y};return points}
function fixture(t,{owned=true}={}){
 let time=1000,next=0,unmount=()=>{},listener=null,urls=0
 const timers=new Map(),revoked=[],requests=[],events={},pipelineCalls=[],releaseCalls=[],state=reactive({enabled:true,session:'camera-a'})
 const doc={hidden:false,addEventListener:(n,fn)=>events[n]=fn,removeEventListener:n=>delete events[n]}
 const props=reactive({session:'camera-a',stream:'color',hands:true,mapping:true,measure:false,actualFps:30})
 const owner=()=>owned?{source:'local',session:'camera-a'}:null
 // release 复刻真实所有权语义：只有"当前开启者就是本方"才真的下发关闭。
 const handPipeline={state,subscribe(fn){listener=fn;return ()=>{if(listener===fn)listener=null}},async configure(...args){pipelineCalls.push(args);state.enabled=args[0]},
  owner,async release(source,session){releaseCalls.push([source,session]);const o=owner()
   if(!o||o.source!==source||o.session!==session)return false
   if(!state.enabled)return false
   pipelineCalls.push([false,source,session]);state.enabled=false;return true}}
 const deps={...numberDisplay,ref,computed,watch,...geometry,...follow,createHandDisplayHold,handPipelineObservation,handPipeline,document:doc,performance:{now:()=>time},Date:{now:()=>1000000+time-1000},
  onBeforeUnmount:fn=>unmount=fn,URL:{createObjectURL:()=>`blob:${++urls}`,revokeObjectURL:url=>revoked.push(url)},
  setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next},clearTimeout:id=>timers.delete(id),
  fetch:(path,options)=>{requests.push({path,options});return new Promise(()=>{})},
  Worker:class{constructor(){throw Error('Browser inference is forbidden')}}}
 const component=new Function(...Object.keys(deps),script)(...Object.values(deps)),scope=effectScope()
 const camera=scope.run(()=>component.setup(props,{expose(){},emit(){}}))
 camera.image.value={naturalWidth:640,naturalHeight:480,removeAttribute(){}};camera.imageSize.value={width:640,height:480};camera.bounds.value={width:320,height:240}
 let disposed=false
 const h={camera,props,state,requests,pipelineCalls,releaseCalls,timers,revoked,doc,events,
  setTime(value){time=value},
  async frame(seq){camera.acceptFrame({seq,stamp:(1000000+time-1000)/1000,blob:new Blob([new Uint8Array([seq])])});await flush()},
  async result(seq,patch={}){
   const result={source:'local',session:props.session,epoch:1,seq,captured_at:time/1000,received_at:time/1000,valid_until:time/1000+.3,clock_domain:'server',control_valid:true,valid:true,coords:'image_normalized',observations:[{kind:'hand',landmarks:landmarks()}],model:{name:'hand',version:'safe-fixture'},inference_started_at:time/1000-.01,inference_finished_at:time/1000,...patch}
   listener?.({snapshot:{enabled:true,source:'local',session:props.session,epoch:1,server_now:time/1000,result},requestStartedAt:time,receivedAt:time});await flush()
  },
  disconnect(){listener?.(null)},tick(){for(const [id,job] of [...timers])if(job.ms===100){timers.delete(id);job.fn()}},
  dispose(){if(disposed)return;disposed=true;unmount();scope.stop()}}
 t.after(()=>h.dispose());return h
}
test('production camera never creates a frontend detector or uploads its workstation stream',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1)
 assert.ok(follow.getHandFollowInput().point)
 assert.ok(h.requests.every(({path})=>path.includes('/api/cameras/stream.mjpg')))
 assert.doesNotMatch(source,/new Worker|createImageBitmap|postMessage|hand-worker/)
})
test('all 21 points align with the one preview mirror and retain the capture sequence',async t=>{
 const h=fixture(t);await h.frame(5);await h.result(5)
 assert.equal(h.camera.handOverlay.value.points.length,21)
 const input=follow.getHandFollowInput();assert.equal(input.frameSeq,5)
 assert.ok(Math.abs(input.point.x-(1-geometry.palmFromLandmarks(landmarks()).x))<1e-9)
})
test('a fresh detector result may lead the preview without waiting or renewing its capture time',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(2)
 assert.equal(follow.getHandFollowInput().frameSeq,2);assert.ok(follow.getHandFollowInput().point)
 const timestamp=follow.getHandFollowInput().timestamp
 await h.frame(2);assert.equal(follow.getHandFollowInput().frameSeq,2);assert.equal(follow.getHandFollowInput().timestamp,timestamp)
})
test('duplicate polls and unchanged images never renew motion or display timestamps',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1)
 const timestamp=follow.getHandFollowInput().timestamp
 h.setTime(1100);h.camera.trackFrame();h.camera.trackFrame()
 assert.equal(follow.getHandFollowInput().timestamp,timestamp)
})
test('slow or expired inference can remain display-only but cannot become a motion input',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1,{captured_at:.5,valid_until:.8,control_valid:false})
 assert.equal(follow.getHandFollowInput().point,null)
 assert.ok(h.camera.shownPalm.value)
 assert.match(h.camera.handStatus.value,/过期/)
})
test('model error revokes the previous motion observation and reports failure',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1);await h.frame(2)
 await h.result(2,{valid:false,control_valid:false,error:'model unavailable',observations:[]})
 assert.equal(follow.getHandFollowInput().point,null);assert.match(h.camera.handStatus.value,/model unavailable/)
})
test('pipeline disconnection cannot reuse the last successful result',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1);h.disconnect()
 assert.equal(follow.getHandFollowInput().point,null);assert.match(h.camera.handStatus.value,/中断/)
})
for(const mutation of [r=>r.session='old',r=>r.epoch=0,r=>r.source='remote',r=>r.coords='pixels'])test('old/foreign inference identity cannot control this camera: '+mutation,async t=>{
 const h=fixture(t);await h.frame(1);const patch={};mutation(patch);await h.result(1,patch)
 assert.equal(follow.getHandFollowInput().point,null)
})
test('source changes and disabling detection clear both the overlay and motion observation',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1);h.props.hands=false;await flush()
 assert.equal(h.camera.shownPalm.value,null);assert.equal(follow.getHandFollowInput()?.point??null,null)
 h.props.session='camera-b';await flush();assert.equal(h.camera.shownPalm.value,null)
})
test('hidden page disposes the preview stream and cannot retain an old palm',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1)
 h.doc.hidden=true;h.events.visibilitychange();await flush()
 assert.equal(follow.getHandFollowInput(),null)
 assert.ok(h.requests[0].options.signal.aborted)
 assert.ok(h.revoked.length>0)
})
test('stalled stream expires even if the last model result and socket remain available',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1);h.setTime(2600);h.tick();await flush()
 assert.equal(follow.getHandFollowInput(),null);assert.equal(h.camera.fresh.value,false)
})
test('unmount clears timers, URLs, observations and explicitly disables owned backend inference',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1);h.dispose();await flush()
 assert.equal(follow.getHandFollowInput(),null);assert.equal(h.timers.size,0)
 assert.deepEqual(h.pipelineCalls,[[false,'local','camera-a']])
 // 必须走所有权释放通道，而不是直接 configure(false) 硬关。
 assert.deepEqual(h.releaseCalls,[['local','camera-a']])
})
test('a frame that does not own the detection cannot stop another operator\'s detector',async t=>{
 const h=fixture(t,{owned:false});await h.frame(1);await h.result(1);h.dispose();await flush()
 // 检测已被别人接管：本组件卸载时不得越权关停全局检测。
 assert.deepEqual(h.pipelineCalls,[])
 assert.equal(h.state.enabled,true)
 assert.deepEqual(h.releaseCalls,[['local','camera-a']])
})


test('fresh overlay actually uses measured time-based filtering rather than caching the raw skeleton first',async t=>{
 const h=fixture(t);await h.frame(1);await h.result(1)
 const before=h.camera.shownPalm.value.x
 const shifted=landmarks().map(p=>({...p,x:p.x+.1}))
 h.setTime(1050);await h.frame(2);await h.result(2,{observations:[{kind:'hand',landmarks:shifted}]})
 const raw=geometry.palmFromLandmarks(shifted).x
 const shown=h.camera.shownPalm.value.x
 assert.ok(shown>before && shown<raw,'display must ease toward the measured next palm rather than jump straight to raw landmarks')
 assert.equal(follow.getHandFollowInput().frameSeq,2)
 assert.equal(follow.getHandFollowInput().timestamp,1050)
})
