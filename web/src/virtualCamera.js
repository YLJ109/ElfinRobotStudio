import * as THREE from 'three'
import { DEFAULT_VACUUM } from './visualAssets.js'
import { HEAD_CAMERA_MOUNT,CAMERA_FORWARD_TRIM_MM } from './virtualCameraMount.js'

// Millimetres in the URDF end-flange frame, before the scene's Z-up/Y-up conversion.
// The default tool is rotated 90deg about flange Z: its 300mm long axis is Y.
export const DEFAULT_CAMERA_TOOL_ROTATION=Object.freeze([0,0,90])
export const CAMERA_MOUNT_VERSION='photo-depth-camera-v8'
// Fallback for callers without model metadata. The scene supplies its already
// loaded robot spec; no duplicate model download or public-directory JS import.
export const DEFAULT_CAMERA_MOUNT=Object.freeze({...HEAD_CAMERA_MOUNT,side_mm:-20,mount_version:CAMERA_MOUNT_VERSION})
// Cache the row views once per allocation, rather than allocating height views
// on every frame. WebGL origin is bottom-left; canvas origin is top-left.
export function createVirtualPixelCopy(pixels,image,width,height){
  const stride=width*4,rows=Array.from({length:height},(_,y)=>pixels.subarray((height-y-1)*stride,(height-y)*stride))
  return ()=>{for(let y=0;y<height;y++)image.data.set(rows[y],y*stride)}
}
async function encodeVirtualCanvas(canvas){
  if(typeof canvas.toBlob==='function' && typeof globalThis.FileReader==='function'){
    const blob=await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('仿真相机 JPEG 编码失败')),'image/jpeg',.82))
    return new Promise((resolve,reject)=>{
      const reader=new FileReader()
      reader.onload=()=>resolve(String(reader.result).split(',')[1])
      reader.onerror=reader.onabort=()=>reject(reader.error || new Error('仿真相机 JPEG 读取失败'))
      reader.readAsDataURL(blob)
    })
  }
  // Compatibility for older canvas implementations; metrics identify this path.
  return canvas.toDataURL('image/jpeg',.82).split(',')[1]
}
export function defaultCameraMount(spec,tool=DEFAULT_VACUUM,rotation=DEFAULT_CAMERA_TOOL_ROTATION) {
  if(!spec){
    const side=tool.mountHeight>=30?-Math.min(20,tool.width*.4,tool.length*.2):-42.506156110930136
    return {...DEFAULT_CAMERA_MOUNT,side_mm:side,
      offset_mm:[Math.floor((side-16)*10)/10,0,tool.mountHeight>=30?tool.mountHeight/2+CAMERA_FORWARD_TRIM_MM:-6]}
  }
  const end=spec.joints.find(joint=>joint.child==='elfin_end_link')
  const visual=spec.links[end.parent].visual
  const transform=(xyz,rpy)=>new THREE.Matrix4().compose(new THREE.Vector3(...xyz),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...rpy,'ZYX')),new THREE.Vector3(1,1,1))
  const toFlange=transform(end.xyz,end.rpy).invert().multiply(transform(visual.origin_xyz,visual.origin_rpy))
  const wrist=new THREE.Box3(new THREE.Vector3(...visual.bbox.min),new THREE.Vector3(...visual.bbox.max)).applyMatrix4(toFlange)
  const toolRotation=new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...rotation.map(THREE.MathUtils.degToRad),'ZYX'))
  const toolBounds=new THREE.Box3(new THREE.Vector3(-tool.length/2000,-tool.width/2000,tool.mountHeight/1000),
    new THREE.Vector3(tool.length/2000,tool.width/2000,(tool.mountHeight+tool.thickness+tool.cupHeight)/1000)).applyMatrix4(toolRotation)
  // The 40mm lower connector owns this mount, not a duplicated upper wrist.
  // For a legacy short/absent connector keep the camera clear of the wrist
  // until the user explicitly applies the new tool through the common workflow.
  const hasConnector=tool.mountHeight>=30
  const side=hasConnector?-Math.min(20,tool.width*.4,tool.length*.2):wrist.min.x*1000
  const x=Math.floor((side-14-2)*10)/10
  return {offset_mm:[x,0,hasConnector?tool.mountHeight/2+CAMERA_FORWARD_TRIM_MM:-6],direction:[0,0,1000],wrist,toolBounds,
    side_mm:side,clearance_mm:2,mount_version:CAMERA_MOUNT_VERSION}
}
// 仅GPU渲染场景画面；不会读取摄像头、修改关节或调用机器人接口。
export function createVirtualCamera({renderer,scene,flange,spec,hidden=()=>[],
  now=()=>performance.now(),createCanvas=()=>document.createElement('canvas'),encodeCanvas=encodeVirtualCanvas}) {
  let mount=defaultCameraMount(spec),lastConfig={enabled:false}
  const eye=new THREE.PerspectiveCamera(55,4/3,.015,20)
  const body=new THREE.Group();body.name='end-effector-camera'
  const housingRig=new THREE.Group();housingRig.name='webcam-housing-frame';body.add(housingRig)
  const opticsRig=new THREE.Group();opticsRig.name='webcam-optics-frame';body.add(opticsRig)
  const bracket=new THREE.Group();bracket.name='webcam-flange-bracket';body.add(bracket)
  // Photo-derived appearance only, not manufacturer CAD or calibrated stereo
  // intrinsics: silver rounded body, recessed black sensor strip, small optics
  // and roof vents. The capture remains a single virtual pinhole camera.
  body.userData.referenceOnly=true
  body.userData.provenance='user-photo: silver multi-window depth camera; dimensions approximate'
  const ownedGeometries=new Set(),ownedMaterials=new Set()
  const material=options=>{const value=new THREE.MeshStandardMaterial(options);ownedMaterials.add(value);return value}
  const shell=material({color:'#bfc5c5',metalness:.72,roughness:.3})
  const trim=material({color:'#626d70',metalness:.75,roughness:.28})
  const dark=material({color:'#060b0e',metalness:.15,roughness:.38})
  const glass=material({color:'#173949',metalness:.8,roughness:.13})
  const rgbGlass=material({color:'#28544f',metalness:.55,roughness:.17})
  function mesh(geometry,surface,position=[0,0,0],parent=housingRig){
    ownedGeometries.add(geometry);const value=new THREE.Mesh(geometry,surface);value.position.set(...position);parent.add(value);return value
  }
  function roundedOutline(width,height,radius){
    const shape=new THREE.Shape(),x=width/2,y=height/2
    shape.moveTo(-x+radius,-y);shape.lineTo(x-radius,-y)
    shape.quadraticCurveTo(x,-y,x,-y+radius);shape.lineTo(x,y-radius)
    shape.quadraticCurveTo(x,y,x-radius,y);shape.lineTo(-x+radius,y)
    shape.quadraticCurveTo(-x,y,-x,y-radius);shape.lineTo(-x,-y+radius)
    shape.quadraticCurveTo(-x,-y,-x+radius,-y)
    return shape
  }
  const outline=roundedOutline(.09,.024,.007)
  const housing=mesh(new THREE.ExtrudeGeometry(outline,{depth:.016,steps:1,bevelEnabled:true,bevelThickness:.0007,bevelSize:.0007,bevelSegments:2,curveSegments:12}),shell,[0,0,.002])
  housing.name='webcam-rounded-housing'
  const face=mesh(new THREE.ExtrudeGeometry(roundedOutline(.082,.017,.006),
    {depth:.0008,steps:1,bevelEnabled:false,curveSegments:12}),dark,[0,-.0015,.0005],opticsRig)
  face.name='depth-camera-black-sensor-panel'
  // Lens windows are flush with the strip, never a large protruding webcam lens.
  function disc(name,radius,depth,xyz,surface){
    const value=mesh(new THREE.CylinderGeometry(radius,radius,depth,28),surface,xyz,opticsRig)
    value.name=name;value.rotation.x=Math.PI/2;return value
  }
  for(const [name,x,radius] of [['left-sensor',-.031,.0043],['center-camera',-.009,.0038],['right-sensor',.031,.0043]]){
    disc(name+'-bezel',radius+.0007,.0007,[x,-.0015,.0001],trim)
    disc(name+'-window',radius,.0007,[x,-.0015,-.0004],name==='center-camera'?rgbGlass:glass)
    disc(name+'-aperture',radius*.42,.0003,[x,-.0015,-.0009],dark)
    const ring=mesh(new THREE.TorusGeometry(radius*.75,.00018,5,24),trim,[x,-.0015,-.0011],opticsRig)
    ring.name=name+'-lens-ring'
  }
  disc('projector-window',.0035,.0007,[.015,-.0015,-.0004],glass)
  disc('small-status-indicator',.00065,.0005,[.003,-.0015,-.0008],rgbGlass)
  // One small instanced strip for the roof slots; no 112-dot speaker grille.
  const holesGeometry=new THREE.BoxGeometry(.0012,.00045,.006);ownedGeometries.add(holesGeometry)
  const holes=new THREE.InstancedMesh(holesGeometry,dark,24),transform=new THREE.Matrix4()
  let index=0
  for(const side of [-1,1])for(let column=0;column<12;column++){
    transform.makeTranslation(side*(.008+column*.0025),.0121,.009);holes.setMatrixAt(index++,transform)
  }
  holes.name='depth-camera-roof-vents';housingRig.add(holes)
  const port=mesh(new THREE.ExtrudeGeometry(roundedOutline(.009,.004,.0012),
    {depth:.0005,steps:1,bevelEnabled:false}),dark,[0,.012,.009])
  port.rotation.x=Math.PI/2;port.name='depth-camera-top-port'
  const contact=mesh(new THREE.BoxGeometry(.001,.032,.012),shell,[0,0,0],bracket)
  const spacer=mesh(new THREE.BoxGeometry(1,.028,.01),trim,[0,0,0],bracket)
  const saddle=mesh(new THREE.BoxGeometry(.001,.034,.012),dark,[0,0,0],bracket)
  contact.name='webcam-flange-contact';spacer.name='webcam-mount-connector';saddle.name='webcam-housing-contact'
  const connectorAxis=new THREE.Vector3(1,0,0),connectorDirection=new THREE.Vector3()
  const housingSeat=new THREE.Vector3(0,-.0125,.007)
  const seatOrientation=new THREE.Quaternion().setFromUnitVectors(connectorAxis,new THREE.Vector3(0,-1,0))
  function viewOrientation(position,direction){
    const look=position.clone().add(new THREE.Vector3(...direction)),up=new THREE.Vector3(-1,0,0)
    if(Math.abs(new THREE.Vector3(...direction).normalize().dot(up))>.99)up.set(0,1,0)
    return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(position,look,up))
  }
  flange.add(eye);flange.add(body);body.visible=true
  let target=null,pixels=null,canvas=null,context=null,imageData=null,copyPixels=null,width=640,height=480,disposed=false,pending=false,encoding=false
  let enabled=false,epoch=0,configKey='',serial=0
  const measurements={completed:0,encoded:0,skipped:0,failed:0,last:null,encode_ms:null}
  function release(){target?.dispose();target=null;pixels=null;canvas=null;context=null;imageData=null;copyPixels=null}
  function configure(config) {
    lastConfig=config || {enabled:false}
    config=config || {enabled:false}
    enabled=!!config?.enabled;body.visible=true
    if(body.parent!==flange)flange.add(body)
    const isPublishedDefault=JSON.stringify(config.offset_mm)===JSON.stringify(HEAD_CAMERA_MOUNT.offset_mm) &&
      JSON.stringify(config.direction)===JSON.stringify(HEAD_CAMERA_MOUNT.direction)
    const offset=isPublishedDefault?mount.offset_mm:(config.offset_mm || mount.offset_mm),direction=config.direction || mount.direction
    if(![...offset,...direction,config.fov ?? 55].every(Number.isFinite) || Math.hypot(...direction)<1e-6)throw new Error('仿真相机安装参数无效')
    eye.position.set(...offset.map(value=>value/1000))
    eye.quaternion.copy(viewOrientation(eye.position,direction))
    // The physical camera stays on the tool. A configurable simulation view
    // changes only the capture eye, never invents an extending physical mount.
    housingRig.position.set(...mount.offset_mm.map(value=>value/1000))
    housingRig.quaternion.copy(viewOrientation(housingRig.position,mount.direction))
    opticsRig.position.copy(housingRig.position);opticsRig.quaternion.copy(housingRig.quaternion)
    contact.position.set(mount.side_mm/1000-.0005,0,mount.offset_mm[2]/1000-.007)
    saddle.position.copy(housingSeat).applyQuaternion(housingRig.quaternion).add(housingRig.position)
    saddle.quaternion.copy(housingRig.quaternion).multiply(seatOrientation)
    connectorDirection.subVectors(saddle.position,contact.position)
    const connectorLength=connectorDirection.length()
    spacer.position.copy(contact.position).add(saddle.position).multiplyScalar(.5)
    spacer.scale.x=Math.max(.0001,connectorLength)
    if(connectorLength>1e-10)spacer.quaternion.setFromUnitVectors(connectorAxis,connectorDirection.multiplyScalar(1/connectorLength))
    else spacer.quaternion.identity()
    eye.fov=Math.max(20,Math.min(100,config.fov || 55));eye.updateProjectionMatrix()
    const requested=[320,640,960].includes(config.width)?config.width:640
    const key=JSON.stringify([enabled,requested,offset,direction,eye.fov])
    if(key!==configKey){epoch++;configKey=key}
    if(requested!==width && target && !pending)release()
    width=requested;height=Math.round(width*.75)
  }
  async function capture({encode=true}={}) {
    if(disposed || pending || !enabled){measurements.skipped++;return null}
    pending=true
    const token=epoch,frameWidth=width,frameHeight=height,started=now(),frameSerial=++serial
    const valid=()=>!disposed && enabled && token===epoch
    const timing={render_submit_ms:0,readback_wait_ms:0,copy_ms:0,total_ms:0,readback:'async-pbo'}
    try{
      if(target && (target.width!==frameWidth || target.height!==frameHeight))release()
      if(!target){target=new THREE.WebGLRenderTarget(frameWidth,frameHeight,{depthBuffer:true});target.texture.colorSpace=THREE.SRGBColorSpace;pixels=new Uint8Array(frameWidth*frameHeight*4)}
      if(!canvas){canvas=createCanvas();context=canvas.getContext('2d',{alpha:false})}
      if(!context)throw new Error('仿真相机图像编码不可用')
      if(canvas.width!==frameWidth || canvas.height!==frameHeight){canvas.width=frameWidth;canvas.height=frameHeight;imageData=null;copyPixels=null}
      flange.updateWorldMatrix(true,true);eye.updateMatrixWorld(true)
      const viewKey=eye.matrixWorld.elements.map(value=>value.toFixed(5)).join(','),capturedAt=Date.now()/1000
      const previousTarget=renderer.getRenderTarget(),face=renderer.getActiveCubeFace?.() || 0,mip=renderer.getActiveMipmapLevel?.() || 0
      const visibility=[...new Set(hidden().concat(body).filter(Boolean))].map(object=>[object,object.visible])
      let stage=now()
      try{
        for(const [object] of visibility)object.visible=false
        renderer.setRenderTarget(target);renderer.clear();renderer.render(scene,eye)
      }finally{try{renderer.setRenderTarget(previousTarget,face,mip)}finally{for(const [object,visible]of visibility)object.visible=visible}}
      timing.render_submit_ms=now()-stage;stage=now()
      // Three's async PBO/fence path still includes CPU getBufferSubData time;
      // this is wall time, never presented as a GPU timer measurement.
      await renderer.readRenderTargetPixelsAsync(target,0,0,frameWidth,frameHeight,pixels)
      timing.readback_wait_ms=now()-stage
      if(!valid())return null
      stage=now()
      if(!imageData){imageData=context.createImageData(frameWidth,frameHeight);copyPixels=createVirtualPixelCopy(pixels,imageData,frameWidth,frameHeight)}
      copyPixels();context.putImageData(imageData,0,0)
      timing.copy_ms=now()-stage
      const metadata={view_key:viewKey,captured_at:capturedAt,width:frameWidth,height:frameHeight,kind:'virtual_end_effector'}
      const frameCanvas=canvas
      async function encodeFrame(){
        if(!valid() || frameSerial!==serial || encoding)return null
        encoding=true;const start=now()
        try{
          // toBlob snapshots the bitmap at invocation; later local frames may
          // reuse the canvas while this single upload's encoding completes.
          const image_base64=await encodeCanvas(frameCanvas)
          if(!valid())return null
          if(!image_base64)throw new Error('仿真相机 JPEG 编码结果为空')
          measurements.encoded++;measurements.encode_ms=now()-start
          return {...metadata,image_base64}
        }finally{encoding=false}
      }
      const result=encode?await encodeFrame():{...metadata,canvas:frameCanvas,encode:encodeFrame}
      if(!valid() || !result)return null
      timing.total_ms=now()-started;measurements.completed++;measurements.last=timing
      return result
    }catch(error){measurements.failed++;throw error}
    finally{pending=false;if(disposed || target && (target.width!==width || target.height!==height))release()}
  }
  configure({enabled:false})
  return {configure,capture,setToolInstallation(tool,rotation=DEFAULT_CAMERA_TOOL_ROTATION){
      mount=defaultCameraMount(spec,tool || {...DEFAULT_VACUUM,mountHeight:0},rotation)
      configure(lastConfig)
    },pose:()=>{flange.updateWorldMatrix(true,true);return eye.matrixWorld.elements.slice()},
    metrics:()=>({...measurements,last:measurements.last?{...measurements.last}:null,pending,encoding,
      encoder:encodeCanvas!==encodeVirtualCanvas?'injected':canvas?.toBlob && typeof globalThis.FileReader==='function'?'async-blob':'sync-data-url'}),
    dispose(){if(disposed)return;disposed=true;epoch++;enabled=false;flange.remove(eye);flange.remove(body);holes.dispose();for(const geometry of ownedGeometries)geometry.dispose();for(const surface of ownedMaterials)surface.dispose();if(!pending)release()}}
}
