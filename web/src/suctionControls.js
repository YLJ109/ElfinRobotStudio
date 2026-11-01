export function suctionFeedback(w, side) {
  const current = w.connected && w.stateFresh !== false
  const state = current ? w.tools?.mounted?.suction?.[side] : null
  return { vacuum: typeof state?.vacuum === 'boolean' ? state.vacuum : null,
    blow: typeof state?.blow === 'boolean' ? state.blow : null,
    holding: state?.holding ?? null, current }
}
export function suctionBlockReason(w, side, action) {
  if (!w.connected || w.stateFresh === false) return '等待有效连接和状态'
  if (w.tools?.tool?.type !== 'suction') return '请先选择吸盘工具'
  if (w.toolPending?.[side] || w.toolPending?.all) return '正在处理该侧动作'
  const feedback = suctionFeedback(w, side)
  if (w.snap?.mode === 'real' && action.endsWith('_on') && (feedback.vacuum === null || feedback.blow === null)) return '气路反馈未知，请先核对 IO'
  if (action === 'vacuum_on' && feedback.blow === true) return '请先关闭该侧吹气'
  if (action === 'blow_on' && feedback.vacuum === true) return '请先停吸该侧吸盘'
  return ''
}
export function validateSuctionMapping(sides, aliases) {
  const physical = new Set()
  for (const side of ['left','right']) for (const channel of ['vacuum','blow']) {
    const value = sides?.[side]?.[channel], alias = aliases?.[value?.alias]
    if (!value?.alias || !alias || alias.direction !== 'output' || alias.confirmed_by_user !== true) return '四路均须选择已标定的输出别名'
    if (value.on !== 0 && value.on !== 1) return '请选择各路有效电平'
    const key = `${alias.group}:${alias.bit}`
    if (physical.has(key)) return '左右吸气和吹气须使用四个不同输出通道'
    physical.add(key)
  }
  return ''
}
