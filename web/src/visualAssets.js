// Display-only assets. Never change controller model, TCP, payload or IO here.
// Elfin 5 是当前唯一的运动学与碰撞模型，其它型号外观不再提供入口。
export const ROBOT_MODELS = [
  ['elfin5', 'Elfin 5（当前运动学）'],
].map(([id, name]) => ({ id, name, base: `/models/${id}/` }))

export const DEFAULT_VACUUM = Object.freeze({
  length: 300, width: 50, thickness: 10, edge: 10, cupHeight: 30,
  // Diameter is not measured. This default is only a visibly labelled placeholder.
  cupDiameter: 15, mountHeight: 40,
})
export function vacuumDimensions(value = {}) {
  const p = { ...DEFAULT_VACUUM, ...value }
  for (const key of Object.keys(DEFAULT_VACUUM)) {
    if (typeof p[key] !== 'number' || !Number.isFinite(p[key])) throw new Error('工具尺寸必须是有限数字')
  }
  if (p.length <= 0 || p.length > 2000 || p.width <= 0 || p.width > 1000 ||
      p.thickness <= 0 || p.thickness > 500 || p.cupHeight <= 0 || p.cupHeight > 1000 ||
      p.cupDiameter <= 0 || p.cupDiameter > p.width || p.edge < p.cupDiameter / 2 ||
      p.edge * 2 + p.cupDiameter >= p.length || p.mountHeight < 0 || p.mountHeight > 1000) {
    throw new Error('工具尺寸不合理，请核对吸盘直径、边距和安装板尺寸')
  }
  return { ...p, spacing: p.length - 2 * p.edge }
}

export function robotAsset(id) {
  const model = ROBOT_MODELS.find((item) => item.id === id)
  if (!model) throw new Error('未知机器人显示模型')
  return model
}

// Both catalog validation and GLB loading use this local allowlist. Neither a
// persisted preference nor an API tool descriptor can introduce arbitrary URLs.
export const TOOL_LIBRARY_ASSETS = Object.freeze({
  'robotiq-2f85': '/tools/robotiq-2f85.glb',
  'robotiq-2f140': '/tools/robotiq-2f140.glb',
  'robotiq-3f': '/tools/robotiq-3f.glb',
  'franka-panda-hand': '/tools/franka-panda-hand.glb',
  'schunk-sdh': '/tools/schunk-sdh.glb',
  'schunk-wsg50': '/tools/schunk-wsg50.glb',
})
export const BUILTIN_TOOLS = Object.freeze([
  Object.freeze({ id: 'vacuum-reference', name: '双吸盘参考模型', kind: 'vacuum-reference' }),
  Object.freeze({ id: 'none', name: '无工具（法兰）', kind: 'none' }),
])
const SOURCE_COMMIT = '45196f6558fe8ba9d89bc8a105396c68c3e7e892'
const SOURCE_BASE = `https://github.com/ros-industrial-attic/robotiq/tree/${SOURCE_COMMIT}/`
const EXTRA_SOURCES = Object.freeze({
  'franka-panda-hand': { source: 'https://github.com/frankarobotics/franka_ros/tree/ddd2fffd9de44b02ad15b4bbb2bfa2cec4d60d98', license: 'Apache-2.0', file: '/tools/source/franka/LICENSE' },
  'schunk-sdh': { source: 'https://github.com/ipa320/schunk_modular_robotics/tree/b2d73bdd333eceda0e91b4f8d4e8881433dc243a', license: 'Apache-2.0', file: '/tools/source/schunk/LICENSE' },
  'schunk-wsg50': { source: 'https://github.com/RobotLocomotion/models/tree/f85076b4d95a8bdfec70153015e723e92312e4e2', license: 'BSD-3-Clause', file: '/tools/source/wsg/wsg_50_description/LICENSE' },
})
const vector3 = (v, maximum = 2000) => Array.isArray(v) && v.length === 3 &&
  v.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= maximum)

export function validateToolCatalog(value) {
  if (value?.schema_version !== 1 || !Array.isArray(value.tools) || value.tools.length > 32) {
    throw new Error('末端工具目录格式无效')
  }
  const seen = new Set()
  const tools = value.tools.map((entry) => {
    const additional = EXTRA_SOURCES[entry?.id]
    if (!entry || !Object.hasOwn(TOOL_LIBRARY_ASSETS, entry.id) || seen.has(entry.id) ||
        entry.asset !== TOOL_LIBRARY_ASSETS[entry.id] || !['parallel_gripper', 'suction'].includes(entry.type) ||
        typeof entry.name !== 'string' || !entry.name.trim() || entry.name.length > 100 ||
        !vector3(entry.tcp_mm) || !vector3(entry.bounds_mm?.min) || !vector3(entry.bounds_mm?.max) ||
        entry.bounds_mm.min.some((n, i) => n >= entry.bounds_mm.max[i]) ||
        !vector3(entry.default_rotation_deg, 360) ||
        typeof entry.source !== 'string' || !(additional ? entry.source === additional.source : entry.source.startsWith(SOURCE_BASE)) ||
        entry.license !== (additional?.license || 'BSD-2-Clause') || !/^[a-f0-9]{64}$/.test(entry.sha256 || '')) {
      throw new Error('末端工具目录包含未授权资产或无效尺寸')
    }
    seen.add(entry.id)
    // Explicit fields only: metadata may not smuggle executable or external URLs.
    return Object.freeze({
      id: entry.id, name: entry.name, type: entry.type, asset: entry.asset,
      source: entry.source, license: entry.license, sha256: entry.sha256,
      tcp_mm: Object.freeze([...entry.tcp_mm]),
      bounds_mm: Object.freeze({ min: Object.freeze([...entry.bounds_mm.min]), max: Object.freeze([...entry.bounds_mm.max]) }),
      default_rotation_deg: Object.freeze([...entry.default_rotation_deg]),
      preview: `/tools/${entry.id}.png`, license_file: additional?.file || '/tools/source/robotiq/LICENSE',
      tcp_reference: String(entry.tcp_reference || '模型参考值，非实物标定'),
      units: 'm', pose: entry.animation?.version===1 && entry.animation.simulation_only===true ? 'articulated-simulation' : 'fixed-open', calibrated: false,
    })
  })
  return Object.freeze({ schema_version: 1, tools: Object.freeze(tools) })
}

let catalogRequest = null
export function loadToolCatalog() {
  if (!catalogRequest) {
    catalogRequest = (async () => {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 10000)
      try {
        const response = await fetch('/tools/catalog.json', { signal: controller.signal, credentials: 'same-origin', redirect: 'error' })
        if (!response.ok) throw new Error(`工具目录加载失败 (${response.status})`)
        const text = await response.text()
        if (text.length > 128 * 1024) throw new Error('工具目录过大')
        return validateToolCatalog(JSON.parse(text))
      } finally { clearTimeout(timer) }
    })().catch((error) => { catalogRequest = null; throw error })
  }
  return catalogRequest
}

export async function libraryToolEntry(value) {
  const id = typeof value === 'string' ? value : value?.id
  if (!Object.hasOwn(TOOL_LIBRARY_ASSETS, id) ||
      (value?.asset !== undefined && value.asset !== TOOL_LIBRARY_ASSETS[id])) {
    throw new Error('未知或未授权的末端工具资产')
  }
  const entry = (await loadToolCatalog()).tools.find((item) => item.id === id)
  if (!entry) throw new Error('末端工具不在已安装目录中')
  return entry
}
