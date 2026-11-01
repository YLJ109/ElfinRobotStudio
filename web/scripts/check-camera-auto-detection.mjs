// Actual CameraWidget setup and Vue watchers, with no physical camera or network.
// The real handFollowInput module owns the detection-demand semantics, so the
// widget's contract is exercised through the very same bus the app uses.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import * as follow from '../src/handFollowInput.js'

const source=readFileSync(new URL('../src/components/CameraWidget.vue',import.meta.url),'utf8')
function fixture({late=false}={}){
 const scope=Vue.effectScope(),calls=[],cleanup=[],listeners=new Map(),storage=new Map([['huayan.camera.open','1']])
 const world=Vue.reactive({mode:'sim',view:'power',connected:false,snap:null,cameras:{active:null}}),enabled=Vue.ref(false),message=Vue.ref(''),discovery=[]
 let resolve,startCount=0
 const controller={browser:Vue.computed(()=>false),virtual:Vue.computed(()=>false),browserPreview:{state:{},stop(){}},handDetection:enabled,
  detectionError:Vue.ref(''),busy:Vue.ref(false),selected:Vue.ref('usb:fixture'),options:Vue.ref([]),running:Vue.computed(()=>!!world.cameras.active),message,
  async setHandDetection(on){calls.push(on);if(on&&late){startCount++;await new Promise(done=>resolve=done)}enabled.value=on;return true},ensureDevices(){discovery.push(world.snap?.session_id)},refresh(){},select(){},toggle(){}}
 const doc={hidden:false,addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n),querySelector(){return null}}
 const imports={vue:{...Vue,onMounted(){},onBeforeUnmount:fn=>cleanup.push(fn)},pinia:{storeToRefs:Vue.toRefs},'../store':{useWorld:()=>world},
  './CameraFrame.vue':{default:{}},'./BrowserCameraPreview.vue':{default:{}},'../cameraSource.js':{useCameraSource:()=>controller,virtualPreview:()=>null},
  '../visionWorkspace.js':{visionWorkspace:Vue.reactive({config:{}}),localVirtualPreview:()=>({}),virtualRecognitionFps:()=>0},
  '../handFollowInput.js':follow}
 const compiled=compileScript(parse(source).descriptor,{id:'camera-auto'}).content.replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,path)=>bindings.startsWith('{')?`const ${bindings.replace(/\bas\b/g,':')}=imports[${JSON.stringify(path)}];`:`const ${bindings}=imports[${JSON.stringify(path)}].default;`).replace('export default','return')
 const component=new Function('imports','document','window','localStorage',compiled)(imports,doc,{addEventListener(){},removeEventListener(){}},{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)})
 const api=scope.run(()=>component.setup({}, {expose(){}}))
 const open=()=>world.cameras.active={session:'fake-color',state:'streaming',images:{color:{width:640,height:480,seq:1}}}
 return {api,world,calls,discovery,open,doc,resolve:()=>resolve?.(),get startCount(){return startCount},
  close(){cleanup.forEach(fn=>fn());scope.stop();follow.setHandFollowDetectionDemand(false)}}
}
test('only a connected visible camera window requests metadata after a backend restart',async()=>{
 const h=fixture()
 try{
  assert.deepEqual(h.discovery,[])
  h.world.snap={session_id:'boot-a'};h.world.connected=true;await Vue.nextTick()
  assert.deepEqual(h.discovery,['boot-a']);assert.deepEqual(h.calls,[])
  await h.api.closePanel();h.world.snap.session_id='boot-b';await Vue.nextTick()
  assert.deepEqual(h.discovery,['boot-a'])
  h.api.open.value=true;await Vue.nextTick();assert.deepEqual(h.discovery,['boot-a','boot-b'])
  h.api.pageVisible.value=false;h.world.snap.session_id='boot-c';await Vue.nextTick()
  assert.deepEqual(h.discovery,['boot-a','boot-b'])
 }finally{h.close()}
})
test('a color capture starts backend detection only while hand-follow demand is set',async()=>{
 const h=fixture()
 try{
  assert.deepEqual(h.calls,[])
  // 没有跟随需求时，即使彩色画面已经在采集也不得拉起推理。
  h.open();await Vue.nextTick();await Vue.nextTick()
  assert.deepEqual(h.calls,[]);assert.equal(h.api.detectHands.value,false)
  assert.equal(h.api.handStatus.value,'')
  // 点"开始跟随"才置位需求：此时才允许请求启动检测。
  follow.setHandFollowDetectionDemand(true);await Vue.nextTick();await Vue.nextTick()
  assert.deepEqual(h.calls,[true]);assert.equal(h.api.detectHands.value,true)
  assert.equal(h.api.handStatus.value,'手掌检测中')
  h.world.cameras.active.images.color.seq++;await Vue.nextTick();assert.deepEqual(h.calls,[true])
  assert.doesNotMatch(source,/type="checkbox"|@change="setHandDetection/)
 }finally{h.close()}
})
test('ending the follow demand stops the backend detector again',async()=>{
 const h=fixture()
 try{
  follow.setHandFollowDetectionDemand(true)
  h.open();await Vue.nextTick();await Vue.nextTick()
  assert.deepEqual(h.calls,[true])
  follow.setHandFollowDetectionDemand(false);await Vue.nextTick();await Vue.nextTick()
  assert.deepEqual(h.calls,[true,false])
  assert.equal(h.api.detectHands.value,false);assert.equal(h.api.handStatus.value,'')
 }finally{h.close()}
})
test('a late detector start after closing its window is explicitly revoked',async()=>{
 const h=fixture({late:true})
 try{
  follow.setHandFollowDetectionDemand(true)
  h.open();await Vue.nextTick();assert.equal(h.startCount,1)
  await h.api.closePanel();h.resolve();await Vue.nextTick();await Vue.nextTick()
  assert.deepEqual(h.calls,[true,false])
 }finally{h.close()}
})
test('depth-only capture never starts hand inference',async()=>{
 const h=fixture()
 try{
  follow.setHandFollowDetectionDemand(true)
  h.world.cameras.active={session:'depth',state:'streaming',images:{depth:{width:640,height:480,seq:1}}}
  await Vue.nextTick();await Vue.nextTick()
  assert.deepEqual(h.calls,[]);assert.equal(h.api.detectHands.value,false)
  assert.equal(h.api.handStatus.value,'')
 }finally{h.close()}
})
