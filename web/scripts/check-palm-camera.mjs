// Offline contract tests: no browser page, camera device, backend or robot.
// node web/scripts/check-palm-camera.mjs
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { computed, ref, reactive, watch, effectScope, nextTick } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as geometry from '../src/handGeometry.js'
import * as follow from '../src/handFollowInput.js'
import { createHandDisplayHold } from '../src/handDisplayHold.js'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const flush = async () => { for (let i=0;i<8;i++) await Promise.resolve(); await nextTick() }
function landmarks() {
  const points = Array.from({length:21}, () => ({x:.5,y:.1}))
  for (const [i,x,y] of [[0,.5,.8],[5,.3,.4],[9,.45,.3],[13,.6,.35],[17,.7,.45]]) points[i]={x,y}
  return points
}
const near = (actual,expected) => assert.ok(Math.abs(actual-expected)<1e-9,`${actual} != ${expected}`)

test('CameraFrame display template and styles compile without changing the motion module',()=>{
  const {descriptor}=parse(read('../src/components/CameraFrame.vue'))
  const script=compileScript(descriptor,{id:'camera-display-test'})
  const template=compileTemplate({source:descriptor.template.content,filename:'CameraFrame.vue',id:'camera-display-test',compilerOptions:{bindingMetadata:script.bindings}})
  assert.deepEqual(template.errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'camera-display-test',scoped:style.scoped}).errors,[])
  assert.doesNotMatch(read('../src/handDisplayHold.js'),/publishHandFollowInput|fetch\(|\.postMessage\(/)
})

test('palm center averages wrist and all four MCPs; curled fingertips cannot move it', () => {
  const points=landmarks(), palm=geometry.palmFromLandmarks(points)
  near(palm.x,.51); near(palm.y,.46)
  near(palm.palm_scale,Math.hypot(.4,.05))
  near(palm.dx,-.05); near(palm.dy,-.5)
  for (const i of [4,8,12,16,20]) points[i]={x:.99,y:.99}
  assert.deepEqual(geometry.palmFromLandmarks(points),palm)
})
test('palm translation, scale and rotation retain their separate meanings', () => {
  const original=geometry.palmFromLandmarks(landmarks())
  const moved=landmarks().map(p=>({x:p.x*.5+.1,y:p.y*.5+.2}))
  const palm=geometry.palmFromLandmarks(moved)
  near(palm.x,original.x*.5+.1); near(palm.y,original.y*.5+.2)
  near(palm.palm_scale,original.palm_scale*.5)
  near(Math.atan2(palm.dy,palm.dx),Math.atan2(original.dy,original.dx))
})
test('missing/nonfinite/degenerate palm anchors produce absence rather than a false origin', () => {
  for (const invalid of [null,[],landmarks().map(()=>({x:.5,y:.5}))])
    assert.equal(geometry.palmFromLandmarks(invalid),null)
  for (const i of [0,5,9,13,17]) {
    const points=landmarks(); points[i]={x:NaN,y:.5}
    assert.equal(geometry.palmFromLandmarks(points),null)
  }
})
test('palm marker is small, finite and obeys the existing mirror/letterbox mapping', () => {
  assert.ok(geometry.palmMarkerDiameter(.1)>=8)
  assert.equal(geometry.palmMarkerDiameter(10),32)
  assert.equal(geometry.palmMarkerDiameter(NaN),8)
  assert.deepEqual(geometry.palmInView({x:.25,y:.5},400,400,640,480,true),{x:300,y:200})
})
test('depth range bus shares exactly the min/max circle normalization and never exposes mutable state', t => {
  t.after(()=>follow.setHandFollowDepthRange([.06,.30]))
  assert.equal(follow.setHandFollowDepthRange([.06,.30]),true)
  assert.deepEqual(follow.getHandFollowDepthRange(),[.06,.30])
  for(const [scale,diameter] of [[0,8],[.06,8],[.18,20],[.30,32],[1,32]])
    near(geometry.palmMarkerDiameter(scale,follow.getHandFollowDepthRange()),diameter)
  const supplied=[.1,.2]
  assert.equal(follow.setHandFollowDepthRange(supplied),true);supplied[0]=0
  const fetched=follow.getHandFollowDepthRange();fetched[1]=100
  assert.deepEqual(follow.getHandFollowDepthRange(),[.1,.2])
  near(geometry.palmMarkerDiameter(.15,follow.getHandFollowDepthRange()),20)
  for(const bad of [null,[],[.1],[0,.3],[-.1,.3],[.3,.1],[.1,.105],[NaN,.3],[.1,Infinity],['.1',.3]]) {
    assert.equal(follow.setHandFollowDepthRange(bad),false)
    assert.deepEqual(follow.getHandFollowDepthRange(),[.1,.2])
  }
  assert.equal(follow.setHandFollowDepthRange([.1,.11]),true)
})
test('all hand points use the same mirror, horizontal/vertical letterbox and invalid-point rejection', () => {
  for(const view of [[400,400,640,480],[400,200,640,480]]) {
    const overlay=geometry.handLandmarksInView(landmarks(),...view,true)
    assert.equal(overlay.points.length,21)
    for(const p of overlay.points) assert.deepEqual({x:p.x,y:p.y},geometry.palmInView(landmarks()[p.id],...view,true))
  }
  const points=landmarks();points[4]={x:Infinity,y:.3};points[8]={x:1.5,y:.5}
  const overlay=geometry.handLandmarksInView(points,400,400,640,480,true)
  assert.equal(overlay.points.length,19)
  assert.equal(overlay.bones.some(b=>b.to.id===4||b.to.id===8),false)
  assert.equal(geometry.cleanHandLandmarks(null).length,0)
  assert.ok(geometry.palmFromLandmarks(points),'bad fingertips cannot erase valid palm anchors')
  for(const transform of [p=>({x:.5+(p.x-.5)*1e-5,y:.5+(p.y-.5)*1e-5}),p=>({x:p.x+5,y:p.y})])
    assert.equal(geometry.palmFromLandmarks(landmarks().map(transform)),null)
})
test('elapsed-time palm smoothing has the same response at 15, 30 and 60fps, with no prediction', () => {
  const results=[]
  for(const fps of [15,30,60]) {
    const tracker=follow.createPalmTracker(), original=landmarks()
    tracker.update({landmarks:original,timestamp:1000,frameSeq:1},1000)
    const shifted=original.map(p=>({x:p.x+.04,y:p.y-.03}))
    let state
    for(let i=1;i<=fps/5;i++) {
      const timestamp=1000+i*1000/fps
      state=tracker.update({landmarks:shifted,timestamp,frameSeq:i+1},timestamp)
    }
    results.push(state.point)
    near(state.point.x,.51+.04*(1-Math.exp(-200/45)))
    near(state.point.y,.46-.03*(1-Math.exp(-200/45)))
    assert.equal(tracker.read(1220).point.x,state.point.x)
  }
  for(const value of results) {near(value.x,results[0].x);near(value.y,results[0].y)}
})
test('all 21 landmarks suppress alternating jitter while following a deliberate step promptly', () => {
  const tracker=follow.createPalmTracker(), original=landmarks()
  tracker.update({landmarks:original,timestamp:1000,frameSeq:1},1000)
  let previous=original, rawTravel=0, filteredTravel=0, state
  for(let i=1;i<=30;i++) {
    const offset=i%2 ? .01 : -.01, timestamp=1000+i*1000/30
    const points=original.map(p=>({x:p.x+offset,y:p.y}))
    state=tracker.update({landmarks:points,timestamp,frameSeq:i+1},timestamp)
    for(let j=0;j<21;j++) filteredTravel+=Math.abs(state.landmarks[j].x-previous[j].x)
    rawTravel+=21*(i===1 ? .01 : .02);previous=state.landmarks
  }
  assert.ok(filteredTravel<rawTravel*.5)
  for(let i=1;i<=6;i++) {
    const timestamp=2000+i*1000/30
    state=tracker.update({landmarks:original.map(p=>({x:p.x+.08,y:p.y})),timestamp,frameSeq:31+i},timestamp)
  }
  for(let j=0;j<21;j++) assert.ok(Math.abs(state.landmarks[j].x-original[j].x-.08)<.002)
  assert.equal(state.status,'tracking')
})

test('palm orientation smoothing takes the short path across -180/180 degrees', () => {
  const tracker=follow.createPalmTracker()
  function rotated(angle) {
    const points=landmarks();points[0]={x:.6,y:.5}
    points[9]={x:.6+.3*Math.cos(angle),y:.5+.3*Math.sin(angle)}
    return points
  }
  tracker.update({landmarks:rotated(Math.PI-.02),timestamp:1000,frameSeq:1},1000)
  const state=tracker.update({landmarks:rotated(-Math.PI+.02),timestamp:1050,frameSeq:2},1050)
  assert.ok(Math.abs(Math.atan2(state.point.dy,state.point.dx))>3)
})
test('仅显示的骨架补帧位于实测端点内，不更改采样坐标或观测年龄', () => {
  const tracker=follow.createPalmTracker(), presenter=follow.createHandOverlayPresentation()
  const first=tracker.update({landmarks:landmarks(),timestamp:1000,frameSeq:1},1000)
  presenter.update(first,1000)
  const next=tracker.update({landmarks:landmarks().map(p=>({...p,x:p.x+.03})),timestamp:1166,frameSeq:2},1166)
  const measured=structuredClone(next)
  presenter.update(next,1166)
  const halfway=presenter.read(1206)
  assert.ok(halfway.animating)
  assert.ok(halfway.point.x>first.point.x && halfway.point.x<next.point.x)
  for(let i=0;i<21;i++) assert.ok(halfway.landmarks[i].x>=first.landmarks[i].x && halfway.landmarks[i].x<=next.landmarks[i].x)
  assert.deepEqual(next,measured)
  assert.equal(tracker.read(1206).timestamp,1166)
  assert.equal(presenter.read(1246).animating,false)
  assert.deepEqual(presenter.read(3000).point,next.point,'显示也不外推')
  presenter.reset();assert.equal(presenter.read(3001).point,null)
})

test('tracker rejects stale/future observations, never renews a hold from malformed results and resets cleanly', () => {
  const tracker=follow.createPalmTracker()
  assert.equal(tracker.update({landmarks:landmarks(),timestamp:1100,frameSeq:1},1000).point,null)
  assert.equal(tracker.update({landmarks:landmarks(),timestamp:600,frameSeq:2},1000).point,null)
  tracker.update({landmarks:landmarks(),timestamp:1000,frameSeq:3},1030)
  const held=tracker.update({landmarks:[],timestamp:1150,frameSeq:4},1150)
  assert.equal(held.status,'holding');assert.equal(held.timestamp,1000);assert.equal(held.expiresAt,1480)
  assert.equal(tracker.read(1480).point,null)
  tracker.update({landmarks:landmarks(),timestamp:1300,frameSeq:5},1590)
  assert.equal(tracker.read(1599).status,'tracking')
  const display=tracker.read(1600)
  assert.ok(display.point);assert.equal(display.status,'holding')
  assert.ok(follow.handFollowInputIssue({session:'s',stream:'color',fresh:true,width:640,height:480,
    frameSeq:5,hands:true,point:display.point,timestamp:display.timestamp,trackingState:display.status},1600),
    'Display debounce must never extend motion freshness beyond 300ms')
  assert.equal(tracker.read(1950).point,null)
  tracker.reset();assert.equal(tracker.read(1601).point,null)
  const recovered=tracker.update({landmarks:landmarks(),timestamp:1602,frameSeq:1},1602)
  near(recovered.point.x,.51)
})

const encoder=new TextEncoder()
function part(seq,stamp=1000,payload=new Uint8Array([255,216,13,10,13,10,255,217])) {
  const head=encoder.encode(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${payload.length}\r\nX-Frame-Seq: ${seq}\r\nX-Frame-Timestamp: ${stamp}\r\n\r\n`)
  const bytes=new Uint8Array(head.length+payload.length+2)
  bytes.set(head);bytes.set(payload,head.length);bytes.set([13,10],head.length+payload.length)
  return bytes
}
test('MJPEG parser handles arbitrary single-byte chunks and binary header-like JPEG bytes', async () => {
  const frames=[], parser=follow.createCameraMultipartParser(packet=>frames.push(packet))
  for (const seq of [1,2]) for (const byte of part(seq)) parser.push(new Uint8Array([byte]))
  assert.deepEqual(frames.map(f=>f.seq),[1,2])
  assert.deepEqual([...new Uint8Array(await frames[0].blob.arrayBuffer())],[255,216,13,10,13,10,255,217])
  assert.equal(frames[0].stamp,1000)
})
test('MJPEG duplicate/older capture IDs never create another frame, even with a changed timestamp', () => {
  const frames=[],parser=follow.createCameraMultipartParser(packet=>frames.push(packet))
  for (const [seq,stamp] of [[3,1000],[3,1001],[2,1002],[4,1003]]) parser.push(part(seq,stamp))
  assert.deepEqual(frames.map(f=>f.seq),[3,4])
})
test('MJPEG malformed lengths, missing identity and unbounded headers are rejected', () => {
  for (const header of [
    '--frame\r\nContent-Length: 0\r\nX-Frame-Seq: 1\r\nX-Frame-Timestamp: 1000\r\n\r\n',
    '--frame\r\nContent-Length: 999999999\r\nX-Frame-Seq: 1\r\nX-Frame-Timestamp: 1000\r\n\r\n',
    '--frame\r\nContent-Length: 10\r\nX-Frame-Timestamp: 1000\r\n\r\n',
    'x'.repeat(8193),
  ]) assert.throws(()=>follow.createCameraMultipartParser(()=>{}).push(encoder.encode(header)))
})
test('FPS is completed new frames over elapsed time, not the request rate or timer ticks', () => {
  assert.equal(follow.measuredFrameRate([],1000),0)
  assert.equal(follow.measuredFrameRate([1000],1000),0)
  const twenty=Array.from({length:20},(_,i)=>i*50)
  near(follow.measuredFrameRate(twenty,950),20)
  near(follow.measuredFrameRate(Array.from({length:60},(_,i)=>i*1000/60),59*1000/60),60)
  assert.equal(follow.measuredFrameRate(twenty,2000),0)
})

// Execute the actual classic worker with a fake detector. The wasm/camera never loads.
test('worker processes a capture only once, returns all 21 points and actual inference duration', async () => {
  const messages=[], calls=[], points=landmarks()
  let time=0,closed=0
  const context={self:{postMessage:message=>messages.push(message)},
    importScripts:()=>{},performance:{now:()=>time},
    Vision:{FilesetResolver:{forVisionTasks:async()=>({})},HandLandmarker:{createFromOptions:async(_,options)=>{
      assert.equal(options.baseOptions.delegate,'CPU')
      assert.equal(options.minHandDetectionConfidence,.5)
      assert.equal(options.minHandPresenceConfidence,.5)
      assert.equal(options.minTrackingConfidence,.5)
      return {detectForVideo:(_,timestamp)=>{calls.push(timestamp);time+=12.5;return {landmarks:[points]}}}
    }}}}
  vm.runInNewContext(read('../public/vision/hand-worker.js'),context)
  await context.self.onmessage({data:{type:'init'}})
  for (const seq of [1,1,2]) await context.self.onmessage({data:{type:'frame',frameSeq:seq,timestamp:seq*50,bitmap:{close(){closed++}}}})
  assert.equal(calls.length,2);assert.equal(closed,3)
  assert.equal(messages[0].delegate,'CPU')
  const results=messages.filter(m=>m.type==='result')
  assert.deepEqual(results.map(m=>m.frameSeq),[1,2])
  assert.equal(results[0].inferenceMs,12.5)
  assert.deepEqual(geometry.palmFromLandmarks(results[0].landmarks),geometry.palmFromLandmarks(points))
  assert.equal(results[0].landmarks.length,21)
  near(results[0].landmarks[8].x,points[8].x)
})
test('actual worker empty results and detector failures acknowledge processing and always close the bitmap', async () => {
  const messages=[];let closed=0,throws=false
  const context={self:{postMessage:message=>messages.push(message)},importScripts:()=>{},performance:{now:()=>1000},
    Vision:{FilesetResolver:{forVisionTasks:async()=>({})},HandLandmarker:{createFromOptions:async()=>({
      detectForVideo:()=>{if(throws)throw new Error('offline detector failure');return {landmarks:[]}},
    })}}}
  vm.runInNewContext(read('../public/vision/hand-worker.js'),context)
  await context.self.onmessage({data:{type:'init'}})
  await context.self.onmessage({data:{type:'frame',frameSeq:1,timestamp:1000,bitmap:{close(){closed++}}}})
  assert.equal(messages.at(-1).type,'result');assert.equal(messages.at(-1).landmarks,null)
  throws=true
  await context.self.onmessage({data:{type:'frame',frameSeq:2,timestamp:1050,bitmap:{close(){closed++}}}})
  assert.equal(messages.at(-1).type,'error');assert.match(messages.at(-1).message,/offline detector failure/)
  assert.equal(closed,2)
})

test('worker always uses the requested CPU delegate even when worker GPU is available', async () => {
  for(const failGpu of [false,true]) {
    const calls=[],messages=[]
    const context={self:{postMessage:m=>messages.push(m)},importScripts(){},
      OffscreenCanvas:class {constructor(w,h){this.width=w;this.height=h}},
      Vision:{FilesetResolver:{forVisionTasks:async()=>({})},HandLandmarker:{createFromOptions:async(_,options)=>{
        calls.push(options.baseOptions.delegate)
        if(failGpu && options.baseOptions.delegate==='GPU')throw new Error('worker WebGL unavailable')
        return {detectForVideo:()=>({landmarks:[]})}
      }}}}
    vm.runInNewContext(read('../public/vision/hand-worker.js'),context)
    await context.self.onmessage({data:{type:'init'}})
    assert.deepEqual(calls,['CPU'])
    assert.equal(messages.at(-1).type,'ready')
    assert.equal(messages.at(-1).delegate,'CPU')
  }
})

const cameraScript=compileScript(parse(read('../src/components/CameraFrame.vue')).descriptor,{id:'camera-test'}).content
  .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
function cameraFixture(t,{bitmapFactory,fetcher,animateOverlay=false}={}) {
  let time=1000,nextTimer=0,unmount=()=>{},urlSerial=0
  const timers=new Map(), workers=[],bitmaps=[], requests=[], revoked=[],listeners={},animationFrames=new Map()
  const doc={hidden:false,addEventListener:(key,fn)=>{listeners[key]=fn},removeEventListener:key=>{delete listeners[key]}}
  const props=reactive({session:'camera-a',stream:'color',hands:true,measure:false,mapping:true,frameSeq:1,frameAt:1000,actualFps:60})
  class FakeWorker {
    constructor(){this.messages=[];this.terminated=false;workers.push(this)}
    postMessage(message){if(this.rejectFrame && message.type==='frame')throw new Error('transfer failed');this.messages.push(message)}
    terminate(){this.terminated=true}
    ready(frameInput='bitmap'){this.onmessage({data:{type:'ready',delegate:'CPU',frameInput}})}
    complete(palm=landmarks()) {
      const frame=this.messages.filter(m=>m.type==='frame').at(-1)
      this.onmessage({data:{type:'result',frameSeq:frame.frameSeq,timestamp:frame.timestamp,landmarks:palm,inferenceMs:10}})
    }
  }
  const deps={ref,watch,computed,...geometry,...follow,createHandDisplayHold,document:doc,performance:{now:()=>time},Date:{now:()=>1000000+time-1000},
    onBeforeUnmount:fn=>{unmount=fn},Worker:FakeWorker,
    URL:{createObjectURL:()=>`blob:fake-${++urlSerial}`,revokeObjectURL:url=>revoked.push(url)},
    setTimeout:(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms});return id},clearTimeout:id=>timers.delete(id),
    fetch:(path,options)=>{requests.push({path,options});return fetcher ? fetcher(path,options) : new Promise(()=>{})},
    createImageBitmap:async(blob,options)=>{
      const bitmap=bitmapFactory ? await bitmapFactory(blob,options) : {width:640,height:480,closed:false,close(){this.closed=true}}
      bitmaps.push(bitmap);return bitmap
    }}
  if (animateOverlay) {
    deps.requestAnimationFrame = fn => { const id=++nextTimer;animationFrames.set(id,fn);return id }
    deps.cancelAnimationFrame = id => animationFrames.delete(id)
  }
  const component=new Function(...Object.keys(deps),cameraScript)(...Object.values(deps))
  const scope=effectScope()
  const camera=scope.run(()=>component.setup(props,{expose(){},emit(){}}))
  camera.image.value={naturalWidth:640,naturalHeight:480,removeAttribute(){}}
  camera.imageSize.value={width:640,height:480}
  camera.bounds.value={width:320,height:240}
  let disposed=false
  const fixture={camera,props,workers,timers,bitmaps,requests,revoked,listeners,doc,animationFrames,
    setTime:value=>{time=value},
    packet(seq){return {seq,stamp:(1000000+time-1000)/1000,blob:new Blob([new Uint8Array([seq])],{type:'image/jpeg'})}},
    async frame(seq){camera.acceptFrame(this.packet(seq));await flush()},
    dispose(){if(!disposed){disposed=true;unmount();scope.stop()}},
  }
  t.after(()=>fixture.dispose())
  return fixture
}

test('真实相机组件补帧只更新骨架，不重新发布手势，卸载取消动画', async t => {
  const h=cameraFixture(t,{animateOverlay:true})
  await h.frame(1);h.workers[0].ready();await flush();h.workers[0].complete();await flush()
  const first=h.camera.shownPalm.value.x
  h.setTime(1166);await h.frame(3)
  h.workers[0].complete(landmarks().map(p=>({...p,x:p.x+.03})));await flush()
  const observed=follow.getHandFollowInput(), measured=h.camera.palm.value.x
  assert.ok(measured>first)
  assert.equal(h.camera.shownPalm.value.x,first)
  assert.equal(h.animationFrames.size,1)
  h.setTime(1206)
  const [id,draw]=h.animationFrames.entries().next().value
  h.animationFrames.delete(id);draw()
  assert.ok(h.camera.shownPalm.value.x>first && h.camera.shownPalm.value.x<measured)
  assert.equal(follow.getHandFollowInput(),observed,'显示补帧不得发送新采样')
  assert.equal(observed.timestamp,1166)
  h.dispose();assert.equal(h.animationFrames.size,0)
})

test('real CameraFrame never repeats inference of an unchanged image despite ticks/result callbacks', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  await h.camera.trackFrame();await h.camera.trackFrame()
  await h.frame(1)
  assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),[1])
  assert.equal(h.bitmaps.length,1)
  near(h.camera.palm.value.x,.51)
  near(follow.getHandFollowInput().point.x,.49) // exactly one mirror
  near(follow.getHandFollowInput().point.dx,.05)
})
test('real CameraFrame detects every capture when CPU keeps up, reporting 60 from 60fps input', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  for(let seq=2;seq<=60;seq++) {
    h.setTime(1000+(seq-1)*1000/60);await h.frame(seq)
    h.workers[0].complete();await flush()
  }
  assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),Array.from({length:60},(_,i)=>i+1))
  near(h.camera.detectionFps.value,60)
})

test('busy CPU accepts the next consecutive capture immediately on completion without queueing', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.setTime(1067);await h.frame(2)
  assert.equal(h.workers[0].messages.filter(m=>m.type==='frame').length,1)
  h.workers[0].complete();await flush()
  assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),[1,2])
  h.setTime(1134);await h.frame(3);h.setTime(1201);await h.frame(4)
  assert.equal(h.workers[0].messages.filter(m=>m.type==='frame').length,2)
  h.workers[0].complete();await flush()
  assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),[1,2,4])
  h.workers[0].complete();await flush()
  await h.camera.trackFrame()
  assert.equal(h.workers[0].messages.filter(m=>m.type==='frame').length,3)
})
test('60fps capture with 20fps inference retains only the latest pending frame and reports 20', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  for(let n=1;n<=20;n++) {
    for(let j=1;j<=3;j++) {
      h.setTime(1000+((n-1)*3+j)*1000/60);await h.frame((n-1)*3+j+1)
    }
    h.workers[0].complete();await flush()
  }
  const frames=h.workers[0].messages.filter(m=>m.type==='frame')
  assert.deepEqual(frames.slice(0,4).map(m=>m.frameSeq),[1,4,7,10])
  assert.equal(frames.length,21) // 20 completed, one newly dispatched
  near(h.camera.detectionFps.value,20)
  assert.equal(follow.getHandFollowInput().inferenceMs,10)
})
test('new frame arriving during bitmap creation cannot replace the immutable submitted capture', async t => {
  let finish
  const h=cameraFixture(t,{bitmapFactory:()=>new Promise(resolve=>{finish=resolve})})
  await h.frame(1);h.workers[0].ready();await flush()
  await h.frame(2);await h.frame(3)
  finish({close(){}});await flush()
  assert.equal(h.workers[0].messages.filter(m=>m.type==='frame')[0].frameSeq,1)
  h.workers[0].complete();await flush()
  finish({close(){}});await flush()
  assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),[1,3])
})
test('late bitmap after session switch is closed and cannot reach the replacement worker', async t => {
  let finish
  const h=cameraFixture(t,{bitmapFactory:()=>new Promise(resolve=>{finish=resolve})})
  await h.frame(1);h.workers[0].ready();await flush()
  const old=h.workers[0];h.props.session='camera-b';await flush()
  let closed=false;finish({close(){closed=true}});await flush()
  assert.equal(closed,true);assert.equal(old.terminated,true)
  assert.equal(old.messages.some(m=>m.type==='frame'),false)
  assert.equal(follow.getHandFollowInput(),null)
})
test('late fetch response after disposal cancels its body; no camera or stream is orphaned', async t => {
  let finish,cancelled=false
  const h=cameraFixture(t,{fetcher:()=>new Promise(resolve=>{finish=resolve})})
  h.dispose()
  finish({body:{cancel:async()=>{cancelled=true}}});await flush()
  assert.equal(cancelled,true)
  assert.equal(h.requests[0].options.signal.aborted,true)
  assert.equal(h.timers.size,0)
  assert.equal(Object.keys(h.listeners).length,0)
})
test('disposal retires worker, object URLs, pending inference, freshness timers and publication', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  h.dispose()
  assert.equal(h.workers[0].terminated,true)
  assert.ok(h.revoked.length>0);assert.equal(h.timers.size,0)
  assert.equal(follow.getHandFollowInput(),null)
})

test('JPEG source swaps cannot destroy a loading or ready detector while captured frames stay fresh', async t => {
  const h=cameraFixture(t);await h.frame(1)
  const worker=h.workers[0]
  // Browsers temporarily report naturalWidth=0 after every img.src swap, even
  // though the continuous source is healthy and its dimensions are established.
  h.camera.image.value.naturalWidth=0;h.camera.image.value.naturalHeight=0
  h.camera.tick(1);await flush()
  assert.equal(worker.terminated,false)
  assert.equal(h.workers.length,1)
  worker.ready();await flush();worker.complete();await flush()
  h.setTime(1067);h.camera.tick(1);await flush()
  assert.equal(worker.terminated,false)
  assert.equal(h.workers.length,1)
  assert.equal(h.camera.fresh.value,true)
})

test('one missed 15fps detection holds the last palm honestly, without renewing its observation', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  const previous=follow.getHandFollowInput()
  h.setTime(1134);await h.frame(3);h.workers[0].complete(null);await flush()
  assert.ok(h.camera.palm.value,'a single empty result must not erase the last palm')
  const held=follow.getHandFollowInput()
  assert.equal(held.trackingState,'holding')
  assert.match(held.detectorStatus,/短暂.*保持/)
  assert.equal(held.timestamp,previous.timestamp)
  assert.equal(held.frameSeq,previous.frameSeq)
})

test('visual debounce bridges short misses but stale motion is rejected and the overlay still expires', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  for(let seq=2;seq<=9;seq++) {
    h.setTime(1000+(seq-1)*67);await h.frame(seq)
    h.workers[0].complete(null);await flush()
    if(seq===6) {
      assert.ok(h.camera.palm.value,'A brief display hold must not flicker away at one slow frame')
      assert.ok(follow.handFollowInputIssue(follow.getHandFollowInput(),1335),'Stale held points cannot drive the robot')
    }
  }
  assert.equal(h.camera.palm.value,null)
  const lost=follow.getHandFollowInput()
  assert.equal(lost.point,null);assert.equal(lost.trackingState,'lost')
  assert.ok(follow.handFollowInputIssue(lost,1536))
  assert.equal(h.camera.handLandmarks.value.length,0)
})

test('camera exposes all 21 points to a mirror-and-letterbox aligned lightweight overlay', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  assert.equal(h.camera.handOverlay.value.points.length,21)
  const wrist=h.camera.handOverlay.value.points.find(p=>p.id===0)
  near(wrist.x,160);near(wrist.y,192)
  assert.equal(h.camera.handOverlay.value.bones.length,21)
  const source=read('../src/components/CameraFrame.vue')
  assert.match(source,/class="hand-skeleton"/)
  assert.match(source,/\.hand-skeleton\s*\{[^}]*pointer-events:\s*none/s)
})
test('motion cursor expires unchanged while display alone holds 1s without another worker result', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  h.setTime(1300);h.timers.get(h.camera.cursorTimer).fn()
  assert.equal(follow.getHandFollowInput().trackingState,'holding')
  h.setTime(1450);h.timers.get(h.camera.cursorTimer).fn()
  assert.equal(follow.getHandFollowInput().point,null)
  assert.equal(h.camera.handOverlay.value.points.length,21)
  assert.equal(h.camera.cursorTimer,null)
  const expiredInput=follow.getHandFollowInput()
  const frameCount=h.workers[0].messages.filter(m=>m.type==='frame').length
  h.setTime(1999);assert.equal(h.camera.handOverlay.value.points.length,21)
  h.setTime(2000);h.timers.get(h.camera.displayExpiryTimer).fn()
  assert.equal(h.camera.handOverlay.value.points.length,0)
  assert.equal(follow.getHandFollowInput(),expiredInput,'display expiry must not publish input')
  assert.equal(h.workers[0].messages.filter(m=>m.type==='frame').length,frameCount)
})

test('display hold is bounded by last valid detection; empty, duplicate and old frames never renew it',()=>{
  const hold=createHandDisplayHold(),tracker=follow.createPalmTracker()
  const sample=tracker.update({landmarks:landmarks(),timestamp:1000,frameSeq:1},1020)
  const original=structuredClone(sample)
  hold.update(sample,1020)
  for(const now of [1100,1400,1800,2019]) {
    hold.update({...sample,frameSeq:1,receivedAt:now},now)
    hold.update({point:null,landmarks:[],frameSeq:2},now)
    assert.ok(hold.read(now).point)
  }
  assert.equal(hold.read(2020).point,null)
  assert.equal(hold.update(sample,2021).point,null)
  assert.deepEqual(sample,original)
  hold.reset();assert.equal(hold.read(2021).point,null)
})

test('display does not flash across repeated short misses and a fresh detection renews only display deadline',async t=>{
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush();h.workers[0].complete();await flush()
  const diameter=h.camera.markerDiameter.value
  for(let seq=2;seq<=10;seq++) {
    h.setTime(1000+(seq-1)*100);await h.frame(seq);h.workers[0].complete(null);await flush()
    assert.equal(h.camera.handOverlay.value.points.length,21)
    near(h.camera.markerDiameter.value,diameter)
    if(seq>=4)assert.ok(follow.handFollowInputIssue(follow.getHandFollowInput(),1000+(seq-1)*100))
  }
  h.setTime(1950);await h.frame(11);h.workers[0].complete();await flush()
  const displayTimer=h.timers.get(h.camera.displayExpiryTimer)
  assert.equal(displayTimer.ms,1000)
  assert.equal(follow.getHandFollowInput().timestamp,1950)
  assert.equal(h.camera.handOverlay.value.points.length,21)
})

test('stopping capture, disabling hands, switching session/source and unmount clear held overlay immediately',async t=>{
  for(const action of [h=>h.camera.clear(),h=>{h.props.hands=false},h=>{h.props.session='camera-b'},
    h=>{h.props.stream='depth'},h=>h.dispose()]) {
    const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush();h.workers[0].complete();await flush()
    h.setTime(1600);h.camera.publishFollow();assert.equal(h.camera.handOverlay.value.points.length,21)
    action(h);await flush()
    assert.equal(h.camera.shownPalm.value,null);assert.equal(h.camera.handOverlay.value.points.length,0)
    assert.equal(h.camera.displayExpiryTimer,null)
    h.dispose()
  }
})

test('slow valid inference may draw for 1s but never becomes motion-fresh or changes frame timestamps',async t=>{
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.setTime(1400);h.workers[0].complete();await flush()
  assert.equal(h.camera.palm.value,null,'400ms observation is invalid for motion')
  assert.equal(follow.getHandFollowInput().point,null)
  assert.ok(follow.handFollowInputIssue(follow.getHandFollowInput(),1400))
  assert.equal(h.camera.handOverlay.value.points.length,21)
  assert.equal(h.timers.get(h.camera.displayExpiryTimer).ms,1000)
  const input=follow.getHandFollowInput()
  h.setTime(2399);assert.ok(h.camera.displayHold.read(2399).point)
  h.setTime(2400);h.timers.get(h.camera.displayExpiryTimer).fn()
  assert.equal(h.camera.handOverlay.value.points.length,0)
  assert.equal(follow.getHandFollowInput(),input)
})

test('display-only raw results reject stale, future, malformed and replayed measurements',()=>{
  const hold=createHandDisplayHold()
  for(const value of [{timestamp:0,frameSeq:1},{timestamp:1001,frameSeq:1},{timestamp:900,frameSeq:0}])
    assert.equal(hold.observe({...value,landmarks:landmarks()},1000).point,null)
  hold.observe({timestamp:900,frameSeq:1,landmarks:landmarks()},1000)
  assert.equal(hold.read(1999).expiresAt,2000)
  hold.observe({timestamp:1900,frameSeq:1,landmarks:landmarks()},1999)
  hold.observe({timestamp:1950,frameSeq:2,landmarks:[]},1999)
  assert.equal(hold.read(2000).point,null)
})
test('no-hand detections count toward measured detection FPS but cannot create a target', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete(null);await flush()
  h.setTime(1134);await h.frame(3);h.workers[0].complete(null);await flush()
  near(h.camera.detectionFps.value,1000/134)
  assert.equal(follow.getHandFollowInput().point,null)
})
test('marker reads the shared depth range on each real palm update; no competing visual scale', async t => {
  t.after(()=>follow.setHandFollowDepthRange([.06,.30]))
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  const scale=h.camera.palm.value.palm_scale
  follow.setHandFollowDepthRange([scale,scale+.1])
  h.setTime(1134);await h.frame(3);h.workers[0].complete();await flush()
  near(h.camera.markerDiameter.value,8)
  follow.setHandFollowDepthRange([scale-.1,scale+.1])
  h.setTime(1268);await h.frame(5);h.workers[0].complete();await flush()
  near(h.camera.markerDiameter.value,20)
})
test('failed worker transfer closes its bitmap and hides any tracked position', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].rejectFrame=true;h.workers[0].ready();await flush()
  assert.equal(h.bitmaps[0].closed,true)
  assert.equal(h.workers[0].terminated,true)
  assert.equal(follow.getHandFollowInput().point,null)
  assert.match(follow.getHandFollowInput().detectorError,/transfer failed/)
})
test('hiding the page disposes the stream, worker, points and all pending timers', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  h.doc.hidden=true;h.listeners.visibilitychange();await flush()
  assert.equal(h.workers[0].terminated,true)
  assert.equal(h.camera.handLandmarks.value.length,0)
  assert.equal(h.timers.size,0)
  assert.equal(follow.getHandFollowInput(),null)
})
test('a delayed JPEG cannot renew a stale palm just because its network receipt is new', async t => {
  const h=cameraFixture(t)
  const packet=h.packet(1);packet.stamp-=.35
  h.camera.acceptFrame(packet);await flush();h.workers[0].ready();await flush()
  h.workers[0].complete();await flush()
  assert.equal(h.camera.fresh.value,true,'the video may still display a delayed camera frame')
  assert.equal(follow.getHandFollowInput().point,null,'motion must reject the stale observation')
  assert.equal(h.camera.handOverlay.value.points.length,0)
})

test('worker JPEG decode is off-thread, preserves timestamps/21 points and closes decoded resources', async () => {
  let clock=0,closed=0,decoded=0
  const messages=[],blob=new Blob(['offline jpeg']),points=landmarks()
  const context={self:{postMessage:m=>messages.push(m)},importScripts(){},performance:{now:()=>clock},
    createImageBitmap:async (input,options)=>{
      assert.equal(input,blob);assert.equal(options.resizeWidth,640);assert.equal(options.resizeHeight,480)
      decoded++;clock+=7
      return {width:640,height:480,close(){closed++}}
    },Vision:{FilesetResolver:{forVisionTasks:async()=>({})},HandLandmarker:{createFromOptions:async()=>({
      detectForVideo:(_,timestamp)=>{assert.equal(timestamp,1234);clock+=95;return {landmarks:[points]}},
    })}}}
  vm.runInNewContext(read('../public/vision/hand-worker.js'),context)
  await context.self.onmessage({data:{type:'init'}})
  assert.equal(messages[0].frameInput,'jpeg')
  await context.self.onmessage({data:{type:'frame',blob,frameSeq:1,timestamp:1234,width:640,height:480}})
  const result=messages.at(-1)
  assert.equal(result.decodeMs,7);assert.equal(result.inferenceMs,95)
  assert.equal(result.timestamp,1234);assert.equal(result.landmarks.length,21)
  assert.equal(result.inputWidth,640);assert.equal(result.inputHeight,480)
  assert.equal(decoded,1);assert.equal(closed,1)
})

test('worker decode in flight cannot overlap inference, double-unlock or reprocess duplicate frames', async () => {
  let finish,decodes=0,closed=0,calls=0
  const messages=[]
  const context={self:{postMessage:m=>messages.push(m)},importScripts(){},performance:{now:()=>0},
    createImageBitmap:()=>{decodes++;return new Promise(resolve=>{finish=resolve})},
    Vision:{FilesetResolver:{forVisionTasks:async()=>({})},HandLandmarker:{createFromOptions:async()=>({
      detectForVideo:()=>{calls++;return {landmarks:[]}},
    })}}}
  vm.runInNewContext(read('../public/vision/hand-worker.js'),context)
  await context.self.onmessage({data:{type:'init'}})
  const data={type:'frame',blob:new Blob(['x']),frameSeq:1,timestamp:1,width:640,height:480}
  const pending=context.self.onmessage({data})
  await context.self.onmessage({data});await context.self.onmessage({data:{...data,frameSeq:2}})
  assert.equal(decodes,1)
  finish({width:640,height:480,close(){closed++}});await pending
  await context.self.onmessage({data})
  assert.equal(calls,1);assert.equal(closed,1)
})

test('CameraFrame JPEG protocol never decodes on main thread, keeps latest-only and one mirror', async t => {
  const h=cameraFixture(t,{bitmapFactory:()=>{throw new Error('main thread decode forbidden')}})
  await h.frame(1);h.workers[0].ready('jpeg');await flush()
  const first=h.workers[0].messages.at(-1)
  assert.equal(first.blob,h.camera.latestFrame.blob);assert.equal(first.width,640);assert.equal(first.height,480)
  assert.equal(first.bitmap,undefined);assert.equal(h.bitmaps.length,0)
  h.setTime(1067);await h.frame(2);h.setTime(1134);await h.frame(3)
  h.workers[0].complete();await flush()
  assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),[1,3])
  near(follow.getHandFollowInput().point.x,.49)
  assert.equal(follow.getHandFollowInput().timestamp,1000)
  assert.equal(follow.getHandFollowInput().roundTripMs,134)
  assert.equal(follow.handFollowInputIssue(follow.getHandFollowInput(),1300),'手掌检测已失效')
})

test('CameraFrame worker decode protocol bounds large inputs without resizing the displayed image', async t => {
  const h=cameraFixture(t)
  h.camera.imageSize.value={width:1920,height:1080}
  await h.frame(1);h.workers[0].ready('jpeg');await flush()
  const frame=h.workers[0].messages.at(-1)
  assert.equal(frame.width,640);assert.equal(frame.height,360)
  assert.deepEqual(h.camera.imageSize.value,{width:1920,height:1080})
})

// Reproduce the former whole-buffer concatenation with the same valid binary
// stream. This is a parser/copy microbenchmark, not JPEG or device FPS.
test('fragmented JPEG parser removes quadratic JS payload copies: measured offline baseline', async () => {
  const payload=new Uint8Array(65536).fill(123), chunks=[]
  for(let seq=1;seq<=20;seq++) {
    const bytes=part(seq,1000,payload)
    for(let offset=0;offset<bytes.length;offset+=1024) chunks.push(bytes.subarray(offset,offset+1024))
  }
  function baseline(onFrame) {
    let buffer=new Uint8Array(),header=null
    return {push(chunk){
      const joined=new Uint8Array(buffer.length+chunk.length)
      joined.set(buffer);joined.set(chunk,buffer.length);buffer=joined
      while(buffer.length){
        if(!header){
          let end=-1
          for(let i=0;i+3<buffer.length;i++)if(buffer[i]===13&&buffer[i+1]===10&&buffer[i+2]===13&&buffer[i+3]===10){end=i;break}
          if(end<0)return
          const text=new TextDecoder().decode(buffer.subarray(0,end))
          header={length:Number(text.match(/Content-Length: (\d+)/)[1])}
          buffer=buffer.subarray(end+4)
        }
        if(buffer.length<header.length)return
        const jpeg=buffer.slice(0,header.length)
        buffer=buffer.subarray(header.length);header=null;onFrame(new Blob([jpeg]))
      }
    }}
  }
  const originalSet=Uint8Array.prototype.set
  const run=factory=>{
    let bytes=0,frames=0
    Uint8Array.prototype.set=function(input,...args){bytes+=input.length;return originalSet.call(this,input,...args)}
    const start=performance.now()
    try {const parser=factory(()=>frames++);for(const chunk of chunks)parser.push(chunk)}
    finally {Uint8Array.prototype.set=originalSet}
    return {ms:performance.now()-start,jsSetBytes:bytes,frames}
  }
  const old=run(baseline),current=run(follow.createCameraMultipartParser)
  assert.equal(old.frames,20);assert.equal(current.frames,20)
  assert.ok(old.jsSetBytes>40*1024*1024);assert.equal(current.jsSetBytes,0)
  console.log('PARSER_COPY_BENCHMARK',JSON.stringify({jpegBytes:payload.length,chunkBytes:1024,frames:20,baseline:old,current,
    note:'JS set copy bytes only; Blob storage copies and browser costs not included; wall time informational'}))
  const decoded=[];const parser=follow.createCameraMultipartParser(p=>decoded.push(p))
  for(const chunk of chunks)parser.push(chunk)
  assert.deepEqual(new Uint8Array(await decoded.at(-1).blob.arrayBuffer()),payload)
})

function resumeFixture() {
  const calls=[],live={active:true,configured:true,token:'owned-token',session:'camera-a',seq:6}
  const saved={active:true,configured:true,backendSession:'backend-a',seq:5,server:{...live,seq:3}}
  const client=follow.createHandFollowClient({now:()=>1000,fetcher:async(path,options)=>{
    calls.push({action:path.split('/').at(-1),body:JSON.parse(options.body),signal:options.signal})
    return {ok:true,json:async()=>({ok:true,state:{...live,seq:calls.at(-1).body.seq}})}
  }})
  return {client,calls,live,saved}
}
test('resumeOwned requires saved tab credentials, matching backend/token/session and valid monotonic sequences', () => {
  const h=resumeFixture()
  for(const [saved,live,backend] of [
    [null,h.live,'backend-a'],[h.saved,h.live,'backend-b'],
    [{...h.saved,server:{...h.saved.server,token:'foreign'}},h.live,'backend-a'],
    [{...h.saved,server:{...h.saved.server,session:'foreign'}},h.live,'backend-a'],
    [{...h.saved,seq:NaN},h.live,'backend-a'],[{...h.saved,seq:-1},h.live,'backend-a'],
    [{...h.saved,seq:1},h.live,'backend-a'],[h.saved,{...h.live,seq:2},'backend-a'],
    [h.saved,{...h.live,active:false},'backend-a'],[h.saved,{...h.live,configured:false},'backend-a'],
    [h.saved,{...h.live,seq:Number.MAX_SAFE_INTEGER},'backend-a'],
  ]) assert.equal(h.client.resumeOwned(saved,live,backend),false)
  assert.equal(h.calls.length,0);assert.equal(h.client.getState().active,false)
  h.client.detach()
})
test('resumeOwned never sends configure/start, exposes sequence and next sample exceeds live and saved', async () => {
  for(const liveSeq of [4,6]) {
    const h=resumeFixture();h.live.seq=liveSeq
    assert.equal(h.client.resumeOwned(h.saved,h.live,'backend-a'),true)
    assert.equal(h.calls.length,0);assert.equal(h.client.getState().seq,Math.max(5,liveSeq))
    assert.equal(h.client.resumeOwned(h.saved,h.live,'backend-a'),false)
    await h.client.sample(null)
    assert.deepEqual(h.calls.map(c=>c.action),['sample'])
    assert.equal(h.calls[0].body.seq,Math.max(5,liveSeq)+1)
    assert.equal(h.calls[0].body.token,'owned-token')
    h.client.detach();h.client.dispose();assert.equal(h.calls.length,1)
  }
})
test('resumeOwned respects guard and detach aborts a pending request permanently without stop', async () => {
  const h=resumeFixture()
  const guarded=follow.createHandFollowClient({guard:()=> 'not simulation'})
  assert.equal(guarded.resumeOwned(h.saved,h.live,'backend-a'),false);guarded.detach()
  let request,finish
  const client=follow.createHandFollowClient({fetcher:async(path,options)=>{
    assert.ok(path.endsWith('/sample'));request=options;return new Promise(resolve=>{finish=resolve})
  }})
  assert.equal(client.resumeOwned(h.saved,h.live,'backend-a'),true)
  const pending=client.sample(null)
  client.detach();assert.equal(request.signal.aborted,true)
  finish({ok:true,json:async()=>({ok:true,state:h.live})});await pending
  assert.equal(client.resumeOwned(h.saved,h.live,'backend-a'),false)
  assert.equal(client.getState().active,false);client.dispose();h.client.detach()
})

test('resume rejects busy clients and detach never retires a late configure result', async () => {
  const h=resumeFixture(),calls=[];let finish
  const client=follow.createHandFollowClient({fetcher:async(path,options)=>{
    calls.push(path);return new Promise(resolve=>{finish=resolve})
  }})
  const configuring=client.configure({session:'camera-a'})
  assert.equal(client.getState().busy,true)
  assert.equal(client.resumeOwned(h.saved,h.live,'backend-a'),false)
  client.detach()
  finish({ok:true,json:async()=>({ok:true,state:h.live})});await configuring
  client.stop();client.dispose()
  assert.deepEqual(calls,['/api/sim/hand-follow/configure'])
  assert.equal(client.getState().active,false);h.client.detach()
})

test('worker JPEG decode or inference errors acknowledge failure and release resources', async () => {
  for(const decodeFails of [true,false]) {
    let closed=0
    const messages=[]
    const context={self:{postMessage:m=>messages.push(m)},importScripts(){},performance:{now:()=>0},
      createImageBitmap:async()=>{
        if(decodeFails)throw new Error('decode failed')
        return {close(){closed++}}
      },Vision:{FilesetResolver:{forVisionTasks:async()=>({})},HandLandmarker:{createFromOptions:async()=>({
        detectForVideo:()=>{throw new Error('inference failed')},
      })}}}
    vm.runInNewContext(read('../public/vision/hand-worker.js'),context)
    await context.self.onmessage({data:{type:'init'}})
    await context.self.onmessage({data:{type:'frame',blob:new Blob(['x']),frameSeq:1,timestamp:1,width:640,height:480}})
    assert.equal(messages.at(-1).type,'error')
    assert.match(messages.at(-1).message,decodeFails?/decode failed/:/inference failed/)
    assert.equal(closed,decodeFails?0:1)
  }
})

test('late JPEG worker result after session change cannot publish a palm into the new camera', async t => {
  const h=cameraFixture(t);await h.frame(1);h.workers[0].ready('jpeg');await flush()
  const old=h.workers[0]
  h.props.session='camera-b';await flush()
  old.complete();await flush()
  assert.equal(old.terminated,true)
  assert.equal(follow.getHandFollowInput(),null)
  assert.equal(h.camera.palm.value,null)
})
