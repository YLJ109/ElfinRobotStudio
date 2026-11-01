import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive } from 'vue'
import { readFileSync } from 'node:fs'
import { parse,compileScript,compileTemplate } from '@vue/compiler-sfc'
import { createCameraSource,VIRTUAL_CAMERA,BROWSER_CAMERA,virtualPreview } from '../src/cameraSource.js'
import { applyVisionSnapshot, localVirtualPreview, virtualCaptureKey } from '../src/visionWorkspace.js'
import { setHandFollowDetectionDemand } from '../src/handFollowInput.js'

function fixture(input='camera'){
  const values=new Map(),calls=[],state=reactive({instance_id:'engine-a',revision:0,config:{input,enabled:input==='virtual'},result:null,stale:false})
  const storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)}
  const w=reactive({cameras:{active:null,devices:[{id:'usb:1',name:'USB 1',streams:['color']},{id:'usb:2',name:'USB 2',streams:['color','depth']}]},
    async closeCamera(session){calls.push(['close',session]);if(this.cameras.active?.session!==session)return {ok:false,why:'会话已变更'};this.cameras.active=null;return {ok:true}},
    async openCamera(id,streams,fps){calls.push(['open',id,streams,fps]);this.cameras.active={id,session:'opened-session',state:'streaming'};return {ok:true,active:{...this.cameras.active}}},
    async refreshCameras(){calls.push('scan');return {devices:this.cameras.devices,active:this.cameras.active,error:''}}})
  const request=async(path,patch)=>{calls.push({...patch});return {ok:true,instance_id:state.instance_id,revision:state.revision+1,config:{...state.config,...patch},result:null}}
  const detectionCalls=[],pipeline={state:reactive({enabled:false}),async configure(enabled,source,session){detectionCalls.push({enabled,source,session});Object.assign(this.state,{enabled,source,session});return true}}
  return {w,state,storage,calls,values,detectionCalls,pipeline,c:createCameraSource(w,{state,storage,request,pipeline})}
}

test('explicit color camera open starts the one detector only while hand-follow demand is set',async()=>{
  const {c,w,detectionCalls}=fixture('virtual')
  try{
   await c.select('usb:2')
   // 没有跟随需求时：只开画面，不起手掌推理。
   await c.toggle()
   assert.equal(w.cameras.active.session,'opened-session')
   assert.deepEqual(detectionCalls,[])
   assert.equal(c.handDetection.value,false)
   // 需求置位后，同一个相机才允许拉起检测，且重复启用是幂等的。
   setHandFollowDetectionDemand(true)
   await c.toggle();await c.toggle()
   assert.equal(w.cameras.active.session,'opened-session')
   assert.deepEqual(detectionCalls,[{enabled:true,source:'local',session:'opened-session'}])
   assert.equal(c.handDetection.value,true)
   await c.setHandDetection(true)
   assert.equal(detectionCalls.length,1)
  }finally{setHandFollowDetectionDemand(false)}
})
test('all reported devices plus virtual are listed; constructing never scans or opens',()=>{
  const {c,calls}=fixture();assert.deepEqual(c.options.value.map(o=>o.name),['1 · USB 1','2 · USB 2','仿真摄像头']);assert.equal(c.selected.value,'');assert.equal(c.options.value.at(-1).id,VIRTUAL_CAMERA);assert.deepEqual(calls,[])
})

test('visible source discovery runs once per backend session and never opens capture',async()=>{
  const {w,c,calls}=fixture('virtual');w.snap={session_id:'boot-a'};w.cameras.devices=[]
  w.refreshCameras=async()=>{calls.push('scan');w.cameras.devices=[{id:'usb:laptop',name:'Laptop Camera'}];return {devices:w.cameras.devices}}
  assert.equal(await c.ensureDevices(),true)
  assert.deepEqual(c.options.value.map(o=>o.name),['1 · Laptop Camera','仿真摄像头'])
  await c.ensureDevices();assert.deepEqual(calls,['scan']);assert.equal(c.message.value,'')
  w.snap.session_id='boot-b';w.cameras.devices=[]
  await c.ensureDevices();assert.deepEqual(calls,['scan','scan'])
  assert.equal(w.cameras.active,null)
})

test('mount, reconnect and a backend session change never scan or open camera hardware',async()=>{
  const {w,calls}=fixture(),mounted=[],unmounted=[],listeners=new Set()
  const source=readFileSync(new URL('../src/cameraSource.js',import.meta.url),'utf8')
  const body=source.split('export function useCameraSource(w){')[1].split('// 知识点')[0]
  const use=new Function('controllers','createCameraSource','onMounted','onBeforeUnmount','window','w',body.slice(0,body.lastIndexOf('}')))(
    new WeakMap(),world=>createCameraSource(world,{state:reactive({config:{input:'camera',enabled:false}}),storage:{getItem(){return null}},request(){throw Error('no automatic config')}}),
    fn=>mounted.push(fn),fn=>unmounted.push(fn),{addEventListener:(_name,fn)=>listeners.add(fn),removeEventListener:(_name,fn)=>listeners.delete(fn)},w)
  mounted.forEach(fn=>fn())
  w.connected=true;w.snap={session_id:'boot-a'};await Promise.resolve()
  w.connected=false;w.snap={session_id:'boot-b'};w.connected=true;await Promise.resolve()
  assert.deepEqual(calls,[])
  assert.equal(await use.refresh(),true);assert.deepEqual(calls,['scan'])
  unmounted.forEach(fn=>fn());assert.equal(listeners.size,0)
})

test('real mode hides a persisted virtual source and refuses virtual selection without hardware calls',async()=>{
  const {w,c,calls,state}=fixture('virtual');w.snap={mode:'real'}
  assert.equal(c.virtual.value,false);assert.equal(c.running.value,false);assert.equal(c.selected.value,'')
  assert.equal(c.options.value.some(row=>row.id===VIRTUAL_CAMERA),false)
  assert.equal(await c.select(VIRTUAL_CAMERA),false);assert.deepEqual(calls,[])
  assert.equal(state.config.input,'virtual','UI mode change must not mutate persistent configuration')
  w.snap.mode='sim';assert.equal(c.virtual.value,true);assert.equal(c.options.value.at(-1).id,VIRTUAL_CAMERA)
})

test('reload discovers a hot-plugged device without reopening active capture',async()=>{
  const {w,c,calls}=fixture();w.snap={mode:'real'}
  w.cameras.active={id:'usb:1',session:'existing-session'}
  w.refreshCameras=async()=>{calls.push('scan');w.cameras.devices=[...w.cameras.devices,{id:'usb:new',name:'New USB'}];return {ok:true}}
  assert.equal(await c.refresh(),true);assert.ok(c.options.value.some(d=>d.id==='usb:new'))
  assert.deepEqual(calls,['scan']);assert.equal(w.cameras.active.session,'existing-session')
  assert.match(c.message.value,/已重新加载/)
})

test('real mode empty scan never recommends unavailable simulation camera',async()=>{
  const {w,c}=fixture('virtual');w.mode='real';w.cameras.devices=[]
  await c.refresh();assert.doesNotMatch(c.message.value,/仿真/);assert.match(c.message.value,/重新加载/)
})

test('selecting virtual preserves explicit historical-value installation without opening hardware',async()=>{
  const {c,state,calls}=fixture()
  state.config.virtual={offset_mm:[72.1,0,-24.25],direction:[1000,0,0],fps:15,width:640,fov:55}
  assert.equal(await c.select(VIRTUAL_CAMERA),true)
  assert.deepEqual(calls,[{input:'virtual',enabled:true}])
  assert.deepEqual(state.config.virtual,{offset_mm:[72.1,0,-24.25],direction:[1000,0,0],fps:15,width:640,fov:55})
})
test('real to virtual closes real before enabling virtual; dropdown and preference agree',async()=>{
  const {w,c,calls,values}=fixture();w.cameras.active={id:'usb:1',session:'existing-session'}
  assert.equal(await c.select(VIRTUAL_CAMERA),true)
  assert.deepEqual(calls,[['close','existing-session'],{input:'virtual',enabled:true}]);assert.equal(c.selected.value,VIRTUAL_CAMERA);assert.equal(values.get('huayan.camera.source'),VIRTUAL_CAMERA)
})
test('virtual to real stops virtual without opening hardware; explicit toggle opens chosen device',async()=>{
  const {c,calls,state}=fixture('virtual')
  await c.select('usb:2');assert.equal(state.config.enabled,false);assert.equal(c.selected.value,'usb:2')
  assert.deepEqual(calls,[{input:'camera',enabled:false}]);await c.toggle()
  assert.deepEqual(calls.slice(1),[{input:'camera',enabled:false},['open','usb:2',['color'],60],{input:'camera',enabled:true}])
})
test('close rejection never enables virtual or changes stored device',async()=>{
  const {w,c,state,calls}=fixture();w.cameras.active={id:'usb:1',session:'existing-session'};w.closeCamera=async()=>({ok:false,why:'关闭失败'})
  assert.equal(await c.select(VIRTUAL_CAMERA),false);assert.equal(state.config.input,'camera');assert.deepEqual(calls,[]);assert.match(c.message.value,/关闭失败/)
})
test('unknown devices fail safely and explicit scan does not open hardware',async()=>{
  const {c,calls}=fixture();assert.equal(await c.select('missing'),false);assert.deepEqual(calls,[])
  await c.refresh();assert.deepEqual(calls,['scan'])
})
test('physical open failure does not enable vision or report a running camera',async()=>{
  const {w,c,calls}=fixture();await c.select('usb:1');calls.length=0
  w.openCamera=async()=>({ok:false,why:'设备被占用'})
  assert.equal(await c.toggle(),false);assert.deepEqual(calls,[{input:'camera',enabled:false}]);assert.equal(c.running.value,false);assert.match(c.message.value,/占用/)
})
test('vision activation failure after physical open closes that new capture',async()=>{
  const {w,state,storage,calls}=fixture()
  storage.setItem('huayan.camera.selected','usb:1')
  const request=async(path,patch)=>{calls.push(patch);if(patch.enabled)throw new Error('识别配置失败');return {ok:true,instance_id:state.instance_id,revision:state.revision+1,config:{...state.config,...patch}}}
  const c=createCameraSource(w,{state,storage,request});assert.equal(await c.toggle(),false)
  assert.deepEqual(calls.at(-1),['close','opened-session']);assert.equal(w.cameras.active,null);assert.match(c.message.value,/识别配置失败/)
})
test('virtual close and reopen operate vision configuration only',async()=>{
  const {c,calls}=fixture('virtual');await c.toggle();assert.equal(c.running.value,false);await c.toggle();assert.equal(c.running.value,true)
  assert.deepEqual(calls,[{input:'virtual',enabled:false},{input:'virtual',enabled:true}])
})
test('shared controller rejects concurrent source clicks instead of opening two inputs',async()=>{
  const {w,c,calls}=fixture();let done;w.cameras.active={id:'usb:1',session:'existing-session'};w.closeCamera=()=>new Promise(resolve=>{done=()=>{w.cameras.active=null;resolve({ok:true})}})
  const task=c.select(VIRTUAL_CAMERA);assert.equal(await c.select('usb:2'),false);done();await task
  assert.deepEqual(calls,[{input:'virtual',enabled:true}])
})
test('same persisted real device and storage events reflect in source selector',async()=>{
  const {c,values}=fixture();await c.select('usb:2');assert.equal(values.get('huayan.camera.selected'),'usb:2')
  c.syncPreference({key:'huayan.camera.selected',newValue:'usb:1'});assert.equal(c.selected.value,'usb:1')
})
test('all explicit closes bind the observed session, including virtual toggle',async()=>{
  for(const input of ['camera','virtual']){
    const {w,c,calls}=fixture(input);w.cameras.active={id:'usb:1',session:'current-session'}
    await c.toggle();assert.deepEqual(calls[0],['close','current-session'])
  }
})
test('missing session refuses close instead of falling back to global close',async()=>{
  const {w,c,calls}=fixture();w.cameras.active={id:'usb:1'}
  assert.equal(await c.select(VIRTUAL_CAMERA),false);assert.deepEqual(calls,[]);assert.match(c.message.value,/会话未知/)
})
test('rollback uses returned open session and cannot close another window replacement',async()=>{
  const {w,state,storage,calls}=fixture();storage.setItem('huayan.camera.selected','usb:1')
  const request=async(path,patch)=>{
    if(patch.enabled){w.cameras.active={id:'usb:2',session:'other-window-session'};throw new Error('配置被占用')}
    return {ok:true,instance_id:'engine-a',revision:1,config:{...state.config,...patch}}
  }
  const c=createCameraSource(w,{state,storage,request});assert.equal(await c.toggle(),false)
  assert.deepEqual(calls.at(-1),['close','opened-session']);assert.equal(w.cameras.active.session,'other-window-session')
  assert.match(c.message.value,/配置被占用.*回滚未关闭会话/)
})
test('missing session in open response never causes a global rollback close',async()=>{
  const {w,state,storage,calls}=fixture();storage.setItem('huayan.camera.selected','usb:1')
  w.openCamera=async()=>({ok:true})
  const request=async(path,patch)=>{if(patch.enabled)throw new Error('配置失败');return {ok:true,instance_id:'engine-a',revision:1,config:{...state.config,...patch}}}
  const c=createCameraSource(w,{state,storage,request});assert.equal(await c.toggle(),false)
  assert.deepEqual(calls,[])
})
test('late config response cannot overwrite newer poll or persist obsolete source',async()=>{
  const {w,state,storage,values}=fixture();let resolve
  const request=()=>new Promise(done=>{resolve=done})
  const c=createCameraSource(w,{state,storage,request}),task=c.select('usb:1')
  applyVisionSnapshot(state,{instance_id:'engine-a',revision:10,config:{input:'virtual',enabled:true}})
  resolve({ok:true,instance_id:'engine-a',revision:1,config:{input:'camera',enabled:false}})
  assert.equal(await task,false);assert.equal(state.revision,10);assert.equal(c.selected.value,VIRTUAL_CAMERA,'shared source readback wins over an idle remembered browser selection')
  assert.equal(values.has('huayan.camera.selected'),false)
})
test('virtual preview requires a current local capture, never a recognized server image',()=>{
  const {state}=fixture('virtual');state.virtual_session='local-session'
  const preview=localVirtualPreview(state)
  state.result={source:'virtual',image:{mime:'image/jpeg',base64:'abcd'}}
  assert.equal(virtualPreview(state),null,'server image cannot masquerade as a local frame')
  preview.visible=true
  const frame={canvas:{},session:state.virtual_session,key:virtualCaptureKey(state)}
  preview.frame=frame;assert.equal(virtualPreview(state),frame)
  state.stale=true;assert.equal(virtualPreview(state),frame,'recognition staleness must not freeze local capture')
  state.config.enabled=false;assert.equal(virtualPreview(state),null);state.config.enabled=true
  preview.suspended=true;assert.equal(virtualPreview(state),null);preview.suspended=false
  preview.visible=false;assert.equal(virtualPreview(state),null);preview.visible=true
  state.virtual_session='replaced';assert.equal(virtualPreview(state),null)
  state.virtual_session=frame.session;state.config.input='camera';assert.equal(virtualPreview(state),null)
  state.config.input='virtual';preview.frame=null;assert.equal(virtualPreview(state),null)
})
for(const name of ['CameraWidget','CameraSettings'])test(name+' unified selector compiles with native Vue',()=>{
  const filename=name+'.vue',source=readFileSync(new URL('../src/components/'+filename,import.meta.url),'utf8'),{descriptor,errors}=parse(source,{filename})
  assert.deepEqual(errors,[]);const script=compileScript(descriptor,{id:name})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename,id:name,compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  assert.match(source,/:value="selected"/);assert.match(source,/@change="select\(\$event.target.value\)"/)
})

test('duplicate hardware interfaces share one numbered entry and preserve device selection',async()=>{
 const f=fixture();f.w.cameras.devices=[
  {id:'sdk:1',name:'Depth Camera',physical_id:'physical-1',streams:['color','depth']},
  {id:'usb:alias',name:'Depth Camera RGB',physical_id:'physical-1',alias_of:'sdk:1',streams:['color']},
  {id:'usb:2',name:'Depth Camera',physical_id:'physical-2',streams:['color']},
 ]
 assert.deepEqual(f.c.options.value,[{id:'sdk:1',name:'1 · Depth Camera'},{id:'usb:2',name:'2 · Depth Camera'},{id:VIRTUAL_CAMERA,name:'仿真摄像头'}])
 f.c.syncPreference({key:'huayan.camera.selected',newValue:'usb:alias'})
 assert.equal(f.c.selected.value,'sdk:1')
 assert.equal(await f.c.select('usb:2'),true);assert.equal(f.c.selected.value,'usb:2')
})

test('old browser source is not presented as another physical camera',()=>{
 const f=fixture();f.c.syncPreference({key:'huayan.camera.selected',newValue:BROWSER_CAMERA})
 assert.equal(f.c.selected.value,'');assert.equal(f.c.options.value.some(row=>row.id===BROWSER_CAMERA),false)
})
test('explicit follow starts a stopped selected camera once and enables the same detector',async()=>{
 const f=fixture();f.c.syncPreference({key:'huayan.camera.selected',newValue:'usb:2'})
 assert.equal(f.calls.length,0)
 assert.equal(await f.c.prepareHandFollow(),true)
 assert.deepEqual(f.calls.filter(row=>Array.isArray(row)&&row[0]==='open'),[['open','usb:2',['color'],30]])
 assert.equal(f.detectionCalls.length,1)
 assert.equal(await f.c.prepareHandFollow(),true)
 assert.equal(f.calls.filter(row=>Array.isArray(row)&&row[0]==='open').length,1)
})

test('explicit follow reports missing cameras without opening or enabling detection',async()=>{
 const f=fixture();f.w.cameras.devices=[]
 assert.equal(await f.c.prepareHandFollow(),false)
 assert.match(f.c.message.value,/未找到/)
 assert.equal(f.detectionCalls.length,0)
 assert.equal(f.calls.some(row=>Array.isArray(row)&&row[0]==='open'),false)
})

test('an existing workstation capture enables detection without enumerating or reopening hardware',async()=>{
 const f=fixture(),calls=[];f.w.cameras.active={id:'usb:1',session:'existing',state:'streaming'}
 const pipeline={state:reactive({enabled:false}),async configure(enabled,source,session){calls.push({enabled,source,session});Object.assign(this.state,{enabled,source,session});return true}}
 const request=async(_,patch)=>({ok:true,instance_id:'engine-a',revision:f.state.revision+1,config:{...f.state.config,...patch}})
 const c=createCameraSource(f.w,{state:f.state,request,storage:f.storage,pipeline})
 assert.equal(await c.prepareHandFollow(),true);assert.deepEqual(f.calls,[]);assert.deepEqual(calls,[{enabled:true,source:'local',session:'existing'}])
})


test('cancelling during camera configuration never opens a late capture',async()=>{
 const f=fixture();let finish,cancelled=false
 const request=(_,patch)=>new Promise(resolve=>{finish=()=>resolve({ok:true,instance_id:'engine-a',revision:1,config:{...f.state.config,...patch}})})
 const c=createCameraSource(f.w,{state:f.state,request,storage:f.storage,pipeline:f.pipeline})
 const pending=c.prepareHandFollow({cancelled:()=>cancelled})
 await Promise.resolve();await Promise.resolve();cancelled=true;finish()
 assert.equal(await pending,false)
 assert.equal(f.calls.some(row=>Array.isArray(row)&&row[0]==='open'),false)
 assert.equal(f.detectionCalls.length,0)
})

test('cancelled late camera open closes only its acquired capture',async()=>{
 const f=fixture();let finish,cancelled=false
 f.w.openCamera=()=>new Promise(resolve=>{finish=()=>{f.w.cameras.active={id:'usb:1',session:'late'};resolve({ok:true,active:{session:'late'}})}})
 const pending=f.c.prepareHandFollow({cancelled:()=>cancelled})
 for(let i=0;i<8&&!finish;i++)await Promise.resolve()
 assert.equal(typeof finish,'function');cancelled=true;finish()
 assert.equal(await pending,false);assert.equal(f.w.cameras.active,null)
 assert.deepEqual(f.calls.at(-1),['close','late']);assert.equal(f.detectionCalls.length,0)
})

test('camera permission or occupancy failure leaves no detector and allows retry',async()=>{
 const f=fixture(),open=f.w.openCamera
 f.w.openCamera=async()=>({ok:false,why:'Permission denied'})
 assert.equal(await f.c.prepareHandFollow(),false)
 assert.match(f.c.message.value,/Permission denied/);assert.equal(f.detectionCalls.length,0)
 assert.equal(f.c.busy.value,false)
 f.w.openCamera=open
 assert.equal(await f.c.prepareHandFollow(),true)
})

test('a stale virtual source reuses an active workstation capture instead of opening twice',async()=>{
 const f=fixture('virtual');f.w.cameras.active={id:'usb:1',session:'existing',requested_streams:['color']}
 assert.equal(await f.c.prepareHandFollow(),true)
 assert.equal(f.calls.some(row=>row==='scan'||(Array.isArray(row)&&row[0]==='open')),false)
 assert.equal(f.w.cameras.active.session,'existing');assert.equal(f.c.handDetection.value,true)
})

test('concurrent follow preparation cannot enumerate and open two cameras',async()=>{
 const f=fixture();let finish;const devices=[...f.w.cameras.devices];f.w.cameras.devices=[]
 f.w.refreshCameras=()=>new Promise(resolve=>{finish=()=>resolve({devices})})
 const pending=f.c.prepareHandFollow()
 assert.equal(await f.c.prepareHandFollow(),false)
 finish();assert.equal(await pending,true)
 assert.equal(f.calls.filter(row=>Array.isArray(row)&&row[0]==='open').length,1)
})


test('follow reuses already listed camera metadata instead of repeating a driver scan',async()=>{
 const f=fixture()
 f.w.refreshCameras=()=>{throw Error('unnecessary camera scan')}
 assert.equal(await f.c.prepareHandFollow(),true)
 assert.equal(f.calls.filter(row=>Array.isArray(row)&&row[0]==='open').length,1)
})
