import { cleanHandLandmarks, palmFromLandmarks, handOpennessFromLandmarks, DEFAULT_HAND_DEPTH_RANGE, validHandDepthRange } from './handGeometry.js'

// One camera / one inference producer. Only scalar landmarks cross this local bus.
let input = null, owner = null
let visionRequested = false
let handDepthRange = [...DEFAULT_HAND_DEPTH_RANGE]
export function setHandFollowDepthRange(range) {
  if (!validHandDepthRange(range)) return false
  handDepthRange = [...range]
  return true
}
export function getHandFollowDepthRange() { return [...handDepthRange] }
const inputs = new Set(), visionRequests = new Set()
export function publishHandFollowInput(value, source) {
  if (value?.source === 'interaction' || value?.stream === 'interaction') return
  if (!value && owner !== source) return
  owner = value ? source : null
  input = value
  for (const fn of inputs) fn(input)
}
export function getHandFollowInput() { return input }
export function subscribeHandFollowInput(fn) {
  inputs.add(fn); fn(input)
  return () => inputs.delete(fn)
}
export function requestHandFollowVision() {
  visionRequested = true
  for (const fn of visionRequests) fn()
}
export function onHandFollowVisionRequest(fn) {
  visionRequests.add(fn)
  if (visionRequested) fn()
  return () => visionRequests.delete(fn)
}

export const PALM_MAX_AGE_MS = 300
// 仅用于光标/骨架：在已测量端点之间短时补帧，绝不进入采样发布链。
export function createHandOverlayPresentation(durationMs = 80) {
  let from = null, target = null, started = 0
  function read(now) {
    if (!target) return {point:null,landmarks:[],animating:false}
    const phase = Math.max(0, Math.min(1, (now-started)/durationMs))
    if (!from || phase >= 1) return {...target,animating:false}
    const alpha = phase*phase*(3-2*phase)
    const mix = (a,b) => a+(b-a)*alpha
    const point = {...target.point}
    for (const key of ['x','y','palm_scale','dx','dy']) point[key] = mix(from.point[key],target.point[key])
    const landmarks = target.landmarks.map((p,i) => {
      const previous = from.landmarks[i]
      if (!p || !previous) return p
      return {...p,x:mix(previous.x,p.x),y:mix(previous.y,p.y),
        ...(Number.isFinite(p.z) && Number.isFinite(previous.z) ? {z:mix(previous.z,p.z)} : {})}
    })
    return {point,landmarks,animating:true}
  }
  return { read,
    update(state, now) {
      const previous = read(now)
      target = {point:state.point ? {...state.point} : null,landmarks:state.landmarks}
      if (!target.point) { from = target = null; return read(now) }
      from = previous.point ? previous : null; started = now
      return read(now)
    },
    reset() { from = target = null },
  }
}
const PALM_HOLD_MS = 450
const PALM_DISPLAY_MAX_AGE_MS = 650
// Brief misses hold a measured position, never extrapolate or refresh its age.
// Both the time since the last successful result and the original frame age
// limit this hold. A continuing stream of empty results cannot keep it alive.
export function createPalmTracker() {
  let observation = null, lastSeq = 0, missed = false
  // Stable identity while lost: preview/housekeeping reads must not invalidate
  // the 21-point overlay computed value every time an empty result arrives.
  const noLandmarks = Object.freeze([])
  const reset = () => { observation = null; lastSeq = 0; missed = false }
  function read(now) {
    if (!observation || !Number.isFinite(now) || now < observation.timestamp || now >= observation.expiresAt) {
      observation = null
      return {point:null,landmarks:noLandmarks,timestamp:0,frameSeq:lastSeq,status:'lost',expiresAt:0}
    }
    const holding = missed || now - observation.timestamp >= PALM_MAX_AGE_MS
    return {...observation,status:holding ? 'holding' : 'tracking',
      nextUpdateAt:holding ? observation.expiresAt : Math.min(observation.expiresAt,observation.timestamp+PALM_MAX_AGE_MS)}
  }
  function update({landmarks,timestamp,frameSeq}, now) {
    if (!Number.isSafeInteger(frameSeq) || frameSeq <= lastSeq) return read(now)
    lastSeq = frameSeq
    const points = cleanHandLandmarks(landmarks), next = palmFromLandmarks(points)
    const previous = read(now)
    if (!next || !Number.isFinite(timestamp) || timestamp > now || now - timestamp >= PALM_MAX_AGE_MS ||
        (previous.point && timestamp <= previous.timestamp)) {
      missed = true
      return read(now)
    }
    const opening=handOpennessFromLandmarks(points)
    let point = {...next,hand_open:opening}, filteredPoints = points
    if (previous.point) {
      const dt = timestamp - previous.timestamp
      // Elapsed-time, not frame-count, smoothing: the same response at 15/30/60fps.
      const positionAlpha = -Math.expm1(-dt / 45), scaleAlpha = -Math.expm1(-dt / 80)
      const mix = (a,b,t) => a + (b-a)*t
      const angle = Math.atan2(previous.point.dy,previous.point.dx)
      const turn = Math.atan2(Math.sin(Math.atan2(next.dy,next.dx)-angle),Math.cos(Math.atan2(next.dy,next.dx)-angle))
      const filteredAngle = angle + turn * -Math.expm1(-dt / 60)
      const length = mix(Math.hypot(previous.point.dx,previous.point.dy),Math.hypot(next.dx,next.dy),positionAlpha)
      point = {x:mix(previous.point.x,next.x,positionAlpha),y:mix(previous.point.y,next.y,positionAlpha),
        palm_scale:mix(previous.point.palm_scale,next.palm_scale,scaleAlpha),
        dx:Math.cos(filteredAngle)*length,dy:Math.sin(filteredAngle)*length,
        hand_open:opening == null ? null : previous.point.hand_open == null ? opening
          : mix(previous.point.hand_open,opening,-Math.expm1(-dt/100))}
      filteredPoints=points.map((p,i)=>p && previous.landmarks[i] ? {
        x:mix(previous.landmarks[i].x,p.x,positionAlpha),y:mix(previous.landmarks[i].y,p.y,positionAlpha),
        ...(Number.isFinite(p.z) ? {z:Number.isFinite(previous.landmarks[i].z)
          ? mix(previous.landmarks[i].z,p.z,positionAlpha) : p.z} : {})} : p)
    }
    // Display debounce is longer than motion freshness. A held overlay never
    // renews timestamp; handFollowInputIssue still rejects motion at 300ms.
    observation = {point,landmarks:filteredPoints,timestamp,frameSeq,receivedAt:now,
      expiresAt:Math.min(now+PALM_HOLD_MS,timestamp+PALM_DISPLAY_MAX_AGE_MS)}
    missed = false
    return read(now)
  }
  return {update,read,reset}
}

export function handFollowFrameIssue(value, now = performance.now()) {
  if (value?.source === 'interaction' || value?.stream === 'interaction') return '合成输入不能用于摄像头跟随'
  if (!value?.session || value.stream !== 'color' || !value.fresh ||
      ![value.width,value.height,value.frameSeq].every(Number.isFinite) ||
      !(value.width > 0 && value.height > 0) || !(value.frameSeq > 0)) return '请在右上角开启有效彩色画面'
  // CameraFrame owns stream freshness (including a 1.5 s timeout) and clears
  // this input on loss. Do not apply a second wall-clock threshold here:
  // inference and camera sequence updates may be scheduled at different rates.
  return ''
}
export function handFollowInputIssue(value, now = performance.now()) {
  const frameIssue = handFollowFrameIssue(value, now)
  if (frameIssue) return frameIssue
  if (value.detectorError) return String(value.detectorError)
  if (!value.hands) return '手掌检测未开启'
  const p = value.point
  if (!p || ![p.x, p.y, p.palm_scale, p.dx, p.dy, value.timestamp].every(Number.isFinite) ||
      p.x < 0 || p.x > 1 || p.y < 0 || p.y > 1 || p.palm_scale <= 0 || Math.hypot(p.dx, p.dy) < 1e-6) return '请将手掌放入画面'
  const age = now - value.timestamp
  return age < 0 || age >= PALM_MAX_AGE_MS || value.trackingState === 'lost' ? '手掌检测已失效' : ''
}

// MJPEG <img> load/timers do not identify individual camera frames. Read the
// existing stream's per-part headers instead, so no JPEG can inflate inference FPS.
// Content-Length permits binary JPEG data and arbitrary transport chunk splits.
export function createCameraMultipartParser(onFrame) {
  const headerBytes = new Uint8Array(8192)
  let headerSize = 0, header = null, lastSeq = 0, remaining = 0, parts = []
  const decoder = new TextDecoder()
  return {
    push(chunk) {
      let offset = 0
      while (offset < chunk.length) {
        if (!header) {
          // Scan each header byte once. Never concatenate/rescan a growing
          // JPEG when the transport fragments it into small chunks.
          let complete = false
          while (offset < chunk.length) {
            if (headerSize >= headerBytes.length) throw new Error('相机帧头无效')
            headerBytes[headerSize++] = chunk[offset++]
            if (headerSize >= 4 && headerBytes[headerSize-4] === 13 && headerBytes[headerSize-3] === 10 &&
                headerBytes[headerSize-2] === 13 && headerBytes[headerSize-1] === 10) { complete = true; break }
          }
          if (!complete) return
          const text = decoder.decode(headerBytes.subarray(0, headerSize-4))
          const field = name => text.match(new RegExp(`^${name}:\\s*([^\\r\\n]+)`, 'im'))?.[1]
          header = { length:Number(field('Content-Length')), seq:Number(field('X-Frame-Seq')),
            stamp:Number(field('X-Frame-Timestamp')) }
          if (!Number.isSafeInteger(header.length) || header.length <= 0 || header.length > 8 * 1024 * 1024 ||
              !Number.isSafeInteger(header.seq) || header.seq <= 0 || !Number.isFinite(header.stamp) || header.stamp <= 0)
            throw new Error('相机帧缺少有效长度、序号或时间戳')
          remaining = header.length; parts = []; headerSize = 0
        }
        const take = Math.min(remaining,chunk.length-offset)
        if (take > 0 && header.seq > lastSeq) parts.push(chunk.subarray(offset,offset+take))
        offset += take; remaining -= take
        if (!remaining) {
          const packet = header; header = null
          if (packet.seq > lastSeq) {
            lastSeq = packet.seq
            // Blob owns immutable JPEG bytes; no intermediate contiguous JPEG
            // allocation or slice. Duplicate/older captures never build a Blob.
            const blob = new Blob(parts, {type:'image/jpeg'})
            parts = []
            onFrame({seq:packet.seq, stamp:packet.stamp, blob})
          }
        }
      }
    },
  }
}

export function measuredFrameRate(times, now, windowMs = 1000) {
  const recent = times.filter(t => t <= now && now - t < windowMs)
  return recent.length > 1 && now > recent[0] ? (recent.length - 1) * 1000 / (now - recent[0]) : 0
}

export function selectHandFollowSnapshot(snap, mode = 'sim') {
  return mode === 'real' ? snap?.real_hand_follow : snap?.hand_follow
}

// The endpoint is immutable for the lifetime of this token owner, including
// late replies and retirement. Only simulation supports the tab refresh handoff.
export function createHandFollowClient({ guard = () => '', onChange = () => {},
  fetcher = (...args) => fetch(...args), now = () => performance.now(), timeoutMs = 4000,
  endpoint = '/api/sim/hand-follow' } = {}) {
  if (!['/api/sim/hand-follow','/api/sim/mouse-follow','/api/real/hand-follow'].includes(endpoint)) throw new Error('跟随接口无效')
  const isReal = endpoint === '/api/real/hand-follow', label = isReal ? '真机' : '仿真'
  const isMouse = endpoint === '/api/sim/mouse-follow'
  let epoch = 0, token = null, session = null, disposed = false, detached = false, inFlight = false, sequence = 0
  let lastSend = -Infinity, lastObservationTimestamp = -Infinity, lastPresentFrameSeq = 0, sampleAbort = null
  let queuedInput = null, sendTimer = null, sampleTimer = null
  const commandTimers = new Set()
  // Motion heartbeat pacing only. Camera inference itself has no 30 Hz cap.
  const sampleInterval = 1000 / 30
  let value = { active: false, configured: false, busy: false, waiting:false, handSeen:false, rejected:false, operation:'', why: '', server: null }
  const snapshot = () => ({ ...value, seq:sequence })
  const emit = (patch) => { value = { ...value, ...patch }; if (!disposed) onChange(snapshot()) }
  async function post(action, body, signal) {
    const response = await fetcher(`${endpoint}/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), keepalive: action === 'stop', signal,
    })
    let result
    try { result = await response.json() }
    catch { throw new Error(`${label}接口返回无效（${response.status}）`) }
    if (!response.ok) throw new Error(result?.why || result?.state?.blocked_reason || result?.state?.why ||
      (typeof result?.detail === 'string' ? result.detail : '') || `${label}接口不可用（${response.status}）`)
    if (!result || typeof result.ok !== 'boolean') throw new Error(`${label}接口返回无效`)
    return result
  }
  function retire(oldToken) {
    if (oldToken == null || detached) return
    // Best effort only; the backend must independently enforce its watchdog.
    void post('stop', { token: oldToken }).catch(() => {})
  }
  function stop(why = '已停止') {
    if (disposed) return
    epoch++
    const previous = token
    token = null; session = null; sequence = 0
    // Retire ownership before abort: even a transport that ignores abort must
    // neither block the next session nor clear its new in-flight request later.
    const previousSample = sampleAbort
    sampleAbort = null; inFlight = false
    clearTimeout(sendTimer); sendTimer = null; queuedInput = null
    clearTimeout(sampleTimer); sampleTimer = null
    for (const timer of commandTimers) clearTimeout(timer)
    commandTimers.clear()
    previousSample?.abort()
    emit({ active: false, configured: false, busy: false, waiting:false, handSeen:false, rejected:false, operation:'', why })
    retire(previous)
  }
  function reject(why) { stop(why); emit({rejected:true}) }
  async function command(action, body) {
    if (disposed || value.busy || value.active) return false
    if (isReal && action === 'start' && body.confirm_real !== true) {
      emit({why:'请先确认启动真机跟随',rejected:true}); return false
    }
    const issue = guard(action)
    if (issue) { stop(issue); return false }
    if (action === 'start' && (!value.configured || token == null || session !== body.session)) {
      stop('请先建立工作区'); return false
    }
    if (action === 'configure') stop('正在建立工作区…')
    const current = ++epoch
    const previous = token
    emit({ busy: true, rejected:false, operation:action, why: action === 'start' ? '正在开始…' : '正在建立工作区…' })
    // Do not abort lifecycle requests: a late success must reveal its new token
    // so it can be explicitly stopped, rather than leaving an orphan session.
    const timer = setTimeout(() => { if (current === epoch) stop('请求超时，请重新建立工作区') }, action === 'configure' ? Math.max(timeoutMs, 30000) : timeoutMs)
    commandTimers.add(timer)
    try {
      const result = await post(action, action === 'start' ? { ...body, token: previous } : body)
      const returned = result.state?.token
      if (disposed || current !== epoch) {
        if (result.ok && typeof returned === 'string') retire(returned)
        return false
      }
      const changed = guard(action)
      if (changed || !result.ok || typeof returned !== 'string' || !returned || !result.state?.configured ||
          (action === 'start' && !result.state.active)) {
        if (result.ok && typeof returned === 'string') retire(returned)
        reject(changed || result.why || result.state?.blocked_reason || result.state?.why || '工作区未就绪'); return false
      }
      token = returned; session = body.session
      sequence = 0; lastSend = lastObservationTimestamp = -Infinity; lastPresentFrameSeq = 0
      emit({ server: result.state, configured: true, active: action === 'start', busy: false, waiting:!!result.state.waiting, operation:'',
        why: action === 'start' ? (isReal ?
          (result.state.phase === 'centering' ? '正在前往中心，J6归零' :
            result.state.phase === 'waiting_hand' ? '已到中心，等待手掌' : '等待手掌；不会自动居中')
          : '跟随中') : '工作区已建立，点击开始跟随' })
      return true
    } catch (error) {
      if (current === epoch) reject(error.message || `${label}连接失败`)
      return false
    } finally { clearTimeout(timer); commandTimers.delete(timer) }
  }
  function scheduleSample() {
    if (sendTimer != null || !queuedInput || inFlight || !value.active || disposed) return
    sendTimer = setTimeout(() => {
      sendTimer = null
      if (queuedInput) void sample(queuedInput)
    }, Math.max(1,Math.ceil(sampleInterval-(now()-lastSend))))
  }
  async function sample(latest) {
    if (!value.active || disposed) return
    // A single pending slot always holds the most recent camera observation.
    // Network latency cannot accumulate a queue of old palm positions.
    queuedInput = latest
    const time = now()
    const issue = guard('sample')
    if (issue) { stop(issue); return }
    // Real may wait for its first palm. Once seen, loss requires an explicit restart.
    // Never adopt a foreign camera session automatically.
    if (isReal && (latest?.source === 'interaction' || latest?.stream === 'interaction')) {
      stop('合成输入不能用于真机'); return
    }
    const syntheticValid = isMouse && latest?.source === 'interaction' && latest?.stream === 'interaction' &&
      latest.fresh && Number.isSafeInteger(latest.frameSeq) && latest.frameSeq > 0 &&
      [latest.timestamp,latest.point?.x,latest.point?.y,latest.point?.hand_open].every(Number.isFinite) &&
      [latest.point.x,latest.point.y,latest.point.hand_open].every(v=>v>=0 && v<=1) &&
      time >= latest.timestamp && time-latest.timestamp < PALM_MAX_AGE_MS
    const handMissing = latest?.session !== session || (isMouse ? !syntheticValid : !!handFollowInputIssue(latest, time))
    if (isReal && handMissing && value.handSeen) { stop('手掌丢失，已请求停止；请手动重新开始'); return }
    if (isReal && !handMissing && !value.handSeen) emit({handSeen:true})
    const sampleTimestamp = handMissing ? time : latest.timestamp
    if (!handMissing && latest.frameSeq <= lastPresentFrameSeq) { queuedInput = null; return }
    if (inFlight || time - lastSend < sampleInterval) { scheduleSample(); return }
    queuedInput = null
    clearTimeout(sendTimer); sendTimer = null
    // Transport heartbeats use send time, observations use capture time. They
    // are NOT one ordered stream: a valid 110ms inference can arrive after a
    // no-hand heartbeat sent 40ms ago. Only actual captures advance this clock.
    if (!handMissing && sampleTimestamp <= lastObservationTimestamp) return
    inFlight = true; lastSend = time
    if (!handMissing) {
      lastPresentFrameSeq = latest.frameSeq
      lastObservationTimestamp = sampleTimestamp
    }
    const controller = new AbortController()
    sampleAbort = controller
    const current = epoch, ownToken = token
    const p = handMissing ? {x:.5,y:.5,palm_scale:1,dx:1,dy:0} : latest.point
    const timer = setTimeout(() => {
      if (current !== epoch || sampleAbort !== controller) return
      if (isReal) { stop('采样超时，已请求停止；请手动重新开始'); return }
      sampleAbort = null; inFlight = false
      controller.abort()
      emit({waiting:true,why:'采样暂时超时，保持位置等待恢复'})
      scheduleSample()
    }, Math.min(timeoutMs, isReal ? 300 : 750))
    sampleTimer = timer
    try {
      const result = await post('sample', { token: ownToken, session, seq: ++sequence,
        // CameraFrame publishes display coordinates, including the visible mirror.
        // No second, hidden flip is applied on the way to the shared workspace.
        u:p.x, v:p.y, palm_scale:p.palm_scale, hand_present:!handMissing,
        ...(!handMissing ? {frame_seq:latest.frameSeq} : {}),
        ...(!handMissing && Number.isFinite(p.hand_open) ? {hand_open:p.hand_open} : {}),
        angle:Math.atan2(p.dy,p.dx)*180/Math.PI, age_ms:time-sampleTimestamp }, controller.signal)
      if (disposed || current !== epoch || token !== ownToken || sampleAbort !== controller) return
      const changed = guard('sample')
      if (changed || !result.state?.active || result.state.token !== ownToken) {
        reject(changed || result.why || result.state?.blocked_reason || result.state?.why || '跟随已停止'); return
      }
      if (isReal && !result.ok) { reject(result.why || result.state?.blocked_reason || result.state?.why || '真机采样被拒绝'); return }
      if (!result.ok) { emit({waiting:true,why:result.why || '等待新的有效手掌画面'}); return }
      // A sample response can update telemetry, never acquire or replace a token.
      const bridging = !isReal && !isMouse && result.state.input_phase === 'bridging'
      emit({ server: result.state, waiting:!bridging && (handMissing || !!result.state.waiting),
        why:handMissing && !bridging ? (isReal ?
          (result.state.phase === 'centering' ? '正在前往中心，J6归零' :
            result.state.phase === 'waiting_hand' ? '已到中心，等待手掌' : '等待手掌；不会自动居中')
          : isMouse ? '输入已释放，平滑返回中心并等待' :
          result.state.returning ? '手掌已丢失1秒，返回中心' : '短暂漏检，保持位置') : '' })
    } catch (error) {
      if (current === epoch && sampleAbort === controller) {
        if (isReal) reject(error.message || '采样连接中断，已请求停止')
        else emit({waiting:true,why:(error.message || '采样连接中断')+'，保持位置等待恢复'})
      }
    }
    finally {
      clearTimeout(timer)
      if (sampleTimer === timer) sampleTimer = null
      if (sampleAbort === controller) {
        sampleAbort = null; inFlight = false
        scheduleSample()
      }
    }
  }
  return {
    selectSnapshot: snap => selectHandFollowSnapshot(snap, isReal ? 'real' : 'sim'),
    // Credentials come exclusively from this tab's saved owned session, never
    // from a websocket snapshot alone. Resuming performs no HTTP operation.
    resumeOwned(saved, live, backendSession) {
      if (isReal || saved?.mode === 'real' || (saved?.endpoint && saved.endpoint !== endpoint) || saved?.endpoint === '/api/real/hand-follow' ||
          disposed || value.busy || value.active || guard('sample') ||
          typeof backendSession !== 'string' || !backendSession || saved?.backendSession !== backendSession ||
          saved?.active !== true || saved?.configured !== true ||
          live?.active !== true || live?.configured !== true ||
          typeof saved?.server?.token !== 'string' || !saved.server.token ||
          typeof saved?.server?.session !== 'string' || !saved.server.session ||
          saved.server.token !== live.token || saved.server.session !== live.session ||
          !Number.isSafeInteger(saved.seq) || saved.seq < 0 ||
          !Number.isSafeInteger(saved.server.seq) || saved.server.seq < -1 || saved.server.seq > saved.seq ||
          !Number.isSafeInteger(live.seq) || live.seq < -1 ||
          live.seq < saved.server.seq || Math.max(live.seq,saved.seq) >= Number.MAX_SAFE_INTEGER) return false
      epoch++
      token = saved.server.token; session = saved.server.session
      // A request may have been sent just before pagehide, so saved.seq can be
      // ahead of the last server acknowledgement. Never reuse either sequence.
      sequence = Math.max(live.seq,saved.seq)
      lastSend = lastObservationTimestamp = -Infinity; lastPresentFrameSeq = 0
      emit({server:{...live},configured:true,active:true,busy:false,waiting:!!live.waiting,operation:'',why:'已续接本标签页跟随'})
      return true
    },
    configure: (payload) => command('configure', isReal ? {...payload,rotation:false,depth_mode:'gesture'} : payload),
    start: (cameraSession, confirmation) => command('start', { session: cameraSession,
      ...(isReal ? {confirm_real:confirmation?.confirm_real === true} : {}) }),
    sample, stop,
    getState: snapshot,
    // Refresh handoff retires all local work without stopping the same server
    // session. This client can never be reused, including via a late callback.
    detach() {
      if (disposed) return
      if (isReal) { stop('页面已离开，已请求停止'); disposed = true; return }
      disposed = detached = true; epoch++; token = null; session = null
      clearTimeout(sendTimer); sendTimer = null; queuedInput = null
      clearTimeout(sampleTimer); sampleTimer = null
      for (const timer of commandTimers) clearTimeout(timer)
      commandTimers.clear()
      sampleAbort?.abort(); sampleAbort = null; inFlight = false
      value = {...value,active:false,configured:false,busy:false}
    },
    dispose() { stop('已离开跟随页面'); disposed = true },
  }
}
