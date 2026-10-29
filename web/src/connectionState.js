// No browser state is created until connect(); dependencies can be replaced in offline tests.
export function createStateConnection({
  url,
  createSocket = (address) => new WebSocket(address),
  schedule = (fn, delay) => setTimeout(fn, delay),
  cancel = (timer) => clearTimeout(timer),
  retryMs = 1500,
  onOpen = () => {}, onMessage = () => {}, onClose = () => {}, onError = () => {},
}) {
  let socket = null
  let timer = null
  let stopped = true
  let retryVersion = 0

  function retry() {
    if (stopped || timer !== null) return
    const version = ++retryVersion
    timer = schedule(() => {
      if (stopped || version !== retryVersion) return
      timer = null
      connect()
    }, retryMs)
  }
  function connect() {
    stopped = false
    if (socket || timer !== null) return
    let candidate
    try { candidate = createSocket(url) }
    catch (error) { onError(error); retry(); return }
    socket = candidate
    const current = () => !stopped && socket === candidate
    candidate.onopen = (event) => { if (current()) onOpen(event) }
    candidate.onmessage = (event) => { if (current()) onMessage(event) }
    candidate.onerror = (event) => { if (current()) onError(event) }
    candidate.onclose = (event) => {
      if (!current()) return
      socket = null
      onClose(event)
      retry()
    }
  }
  function stop() {
    stopped = true
    retryVersion++
    if (timer !== null) cancel(timer)
    timer = null
    const old = socket
    socket = null
    if (old) old.close()
  }
  return { connect, stop }
}

export async function parseResponse(response, path) {
  let body
  try { body = await response.json() }
  catch {
    throw new Error(`${path} -> HTTP ${response.status}：${response.ok ? '响应不是有效 JSON' : '请求失败，响应不是有效 JSON'}`)
  }
  if (!response.ok) {
    const detail = body?.why || body?.detail || body?.error
    const reason = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((item) => item.msg || String(item)).join('；') : ''
    const error = new Error(`${path} -> HTTP ${response.status}${reason ? '：' + reason : ''}`)
    error.status = response.status
    error.payload = body
    throw error
  }
  return body
}

export function isSixAxis(values) {
  return Array.isArray(values) && values.length === 6
    && values.every((value) => typeof value === 'number' && Number.isFinite(value))
}

// Rendering consumes encoder feedback, not permission to move. Never substitute
// simulated zero joints when real feedback is unavailable or too old.
export function displayJoints(snap) {
  if(snap?.mode!=='real')return snap?.joints?.known!==false && isSixAxis(snap?.joints?.q)?snap.joints.q:null
  const real=snap.real
  if(!real?.stream_up || !isSixAxis(real.joints))return null
  if(typeof real.lag_ms!=='number' || !Number.isFinite(real.lag_ms) || real.lag_ms<0 || real.lag_ms>500)return null
  return real.joints
}

// A stored request is never proof of a controller speed. All UI consumers share this rule.
export function speedState(snap, connected) {
  const isReal = snap?.mode === 'real'
  const finitePct = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
  const requested = isReal ? snap?.real_speed_pct : snap?.speed_pct
  const requestedPct = finitePct(requested) ? requested : null
  const override = snap?.real?.override
  const actualPct = !connected ? null : !isReal ? requestedPct
    : snap?.real?.stream_up && snap.real.quality === 'valid'
      && typeof override === 'number' && Number.isFinite(override) && override >= 0 && override <= 1
      ? Math.round(override * 1000) / 10 : null
  const ack = snap?.speed_ack
  const relevant = isReal && ack?.target === 'real' && ack.req === requestedPct
  const pending = relevant && ack.ok == null
  const rejected = relevant && ack.ok === false
  const mismatch = actualPct !== null && requestedPct !== null && Math.abs(actualPct - requestedPct) > .05
  const why = actualPct === null ? '实际倍率未知，等待有效控制器回读'
    : rejected ? (ack.why || '倍率设置未确认')
    : pending ? `等待控制器确认目标 ${requestedPct}%`
    : mismatch ? `目标设置 ${requestedPct}%，控制器实际 ${actualPct}%`
    : ''
  return { isReal, actualPct, requestedPct, pending, rejected, mismatch, why }
}
