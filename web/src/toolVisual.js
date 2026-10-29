import * as THREE from 'three'
import { vacuumDimensions, libraryToolEntry } from './visualAssets.js'
import { createToolAnimation } from './toolKinematics.js'
export { createToolAnimation, validateToolAnimation, toolClosure } from './toolKinematics.js'

// Same neutral silver/black PBR family as scene.js's robot, using its existing
// RoomEnvironment reflections. Rubber remains rough, not painted shiny metal.
export const TOOL_FINISHES = Object.freeze({
  silver: Object.freeze({ color: '#c2c9cc', metalness: .65, roughness: .28 }),
  'black-metal': Object.freeze({ color: '#171d23', metalness: .5, roughness: .34 }),
  rubber: Object.freeze({ color: '#161b20', metalness: .02, roughness: .8 }),
  'white-rubber': Object.freeze({ color: '#edf2f4', metalness: .02, roughness: .8 }),
  brass: Object.freeze({ color: '#b89955', metalness: .72, roughness: .32 }),
  pneumatic: Object.freeze({ color: '#158fc4', metalness: .02, roughness: .42 }),
  connector: Object.freeze({ color: '#22865b', metalness: .05, roughness: .52 }),
  'translucent-cup': Object.freeze({ color: '#dce8e4', metalness: 0, roughness: .38,
    transparent: true, opacity: .72, depthWrite: false }),
})
export function applyToolSkin(root, skin = 'factory') {
  if (!['factory', 'cyber'].includes(skin)) throw new Error('未知工具皮肤')
  if (!root) return root
  const seen = new Set()
  root.traverse((object) => {
    for (const material of (Array.isArray(object.material) ? object.material : [object.material]).filter(Boolean)) {
      if (seen.has(material) || !material.isMeshStandardMaterial) continue
      seen.add(material)
      const surface = material.userData?.surface || 'silver'
      const finish = TOOL_FINISHES[surface] || TOOL_FINISHES.silver
      // Only silver follows the robot skin. Structural black metal, black
      // gripper rubber and white suction cups retain their semantic finish.
      const cyberSilver = skin === 'cyber' && surface === 'silver'
      material.color.set(cyberSilver ? '#8d9aaa' : finish.color)
      material.metalness = cyberSilver ? .6 : finish.metalness
      material.roughness = cyberSilver ? .3 : finish.roughness
      material.envMapIntensity = 1
      material.emissive?.set('#000000')
      material.emissiveIntensity = 0
      material.needsUpdate = true
    }
  })
  root.userData.skin = skin
  return root
}

export function buildVacuumReference(value) {
  const p = vacuumDimensions(value), root = new THREE.Group()
  root.name = 'photo-reference-dual-vacuum'
  const surfaces = Object.fromEntries(Object.entries(TOOL_FINISHES).map(([key, finish]) => {
    const material = new THREE.MeshStandardMaterial(finish)
    material.userData.surface = key
    return [key, material]
  }))
  const mm = (v) => v / 1000
  // All parts remain in the declared tool envelope. Dimensions and TCP are not
  // secretly extended in the renderer; the existing tool transaction owns them.
  function mesh(name, geometry, xyz, surface = 'silver') {
    const part = new THREE.Mesh(geometry, surfaces[surface])
    part.name = name; part.position.set(...xyz.map(mm)); root.add(part)
    return part
  }
  function cylinder(name, top, bottom, height, xyz, surface = 'silver', segments = 24) {
    const geometry = new THREE.CylinderGeometry(mm(top), mm(bottom), mm(height), segments)
    geometry.rotateX(Math.PI / 2)
    return mesh(name, geometry, xyz, surface)
  }
  const box = (name, size, xyz, surface) => mesh(name, new THREE.BoxGeometry(...size.map(mm)), xyz, surface)
  const h = p.mountHeight, radius = Math.min(20, p.width * .4, p.length * .2)
  if (h > 0) {
    cylinder('lower-connector-body', radius * .86, radius * .86, h * .76, [0, 0, h * .5])
    cylinder('upper-adapter-ring', radius, radius, h * .12, [0, 0, h * .06])
    cylinder('lower-adapter-ring', radius, radius, h * .12, [0, 0, h * .94])
    // Photo reference: valve and push fittings alongside the lower connector,
    // not another 50mm wrist cylinder (the robot mesh already owns the wrist).
    box('vacuum-valve-block', [radius * .6, radius * .9, h * .48],
      [radius * .5, radius * .52, h * .5], 'black-metal')
    for (const side of [-1, 1]) {
      const fitting = cylinder('valve-push-fitting-' + side, radius * .16, radius * .16,
        radius * .3, [side * radius * .52, -radius * .63, h * .55], 'connector', 12)
      fitting.rotation.x = Math.PI / 2
      const curve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(side * radius * .52, -radius * .63, h * .55),
        new THREE.Vector3(side * p.spacing * .25, -p.width * .3, h * .68),
        new THREE.Vector3(side * p.spacing * .5, 0, h + p.thickness * .25),
      ].map(point => point.multiplyScalar(.001)))
      mesh('blue-air-hose-' + side, new THREE.TubeGeometry(curve, 18,
        mm(Math.min(1.5, p.width * .025, h * .025)), 6, false), [0, 0, 0], 'pneumatic')
    }
  }
  // Rounded plate and real through holes. Keep the exact declared outer size:
  // inset the contour by the small bevel instead of extending collision bounds.
  const bevel=Math.min(.25,p.thickness*.08,p.width*.01,p.length*.01)
  const halfX=p.length/2-bevel,halfY=p.width/2-bevel
  const corner=Math.min(3,p.width*.1,p.length*.05),outline=new THREE.Shape()
  outline.moveTo(mm(-halfX+corner),mm(-halfY));outline.lineTo(mm(halfX-corner),mm(-halfY))
  outline.quadraticCurveTo(mm(halfX),mm(-halfY),mm(halfX),mm(-halfY+corner))
  outline.lineTo(mm(halfX),mm(halfY-corner));outline.quadraticCurveTo(mm(halfX),mm(halfY),mm(halfX-corner),mm(halfY))
  outline.lineTo(mm(-halfX+corner),mm(halfY));outline.quadraticCurveTo(mm(-halfX),mm(halfY),mm(-halfX),mm(halfY-corner))
  outline.lineTo(mm(-halfX),mm(-halfY+corner));outline.quadraticCurveTo(mm(-halfX),mm(-halfY),mm(-halfX+corner),mm(-halfY))
  // Hole distances are photo proportions, not factory drilling coordinates.
  const featureScale=Math.min(1,p.width/50,p.length/120)
  const holes=[[-12,9,2.8],[10,9,2.8],[-10,-10,2.8],[12,-10,2.8],
    [-31,-13,3.7],[33,13,3.7],[0,18,1.2],[1,-18,1.2]]
  for(const [x,y,r] of holes){
    const hole=new THREE.Path();hole.absarc(mm(x*featureScale),mm(y*featureScale),mm(r*featureScale),0,Math.PI*2,true)
    outline.holes.push(hole)
  }
  const plateGeometry=new THREE.ExtrudeGeometry(outline,{depth:mm(p.thickness-2*bevel),
    bevelEnabled:true,bevelThickness:mm(bevel),bevelSize:mm(bevel),bevelSegments:2,steps:1,curveSegments:12})
  const plate=mesh('dual-vacuum-mounting-plate',plateGeometry,[0,0,h+bevel])
  plate.userData.photoReference={cornerRadiusMm:corner,holes:holes.map(([x,y,r])=>[x*featureScale,y*featureScale,r*featureScale]),measured:false}
  // Recessed black inserts stop the bright connector behind the plate showing
  // through. Leave the opening recessed: do not paint a flat dot on the face.
  for(const [index,[x,y,r]] of holes.slice(0,4).entries()){
    const depth=p.thickness*.6,outer=mm(r*featureScale*.96),inner=outer*.86
    const wallGeometry=new THREE.LatheGeometry([
      new THREE.Vector2(inner,0),new THREE.Vector2(inner,mm(depth)),
      new THREE.Vector2(outer,mm(depth)),new THREE.Vector2(outer,0),
    ],24)
    wallGeometry.rotateX(Math.PI/2)
    mesh('black-hole-inner-wall-'+index,wallGeometry,
      [x*featureScale,y*featureScale,h+p.thickness*.2],'black-metal')
    mesh('black-hole-recess-base-'+index,new THREE.CircleGeometry(outer,24),
      [x*featureScale,y*featureScale,h+p.thickness*.22],'black-metal')
  }
  for(const [index,[x,y]] of holes.slice(4,6).entries()){
    const depth=Math.min(.8,p.thickness*.2)
    cylinder('recessed-brass-screw-'+index,3.35*featureScale,3.35*featureScale,depth,
      [x*featureScale,y*featureScale,h+p.thickness-depth/2-.02*p.thickness],'brass',24)
    mesh('screw-hex-socket-'+index,new THREE.CircleGeometry(mm(1.45*featureScale),6),
      [x*featureScale,y*featureScale,h+p.thickness-.015*p.thickness],'black-metal')
  }
  for (const side of [-1, 1]) {
    const x = side * p.spacing / 2, top = p.mountHeight + p.thickness
    const d = p.cupDiameter, height = p.cupHeight
    const stages = [
      ['threaded-stem', .32, d * .18, d * .18, 'silver', 20],
      ['hex-locknut', .12, d * .32, d * .32, 'brass', 6],
      ['push-collar', .18, d * .3, d * .3, 'connector', 24],
      ['cup-neck', .16, d * .23, d * .18, 'translucent-cup', 24],
    ]
    const assembly = new THREE.Group(); assembly.name = 'suction-assembly-' + side
    root.add(assembly)
    let z = top
    for (const [name, fraction, bottom, upper, surface, segments] of stages) {
      const length = height * fraction
      const part = cylinder(name + '-' + side, bottom, upper, length, [x, 0, z + length / 2], surface, segments)
      assembly.attach(part); z += length
    }
    // Open, double-wall suction bell with a rolled contact lip, rather than a
    // solid cone/cylinder painted white. Its tip still ends at exactly 30mm.
    const bellHeight=height*.22
    const profile=[[.23,0],[.27,.2],[.49,.76],[.5,1],[.42,1],[.40,.76],[.16,0]]
      .map(([r,level])=>new THREE.Vector2(mm(d*r),mm(bellHeight*level)))
    const bellGeometry=new THREE.LatheGeometry(profile,32)
    bellGeometry.rotateX(Math.PI/2)
    assembly.attach(mesh('open-suction-bell-'+side,bellGeometry,[x,0,z],'translucent-cup'))
    // Shared instanced ridges per stem: visible threaded metal without creating
    // a draw call for every individual turn.
    const threadGeometry=new THREE.TorusGeometry(mm(d*.18),mm(d*.012),5,20)
    const thread=new THREE.InstancedMesh(threadGeometry,surfaces.silver,6)
    thread.name='stem-thread-ridges-'+side
    for(let i=0;i<6;i++)thread.setMatrixAt(i,new THREE.Matrix4().makeTranslation(mm(x),0,mm(top+height*(.025+i*.05))))
    assembly.add(thread)
  }
  root.userData.referenceOnly = true
  root.userData.dimensions = p
  root.userData.provenance = { source: 'user-photo', measured_mm: { mountHeight: 40, cupHeight: 30 },
    unmeasured: ['connectorDiameter', 'fittings', 'hoses', 'cupDiameter','plateCornerRadius','plateHoleSpacing'], controllerCalibrated: false }
  // Dispose only materials actually used by this assembly.
  const used = new Set(); root.traverse(part => { if (part.material) used.add(part.material) })
  for (const material of Object.values(surfaces)) if (!used.has(material)) material.dispose()
  return root
}

export function disposeAsset(root) {
  if (!root) return
  const geometry = new Set(), materials = new Set(), textures = new Set()
  root.traverse((o) => {
    o.animation?.dispose()
    if (o.isInstancedMesh) o.dispose()
    if (o.geometry) geometry.add(o.geometry)
    for (const m of (Array.isArray(o.material) ? o.material : [o.material]).filter(Boolean)) {
      materials.add(m)
      Object.values(m).filter((v) => v?.isTexture).forEach((t) => textures.add(t))
    }
  })
  const bitmaps = new Set()
  textures.forEach((texture) => { if (typeof texture.image?.close === 'function') bitmaps.add(texture.image) })
  geometry.forEach((g) => g.dispose()); materials.forEach((m) => m.dispose()); textures.forEach((t) => t.dispose())
  bitmaps.forEach((bitmap) => bitmap.close())
}

export async function readToolFile(file, unit) {
  if (!file || file.size > 25 * 1024 * 1024) throw new Error('工具文件无效或超过 25 MB')
  if (!['mm', 'm'].includes(unit)) throw new Error('请选择正确模型单位')
  const bytes = await file.arrayBuffer()
  let root
  if (/\.stl$/i.test(file.name)) {
    const { STLLoader } = await import('three/examples/jsm/loaders/STLLoader.js')
    const geometry = new STLLoader().parse(bytes)
    root = new THREE.Group()
    root.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: '#b9c6c4', metalness: .5, roughness: .5 })))
  } else if (/\.glb$/i.test(file.name)) {
    const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js')
    // No untrusted URLs, sidecar downloads or relative path access from imported GLBs.
    const manager = new THREE.LoadingManager()
    manager.setURLModifier((url) => { if (/^(blob:|data:)/i.test(url)) return url; throw new Error('仅支持内嵌资源 GLB') })
    root = (await new GLTFLoader(manager).parseAsync(bytes, '')).scene
  } else throw new Error('只支持 GLB 和 STL')
  root.scale.multiplyScalar(unit === 'mm' ? .001 : 1)
  root.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(root), size = box.getSize(new THREE.Vector3())
  if (box.isEmpty() || ![...box.min, ...box.max].every(Number.isFinite) || size.length() < .0001 || size.length() > 5) {
    disposeAsset(root)
    throw new Error('模型为空、坐标无效或尺寸异常；请检查单位（工具尺寸须为 0.1 mm～5 m）')
  }
  return root
}

// Reject external sidecars before invoking any loader. The distributed GLBs do
// not require textures, decoders, CDN requests, ROS or additional mesh files.
export function inspectLibraryGlb(bytes) {
  if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 28 || bytes.byteLength > 25 * 1024 * 1024) {
    throw new Error('末端 GLB 为空或超过 25 MB')
  }
  const view = new DataView(bytes)
  if (view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2 ||
      view.getUint32(8, true) !== bytes.byteLength || view.getUint32(16, true) !== 0x4e4f534a) {
    throw new Error('末端 GLB 头部无效')
  }
  const jsonLength = view.getUint32(12, true)
  if (20 + jsonLength > bytes.byteLength) throw new Error('末端 GLB 数据不完整')
  const document = JSON.parse(new TextDecoder().decode(new Uint8Array(bytes, 20, jsonLength)))
  if (document.asset?.version !== '2.0' || !Array.isArray(document.buffers) || document.buffers.length !== 1 ||
      document.buffers.some((buffer) => buffer.uri !== undefined) || (document.images || []).length ||
      (document.extensionsRequired || []).length || (document.extensionsUsed || []).length) {
    throw new Error('末端工具仅支持不含外部资源或解码器的自包含 GLB')
  }
  return document
}

export async function readLibraryTool(value) {
  const entry = await libraryToolEntry(value)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 20000)
  let bytes
  try {
    const response = await fetch(entry.asset, { signal: controller.signal, credentials: 'same-origin', redirect: 'error' })
    if (!response.ok) throw new Error(`末端模型加载失败 (${response.status})`)
    if (Number(response.headers?.get('content-length')) > 25 * 1024 * 1024) throw new Error('末端模型超过 25 MB')
    bytes = await response.arrayBuffer()
  } finally { clearTimeout(timer) }
  const document = inspectLibraryGlb(bytes)
  if (!globalThis.crypto?.subtle) throw new Error('无法校验末端模型，请使用本机页面或 HTTPS')
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  const hash = [...new Uint8Array(digest)].map((v) => v.toString(16).padStart(2, '0')).join('')
  if (hash !== entry.sha256) throw new Error('末端模型校验失败，请重新打包资源')
  const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js')
  const manager = new THREE.LoadingManager()
  manager.setURLModifier(() => { throw new Error('末端 GLB 不允许请求附属资源') })
  const root = (await new GLTFLoader(manager).parseAsync(bytes, '')).scene
  try {
    root.updateMatrixWorld(true)
    const bounds = new THREE.Box3().setFromObject(root)
    if (bounds.isEmpty() || ![...bounds.min, ...bounds.max].every(Number.isFinite) ||
        [...bounds.min].some((v, i) => Math.abs(v * 1000 - entry.bounds_mm.min[i]) > .1) ||
        [...bounds.max].some((v, i) => Math.abs(v * 1000 - entry.bounds_mm.max[i]) > .1)) {
      throw new Error('末端模型包络与工具目录不一致')
    }
    // Canonical local metadata, never supplied TCP/bounds from the caller.
    Object.assign(root.userData, {
      kind: 'library', id: entry.id, name: entry.name, tcp_mm: [...entry.tcp_mm],
      bounds_mm: { min: [...entry.bounds_mm.min], max: [...entry.bounds_mm.max] },
      calibrated: false, units: 'm', source: entry.source, license: entry.license,
    })
    applyToolSkin(root)
    // Keep controller functions/references out of userData: Three's clone()
    // JSON-copies userData, and each ghost requires an independent controller.
    const animation = createToolAnimation(root, document.asset.extras?.animation)
    Object.defineProperty(root, 'animation', { value: animation, configurable: true })
    root.userData.animation_available = animation.supported
    if (animation.supported) root.userData.collision_bounds_mm = animation.collisionBoundsMm
    return root
  } catch (error) { disposeAsset(root); throw error }
}
