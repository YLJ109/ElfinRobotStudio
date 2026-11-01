import { expandedCameraFence, constrainCameraOrbit } from '../src/cameraFence.js'
// Offline CPU/call-count harness. Real Three.js scene math/geometries; no browser,
// GPU, sockets, service, camera, hardware, dist build or persisted view writes.
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {performance as timer} from 'node:perf_hooks'
import * as Real from 'three'
import { TransformControls } from 'three/addons/controls/TransformControls.js'
import {buildVacuumReference,readToolFile,readLibraryTool,disposeAsset,applyToolSkin,createToolAnimation} from '../src/toolVisual.js'
import {robotAsset} from '../src/visualAssets.js'
import {applyJointCaps,applyRobotFinishes} from '../src/jointCaps.js'
import {createRobotSurfaceMaterial,smoothRobotSurface} from '../src/robotSurface.js'
import {robotSurfaceAssetUrl,hydrateRobotSurfaceGeometry,ROBOT_SURFACE_ASSET_VERSION,ROBOT_SURFACE_RECIPE} from '../src/robotSurfaceAsset.js'
import {GROUND_DIRECTIONS} from '../src/groundDirections.js'
import {recordMetric,snapshotMetrics,resetMetrics} from '../src/runtimeMetrics.js'
import {createObjectTeaching} from '../src/objectTeaching.js'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { OBJECT_CATALOG, OBJECT_SURFACES, catalogEntry, catalogSpec } from '../src/objectCatalog.js'
import { createJointPresentation } from '../src/jointPresentation.js'
import { objectGroundHeight } from '../src/objectGround.js'
import { createVirtualCamera } from '../src/virtualCamera.js'
import { createDrawingVisual } from '../src/drawingVisual.js'

const source=readFileSync(new URL('../src/scene.js',import.meta.url),'utf8')
const spec=JSON.parse(readFileSync(new URL('../public/models/elfin5/robot.json',import.meta.url),'utf8'))
const metrics={matrix:0,ancestors:0,traverse:0,geometryDispose:0,vector:0,quaternion:0,uploads:0,renders:0}
const originals=[]
for(const [proto,key,counter] of [[Real.Object3D.prototype,'updateMatrixWorld','matrix'],
  [Real.Object3D.prototype,'updateWorldMatrix','ancestors'],[Real.Object3D.prototype,'traverse','traverse'],
  [Real.BufferGeometry.prototype,'dispose','geometryDispose']]) {
  const original=proto[key];originals.push(()=>{proto[key]=original})
  proto[key]=function(...args){metrics[counter]++;return original.apply(this,args)}
}
const upload=Object.getOwnPropertyDescriptor(Real.BufferAttribute.prototype,'needsUpdate')
Object.defineProperty(Real.BufferAttribute.prototype,'needsUpdate',{...upload,set(value){if(value)metrics.uploads++;upload.set.call(this,value)}})
originals.push(()=>Object.defineProperty(Real.BufferAttribute.prototype,'needsUpdate',upload))
class Vector3 extends Real.Vector3 {constructor(...args){super(...args);metrics.vector++}}
class Quaternion extends Real.Quaternion {constructor(...args){super(...args);metrics.quaternion++}}
const events=new Map(),frames=new Map();let frameId=0,clock=1000,storageWrites=0,lastScene,lastCamera,canvas
const ctx=new Proxy({}, {get:(o,k)=>o[k] ?? (()=>{}),set:(o,k,v)=>(o[k]=v,true)})
const element=()=>{
  const listeners=new Map()
  return {width:800,height:600,clientWidth:800,clientHeight:600,style:{},listeners,
    addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn)},
    removeEventListener(name,fn){listeners.get(name)?.delete(fn)},
    dispatchEvent(event){for(const fn of [...(listeners.get(event.type)||[])])fn(event)},
    listenerCount(){return [...listeners.values()].reduce((n,set)=>n+set.size,0)},
    setPointerCapture(){},releasePointerCapture(){},
    getContext:()=>ctx,remove(){},getBoundingClientRect:()=>({left:0,top:0,width:800,height:600})}
}
const win={devicePixelRatio:2,addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name),dispatchEvent:event=>{events.get(event.type)?.(event);return true}}
const doc={hidden:false,createElement:element,addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name)}
const longObservers=[]
class PerformanceObserver {
  static supportedEntryTypes=['longtask']
  constructor(callback){this.callback=callback;this.connected=false;longObservers.push(this)}
  observe(options){assert.deepEqual(options,{type:'longtask',buffered:false});this.connected=true}
  disconnect(){this.connected=false}
  deliver(entries){this.callback({getEntries:()=>entries})}
}
class Renderer {
  constructor(){this.domElement=element();canvas=this.domElement;this.shadowMap={};this.pixelRatio=1}
  setPixelRatio(value){this.pixelRatio=value}
  setSize(){}
  render(scene,camera){metrics.renders++;lastScene=scene;lastCamera=camera;scene.updateMatrixWorld();camera.updateMatrixWorld()}
  dispose(){}
}
class GLTFLoader {
  setMeshoptDecoder(){return this}
  async parseAsync(){const group=new Real.Group();group.add(new Real.Mesh(new Real.BoxGeometry(.08,.08,.08),new Real.MeshStandardMaterial()));return {scene:group}}
}
class RoomEnvironment extends Real.Group {dispose(){}}
class PMREMGenerator {fromScene(){return {texture:new Real.Texture(),dispose(){}}}dispose(){}}
const deps={expandedCameraFence,constrainCameraOrbit,TransformControls,THREE:{...Real,Vector3,Quaternion,WebGLRenderer:Renderer,PMREMGenerator},
  createGameLayer:()=>({setSceneObjects(){},presentAttachment(){},setState(){return {ok:true}},update(){},animating(){return false},dispose(){},
    center(){return [0.52,.03,0]},info(){return {visible:false,pieces:0,board:''}}}),
  recordMetric,PerformanceObserver,mergeGeometries,catalogEntry,OBJECT_SURFACES,createVirtualCamera,createDrawingVisual,createRobotSurfaceMaterial,smoothRobotSurface,
  robotSurfaceAssetUrl,hydrateRobotSurfaceGeometry,ROBOT_SURFACE_ASSET_VERSION,ROBOT_SURFACE_RECIPE,
  createJointPresentation: () => createJointPresentation({now: () => clock/1000}),
  buildVacuumReference,readToolFile,readLibraryTool,disposeAsset,applyToolSkin,createToolAnimation,robotAsset,applyJointCaps,applyRobotFinishes,GROUND_DIRECTIONS,objectGroundHeight,
  window:win,document:doc,localStorage:{getItem:()=>null,setItem:()=>storageWrites++},
  fetch:async()=>({ok:true,json:async()=>structuredClone(spec),arrayBuffer:async()=>new ArrayBuffer(1)}),
  performance:{now:()=>clock},requestAnimationFrame:fn=>{const id=++frameId;frames.set(id,fn);return id},
  cancelAnimationFrame:id=>frames.delete(id),ResizeObserver:class{observe(){}disconnect(){}},
  loadModule:async name=>name.includes('GLTFLoader')?{GLTFLoader}:name.includes('meshoptimizer')?{MeshoptDecoder:{}}:{RoomEnvironment},
}
const body=source.replace(/^import .*from ['"][^'"]+['"]\r?\n/gm,'')
  .replaceAll(/import\('([^']+)'\)/g,(_,name)=>`loadModule('${name}')`)
  .replace('export async function createScene','async function createScene')+'\nreturn createScene'
let createScene=new Function(...Object.keys(deps),body)(...Object.values(deps))
const reset=()=>{for(const k in metrics)metrics[k]=0}
const snapshot=()=>({...metrics})
function step(ms=1000/60){clock+=ms;const [id,fn]=frames.entries().next().value;frames.delete(id);fn(clock)}
const find=(predicate)=>{let found;lastScene.traverse(o=>{if(predicate(o))found=o});return found}


// Failure injection is CPU-only: fake renderer, real Three geometries; no GPU claim.
let rendererDisposals=0,canvasRemovals=0,mode='missing'
class CheckedRenderer extends Renderer {
 constructor(){super();this.domElement.remove=()=>canvasRemovals++}
 setSize(){if(mode==='size')throw new Error('size failure')}
 async compileAsync(){if(mode==='compile')throw new Error('compile failure')}
 dispose(){rendererDisposals++}
}
deps.THREE={...deps.THREE,WebGLRenderer:CheckedRenderer}
deps.createVirtualCamera=(...args)=>{if(mode==='virtual')throw new Error('virtual initialization failed');return createVirtualCamera(...args)}
const originalFetch=deps.fetch
let pendingFetch
deps.fetch=async (url,options={})=>{
 if(mode==='abort'&&url.endsWith('.glb'))return new Promise((resolve,reject)=>{
  pendingFetch?.()
  if(options.signal?.aborted)return reject(options.signal.reason)
  options.signal?.addEventListener('abort',()=>reject(options.signal.reason),{once:true})
 })
 return mode==='missing'&&!url.endsWith('robot.json')?{ok:false}:originalFetch(url)
}
createScene=new Function(...Object.keys(deps),body)(...Object.values(deps))
for(const failure of ['missing','size','virtual']){
 mode=failure;const before=rendererDisposals,removed=canvasRemovals
 await assert.rejects(createScene({clientWidth:800,clientHeight:600,appendChild(){}},{model:'elfin5'}))
 assert.equal(rendererDisposals,before+1,'failed initialization leaked renderer: '+failure)
 assert.equal(canvasRemovals,removed+1,'failed initialization stranded canvas: '+failure)
 assert.equal(events.size,0);assert.equal(frames.size,0)
}
mode='abort'
const cancellation=new AbortController()
const firstFetch=new Promise(resolve=>{pendingFetch=resolve}),beforeCancel=rendererDisposals
const cancelled=createScene({clientWidth:800,clientHeight:600,appendChild(){}},{model:'elfin5',signal:cancellation.signal})
await firstFetch;cancellation.abort();await assert.rejects(cancelled)
assert.equal(rendererDisposals,beforeCancel+1);assert.equal(events.size,0);assert.equal(frames.size,0)
mode='ok'
const beforeSkin=rendererDisposals
await assert.rejects(createScene({clientWidth:800,clientHeight:600,appendChild(){}},{model:'elfin5',skin:'invalid'}),/未知皮肤/)
assert.equal(rendererDisposals,beforeSkin+1);assert.equal(events.size,0);assert.equal(frames.size,0)
mode='compile'
const candidate=await createScene({clientWidth:800,clientHeight:600,appendChild(){}},{model:'elfin5'})
await assert.rejects(candidate.prepareFirstFrame(),/compile failure/);candidate.dispose()
assert.equal(events.size,0);assert.equal(frames.size,0)
mode='ok'
const valid=await createScene({clientWidth:800,clientHeight:600,appendChild(){}},{model:'elfin5'}),rendersBefore=metrics.renders
await valid.prepareFirstFrame();assert.equal(metrics.renders,rendersBefore+1,'first-frame gate must render, not only compile');valid.dispose()
const appSource=readFileSync(new URL('../src/App.vue',import.meta.url),'utf8')
const loadCode=appSource.slice(appSource.indexOf('async function loadVisualModel('),appSource.indexOf('async function selectVisualTool(')).replace("await import('./scene')",'await createSceneModule()')
function appFixture({failure=false,toolWait=null}={}){
 let removed=0,newDisposed=0,oldDisposed=0,firstFrames=0
 const old={dispose(){oldDisposed++}},host={style:{},remove(){removed++}}
 const next=new Proxy({dispose(){newDisposed++},prepareFirstFrame:async()=>{firstFrames++;if(failure)throw new Error('shader failed')},setToolVisual:async()=>{if(toolWait)await toolWait},setView:()=>false,getView:()=>({})},{get:(o,k)=>k==='then'?undefined:o[k]||(()=>{})})
 const injected={sceneLoading:{value:false},document:{createElement:()=>host},canvasBox:{value:{appendChild(){}}},visualModel:{value:'elfin5'},visualSkin:{value:'factory'},createSceneModule:async()=>({createScene:async()=>next}),visualTool:{},w:{snap:null},mappedVisionObjects:()=>[],visionWorkspace:{},readPreference:()=>null,localStorage:{getItem:()=>null},WORKSPACE_VIEW_KEY:'view',savePreference(){},setFrameHandler(){},feed3d(){},latestFrame:()=>null,restoreSceneHistory(){},interactionShowing:{value:false},handBoxDraft:{value:null},handDirection:{value:0}}
 const init='let scene=old,sceneHost=null,sceneInitialization=null,disposed=false,needsFit=false,sampleIdentity=null;'
 const api=new Function(...Object.keys(injected),'old',init+loadCode+';return {loadVisualModel,unmount(){disposed=true},current(){return scene}}')(...Object.values(injected),old)
 return {...api,stats:()=>({removed,newDisposed,oldDisposed,firstFrames}),w:injected.w}
}
const failed=appFixture({failure:true});assert.equal(await failed.loadVisualModel(),false);assert.deepEqual(failed.stats(),{removed:1,newDisposed:1,oldDisposed:0,firstFrames:1});assert.match(failed.w.error,/shader failed/)
let release;const waiting=new Promise(resolve=>{release=resolve});const late=appFixture({toolWait:waiting});const pending=late.loadVisualModel();await Promise.resolve();await Promise.resolve();late.unmount();release();await pending;assert.equal(late.stats().newDisposed,1);assert.equal(late.stats().oldDisposed,0)
console.log('scene initialization cleanup, missing mesh, first-frame failure and late unmount verified (CPU only)')
for(const restore of originals.reverse())restore()

