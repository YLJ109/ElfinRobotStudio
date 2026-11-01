// CPU / deterministic scheduler evidence only. No GPU context, network,
// physical camera, service startup, backend import, or build.
import test from 'node:test'
import assert from 'node:assert/strict'
import { performance } from 'node:perf_hooks'
import { readFileSync } from 'node:fs'
import * as THREE from 'three'
import { reactive, ref } from 'vue'
import * as Vue from 'vue'
import { parse,compileScript,compileTemplate,compileStyle } from '@vue/compiler-sfc'
import { createVirtualCamera,createVirtualPixelCopy } from '../src/virtualCamera.js'
import { createVirtualCapture,localVirtualPreview,virtualCaptureKey,virtualRecognitionFps,applyVisionSnapshot,VIRTUAL_PREVIEW_MAX_AGE_MS } from '../src/visionWorkspace.js'
import { virtualPreview,createCameraSource,VIRTUAL_CAMERA } from '../src/cameraSource.js'
import * as handFollow from '../src/handFollowInput.js'

const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const flush=()=>new Promise(resolve=>setImmediate(resolve))
function cameraFixture(t,{readback,encoder}={}){
  const scene=new THREE.Scene(),flange=new THREE.Group(),overlay=new THREE.Group();scene.add(flange,overlay)
  let currentTarget={name:'previous-target'},face=3,mip=2,clock=0,allocations=0,disposedTargets=0,image=null,renderFailure=false
  const stats={renders:0,reads:0,encodes:0,puts:0,images:0,canvases:0}
  const canvas={width:0,height:0,getContext:()=>({
    createImageData(w,h){stats.images++;return {data:new Uint8ClampedArray(w*h*4)}},
    putImageData(value){image=value;stats.puts++;clock+=1},
  })}
  const renderer={
    getRenderTarget:()=>currentTarget,getActiveCubeFace:()=>face,getActiveMipmapLevel:()=>mip,
    setRenderTarget(value,f=0,m=0){currentTarget=value;face=f;mip=m;if(value?.isWebGLRenderTarget && !value.userData?.counted){value.userData={counted:true};allocations++;value.addEventListener('dispose',()=>disposedTargets++)}},
    clear(){},render(){stats.renders++;assert.equal(overlay.visible,false);clock+=2;if(renderFailure)throw new Error('render failed')},
    async readRenderTargetPixelsAsync(target,x,y,w,h,pixels){
      stats.reads++;assert.equal(currentTarget.name,'previous-target');assert.equal(face,3);assert.equal(mip,2)
      assert.equal(overlay.visible,true)
      pixels.fill(20);pixels.fill(200,0,w*4)
      if(readback)await readback(target,w,h,pixels)
      clock+=4
    },
    readRenderTargetPixels(){throw new Error('synchronous GPU readback forbidden')},
  }
  const camera=createVirtualCamera({renderer,scene,flange,hidden:()=>[overlay,overlay],now:()=>clock,
    createCanvas:()=>{stats.canvases++;return canvas},encodeCanvas:async value=>{stats.encodes++;clock+=6;return encoder?encoder(value):'b2ZmbGluZQ=='}})
  t.after(()=>camera.dispose());camera.configure({enabled:true,width:320})
  return {camera,renderer,stats,canvas,overlay,get image(){return image},get allocations(){return allocations},get disposedTargets(){return disposedTargets},failRender:()=>{renderFailure=true}}
}

test('local capture skips JPEG, reuses allocations, flips pixels and restores target face/mipmap and duplicate hidden objects',async t=>{
  const f=cameraFixture(t)
  for(let n=0;n<4;n++){
    const frame=await f.camera.capture({encode:false})
    assert.equal(frame.canvas,f.canvas);assert.equal(typeof frame.encode,'function')
    assert.equal(frame.image_base64,undefined);assert.equal(f.image.data[0],20);assert.equal(f.image.data.at(-1),200)
  }
  assert.deepEqual(f.stats,{renders:4,reads:4,encodes:0,puts:4,images:1,canvases:1})
  assert.equal(f.allocations,1);assert.equal(f.overlay.visible,true)
  const metrics=f.camera.metrics();assert.equal(metrics.completed,4);assert.equal(metrics.encoded,0)
  assert.deepEqual(metrics.last,{render_submit_ms:2,readback_wait_ms:4,copy_ms:1,total_ms:7,readback:'async-pbo'})
})

test('readback stays single in flight; resizing never disposes an active target or publishes mismatched dimensions',async t=>{
  const pending=deferred();let activeTarget
  const f=cameraFixture(t,{readback:target=>{activeTarget=target;return pending.promise}})
  const first=f.camera.capture({encode:false});assert.equal(await f.camera.capture({encode:false}),null)
  f.camera.configure({enabled:true,width:640});assert.equal(f.disposedTargets,0)
  assert.equal(activeTarget.width,320)
  pending.resolve();assert.equal(await first,null);assert.equal(f.disposedTargets,1)
  const second=await f.camera.capture({encode:false})
  assert.deepEqual([second.width,second.height],[640,480]);assert.equal(f.allocations,2)
  assert.equal(f.camera.metrics().completed,1)
})

test('stop/restart and disposal invalidate late readback; render failure restores state',async t=>{
  for(const dispose of [false,true]){
    const task=deferred(),f=cameraFixture(t,{readback:()=>task.promise}),pending=f.camera.capture({encode:false})
    if(dispose)f.camera.dispose()
    else{f.camera.configure({enabled:false});f.camera.configure({enabled:true,width:320})}
    task.resolve();assert.equal(await pending,null);assert.equal(f.camera.metrics().completed,0)
    if(dispose){assert.equal(f.disposedTargets,1);f.camera.dispose();assert.equal(f.disposedTargets,1)}
  }
  const f=cameraFixture(t);f.failRender()
  await assert.rejects(f.camera.capture(),/render failed/)
  assert.equal(f.renderer.getRenderTarget().name,'previous-target');assert.equal(f.overlay.visible,true)
  assert.equal(f.camera.metrics().pending,false)
})

test('deferred encoding is single in flight while new local frames continue; stopping rejects late encoded data',async t=>{
  const encoded=deferred(),f=cameraFixture(t,{encoder:()=>encoded.promise})
  const first=await f.camera.capture({encode:false}),pending=first.encode()
  const second=await f.camera.capture({encode:false})
  assert.equal(await second.encode(),null);assert.equal(await first.encode(),null)
  assert.equal(f.camera.metrics().completed,2);assert.equal(f.stats.encodes,1)
  f.camera.configure({enabled:false});encoded.resolve('late')
  assert.equal(await pending,null);assert.equal(f.camera.metrics().encoded,0)
})

test('browser JPEG uses asynchronous blob/read without calling synchronous toDataURL',async t=>{
  const original=globalThis.FileReader
  let completeBlob,completeRead,reader,quality
  globalThis.FileReader=class{
    constructor(){reader=this}
    readAsDataURL(blob){assert.deepEqual(blob,{snapshot:true});completeRead=()=>{this.result='data:image/jpeg;base64,YmxvYg==';this.onload()}}
  }
  t.after(()=>{globalThis.FileReader=original})
  const flange=new THREE.Group(),canvas={width:0,height:0,getContext:()=>({createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(){}}),
    toBlob(done,mime,q){quality=q;assert.equal(mime,'image/jpeg');completeBlob=()=>done({snapshot:true})},
    toDataURL(){throw new Error('sync JPEG forbidden')}}
  let target=null
  const camera=createVirtualCamera({renderer:{getRenderTarget:()=>target,setRenderTarget:t=>{target=t},clear(){},render(){},readRenderTargetPixelsAsync:async()=>{}},scene:new THREE.Scene(),flange,createCanvas:()=>canvas})
  t.after(()=>camera.dispose());camera.configure({enabled:true,width:320})
  const frame=await camera.capture({encode:false}),pending=frame.encode()
  assert.equal(camera.metrics().encoding,true);assert.equal(quality,.82)
  completeBlob();await flush();assert.ok(reader);completeRead()
  assert.equal((await pending).image_base64,'YmxvYg==');assert.equal(camera.metrics().encoding,false)
})

function schedulerFixture(t,{fps=15,cost=5,holdCapture=false,holdUpload=false,uploadError=null}={}){
  const state=reactive({instance_id:'offline-engine',virtual_session:'session-a',config:{input:'virtual',enabled:true,detection_enabled:true,virtual:{fps,width:320}},result:{source:'virtual',objects:[{id:'measured'}]},stale:false})
  const preview=localVirtualPreview(state);preview.visible=true
  const allowed=ref(true),doc=new EventTarget();doc.hidden=false
  const timers=new Map(),expiryTimers=new Map(),captures=[],uploads=[],configured=[],captureTasks=[],encodeTasks=[]
  let clock=0,id=0,stopped=false,encoding=false,encodes=0,uploadActive=0,uploadPeak=0,captureActive=0,capturePeak=0
  const scene={configureVirtualCamera:c=>configured.push(c),captureVirtualFrame:async options=>{
    assert.equal(options.encode,false);captures.push(clock);captureActive++;capturePeak=Math.max(capturePeak,captureActive)
    const task=deferred();captureTasks.push(task)
    if(typeof holdCapture==='function'?holdCapture(captures.length):holdCapture)await task.promise
    clock+=cost;captureActive--
    const metadata={canvas:{offline:true},width:320,height:240,view_key:'pose-'+captures.length,captured_at:100+clock/1000}
    return {...metadata,encode:async()=>{
      assert.equal(encoding,false);encoding=true;encodes++
      const encodeTask=deferred();encodeTasks.push(encodeTask)
      if(holdUpload)await encodeTask.promise
      encoding=false;return {...metadata,image_base64:'encoded-'+encodes}
    }}
  }}
  const stop=createVirtualCapture({state,getScene:()=>scene,allowed:()=>allowed.value,doc,now:()=>clock,
    schedule:(fn,delay)=>{timers.set(++id,{fn,delay,at:clock+delay});return id},cancel:id=>timers.delete(id),
    scheduleExpiry:(fn,delay)=>{expiryTimers.set(++id,{fn,delay,at:clock+delay});return id},cancelExpiry:id=>expiryTimers.delete(id),
    request:async(path,payload,signal)=>{
      assert.equal(path,'virtual');uploadActive++;uploadPeak=Math.max(uploadPeak,uploadActive)
      const task=deferred();uploads.push({payload,signal,task})
      if(holdUpload)await task.promise
      if(uploadError)throw uploadError
      uploadActive--;return {ok:true,result:{objects:[{id:'must-not-replace-measured'}]}}
    }})
  t.after(()=>{if(!stopped)stop()})
  return {state,preview,allowed,doc,scene,timers,expiryTimers,captures,uploads,configured,captureTasks,encodeTasks,
    get encodes(){return encodes},get uploadPeak(){return uploadPeak},get capturePeak(){return capturePeak},
    async ready(){await flush()},
    async tick(){const [key,timer]=[...timers][0] || [];assert.ok(timer,'scheduled frame missing');timers.delete(key);clock=timer.at;await timer.fn();await flush()},
    advance(ms){clock+=ms;for(const [key,timer]of [...expiryTimers])if(timer.at<=clock){expiryTimers.delete(key);timer.fn()}},
    stop(){stopped=true;stop()},
  }
}

test('explicit config-priority 429 drops only this frame without success or overwriting configuration error',async t=>{
  const h=schedulerFixture(t,{uploadError:Object.assign(new Error('视觉配置任务繁忙'),{status:429,why:'视觉配置任务繁忙'})})
  h.state.error='已有配置错误';await h.ready()
  assert.equal(h.state.error,'已有配置错误');assert.equal(h.preview.uploaded,0);assert.equal(h.preview.uploadSkipped,1)
  assert.deepEqual(h.state.result.objects,[{id:'measured'}])
})
for(const [status,why]of [[429,'其它限流原因'],[500,'视觉配置任务繁忙'],[422,'图像解码失败']])test(`other upload rejection remains visible: ${status} ${why}`,async t=>{
  const h=schedulerFixture(t,{uploadError:Object.assign(new Error(why),{status,why})});await h.ready()
  assert.match(h.state.error,new RegExp(why));assert.equal(h.preview.uploaded,0)
})
for(const changed of ['config','source','off'])test(`late upload failure after ${changed} never overwrites current state`,async t=>{
  const h=schedulerFixture(t,{holdUpload:true});await h.ready();h.encodeTasks[0].resolve();await h.ready();assert.equal(h.uploads.length,1)
  if(changed==='config'){h.state.config_generation=2;h.state.config.colors=['blue']}
  if(changed==='source')h.state.virtual_session='new-source'
  if(changed==='off')h.state.config.detection_enabled=false
  h.state.error='当前状态';h.uploads[0].task.reject(new Error('旧上传失败'));await h.ready()
  assert.equal(h.state.error,'当前状态');assert.equal(h.preview.uploaded,0)
  if(changed==='off'){const count=h.uploads.length;await h.tick();assert.equal(h.uploads.length,count)}
})

test('local target is 30fps with elapsed-time compensation; backend fps=15 only throttles recognition uploads',async t=>{
  const h=schedulerFixture(t);await h.ready()
  for(let n=1;n<30;n++)await h.tick()
  assert.equal(h.preview.completed,30);assert.equal(h.captures.length,30)
  for(let n=1;n<30;n++)assert.ok(Math.abs(h.captures[n]-h.captures[n-1]-1000/30)<1e-9)
  assert.equal(h.uploads.length,15);assert.equal(h.encodes,15)
  assert.equal(h.state.config.virtual.fps,15);assert.equal(h.capturePeak,1);assert.equal(h.uploadPeak,1)
  assert.equal(h.configured.length,1,'unchanged settings are not reapplied at preview frame rate')
  assert.deepEqual(h.state.result.objects,[{id:'measured'}]);assert.equal(h.preview.frame.sequence,30)
  t.diagnostic('Deterministic 5ms capture fixture: 30 local completions, 15 encode/uploads over starts 0..966.67ms. This is scheduler evidence, not measured GPU FPS.')
})

test('slow upload/encoding skips frames without delaying or accumulating local captures',async t=>{
  const h=schedulerFixture(t,{holdUpload:true});await h.ready()
  for(let n=1;n<30;n++)await h.tick()
  assert.equal(h.preview.completed,30);assert.equal(h.encodes,1);assert.equal(h.uploads.length,0)
  assert.equal(h.preview.uploadSkipped,29);assert.equal(h.capturePeak,1)
  h.encodeTasks[0].resolve();await h.ready();assert.equal(h.uploads.length,1)
  for(let n=0;n<5;n++)await h.tick()
  assert.equal(h.uploads.length,1);assert.equal(h.uploadPeak,1)
  h.uploads[0].task.resolve();await h.ready();assert.equal(h.preview.uploaded,1)
  await h.tick();assert.equal(h.encodes,2)
})

test('explicit recognition frequency is preserved while preview stays at 30fps',async t=>{
  const h=schedulerFixture(t,{fps:5});await h.ready()
  for(let n=1;n<30;n++)await h.tick()
  assert.equal(h.preview.completed,30);assert.equal(h.encodes,5);assert.equal(h.uploads.length,5)
  assert.equal(h.state.config.virtual.fps,5)
})

test('empty or failed captures never count frames or leave a stale LIVE image',async t=>{
  const h=schedulerFixture(t);await h.ready();assert.equal(h.preview.completed,1)
  h.scene.captureVirtualFrame=async()=>null
  await h.tick();assert.equal(h.preview.frame,null);assert.equal(h.preview.completed,1)
  h.scene.captureVirtualFrame=async()=>{throw new Error('offline GPU failure')}
  await h.tick();assert.equal(h.preview.frame,null);assert.equal(h.preview.completed,1)
  assert.match(h.state.error,/offline GPU failure/)
})

test('pending GPU readback expires previous computed LIVE without unlocking, recounting, or scheduling a second capture',async t=>{
  const h=schedulerFixture(t,{holdCapture:n=>n===2});await h.ready()
  const image=Vue.computed(()=>virtualPreview(h.state)),live=Vue.computed(()=>!!image.value)
  assert.equal(live.value,true);assert.equal(h.preview.completed,1)
  const pending=h.tick();await h.ready()
  assert.equal(h.captures.length,2);assert.equal(h.timers.size,0)
  h.advance(VIRTUAL_PREVIEW_MAX_AGE_MS+250)
  assert.equal(image.value,null);assert.equal(live.value,false)
  assert.equal(h.preview.completed,1);assert.equal(h.captures.length,2)
  assert.equal(h.capturePeak,1);assert.equal(h.timers.size,0);assert.equal(h.configured.length,1)
  h.captureTasks[1].resolve();await pending
  assert.equal(live.value,false,'over-age readback must not restore LIVE')
  assert.equal(h.preview.completed,1);assert.equal(h.encodes,1)
  await h.tick();assert.equal(live.value,true);assert.equal(h.preview.completed,2);assert.equal(h.capturePeak,1)
})

test('preview age includes capture latency, and stale expiry callbacks cannot clear a newer frame or session',async t=>{
  const h=schedulerFixture(t);await h.ready()
  const oldExpiry=[...h.expiryTimers.values()][0]
  assert.equal(oldExpiry.delay,VIRTUAL_PREVIEW_MAX_AGE_MS-5)
  await h.tick();const second=h.preview.frame
  oldExpiry.fn();assert.equal(h.preview.frame,second)
  h.state.virtual_session='new-session';await h.ready();const fresh=h.preview.frame
  assert.ok(fresh);assert.equal(fresh.session,'new-session')
  oldExpiry.fn();assert.equal(h.preview.frame,fresh)
  h.stop();assert.equal(h.expiryTimers.size,0);assert.equal(h.timers.size,0)
  oldExpiry.fn();assert.equal(h.preview.frame,null)
})

test('over-budget capture starts next frame at completion, never a catch-up burst',async t=>{
  const h=schedulerFixture(t,{cost:45});await h.ready()
  for(let n=1;n<5;n++)await h.tick()
  assert.deepEqual(h.captures,[0,45,90,135,180]);assert.equal(h.capturePeak,1)
  assert.equal(h.preview.completed,5)
})

test('hidden/closed/real modes capture zero new frames and clear local preview synchronously',async t=>{
  const h=schedulerFixture(t);await h.ready();assert.ok(virtualPreview(h.state))
  for(const [disable,restore] of [
    [()=>{h.preview.visible=false},()=>{h.preview.visible=true}],
    [()=>{h.doc.hidden=true;h.doc.dispatchEvent(new Event('visibilitychange'))},()=>{h.doc.hidden=false;h.doc.dispatchEvent(new Event('visibilitychange'))}],
    [()=>{h.allowed.value=false},()=>{h.allowed.value=true}],
    [()=>{h.state.config.enabled=false},()=>{h.state.config.enabled=true}],
    [()=>{h.state.config.input='camera'},()=>{h.state.config.input='virtual'}],
  ]){
    disable();const count=h.captures.length
    assert.equal(h.preview.frame,null);assert.equal(virtualPreview(h.state),null)
    await h.ready();await h.tick();assert.equal(h.captures.length,count)
    restore();await h.ready();assert.ok(h.captures.length>count)
  }
})

test('session/config/source/stop invalidation discards pending captures including switch-away-and-back',async t=>{
  for(const change of [h=>{h.state.virtual_session='session-b'},h=>{h.state.config.virtual.width=640},
    h=>{h.state.config.input='camera';h.state.config.input='virtual'},h=>{h.stop()}]){
    const h=schedulerFixture(t,{holdCapture:true});assert.equal(h.captures.length,1)
    change(h);h.captureTasks[0].resolve();await h.ready()
    assert.equal(h.preview.completed,0);assert.equal(h.preview.frame,null);assert.equal(h.encodes,0)
    if(h.timers.size){const next=h.tick();await h.ready();assert.equal(h.captures.length,2);h.captureTasks[1].resolve();await next}
  }
})

test('late encoding and uploads cannot publish after source/session change or dispose; no listener/timer leaks',async t=>{
  const h=schedulerFixture(t,{holdUpload:true});await h.ready()
  h.state.virtual_session='session-b';h.encodeTasks[0].resolve();await h.ready()
  assert.equal(h.uploads.length,0);assert.equal(h.preview.uploaded,0)
  await h.tick();h.encodeTasks.at(-1).resolve();await h.ready();assert.equal(h.uploads.length,1)
  const pending=h.uploads[0];h.stop();assert.equal(pending.signal.aborted,true)
  pending.task.resolve();await h.ready();assert.equal(h.preview.uploaded,0);assert.equal(h.preview.frame,null)
  assert.equal(h.timers.size,0)
  const count=h.captures.length;h.doc.dispatchEvent(new Event('visibilitychange'));h.preview.visible=false
  await h.ready();assert.equal(h.captures.length,count)
})

test('polling recognized results never replace/clear local preview; preview never reads server images',async t=>{
  const h=schedulerFixture(t);await h.ready();const frame=virtualPreview(h.state),key=virtualCaptureKey(h.state)
  assert.ok(frame?.canvas)
  applyVisionSnapshot(h.state,{instance_id:'offline-engine',revision:2,config:{enabled:true,input:'virtual',virtual:{width:320,fps:15}},stale:true,result:{source:'virtual',image:{base64:'server-image'},objects:[{id:'recognized'}]}})
  assert.equal(virtualCaptureKey(h.state),key);assert.equal(virtualPreview(h.state),frame)
  h.preview.frame=null;assert.equal(virtualPreview(h.state),null)
  assert.deepEqual(h.state.result.objects,[{id:'recognized'}])
  assert.equal(virtualRecognitionFps({virtual:{fps:7}}),7);assert.equal(virtualRecognitionFps({}),30)
})

test('source change suspends and clears local preview before config reply; failed config safely resumes prior source',async t=>{
  const h=schedulerFixture(t);await h.ready();const pending=deferred()
  const w=reactive({snap:{mode:'sim'},cameras:{devices:[{id:'offline-device'}],active:null}})
  const controller=createCameraSource(w,{state:h.state,storage:{getItem(){},setItem(){}},request:()=>pending.promise})
  const selection=controller.select('offline-device')
  assert.equal(h.preview.suspended,true);assert.equal(virtualPreview(h.state),null)
  const count=h.captures.length;await h.tick();assert.equal(h.captures.length,count)
  pending.reject(new Error('offline config rejection'));assert.equal(await selection,false)
  await h.ready();assert.equal(h.preview.suspended,false);assert.ok(h.captures.length>count)
  assert.equal(controller.selected.value,VIRTUAL_CAMERA)
})

test('CameraWidget canvas preview compiles and distinguishes preview target from recognition sample rate',()=>{
  const source=readFileSync(new URL('../src/components/CameraWidget.vue',import.meta.url),'utf8')
  const {descriptor,errors}=parse(source);assert.deepEqual(errors,[])
  const script=compileScript(descriptor,{id:'virtual-performance'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'CameraWidget.vue',id:'virtual-performance',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'virtual-performance',scoped:style.scoped}).errors,[])
  assert.match(source,/<canvas v-else-if="virtualImage && virtual"/);assert.match(source,/drawImage\(frame.canvas/)
  assert.match(source,/仅本地预览，不上传/)
  assert.doesNotMatch(source,/visionWorkspace\.result\?\.image|:src="virtualImage"/)
})

test('actual CameraWidget setup draws only local canvas and clears visibility on close, real mode and teardown',async t=>{
  const state=reactive({instance_id:'widget-engine',virtual_session:'widget-session',config:{input:'virtual',enabled:true,virtual:{fps:15}},result:{image:{base64:'server-should-never-be-drawn'}}})
  const preview=localVirtualPreview(state),w=reactive({mode:'sim',view:'sim',cameras:{active:null}}),unmount=[]
  const scope=Vue.effectScope();t.after(()=>scope.stop())
  const storage={getItem:key=>key==='huayan.camera.open'?'1':null,setItem(){}}
  const doc={hidden:false,querySelector:()=>null,addEventListener(){},removeEventListener(){}}
  const dependencies={...Vue,localStorage:storage,document:doc,window:{removeEventListener(){}},
    useWorld:()=>w,storeToRefs:world=>({cameras:Vue.toRef(world,'cameras')}),CameraFrame:{},BrowserCameraPreview:{},
    visionWorkspace:state,localVirtualPreview,virtualRecognitionFps,virtualPreview,
    useCameraSource:()=>({busy:ref(false),selected:ref(VIRTUAL_CAMERA),message:ref(''),
      browser:ref(false),browserPreview:{state:reactive({status:'idle'}),stop(){}},handDetection:ref(false),setHandDetection(){},detectionError:ref(''),
      virtual:Vue.computed(()=>w.mode==='sim' && state.config.input==='virtual'),options:ref([]),running:Vue.computed(()=>state.config.enabled),refresh(){},select(){},toggle(){}}),
    onHandFollowVisionRequest:handFollow.onHandFollowVisionRequest,getHandFollowInput:handFollow.getHandFollowInput,
    subscribeHandFollowInput:handFollow.subscribeHandFollowInput,onHandFollowDetectionDemand:handFollow.onHandFollowDetectionDemand,
    onMounted(){},onBeforeUnmount:fn=>unmount.push(fn)}
  const {descriptor}=parse(readFileSync(new URL('../src/components/CameraWidget.vue',import.meta.url),'utf8'))
  const script=compileScript(descriptor,{id:'widget-local-runtime'}).content
    .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
  const names=Object.keys(dependencies).filter(name=>name!=='default' && /^[A-Za-z_$][\w$]*$/.test(name))
  const component=new Function(...names,script)(...names.map(name=>dependencies[name]))
  const panel=scope.run(()=>component.setup({}, {expose(){},emit(){}}))
  const drawn=[],canvas={width:0,height:0,getContext:()=>({drawImage:source=>drawn.push(source)})}
  panel.previewCanvas.value=canvas
  const publish=()=>{preview.frame={canvas:{local:true},session:state.virtual_session,key:virtualCaptureKey(state),width:640,height:480,sequence:1}}
  const settle=async()=>{for(let n=0;n<4;n++)await Vue.nextTick()}
  assert.equal(preview.visible,true);publish();const local=preview.frame.canvas;await settle()
  assert.deepEqual(drawn,[local]);assert.deepEqual([canvas.width,canvas.height],[640,480])
  state.result={image:{base64:'another-server-result'}};await settle();assert.equal(drawn.length,1)
  panel.open.value=false;assert.equal(preview.visible,false);assert.equal(preview.frame,null)
  panel.open.value=true;w.mode='real';assert.equal(preview.visible,false);assert.equal(preview.frame,null)
  w.mode='sim';publish();await settle();assert.equal(drawn.length,2)
  unmount.forEach(fn=>fn());assert.equal(preview.visible,false);assert.equal(preview.frame,null)
})

test('CPU-only pixel-copy comparison at three resolutions (wall times are diagnostics, not GPU claims)',t=>{
  const summary=[]
  for(const width of [320,640,960]){
    const height=width*.75,stride=width*4,pixels=new Uint8Array(width*height*4),image={data:new Uint8ClampedArray(pixels.length)}
    pixels.fill(87)
    const original=()=>{for(let y=0;y<height;y++)image.data.set(pixels.subarray((height-y-1)*stride,(height-y)*stride),y*stride)}
    const cached=createVirtualPixelCopy(pixels,image,width,height)
    for(let n=0;n<50;n++){original();cached()}
    const before=[],after=[]
    for(let n=0;n<200;n++)for(const [fn,samples] of n%2?[[cached,after],[original,before]]:[[original,before],[cached,after]]){
      const start=performance.now();fn();samples.push(performance.now()-start)
    }
    const stats=samples=>{samples.sort((a,b)=>a-b);return {p50_ms:+samples[99].toFixed(4),p95_ms:+samples[189].toFixed(4)}}
    summary.push({size:`${width}x${height}`,original:stats(before),cached:stats(after),row_views_per_frame_before:height,row_views_per_frame_after:0})
    assert.equal(image.data[0],87)
  }
  t.diagnostic(JSON.stringify({runtime:process.version,platform:process.platform,arch:process.arch,warmup:50,samples:200,CPU_pixel_copy:summary}))
})


test('preview with detection off performs no encode or upload while continuing local frames',async t=>{
 const h=schedulerFixture(t,{holdCapture:true});h.state.config.detection_enabled=false
 h.captureTasks[0].resolve();await h.ready();for(let n=0;n<5;n++){const tick=h.tick();await Promise.resolve();h.captureTasks.at(-1).resolve();await tick}
 assert.ok(h.preview.completed>=5);assert.equal(h.encodes,0);assert.equal(h.uploads.length,0)
})
