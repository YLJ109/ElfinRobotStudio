import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { readFileSync } from 'node:fs'
import { createVirtualCamera, defaultCameraMount, DEFAULT_CAMERA_MOUNT, DEFAULT_CAMERA_TOOL_ROTATION, CAMERA_MOUNT_VERSION } from '../src/virtualCamera.js'
import { buildVacuumReference, disposeAsset } from '../src/toolVisual.js'
import { DEFAULT_VACUUM } from '../src/visualAssets.js'
import { createVirtualCapture, localVirtualPreview } from '../src/visionWorkspace.js'
function fixture(t){
  const scene=new THREE.Scene(),flange=new THREE.Group(),overlay=new THREE.Group();scene.add(flange,overlay)
  let image=null,target=null,allocations=new Set(),disposedTargets=0,renders=0
  const original=globalThis.document
  globalThis.document={createElement:()=>({width:0,height:0,getContext:()=>({createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData:value=>{image=value}}),toDataURL:()=> 'data:image/jpeg;base64,dGVzdA=='})}
  t.after(()=>{globalThis.document=original;camera.dispose()})
  const renderer={getRenderTarget:()=>target,setRenderTarget:value=>{target=value;if(value && !allocations.has(value)){allocations.add(value);value.addEventListener('dispose',()=>disposedTargets++)}},
    clear(){},render(){renders++;assert.equal(overlay.visible,false)},async readRenderTargetPixelsAsync(rt,x,y,w,h,pixels){pixels.fill(20);pixels.fill(200,0,w*4)}}
  const camera=createVirtualCamera({renderer,scene,flange,hidden:()=>[overlay]})
  return{camera,flange,overlay,renderer,get image(){return image},get allocations(){return allocations.size},get disposedTargets(){return disposedTargets},get renders(){return renders}}
}

test('arbitrary simulation viewpoints never stretch or move the fixed physical camera mount',t=>{
  const f=fixture(t),body=f.flange.getObjectByName('end-effector-camera'),bracket=body.getObjectByName('webcam-flange-bracket')
  const [contact,connector,saddle]=bracket.children,anchor=contact.position.clone(),geometries=bracket.children.map(mesh=>mesh.geometry)
  const housing=body.getObjectByName('webcam-housing-frame'),physicalPosition=housing.position.clone(),physicalOrientation=housing.quaternion.clone(),mountLength=connector.scale.x
  const configs=[
    {offset_mm:[450,-350,-725],direction:[0,0,-1000]},
    {offset_mm:[-350,50,5],direction:[-.00439,0,1000]},
    {offset_mm:[1000,-1000,1000],direction:[400,0,-1000]},
    {offset_mm:[-1000,1000,-1000],direction:[0,-1,0]},
    {offset_mm:[0,0,0],direction:[1,0,0]},
  ]
  for(const config of configs){
    f.camera.configure({enabled:true,...config});f.flange.updateMatrixWorld(true)
    assert.ok(contact.position.distanceTo(anchor)<1e-12,'flange contact must not follow camera Y/Z offsets')
    assert.ok(housing.position.distanceTo(physicalPosition)<1e-12)
    assert.ok(housing.quaternion.angleTo(physicalOrientation)<1e-10)
    assert.ok(Math.abs(connector.scale.x-mountLength)<1e-12 && connector.scale.x<.005,'virtual views cannot invent long physical brackets')
    const housingSeat=new THREE.Vector3(0,-.0125,.007).applyQuaternion(housing.quaternion).add(housing.position)
    assert.ok(saddle.position.distanceTo(housingSeat)<1e-10,'housing contact must follow the same rigid pose as its shell')
    const endpoints=[-.5,.5].map(x=>new THREE.Vector3(x,0,0).multiply(connector.scale).applyQuaternion(connector.quaternion).add(connector.position))
    assert.ok(Math.min(endpoints[0].distanceTo(contact.position)+endpoints[1].distanceTo(saddle.position),endpoints[1].distanceTo(contact.position)+endpoints[0].distanceTo(saddle.position))<1e-9,'connector endpoints must touch both actual mount contacts')
    assert.ok(body.visible);assert.equal(f.flange.children.filter(node=>node.name==='end-effector-camera').length,1)
    assert.deepEqual(bracket.children.map(mesh=>mesh.geometry),geometries,'pose changes must reuse existing resources')
    const pose=new THREE.Matrix4().fromArray(f.camera.pose()),position=new THREE.Vector3().setFromMatrixPosition(pose)
    assert.ok(position.distanceTo(new THREE.Vector3(...config.offset_mm).multiplyScalar(.001))<1e-10,'geometry fix cannot change capture pose')
    assert.ok(new THREE.Vector3(0,0,-1).transformDirection(pose).distanceTo(new THREE.Vector3(...config.direction).normalize())<1e-10,'custom view orientation must remain exact')
  }
  f.camera.configure({enabled:false,...configs[0]});assert.ok(body.visible,'never hide an unsupported-looking assembly to pass screenshots')
})
test('virtual eye at the physical contact cannot collapse or alter the installed mount',t=>{
  const f=fixture(t),body=f.flange.getObjectByName('end-effector-camera'),bracket=body.getObjectByName('webcam-flange-bracket')
  const [contact,connector,saddle]=bracket.children
  const before={length:connector.scale.x,saddle:saddle.position.clone(),position:connector.position.clone()}
  // At the default look direction this shell-local seat maps to flange +X/-Z.
  f.camera.configure({enabled:true,offset_mm:[(contact.position.x-.0125)*1000,0,(contact.position.z+.007)*1000],direction:[0,0,1000]})
  assert.ok(saddle.position.distanceTo(before.saddle)<1e-10)
  assert.ok(connector.position.distanceTo(before.position)<1e-10)
  assert.ok(connector.position.toArray().concat(connector.quaternion.toArray(),connector.scale.toArray()).every(Number.isFinite))
  assert.equal(connector.scale.x,before.length);assert.ok(body.visible);assert.equal(bracket.children.length,3)
})
test('相机关闭只停止采集，头部硬件模型仍显示并随法兰变换',async t=>{
  const f=fixture(t),before=f.camera.pose()
  assert.equal(f.flange.children.length,2)
  f.camera.configure({enabled:true,offset_mm:[100,0,50],direction:[0,0,-1],fov:55,width:320})
  const pose=f.camera.pose();assert.ok(Math.abs(pose[12]-.1)<1e-9);assert.ok(Math.abs(pose[14]-.05)<1e-9)
  f.flange.position.x=1;assert.ok(Math.abs(f.camera.pose()[12]-1.1)<1e-9)
  f.camera.configure({enabled:false});assert.equal(f.flange.children.length,2);assert.notDeepEqual(before,pose)
  assert.equal(f.flange.getObjectByName('end-effector-camera').visible,true)
  assert.equal(await f.camera.capture(),null)
  assert.equal(f.renders,0,'closed camera must not render a capture frame')
})

test('实拍深度相机外观：银灰壳、黑面板、小镜头和顶部槽，资源只释放一次',t=>{
  const f=fixture(t);f.camera.configure({enabled:true})
  const pose=f.camera.pose(),body=f.flange.getObjectByName('end-effector-camera')
  assert.deepEqual(pose.slice(12,15),DEFAULT_CAMERA_MOUNT.offset_mm.map(value=>value/1000))
  assert.ok(new THREE.Vector3(0,0,-1).applyMatrix4(new THREE.Matrix4().fromArray(pose).setPosition(0,0,0)).distanceTo(new THREE.Vector3(0,0,1))<1e-9)
  assert.equal(DEFAULT_CAMERA_MOUNT.mount_version,CAMERA_MOUNT_VERSION)
  assert.equal(body.getObjectByName('webcam-rounded-housing').geometry.type,'ExtrudeGeometry')
  assert.equal(body.getObjectByName('depth-camera-roof-vents').count,24)
  assert.equal(body.getObjectByName('webcam-rounded-housing').material.color.getHexString(),'bfc5c5')
  assert.equal(body.getObjectByName('depth-camera-black-sensor-panel').material.color.getHexString(),'060b0e')
  assert.equal(body.getObjectByName('center-camera-window').material.color.getHexString(),'28544f')
  assert.ok(body.getObjectByName('left-sensor-window'))
  assert.ok(body.getObjectByName('right-sensor-window'))
  assert.ok(body.getObjectByName('projector-window'))
  assert.equal(body.userData.referenceOnly,true)
  const optics=body.getObjectByName('webcam-optics-frame')
  assert.ok(new THREE.Box3().setFromObject(optics).getSize(new THREE.Vector3()).z<.004,'no protruding big lens')
  let releases=0;body.getObjectByName('webcam-rounded-housing').geometry.addEventListener('dispose',()=>releases++)
  f.camera.dispose();f.camera.dispose();assert.equal(releases,1)
})

const robot=JSON.parse(readFileSync(new URL('../public/models/elfin5/robot.json',import.meta.url),'utf8'))
function meshBoxInFlange(mesh,flange){
  mesh.updateWorldMatrix(true,false);flange.updateWorldMatrix(true,false)
  const transform=new THREE.Matrix4().copy(flange.matrixWorld).invert().multiply(mesh.matrixWorld)
  if(mesh.isInstancedMesh){mesh.computeBoundingBox();return mesh.boundingBox.clone().applyMatrix4(transform)}
  if(!mesh.geometry.boundingBox)mesh.geometry.computeBoundingBox()
  return mesh.geometry.boundingBox.clone().applyMatrix4(transform)
}
function strictOverlap(a,b){return ['x','y','z'].every(axis=>Math.min(a.max[axis],b.max[axis])-Math.max(a.min[axis],b.min[axis])>1e-7)}

test('相机安装在40mm连接段中间，J6转动时不穿腕、连接件、板或吸盘',t=>{
  const backend=readFileSync(new URL('../../backend/core/sim_tool.py',import.meta.url),'utf8')
  const rotation=JSON.parse(backend.match(/DEFAULT_TOOL\s*=\s*\{[^\n]*"rotation":\s*(\[[^\]]+\])/)[1])
  assert.deepEqual(rotation,DEFAULT_CAMERA_TOOL_ROTATION,'camera installation must track the actual default tool rotation')
  const f=fixture(t),tool=buildVacuumReference(DEFAULT_VACUUM),mount=defaultCameraMount(robot,DEFAULT_VACUUM,rotation)
  tool.rotation.set(...rotation.map(THREE.MathUtils.degToRad),'ZYX')
  f.flange.add(tool);t.after(()=>disposeAsset(tool))
  const toolSize=new THREE.Box3().setFromObject(tool).getSize(new THREE.Vector3())
  assert.ok(Math.abs(toolSize.y-DEFAULT_VACUUM.length/1000)<1e-7,'rotated plate long axis must be flange Y')
  assert.ok(Math.abs(toolSize.x-DEFAULT_VACUUM.width/1000)<1e-7,'rotated plate narrow side must be flange X')
  const before=new THREE.Box3().setFromObject(f.flange)
  f.camera.configure({enabled:true})
  f.flange.updateMatrixWorld(true)
  const body=f.flange.getObjectByName('end-effector-camera'),cameraMeshes=[],toolBoxes=[]
  body.traverse(mesh=>{if(mesh.isMesh)cameraMeshes.push(mesh)})
  tool.traverse(mesh=>{if(mesh.isMesh)toolBoxes.push(meshBoxInFlange(mesh,f.flange))})
  // robot.json's link6 bounds are conservatively transformed through the end joint.
  for(const degrees of [-180,-90,-30,0,45,90,180]){
    f.flange.rotation.z=THREE.MathUtils.degToRad(degrees)
    for(const mesh of cameraMeshes){
      const bounds=meshBoxInFlange(mesh,f.flange)
      assert.ok(!strictOverlap(bounds,mount.wrist),'camera / wrist overlap: '+mesh.name)
      for(const toolBox of toolBoxes)assert.ok(!strictOverlap(bounds,toolBox),'camera / tool overlap: '+mesh.name)
    }
  }
  f.flange.rotation.z=0;f.flange.updateMatrixWorld(true)
  const housing=f.flange.getObjectByName('webcam-housing-frame')
  const housingBox=new THREE.Box3().setFromObject(housing)
  assert.deepEqual(mount.offset_mm,DEFAULT_CAMERA_MOUNT.offset_mm,'fallback must match loaded model geometry')
  assert.equal(mount.offset_mm[1],0,'webcam must stay centered on J6 instead of sliding along plate')
  const gap=mount.side_mm/1000-housingBox.max.x
  assert.ok(gap>=.002 && gap<.005,`compact shell must hug connector, got ${gap*1000}mm`)
  assert.equal(mount.offset_mm[2],31,'40mm connector midpoint plus requested cumulative 11mm forward adjustment')
  const bracket=f.flange.getObjectByName('webcam-flange-bracket')
  const clampBox=new THREE.Box3().setFromObject(bracket)
  const plate=toolBoxes.find(box=>Math.abs(box.getSize(new THREE.Vector3()).y-DEFAULT_VACUUM.length/1000)<1e-7)
  assert.ok(Math.abs(clampBox.max.x-mount.side_mm/1000)<1e-7,'clamp must contact connector side')
  assert.ok(Math.abs((clampBox.min.y+clampBox.max.y)/2)<1e-7,'clamp must stay centered on J6')
  const cameraBox=new THREE.Box3().setFromObject(body)
  assert.ok(cameraBox.max.x<=mount.side_mm/1000+1e-7,'camera must stay outside connector')
  assert.ok(cameraBox.getSize(new THREE.Vector3()).y<.1,'compact housing must stay below 100mm wide')
  assert.ok(cameraBox.getSize(new THREE.Vector3()).z<.03,'no long lens or hanging camera')
  // Changing the reference dimensions changes the derived installation, not a magic 100mm offset.
  assert.equal(defaultCameraMount(robot,{...DEFAULT_VACUUM,mountHeight:60}).offset_mm[2],41)
  assert.ok(defaultCameraMount(robot,{...DEFAULT_VACUUM,mountHeight:0}).offset_mm[2]<0,'legacy absent connector uses wrist fallback')
  f.camera.configure({enabled:false})
  const after=new THREE.Box3().setFromObject(f.flange)
  assert.ok(before.min.distanceTo(after.min)<1e-12 && before.max.distanceTo(after.max)<1e-12,'disabled camera must not change fit bounds')
  let foreignReleases=0;tool.traverse(mesh=>mesh.geometry?.addEventListener('dispose',()=>foreignReleases++))
  f.camera.dispose();assert.equal(foreignReleases,0,'camera does not own the shared tool geometry')
})

test('J6多角度完整继承：机身沿板长轴横放、镜头与外壳始终一体、夹座固定在法兰层',t=>{
  const f=fixture(t),parent=new THREE.Group();parent.rotation.set(.4,-.7,.2);parent.position.set(.5,.3,-.2)
  f.flange.removeFromParent();parent.add(f.flange);parent.updateMatrixWorld(true)
  const offset=new THREE.Vector3(...DEFAULT_CAMERA_MOUNT.offset_mm).multiplyScalar(.001)
  const points=[]
  for(const degrees of [-180,-135,-90,-30,0,45,90,135,180]){
    f.flange.rotation.set(0,0,THREE.MathUtils.degToRad(degrees));f.camera.configure({enabled:true})
    const pose=new THREE.Matrix4().fromArray(f.camera.pose())
    const housing=f.flange.getObjectByName('webcam-housing-frame'),bracket=f.flange.getObjectByName('webcam-flange-bracket')
    const quaternion=housing.getWorldQuaternion(new THREE.Quaternion())
    const longAxis=new THREE.Vector3(1,0,0).applyQuaternion(quaternion)
    const plateAxis=new THREE.Vector3(0,1,0).transformDirection(f.flange.matrixWorld)
    assert.ok(Math.abs(longAxis.dot(plateAxis))>1-1e-10)
    const position=new THREE.Vector3().setFromMatrixPosition(pose)
    assert.ok(position.distanceTo(offset.clone().applyMatrix4(f.flange.matrixWorld))<1e-10)
    const direction=new THREE.Vector3(0,0,-1).transformDirection(pose)
    assert.ok(direction.distanceTo(new THREE.Vector3(0,0,1).transformDirection(f.flange.matrixWorld))<1e-10)
    assert.ok(housing.quaternion.equals(f.flange.getObjectByName('webcam-optics-frame').quaternion))
    assert.ok(bracket.quaternion.angleTo(new THREE.Quaternion())<1e-10)
    points.push(position)
  }
  assert.ok(points[4].distanceTo(points[6])>.05,'camera must orbit J6, not remain at a fixed world offset')
  const housing=f.flange.getObjectByName('webcam-housing-frame'),bracket=f.flange.getObjectByName('webcam-flange-bracket')
  const fixedHousing=housing.quaternion.clone(),fixedBracket=bracket.quaternion.clone()
  f.camera.configure({enabled:true,direction:[1000,0,200]})
  assert.ok(housing.quaternion.angleTo(fixedHousing)<1e-10,'view aiming cannot rotate the physical shell')
  const aimedPose=new THREE.Matrix4().fromArray(f.camera.pose())
  assert.ok(new THREE.Vector3(0,0,-1).transformDirection(aimedPose).distanceTo(new THREE.Vector3(1000,0,200).normalize().transformDirection(f.flange.matrixWorld))<1e-10)
  assert.ok(bracket.quaternion.angleTo(fixedBracket)<1e-10)
  const optics=f.flange.getObjectByName('webcam-optics-frame')
  assert.ok(optics.quaternion.equals(housing.quaternion))
  assert.ok(optics.position.distanceTo(housing.position)<1e-12)
})

test('显式应用历史同值安装不触发迁移且工具更新仍保留用户参数',t=>{
  const f=fixture(t)
  f.camera.configure({enabled:false,offset_mm:[100,0,50],direction:[400,0,-1000]})
  assert.deepEqual(f.camera.pose().slice(12,15),[.1,0,.05])
  const pose=f.camera.pose()
  f.camera.setToolInstallation({...DEFAULT_VACUUM,mountHeight:60})
  assert.deepEqual(f.camera.pose(),pose)
  const housing=f.flange.getObjectByName('webcam-housing-frame'),optics=f.flange.getObjectByName('webcam-optics-frame')
  assert.ok(Math.abs(housing.position.z-.041)<1e-10,'physical installation follows the tool even when capture uses a custom view')
  assert.ok(housing.position.distanceTo(optics.position)<1e-12)
  assert.ok(housing.position.distanceTo(new THREE.Vector3(.1,0,.05))>.05,'custom eye is not a physical mounting instruction')
  const direction=new THREE.Vector3(0,0,-1).applyMatrix4(new THREE.Matrix4().fromArray(pose)).sub(new THREE.Vector3(.1,0,.05)).normalize()
  assert.ok(direction.dot(new THREE.Vector3(400,0,-1000).normalize())>.999999)
})

test('切换工具同步安装参考，旧工具回退腕部且自定义相机参数不被覆盖',t=>{
  const f=fixture(t)
  f.camera.configure({enabled:false,offset_mm:[-36,0,31],direction:[0,0,1000]})
  f.camera.setToolInstallation({...DEFAULT_VACUUM,mountHeight:60})
  assert.ok(Math.abs(f.camera.pose()[14]-.041)<1e-10)
  f.camera.setToolInstallation({...DEFAULT_VACUUM,mountHeight:0})
  assert.ok(f.camera.pose()[14]<0)
  f.camera.configure({enabled:false,offset_mm:[1,2,3],direction:[0,0,1000]})
  f.camera.setToolInstallation(DEFAULT_VACUUM)
  assert.deepEqual(f.camera.pose().slice(12,15),[.001,.002,.003])
})
test('离屏画面翻转正确、还原遮挡图层，重复取帧不重建渲染目标',async t=>{
  const f=fixture(t);f.camera.configure({enabled:true,width:320})
  await f.camera.capture();assert.equal(f.image.data[0],20);assert.equal(f.image.data.at(-1),200)
  assert.equal(f.overlay.visible,true);assert.equal(f.renderer.getRenderTarget(),null)
  await f.camera.capture();assert.equal(f.allocations,1);assert.equal(f.renders,2)
  f.camera.configure({enabled:true,width:640});assert.equal(f.disposedTargets,1)
  await f.camera.capture();assert.equal(f.allocations,2)
})
test('GPU读回失败仍恢复状态并允许下次取帧',async t=>{
  const f=fixture(t);f.camera.configure({enabled:true,width:320})
  f.renderer.readRenderTargetPixelsAsync=async()=>{throw new Error('GPU读回失败')}
  await assert.rejects(f.camera.capture(),/GPU/);assert.equal(f.overlay.visible,true);assert.equal(f.renderer.getRenderTarget(),null)
  await assert.rejects(f.camera.capture(),/GPU/)
})
test('仿真采集单在途，停止/模式改变后不再提交帧',async()=>{
  let resolve,requests=0,cancelled=false,configured=[]
  const pending=new Promise(yes=>{resolve=yes}),timers=[]
  const scene={configureVirtualCamera:value=>configured.push(value),captureVirtualFrame:()=>pending}
  const state={config:{input:'virtual',enabled:true,virtual:{fps:15}},virtual_session:'s'}
  localVirtualPreview(state).visible=true
  const dispose=createVirtualCapture({state,getScene:()=>scene,allowed:()=>true,request:async()=>{requests++},schedule:fn=>{timers.push(fn);return 1},cancel:()=>{cancelled=true}})
  dispose();resolve({image_base64:'x',view_key:'v',captured_at:1})
  await new Promise(yes=>setImmediate(yes));assert.equal(requests,0);assert.equal(timers.length,0);assert.equal(cancelled,true);assert.equal(configured.at(-1).enabled,false)
})
