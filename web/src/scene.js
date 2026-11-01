/* Three.js 场景：官方 URDF 导出的层级 + STL 网格 + 围栏 + 残影 + 真机插值。
 *
 * 单位用米（和 URDF 一致），相机和栅格按 1.2m 高的臂展取景。
 * 真机 20Hz 数据在本地缓冲后按渲染帧插值，避免"一顿一顿"。
 */
import * as THREE from 'three'
import { TransformControls } from 'three/addons/controls/TransformControls.js'
import { buildVacuumReference, buildPen, readToolFile, readLibraryTool, disposeAsset, applyToolSkin, createToolAnimation } from './toolVisual.js'
import { robotAsset } from './visualAssets.js'
import { applyJointCaps, applyRobotFinishes } from './jointCaps.js'
import { createRobotSurfaceMaterial, smoothRobotSurface } from './robotSurface.js'
import { robotSurfaceAssetUrl, hydrateRobotSurfaceGeometry, ROBOT_SURFACE_ASSET_VERSION, ROBOT_SURFACE_RECIPE } from './robotSurfaceAsset.js'
import { GROUND_DIRECTIONS } from './groundDirections.js'
import { recordMetric } from './runtimeMetrics.js'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { catalogEntry, OBJECT_SURFACES } from './objectCatalog.js'
import { createJointPresentation } from './jointPresentation.js'
import { objectGroundHeight } from './objectGround.js'
import { createVirtualCamera } from './virtualCamera.js'
import { createDrawingVisual } from './drawingVisual.js'
import { parseSceneAsset } from './sceneAssetVisual.js'

// Original closed solids; box remains the conservative collision envelope.
function createPlayTokenGeometry(object) {
  if (object.kind !== 'box' || !['token_o','token_x'].includes(object.preset)) return null
  const shape = new THREE.Shape()
  if (object.preset === 'token_o') {
    shape.absarc(0,0,.5,0,Math.PI*2,false)
    const hole = new THREE.Path()
    hole.absarc(0,0,.31,0,Math.PI*2,true)
    shape.holes.push(hole)
  } else {
    const points=[[-.5,-.34],[-.34,-.5],[0,-.16],[.34,-.5],[.5,-.34],[.16,0],[.5,.34],[.34,.5],[0,.16],[-.34,.5],[-.5,.34],[-.16,0]]
    shape.moveTo(...points[0]);for(const point of points.slice(1))shape.lineTo(...point)
    shape.closePath()
  }
  const geometry = new THREE.ExtrudeGeometry(shape,{depth:1,bevelEnabled:false,steps:1,curveSegments:32})
  geometry.translate(0,0,-.5);geometry.rotateX(-Math.PI/2)
  const size=object.size.map(value=>Math.max(value,1)/1000)
  geometry.scale(size[0],size[2],size[1])
  geometry.userData.preset=object.preset
  return geometry
}


export async function createScene(container, options = {}) {
  const initializationCleanup=[]
  const released=new Set()
  const release=resource=>{if(resource && !released.has(resource)){released.add(resource);resource.dispose?.()}}
  const listen=(target,name,handler,options)=>{
    target.addEventListener(name,handler,options)
    initializationCleanup.push(()=>target.removeEventListener(name,handler,options))
  }
  let initializedApi=null
  try {
  const checkCancelled=()=>options.signal?.throwIfAborted()
  checkCancelled()
  const loadStarted=performance.now()
  const asset = robotAsset(options.model || 'elfin5')
  const modelBase = asset.base
  const specResponse = await fetch(modelBase + 'robot.json',{signal:options.signal})
  if (!specResponse.ok) throw new Error('无法加载模型 ' + asset.name)
  const spec = await specResponse.json()
  checkCancelled()
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  initializationCleanup.push(()=>{release(renderer);renderer.domElement.remove()})
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(container.clientWidth, container.clientHeight)
  renderer.shadowMap.enabled = false
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  const drawingVisual=createDrawingVisual({scene,now:()=>performance.now()})
  const drawingRoot=scene.getObjectByName('drawing-visual')
  initializationCleanup.push(()=>scene.traverse(object=>{
    release(object.geometry)
    for(const material of (Array.isArray(object.material)?object.material:[object.material]).filter(Boolean)){
      Object.values(material).filter(value=>value?.isTexture).forEach(release);release(material)
    }
  }))
  initializationCleanup.push(()=>drawingVisual.dispose())
  scene.background = new THREE.Color('#0b1416')
  // A monitoring viewer must not fade the robot into black when zooming out.

  /* 2.5D 口径：窄视场角 + 远距离机位，把透视压平成轴测感。
     正交会丢掉"纵深那口气"，广角又会把臂展拉变形，所以走中间这条。 */
  const camera = new THREE.PerspectiveCamera(22, container.clientWidth / container.clientHeight, 0.05, 80)
  // Off-axis framing leaves space on the right without changing orbit controls,
  // the persisted observation target, or any robot/world coordinates.
  function frameLeft(w, h) { camera.setViewOffset(w,h,w*.13,0,w,h) }
  frameLeft(container.clientWidth,container.clientHeight)
  // 机位只有一个权威来源：下面的 orbit/applyOrbit（默认正面 + 地面留白）。
  // 这里不再单独写 camera.position，避免初始斜侧视和默认机位打架。

  scene.add(new THREE.HemisphereLight('#e4efff', '#505969', 2.1))
  const key = new THREE.DirectionalLight('#ffffff', 3)
  key.position.set(3, 5, 2)
  scene.add(key)
  const rim = new THREE.DirectionalLight('#48b7af', 0.6)
  rim.position.set(-3, 1.5, -3)
  scene.add(rim)

  const grid = new THREE.GridHelper(4, 40, '#2c736e', '#18312f')
  grid.position.y = 0
  scene.add(grid)
  grid.visible=false
  // 不透明工业地坪；场景几何与视觉相机共享同一物理表面。
  const labFloor=new THREE.Mesh(new THREE.PlaneGeometry(1,1),new THREE.MeshStandardMaterial({color:'#69747a',roughness:.96,metalness:.02}))
  labFloor.name='laboratory-floor';labFloor.rotation.x=-Math.PI/2;labFloor.position.y=-.001;scene.add(labFloor)
  labFloor.scale.set(3,3,1)
  const floorSeams=new THREE.GridHelper(1,12,'#566269','#566269');floorSeams.position.y=.0001;floorSeams.scale.set(3,1,3)
  floorSeams.material.transparent=true;floorSeams.material.opacity=.25;scene.add(floorSeams)
  const groundRiskMat=new THREE.MeshBasicMaterial({color:'#ffd166',transparent:true,opacity:0,depthWrite:false,side:THREE.DoubleSide})
  const groundRiskPlane=new THREE.Mesh(new THREE.PlaneGeometry(4,4),groundRiskMat)
  groundRiskPlane.rotation.x=-Math.PI/2;groundRiskPlane.position.y=.001;groundRiskPlane.visible=false;scene.add(groundRiskPlane)
  let groundSafety='green'
  function setGroundSafety(level) {
    groundSafety=['yellow','red','alarm'].includes(level)?level:'green'
    groundRiskPlane.visible=groundSafety!=='green'
    groundRiskMat.color.set(groundSafety==='yellow'?'#ffd166':'#ff4d6d')
    groundRiskMat.opacity=groundSafety==='yellow'?.025:.05
  }

  /* 脚下的青色刻度盘：一圈环 + 刻度 + 两条正交基准线。纯线段不吃性能，
     但它是这套 HUD 观感的锚点 —— 没有它，机械臂像浮在空里。 */
  const hud = new THREE.Group()
  {
    const seg = (pts, color, op) => new THREE.LineSegments(
      new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(pts, 3)),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: op }))
    const R = 1.02
    const ring = []
    const tick = []
    for (let i = 0; i <= 72; i++) {
      const a = (i / 72) * Math.PI * 2
      ring.push(Math.cos(a) * R, 0.002, Math.sin(a) * R)
      if (i < 72 && i % 3 === 0) {
        const r2 = R - (i % 6 === 0 ? 0.075 : 0.038)
        tick.push(Math.cos(a) * R, 0.002, Math.sin(a) * R)
        tick.push(Math.cos(a) * r2, 0.002, Math.sin(a) * r2)
      }
    }
    hud.add(seg(ring, '#48b7af', 0.5))
    hud.add(seg(tick, '#48b7af', 0.32))
    hud.add(seg([-(R + 0.1), 0.001, 0, R + 0.1, 0.001, 0,
                 0, 0.001, -(R + 0.1), 0, 0.001, R + 0.1], '#305274', 0.45))
    scene.add(hud)
  }
  const groundDirectionLabels=[]
  for(const direction of GROUND_DIRECTIONS) {
    const canvas=document.createElement('canvas');canvas.width=256;canvas.height=80
    const ctx=canvas.getContext('2d')
    ctx.fillStyle='#0b1416';ctx.fillRect(14,9,228,62)
    ctx.strokeStyle='#30555b';ctx.strokeRect(14,9,228,62)
    ctx.fillStyle='#b8dce1';ctx.font='600 34px "Microsoft YaHei",sans-serif'
    ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(direction.name,128,41)
    const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas),transparent:true,depthWrite:false}))
    const [x,y]=direction.xyz
    sprite.position.set(x/1000,.018,-y/1000);sprite.scale.set(.29,.091,1)
    hud.add(sprite);groundDirectionLabels.push({...direction,sprite})
  }

  // ---------------- 机器人 ----------------
  const [{ GLTFLoader }, { MeshoptDecoder }] = await Promise.all([
    import('three/examples/jsm/loaders/GLTFLoader.js'), import('meshoptimizer/decoder'),
  ])
  const gltfLoader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder)
  const geoms = {}
  const matCache = {}
  initializationCleanup.push(()=>{Object.values(geoms).forEach(release);Object.values(matCache).forEach(release)})
  for (const [name, m] of Object.entries(spec.materials || {})) {
    const [r, g, b, a] = m.rgba
    matCache[name] = new THREE.MeshStandardMaterial({
      color: new THREE.Color(r, g, b), metalness: 0.55, roughness: 0.45, vertexColors: true,
      transparent: a < 1, opacity: a,
    })
  }
  const defaultMat = new THREE.MeshStandardMaterial({ color: '#b9c6c4', metalness: 0.5, roughness: 0.5, vertexColors: true })
  initializationCleanup.push(()=>release(defaultMat))

  // Static display preparation is done once offline, not on every page refresh.
  // Other models and unavailable/outdated assets keep the original GLB path.
  let surfaceReport=null
  if(asset.id==='elfin5') {
    try {
      const response=await fetch(modelBase+'surface-report.json',{signal:options.signal})
      const report=response.ok ? await response.json() : null
      if(report?.version===ROBOT_SURFACE_ASSET_VERSION && report.recipe===ROBOT_SURFACE_RECIPE &&
          report.variant===asset.id && Array.isArray(report.assets))surfaceReport=report
    } catch { /* Original model remains the supported fallback. */ }
  }
  async function parseDisplayGeometry(response) {
    const parsed=await gltfLoader.parseAsync(await response.arrayBuffer(),modelBase)
    let geometry=null
    try {
      parsed.scene.updateMatrixWorld(true)
      const identity=new THREE.Matrix4()
      parsed.scene.traverse(object=>{
        if(object.isMesh && !geometry) {
          geometry=object.geometry.clone()
          if(!object.matrixWorld.equals(identity))geometry.applyMatrix4(object.matrixWorld)
        }
      })
      if(!geometry)throw new Error('GLB has no geometry')
      return geometry
    } catch(error) {release(geometry);throw error}
    finally {
      parsed.scene.traverse(object=>{
        release(object.geometry)
        for(const material of (Array.isArray(object.material)?object.material:[object.material]).filter(Boolean)){
          Object.values(material).filter(value=>value?.isTexture).forEach(release);release(material)
        }
      })
    }
  }

  const loadedLinks=await Promise.allSettled(Object.entries(spec.links).map(async ([lname, l]) => {
    if (!l.visual || !l.visual.mesh) return
    const url = modelBase + l.visual.mesh
    if (!geoms[lname]) {
      try {
        const lod = localStorage.getItem('huayan.modelQuality') === 'low' ? '.low.glb' : '.glb'
        const quality=lod==='.low.glb'?'low':'full'
        const baked=surfaceReport?.assets.find(entry=>entry.link===lname && entry.quality===quality &&
          entry.file===l.visual.mesh.replace(/\.STL$/i,lod).replace(/\.glb$/i,'.surface.glb'))
        if(baked) {
          let prepared=null
          try {
            const response=await fetch(robotSurfaceAssetUrl(url.replace(/\.STL$/i,lod)),{signal:options.signal})
            if(!response.ok)throw new Error('Prepared display unavailable')
            prepared=await parseDisplayGeometry(response)
            if(!hydrateRobotSurfaceGeometry(prepared,lname,l,{sourceSha256:baked.source_sha256,quality,variant:asset.id}))
              throw new Error('Prepared display outdated')
            geoms[lname]=prepared
            return
          } catch { prepared?.dispose() }
        }
        const response = await fetch(url.replace(/\.STL$/i, lod),{signal:options.signal})
        if (!response.ok) throw new Error('GLB unavailable')
        geoms[lname] = await parseDisplayGeometry(response)
      } catch (e) {
        try {
          const { STLLoader } = await import('three/examples/jsm/loaders/STLLoader.js')
          const response = await fetch(url,{signal:options.signal})
          if (!response.ok) throw new Error('STL unavailable')
          geoms[lname] = new STLLoader().parse(await response.arrayBuffer())
        } catch (fallback) {
          console.warn('网格加载失败', url, fallback)
          return
        }
      }
    }
  }))
  checkCancelled()
  const failedLink=loadedLinks.find(result=>result.status==='rejected')
  if(failedLink)throw failedLink.reason
  const missingLinks=Object.entries(spec.links).filter(([name,link])=>link.visual?.mesh && !geoms[name]).map(([name])=>name)
  if(missingLinks.length)throw new Error('模型资源缺失：'+missingLinks.join('、'))

  // Paint measured covers on the original faces, never add discs over a link.
  const prepareStarted=performance.now()
  for (const [name, geometry] of Object.entries(geoms)) {
    if(geometry.userData.robotSurfaceAsset)continue
    applyJointCaps(geometry,name,spec.links[name]);applyRobotFinishes(geometry,name)
    smoothRobotSurface(geometry)
  }
  recordMetric('scene.prepare',performance.now()-prepareStarted)
  const robotBodyMaterials=new Map()
  initializationCleanup.push(()=>robotBodyMaterials.forEach(release))
  for (const [name,geometry] of Object.entries(geoms)) {
    const body=matCache[spec.links[name].visual.material] || defaultMat
    robotBodyMaterials.set(name,createRobotSurfaceMaterial(body,geometry,name))
  }
  const socketMat=new THREE.MeshStandardMaterial({color:'#101417',metalness:.05,roughness:.72,vertexColors:true})
  initializationCleanup.push(()=>release(socketMat))
  socketMat.userData.surface='socket-black'

  /* URDF 是 Z 轴朝上，Three.js 是 Y 轴朝上。所有外部数据（关节原点、围栏、
     胶囊体）必须走同一个基变换 (x,y,z) -> (x,z,-y)，否则机器人躺倒而围栏立着。 */
  const urdfToThree = (v) => [v[0], v[2], -v[1]]

  const childrenOf = {}
  for (const j of spec.joints) {
    if (!j.parent) continue
    ;(childrenOf[j.parent] ||= []).push(j)
  }
  const movable = spec.joints.filter((j) => j.limit)
  const jointIndex = {}
  movable.forEach((j, i) => { jointIndex[j.name] = i })

  /* 建一台完整的臂。materialOf 给网格换材质，就能用同一份几何体再建一台半透明的"残影臂"。
     固定原点（xyz+rpy）与绕轴转动必须分两层：同一节点上先设 rpy 再 setRotationFromAxisAngle
     会把 rpy 覆盖掉，整条臂就折成一团（实测包围盒只有 0.48m）。 */
  function buildRobot(materialOf) {
    const root = new THREE.Group()
    root.rotation.x = -Math.PI / 2      // (x,y,z) -> (x,z,-y)
    scene.add(root)
    const jn = {}
    const ln = {}
    const walk = (parentObj, joint) => {
      const origin = new THREE.Group()
      origin.position.set(...joint.xyz)
      // URDF 的 rpy 是固定轴 X-Y-Z，等价 Rz·Ry·Rx；Three 的 'XYZ' 是自身轴 X→Y→Z，
      // 两者不等价，用 'ZYX' 才和后端 kinematics.rpy_to_mat 同构。
      origin.rotation.order = 'ZYX'
      origin.rotation.set(joint.rpy[0], joint.rpy[1], joint.rpy[2], 'ZYX')
      parentObj.add(origin)
      const node = new THREE.Group()
      origin.add(node)
      jn[joint.name] = node
      const linkObj = new THREE.Group()
      node.add(linkObj)
      ln[joint.child] = linkObj
      const l = spec.links[joint.child]
      if (l && l.visual && geoms[joint.child]) {
        const mesh = new THREE.Mesh(geoms[joint.child], materialOf(l,joint.child))
        mesh.position.set(...l.visual.origin_xyz)
        mesh.rotation.order = 'ZYX'
        mesh.rotation.set(l.visual.origin_rpy[0], l.visual.origin_rpy[1], l.visual.origin_rpy[2], 'ZYX')
        mesh.userData.link = joint.child
        linkObj.add(mesh)
      }
      // 子关节挂在转动层之后，这样它随父关节一起转
      for (const cj of childrenOf[joint.child] || []) walk(node, cj)
    }
    for (const rootJoint of childrenOf.world || []) walk(root, rootJoint)
    return { root, jn, ln }
  }

  const real = buildRobot((l,name) => {
    const body=robotBodyMaterials.get(name) || matCache[l.visual.material] || defaultMat
    return [body,body,socketMat]
  })
  const robotRoot = real.root
  // Robot, TCP and trajectory remain in the fixed world frame. Only the
  // workspace visualization may change direction; never rotate the robot here.
  const handDisplayFrame = new THREE.Group()
  scene.add(handDisplayFrame);handDisplayFrame.add(robotRoot)
  const jointNodes = real.jn
  const linkNodes = real.ln

  /* 假手 = 另一台半透明臂，几何体与本体共享，只多一套 Object3D 和一个材质。
     一个模型两种用途：残影预览（青）与时间轴回放（紫），颜色就是它们的身份牌。 */
  const GHOST_COLOR = { ghost: '#4fd8cf', playback: '#9b7bff', yellow: '#ffd166', red: '#ff4d6d' }
  const ghostMat = new THREE.MeshBasicMaterial({
    color: GHOST_COLOR.ghost, transparent: true, opacity: 0.34, depthWrite: false,
  })
  const ghost = buildRobot(() => ghostMat)
  ghost.root.visible = false
  // Display-only skin. It never changes the transparent target arm or physics.
  const factoryMaterials = new Map([...Object.values(matCache), defaultMat, ...robotBodyMaterials.values()].map((m) => [m, {
    color: m.color.clone(), metalness: m.metalness, roughness: m.roughness,
  }]))
  const { RoomEnvironment } = await import('three/examples/jsm/environments/RoomEnvironment.js')
  const room = new RoomEnvironment()
  initializationCleanup.push(()=>release(room))
  const pmrem = new THREE.PMREMGenerator(renderer)
  initializationCleanup.push(()=>release(pmrem))
  const environment = pmrem.fromScene(room, .04)
  initializationCleanup.push(()=>release(environment))
  scene.environment = environment.texture
  release(room); release(pmrem)
  let skin = 'factory'
  async function setSkin(name) {
    if (!['factory', 'cyber'].includes(name)) throw new Error('未知皮肤')
    skin = name
    for (const [material, original] of factoryMaterials) {
      material.color.copy(name === 'cyber' ? new THREE.Color('#8d9aaa') : original.color)
      material.metalness = name === 'cyber' ? .6 : original.metalness
      material.roughness = name === 'cyber' ? .3 : original.roughness
      material.emissive.set('#000000')
      material.emissiveIntensity = 0
    }
    applyToolSkin(toolRoot, name)
    healthKey = ''
  }
  let toolRoot = null, ghostTool = null, toolRevision = 0, sceneDisposed = false
  let toolAnimation=null, ghostAnimation=null, toolState=null, animationHydrated=false, animationLastTime=null
  function setToolState(state,mode='sim') {
    const matches=mode==='sim' && state?.mounted?.scope==='simulation' &&
      state.mounted.tool_id===(toolRoot?.userData.id || 'vacuum-reference')
    toolState=matches?state.mounted:null
    if(toolState && !animationHydrated) {
      toolAnimation?.reset(toolState);ghostAnimation?.reset(toolState);animationHydrated=true
    }
  }
  function clearTool() {
    ghostAnimation?.dispose();toolAnimation=null;ghostAnimation=null;animationHydrated=false
    toolRoot?.removeFromParent(); ghostTool?.removeFromParent()
    // Ghost shares geometry/textures with the original and owns no materials.
    disposeAsset(toolRoot)
    toolRoot = ghostTool = null
  }
  async function setToolVisual(value) {
    const revision = ++toolRevision
    if (!value) { clearTool(); virtualEye.setToolInstallation(null); return }
    const next = value.kind === 'pen' ? buildPen() : value.kind === 'vacuum-reference' ? buildVacuumReference(value.dimensions)
        : value.kind === 'library' ? await readLibraryTool(value) : await readToolFile(value.file, value.unit)
    applyToolSkin(next, skin)
    const rotation = value.rotation || [0, 0, 0]
    if (!Array.isArray(rotation) || rotation.length !== 3 || !rotation.every((v) => typeof v === 'number' && Number.isFinite(v))) {
      disposeAsset(next); throw new Error('工具显示角度需要三个有限数值')
    }
    // A wrapper preserves any root transforms embedded in a downloaded GLB.
    const mounted = new THREE.Group()
    mounted.rotation.set(...rotation.map(THREE.MathUtils.degToRad), 'ZYX')
    mounted.userData = { ...next.userData, rotation: rotation.slice() }
    mounted.add(next)
    const toolAxes = new THREE.AxesHelper(.075)
    if (value.kind==='vacuum-reference') {
      const d=mounted.userData.dimensions
      toolAxes.position.z=(d.mountHeight+d.thickness/2)/1000
      } else if(['library','pen'].includes(value.kind) && mounted.userData.tcp_mm) {
        toolAxes.position.fromArray(mounted.userData.tcp_mm.map(v=>v/1000))
    }
    toolAxes.userData.displayAxes = true
    mounted.add(toolAxes)
    if (sceneDisposed || revision !== toolRevision) { disposeAsset(mounted); return }
    const flange = linkNodes.elfin_end_link || linkNodes.elfin_link6
    const ghostFlange = ghost.ln.elfin_end_link || ghost.ln.elfin_link6
    if (!flange || !ghostFlange) { disposeAsset(mounted); throw new Error('模型没有可识别的末端法兰') }
    clearTool()
    toolRoot = mounted
    ghostTool = mounted.clone(true)
    ghostTool.traverse((o) => { if (o.isMesh) o.material = ghostMat; if (o.userData.displayAxes) o.visible = false })
    toolAnimation=next.animation?.supported?next.animation:null
    if(toolAnimation)ghostAnimation=createToolAnimation(ghostTool.children[0],toolAnimation.metadata)
    flange.add(toolRoot); ghostFlange.add(ghostTool)
    virtualEye.setToolInstallation(value.kind==='vacuum-reference' ? next.userData.dimensions : null,rotation)
  }
  let ghostMode = 'ghost'
  let ghostRisk = 'green'
  let ghostContact = false
  /* 每帧 new 6 个 Vector3 是白扔的 GC 压力，轴向量是常量，先算好 */
  const jointAxes = movable.map((j) => new THREE.Vector3(...j.axis).normalize())
  let robotPoseVersion = 0
  function applyAngles(root, jn, q) {
    for (let i = 0; i < movable.length; i++) {
      const node = jn[movable[i].name]
      if (!node) continue
      // 关节角必须是有限数：字符串或 NaN 会把旋转矩阵写成 NaN，整台模型都花掉。
      const raw = q?.[i]
      const value = typeof raw === 'number' && Number.isFinite(raw) ? raw : 0
      node.setRotationFromAxisAngle(jointAxes[i], value * Math.PI / 180)
    }
    root.updateMatrixWorld(true)
    if (root === robotRoot) robotPoseVersion++
  }

  /* 残影 = 另一台假手，跟着预设滑块摆；点执行后真机开过来，到位时把它收掉。
     depthWrite=false 是为了半透明的假手不会挡住本体。 */
  let ghostMeshes = 0
  const GHOST_JOINTS = movable.length
  const ghostAngles = Array(GHOST_JOINTS).fill(NaN)
  ghost.root.traverse((o) => { if (o.isMesh) ghostMeshes++ })
  function setGhost(q, mode, risk = 'green', contact = false) {
    // 残影必须是一整组有限关节角：缺列或含 NaN 时宁可隐藏，也不能用零位
    // 假装"在某个姿态"——那会让回放凭空跳回原点，还每帧重算一次。
    let valid = Array.isArray(q) && q.length >= GHOST_JOINTS
    if (valid) {
      for (let i = 0; i < GHOST_JOINTS; i++) {
        const value = q[i]
        if (typeof value !== 'number' || !Number.isFinite(value)) { valid = false; break }
      }
    }
    if (!valid) { ghost.root.visible = false; return }
    const m = mode || 'ghost'
    ghostContact = m === 'ghost' && contact === true
    const severity = m === 'ghost' ? (risk === 'yellow' ? 'yellow' : ['red','alarm'].includes(risk) ? 'red' : 'green') : m
    if (m !== ghostMode || severity !== ghostRisk) {
      ghostMode = m; ghostRisk = severity
      ghostMat.color.set(GHOST_COLOR[severity] || GHOST_COLOR.ghost)
    }
    ghost.root.visible = true
    ghostMat.opacity = api.ghostOpacity
    if (!ghostAngles.every((v,i)=>Math.abs(v-q[i])<1e-6)) {
      applyAngles(ghost.root, ghost.jn, q)
      for(let i=0;i<GHOST_JOINTS;i++) ghostAngles[i]=q[i]
    }
  }
  function clearGhosts() { ghost.root.visible = false }

  // ---------------- TCP 轨迹线 ----------------
  /* 长度是"帧数"，10~100 可调（参考开源 ROS 网页可视化的做法）。
     没动就不落点 —— 否则静止时同一坐标被反复堆进缓冲，线会看着发抖。 */
  const TRAIL_CAP = 100
  const trailPos = new Float32Array(TRAIL_CAP * 3)
  const trailGeo = new THREE.BufferGeometry()
  trailGeo.setAttribute('position', new THREE.BufferAttribute(trailPos, 3))
  trailGeo.setDrawRange(0, 0)
  const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ color: '#48b7af' }))
  handDisplayFrame.add(trail)
  let trailN = 0
  let trailLen = 40
  function setTrailLen(n) { trailLen = Math.max(0, Math.min(TRAIL_CAP, Number(n) || 0)) }
  function pushTrail(p) {
    if (!trailLen) return
    if (trailN) {
      const i = (trailN - 1) * 3
      const dx = p.x - trailPos[i], dy = p.y - trailPos[i + 1], dz = p.z - trailPos[i + 2]
      if (dx * dx + dy * dy + dz * dz < 1e-6) return
    }
    if (trailN >= trailLen) {                 // 超长就把最老的挤出去
      trailPos.copyWithin(0, (trailN - trailLen + 1) * 3, trailN * 3)
      trailN = trailLen - 1
    }
    const offset=trailN*3
    trailPos[offset]=p.x;trailPos[offset+1]=p.y;trailPos[offset+2]=p.z
    trailN++
    trailGeo.setDrawRange(0, trailN)
    trailGeo.attributes.position.needsUpdate = true
  }
  function clearTrail() { trailN = 0; trailGeo.setDrawRange(0, 0) }

  // ---------------- 围栏 / 地面 / 胶囊体 ----------------
  const fenceMat = new THREE.MeshBasicMaterial({
    color: '#48b7af', transparent: true, opacity: 0.008, side: THREE.DoubleSide, depthWrite: false,
  })
  const fenceEdges = new THREE.LineBasicMaterial({ color: '#2c736e', transparent: true, opacity: .28 })
  let fenceSafety = 'green'
  let fenceObj = null
  let fenceKey = ''
  let fenceVisible = true
  function setFenceVisible(value) { fenceVisible = !!value; if (fenceObj) fenceObj.visible = fenceVisible }
  function setFenceSafety(level) {
    const next = ['yellow','red','alarm'].includes(level) ? level : 'green'
    if (next === fenceSafety) return
    fenceSafety = next
    const color = fenceSafety === 'green' ? '#2c736e' : fenceSafety === 'yellow' ? '#ffd166' : '#ff4d6d'
    fenceEdges.color.set(color)
    fenceMat.color.set(fenceSafety === 'yellow' ? '#ffd166' : fenceSafety === 'green' ? '#48b7af' : '#ff4d6d')
    fenceEdges.opacity = fenceSafety === 'green' ? .28 : .78
    fenceMat.opacity = fenceSafety === 'green' ? .008 : .035
  }
  function setFence(f) {
    const key = f && f.enabled ? JSON.stringify(f) : ''
    if (key === fenceKey) return          // 每帧重建 Box+Edges 是纯浪费
    fenceKey = key
    // 地坪仅比围栏投影每侧多100毫米；缩放复用几何，不修改围栏约束。
    const bounded=f && f.enabled && [f.x,f.y].every(axis=>Array.isArray(axis) && axis.length===2 && axis.every(Number.isFinite) && axis[1]>axis[0])
    const floorWidth=bounded?(f.x[1]-f.x[0])/1000+.2:3
    const floorDepth=bounded?(f.y[1]-f.y[0])/1000+.2:3
    const floorCenter=bounded?urdfToThree([(f.x[0]+f.x[1])/2000,(f.y[0]+f.y[1])/2000,0]):[0,0,0]
    labFloor.scale.set(floorWidth,floorDepth,1);labFloor.position.set(floorCenter[0],-.001,floorCenter[2])
    floorSeams.scale.set(floorWidth,1,floorDepth);floorSeams.position.set(floorCenter[0],.0001,floorCenter[2])
    if (fenceObj) { scene.remove(fenceObj); fenceObj.traverse((o) => o.geometry && o.geometry.dispose()) }
    if (!f || !f.enabled) return
    // Keep the displayed safety fence above the ground plane. This is only a
    // visualization clamp; backend fence limits and collision checks stay as set.
    const floorZ = 0, ceilingZ = Math.max(0, f.z[1])
    const zSize = ceilingZ - floorZ
    if (zSize <= 0) return
    const size = [(f.x[1] - f.x[0]) / 1000, zSize / 1000, (f.y[1] - f.y[0]) / 1000]
    const ctr = urdfToThree([(f.x[0] + f.x[1]) / 2000, (f.y[0] + f.y[1]) / 2000, (floorZ + ceilingZ) / 2000])
    const g = new THREE.Group()
    const box = new THREE.BoxGeometry(size[0], size[1], size[2])
    g.add(new THREE.Mesh(box, fenceMat))
    g.add(new THREE.LineSegments(new THREE.EdgesGeometry(box), fenceEdges))
    g.position.set(...ctr)
    g.visible = fenceVisible
    scene.add(g)
    fenceObj = g
    const cx=(f.x[0]+f.x[1])/2,cy=(f.y[0]+f.y[1])/2
    const labels=[[cx,f.y[0]+100,0],[f.x[1]-100,cy,0],[cx,f.y[1]-100,0],[f.x[0]+100,cy,0]]
    groundDirectionLabels.forEach((entry,i)=>{
      entry.xyz=labels[i]
      entry.sprite.position.set(entry.xyz[0]/1000,.018,-entry.xyz[1]/1000)
    })
  }

  let capsuleGroup = new THREE.Group()
  scene.add(capsuleGroup)
  const capsuleGeometry = new THREE.CylinderGeometry(1, 1, 1, 8, 1, true)
  const capsuleMaterial = new THREE.MeshBasicMaterial({
    color: '#e0a33b', wireframe: true, transparent: true, opacity: 0.35,
  })
  const capsulePool = []
  const capsuleA=new THREE.Vector3(),capsuleB=new THREE.Vector3(),capsuleDir=new THREE.Vector3()
  const upAxis=new THREE.Vector3(0,1,0)
  function setCapsules(list, show) {
    capsuleGroup.visible = !!show
    if (!show) return
    capsulePool.forEach((mesh) => { mesh.visible = false })
    list.forEach((c, index) => {
      const a=capsuleA.set(c[0]/1000,c[2]/1000,-c[1]/1000)
      const b=capsuleB.set(c[3]/1000,c[5]/1000,-c[4]/1000)
      const dir = capsuleDir.copy(b).sub(a)
      const len = dir.length()
      if (len < 1e-4) return
      let mesh = capsulePool[index]
      if (!mesh) {
        mesh = new THREE.Mesh(capsuleGeometry, capsuleMaterial)
        capsulePool[index] = mesh
        capsuleGroup.add(mesh)
      }
      mesh.visible = true
      mesh.scale.set(c[6] / 1000, len, c[6] / 1000)
      mesh.position.copy(a).addScaledVector(dir,0.5)
      mesh.quaternion.setFromUnitVectors(upAxis, dir.normalize())
    })
  }

  // ---------------- 摆放物（纯仿真道具，坐标同样是 URDF mm / z 朝上） ----------------
  const objGroup = new THREE.Group()
  scene.add(objGroup)
  const planLine = new THREE.Line(new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ color: '#e0a33b', transparent: true, opacity: 0.8 }))
  let planCapacity=32,planCount=0,planOwner=null
  planLine.geometry.setAttribute('position',new THREE.BufferAttribute(new Float32Array(planCapacity*3),3).setUsage(THREE.DynamicDrawUsage))
  planLine.geometry.setDrawRange(0,0)
  planLine.visible=false
  scene.add(planLine)
  const objMeshes = {}
  // 视觉代理独立于持久场景和碰撞链路；共用单位几何，不逐帧重建网格。
  const visionGroup=new THREE.Group();visionGroup.name='vision-observations';scene.add(visionGroup)
  const visionMeshes=new Map(),visionGeometries={box:new THREE.BoxGeometry(1,1,1),
    cylinder:new THREE.CylinderGeometry(.5,.5,1,24),triangle:new THREE.CylinderGeometry(.5,.5,1,3)}
  let visionAnimating=false
  function setVisionObjects(objects=[]) {
    const seen=new Set()
    for(const object of objects.slice(0,50)) {
      const mapped=object.mapped
      if(!mapped || !Array.isArray(mapped.xyz_mm) || !Array.isArray(mapped.size_mm) ||
        ![...mapped.xyz_mm,...mapped.size_mm,mapped.yaw_deg].every(value=>Number.isFinite(value) && Math.abs(value)<10000) ||
        mapped.size_mm.some(value=>value<=0) || mapped.xyz_mm[2]-mapped.size_mm[2]/2<-.001 ||
        !/^V\d+$/.test(object.id) || !/^#[0-9a-f]{6}$/i.test(object.rgb))continue
      const kind=object.shape==='circle'?'cylinder':object.shape==='triangle'?'triangle':'box'
      seen.add(object.id)
      let mesh=visionMeshes.get(object.id)
      if(!mesh){
        mesh=new THREE.Mesh(visionGeometries[kind],new THREE.MeshStandardMaterial({color:object.rgb,transparent:true,opacity:.65,metalness:.1,roughness:.7}))
        mesh.name='vision-'+object.id;mesh.userData.vision=true;mesh.userData.target=new THREE.Vector3();mesh.userData.targetScale=new THREE.Vector3()
        visionGroup.add(mesh);visionMeshes.set(object.id,mesh)
      }
      mesh.geometry=visionGeometries[kind];mesh.material.color.set(object.rgb)
      mesh.material.opacity=object.status==='holding'?.2:.65
      const target=urdfToThree(mapped.xyz_mm.map(value=>value/1000))
      mesh.userData.target.set(...target)
      const [x,y,z]=mapped.size_mm.map(value=>value/1000)
      mesh.userData.targetScale.set(x,z,y);mesh.userData.targetYaw=mapped.yaw_deg*Math.PI/180
      if(!mesh.userData.initialized){mesh.position.copy(mesh.userData.target);mesh.scale.copy(mesh.userData.targetScale);mesh.rotation.y=mesh.userData.targetYaw;mesh.userData.initialized=true}
    }
    for(const [id,mesh] of visionMeshes)if(!seen.has(id)){visionGroup.remove(mesh);mesh.material.dispose();visionMeshes.delete(id)}
    visionAnimating=visionMeshes.size>0
  }
  function highlightObject(m) {
    const collision=m.userData.collided, selected=m.userData.sel
    m.material.emissive.setHex(collision ? 0xe4553f : selected ? 0x2fd6f0 : 0x000000)
    m.material.emissiveIntensity=collision ? .9 : selected ? .55 : 0
    m.userData.assetVisual?.traverse(child=>{
      for(const material of (Array.isArray(child.material)?child.material:[child.material]).filter(Boolean)) {
        if(material.emissive){material.emissive.copy(m.material.emissive);material.emissiveIntensity=m.material.emissiveIntensity}
      }
    })
  }
  function objGeometry(o) {
    const token=createPlayTokenGeometry(o)
    if(token)return token
    const s = o.size.map((v) => Math.max(v, 1) / 1000)
    const preset=catalogEntry(o.preset)
    if(preset?.parts && preset.kind===o.kind) {
      const parts=preset.parts.slice()
      if(preset.id==='zone_b' && o.task?.mode==='chess') {
        for(const offset of [-1/6,1/6]) {
          parts.push({shape:'box',size:[.008,.92,.15],at:[offset,0,.425]},
            {shape:'box',size:[.92,.008,.15],at:[0,offset,.425]})
        }
      }
      const pieces=parts.map(part=>{
        const [x,y,z]=part.size
        let geometry
        if(part.shape==='box') geometry=new THREE.BoxGeometry(x,z,y)
        else {
          geometry=new THREE.CylinderGeometry(.5,part.shape==='hopper'?.15:.5,1,24)
          if(part.axis==='y')geometry.rotateX(Math.PI/2)
          else if(part.axis==='x')geometry.rotateZ(Math.PI/2)
          geometry.scale(x,z,y)
        }
        geometry.translate(part.at[0],part.at[2],-part.at[1])
        return geometry
      })
      const geometry=mergeGeometries(pieces)
      pieces.forEach(piece=>piece.dispose())
      if(!geometry)throw new Error('工业预设几何无法合并')
      const width=o.kind==='cylinder'?s[0]*2:s[0],depth=o.kind==='cylinder'?s[0]*2:s[1]
      geometry.scale(width,s[2],depth)
      geometry.userData.preset=preset.id;geometry.userData.parts=parts.length
      return geometry
    }
    if (o.kind === 'sphere') return new THREE.SphereGeometry(s[0], 20, 14)
    // A multiple of four includes both radial extrema in the mesh bounds.
    if (o.kind === 'cylinder') return new THREE.CylinderGeometry(s[0], s[0], s[2], 24)
    return new THREE.BoxGeometry(s[0], s[2], s[1])
  }
  function clearPlayLabel(mesh) {
    for(const child of [...mesh.children])if(child.userData.playLabel) {
      mesh.remove(child);child.geometry.dispose();child.material.map.dispose();child.material.dispose()
    }
  }
  function updatePlayLabel(mesh,object) {
    const role=object.task?.role
    const zone=role==='zone_a' || role==='zone_b' ? role : object.preset
    const text=zone==='zone_a'?'A 原料区':zone==='zone_b'?'B 放料区':''
    const key=text?`${text}:${object.size.join(',')}`:''
    if(mesh.userData.playLabelKey===key)return
    clearPlayLabel(mesh);mesh.userData.playLabelKey=key
    if(!text)return
    const canvas=document.createElement('canvas');canvas.width=512;canvas.height=96
    const context=canvas.getContext('2d')
    context.font='600 54px "Microsoft YaHei", sans-serif';context.textAlign='center';context.textBaseline='middle'
    context.fillStyle='#102d37';context.fillText(text,256,48)
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace
    const label=new THREE.Mesh(new THREE.PlaneGeometry(object.size[0]/1000*.84,object.size[1]/1000*.16),
      new THREE.MeshBasicMaterial({map:texture,transparent:true,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1}))
    label.rotation.x=-Math.PI/2
    label.position.set(0,object.size[2]/2000,object.size[1]/1000*.37)
    label.userData.playLabel=true;label.raycast=()=>{}
    mesh.add(label)
  }
  function clearObjectAsset(mesh) {
    mesh.userData.assetRequest?.abort()
    mesh.userData.assetRequest=null
    if(mesh.userData.assetVisual){mesh.remove(mesh.userData.assetVisual);disposeAsset(mesh.userData.assetVisual);mesh.userData.assetVisual=null}
    mesh.material.transparent=false;mesh.material.opacity=1;mesh.material.depthWrite=true
  }
  function syncObjectAsset(mesh,object) {
    const key=object.asset_id?`${object.asset_id}:${object.size.join(',')}`:''
    if(mesh.userData.assetKey===key)return
    clearObjectAsset(mesh);mesh.userData.assetKey=key;mesh.userData.assetError=''
    if(!key)return
    const controller=new AbortController();mesh.userData.assetRequest=controller
    const timeout=setTimeout(()=>controller.abort(),10000)
    const current=()=>!sceneDisposed && objMeshes[object.id]===mesh && mesh.userData.assetKey===key && !controller.signal.aborted
    fetch(`/api/scene-assets/${object.asset_id}/model.glb`,{signal:controller.signal})
      .then(response=>{if(!response.ok)throw new Error('模型缓存不可用');return response.arrayBuffer()})
      .then(buffer=>parseSceneAsset(buffer,object.size))
      .then(root=>{
        if(!current()){disposeAsset(root);return}
        mesh.add(root);mesh.userData.assetVisual=root
        mesh.material.transparent=true;mesh.material.opacity=0;mesh.material.depthWrite=false
        mesh.userData.assetRequest=null;highlightObject(mesh)
        window.dispatchEvent(new CustomEvent('elfin-scene-asset-ready',{detail:{id:object.id,asset_id:object.asset_id}}))
      })
      .catch(error=>{
        if(sceneDisposed || objMeshes[object.id]!==mesh || mesh.userData.assetKey!==key)return
        mesh.userData.assetError=error.name==='AbortError'?'资源加载超时':error.message
        window.dispatchEvent(new CustomEvent('elfin-scene-asset-error',{detail:{id:object.id,why:`${object.name}显示失败：${mesh.userData.assetError}；保留碰撞代理`}}))
      }).finally(()=>clearTimeout(timeout))
  }
  let placement=null
  function cancelPlacement(force=false){
    if(!placement || (placement.pending && !force))return false
    const old=placement;placement=null;scene.remove(old.mesh);old.mesh.geometry.dispose();old.mesh.material.dispose();old.callbacks.cancel?.();window.dispatchEvent(new CustomEvent('elfin-objects-ready'));return true
  }
  function beginPlacement(spec,callbacks={}){
    if(sceneDisposed || placement?.pending || objectMove?.phase==='pending')return false
    cancelPlacement();setObjectTarget(null);drag=null
    const mesh=new THREE.Mesh(objGeometry(spec),new THREE.MeshStandardMaterial({color:spec.color || '#4fd8cf',transparent:true,opacity:.4,depthWrite:false,metalness:.1,roughness:.7}))
    mesh.name='object-placement-preview';mesh.visible=false;mesh.raycast=()=>{};scene.add(mesh)
    placement={spec:JSON.parse(JSON.stringify(spec)),mesh,callbacks,pending:false,xyz:null}
    return true
  }
  function movePlacement(event){
    if(!placement || placement.pending)return false
    const rect=el.getBoundingClientRect()
    if(event.clientX<rect.left || event.clientX>rect.right || event.clientY<rect.top || event.clientY>rect.bottom){placement.mesh.visible=false;placement.xyz=null;return false}
    const ray=new THREE.Raycaster();ray.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),camera)
    const point=ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0,1,0),0),new THREE.Vector3())
    if(!point || Math.abs(point.x)>1000 || Math.abs(point.z)>1000){placement.mesh.visible=false;placement.xyz=null;return false}
    const z=Math.max(objectGroundHeight(placement.spec),placement.spec.xyz?.[2] || 0)
    placement.xyz=[point.x*1000,-point.z*1000,z];placement.mesh.position.set(point.x,z/1000,point.z);placement.mesh.visible=true;return true
  }
  async function commitPlacement(){
    const current=placement
    if(!current || current.pending || !current.xyz)return false
    current.pending=true
    try{await current.callbacks.commit?.({...current.spec,xyz:current.xyz.slice()});if(placement===current)cancelPlacement(true);return true}
    catch(error){if(placement===current){current.pending=false;current.callbacks.error?.(error)}return false}
  }
  const onPlacementDragOver=event=>{if(placement){event.preventDefault();event.dataTransfer.dropEffect='copy';movePlacement(event)}}
  const onPlacementDrop=event=>{if(placement){event.preventDefault();if(movePlacement(event))void commitPlacement()}}
  let objectAttachment=null
  const attachmentPoint=new THREE.Vector3()
  function presentObjectAttachment() {
    if(!objectAttachment || objectMove?.id===objectAttachment.id)return
    const mesh=objMeshes[objectAttachment.id],flange=linkNodes[objectAttachment.link]
    if(!mesh || !flange)return
    attachmentPoint.fromArray(objectAttachment.local_m).applyMatrix4(flange.matrixWorld)
    mesh.position.copy(objGroup.worldToLocal(attachmentPoint))
  }
  function setObjects(list, selId, attachment=null) {
    objectAttachment=attachment && typeof attachment.id==='string' && typeof attachment.link==='string' &&
      Array.isArray(attachment.local_m) && attachment.local_m.length===3 && attachment.local_m.every(Number.isFinite)?attachment:null
    const seen = new Set()
    let membershipChanged=false
    for (const o of (list || [])) {
      seen.add(o.id)
      const geometryKey=`${o.kind}:${o.preset || o.kind}:${o.size.join(',')}${o.preset==='zone_b'?':'+(o.task?.mode==='chess'?'chess':'plain'):''}`
      let m = objMeshes[o.id]
      if (!m) {
        m = new THREE.Mesh(objGeometry(o), new THREE.MeshStandardMaterial({
          color: o.color || '#4fd8cf', metalness: 0.15, roughness: 0.7,
        }))
        m.userData.id = o.id
        m.userData.color = o.color
        m.userData.sel = false
        m.userData.collided = false
        m.userData.geometryKey = geometryKey
        objGroup.add(m)
        objMeshes[o.id] = m
        membershipChanged=true
      } else {
        if (m.userData.geometryKey !== geometryKey) {
          m.geometry.dispose(); m.geometry = objGeometry(o)
          m.userData.geometryKey = geometryKey
        }
        if (m.userData.color !== o.color) {
          m.material.color.set(o.color || '#4fd8cf')
          m.userData.color = o.color
        }
      }
      const surface=Object.hasOwn(OBJECT_SURFACES,o.surface)?o.surface:'painted'
      if(m.userData.surface!==surface) {
        m.material.metalness=OBJECT_SURFACES[surface].metalness
        m.material.roughness=OBJECT_SURFACES[surface].roughness
        m.userData.surface=surface
      }
      m.userData.authoritativeXYZ=o.xyz.slice()
      m.userData.groundHeight=objectGroundHeight(o)/1000
      if (!objectMove || objectMove.id !== o.id) {
        const p = urdfToThree(o.xyz.map((v) => v / 1000))
        m.position.set(...p)
      }
      const transformKey=JSON.stringify([o.rotation_deg || [0,0,0],o.scale ?? 1])
      if(m.userData.transformKey!==transformKey) {
        if((o.rotation_deg || [0,0,0]).every(value=>value===0))m.rotation.set(0,0,0)
        else {
        const basis=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),-Math.PI/2)
        const rotation=new THREE.Quaternion().setFromEuler(new THREE.Euler(...(o.rotation_deg || [0,0,0]).map(THREE.MathUtils.degToRad),'ZYX'))
        m.quaternion.copy(basis).multiply(rotation).multiply(basis.clone().invert())
        }
        m.scale.setScalar(o.scale ?? 1);m.userData.transformKey=transformKey
      }
      m.userData.locked=!!o.locked
      if(o.locked && objectTargetId===o.id)setObjectTarget(null)
      updatePlayLabel(m,o)
      syncObjectAsset(m,o)
      const sel = o.id === selId
      if (m.userData.sel !== sel) {
        m.userData.sel = sel
        highlightObject(m)
      }
    }
    for (const id of Object.keys(objMeshes)) {
      if (seen.has(id)) continue
      const m = objMeshes[id]
      if (id === objectTargetId) setObjectTarget(null)
      clearObjectAsset(m);objGroup.remove(m); clearPlayLabel(m); m.geometry.dispose(); m.material.dispose()
      delete objMeshes[id]
      membershipChanged=true
    }
    // Scene creation can precede the first object snapshot. Emit only on
    // membership changes, never on ordinary XYZ frames during a local drag.
    if(membershipChanged)window.dispatchEvent(new CustomEvent('elfin-objects-ready'))
  }
  function markCollisions(hits) {
    const on = new Set((hits || []).map((h) => h.id))
    for (const [id, m] of Object.entries(objMeshes)) {
      const c = on.has(id)
      if (c === m.userData.collided) continue
      m.userData.collided = c
      highlightObject(m)
    }
  }

  // ---------------- 交互（轨道控制，不引额外包） ----------------
  /* 默认机位：正面。theta=π 让相机落在 -X 一侧（与 GROUND_DIRECTIONS 的"正面"
     地标、INTERACTION_RANGE_FACTORY 的 270°、taskFrontView 的 FRONT_TASK_THETA 同轴），
     水平看向 +X（正对基座正面），没有左右斜偏。
     target 刻意压在臂体重心以下：机器人整体上移，脚下留出可见地面带。
     相机距目标 4m，远在围栏（默认 ±0.9m）之外，不做任何坐标夹取。 */
  const HOME = { theta: Math.PI, phi: 1.2, r: 4.0, targetY: 0.27,
                 fitPadding: 2.05, groundBias: 0.15 }
  const orbit = { theta: HOME.theta, phi: HOME.phi, r: HOME.r,
                  target: new THREE.Vector3(0, HOME.targetY, 0) }
  let viewChanged = () => {}
  function applyOrbit() {
      orbit.phi = Math.max(0.02, Math.min(Math.PI - 0.02, orbit.phi))
      orbit.r = Math.max(1.0, Math.min(12, orbit.r))
      camera.position.set(
        orbit.target.x + orbit.r * Math.sin(orbit.phi) * Math.cos(orbit.theta),
        orbit.target.y + orbit.r * Math.cos(orbit.phi),
        orbit.target.z + orbit.r * Math.sin(orbit.phi) * Math.sin(orbit.theta))
    camera.lookAt(orbit.target)
    camera.updateMatrixWorld()
    viewChanged({theta:orbit.theta,phi:orbit.phi,r:orbit.r,target:orbit.target.toArray(),fov:camera.fov})
  }
  applyOrbit()
  let drag = null
  const el = renderer.domElement
  let teachControl=null, teachTarget=null, teachHelper=null
  let objectControl=null, objectHelper=null, objectTargetId=null, objectCallbacks={}, objectMove=null
  const validObjectXYZ=xyz=>Array.isArray(xyz)&&xyz.length===3&&xyz.every(Number.isFinite)
  const objectXYZ=mesh=>[mesh.position.x*1000,-mesh.position.z*1000,mesh.position.y*1000]
  function writeObjectXYZ(mesh,xyz) { mesh.position.set(xyz[0]/1000,xyz[2]/1000,-xyz[1]/1000) }
  function restoreObjectXYZ(id) {
    const mesh=objMeshes[id]
    if(mesh && validObjectXYZ(mesh.userData.authoritativeXYZ)) writeObjectXYZ(mesh,mesh.userData.authoritativeXYZ)
  }
  function resetObjectControl() {
    drag=null
    if(objectControl) {
      objectControl.dragging=false;objectControl.axis=null
      objectControl.enabled=!sceneDisposed && objectTargetId!==null && !!objMeshes[objectTargetId]
    }
  }
  function cancelObjectMove() {
    if(!objectMove) return false
    const move=objectMove
    objectMove=null
    restoreObjectXYZ(move.id);resetObjectControl()
    move.callbacks.cancel?.()
    return true
  }
  function finishObjectMove(xyz=null) {
    if(sceneDisposed || objectMove?.phase!=='pending') return false
    const mesh=objMeshes[objectMove.id]
    if(!mesh) { cancelObjectMove();return false }
    const actual=xyz===null?objectXYZ(mesh):xyz
    if(!validObjectXYZ(actual)) return false
    mesh.userData.authoritativeXYZ=actual.slice();writeObjectXYZ(mesh,actual)
    objectMove=null;resetObjectControl()
    return true
  }
  function setObjectTarget(id,callbacks={}) {
    if(id!==objectTargetId || id===null) {
      const previous=objectTargetId, notify=objectMove?.callbacks || objectCallbacks
      // Clear ownership before invoking a controller callback: a reactive
      // selection change must never commit the gesture being detached.
      objectMove=null;objectTargetId=null;objectCallbacks={}
      if(previous!==null) restoreObjectXYZ(previous)
      if(objectControl) { objectControl.detach();resetObjectControl();objectHelper.visible=false }
      if(previous!==null) notify.cancel?.()
    }
    if(id===null) return true
    if(sceneDisposed || placement || !objMeshes[id] || objMeshes[id].userData.locked) return false
    setTeachTarget(null)
    objectTargetId=id;objectCallbacks=callbacks || {}
    if(!objectControl) {
      objectControl=new TransformControls(camera,el)
      objectControl.setMode('translate');objectControl.setSpace('world');objectControl.setSize(.7)
      objectHelper=objectControl.getHelper();objectHelper.name='object-gizmo';scene.add(objectHelper)
      objectControl.addEventListener('dragging-changed',()=>{drag=null})
      objectControl.addEventListener('mouseDown',()=>{
        if(sceneDisposed || !objectControl.enabled || objectMove || objectTargetId===null) return
        const mesh=objMeshes[objectTargetId]
        if(!mesh) return
        objectMove={id:objectTargetId,phase:'dragging',start:objectXYZ(mesh),callbacks:objectCallbacks}
        drag=null
        objectMove.callbacks.start?.(objectMove.start.slice())
      })
      objectControl.addEventListener('objectChange',()=>{
        if(sceneDisposed || objectMove?.phase!=='dragging') return
        const mesh=objMeshes[objectMove.id]
        mesh.position.y=Math.max(mesh.userData.groundHeight,mesh.position.y)
        const xyz=objectXYZ(mesh)
        if(!validObjectXYZ(xyz)) { cancelObjectMove();return }
        objectMove.callbacks.change?.(xyz)
      })
      objectControl.addEventListener('mouseUp',()=>{
        if(sceneDisposed || objectMove?.phase!=='dragging') return
        const move=objectMove,xyz=objectXYZ(objMeshes[move.id])
        if(!validObjectXYZ(xyz) || xyz.every((v,i)=>Math.abs(v-move.start[i])<1e-6) || typeof move.callbacks.commit!=='function') {
          cancelObjectMove();return
        }
        move.phase='pending';objectControl.enabled=false;objectControl.dragging=false;objectControl.axis=null;drag=null
        move.callbacks.commit(xyz)
      })
    }
    objectControl.attach(objMeshes[id]);objectHelper.visible=true
    objectControl.enabled=objectMove?.phase!=='pending'
    return true
  }
  function setTeachTarget(pose,callback=null) {
    if(!pose) {
      if(teachControl) { teachControl.detach();teachControl.enabled=false;teachHelper.visible=false }
      if(teachTarget)teachTarget.visible=false
      api.onTeachTarget=null
      return
    }
    if(!Array.isArray(pose)||pose.length!==6||!pose.every(Number.isFinite))throw new Error('示教目标需为六维有限数值')
    setObjectTarget(null)
    if(!teachControl) {
      teachTarget=new THREE.Mesh(new THREE.SphereGeometry(.012,12,8),new THREE.MeshBasicMaterial({color:0x22d3ee,depthTest:false}))
      teachTarget.name='teaching-target'
      scene.add(teachTarget)
      teachControl=new TransformControls(camera,el);teachControl.setMode('translate');teachControl.setSpace('world');teachControl.setSize(.7)
      teachHelper=teachControl.getHelper();teachHelper.name='teaching-gizmo';scene.add(teachHelper)
      teachControl.addEventListener('dragging-changed',()=>{drag=null})
      teachControl.addEventListener('objectChange',()=>{
        const p=teachTarget.position
        api.onTeachTarget?.([p.x*1000,-p.z*1000,p.y*1000])
      })
    }
    api.onTeachTarget=callback;teachTarget.visible=true;teachHelper.visible=true;teachControl.enabled=true
    if(!teachControl.dragging)teachTarget.position.set(pose[0]/1000,pose[2]/1000,-pose[1]/1000)
    teachControl.attach(teachTarget)
  }
  const preventContextMenu = (event) => event.preventDefault()
  listen(el,'contextmenu', preventContextMenu)
  listen(el,'dragover',onPlacementDragOver);listen(el,'drop',onPlacementDrop)
  const onPointerDown = (e) => {
    if(placement){e.preventDefault();movePlacement(e);return}
    if((teachControl?.enabled && teachControl.axis) || (objectControl?.enabled && objectControl.axis) || objectControl?.dragging)return
    if (![0,1,2].includes(e.button)) return
    e.preventDefault()
    drag = { mode:e.button===0?'orbit':'pan', x:e.clientX, y:e.clientY, t:orbit.theta, p:orbit.phi,
      target:orbit.target.clone(), right:new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,0),
      up:new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,1), down:[e.clientX,e.clientY] }
    el.setPointerCapture?.(e.pointerId)
  }
  listen(el,'pointerdown', onPointerDown)
  const onPointerUp = (e) => {
    if(placement){if(e.button===0 && movePlacement(e))void commitPlacement();return}
    // 位移不到 4px 当作"点了一下"，用来选中摆放物；否则是转视角
    if (drag && drag.mode === 'orbit' && drag.down && Math.hypot(e.clientX - drag.down[0], e.clientY - drag.down[1]) < 4) {
      const r = el.getBoundingClientRect()
      const nd = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1,
                                   -((e.clientY - r.top) / r.height) * 2 + 1)
      const rc = new THREE.Raycaster()
      rc.setFromCamera(nd, camera)
      const hit = rc.intersectObjects(objGroup.children, false)[0]
      api.onPick && api.onPick(hit ? hit.object.userData.id : null)
    }
    drag = null
  }
  listen(window,'pointerup', onPointerUp)
  // An in-flight save owns the lock until the controller finishes or rejects it.
  // Browser input cancellation cannot silently unlock a second concurrent save.
  const cancelDrag = event => { drag = null;if(!(event?.type==='pointercancel' && placement?.callbacks.nativeDrag))cancelPlacement();if(objectMove?.phase==='dragging')cancelObjectMove() }
  // Normal pointerUp releases capture before TransformControls emits mouseUp.
  // Loss alone is not cancellation; unexpected loss relies on cancel/blur/hidden.
  const onLostPointerCapture = () => { drag = null }
  listen(window,'pointercancel', cancelDrag)
  listen(window,'blur', cancelDrag)
  listen(el,'lostpointercapture', onLostPointerCapture)
  const onPointerMove = (e) => {
    if(placement){movePlacement(e);return}
    if(teachControl?.dragging || objectControl?.dragging)return
    if (!drag) return
    if (drag.mode === 'pan') {
      const scale=2*orbit.r*Math.tan(THREE.MathUtils.degToRad(camera.fov/2))/Math.max(el.clientHeight,1)
      orbit.target.copy(drag.target).addScaledVector(drag.right,-(e.clientX-drag.x)*scale).addScaledVector(drag.up,(e.clientY-drag.y)*scale)
      orbit.target.clampScalar(-10,10)
    } else {
      orbit.theta = drag.t + (e.clientX - drag.x) * 0.006
      orbit.phi = drag.p - (e.clientY - drag.y) * 0.006
    }
    applyOrbit()
  }
  listen(window,'pointermove', onPointerMove)
  const onWheel = (e) => { e.preventDefault(); if(objectControl?.dragging)return;orbit.r *= 1 + Math.sign(e.deltaY) * 0.09; applyOrbit() }
  listen(el,'wheel', onWheel, { passive: false })

  // ---------------- 插值缓冲 ----------------
  let current = new Array(6).fill(0)
  const presentation = createJointPresentation()
  let ghostEvery = 0
  const pushSample = q => presentation.push(q)
  const sampleAt = now => presentation.sample(now)
  const lastAngles = Array(6).fill(NaN)
  const setAngles = (q) => {
    if (lastAngles.every((v,i)=>Math.abs(v-q[i])<1e-6)) return
    applyAngles(robotRoot,jointNodes,q)
    for(let i=0;i<6;i++) lastAngles[i]=q[i]
  }

  /* 按轴健康给连杆上色：静态时几乎全灰（ISA-101 的"彩色只留给异常"），
     接近限位 / 有轴级错误码才亮起来。材质只有本体在用，不会污染假手。 */
  const linkByJoint = movable.map((j) => linkNodes[j.child])
  let healthKey = ''
  let limitYellow=90,limitRed=95,limitAlarm=100
  const hardLimitMaterials=new Set()
  function setSafetyThresholds(settings) {
    const yellow=settings?.safety_yellow_pct ?? 90, red=settings?.safety_red_pct ?? 95,alarm=settings?.safety_alarm_pct ?? 100
    if(yellow!==limitYellow || red!==limitRed || alarm!==limitAlarm) {limitYellow=yellow;limitRed=red;limitAlarm=alarm;healthKey=''}
  }
  for (const m of Object.values(matCache)) {
    m.emissive = new THREE.Color(0x000000)
    m.emissiveIntensity = 0
  }
  function setHealth(axes) {
    if (!axes || !axes.length) return
    const key = axes.map((a) => `${a.err}|${a.pct >= limitAlarm ? 10 : a.pct >= limitRed ? 9 : a.pct >= limitYellow ? 8 : 0}`).join(',')
    if (key === healthKey) return
    healthKey = key
    axes.forEach((a, i) => {
      const node = linkByJoint[i]
      if (!node) return
      /* 只按"轴级错误码 + 限位占用"上色。nAxisStatus 不参与：
         实测未上电时它是 0/1 而不是 3，拿它当故障会把整条臂染红（第一版就翻了这个车）。 */
      let c = 0x000000
      if (a.err) c = 0xff4d6d
      else if (a.pct >= limitRed) c = 0xff4d6d
      else if (a.pct >= limitYellow) c = 0xffb020
      node.traverse((o) => {
        const material=Array.isArray(o.material)?o.material[0]:o.material
        if (!o.isMesh || !o.userData.link || material === defaultMat || !material?.emissive) return
        material.emissive.setHex(c)
        material.emissiveIntensity = c ? 0.65 : 0
        if(a.pct>=limitAlarm)hardLimitMaterials.add(material);else hardLimitMaterials.delete(material)
      })
    })
  }

  const tcpObj = new THREE.Mesh(new THREE.SphereGeometry(0.012, 12, 12),
    new THREE.MeshBasicMaterial({ color: '#e4553f' }))
  handDisplayFrame.add(tcpObj)
  let tcpTranslation=[0,0,.005]
  let tcpTranslationVersion=0
  function setTcpFrame(frame) {
    if(Array.isArray(frame?.translation_mm) && frame.translation_mm.length===3 && frame.translation_mm.every(Number.isFinite)
      && frame.translation_mm.some((v,i)=>v/1000!==tcpTranslation[i])) {
      tcpTranslation=frame.translation_mm.map(v=>v/1000);tcpTranslationVersion++
    }
  }
  const tcpPosition=new THREE.Vector3(),frameInverse=new THREE.Matrix4()
  const cachedTcpPosition=new THREE.Vector3()
  let cachedPoseVersion=-1,cachedTranslationVersion=-1
  function tcpWorld(out=new THREE.Vector3()) {
    const node = linkNodes['elfin_end_link'] || linkNodes['elfin_link6']
    if (!node) return out.set(0,0,0)
    // This fixed display frame never follows camera/workspace direction. Only
    // robot pose or configured TCP translation can change its local TCP position.
    if(cachedPoseVersion!==robotPoseVersion || cachedTranslationVersion!==tcpTranslationVersion) {
      node.updateWorldMatrix(true,false)
      frameInverse.copy(handDisplayFrame.matrixWorld).invert()
      cachedTcpPosition.fromArray(tcpTranslation).applyMatrix4(node.matrixWorld).applyMatrix4(frameInverse)
      cachedPoseVersion=robotPoseVersion;cachedTranslationVersion=tcpTranslationVersion
    }
    return out.copy(cachedTcpPosition)
  }

  // Camera-content corners 1..4 correspond to the front X/Z plane of this
  // virtual TCP box. This visualization never changes the real safety fence.
  const handWorkspace = new THREE.Group()
  handWorkspace.visible = false
  handDisplayFrame.add(handWorkspace)
  let handWorkspaceKey = '', handBounds = null, handWorkspaceCenter = new THREE.Vector3(), handTarget = null, handActual = null, handFollowLine=null
  const workspaceInverse=new THREE.Quaternion()
  const followLineLast=new Float64Array(6).fill(NaN)
  const handBoxPreview=new THREE.Group();handBoxPreview.visible=false;handDisplayFrame.add(handBoxPreview)

  // 追踪：范围盒 + 模拟手掌。颜色和跟随的青色区分开，一眼能看出这是"镜头能转到哪"。
  const trackWorkspace = new THREE.Group()
  trackWorkspace.visible = false
  handDisplayFrame.add(trackWorkspace)
  let trackWorkspaceKey = ''
  function clearTrackWorkspace() {
    for (const child of [...trackWorkspace.children]) {
      trackWorkspace.remove(child)
      child.geometry?.dispose?.()
      child.material?.dispose?.()
    }
  }
  function setTrackWorkspace(enclosure, visible, direction = 0) {
    const ok = !!visible && !!enclosure
      && ['min','max'].every(k => Array.isArray(enclosure[k]) && enclosure[k].length === 3 && enclosure[k].every(Number.isFinite))
    trackWorkspace.visible = !!ok
    if (!ok) return
    const lo = enclosure.min, hi = enclosure.max
    if (lo.some((v, i) => v >= hi[i])) return
    const key = JSON.stringify([lo, hi, direction])
    if (key === trackWorkspaceKey) return
    clearTrackWorkspace(); trackWorkspaceKey = key
    const center = lo.map((v, i) => (v + hi[i]) / 2)
    const size = hi.map((v, i) => (v - lo[i]) / 1000)
    trackWorkspace.position.copy(handPoint(center))
    // 盒体放进一个按 direction 旋转的子组，编号牌留在外层用世界坐标摆：
    // 跟随的工作区就是这么做的，两边朝向才会一致。
    const frame = new THREE.Group()
    frame.rotation.y = THREE.MathUtils.degToRad([0, 90, 180, 270].includes(direction) ? direction : 0)
    trackWorkspace.add(frame)
    const box = new THREE.BoxGeometry(size[0], size[2], size[1])
    frame.add(new THREE.LineSegments(new THREE.EdgesGeometry(box),
      new THREE.LineBasicMaterial({ color: '#2fd6f0', transparent: true, opacity: .65 })))
    box.dispose()
    const face = new THREE.Mesh(new THREE.PlaneGeometry(size[0], size[2]),
      new THREE.MeshBasicMaterial({ color: '#2fd6f0', transparent: true, opacity: .025, side: THREE.DoubleSide, depthWrite: false }))
    frame.add(face)
    // 角点取法与跟随一致：深度方向取中面，四个角是"前上、前下、后下、后上"。
    const corners = [[-size[0] / 2, 0, size[2] / 2], [size[0] / 2, 0, size[2] / 2],
                     [size[0] / 2, 0, -size[2] / 2], [-size[0] / 2, 0, -size[2] / 2]]
    const angle = frame.rotation.y, cos = Math.cos(angle), sin = Math.sin(angle)
    corners.forEach((p, i) => {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 64
      const c = canvas.getContext('2d')
      c.fillStyle = '#081725'; c.fillRect(8, 8, 48, 48)
      c.strokeStyle = '#2fd6f0'; c.strokeRect(8, 8, 48, 48)
      c.fillStyle = '#b5f6ff'; c.font = 'bold 34px monospace'
      c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(String(i + 1), 32, 34)
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: new THREE.CanvasTexture(canvas), depthTest: false, transparent: true }))
      // 局部 (x, y, z) 绕竖直轴转到世界：场景里 y 是高度、z 是深度（取负）。
      const wx = p[0] * cos - p[1] * sin
      const wy = p[0] * sin + p[1] * cos
      sprite.position.set(wx, p[2], -wy)
      sprite.scale.set(.07, .07, .07)
      trackWorkspace.add(sprite)
    })
  }
  const trackHand = new THREE.Group()
  trackHand.visible = false
  handDisplayFrame.add(trackHand)
  {
    const mat = new THREE.MeshBasicMaterial({ color: '#69f0ae', transparent: true, opacity: .92 })
    trackHand.add(new THREE.Mesh(new THREE.SphereGeometry(.03, 16, 12), mat))
    for (let i = 0; i < 5; i++) {
      const finger = new THREE.Mesh(new THREE.CylinderGeometry(.005, .005, .048, 6), mat)
      finger.position.set(-0.028 + i * 0.014, 0.042, 0)
      finger.rotation.z = (i - 2) * 0.12
      trackHand.add(finger)
    }
  }
  function setTrackHand(xyz, visible) {
    const ok = !!visible && Array.isArray(xyz) && xyz.length === 3 && xyz.every(Number.isFinite)
    trackHand.visible = !!ok
    if (ok) trackHand.position.copy(handPoint(xyz))
  }
  let previewBounds=null,previewDirection=0,previewKey=''
  function setHandBoxPreview(box,direction=0) {
    const valid=box && ['min','max'].every(k=>Array.isArray(box[k]) && box[k].length===3 && box[k].every(Number.isFinite)) && box.min.every((v,i)=>v<box.max[i])
    handBoxPreview.visible=!!valid
    previewBounds=valid?{min:box.min.slice(),max:box.max.slice()}:null
    previewDirection=[0,90,180,270].includes(direction)?direction:0
    if(!valid) return
    handWorkspace.visible=false
    handBoxPreview.rotation.y=THREE.MathUtils.degToRad(previewDirection)
    const center=box.min.map((v,i)=>(v+box.max[i])/2)
    handBoxPreview.position.copy(handPoint(center))
    const key=JSON.stringify(box)
    if(key===previewKey) return
    handBoxPreview.traverse(o=>{o.geometry?.dispose();o.material?.map?.dispose();o.material?.dispose()});handBoxPreview.clear();previewKey=key
    const size=box.max.map((v,i)=>(v-box.min[i])/1000)
    const geometry=new THREE.BoxGeometry(size[0],size[2],size[1])
    const edges=new THREE.LineSegments(new THREE.EdgesGeometry(geometry),new THREE.LineBasicMaterial({color:'#2fd6f0'}))
    geometry.dispose();edges.position.set(0,0,0);handBoxPreview.add(edges)
    const previewCorners=[[box.min[0],center[1],box.max[2]],[box.max[0],center[1],box.max[2]],
      [box.max[0],center[1],box.min[2]],[box.min[0],center[1],box.min[2]]]
    previewCorners.forEach((p,i)=>{
      const canvas=document.createElement('canvas');canvas.width=canvas.height=64
      const c=canvas.getContext('2d');c.fillStyle='#081725';c.fillRect(8,8,48,48)
      c.strokeStyle='#2fd6f0';c.lineWidth=3;c.strokeRect(8,8,48,48)
      c.fillStyle='#b5f6ff';c.font='bold 34px monospace';c.textAlign='center';c.textBaseline='middle';c.fillText(String(i+1),32,34)
      const texture=new THREE.CanvasTexture(canvas)
      const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,depthTest:false,transparent:true}))
      sprite.position.copy(handPoint(p).sub(handPoint(center)));sprite.scale.set(.07,.07,.07);handBoxPreview.add(sprite)
    })
  }
  const handPoint = (p) => new THREE.Vector3(p[0]/1000,p[2]/1000,-p[1]/1000)
  function clearHandWorkspace() {
    handWorkspace.traverse(o=>{
      o.geometry?.dispose()
      for (const m of (Array.isArray(o.material)?o.material:[o.material]).filter(Boolean)) {
        m.map?.dispose(); m.dispose()
      }
    })
    handWorkspace.clear(); handWorkspaceKey = ''; handBounds = null
    handTarget = handActual = handFollowLine = null
  }
  function setHandWorkspace(state, visible) {
    const privateVisible=!!visible && !!state?.configured
    // Show the established follow workspace as soon as it is configured;
    // active finger motion is not required for users to see the bounds.
    handWorkspace.visible = privateVisible && !handBoxPreview.visible
    const direction=privateVisible && [0,90,180,270].includes(state?.direction) ? state.direction : 0
    handWorkspace.rotation.y=THREE.MathUtils.degToRad(direction)
    workspaceInverse.setFromAxisAngle(upAxis,-handWorkspace.rotation.y)
    robotRoot.position.set(0,0,0)
    if (!privateVisible) return
    const lo=(state.bounds || state.outer_bounds)?.min, hi=(state.bounds || state.outer_bounds)?.max
    if (!lo || !hi || [...lo,...hi].some(v=>!Number.isFinite(v)) || lo.some((v,i)=>v>=hi[i])) return
    const key=JSON.stringify([lo,hi,state.corners,direction])
    if (key!==handWorkspaceKey) {
      clearHandWorkspace(); handWorkspaceKey=key; handBounds={min:lo.slice(),max:hi.slice()}
      const center=lo.map((v,i)=>(v+hi[i])/2), size=hi.map((v,i)=>(v-lo[i])/1000)
      handWorkspaceCenter.copy(handPoint(center));handWorkspace.position.copy(handWorkspaceCenter)
      const box=new THREE.BoxGeometry(size[0],size[2],size[1])
      const edges=new THREE.LineSegments(new THREE.EdgesGeometry(box),new THREE.LineBasicMaterial({color:'#2fd6f0',transparent:true,opacity:.65}))
      edges.position.set(0,0,0); handWorkspace.add(edges); box.dispose()
      const plane=new THREE.Mesh(new THREE.PlaneGeometry(size[0],size[2]),new THREE.MeshBasicMaterial({color:'#2fd6f0',transparent:true,opacity:.025,side:THREE.DoubleSide,depthWrite:false}))
      plane.position.set(0,0,0); handWorkspace.add(plane)
      const sourceCorners=state.corners || [[lo[0],center[1],hi[2]],[hi[0],center[1],hi[2]],[hi[0],center[1],lo[2]],[lo[0],center[1],lo[2]]]
      const corners=sourceCorners
      const undoRotation=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),-handWorkspace.rotation.y)
      corners.forEach((p,i)=>{
        const canvas=document.createElement('canvas');canvas.width=canvas.height=64
        const c=canvas.getContext('2d');c.fillStyle='#081725';c.fillRect(8,8,48,48)
        c.strokeStyle='#2fd6f0';c.strokeRect(8,8,48,48);c.fillStyle='#b5f6ff';c.font='bold 34px monospace';c.textAlign='center';c.textBaseline='middle';c.fillText(String(i+1),32,34)
        const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas),depthTest:false,transparent:true}))
        sprite.position.copy(handPoint(p).sub(handWorkspaceCenter).applyQuaternion(undoRotation));sprite.scale.set(.07,.07,.07);handWorkspace.add(sprite)
      })
      handTarget=new THREE.Mesh(new THREE.SphereGeometry(.009,12,12),new THREE.MeshBasicMaterial({color:'#2fd6f0',transparent:true,opacity:.5}))
      handActual=new THREE.Mesh(new THREE.SphereGeometry(.006,12,12),new THREE.MeshBasicMaterial({color:'#e7fcff'}))
      handWorkspace.add(handTarget,handActual)
      const lineGeometry=new THREE.BufferGeometry()
      lineGeometry.setAttribute('position',new THREE.Float32BufferAttribute(new Float32Array(6),3))
      handFollowLine=new THREE.Line(lineGeometry,new THREE.LineBasicMaterial({color:'#2fd6f0',transparent:true,opacity:.7}))
      handWorkspace.add(handFollowLine)
      followLineLast.fill(NaN)
    }
    // Telemetry is world-space: compensate the box-only rotation so markers
    // stay at their real coordinates instead of appearing to rotate with it.
    const validPoint=p=>Array.isArray(p) && p.length>=3 && Number.isFinite(p[0]) && Number.isFinite(p[1]) && Number.isFinite(p[2])
    if (validPoint(state.target)) handTarget.position.set(state.target[0]/1000,state.target[2]/1000,-state.target[1]/1000).sub(handWorkspaceCenter).applyQuaternion(workspaceInverse)
    if (validPoint(state.actual)) handActual.position.set(state.actual[0]/1000,state.actual[2]/1000,-state.actual[1]/1000).sub(handWorkspaceCenter).applyQuaternion(workspaceInverse)
  }

  function fitHandWorkspace() {
    if (!handBounds) return
    robotRoot.updateMatrixWorld(true);handWorkspace.updateMatrixWorld(true)
    const box=new THREE.Box3().setFromObject(robotRoot).union(new THREE.Box3().setFromObject(handWorkspace))
    const size=box.getSize(new THREE.Vector3())
    orbit.target.copy(box.getCenter(new THREE.Vector3()))
    orbit.theta=HOME.theta;orbit.phi=HOME.phi
    orbit.r=Math.max(1,Math.min(12,Math.max(size.y,size.x/camera.aspect)*.72/Math.tan(THREE.MathUtils.degToRad(camera.fov/2))))
    applyOrbit()
    // Fit the projected WHOLE box into the space left of the right-side docks.
    // World X width alone misses the projected depth of an oblique 3D box.
    const stage=container.closest('.stage')?.getBoundingClientRect() || container.getBoundingClientRect()
    const dock=document.querySelector('.stage .dock')?.getBoundingClientRect()
    const rightReserve=Math.min(stage.width*.44,Math.max(332,dock?.width || 390)+12)
    const maxX=1-2*rightReserve/stage.width
    const corners=[]
    for(const x of [box.min.x,box.max.x]) for(const y of [box.min.y,box.max.y]) for(const z of [box.min.z,box.max.z]) corners.push(new THREE.Vector3(x,y,z))
    for(let i=0;i<32 && orbit.r<12;i++) {
      camera.updateMatrixWorld(true)
      if(corners.every(p=>{const v=p.clone().project(camera);return v.x>=-.94 && v.x<=maxX && v.y>=-.91 && v.y<=.88})) break
      orbit.r=Math.min(12,orbit.r*1.07);applyOrbit()
    }
  }

  const ro = new ResizeObserver(() => api.resize())
  initializationCleanup.push(()=>ro.disconnect())
  ro.observe(container)

  let running = true
  let raf = 0
  let dpr = Math.min(window.devicePixelRatio || 1, 2)
  let slow = 0
  let minInterval = 0
  let lastSkip = 0
  let idleSince = 0
  const renderCosts = []
  const updateCosts=new Float64Array(120),submitCosts=new Float64Array(120)
  let costCursor=0,costSamples=0,submittedFrames=0
  let lastFrameStart=null
  let longTaskObserver=null
  const virtualEye=createVirtualCamera({renderer,scene,spec,flange:linkNodes.elfin_end_link || linkNodes.elfin_link6,
    hidden:()=>[hud,fenceObj,handWorkspace,trackWorkspace,trackHand,handBoxPreview,ghost.root,visionGroup,planLine,drawingRoot,grid,capsuleGroup,trail,groundRiskPlane]})
  initializationCleanup.push(()=>virtualEye.dispose())
  function observeLongTasks() {
    if(longTaskObserver || typeof PerformanceObserver!=='function' ||
      !PerformanceObserver.supportedEntryTypes?.includes('longtask')) return
    try {
      const observer=new PerformanceObserver(list=>{
        if(longTaskObserver!==observer || !running || sceneDisposed) return
        for(const entry of list.getEntries()) if(entry.entryType==='longtask') recordMetric('longtask',entry.duration)
      })
      longTaskObserver=observer
      // New entries only: do not replay long tasks from an earlier scene/session.
      longTaskObserver.observe({type:'longtask',buffered:false})
    } catch { longTaskObserver?.disconnect();longTaskObserver=null }
  }
  /* 三条省电/降帧：
     * 标签页不可见时彻底停 rAF —— 后台空转 60fps 是最没意义的耗电
     * 连续帧时间超标就把 pixelRatio 降到 1（集显 + 大屏最常见）
     * 臂和摆放物都不动时降到 30fps：监控台盯着静止的机器人不需要 60 次重绘 */
  const IDLE_S = 1.2
  function movingLike() {
    return presentation.moving()
  }
  function loop() {
    if (!running) return
    raf = requestAnimationFrame(loop)
    const t0 = performance.now()
    if(lastFrameStart!==null) recordMetric('scene.frame',t0-lastFrameStart)
    lastFrameStart=t0
    if (minInterval && t0 - lastSkip < minInterval * 1000 - .5) return
    lastSkip = t0
    const now = t0 / 1000
    for(const material of hardLimitMaterials)material.emissiveIntensity=.25+.6*(.5+.5*Math.sin(t0*.018))
    if(groundSafety==='alarm')groundRiskMat.opacity=.025+.055*(.5+.5*Math.sin(t0*.018))
    const animationDt=animationLastTime===null?0:Math.min(.1,now-animationLastTime)
    animationLastTime=now
    if(toolState) {toolAnimation?.update(toolState,animationDt);ghostAnimation?.update(toolState,animationDt)}
    if (fenceSafety === 'alarm') {
      const pulse = .18 + .72 * (0.5 + 0.5 * Math.sin(t0 * .018))
      fenceEdges.opacity = pulse
      fenceMat.opacity = .012 + .055 * pulse
    }
    const q = sampleAt(now)
    visionAnimating=false
    for(const mesh of visionMeshes.values()){
      mesh.position.lerp(mesh.userData.target,.35);mesh.scale.lerp(mesh.userData.targetScale,.35)
      const angle=Math.atan2(Math.sin(mesh.userData.targetYaw-mesh.rotation.y),Math.cos(mesh.userData.targetYaw-mesh.rotation.y))
      mesh.rotation.y+=angle*.35
      if(mesh.position.distanceToSquared(mesh.userData.target)>1e-9 || mesh.scale.distanceToSquared(mesh.userData.targetScale)>1e-9 || Math.abs(angle)>.001)visionAnimating=true
    }
    current = q
    setAngles(q)
    presentObjectAttachment()
    const p = tcpWorld(tcpPosition)
    tcpObj.position.copy(p)
    if(handWorkspace.visible && handActual && handTarget) {
      handActual.position.copy(p).sub(handWorkspaceCenter).applyQuaternion(workspaceInverse)
      if(handFollowLine) {
        const positions=handFollowLine.geometry.attributes.position
        const a=handActual.position,b=handTarget.position
        if(a.x!==followLineLast[0] || a.y!==followLineLast[1] || a.z!==followLineLast[2] ||
            b.x!==followLineLast[3] || b.y!==followLineLast[4] || b.z!==followLineLast[5]) {
          positions.setXYZ(0,a.x,a.y,a.z);positions.setXYZ(1,b.x,b.y,b.z)
          followLineLast[0]=a.x;followLineLast[1]=a.y;followLineLast[2]=a.z
          followLineLast[3]=b.x;followLineLast[4]=b.y;followLineLast[5]=b.z
          positions.needsUpdate=true
        }
      }
    }
    if(ghost.root.visible && ghostContact) ghostMat.opacity=api.ghostOpacity*(.45+.55*(.5+.5*Math.sin(t0*.018)))
    pushTrail(p)
    const drawingAnimating=drawingVisual.tick(t0)
    const submitStart=performance.now()
    renderer.render(scene, camera)
    const completed=performance.now(),cost=completed-t0
    recordMetric('scene.update',submitStart-t0)
    recordMetric('scene.render',completed-submitStart)
    updateCosts[costCursor]=submitStart-t0;submitCosts[costCursor]=completed-submitStart
    costCursor=(costCursor+1)%120;costSamples=Math.min(120,costSamples+1);submittedFrames++
    renderCosts.push(cost)
    if (renderCosts.length > 120) renderCosts.shift()
    slow = cost > 22 ? slow + 1 : Math.max(0, slow - 1)
    if (slow > 90 && dpr > 1) { dpr = 1; renderer.setPixelRatio(1); slow = 0 }
    if (movingLike() || visionAnimating || drawingAnimating) idleSince = 0
    else if (!idleSince) idleSince = now
    minInterval = idleSince && now - idleSince > IDLE_S ? 1 / 30 : 0
  }
  const onVis = () => {
    if (document.hidden) { cancelDrag();running = false; cancelAnimationFrame(raf);lastFrameStart=null;longTaskObserver?.disconnect();longTaskObserver=null }
    else if (!running) { running = true; lastSkip = 0; idleSince = 0;lastFrameStart=null;observeLongTasks();loop() }
  }
  listen(document,'visibilitychange', onVis)

  const api = {
    configureVirtualCamera:virtualEye.configure,
    captureVirtualFrame:virtualEye.capture,
    virtualCameraPose:virtualEye.pose,
    setDrawingBounds(value) { return sceneDisposed?false:drawingVisual.setBounds(value) },
    setDrawingState(value) {
      if(!value || !value.owner || sceneDisposed)return false
      if(value.mode!=='sim'){drawingVisual.clear(value.owner);return false}
      if(value.path?.length>1)drawingVisual.setPreview(value)
      return drawingVisual.sample(value)
    },
    clearDrawing(owner) { return owner==null?false:drawingVisual.clear(owner) },
    drawingInfo:drawingVisual.info,
    setVisionObjects,
    async prepareFirstFrame() {
      checkCancelled()
      // 正确：缺失模型不能用空场景冒充加载完成。
      const missing=Object.entries(spec.links).filter(([name,link])=>link.visual?.mesh && !geoms[name]).map(([name])=>name)
      if(missing.length) throw new Error('模型资源缺失：'+missing.join('、'))
      await renderer.compileAsync(scene,camera)
      checkCancelled()
      renderer.render(scene,camera)
      if(window.__elfinScene!==api) {
        window.__elfinScene=api
        window.dispatchEvent(new CustomEvent('elfin-scene-ready'))
      }
    },
    setSafetyThresholds,
    setGroundSafety,
    setToolState,
    setTcpFrame,
    setFollowLatency(active) { presentation.setFollowing(active) },
    setHandBoxPreview,
    groundDirectionsInfo() { return groundDirectionLabels.map(({name,angle,xyz})=>({name,angle,xyz:xyz.slice()})) },
    setHandWorkspace,
    fitHandWorkspace,
    handWorkspaceInfo() {
      const p=tcpWorld()
      return {visible:handWorkspace.visible || handBoxPreview.visible,bounds:previewBounds || handBounds,appliedBounds:handBounds,preview:handBoxPreview.visible,children:handWorkspace.children.length,basePosition:robotRoot.position.toArray(),
        direction:THREE.MathUtils.radToDeg((handBoxPreview.visible?handBoxPreview:handWorkspace).rotation.y),
        robotRotation:robotRoot.getWorldQuaternion(new THREE.Quaternion()).toArray(),
        toolTcp:p?[p.x*1000,-p.z*1000,p.y*1000]:null}
    },
    setSkin,
    skinInfo() { return { skin, jointLights: 0, glowJoints:[],
      animated:false, phases:[], bloom:false,
      covers:Object.fromEntries(Object.entries(geoms).map(([name,g])=>[name,g.userData.jointCaps])) } },
    model: asset.id,
    setToolVisual,
    toolInfo() {
      const finishes=new Map()
      toolRoot?.traverse(o=>{ if(o.isMesh) for(const m of (Array.isArray(o.material)?o.material:[o.material])) {
        if(m?.isMeshStandardMaterial) finishes.set(m.uuid,{surface:m.userData.surface || 'silver',color:'#'+m.color.getHexString(),
          metalness:m.metalness,roughness:m.roughness})
      } })
      return { loaded: !!toolRoot, id:toolRoot?.userData.id || (toolRoot ? 'vacuum-reference' : 'none'),
        referenceOnly: !!toolRoot?.userData.referenceOnly,skin,finishes:[...finishes.values()],animation:toolAnimation?.snapshot() || {supported:false},
        tcp_mm:toolRoot?.userData.tcp_mm || null,
        dimensions: toolRoot?.userData.dimensions || null,rotation:toolRoot?.userData.rotation || null }
    },
    getView() { return { theta: orbit.theta, phi: orbit.phi, r: orbit.r, target: orbit.target.toArray(), fov:camera.fov } },
    cameraBoundsInfo() { return {bounds:null,
      eye:camera.position.toArray(),target:orbit.target.toArray(),theta:orbit.theta,phi:orbit.phi,r:orbit.r,fov:camera.fov} },
    framingInfo() {
      const base=robotRoot.getWorldPosition(new THREE.Vector3()).project(camera)
      return {offsetFraction:.13,fov:camera.fov,view:{...camera.view},baseScreen:[(base.x+1)/2,(1-base.y)/2]}
    },
    onViewChange(callback) { viewChanged = callback || (() => {}) },
    setView(value) {
      if (!value || !Array.isArray(value.target) || value.target.length !== 3 ||
          ![value.theta,value.phi,value.r,...value.target].every((v)=>typeof v==='number' && Number.isFinite(v)) || value.r<=0 || value.r>12 || (value.fov!==undefined&&(!Number.isFinite(value.fov)||value.fov<10||value.fov>110))) return false
      if(value.fov!==undefined){camera.fov=value.fov;camera.updateProjectionMatrix()}
      Object.assign(orbit,{theta:value.theta,phi:value.phi,r:value.r}); orbit.target.fromArray(value.target); applyOrbit(); return true
    },
    ghostOpacity: 0.34,
    showCapsules: false,
    setAngles(q) { pushSample(q) },
    setImmediate(q) { if(presentation.reset(q)) { current=q.slice(); setAngles(q) } },
    setFence,
    setFenceSafety,
    setFenceVisible,
    fenceInfo() { return { visible: !!fenceObj?.visible, opacity: fenceMat.opacity,
      level: fenceSafety, color: fenceEdges.color.getHexString() } },
    fitView(q = current) {
      applyAngles(robotRoot, jointNodes, q)
      const box = new THREE.Box3().setFromObject(robotRoot)
      if (box.isEmpty()) return
      // Fit from a known lens, not a wide lens left behind by a previous view.
      camera.fov = 22
      camera.updateProjectionMatrix()
      const size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3())
      orbit.theta = HOME.theta; orbit.phi = HOME.phi
      // Fit only the physical display arm/tool, never the huge workspace box.
      // 留白 2.05：机器人约占画幅一半，顶端留出余量（旧值 1.45 会顶到上边缘）。
      const halfView = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))
      orbit.r = Math.max(1.3, Math.max(size.y, Math.hypot(size.x, size.z) / camera.aspect) / (2 * halfView) * HOME.fitPadding)
      // 目标点压到臂体重心以下，机器人整体上移、脚下露出地面；不改机器人世界坐标。
      orbit.target.copy(center)
      orbit.target.y = center.y - size.y * HOME.groundBias
      applyOrbit()
    },
    setObjects,
    setObjectTarget,
    beginPlacement,cancelPlacement,
    placementInfo(){return placement?{pending:placement.pending,visible:placement.mesh.visible,xyz:placement.xyz?.slice(),opacity:placement.mesh.material.opacity}:null},
    finishObjectMove,
    cancelObjectMove,
    setTeachTarget,
    onTeachTarget:null,
    setPlanPath(points,{owner=null}={}) {
      planOwner=owner
      const list=points || [], count=list.length
      let changed=count!==planCount
      if(count>planCapacity) {
        while(planCapacity<count) planCapacity*=2
        planLine.geometry.dispose();planLine.geometry=new THREE.BufferGeometry()
        planLine.geometry.setAttribute('position',new THREE.BufferAttribute(new Float32Array(planCapacity*3),3).setUsage(THREE.DynamicDrawUsage))
        changed=true
      }
      const position=planLine.geometry.attributes.position
      for(let i=0;i<count;i++) {
        const p=list[i],x=Math.fround(p[0]/1000),y=Math.fround(p[2]/1000),z=Math.fround(-p[1]/1000)
        if(position.getX(i)!==x || position.getY(i)!==y || position.getZ(i)!==z) {
          position.setXYZ(i,x,y,z);changed=true
        }
      }
      if(changed) {
        position.needsUpdate=true;planLine.geometry.setDrawRange(0,count)
        // Bounds must exclude unused capacity after a path shrinks.
        const box=new THREE.Box3()
        for(let i=0;i<count;i++) box.expandByPoint(tcpPosition.set(position.getX(i),position.getY(i),position.getZ(i)))
        planLine.geometry.boundingSphere=box.isEmpty()?new THREE.Sphere(new THREE.Vector3(),0):box.getBoundingSphere(new THREE.Sphere())
      }
      planCount=count;planLine.visible=count>1
      if(!count)planOwner=null
      return true
    },
    clearPlanPath(owner) {
      if(owner==null || planOwner!==owner)return false
      this.setPlanPath([])
      return true
    },
    markCollisions,
    onPick: null,
    setCapsules(list) { setCapsules(list, api.showCapsules) },
    // 追踪：琥珀色范围盒 + 绿色模拟手掌，只在追踪页打开时显示。
    setTrackWorkspace(enclosure, visible, direction) { setTrackWorkspace(enclosure, visible, direction) },
    setTrackHand(xyz, visible) { setTrackHand(xyz, visible) },
    // 自检用：不依赖 requestAnimationFrame（隐藏标签页里 rAF 不跑），
    // 直接写角度并强制刷新矩阵，这样无头环境也能确定性量几何。
    poseNow(q) {
      setAngles(q)
      const box = new THREE.Box3().setFromObject(robotRoot)
      const end = linkNodes['elfin_end_link'] || linkNodes['elfin_link6']
      const ep = end ? end.getWorldPosition(new THREE.Vector3()) : null
      return {
        min: box.min.toArray().map((v) => +v.toFixed(3)),
        max: box.max.toArray().map((v) => +v.toFixed(3)),
        endXYZ: ep ? [+(ep.x * 1000).toFixed(1), +(ep.y * 1000).toFixed(1), +(ep.z * 1000).toFixed(1)] : null,
        nodes: Object.keys(jointNodes).length, links: Object.keys(linkNodes).length,
        meshes: (() => { let n = 0; robotRoot.traverse((o) => { if (o.isMesh) n++ }); return n })(),
      }
    },
    clearTrail,
    exportHistory() { return {version:1,trail:Array.from(trailPos.subarray(0,trailN*3))} },
    restoreHistory(state) {
      if(state?.version!==1 || !Array.isArray(state.trail) || state.trail.length%3 || state.trail.length>TRAIL_CAP*3 ||
        !state.trail.every(v=>Number.isFinite(v) && Math.abs(v)<100))return false
      trailN=Math.min(state.trail.length/3,trailLen)
      trailPos.set(state.trail.slice(-trailN*3));trailGeo.setDrawRange(0,trailN)
      trailGeo.attributes.position.needsUpdate=true
      return true
    },
    setTrailLen,
    setHealth,
    clearGhosts,
    // 自检用：残影当前有多少帧、每帧几个网格
    setGhost,
    // 自检用：残影臂是否可见、它有几个网格、本体有几个网格
    ghostInfo() { return { visible: ghost.root.visible, meshes: ghostMeshes,contact:ghostContact,opacity:ghostMat.opacity,risk:ghostRisk,
                           realMeshes: (() => { let n = 0; robotRoot.traverse((o) => { if (o.isMesh) n++ }); return n })() } },
    resize() {
      const w = Math.max(320, container.clientWidth), h = Math.max(240, container.clientHeight)
      renderer.setSize(w, h)
      drawingVisual.resize(w,h)
      camera.aspect = w / h
      frameLeft(w,h)
      camera.updateProjectionMatrix()
    },
    resourceInfo() { return { geometries:renderer.info?.memory?.geometries ?? null,
      textures:renderer.info?.memory?.textures ?? null, programs:renderer.info?.programs?.length ?? null } },
    dprInfo() { return { dpr, minInterval, trailN, trailLen, samples:renderCosts.length,
      averageRenderMs:renderCosts.length ? renderCosts.reduce((a,b)=>a+b,0)/renderCosts.length : null,
      // CPU timings only: WebGL submission is not a GPU execution timer or FPS.
      averageSceneUpdateMs:costSamples ? updateCosts.reduce((a,b)=>a+b,0)/costSamples : null,
      averageRenderSubmitMs:costSamples ? submitCosts.reduce((a,b)=>a+b,0)/costSamples : null,
      submittedFrames } },
    dispose() {
      cancelPlacement(true);el.removeEventListener('dragover',onPlacementDragOver);el.removeEventListener('drop',onPlacementDrop)
      for(const mesh of Object.values(objMeshes))clearObjectAsset(mesh)
      if (sceneDisposed) return
      drawingVisual.dispose()
      virtualEye.dispose()
      setObjectTarget(null)
      if(objectControl) { objectControl.dispose();scene.remove(objectHelper) }
      if(teachControl) { teachControl.detach();teachControl.dispose();scene.remove(teachHelper) }
      clearHandWorkspace()
      sceneDisposed = true; toolRevision++; clearTool()
      environment.dispose()
      running = false
      longTaskObserver?.disconnect();longTaskObserver=null;lastFrameStart=null
      cancelAnimationFrame(raf)
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', cancelDrag)
      window.removeEventListener('blur', cancelDrag)
      el.removeEventListener('lostpointercapture', onLostPointerCapture)
      window.removeEventListener('pointermove', onPointerMove)
      el.removeEventListener('contextmenu', preventContextMenu)
      el.removeEventListener('pointerdown', onPointerDown)
      el.removeEventListener('wheel', onWheel)
      const geometries = new Set(Object.values(geoms))
      Object.values(visionGeometries).forEach(geometry=>geometries.add(geometry))
      const materials = new Set([...Object.values(matCache), defaultMat, ...robotBodyMaterials.values(), socketMat, capsuleMaterial])
      scene.traverse((object) => {
        if (object.geometry) geometries.add(object.geometry)
        const list = Array.isArray(object.material) ? object.material : [object.material]
        list.filter(Boolean).forEach((material) => materials.add(material))
      })
      geometries.forEach((geometry) => geometry.dispose())
      const textures = new Set()
      materials.forEach((material) => { Object.values(material).filter((v) => v?.isTexture).forEach((t) => textures.add(t)); material.dispose() })
      textures.forEach((texture) => texture.dispose())
      if (window.__elfinScene === api) delete window.__elfinScene
      ro.disconnect(); renderer.dispose(); renderer.domElement.remove()
    },
    resetView() { api.fitView(current) },
  }
  initializedApi=api
  await setSkin(options.skin || 'factory')
  checkCancelled()
  recordMetric('scene.load',performance.now()-loadStarted)
  observeLongTasks()
  loop()
  return api
  } catch(error) {
    let fullyDisposed=false
    if(initializedApi) {
      try { initializedApi.dispose();fullyDisposed=true } catch { /* Continue releasing partial initialization below. */ }
    }
    if(!fullyDisposed) {
      for(const cleanup of initializationCleanup.reverse()) {
        try { cleanup() } catch { /* One disposal failure must not strand other resources. */ }
      }
    }
    throw error
  }
}
