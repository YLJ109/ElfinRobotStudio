import * as THREE from 'three'

// Purely local visual kinematics. Never sends network requests or real IO.
// closure=0 is the exported open bind pose; 1 is the documented closed preset.
// The controller's reference TCP stays fixed even when contact centres move.
const controllers = new WeakMap()
const finite = (v) => typeof v === 'number' && Number.isFinite(v)
const vector = (v, n) => Array.isArray(v) && v.length === n && v.every(finite)
const clamp = (n) => Math.max(0, Math.min(1, n))

export function validateToolAnimation(value) {
  if (value?.version !== 1 || value.simulation_only !== true || value.tcp_policy !== 'fixed-reference' ||
      !finite(value.duration_s) || value.duration_s < .1 || value.duration_s > 10 ||
      !Array.isArray(value.links) || !value.links.length || value.links.length > 64 ||
      !Array.isArray(value.parts) || !value.parts.length || value.parts.length > 128) {
    throw new Error('工具动画元数据无效')
  }
  const names = new Set(), partNames = new Set()
  const links = value.links.map((link) => {
    if (typeof link.name !== 'string' || !link.name || names.has(link.name) ||
        (link.parent !== null && !names.has(link.parent)) ||
        !['fixed', 'revolute', 'prismatic'].includes(link.type) ||
        !vector(link.origin, 16) || !vector(link.axis, 3) ||
        !finite(link.open) || !finite(link.closed) || Math.max(Math.abs(link.open), Math.abs(link.closed)) > 7 ||
        (link.type === 'prismatic' && Math.max(Math.abs(link.open), Math.abs(link.closed)) > 1) ||
        (link.type === 'fixed' && (link.open !== 0 || link.closed !== 0))) throw new Error('工具动画关节无效')
    const matrix = new THREE.Matrix4().fromArray(link.origin), e = matrix.elements
    const position = new THREE.Vector3(), rotation = new THREE.Quaternion(), scale = new THREE.Vector3()
    matrix.decompose(position, rotation, scale)
    const rigid = new THREE.Matrix4().compose(position, rotation, new THREE.Vector3(1, 1, 1))
    if (e.some((n, i) => Math.abs(n - rigid.elements[i]) > 1e-6) || position.length() > 2 ||
        new THREE.Vector3(...link.axis).length() < .999 || new THREE.Vector3(...link.axis).length() > 1.001) {
      throw new Error('工具动画必须使用刚体变换，不能缩放模型')
    }
    names.add(link.name)
    return { ...link, origin: [...link.origin], axis: [...link.axis] }
  })
  const parts = value.parts.map((part) => {
    if (typeof part.name !== 'string' || !part.name || partNames.has(part.name) || !names.has(part.link)) {
      throw new Error('工具动画部件无效')
    }
    partNames.add(part.name)
    return { name: part.name, link: part.link }
  })
  const bounds = value.collision_bounds_mm
  if (!vector(bounds?.min, 3) || !vector(bounds?.max, 3) ||
      bounds.min.some((n, i) => Math.abs(n) > 5000 || Math.abs(bounds.max[i]) > 5000 || n >= bounds.max[i])) {
    throw new Error('工具动画完整开合包络无效')
  }
  return { ...value, links, parts, collision_bounds_mm: { min: [...bounds.min], max: [...bounds.max] } }
}

export function toolClosure(state) {
  const mounted = state?.mounted ?? state
  if (!mounted || state?.sim === false || state?.mode === 'real') return null
  if (finite(mounted.closure)) return clamp(mounted.closure)
  if (mounted.action_state === 'active') return 1
  if (mounted.action_state === 'idle') return 0
  // Unknown/stale/error state is not an open command. Hold the last target.
  return null
}

/**
 * Main-agent integration contract:
 *   const animation = createToolAnimation(root, glbDocument.asset.extras.animation)
 *   animation.reset(snapshot.tools.mounted) // optional initial state hydration
 *   animation.update(snapshot.tools.mounted, dtSeconds) // render loop, sim only
 *   animation.dispose() // tool replacement; does NOT dispose shared geometry
 *
 * readLibraryTool already attaches a non-enumerable root.animation handle.
 * Cloned ghosts need their OWN createToolAnimation(clone, root.animation.metadata)
 * and reset({closure: intendedClosure}); never share the real tool controller.
 * collisionBoundsMm is the FULL CONTINUOUS PRESET SWEEP, not current-pose AABB.
 */
export function createToolAnimation(root, metadata) {
  controllers.get(root)?.dispose()
  if (!metadata) return Object.freeze({ supported: false, reason: '工具资产尚未导出分节关节数据',
    update: () => false, reset: () => false, snapshot: () => ({ supported: false }), dispose() {} })
  const rig = validateToolAnimation(metadata)
  const meshes = [], existing = new Map()
  root.traverse((node) => {
    if (node.isMesh) meshes.push(node)
    if (node.userData?.toolAnimationLink) existing.set(node.userData.toolAnimationLink, node)
  })
  const parts = rig.parts.map((part) => {
    const candidates = meshes.filter((node) => node.name === part.name &&
      (!node.userData.original_link || node.userData.original_link === part.link))
    if (candidates.length !== 1) throw new Error(`工具资产缺少独立可动部件：${part.name}；需要重新导出，不能缩放冒充`)
    return { ...part, mesh: candidates[0] }
  })
  // Extra geometry must not silently remain unrigged (e.g. an old merged mesh).
  if (new Set(parts.map((p) => p.mesh)).size !== meshes.length) throw new Error('工具资产与分节关节目录不一致')
  const bones = new Map(), bindings = [], worldBind = new Map()
  if (existing.size && existing.size !== rig.links.length) throw new Error('工具动画骨架不完整')
  for (const link of rig.links) {
    const origin = new THREE.Matrix4().fromArray(link.origin)
    const axis = new THREE.Vector3(...link.axis).normalize()
    const restPosition = new THREE.Vector3(), restRotation = new THREE.Quaternion(), scale = new THREE.Vector3()
    origin.decompose(restPosition, restRotation, scale)
    const bone = existing.get(link.name) || new THREE.Group()
    bone.name = `tool_joint_${link.name}`
    bone.userData.toolAnimationLink = link.name
    const bindMotion = link.type === 'prismatic'
      ? new THREE.Matrix4().makeTranslation(axis.x * link.open, axis.y * link.open, axis.z * link.open)
      : new THREE.Matrix4().makeRotationAxis(axis, link.type === 'revolute' ? link.open : 0)
    const bind = origin.clone().multiply(bindMotion)
    if (link.parent !== null) bind.premultiply(worldBind.get(link.parent))
    worldBind.set(link.name, bind)
    bones.set(link.name, bone)
    bindings.push({ bone, link, axis, translationAxis: axis.clone().applyQuaternion(restRotation), restPosition, restRotation, delta: new THREE.Quaternion() })
    if (!existing.size) (link.parent === null ? root : bones.get(link.parent)).add(bone)
  }
  if (!existing.size) {
    // Exported vertices are baked in flange coordinates. Move only their rigid
    // node transform into the source-link frame; never change vertex buffers.
    for (const { mesh, link } of parts) {
      const matrix = new THREE.Matrix4().copy(worldBind.get(link)).invert()
      mesh.updateMatrix()
      matrix.multiply(mesh.matrix)
      matrix.decompose(mesh.position, mesh.quaternion, mesh.scale)
      bones.get(link).add(mesh)
    }
  }
  let closure = 0, target = 0, from = 0, elapsed = 0, duration = rig.duration_s, disposed = false
  function apply() {
    for (const { bone, link, axis, translationAxis, restPosition, restRotation, delta } of bindings) {
      const q = link.open + closure * (link.closed - link.open)
      bone.position.copy(restPosition)
      bone.quaternion.copy(restRotation)
      if (link.type === 'prismatic') bone.position.addScaledVector(translationAxis, q)
      if (link.type === 'revolute') bone.quaternion.multiply(delta.setFromAxisAngle(axis, q))
      bone.scale.set(1, 1, 1)
    }
  }
  const animation = {
    supported: true, metadata: rig,
    collisionBoundsMm: { min: [...rig.collision_bounds_mm.min], max: [...rig.collision_bounds_mm.max] },
    reset(state = { closure: 0 }) {
      if (disposed) return false
      const value = toolClosure(state)
      if (value === null) return false
      closure = target = from = value; elapsed = 0; apply(); return true
    },
    update(state, dt) {
      if (disposed || state?.mode === 'real' || state?.sim === false) return false
      const value = toolClosure(state)
      if (value !== null && value !== target) {
        from = closure; target = value; elapsed = 0
        // Keep the same joint velocity for a narrow board-piece opening;
        // a 3 mm finger stroke must not consume a full 85 mm-stroke duration.
        duration = Math.max(.04, rig.duration_s * Math.abs(target-from))
      }
      if (!finite(dt) || dt <= 0 || closure === target) return false
      // dt is seconds. Bound background-tab catch-up; don't jump on resume.
      elapsed = Math.min(duration, elapsed + Math.min(dt, .1))
      const t = elapsed / duration
      const ease = t*t*t*(10 + t*(-15 + 6*t))
      closure = elapsed === duration ? target : from + (target-from) * ease
      apply(); return true
    },
    snapshot: () => ({ supported: true, closure, target, moving: !disposed && closure !== target, disposed }),
    dispose() {
      if (disposed) return
      disposed = true; bindings.length = 0; bones.clear(); worldBind.clear()
      if (controllers.get(root) === animation) controllers.delete(root)
    },
  }
  apply()
  controllers.set(root, animation)
  Object.defineProperty(root, 'animation', { value: animation, configurable: true })
  return animation
}
