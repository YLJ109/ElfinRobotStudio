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
const deps={TransformControls,THREE:{...Real,Vector3,Quaternion,WebGLRenderer:Renderer,PMREMGenerator},
  recordMetric,PerformanceObserver,mergeGeometries,catalogEntry,OBJECT_SURFACES,createVirtualCamera,createRobotSurfaceMaterial,smoothRobotSurface,
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
const createScene=new Function(...Object.keys(deps),body)(...Object.values(deps))
const reset=()=>{for(const k in metrics)metrics[k]=0}
const snapshot=()=>({...metrics})
function step(ms=1000/60){clock+=ms;const [id,fn]=frames.entries().next().value;frames.delete(id);fn(clock)}
const find=(predicate)=>{let found;lastScene.traverse(o=>{if(predicate(o))found=o});return found}

let scene,disposeControls,disposeCancelled=0
try {
  scene=await createScene({clientWidth:800,clientHeight:600,appendChild(){}},{model:'elfin5'})
  await scene.setToolVisual({kind:'vacuum-reference',dimensions:{},rotation:[0,0,0]})
  const view=scene.getView()
  const bounds={min:[-400,-550,450],max:[400,-250,950]}
  const state={configured:true,direction:0,bounds,target:[100,-400,700],actual:[0,-400,700]}
  const q=[0,25,70,0,25,0]
  scene.setImmediate(q);scene.setHandWorkspace(state,true);scene.setGhost(q,'ghost')
  scene.showCapsules=true
  const caps=Array.from({length:8},(_,i)=>[i*10,0,50,i*10,0,150,20])
  const object={id:'fixture',kind:'box',size:[100,100,100],xyz:[300,-300,50],color:'#4fd8cf'}
  scene.setObjects([object],null)
  reset();const begin=timer.now()
  for(let i=0;i<600;i++){
    clock+=1000/30
    scene.setAngles(q);scene.setGhost(q,'ghost');scene.setHandWorkspace(structuredClone(state),true)
    scene.setCapsules(caps);scene.setObjects([structuredClone(object)],null);scene.markCollisions([])
  }
  const repeated={snapshots:600,elapsedMs:timer.now()-begin,...snapshot()}
  reset();resetMetrics();const renderStart=timer.now()
  for(let i=0;i<180;i++)step()
  const idleFrames={callbacks:180,elapsedMs:timer.now()-renderStart,...snapshot()}
  const path=[[0,0,0],[10,20,30]]
  reset();for(let i=0;i<100;i++)scene.setPlanPath(structuredClone(path))
  const samePath=snapshot()
  console.log(JSON.stringify({kind:'offline CPU calls, not GPU or actual FPS',repeated,idleFrames,samePath},null,2))
  if(!process.argv.includes('--baseline')){
    assert.equal(repeated.geometryDispose,0)
    assert.ok(repeated.matrix<100,'unchanged snapshots must not force robot/ghost tree updates')
    assert.ok(repeated.vector<100,'scratch vectors must replace per-capsule/marker allocations')
    assert.equal(samePath.geometryDispose,0,'unchanged plan path must reuse its BufferGeometry')
    assert.ok(idleFrames.uploads<=2,'idle follow lines must not upload every render')
    assert.ok(idleFrames.ancestors<=22,'unchanged pose/TCP must not recalculate TCP ancestor chains every frame')
  }
  const timings=snapshotMetrics()
  assert.equal(timings['scene.frame'].n,180)
  assert.equal(timings['scene.update'].n,idleFrames.renders)
  assert.equal(timings['scene.render'].n,idleFrames.renders)
  // Every visual state remains independently live, not tied to pose equality.
  scene.markCollisions([{id:'fixture'}]);scene.setObjects([object],'fixture');scene.markCollisions([{id:'fixture'}])
  const mesh=find(o=>o.userData.id==='fixture')
  assert.equal(mesh.material.emissive.getHex(),0xe4553f)
  scene.markCollisions([]);assert.equal(mesh.material.emissive.getHex(),0x2fd6f0)
  scene.setCapsules([[100,200,300,100,200,500,25]])
  const capsule=find(o=>o.visible && o.isMesh && o.geometry.type==='CylinderGeometry' && o.material.wireframe)
  assert.deepEqual(capsule.position.toArray(),[.1,.4,-.2])
  assert.deepEqual(capsule.scale.toArray(),[.025,.2,.025])
  const allocationBefore=metrics.geometryDispose
  scene.setPlanPath(Array.from({length:65},(_,i)=>[i*10,100,200]))
  assert.equal(metrics.geometryDispose,allocationBefore+1)
  const planned=find(o=>o.isLine && o.material.color.getHex()===0xe0a33b)
  assert.equal(planned.geometry.drawRange.count,65)
  scene.setPlanPath([[10,20,30],[50,70,90]])
  assert.equal(planned.geometry.drawRange.count,2)
  assert.ok(planned.geometry.boundingSphere.radius<.1,'shrunk path bounds exclude unused capacity')
  const tcpBeforeScratch=scene.handWorkspaceInfo().toolTcp
  scene.setPlanPath([[200,400,800],[100,300,600]])
  assert.deepEqual(scene.handWorkspaceInfo().toolTcp,tcpBeforeScratch,'path bounds scratch must not corrupt cached TCP')
  scene.setFence({enabled:true,x:[-900,900],y:[-900,900],z:[0,1400]})
  scene.setFenceSafety('alarm');step();assert.equal(scene.fenceInfo().level,'alarm')
  const opacity=scene.fenceInfo().opacity;step(40);assert.notEqual(scene.fenceInfo().opacity,opacity)
  scene.setGhost(q,'ghost','alarm',true);step(40)
  const ghostOpacity=scene.ghostInfo().opacity;step(40);assert.notEqual(scene.ghostInfo().opacity,ghostOpacity)
  scene.setGhost(q,'ghost','red',false);step(40)
  const limitedOpacity=scene.ghostInfo().opacity;step(40);assert.equal(scene.ghostInfo().opacity,limitedOpacity,'97 percent red warning stays steady; only hard boundary flashes')
  scene.setFollowLatency(true);scene.setImmediate([20,30,60,0,30,0]);step(40)
  const beforeTcp=scene.handWorkspaceInfo().toolTcp
  scene.setTcpFrame({translation_mm:[0,0,105]});step(40)
  assert.ok(beforeTcp.some((v,i)=>Math.abs(v-scene.handWorkspaceInfo().toolTcp[i])>1))
  const configuredTcp=scene.handWorkspaceInfo().toolTcp
  reset();scene.handWorkspaceInfo();const fixedInfoAncestors=metrics.ancestors
  assert.ok(fixedInfoAncestors<10,'info reads may update the root quaternion chain, but not the full TCP chain')
  reset();for(let i=0;i<100;i++){scene.setTcpFrame({translation_mm:[0,0,105]});scene.handWorkspaceInfo()}
  assert.equal(metrics.ancestors,fixedInfoAncestors*100,'unchanged TCP frame adds no ancestor work beyond existing info quaternion reads')
  assert.deepEqual(scene.handWorkspaceInfo().toolTcp,configuredTcp)
  scene.setHandWorkspace({...state,direction:90,target:[250,-350,600]},true);step(40)
  assert.equal(scene.handWorkspaceInfo().direction,90)
  assert.deepEqual(scene.handWorkspaceInfo().basePosition,[0,0,0])
  assert.deepEqual(scene.getView(),view);assert.equal(storageWrites,0)
  // Same joints with a changed target/safety flag must still update on next render.
  reset();scene.setHandWorkspace({...state,direction:90,target:[280,-350,600]},true);step(40)
  assert.ok(metrics.uploads>=1)
  assert.ok(Number.isFinite(scene.dprInfo().averageSceneUpdateMs))
  assert.ok(Number.isFinite(scene.dprInfo().averageRenderSubmitMs))
  await scene.setSkin('cyber');assert.equal(scene.toolInfo().skin,'cyber')
  await scene.setSkin('factory');assert.equal(scene.toolInfo().skin,'factory')
  assert.ok(scene.toolInfo().finishes.some(f=>f.surface==='translucent-cup'))
  const teachUpdates=[]
  scene.setTeachTarget([100,-200,700,90,0,0],value=>teachUpdates.push(value))
  const teachTarget=find(o=>o.name==='teaching-target'),gizmo=find(o=>o.name==='teaching-gizmo')
  assert.deepEqual(teachTarget.position.toArray(),[.1,.7,.2],'TCP millimetres map to world X/Z/-Y metres')
  teachTarget.position.set(.3,.6,-.1);gizmo.controls.dispatchEvent({type:'objectChange'})
  assert.deepEqual(teachUpdates,[[300,100,600]],'drag emits TCP XYZ in millimetres, not scene axes')
  scene.setTeachTarget(null)
  assert.equal(teachTarget.visible,false);assert.equal(gizmo.visible,false);assert.equal(gizmo.controls.enabled,false)
  assert.equal(scene.onTeachTarget,null)
  // Object editing uses a second REAL Three TransformControls instance. Its
  // pointerUp runs the real mouseUp/dragging lifecycle; mesh math is not mocked.
  const edits=[],callbacks={
    start:xyz=>edits.push(['start',xyz]),change:xyz=>edits.push(['change',xyz]),
    commit:xyz=>edits.push(['commit',xyz]),cancel:()=>edits.push(['cancel']),
  }
  const other={...object,id:'other',xyz:[-100,200,300]}
  const originalXYZ=[300,-300,50]
  const objectView=scene.getView()
  scene.setObjects([object,other],'fixture')
  assert.equal(scene.setObjectTarget('missing',callbacks),false)
  assert.equal(scene.setObjectTarget('fixture',callbacks),true)
  const objectGizmo=find(o=>o.name==='object-gizmo'),control=objectGizmo.controls
  assert.ok(control instanceof TransformControls)
  assert.notEqual(control,gizmo.controls)
  assert.equal(control.mode,'translate');assert.equal(control.space,'world')
  const objectMesh=()=>find(o=>o.userData.id==='fixture')
  const beginMove=()=>{control.axis='X';control.dragging=true;control.dispatchEvent({type:'mouseDown',mode:'translate'})}
  const moveTo=(x,y,z)=>{objectMesh().position.set(x,y,z);control.dispatchEvent({type:'objectChange'})}
  const commits=()=>edits.filter(e=>e[0]==='commit').length
  const cancels=()=>edits.filter(e=>e[0]==='cancel').length
  beginMove();assert.deepEqual(edits.at(-1),['start',originalXYZ])
  moveTo(.51,.62,.73);assert.deepEqual(edits.at(-1),['change',[510,-730,620]])
  const orbitDown=[...canvas.listeners.get('pointerdown')][0]
  orbitDown({button:0,clientX:0,clientY:0,preventDefault(){}})
  events.get('pointermove')({clientX:100,clientY:100})
  canvas.dispatchEvent({type:'wheel',deltaY:1,preventDefault(){}})
  assert.deepEqual(scene.getView(),objectView,'object gizmo must own pointer drag without orbit/zoom competition')
  scene.setObjects([structuredClone(object),{...other,xyz:[500,600,700]}],'fixture')
  assert.deepEqual(objectMesh().position.toArray(),[.51,.62,.73],'old WS coordinates must not steal the local drag')
  assert.deepEqual(find(o=>o.userData.id==='other').position.toArray(),[.5,.7,-.6],'other objects continue receiving authoritative coordinates')
  scene.setObjects([{...object,xyz:[10,20,30]},other],'fixture')
  control.pointerUp({button:0})
  assert.equal(commits(),1);assert.deepEqual(edits.at(-1),['commit',[510,-730,620]])
  assert.equal(control.enabled,false);assert.equal(control.dragging,false)
  control.pointerUp({button:0});control.dispatchEvent({type:'mouseUp'})
  assert.equal(commits(),1,'mouseUp must submit once')
  scene.setObjects([{...object,xyz:[40,50,60]},other],'fixture')
  assert.deepEqual(objectMesh().position.toArray(),[.51,.62,.73],'pending save must retain local coordinates')
  const pendingCancels=cancels()
  events.get('pointercancel')();events.get('blur')()
  canvas.dispatchEvent({type:'lostpointercapture'})
  doc.hidden=true;events.get('visibilitychange')()
  assert.equal(control.enabled,false);assert.equal(cancels(),pendingCancels,'automatic cancellation must not unlock an in-flight HTTP save')
  doc.hidden=false;events.get('visibilitychange')()
  assert.equal(control.enabled,false)
  control.dispatchEvent({type:'mouseDown'});control.dispatchEvent({type:'mouseUp'})
  assert.equal(commits(),1,'pending lock prevents a second gesture/save')
  assert.equal(scene.finishObjectMove([NaN,0,0]),false);assert.equal(control.enabled,false)
  assert.equal(scene.finishObjectMove([100,200,300]),true)
  assert.deepEqual(objectMesh().position.toArray(),[.1,.3,-.2]);assert.equal(control.enabled,true)
  assert.equal(scene.finishObjectMove([1,2,3]),false,'late duplicate finish cannot move an idle object')
  beginMove();control.pointerUp({button:0})
  assert.equal(commits(),1,'unchanged gesture must not PATCH')
  beginMove();moveTo(.9,.8,.7)
  scene.setObjects([{...object,xyz:[70,80,90]},other],'fixture')
  assert.equal(scene.cancelObjectMove(),true)
  assert.deepEqual(objectMesh().position.toArray(),[.07,.09,-.08]);assert.equal(control.enabled,true)
  control.pointerUp({button:0});assert.equal(commits(),1)
  // Explicit rejection rollback is allowed to unlock pending, unlike browser
  // pointercancel/blur/hidden. Rollback always uses the newest received snapshot.
  beginMove();moveTo(.9,.8,.7);control.pointerUp({button:0})
  scene.setObjects([{...object,xyz:[80,90,100]},other],'fixture')
  assert.equal(scene.cancelObjectMove(),true);assert.equal(control.enabled,true)
  assert.deepEqual(objectMesh().position.toArray(),[.08,.1,-.09])
  for(const event of ['pointercancel','blur','hidden']) {
    const beforeCancel=cancels(),beforeCommit=commits()
    beginMove();moveTo(.9,.8,.7)
    if(event==='hidden'){doc.hidden=true;events.get('visibilitychange')()}
    else events.get(event)()
    assert.equal(cancels(),beforeCancel+1);assert.equal(commits(),beforeCommit)
    assert.equal(control.enabled,true);assert.equal(control.dragging,false)
    assert.deepEqual(objectMesh().position.toArray(),[.08,.1,-.09])
    control.pointerUp({button:0});assert.equal(commits(),beforeCommit)
    if(event==='hidden'){doc.hidden=false;events.get('visibilitychange')()}
  }
  for(const kind of ['box','sphere','cylinder']) {
    const shape={...object,kind,size:[100,100,200]}
    scene.setObjects([shape,other],'fixture');assert.equal(scene.setObjectTarget('fixture',callbacks),true)
    assert.equal(control.object,objectMesh(),'every object kind attaches its existing mesh')
    const geometry=objectMesh().geometry
    scene.setObjects([{...shape,size:[200,300,400]},other],'fixture')
    assert.notEqual(objectMesh().geometry,geometry,'same-kind size changes must rebuild geometry')
    const params=objectMesh().geometry.parameters
    assert.deepEqual(objectMesh().rotation.toArray().slice(0,3),[0,0,0],'placement objects have no attitude field')
    if(kind==='box')assert.deepEqual([params.width,params.height,params.depth],[.2,.4,.3])
    else if(kind==='sphere')assert.equal(params.radius,.2)
    else {
      assert.deepEqual([params.radiusTop,params.height],[.2,.4])
      const dimensions=new Real.Box3().setFromObject(objectMesh()).getSize(new Real.Vector3())
      for(const value of dimensions.toArray()) assert.ok(Math.abs(value-.4)<1e-6,'cylinder Box3 is height .4 along Y, diameter .4 along X/Z')
      // Unequal height/diameter catches an accidental quarter-turn even when
      // the requested .4-height/.4-diameter example has symmetric bounds.
      scene.setObjects([{...shape,size:[200,300,600]},other],'fixture')
      const tall=new Real.Box3().setFromObject(objectMesh()).getSize(new Real.Vector3())
      for(const [i,value] of [.4,.6,.4].entries()) assert.ok(Math.abs(tall.getComponent(i)-value)<1e-6,'base Z height must remain the scene Y axis')
    }
    const before=commits();beginMove();moveTo(.1,.2,-.3);control.pointerUp({button:0})
    assert.equal(commits(),before+1)
    assert.deepEqual(edits.at(-1),['commit',[100,300,kind==='cylinder'?300:200]])
    assert.equal(scene.finishObjectMove(),true);assert.equal(control.enabled,true)
  }
  // Switch, detach and deletion invalidate a gesture without a late commit.
  for(const pending of [false,true]) {
    scene.setObjects([object,other],'fixture');scene.setObjectTarget('fixture',callbacks)
    beginMove();moveTo(.9,.8,.7);if(pending)control.pointerUp({button:0})
    const beforeCommit=commits(),beforeCancel=cancels()
    scene.setObjects([other],null)
    assert.equal(cancels(),beforeCancel+1);assert.equal(commits(),beforeCommit)
    assert.equal(control.object,undefined);assert.equal(control.enabled,false);assert.equal(objectGizmo.visible,false)
    control.dispatchEvent({type:'mouseUp'});assert.equal(commits(),beforeCommit)
    assert.equal(scene.finishObjectMove([1,2,3]),false)
  }
  scene.setObjects([object,other],'fixture');scene.setObjectTarget('fixture',callbacks)
  beginMove();moveTo(.9,.8,.7)
  const beforeSwitch=commits()
  scene.setObjectTarget('other',callbacks)
  assert.equal(control.object,find(o=>o.userData.id==='other'))
  assert.deepEqual(objectMesh().position.toArray(),[.3,.05,.3])
  control.pointerUp({button:0});assert.equal(commits(),beforeSwitch)
  assert.equal(scene.setObjectTarget(null),true);assert.equal(control.enabled,false)
  scene.setObjectTarget('fixture',callbacks);beginMove();moveTo(.9,.8,.7)
  const beforeTeach=commits(),cancelBeforeTeach=cancels()
  scene.setTeachTarget([100,-200,700,90,0,0],value=>teachUpdates.push(value))
  assert.equal(control.object,undefined);assert.equal(control.enabled,false);assert.equal(cancels(),cancelBeforeTeach+1)
  assert.equal(gizmo.controls.enabled,true)
  control.dispatchEvent({type:'mouseUp'});assert.equal(commits(),beforeTeach)
  assert.equal(scene.setObjectTarget('fixture',callbacks),true)
  assert.equal(gizmo.controls.enabled,false);assert.equal(teachTarget.visible,false);assert.equal(scene.onTeachTarget,null)
  assert.equal(scene.setObjectTarget('missing',callbacks),false);assert.equal(control.object,undefined)
  assert.deepEqual(scene.getView(),objectView);assert.equal(storageWrites,0)
  // Real controller + real scene/TransformControls; only persistence is a local
  // async mock. This verifies the callbacks/acknowledgement contract end to end.
  const editorState={},savedPositions=[]
  const editor=createObjectTeaching({state:editorState,allowed:()=>true,
    save:async(id,patch)=>{savedPositions.push({id,patch});return {ok:true,object:{id,xyz:patch.xyz.slice()}}},
  })
  scene.setObjects([object,other],'fixture')
  assert.equal(editor.attach(scene,'fixture','s'),true)
  assert.equal(editorState.active,true)
  beginMove();assert.equal(editorState.dragging,true)
  moveTo(.21,.43,-.32)
  assert.deepEqual(editorState.xyz,[210,320,430])
  control.pointerUp({button:0})
  assert.equal(editorState.busy,true);assert.equal(control.enabled,false)
  for(let i=0;i<6;i++)await Promise.resolve()
  assert.deepEqual(savedPositions,[{id:'fixture',patch:{xyz:[210,320,430]}}])
  assert.equal(editorState.busy,false);assert.equal(editorState.dragging,false)
  assert.equal(editorState.xyz,null);assert.equal(editorState.error,'')
  assert.equal(control.enabled,true)
  assert.deepEqual(objectMesh().position.toArray(),[.21,.43,-.32])
  control.pointerUp({button:0});assert.equal(savedPositions.length,1)
  editor.detach();assert.equal(editorState.active,false)
  assert.equal(control.enabled,false);assert.equal(control.object,undefined)
  assert.deepEqual(scene.getView(),objectView)
  console.log('PASS: real objectTeaching controller + scene + TransformControls async save, one XYZ write and acknowledgement unlock')
  const assemblySignatures=new Set()
  for(const preset of OBJECT_CATALOG.filter(item=>item.parts)) {
    const spec={id:'fixture',...catalogSpec(preset.id,[300,-300,800])}
    scene.setObjects([spec],'fixture')
    const mesh=objectMesh(),box=new Real.Box3().setFromObject(mesh),center=mesh.position
    const half=preset.kind==='cylinder'?[spec.size[0],spec.size[2]/2,spec.size[0]]:[spec.size[0]/2,spec.size[2]/2,spec.size[1]/2]
    for(let axis=0;axis<3;axis++) {
      assert.ok(box.max.getComponent(axis)<=center.getComponent(axis)+half[axis]/1000+1e-6,`${preset.id}: display must fit conservative proxy`)
      assert.ok(box.min.getComponent(axis)>=center.getComponent(axis)-half[axis]/1000-1e-6,`${preset.id}: display must fit conservative proxy`)
    }
    assert.equal(mesh.geometry.userData.preset,preset.id)
    assert.equal(mesh.geometry.userData.parts,preset.parts.length)
    assemblySignatures.add(JSON.stringify(preset.parts))
    // Exercise the real canvas selection ray through a rendered assembly
    // triangle, rather than assuming that preselecting an ID proves picking.
    scene.setObjectTarget(null);step(40)
    const vertices=mesh.geometry.attributes.position,indices=mesh.geometry.index
    const point=new Real.Vector3()
    for(let i=0;i<3;i++)point.add(new Real.Vector3().fromBufferAttribute(vertices,indices?indices.getX(i):i))
    point.multiplyScalar(1/3).applyMatrix4(mesh.matrixWorld).project(lastCamera)
    const pickEvent={button:0,clientX:(point.x+1)*400,clientY:(1-point.y)*300,preventDefault(){}}
    let picked=null
    scene.onPick=id=>{picked=id}
    orbitDown(pickEvent);events.get('pointerup')(pickEvent)
    assert.equal(picked,'fixture',`${preset.id}: canvas ray selects the real visible assembly`)
    scene.onPick=null
    for(const [surface,properties] of Object.entries(OBJECT_SURFACES)) {
      const geometry=mesh.geometry
      scene.setObjects([{...spec,surface,color:'#123abc'}],'fixture')
      assert.equal(mesh.geometry,geometry,'surface changes do not rebuild geometry')
      assert.equal(mesh.material.roughness,properties.roughness);assert.equal(mesh.material.metalness,properties.metalness)
      assert.equal(mesh.material.color.getHexString(),'123abc')
    }
    scene.markCollisions([{id:'fixture'}]);assert.equal(mesh.material.emissive.getHex(),0xe4553f)
    scene.markCollisions([]);assert.equal(mesh.material.emissive.getHex(),0x2fd6f0)
    assert.equal(editor.attach(scene,'fixture','s'),true)
    beginMove();moveTo(.21,.43,-.32);control.pointerUp({button:0})
    for(let i=0;i<6;i++)await Promise.resolve()
    assert.equal(editorState.busy,false);assert.equal(control.enabled,true)
    editor.detach()
  }
  assert.equal(assemblySignatures.size,13,'industrial presets plus shared zone plate must be thirteen distinct geometry assemblies')
  assert.deepEqual(scene.getView(),objectView)
  console.log('PASS: 12 distinct industrial assemblies, actual canvas picking, conservative envelopes, six surfaces, color/collision highlights and controller drag persistence')
  console.log('PASS: real object TransformControls, XYZ, WS protection, pending lock, once-only commit, rollback, shapes, resize, deletion, TCP exclusion and unchanged view')
  resetMetrics();step(16)
  const observer=longObservers.at(-1)
  observer.deliver([{entryType:'longtask',duration:75,name:'must-not-retain',attribution:[{src:'private-url'}]},{entryType:'paint',duration:99}])
  assert.deepEqual(snapshotMetrics().longtask,{n:1,p50:75,p95:75,max:75})
  assert.ok(!JSON.stringify(snapshotMetrics()).includes('private-url'))
  doc.hidden=true;events.get('visibilitychange')();assert.equal(frames.size,0);assert.equal(observer.connected,false)
  observer.deliver([{entryType:'longtask',duration:9000}]);assert.equal(snapshotMetrics().longtask.n,1)
  const beforeResume=snapshotMetrics()['scene.frame'].n
  clock+=10000
  doc.hidden=false;events.get('visibilitychange')();assert.equal(frames.size,1)
  assert.equal(snapshotMetrics()['scene.frame'].n,beforeResume,'background pause must not become a long frame sample')
  observer.deliver([{entryType:'longtask',duration:9000}]);assert.equal(snapshotMetrics().longtask.n,1,'late old observer callback must be ignored after resume')
  step(16);assert.ok(snapshotMetrics()['scene.frame'].max<50)
  longObservers.at(-1).deliver([{entryType:'longtask',duration:60}]);assert.equal(snapshotMetrics().longtask.n,2)
  scene.setObjects([object],'fixture');scene.setObjectTarget('fixture',{cancel:()=>disposeCancelled++})
  disposeControls=control;beginMove();moveTo(.2,.4,.6)
  const floor=lastScene.getObjectByName('laboratory-floor')
  assert.ok(floor && !floor.material.transparent,'实验室地面必须不透明')
  assert.equal(lastScene.getObjectByName('laboratory-walls'),undefined,'不要围墙')
  scene.setFence({enabled:true,x:[-800,1000],y:[-500,500],z:[0,1500]})
  assert.equal(floor.scale.x,2);assert.equal(floor.scale.y,1.2)
  assert.equal(floor.position.x,.1)
  const observation={id:'V1',shape:'rectangle',rgb:'#ff3333',status:'observed',mapped:{xyz_mm:[100,200,10],size_mm:[40,30,20],yaw_deg:0}}
  scene.setVisionObjects([observation]);step(16)
  const layer=lastScene.getObjectByName('vision-observations'),proxy=layer.children[0]
  assert.equal(layer.children.length,1);assert.equal(proxy.geometry.type,'BoxGeometry')
  assert.equal(proxy.position.y,.01)
  let released=0;proxy.material.addEventListener('dispose',()=>released++)
  scene.setVisionObjects([{...observation,status:'holding'}]);step(16)
  assert.equal(layer.children[0],proxy);assert.equal(proxy.material.opacity,.2)
  scene.setVisionObjects(Array.from({length:80},(_,index)=>({...observation,id:'V'+(index+1)})));step(16)
  assert.equal(layer.children.length,50,'识别代理必须有数量上限')
  assert.equal(new Set(layer.children.map(mesh=>mesh.geometry)).size,1,'代理必须复用几何资源')
  scene.setVisionObjects([{...observation,mapped:{...observation.mapped,xyz_mm:[0,0,-10]}}]);step(16)
  assert.equal(layer.children.length,0);assert.equal(released,1,'移除代理应释放其独占材质')
  console.log('PASS: opaque laboratory floor, bounded/pool-reused observed proxies, ground validation and material cleanup')
  console.log('PASS: live pose/TCP/direction, fence flash, selected collision, skin/tool, view preservation and lifecycle')
} finally {
  const resources=new Set()
  lastScene?.traverse(object=>{
    if(!object.userData.link)return
    if(object.geometry)resources.add(object.geometry)
    for(const material of (Array.isArray(object.material)?object.material:[object.material]).filter(Boolean))resources.add(material)
  })
  const disposed=new Map()
  for(const resource of resources)resource.addEventListener('dispose',()=>disposed.set(resource,(disposed.get(resource)||0)+1))
  scene?.dispose()
  scene?.dispose()
  for(const resource of resources)assert.equal(disposed.get(resource),1,'shared robot/ghost geometries and materials dispose once, including repeated scene disposal')
  for(const restore of originals)restore()
}
assert.equal(frames.size,0)
assert.equal(disposeCancelled,1,'dispose must notify active object move cancellation')
assert.equal(disposeControls.object,undefined);assert.equal(disposeControls.enabled,false)
assert.equal(canvas.listenerCount(),0,'dispose must remove gizmo and orbit DOM listeners')
assert.equal(events.has('blur'),false);assert.equal(events.has('pointercancel'),false)
assert.ok(longObservers.every(observer=>!observer.connected),'dispose must disconnect all long-task observers')
const countAfterDispose=snapshotMetrics().longtask.n
longObservers.at(-1).deliver([{entryType:'longtask',duration:1234}]);assert.equal(snapshotMetrics().longtask.n,countAfterDispose)
