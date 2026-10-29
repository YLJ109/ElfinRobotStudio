// Display-only, conservative summaries. Backend records and row objects stay intact.
const KIND_LABELS = Object.freeze({cmd:'指令',event:'事件',alarm:'告警',ai:'智能助手',program:'程序',point:'点位',
  link:'连接',sys:'系统',interlock:'联锁',perm:'权限',jog:'点动',ready:'就绪',speed:'速度',power:'电源',
  free:'拖动',execution:'执行',motion:'运动',camera:'相机',vision:'视觉',safety:'安全',tool:'工具'})
export function logKindLabel(kind) {
  return Object.hasOwn(KIND_LABELS,kind) ? KIND_LABELS[kind] : String(kind ?? '')
}
export function summarizeLog(row) {
  const original = typeof row === 'string' ? row : row?.text
  if (typeof original !== 'string') return { text: '', original: '', changed: false }
  let text = original
  let m
  if (original === '仿真已到达残影目标（六轴残差≤0.3°），残影自动收掉') {
    text = '仿真已到位，已收起预览'
  } else if ((m = original.match(/^模型 (\S+) 载入，限位与几何来自官方 URDF$/))) {
    text = `模型 ${m[1]} 已载入`
  } else if (original === '触发 100%/碰撞级：已自动停止执行，需人工复位后继续') {
    text = '已停止：触发保护，需复位'
  } else if ((m = original.match(/^(?:安全等级 -> \S+：)?(?:检测到越界\/碰撞：([\w/]+)（(\d+) 处）；)?摆放物「([^」]+)」压到 (\S+)$/))) {
    const scope = m[1] && (m[1] !== 'object' || Number(m[2]) > 1)
      ? `（${m[1]}，${m[2]}处）` : ''
    text = `碰撞${scope}：${m[3]} / ${m[4]}`
  } else if ((m = original.match(/^(?:安全等级 -> \S+：)?检测到越界\/碰撞：([\w/]+)（(\d+) 处）$/))) {
    // Keep fence/ground/self/object names: do not recast all collisions as objects.
    text = `越界/碰撞：${m[1]}（${m[2]}处）`
  } else if ((m = original.match(/^未验证的控制器状态 FSM=(\S+)，流程已中止$/))) {
    text = `已中止：未知 FSM=${m[1]}`
  } else if ((m = original.match(/^当前状态不允许 (\S+)：FSM=([^、]+)、enabled=(\S+)$/))) {
    text = `${m[1]} 被拒绝：FSM=${m[2]} / enabled=${m[3]}`
  } else if ((m = original.match(/^.*? -> ([A-Za-z]\w*),Fail,(\d+)(?:,(.*))?$/))) {
    const detail = (m[3] || '').replace(/\s*\(\d+ms\)$/, '').replace(/;$/, '').trim()
    const reason = /^(?:0,)?Emergency stop reset timeout$/.test(detail)
      ? '急停复位超时'
      : /^(?:This command is prohibited from being executed in the current robot state|当前状态不允许该指令(?: command forbidden in this state)?)$/.test(detail)
        ? '当前状态禁止执行' : detail
    text = `${m[1]} 失败 [${m[2]}]${reason ? '：' + reason : ''}`
  }
  // A future known phrase must never hide an important exception or state code.
  // Unknown records are not truncated, translated, or falsely labelled successful.
  if (text !== original) {
    const important = original.match(/\b(?:FSM\s*[=:]\s*-?\d+|[A-Za-z_]\w*(?:Error|Exception))\b/g) || []
    for (const token of new Set(important)) if (!text.includes(token)) text += ` · ${token}`
  }
  return { text, original, changed: text !== original }
}
