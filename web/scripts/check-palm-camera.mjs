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
