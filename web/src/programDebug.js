// Explicit operations only. Construction never starts or loads a program.
export const canPollDebug = (tab,visible,allowed) => tab==='debug' && visible===true && allowed===true
export function parseBreakpoints(text) {
  if (!text.trim()) return []
  const values = text.split(/[,，\s]+/u).filter(Boolean)
  if (values.length > 200 || values.some(v => !/^\d+$/u.test(v) || Number(v) < 1 || Number(v) > 200000)) {
    throw new Error('断点需为最多200个正整数行号，以逗号或空格分隔')
  }
  return [...new Set(values.map(Number))].sort((a, b) => a - b)
}

export function createProgramDebug({ request = fetch, allowed = () => false } = {}) {
  let session = null, pending = false
  async function call(action = '', payload) {
    if (!allowed()) throw new Error('调试仅限未连接真机的仿真')
    if (pending) throw new Error('调试请求正在处理')
    pending = true
    try {
      const path = '/api/program/debug' + (action ? '/' + action : '')
      const response = await request(path, payload === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, session }),
      })
      if (response.status === 404) throw new Error('调试路由尚未集成，请挂载 create_debug_router(world)')
      const result = await response.json()
      if (!response.ok || !result.ok) throw new Error(result.detail || result.why || '调试操作失败')
      if (!allowed()) { session = null; throw new Error('模式或连接已变化，调试响应已丢弃') }
      session = result.session || null
      return result
    } finally { pending = false }
  }
  return { status: () => call(), load: (src, breakpoints) => call('load', { src, breakpoints }),
    control: action => call(action, {}), breakpoints: lines => call('breakpoints', { breakpoints: lines }) }
}
