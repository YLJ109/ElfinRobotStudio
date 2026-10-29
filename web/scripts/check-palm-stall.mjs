// Offline: executes the real Vue script and classic worker with controlled clocks.
// node --test web/scripts/check-palm-stall.mjs
// node web/scripts/check-palm-stall.mjs --profile   (same fixtures before/after)
// No browser, camera, model, server or physical robot is opened.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { performance as timer } from 'node:perf_hooks'
import { computed, ref, reactive, watch, effectScope, nextTick } from 'vue'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as geometry from '../src/handGeometry.js'
import * as follow from '../src/handFollowInput.js'
import { createHandDisplayHold } from '../src/handDisplayHold.js'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const source = read('../src/components/CameraFrame.vue')
const descriptor = parse(source).descriptor
const cameraScript = compileScript(descriptor, {id:'palm-stall'}).content
  .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
const workerScript = read('../public/vision/hand-worker.js')
const flush = async () => { for (let i=0;i<8;i++) await Promise.resolve(); await nextTick() }
const near = (a,b) => assert.ok(Math.abs(a-b)<1e-9, `${a} != ${b}`)
function landmarks() {
  const points=Array.from({length:21}, (_,i)=>({x:.5,y:.1,z:-i/100}))
  for (const [i,x,y] of [[0,.5,.8],[5,.3,.4],[9,.45,.3],[13,.6,.35],[17,.7,.45]])
    points[i]={...points[i],x,y}
  return points
}

function cameraFixture() {
  let clock=1000, nextTimer=0, unmount=()=>{}, camera, disposed=false
  const timers=new Map(), workers=[], metrics={publications:0,overlayEvaluations:0,mainDecodes:0}
  const props=reactive({session:'camera-a',stream:'color',hands:true,mapping:true,actualFps:60})
  const epochNow=()=>1000000+clock-1000
  class Worker {
    constructor(){ this.messages=[];this.terminated=false;workers.push(this) }
    postMessage(m){ this.messages.push(m) }
    terminate(){ this.terminated=true }
    ready(){ this.onmessage({data:{type:'ready',delegate:'CPU',frameInput:'jpeg'}}) }
    complete(points=landmarks(), patch={}) {
      const frame=this.messages.filter(m=>m.type==='frame').at(-1)
      this.onmessage({data:{type:'result',frameSeq:frame.frameSeq,timestamp:frame.timestamp,
        landmarks:points,inferenceMs:95,decodeMs:7,inputWidth:640,inputHeight:480,...patch}})
    }
  }
  const deps={ref,watch,computed,...geometry,...follow,createHandDisplayHold,
    handLandmarksInView:(...args)=>{metrics.overlayEvaluations++;return geometry.handLandmarksInView(...args)},
    publishHandFollowInput:(value,owner)=>{metrics.publications++;follow.publishHandFollowInput(value,owner)},
    onBeforeUnmount:fn=>{unmount=fn}, Worker,
    document:{hidden:false,addEventListener(){},removeEventListener(){}},
    performance:{now:()=>clock},Date:{now:epochNow},
    setTimeout:(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,at:clock+ms});return id},
    clearTimeout:id=>timers.delete(id),
    URL:{createObjectURL:()=>`blob:${clock}`,revokeObjectURL(){}},
    fetch:()=>new Promise(()=>{}),
    createImageBitmap:async()=>{metrics.mainDecodes++;return {width:640,height:480,close(){}}},
  }
  const scope=effectScope()
  const component=new Function(...Object.keys(deps),cameraScript)(...Object.values(deps))
  camera=scope.run(()=>component.setup(props,{expose(){},emit(){}}))
  camera.image.value={naturalWidth:640,naturalHeight:480,removeAttribute(){}}
  camera.imageSize.value={width:640,height:480};camera.bounds.value={width:320,height:240}
  // Evaluate only when the real computed overlay invalidates, like a rendered view.
  scope.run(()=>watch(()=>camera.handOverlay.value,()=>{}, {flush:'sync',immediate:true}))
  return {camera,props,workers,timers,metrics,
    setTime:value=>{clock=value},
    frame(seq,age=0) {
      camera.acceptFrame({seq,stamp:(epochNow()-age)/1000,blob:new Blob([String(seq)])})
      camera.imageLoaded()
    },
    async fire(id,time) {
      clock=time;const entry=timers.get(id);assert.ok(entry,'expected a live timer')
      timers.delete(id);entry.fn();await flush()
    },
    dispose(){if(!disposed){disposed=true;unmount();scope.stop()}},
  }
}

async function workerFixture({decodeMs=7,inferenceMs=95,points=landmarks(),deferred=false}={}) {
  let clock=1000000, resolveDecode
  const messages=[],metrics={decodes:0,inferences:0,closed:0,initializations:0}
  const bitmap=()=>({width:640,height:480,close(){metrics.closed++}})
  const context={self:{postMessage:m=>messages.push(m)},importScripts(){},
    performance:{now:()=>clock-1000000},Date:{now:()=>clock},
    createImageBitmap:async()=>{
      metrics.decodes++
      if (deferred) return new Promise(resolve=>{resolveDecode=()=>resolve(bitmap())})
      clock+=decodeMs;return bitmap()
    },
    Vision:{FilesetResolver:{forVisionTasks:async()=>({})},HandLandmarker:{createFromOptions:async()=>{
      metrics.initializations++
      return {detectForVideo:()=>{
        metrics.inferences++;clock+=inferenceMs
        const measured=typeof points==='function' ? points(metrics.inferences) : points
        return {landmarks:measured ? [measured] : []}
      }}
    }}},
  }
  vm.runInNewContext(workerScript,context)
  await context.self.onmessage({data:{type:'init'}})
  const frame=(seq,remaining=300)=>({type:'frame',frameSeq:seq,timestamp:seq*100,
    blob:new Blob(['encoded fixture']),width:640,height:480,expiresAt:clock+remaining})
  return {messages,metrics,frame,send:data=>context.self.onmessage({data}),
    advance:ms=>{clock+=ms},finishDecode:()=>resolveDecode()}
}

async function profileCamera() {
  const h=cameraFixture()
  try {
    h.frame(1);await flush();h.workers[0].ready();await flush()
    for (let seq=2;seq<=180;seq++) {
      h.setTime(1000+(seq-1)*1000/60);h.frame(seq);await flush()
      if (seq%3===0) {h.workers[0].complete(seq>=45&&seq<=105 ? null : landmarks());await flush()}
      if (seq%6===0) {h.camera.tick(1);await flush()}
    }
    return {...h.metrics,submitted:h.workers[0].messages.filter(m=>m.type==='frame').length,
      workers:h.workers.length,terminated:h.workers.filter(w=>w.terminated).length}
  } finally {h.dispose()}
}
async function profileExpired() {
  const h=await workerFixture()
  for(let seq=1;seq<=60;seq++) await h.send(h.frame(seq,-50))
  return {...h.metrics,results:h.messages.filter(m=>m.type==='result').length,
    skipped:h.messages.filter(m=>m.type==='skipped').length}
}
async function profileDecodeExpiry() {
  const h=await workerFixture({decodeMs:70})
  for(let seq=1;seq<=60;seq++) await h.send(h.frame(seq,50))
  return {...h.metrics,results:h.messages.filter(m=>m.type==='result').length,
    skipped:h.messages.filter(m=>m.type==='skipped').length}
}

if (process.argv.includes('--profile')) {
  // Wall times below measure this Node/Vue harness only. The detector is a stub;
  // its 7/95ms values advance a clock and are NOT measured MediaPipe performance.
  const times=[];let camera
  for(let i=0;i<13;i++) {
    const start=timer.now();camera=await profileCamera()
    if(i>=3) times.push(timer.now()-start)
  }
  times.sort((a,b)=>a-b)
  console.log(JSON.stringify({kind:'offline work counts; no real model/GPU/PC hitch measurement',
    fixture:'180 captures at 60Hz / 60 completions / 21 empty / 30 housekeeping ticks',
    node:process.version,camera,harnessMs:{samples:times.length,median:times[5],p95:times[9]},
    expiredBeforeDecode:await profileExpired(),expiredDuringDecode:await profileDecodeExpiry()},null,2))
} else {
  test('expired captures skip decode and inference, acknowledge each ID and never reload detector',async()=>{
    const h=await profileExpired()
    assert.equal(h.decodes,0);assert.equal(h.inferences,0);assert.equal(h.skipped,60)
    assert.equal(h.results,0);assert.equal(h.initializations,1)
  })
  test('a JPEG expiring during decode is closed without spending a detector call',async()=>{
    const h=await profileDecodeExpiry()
    assert.equal(h.decodes,60);assert.equal(h.inferences,0);assert.equal(h.closed,60)
    assert.equal(h.skipped,60);assert.equal(h.results,0)
  })
  test('fresh JPEG retains all 21 measured xyz points and actual stage timings',async()=>{
    const h=await workerFixture();await h.send(h.frame(1))
    const result=h.messages.at(-1)
    assert.equal(result.type,'result');assert.equal(result.decodeMs,7);assert.equal(result.inferenceMs,95)
    assert.equal(result.landmarks.length,21);near(result.landmarks[8].z,-.08)
    assert.equal(h.metrics.closed,1)
  })
  test('expiry exactly at 300ms is skipped; duplicate skips never process a frame twice',async()=>{
    const h=await workerFixture(),frame=h.frame(1,0)
    await h.send(frame);await h.send(frame)
    assert.equal(h.metrics.inferences,0);assert.equal(h.metrics.decodes,0)
    assert.equal(h.messages.filter(m=>m.type==='skipped').length,1)
  })
  test('expired transferred bitmaps are closed even when decode never begins',async()=>{
    let closed=0
    const h=await workerFixture()
    await h.send({...h.frame(1,0),bitmap:{close(){closed++}}})
    assert.equal(closed,1);assert.equal(h.metrics.inferences,0);assert.equal(h.metrics.decodes,0)
    assert.equal(h.messages.at(-1).stage,'before-decode')
  })
  test('repeated empty results and reacquisition reuse one detector without invented points',async()=>{
    const h=await workerFixture({points:n=>n>1&&n<10 ? null : landmarks()})
    for(let seq=1;seq<=10;seq++) await h.send(h.frame(seq))
    const results=h.messages.filter(m=>m.type==='result')
    assert.equal(results.length,10);assert.equal(results.filter(m=>m.landmarks===null).length,8)
    assert.equal(results.at(-1).landmarks.length,21)
    assert.equal(h.metrics.initializations,1);assert.equal(h.metrics.closed,10)
  })
  test('skip acknowledgements release the pending slot, choose latest frame and do not inflate FPS',async t=>{
    const h=cameraFixture();t.after(()=>h.dispose())
    h.frame(1);await flush();h.workers[0].ready();await flush()
    h.setTime(1010);h.frame(2);h.frame(3);await flush()
    h.workers[0].complete(null,{type:'skipped'});await flush()
    assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),[1,3])
    assert.equal(h.camera.resultTimes.length,0);assert.equal(h.camera.detectionFps.value,0)
    assert.equal(h.workers.length,1);assert.equal(h.workers[0].terminated,false)
  })
  test('a mismatched skip cannot release another frame or count as a detection',async t=>{
    const h=cameraFixture();t.after(()=>h.dispose())
    h.frame(1);await flush();h.workers[0].ready();await flush()
    h.frame(3);h.workers[0].complete(null,{type:'skipped',frameSeq:999});await flush()
    assert.equal(h.camera.handPending,true);assert.equal(h.camera.resultTimes.length,0)
    h.workers[0].complete(null,{type:'skipped'});await flush()
    assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),[1,3])
  })
  test('camera forwards the authentic capture deadline for both worker input protocols',async t=>{
    const h=cameraFixture();t.after(()=>h.dispose())
    h.frame(1,250);await flush();h.workers[0].ready();await flush()
    assert.equal(h.workers[0].messages.at(-1).expiresAt,1000050)
    h.workers[0].complete();await flush()
    h.camera.workerFrameInput='bitmap';h.setTime(1067);h.frame(3,200);await flush()
    assert.equal(h.workers[0].messages.at(-1).expiresAt,1000167)
  })
  test('preview dedup still publishes first dimensions and later resolution changes',async t=>{
    const h=cameraFixture();t.after(()=>h.dispose())
    h.camera.imageSize.value={width:0,height:0};h.frame(1);await flush()
    assert.equal(follow.getHandFollowInput().width,640)
    const previous=h.metrics.publications
    h.camera.imageLoaded();assert.equal(h.metrics.publications,previous)
    h.camera.image.value.naturalWidth=1280;h.camera.imageLoaded()
    assert.equal(follow.getHandFollowInput().width,1280)
    assert.equal(h.metrics.publications,previous+1)
  })
  test('capture/preview completion does not repeat the same publication or rebuild an empty skeleton',async()=>{
    const h=await profileCamera()
    assert.ok(h.publications<=280,JSON.stringify(h))
    assert.ok(h.overlayEvaluations<=50,JSON.stringify(h))
    assert.equal(h.workers,1);assert.equal(h.terminated,0);assert.equal(h.mainDecodes,0)
  })
  test('holding distinguishes old overlay from motion freshness and never renews capture credentials',async t=>{
    const h=cameraFixture();t.after(()=>h.dispose())
    h.frame(1);await flush();h.workers[0].ready();await flush();h.workers[0].complete();await flush()
    const original=follow.getHandFollowInput()
    h.setTime(1134);h.frame(3);h.workers[0].complete(null);await flush()
    const held=follow.getHandFollowInput()
    assert.equal(held.timestamp,original.timestamp);assert.equal(held.frameSeq,original.frameSeq)
    assert.match(held.detectorStatus,/光标/)
    assert.equal(follow.handFollowInputIssue(held,1299),'')
    assert.ok(follow.handFollowInputIssue(held,1300))
    await h.fire(h.camera.cursorTimer,1450)
    assert.equal(follow.getHandFollowInput().point,null)
    assert.equal(h.camera.handOverlay.value.points.length,21,'display survives motion expiry only')
    assert.ok(follow.handFollowInputIssue(follow.getHandFollowInput(),1450))
    const expiredInput=follow.getHandFollowInput()
    const frameCount=h.workers[0].messages.filter(m=>m.type==='frame').length
    h.setTime(1999);assert.equal(h.camera.displayHold.read(1999).landmarks.length,21)
    await h.fire(h.camera.displayExpiryTimer,2000)
    assert.equal(h.camera.handOverlay.value.points.length,0)
    assert.equal(follow.getHandFollowInput(),expiredInput,'display expiry does not publish or renew motion input')
    assert.equal(h.workers[0].messages.filter(m=>m.type==='frame').length,frameCount)
  })
  test('inference which overruns the deadline cannot publish stale motion or fabricate capture age',async t=>{
    const h=cameraFixture();t.after(()=>h.dispose())
    h.frame(1);await flush();h.workers[0].ready();await flush()
    h.setTime(1400);h.workers[0].complete();await flush()
    assert.equal(follow.getHandFollowInput().point,null)
    assert.ok(follow.handFollowInputIssue(follow.getHandFollowInput(),1400))
    assert.equal(h.camera.handLandmarks.value.length,0)
    assert.equal(h.workers[0].terminated,false)
    h.setTime(1410);h.frame(3);h.workers[0].complete();await flush()
    assert.equal(follow.getHandFollowInput().timestamp,1410)
    assert.equal(follow.handFollowInputIssue(follow.getHandFollowInput(),1410),'')
  })
  test('tracker preserves z and hand_open through smoothing; mirror stays exactly once',async t=>{
    const h=cameraFixture();t.after(()=>h.dispose())
    h.frame(1);await flush();h.workers[0].ready();await flush();h.workers[0].complete();await flush()
    h.setTime(1134);h.frame(3);h.workers[0].complete();await flush()
    const input=follow.getHandFollowInput()
    near(input.point.x,.49);near(input.point.dx,.05)
    assert.ok(Number.isFinite(input.point.hand_open))
    assert.equal(h.camera.handLandmarks.value.length,21)
    near(h.camera.handLandmarks.value[8].z,-.08)
  })
  test('late expiry from a retired camera cannot affect its replacement',async t=>{
    const h=cameraFixture();t.after(()=>h.dispose())
    h.frame(1);await flush();h.workers[0].ready();await flush()
    const old=h.workers[0];h.props.session='camera-b';await flush()
    old.complete(null,{type:'skipped'});await flush()
    assert.equal(follow.getHandFollowInput(),null);assert.equal(h.camera.palm.value,null)
  })
  test('bounded expiry waiters release busy ownership and a fresh frame can reacquire',async()=>{
    const h=await workerFixture({deferred:true}),first=h.send(h.frame(1,50))
    await h.send(h.frame(2));h.advance(50);h.finishDecode();await first
    assert.equal(h.messages.at(-1).type,'skipped');assert.equal(h.metrics.closed,1)
    const next=h.send(h.frame(3));h.finishDecode();await next
    assert.equal(h.messages.at(-1).type,'result');assert.equal(h.metrics.initializations,1)
  })
  test('camera template compiles with the genuine 21-point overlay',()=>{
    const compiled=compileTemplate({source:descriptor.template.content,filename:'CameraFrame.vue',id:'palm-stall'})
    assert.deepEqual(compiled.errors,[])
    assert.match(source,/handOverlay\.points/);assert.match(source,/handOverlay\.bones/)
  })
  test('uncapped delivery detects every fresh capture when CPU is available',async t=>{
    assert.match(source,/stream\.mjpg\?[^`]*fps=0/)
    const h=cameraFixture();t.after(()=>h.dispose())
    h.frame(1);await flush();h.workers[0].ready();await flush()
    for(let seq=1;seq<=120;seq++) {
      h.setTime(1000+(seq-1)*1000/120)
      if(seq>1)h.frame(seq)
      h.workers[0].complete();await flush()
    }
    assert.deepEqual(h.workers[0].messages.filter(m=>m.type==='frame').map(m=>m.frameSeq),
      Array.from({length:120},(_,i)=>i+1))
    assert.equal(h.workers.length,1)
    assert.equal(follow.getHandFollowInput().frameSeq,120)
  })
  for(const fps of [15,30,60]) test(`${fps}fps camera sends every capture to an available CPU worker without changing preview`,async t=>{
    const h=cameraFixture();t.after(()=>h.dispose());h.props.actualFps=fps
    h.frame(1);await flush();h.workers[0].ready();await flush()
    for(let seq=1;seq<=2*fps;seq++) {
      h.setTime(1000+(seq-1)*1000/fps)
      if(seq>1)h.frame(seq)
      h.workers[0].complete()
      await flush()
    }
    assert.equal(h.workers[0].messages.filter(m=>m.type==='frame').length,2*fps)
    assert.equal(h.camera.detectorDelegate.value,'CPU')
    assert.equal(h.workers.length,1)
    assert.equal(h.camera.fresh.value,true)
  })
}
