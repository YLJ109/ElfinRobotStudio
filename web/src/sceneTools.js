import { objectGroundHeight } from './objectGround.js'
// Scene geometry and request projections only; no store, network or device access.
const finite = value => typeof value === 'number' && Number.isFinite(value)
const vector = values => Array.isArray(values) && values.length === 3 && values.every(finite)
export const SCENE_LIMIT = 1000
export const TEMPLATE_LABELS = { workbench: '工作台布局', pallet: '托盘布局' }

export function gridArrayPreview(source, counts, spacing, objectCount = 0) {
  if(source?.locked)throw Error('物体已锁定，请先解锁')
  if(source?.task)throw Error('任务物体请使用任务布置')
  if (!source?.id || !vector(source.xyz)) throw new Error('请先选择位置有效的源物体')
  if (!Array.isArray(counts) || counts.length !== 3 || counts.some(n => !Number.isInteger(n) || n < 1 || n > 12)) throw new Error('X/Y/Z 数量需要 1～12 的整数')
  const total = counts.reduce((a, b) => a * b, 1)
  if (total < 2 || total > 128) throw new Error('数组总数需要 2～128（含源物体）')
  if (!vector(spacing) || spacing.some(n => Math.abs(n) > 1e6)) throw new Error('间距需要 ±1000000mm 内的有限数值')
  if (!Number.isInteger(objectCount) || objectCount < 0 || objectCount + total - 1 > SCENE_LIMIT) throw new Error('现场最多 1000 个物体')
  if (counts.some((n, i) => n > 1 && spacing[i] === 0)) throw new Error('有多份的轴不能使用零间距，否则会完全重叠')
  const positions = []
  for (let x = 0; x < counts[0]; x++) for (let y = 0; y < counts[1]; y++) for (let z = 0; z < counts[2]; z++) {
    if (!x && !y && !z) continue
    const xyz = source.xyz.map((v, i) => v + [x, y, z][i] * spacing[i])
    if (xyz.some(n => !finite(n) || Math.abs(n) > 1e6)) throw new Error('数组位置超出 ±1000000mm 范围')
    positions.push(xyz)
  }
  return { total, added:total - 1, positions,
    min:source.xyz.map((v, i) => Math.min(v, ...positions.map(p => p[i]))),
    max:source.xyz.map((v, i) => Math.max(v, ...positions.map(p => p[i]))) }
}

export function alignedPosition(object, mode, step = 50, axes = [true, true, false]) {
  if(object?.locked)throw Error('物体已锁定，请先解锁')
  if (!object?.id || !vector(object.xyz)) throw new Error('请先选择位置有效的物体')
  const xyz = object.xyz.slice()
  if (mode === 'floor') {
    if (!['box','sphere','cylinder'].includes(object.kind) || !Array.isArray(object.size)) throw new Error('不支持该物体的落地几何')
    const height = objectGroundHeight(object)
    if (!finite(height) || height <= 0 || height > 1e6) throw new Error('物体尺寸无效，无法计算落地位置')
    xyz[2] = height
  } else if (mode === 'grid') {
    if (!finite(step) || step <= 0 || step > 1e6) throw new Error('网格步距需要大于 0 且不超过 1000000mm')
    if (!Array.isArray(axes) || axes.length !== 3 || axes.some(v => typeof v !== 'boolean') || !axes.some(Boolean)) throw new Error('请至少选择一个对齐轴')
    for (let i = 0; i < 3; i++) if (axes[i]) xyz[i] = Math.floor(xyz[i] / step + .5) * step
  } else throw new Error('未知的对齐方式')
  if (xyz.some(n => !finite(n) || Math.abs(n) > 1e6)) throw new Error('对齐后位置超出 ±1000000mm 范围')
  return xyz.map(n => Object.is(n, -0) ? 0 : n)
}

export function sceneEditBlock(snap, connected) {
  if (!connected || !snap) return '状态流未连接，无法确认当前现场'
  if (snap.mode !== 'sim' || snap.real?.cmd_up || snap.real?.stream_up) return '需要仿真模式且真机链路断开'
  if (snap.moving !== false || snap.paused || snap.exec?.execution?.busy || ['running','waiting','paused'].includes(snap.exec?.state)
      || ['running','paused'].includes(snap.exec?.queue_state) || snap.hand_follow?.active || snap.real_hand_follow?.active
      || snap.ready?.running || snap.free?.on || snap.exec?.stop_result?.settled === false) return '请先停止运动、程序、跟随或就绪任务，并确认停止完成'
  if ((snap.objects || []).some(o => o.gravity && !o.floating && !o.locked && (Math.abs(o.vz || 0) > 0 || o.xyz?.[2] > objectGroundHeight(o) + 1e-6 && o.supported !== true))) return '物体仍在下落，请等待稳定'
  return ''
}

export function readSceneStatus(value) {
  if (value?.ok !== true || typeof value.revision !== 'string' || !value.revision || !Array.isArray(value.objects)
    || !Array.isArray(value.templates) || typeof value.can_undo !== 'boolean' || typeof value.can_redo !== 'boolean') throw new Error('场景工具响应不完整，操作未开放')
  return value
}

export function sceneActionBody(action, status, options = {}) {
  const current = readSceneStatus(status)
  if (action === 'template') {
    if (!current.templates.includes(options.template)) throw new Error('服务端未提供该模板')
    if (current.objects.length >= SCENE_LIMIT) throw new Error('现场最多 1000 个物体')
    return { template:options.template, revision:current.revision }
  }
  if (action === 'array') {
    const source = current.objects.find(o => o.id === options.id)
    gridArrayPreview(source, options.counts, options.spacing, current.objects.length)
    return { id:source.id, counts:options.counts.slice(), spacing:options.spacing.slice(), revision:current.revision }
  }
  if (action === 'align') {
    const ids = options.ids || [options.id]
    const axes = options.axes ?? [true,true,false], step = options.step ?? 50
    if (!Array.isArray(ids) || !ids.length || ids.length > 1000 || new Set(ids).size !== ids.length
      || ids.some(id => typeof id !== 'string' || !id || !current.objects.some(o => o.id === id))) throw new Error('选中的物体已不存在，请重新读取现场')
    for (const id of ids) alignedPosition(current.objects.find(o => o.id === id), options.mode, step, axes)
    return options.mode === 'floor' ? { ids:ids.slice(), ground:true, revision:current.revision }
      : { ids:ids.slice(), axis:['x','y','z'].filter((_,i) => axes[i]), step, revision:current.revision }
  }
  if (!['undo','redo'].includes(action)) throw new Error('未知场景工具操作')
  if (!(action === 'undo' ? current.can_undo : current.can_redo)) throw new Error('没有可撤销/重做的新增批量操作')
  return { revision:current.revision }
}
