// Local read-only views. No HTTP, model calls, credentials, robot commands or store writes.
import { inspectionPayload } from './taskStudio.js'

export function diagnosticRows(connected, snap) {
  const known = connected && !!snap
  const mode = known && ['sim', 'real'].includes(snap.mode) ? snap.mode : 'unknown'
  const level = known && ['green', 'yellow', 'red', 'alarm'].includes(snap.safety?.level) ? snap.safety.level : 'unknown'
  const jointsKnown = known && snap.joints?.known !== false && Array.isArray(snap.joints?.q) && snap.joints.q.length === 6 && snap.joints.q.every(Number.isFinite)
  return [
    { label: '状态来源', value: known ? '已连接 · 当前快照' : '状态不可确认', detail: known ? '以下内容来自现有状态快照，不发起设备请求。' : '连接中断或尚无快照，不能据旧读数判定设备状态。', tone: known ? 'ok' : 'warn' },
    { label: '运行模式', value: mode === 'sim' ? '仿真' : mode === 'real' ? '真机' : '未知', detail: '此面板提供只读信息，不连接、使能或执行机器人。', tone: mode === 'unknown' ? 'warn' : 'dim' },
    { label: '安全状态', value: { green: '绿色', yellow: '黄色', red: '红色', alarm: '告警', unknown: '未知' }[level], detail: level === 'unknown' ? '缺少有效安全读数，无法判断。' : '颜色是系统阈值状态，不代表碰撞概率或现场安全认证。', tone: ['red', 'alarm'].includes(level) ? 'bad' : level === 'green' ? 'ok' : 'warn' },
    { label: '关节读数', value: jointsKnown ? '六轴读数有效' : '未知 / 不完整', detail: '数值格式有效不等于机器人已停稳或实际位置已核验。', tone: jointsKnown ? 'dim' : 'warn' },
  ]
}

export function diffDraftText(before, after) {
  const split = value => String(value ?? '').replace(/\r\n/g, '\n').split('\n')
  const oldLines = before === '' ? [] : split(before), newLines = after === '' ? [] : split(after)
  if (oldLines.length > 400 || newLines.length > 400 || String(before).length + String(after).length > 80000) throw new Error('差异查看最多支持每侧 400 行、合计 80000 字符，请缩小比较范围。')
  const lengths = Array.from({ length: oldLines.length + 1 }, () => new Uint16Array(newLines.length + 1))
  for (let i = oldLines.length - 1; i >= 0; i--) for (let j = newLines.length - 1; j >= 0; j--) {
    lengths[i][j] = oldLines[i] === newLines[j] ? 1 + lengths[i + 1][j + 1] : Math.max(lengths[i + 1][j], lengths[i][j + 1])
  }
  const rows = []
  let i = 0, j = 0, added = 0, removed = 0
  while (i < oldLines.length || j < newLines.length) {
    if (i < oldLines.length && j < newLines.length && oldLines[i] === newLines[j]) {
      rows.push({ kind: 'same', oldLine: i + 1, newLine: j + 1, text: oldLines[i] }); i++; j++
    } else if (i < oldLines.length && (j === newLines.length || lengths[i + 1][j] >= lengths[i][j + 1])) {
      rows.push({ kind: 'removed', oldLine: i + 1, newLine: null, text: oldLines[i++] }); removed++
    } else { rows.push({ kind: 'added', oldLine: null, newLine: j + 1, text: newLines[j++] }); added++ }
  }
  return { rows, added, removed }
}

export function studioDraftText(draft) {
  const payload = inspectionPayload(draft)
  const key = payload.joints ? 'joints' : 'poses'
  return `SPEED ${payload.speed}\n` + payload[key].map(row => `${key === 'joints' ? 'MOVEJ' : 'MOVEL'} ${row.join(',')}`).join('\n')
}
