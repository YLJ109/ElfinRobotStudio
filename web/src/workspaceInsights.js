// Read-only projections: never infer a command target from an action name or FK.
export const sixFinite = value => Array.isArray(value) && value.length === 6 && value.every(v => typeof v === 'number' && Number.isFinite(v))

export function readTaskTab(key, tabs, fallback) {
  try {
    const value = globalThis.localStorage?.getItem(key)
    if (tabs.some(tab => tab.id === value)) return value
  } catch { /* Storage can be unavailable in private browsing. */ }
  return tabs.some(tab => tab.id === fallback) ? fallback : tabs[0]?.id
}
export function saveTaskTab(key, value) {
  try { globalThis.localStorage?.setItem(key, value) } catch { /* Navigation still works. */ }
}
export function tabFocusIndex(key, index, count) {
  if (!count) return null
  return key === 'ArrowRight' ? (index + 1) % count : key === 'ArrowLeft' ? (index + count - 1) % count
    : key === 'Home' ? 0 : key === 'End' ? count - 1 : null
}

export function workspaceRun(snap, connected) {
  const real = snap?.mode === 'real'
  const feedback = real ? snap?.real?.joints : snap?.joints?.q
  const available = !!connected && snap?.joints?.known === true && sixFinite(feedback)
    && (!real || (snap.real?.stream_up === true && snap.real?.quality === 'valid'))
  const target = sixFinite(snap?.preview) ? snap.preview : null
  const errors = target && available ? target.map((v, i) => v - feedback[i]) : null
  const exec = snap?.exec || {}
  const source = !connected ? '连接断开 · 最后快照' : snap?.real_hand_follow?.active ? '真机手掌跟随'
    : snap?.hand_follow?.active ? '仿真手掌跟随' : ['running', 'waiting', 'paused'].includes(exec.state) ? '程序'
      : exec.execution?.busy ? `执行器 · ${exec.execution.action || '来源未上报'}` : '当前无活动任务来源上报'
  return { available, feedback: available ? feedback : null, target, errors,
    maxError: errors ? Math.max(...errors.map(Math.abs)) : null, source,
    state: exec.state || '未知', action: exec.execution?.action || '未上报',
    error: exec.error || exec.execution?.error || '', line: exec.current?.line ?? null }
}

export function driveDiagnostics(snap, connected) {
  if (!connected) return [{ state: '未知', why: '后端状态流断开，缓存不能代表现态', fix: '检查本地服务与页面连接，收到新快照后再核对控制器。' }]
  if (snap?.mode !== 'real') return [{ state: '仿真', why: '当前没有真机控制器反馈', fix: '仅演练软件流程；需要现场操作时在连接页主动选择设备。' }]
  const r = snap.real || {}, rows = []
  if (!r.stream_up || r.quality !== 'valid') rows.push({ state: '反馈未知', why: `取流 ${r.stream_up ? '已连接' : '断开'} · 质量 ${r.quality || '未知'}`, fix: '检查控制器供电、网线、IP 和 10004 状态流；等待有效六轴回读。' })
  if (!r.cmd_up) rows.push({ state: '命令断开', why: '10003 命令通道未连接', fix: '核对网络与控制器服务；连接恢复后重新检查许可，不重放旧动作。' })
  const stop = snap.exec?.stop_result
  if (stop && stop.settled !== true) rows.push({ state: '停止待核对', why: stop.why || '停止结果未知', fix: '保持闭锁，现场确认停稳与物理保护；ACK 不等于停稳，不重发旧任务。' })
  if (r.err_code) rows.push({ state: '故障', why: `错误码 ${r.err_code} · 轴 ${r.err_axis ?? '--'}`, fix: '先排查故障原因与现场保护，再按设备规程复位；不要反复清错。' })
  for (const row of snap.telemetry?.preflight?.rows || []) if (!row.state) rows.push({ state: row.label, why: row.detail, fix: row.fix })
  if (!rows.length) rows.push({ state: '链路有效', why: `FSM ${r.fsm ?? '--'} · 运动许可 ${snap.motion_granted ? '已授予' : '未授予'}`, fix: '核对工具、负载、现场与就绪流程；链路有效不代表允许运动或安全认证。' })
  return rows
}

export function filterLogRows(logs, { kind = '', task = '', keyword = '', collapse = false } = {}) {
  const needle = keyword.trim().toLocaleLowerCase(), result = []
  let previous = null
  // Fold only adjacent original events. Filtering must not create false adjacency.
  for (const row of logs) {
    const taskId = row.extra?.task_id ?? row.extra?.epoch
    const matches = (!kind || row.kind === kind) && (!task || String(taskId ?? '') === task)
      && (!needle || `${row.text} ${JSON.stringify(row.extra || {})}`.toLocaleLowerCase().includes(needle))
    if (!matches) { previous = null; continue }
    const signature = JSON.stringify([row.kind, row.text, row.extra || null])
    if (collapse && previous?.signature === signature) {
      previous.item.count++; previous.item.lastT = row.t; previous.item.lastSeq = row.seq
    } else {
      const item = { ...row, count: 1, lastT: row.t, lastSeq: row.seq }
      result.push(item); previous = { signature, item }
    }
  }
  return result
}

export function settingChanges(base, draft) {
  return Object.entries(draft).filter(([key, value]) => !Object.is(base[key], value))
    .map(([key, value]) => ({ key, before: base[key], after: value }))
}
