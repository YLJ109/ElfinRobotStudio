/* Three.js 场景：官方 URDF 导出的层级 + STL 网格 + 围栏 + 残影 + 真机插值。
 *
 * 单位用米（和 URDF 一致），相机和栅格按 1.2m 高的臂展取景。
 * 真机 20Hz 数据在本地缓冲后按渲染帧插值，避免"一顿一顿"。
 */
import * as THREE from 'three'

const INTERP_DELAY = 0.12      // 渲染滞后 120ms，换取连续
const BUFFER_MAX = 0.5

export async function createScene(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(container.clientWidth, container.clientHeight)
  renderer.shadowMap.enabled = false
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color('#0b1416')
  scene.fog = new THREE.Fog('#0b1416', 6, 14)

  /* 2.5D 口径：窄视场角 + 远距离机位，把透视压平成轴测感。
     正交会丢掉"纵深那口气"，广角又会把臂展拉变形，所以走中间这条。 */
  const camera = new THREE.PerspectiveCamera(22, container.clientWidth / container.clientHeight, 0.05, 80)
  camera.position.set(2.6, 1.9, 2.9)
  camera.lookAt(0, 0.55, 0)

  scene.add(new THREE.HemisphereLight('#cfe9e6', '#0b1416', 1.05))
  const key = new THREE.DirectionalLight('#ffffff', 1.25)
  key.position.set(3, 5, 2)
  scene.add(key)
  const rim = new THREE.DirectionalLight('#48b7af', 0.6)
  rim.position.set(-3, 1.5, -3)
  scene.add(rim)

  const grid = new THREE.GridHelper(4, 40, '#2c736e', '#18312f')
  grid.position.y = 0
  scene.add(grid)

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

  // ---------------- 机器人 ----------------
  const spec = await (await fetch('./models/elfin5/robot.json')).json()
  const [{ GLTFLoader }, { MeshoptDecoder }] = await Promise.all([
    import('three/examples/jsm/loaders/GLTFLoader.js'), import('meshoptimizer/decoder'),
  ])
  const gltfLoader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder)
  const geoms = {}
  const matCache = {}
  for (const [name, m] of Object.entries(spec.materials || {})) {
    const [r, g, b, a] = m.rgba
    matCache[name] = new THREE.MeshStandardMaterial({
      color: new THREE.Color(r, g, b), metalness: 0.55, roughness: 0.45,
      transparent: a < 1, opacity: a,
    })
  }
  const defaultMat = new THREE.MeshStandardMaterial({ color: '#b9c6c4', metalness: 0.5, roughness: 0.5 })

  await Promise.all(Object.entries(spec.links).map(async ([lname, l]) => {
    if (!l.visual || !l.visual.mesh) return
    const url = `./models/elfin5/${l.visual.mesh}`
    if (!geoms[lname]) {
      try {
        const lod = localStorage.getItem('huayan.modelQuality') === 'low' ? '.low.glb' : '.glb'
        const response = await fetch(url.replace(/\.STL$/i, lod))
        if (!response.ok) throw new Error('GLB unavailable')
        const parsed = await gltfLoader.parseAsync(await response.arrayBuffer(), './models/elfin5/')
        parsed.scene.updateMatrixWorld(true)
        let g = null
        parsed.scene.traverse((object) => {
          if (object.isMesh && !g) g = object.geometry.clone().applyMatrix4(object.matrixWorld)
          if (object.geometry) object.geometry.dispose()
          if (object.material) object.material.dispose()
        })
        if (!g) throw new Error('GLB has no geometry')
        geoms[lname] = g
      } catch (e) {
        try {
          const { STLLoader } = await import('three/examples/jsm/loaders/STLLoader.js')
          const response = await fetch(url)
          if (!response.ok) throw new Error('STL unavailable')
          geoms[lname] = new STLLoader().parse(await response.arrayBuffer())
        } catch (fallback) {
          console.warn('网格加载失败', url, fallback)
          return
        }
      }
    }
  }))

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
        const mesh = new THREE.Mesh(geoms[joint.child], materialOf(l))
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

  const real = buildRobot((l) => matCache[l.visual.material] || defaultMat)
  const robotRoot = real.root
  const jointNodes = real.jn
  const linkNodes = real.ln

  /* 假手 = 另一台半透明臂，几何体与本体共享，只多一套 Object3D 和一个材质。
     一个模型两种用途：残影预览（青）与时间轴回放（紫），颜色就是它们的身份牌。 */
  const GHOST_COLOR = { ghost: '#4fd8cf', playback: '#9b7bff' }
  const ghostMat = new THREE.MeshBasicMaterial({
    color: GHOST_COLOR.ghost, transparent: true, opacity: 0.34, depthWrite: false,
  })
  const ghost = buildRobot(() => ghostMat)
  ghost.root.visible = false
  let ghostMode = 'ghost'
  /* 每帧 new 6 个 Vector3 是白扔的 GC 压力，轴向量是常量，先算好 */
  const jointAxes = movable.map((j) => new THREE.Vector3(...j.axis).normalize())
  function applyAngles(root, jn, q) {
    for (let i = 0; i < movable.length; i++) {
      const node = jn[movable[i].name]
      if (!node) continue
      node.setRotationFromAxisAngle(jointAxes[i], 0)
      node.rotateOnAxis(jointAxes[i], (q[i] || 0) * Math.PI / 180)
    }
    root.updateMatrixWorld(true)
  }

  /* 残影 = 另一台假手，跟着预设滑块摆；点执行后真机开过来，到位时把它收掉。
     depthWrite=false 是为了半透明的假手不会挡住本体。 */
  let ghostMeshes = 0
  ghost.root.traverse((o) => { if (o.isMesh) ghostMeshes++ })
  function setGhost(q, mode) {
    if (!q) { ghost.root.visible = false; return }
    const m = mode || 'ghost'
    if (m !== ghostMode) {
      ghostMode = m
      ghostMat.color.set(GHOST_COLOR[m] || GHOST_COLOR.ghost)
    }
    ghost.root.visible = true
    ghostMat.opacity = api.ghostOpacity
    applyAngles(ghost.root, ghost.jn, q)
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
  scene.add(trail)
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
    trailPos.set([p.x, p.y, p.z], trailN * 3)
    trailN++
    trailGeo.setDrawRange(0, trailN)
    trailGeo.attributes.position.needsUpdate = true
  }
  function clearTrail() { trailN = 0; trailGeo.setDrawRange(0, 0) }

  // ---------------- 围栏 / 地面 / 胶囊体 ----------------
  const fenceMat = new THREE.MeshBasicMaterial({
    color: '#48b7af', transparent: true, opacity: 0.055, side: THREE.DoubleSide, depthWrite: false,
  })
  const fenceEdges = new THREE.LineBasicMaterial({ color: '#2c736e' })
  let fenceObj = null
  let fenceKey = ''
  function setFence(f) {
    const key = f && f.enabled ? JSON.stringify(f) : ''
    if (key === fenceKey) return          // 每帧重建 Box+Edges 是纯浪费
    fenceKey = key
    if (fenceObj) { scene.remove(fenceObj); fenceObj.traverse((o) => o.geometry && o.geometry.dispose()) }
    if (!f || !f.enabled) return
    const size = urdfToThree([(f.x[1] - f.x[0]) / 1000, (f.y[1] - f.y[0]) / 1000, (f.z[1] - f.z[0]) / 1000])
    const ctr = urdfToThree([(f.x[0] + f.x[1]) / 2000, (f.y[0] + f.y[1]) / 2000, (f.z[0] + f.z[1]) / 2000])
    const g = new THREE.Group()
    const box = new THREE.BoxGeometry(size[0], size[1], size[2])
    g.add(new THREE.Mesh(box, fenceMat))
    g.add(new THREE.LineSegments(new THREE.EdgesGeometry(box), fenceEdges))
    g.position.set(...ctr)
    scene.add(g)
    fenceObj = g
  }

  let capsuleGroup = new THREE.Group()
  scene.add(capsuleGroup)
  const capsuleGeometry = new THREE.CylinderGeometry(1, 1, 1, 8, 1, true)
  const capsuleMaterial = new THREE.MeshBasicMaterial({
    color: '#e0a33b', wireframe: true, transparent: true, opacity: 0.35,
  })
  const capsulePool = []
  function setCapsules(list, show) {
    capsuleGroup.visible = !!show
    if (!show) return
    capsulePool.forEach((mesh) => { mesh.visible = false })
    list.forEach((c, index) => {
      const pa = urdfToThree([c[0] / 1000, c[1] / 1000, c[2] / 1000])
      const pb = urdfToThree([c[3] / 1000, c[4] / 1000, c[5] / 1000])
      const a = new THREE.Vector3(...pa)
      const b = new THREE.Vector3(...pb)
      const dir = b.clone().sub(a)
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
      mesh.position.copy(a).add(dir.clone().multiplyScalar(0.5))
      mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize())
    })
  }

  // ---------------- 摆放物（纯仿真道具，坐标同样是 URDF mm / z 朝上） ----------------
  const objGroup = new THREE.Group()
  scene.add(objGroup)
  const planLine = new THREE.Line(new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ color: '#e0a33b', transparent: true, opacity: 0.8 }))
  scene.add(planLine)
  const objMeshes = {}
  function objGeometry(o) {
    const s = o.size.map((v) => Math.max(v, 1) / 1000)
    if (o.kind === 'sphere') return new THREE.SphereGeometry(s[0], 20, 14)
    if (o.kind === 'cylinder') return new THREE.CylinderGeometry(s[0], s[0], s[2], 22)
    return new THREE.BoxGeometry(s[0], s[2], s[1])
  }
  function setObjects(list, selId) {
    const seen = new Set()
    for (const o of (list || [])) {
      seen.add(o.id)
      let m = objMeshes[o.id]
      if (!m) {
        m = new THREE.Mesh(objGeometry(o), new THREE.MeshStandardMaterial({
          color: o.color || '#4fd8cf', metalness: 0.15, roughness: 0.7,
        }))
        m.userData.id = o.id
        m.userData.color = o.color
        m.userData.sel = false
        objGroup.add(m)
        objMeshes[o.id] = m
      } else {
        if (m.geometry.type !== ({ sphere: 'SphereGeometry', cylinder: 'CylinderGeometry', box: 'BoxGeometry' }[o.kind])) {
          m.geometry.dispose(); m.geometry = objGeometry(o)
        }
        if (m.userData.color !== o.color) {
          m.material.color.set(o.color || '#4fd8cf')
          m.userData.color = o.color
        }
      }
      const p = urdfToThree(o.xyz.map((v) => v / 1000))
      m.position.set(...p)
      // URDF 圆柱绕 Z 立着，Three 的圆柱默认沿 Y —— 摆到同一朝向
      m.rotation.set(o.kind === 'cylinder' ? Math.PI / 2 : 0, 0, 0)
      const sel = o.id === selId
      if (m.userData.sel !== sel) {
        m.userData.sel = sel
        m.material.emissive.setHex(sel ? 0x2fd6f0 : 0x000000)
        m.material.emissiveIntensity = sel ? 0.55 : 0
      }
      m.userData.collided = false
    }
    for (const id of Object.keys(objMeshes)) {
      if (seen.has(id)) continue
      const m = objMeshes[id]
      objGroup.remove(m); m.geometry.dispose(); m.material.dispose()
      delete objMeshes[id]
    }
  }
  function markCollisions(hits) {
    const on = new Set((hits || []).map((h) => h.id))
    for (const [id, m] of Object.entries(objMeshes)) {
      const c = on.has(id)
      if (c === m.userData.collided) continue
      m.userData.collided = c
      m.material.emissive.setHex(c ? 0xe4553f : (m.userData.hit ? 0x2fd6f0 : 0x000000))
      m.material.emissiveIntensity = c ? 0.9 : (m.userData.hit ? 0.55 : 0)
    }
  }

  // ---------------- 交互（轨道控制，不引额外包） ----------------
  /* 2.5D 默认机位：r 拉远配 22° 视场角 —— 构图和原来一致，但透视被压平成轴测 */
  const HOME = { theta: 0.72, phi: 1.16, r: 5.3 }
  const orbit = { theta: HOME.theta, phi: HOME.phi, r: HOME.r,
                  target: new THREE.Vector3(0, 0.62, 0) }
  function applyOrbit() {
    orbit.phi = Math.max(0.12, Math.min(Math.PI / 2 - 0.02, orbit.phi))
    orbit.r = Math.max(1.0, Math.min(12, orbit.r))
    camera.position.set(
      orbit.target.x + orbit.r * Math.sin(orbit.phi) * Math.cos(orbit.theta),
      orbit.target.y + orbit.r * Math.cos(orbit.phi),
      orbit.target.z + orbit.r * Math.sin(orbit.phi) * Math.sin(orbit.theta))
    camera.lookAt(orbit.target)
  }
  applyOrbit()
  let drag = null
  const el = renderer.domElement
  el.addEventListener('pointerdown', (e) => {
    drag = { x: e.clientX, y: e.clientY, t: orbit.theta, p: orbit.phi, down: [e.clientX, e.clientY] }
  })
  const onPointerUp = (e) => {
    // 位移不到 4px 当作"点了一下"，用来选中摆放物；否则是转视角
    if (drag && drag.down && Math.hypot(e.clientX - drag.down[0], e.clientY - drag.down[1]) < 4) {
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
  window.addEventListener('pointerup', onPointerUp)
  const onPointerMove = (e) => {
    if (!drag) return
    orbit.theta = drag.t + (e.clientX - drag.x) * 0.006
    orbit.phi = drag.p - (e.clientY - drag.y) * 0.006
    applyOrbit()
  }
  window.addEventListener('pointermove', onPointerMove)
  el.addEventListener('wheel', (e) => { e.preventDefault(); orbit.r *= 1 + Math.sign(e.deltaY) * 0.09; applyOrbit() }, { passive: false })

  // ---------------- 插值缓冲 ----------------
  const buffer = []
  let current = new Array(6).fill(0)
  let ghostEvery = 0
  function pushSample(q, t) {
    buffer.push({ q: q.slice(), t: t || performance.now() / 1000 })
    const cut = (performance.now() / 1000) - BUFFER_MAX
    while (buffer.length > 2 && buffer[0].t < cut) buffer.shift()
  }
  function sampleAt(now) {
    const want = now - INTERP_DELAY
    if (buffer.length === 0) return current
    if (buffer.length === 1) return buffer[0].q
    let i = buffer.findIndex((s) => s.t >= want)
    if (i === -1) return buffer[buffer.length - 1].q
    if (i <= 0) return buffer[0].q
    const a = buffer[i - 1], b = buffer[i]
    const k = (want - a.t) / Math.max(1e-4, b.t - a.t)
    return a.q.map((v, idx) => v + (b.q[idx] - v) * Math.max(0, Math.min(1, k)))
  }
  const setAngles = (q) => applyAngles(robotRoot, jointNodes, q)

  /* 按轴健康给连杆上色：静态时几乎全灰（ISA-101 的"彩色只留给异常"），
     接近限位 / 有轴级错误码才亮起来。材质只有本体在用，不会污染假手。 */
  const linkByJoint = movable.map((j) => linkNodes[j.child])
  let healthKey = ''
  for (const m of Object.values(matCache)) {
    m.emissive = new THREE.Color(0x000000)
    m.emissiveIntensity = 0
  }
  function setHealth(axes) {
    if (!axes || !axes.length) return
    const key = axes.map((a) => `${a.err}|${a.pct >= 90 ? 9 : a.pct >= 80 ? 8 : 0}`).join(',')
    if (key === healthKey) return
    healthKey = key
    axes.forEach((a, i) => {
      const node = linkByJoint[i]
      if (!node) return
      /* 只按"轴级错误码 + 限位占用"上色。nAxisStatus 不参与：
         实测未上电时它是 0/1 而不是 3，拿它当故障会把整条臂染红（第一版就翻了这个车）。 */
      let c = 0x000000
      if (a.err) c = 0xff4d6d
      else if (a.pct >= 90) c = 0xff4d6d
      else if (a.pct >= 80) c = 0xffb020
      node.traverse((o) => {
        if (!o.isMesh || o.material === defaultMat) return
        o.material.emissive.setHex(c)
        o.material.emissiveIntensity = c ? 0.22 : 0
      })
    })
  }

  const tcpObj = new THREE.Mesh(new THREE.SphereGeometry(0.012, 12, 12),
    new THREE.MeshBasicMaterial({ color: '#e4553f' }))
  scene.add(tcpObj)
  function tcpWorld() {
    const node = linkNodes['elfin_end_link'] || linkNodes['elfin_link6']
    if (!node) return new THREE.Vector3()
    return node.getWorldPosition(new THREE.Vector3())
  }

  const ro = new ResizeObserver(() => api.resize())
  ro.observe(container)

  let running = true
  let raf = 0
  let dpr = Math.min(window.devicePixelRatio || 1, 2)
  let slow = 0
  let minInterval = 0
  let lastSkip = 0
  let idleSince = 0
  /* 三条省电/降帧：
     * 标签页不可见时彻底停 rAF —— 后台空转 60fps 是最没意义的耗电
     * 连续帧时间超标就把 pixelRatio 降到 1（集显 + 大屏最常见）
     * 臂和摆放物都不动时降到 30fps：监控台盯着静止的机器人不需要 60 次重绘 */
  const IDLE_S = 1.2
  function movingLike() {
    const n = buffer.length
    if (n < 2) return false
    const a = buffer[n - 2].q, b = buffer[n - 1].q
    return a.some((v, i) => Math.abs(v - b[i]) > 0.004)
  }
  function loop() {
    if (!running) return
    raf = requestAnimationFrame(loop)
    const t0 = performance.now()
    if (minInterval && t0 - lastSkip < minInterval * 1000) return
    lastSkip = t0
    const now = t0 / 1000
    const q = sampleAt(now)
    current = q
    setAngles(q)
    const p = tcpWorld()
    tcpObj.position.copy(p)
    pushTrail(p)
    renderer.render(scene, camera)
    const cost = performance.now() - t0
    slow = cost > 22 ? slow + 1 : Math.max(0, slow - 1)
    if (slow > 90 && dpr > 1) { dpr = 1; renderer.setPixelRatio(1); slow = 0 }
    if (movingLike()) idleSince = 0
    else if (!idleSince) idleSince = now
    minInterval = idleSince && now - idleSince > IDLE_S ? 1 / 30 : 0
  }
  const onVis = () => {
    if (document.hidden) { running = false; cancelAnimationFrame(raf) }
    else if (!running) { running = true; lastSkip = 0; idleSince = 0; loop() }
  }
  document.addEventListener('visibilitychange', onVis)

  const api = {
    ghostOpacity: 0.34,
    showCapsules: false,
    setAngles(q) { pushSample(q) },
    setImmediate(q) { buffer.length = 0; pushSample(q) },
    setFence,
    setObjects,
    setPlanPath(points) {
      planLine.geometry.dispose()
      const vectors = (points || []).map((p) => new THREE.Vector3(...urdfToThree(p.map((v) => v / 1000))))
      planLine.geometry = new THREE.BufferGeometry().setFromPoints(vectors)
      planLine.visible = vectors.length > 1
    },
    markCollisions,
    onPick: null,
    setCapsules(list) { setCapsules(list, api.showCapsules) },
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
    setTrailLen,
    setHealth,
    clearGhosts,
    // 自检用：残影当前有多少帧、每帧几个网格
    setGhost,
    // 自检用：残影臂是否可见、它有几个网格、本体有几个网格
    ghostInfo() { return { visible: ghost.root.visible, meshes: ghostMeshes,
                           realMeshes: (() => { let n = 0; robotRoot.traverse((o) => { if (o.isMesh) n++ }); return n })() } },
    resize() {
      const w = Math.max(320, container.clientWidth), h = Math.max(240, container.clientHeight)
      renderer.setSize(w, h)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    },
    dprInfo() { return { dpr, minInterval, trailN, trailLen } },
    dispose() {
      running = false
      cancelAnimationFrame(raf)
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointermove', onPointerMove)
      const geometries = new Set(Object.values(geoms))
      const materials = new Set([...Object.values(matCache), defaultMat, capsuleMaterial])
      scene.traverse((object) => {
        if (object.geometry) geometries.add(object.geometry)
        const list = Array.isArray(object.material) ? object.material : [object.material]
        list.filter(Boolean).forEach((material) => materials.add(material))
      })
      geometries.forEach((geometry) => geometry.dispose())
      materials.forEach((material) => material.dispose())
      if (window.__elfinScene === api) delete window.__elfinScene
      ro.disconnect(); renderer.dispose(); container.innerHTML = ''
    },
    resetView() { orbit.theta = HOME.theta; orbit.phi = HOME.phi; orbit.r = HOME.r; applyOrbit() },
  }
  loop()
  window.__elfinScene = api      // 自检钩子：无头环境也能读包围盒
  return api
}
