// Offline helpers: no store, network, hardware or browser side effects.
export const STUDIO_TABS = [
  { id: 'recordings', label: '轨迹录制' }, { id: 'scan', label: '擦拭扫描' },
  { id: 'handling', label: '搬运码垛' }, { id: 'acceptance', label: '任务验收' },
]
export const finiteVector = (v, length = 6) => Array.isArray(v) && v.length === length && v.every(Number.isFinite)
export const MAX_WAYPOINTS = 256
export const STUDIO_SESSION_KEY = 'huayan.taskStudio.session.v1'
export function recordingStatus(recording) {
  if (!recording?.active) return { text: '当前没有活动录制', warning: false }
  const state = recording.interrupted ? '录制已中断' : recording.full ? '录制已满，采样暂停' : '采样中'
  return { text: `${state} · ${recording.count || 0} 点${recording.full ? ' · 已达容量上限' : ''} · 每 2 秒刷新`, warning: !!(recording.interrupted || recording.full) }
}
export function parseVector(value, length) {
  const tokens = Array.isArray(value) ? value : String(value).trim().split(/[,，\s]+/)
  if (tokens.some(v => v === '' || v === null || typeof v === 'boolean')) throw new Error(`请输入 ${length} 个有限数值`)
  const numbers = tokens.map(Number)
  if (!finiteVector(numbers, length)) throw new Error(`请输入 ${length} 个有限数值`)
  return numbers
}
export function buildGeneratePayload(kind, name, input = {}) {
  if (!['raster', 'circle', 'pick-place', 'pallet'].includes(kind)) throw new Error('未知工艺类型')
  name = String(name || '').trim()
  if (!name || name.length > 80 || /[\x00-\x1f]/.test(name)) throw new Error('任务名称须为 1–80 个可见字符')
  const options = {}
  const scalar = (key, min, max = 10000, integer = false) => {
    const raw = input[key], n = Number(raw)
    if (raw === '' || raw == null || typeof raw === 'boolean' || !Number.isFinite(n) || n < min || n > max || (integer && !Number.isInteger(n))) throw new Error(`${key} 须在 ${min}–${max} 范围内${integer ? '且为整数' : ''}`)
    options[key] = n
  }
  scalar('speed', 1, 100)
  if (kind === 'raster' || kind === 'circle') {
    options.center = parseVector(input.center, 3); options.rpy = parseVector(input.rpy, 3)
    if (kind === 'raster') { scalar('width', 1, 3000); scalar('height', 1, 3000); scalar('spacing', 1, 3000) }
    else { scalar('radius', 1, 1500); scalar('segments', 8, 128, true) }
    const count = kind === 'raster' ? 2 * (Math.ceil(options.height / options.spacing) + 1) : options.segments + 1
    if (count > MAX_WAYPOINTS) throw new Error('轨迹最多 256 点，请增大间距')
  } else {
    options.pick = parseVector(input.pick, 6); options.place = parseVector(input.place, 6)
    scalar('lift', 1, 1000)
    if (kind === 'pallet') {
      for (const key of ['rows', 'cols', 'layers']) scalar(key, 1, key === 'layers' ? 32 : 64, true)
      for (const key of ['step_x', 'step_y', 'step_z']) scalar(key, 1, 2000)
      if (options.rows * options.cols * options.layers > 42) throw new Error('码垛最多 42 个工位，每件 6 个路点')
    }
  }
  return { kind, name, options }
}
export function filterRecordings(items, search = '') {
  const needle = String(search).trim().toLocaleLowerCase()
  return (Array.isArray(items) ? items : []).filter(item => item && item.id != null && `${item.name || ''} ${item.id}`.toLocaleLowerCase().includes(needle))
}
export function filterTrajectory(points, search = '', selected = [], onlySelected = false) {
  const needle = String(search).trim().toLowerCase(), ids = new Set(selected)
  return points.map((point, index) => ({ point, index })).filter(({ point, index }) =>
    (!onlySelected || ids.has(index)) && `${index + 1} ${JSON.stringify(point)}`.toLowerCase().includes(needle))
}
export function simplifyTrajectory(points, stride = 2) {
  if (!Number.isInteger(stride) || stride < 2) throw new Error('抽稀步长须为大于 1 的整数')
  return points.filter((_, i) => i === 0 || i === points.length - 1 || i % stride === 0)
}
function trajectoryPayload(draft, max) {
  const key = draft?.joints?.length ? 'joints' : 'poses'
  if (!draft?.[key]?.length || !draft[key].every(p => finiteVector(p))) throw new Error('轨迹须包含有效的六维点')
  if (draft[key].length > max) throw new Error(`轨迹最多 ${max} 点，请先显式简化`)
  return { [key]: draft[key].map(p => [...p]) }
}
export function inspectionPayload(draft) {
  const speed = draft?.speed ?? 10
  if (!Number.isFinite(speed) || speed < 1 || speed > 100) throw new Error('检查速度须在 1–100 范围内')
  return { ...trajectoryPayload(draft, MAX_WAYPOINTS), speed }
}
export function buildRevisionPayload(recording, name) {
  if (!Number.isSafeInteger(recording?.revision) || recording.revision < 0) throw new Error('缺少有效 revision，请读取录制详情后保存')
  name = String(name || '').trim()
  if (!name || name.length > 80) throw new Error('录制名称须为 1–80 字')
  return { name, revision: recording.revision, ...trajectoryPayload(recording, 6000) }
}
export function isRevisionConflict(error) {
  return error?.status === 409 || (error?.status === 422 && /轨迹已被修改|重新读取后再保存|revision.{0,30}(conflict|mismatch|stale)|修订.{0,12}(冲突|不一致|过期)/i.test(error.message || ''))
}
// Persist only editing data. Checked plans and execution confirmations are never restored.
export function readStudioSession(storage) {
  const raw = storage?.getItem(STUDIO_SESSION_KEY)
  if (!raw) return null
  const saved = JSON.parse(raw)
  if (saved?.version !== 1 || !saved.form || !STUDIO_TABS.some(t => t.id === saved.tab)) throw new Error('会话草稿格式无效')
  for (const draft of [saved.draft, saved.localRecovery?.draft]) {
    if (!draft) continue
    for (const key of ['joints', 'poses']) if (draft[key] != null && (!Array.isArray(draft[key]) || draft[key].length > 6000 || !draft[key].every(p => finiteVector(p)))) throw new Error('会话轨迹格式无效')
  }
  return saved
}
export function canRunStudio({ connected, mode, inspection, confirmed, busy }) {
  return !!(connected && mode === 'sim' && !busy && confirmed && inspection?.plan_id && inspection.report?.risk_detected === false)
}
