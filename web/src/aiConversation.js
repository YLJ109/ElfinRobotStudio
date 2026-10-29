// Ephemeral, message-owned AI authority. Nothing here is persisted or replayed.
import { watch } from 'vue'

export function aiContext(w) {
  const s = w.snap
  return JSON.stringify([w.connected, s?.session_id, s?.mode, s?.config_rev, s?.tcp_frame,
    s?.tools, w.tools, w.points, (s?.objects || []).map(o => [o.id, o.xyz, o.size, o.rotation, o.collide]),
    s?.real?.connected, s?.real?.epoch])
}

const controllers = new WeakMap()
export function aiFor(w, post) {
  if (controllers.has(w)) return controllers.get(w)
  let revision = 0, serial = 0, pending = false, running = false, timer, feedbackTimer, activeRequest = null
  const freshFeedback = () => Number.isFinite(w.snap?.t) && Math.abs(Date.now() - w.snap.t * 1000) <= 2000
  function invalidate(why = '计划已撤销，请重新提出本次要求。') {
    revision++
    clearTimeout(timer)
    clearTimeout(feedbackTimer)
    if (w.aiPlan) { w.aiPlan.status = 'invalid'; w.aiPlan.reason = why }
    w.aiPlan = null
  }
  function faultReason() {
    if (w.connected !== true || !w.snap?.session_id) return '连接或会话失效，请重新生成。'
    if (!w.snap?.motion_granted) return '当前没有运动许可；旧计划已失效，请重新生成。'
    if (!['green', 'yellow'].includes(w.snap?.safety?.level)) return '安全状态未知或异常；旧计划已失效，请重新生成。'
    if (w.snap?.joints?.known === false) return '当前关节反馈未知；旧计划已失效，请重新生成。'
    return ''
  }
  async function discard(why) {
    const request_id = activeRequest, plan_id = w.aiPlan?.plan_id
    activeRequest = null
    invalidate(why)
    if (!request_id && !plan_id) return { ok: true }
    try {
      const r = await post('/api/ai/revoke', { request_id, plan_id })
      if (!r.ok) throw new Error('revoke refused')
      return r
    } catch {
      const error = '本地计划已撤销，但无法确认服务端撤销；请重新提问或等待原计划过期。'
      w.error = error
      return { ok: false, error }
    }
  }
  watch(() => JSON.stringify([aiContext(w), faultReason()]), () => { void discard(faultReason() || '连接、会话、模式、工具或现场已变化，请重新生成。') }, { flush: 'sync' })
  function armFeedbackExpiry() {
    clearTimeout(feedbackTimer)
    if (!w.aiPlan) return
    const remaining = Number.isFinite(w.snap?.t) ? w.snap.t * 1000 + 2001 - Date.now() : 0
    feedbackTimer = setTimeout(() => { if (!freshFeedback()) void discard('服务器状态反馈已过期，请恢复连接后重新生成。') }, Math.max(0, Math.min(2001, remaining)))
    feedbackTimer?.unref?.()
  }
  watch(() => w.snap?.t, armFeedbackExpiry, { flush: 'sync' })
  function reason(message) {
    const p = w.aiPlan
    if (!p || !message || p.message_id !== message.message_id || p !== message)
      return message?.reason || '这条历史消息没有当前执行许可。'
    if (p.context !== aiContext(w) || Date.now() >= p.expires_at) {
      void discard('计划已过期或现场变化，请重新生成。'); return p.reason
    }
    if (p.status !== 'ready') return p.reason || '计划当前不可执行。'
    if (pending || running) return '请求处理中，请勿重复提交。'
    if (!freshFeedback()) return '服务器状态反馈未知或过期，不能执行。'
    if (faultReason()) return faultReason()
    if (!w.snap?.motion_granted) return '当前没有运动许可。'
    if (!['green', 'yellow'].includes(w.snap?.safety?.level)) return '安全状态未知，不能确认执行。'
    if (w.snap?.joints?.known === false || !Array.isArray(w.snap?.joints?.q)
      || w.snap.joints.q.length !== 6 || !w.snap.joints.q.every(Number.isFinite)) return '当前关节反馈未知。'
    if (w.snap?.moving !== false) return '等待有效的静止反馈。'
    if (w.snap?.hand_follow?.active || w.snap?.hand_follow?.returning || w.snap?.real_hand_follow?.active
      || ['running', 'waiting', 'paused'].includes(w.snap?.exec?.state)
      || ['running', 'paused'].includes(w.snap?.exec?.queue_state) || w.snap?.free?.on)
      return '跟随、程序、调试或手拖正在占用机器人，请先主动停止原作业。'
    return ''
  }
  async function say(text) {
    if (pending || running) return { ok: false, error: '请求处理中，请稍后重试。' }
    if (typeof text !== 'string' || !text.trim() || text.length > 2000)
      return { ok: false, error: '请输入 1～2000 字的本次要求。' }
    const previousRequest = activeRequest, previousPlan = w.aiPlan?.plan_id
    invalidate('已提出新的要求，旧计划不再可执行。')
    pending = true; w.aiPending = true
    const request = revision, context = aiContext(w), id = `ai-${++serial}`
    const request_id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${serial}-${Math.random().toString(36).slice(2)}`
    activeRequest = request_id
    // Snapshot BEFORE appending current input: never send it twice.
    const history = w.aiHistory.slice(-6).map(({ role, content }) => ({ role, content }))
    w.aiHistory.push({ role: 'user', content: text })
    const message = { role: 'assistant', content: '思考中…', result: { message_id: id, status: 'thinking' } }
    w.aiHistory.push(message)
    const slot = w.aiHistory[w.aiHistory.length - 1]
    try {
      // Revoke before generating, even if the following chat never reaches the
      // server. Exact request IDs make delayed revokes harmless to newer turns.
      if (previousRequest || previousPlan) {
        const revoked = await post('/api/ai/revoke', { request_id: previousRequest, plan_id: previousPlan })
        if (!revoked.ok) throw new Error('无法确认旧计划已撤销；没有发送新请求，请重试。')
      }
      if (request !== revision || context !== aiContext(w)) {
        slot.content = '请求期间现场已变化，没有生成计划。'
        slot.result = { message_id: id, status: 'invalid', reason: slot.content }
        return { ok: false, error: slot.content }
      }
      const r = await post('/api/ai/chat', { text, history, request_id })
      if (request !== revision || context !== aiContext(w)) {
        slot.content = '这条回答已失效，请按当前现场重新提问。'
        slot.result = { message_id: id, status: 'invalid', reason: slot.content }
        return { ok: false, error: slot.content }
      }
      const isPlan = r.ok && r.kind === 'plan' && r.plan_id && r.plan?.length && typeof r.code === 'string' && r.code.trim()
      if (r.kind === 'plan' && !isPlan) throw new Error(r.error || '没有有效动作或已验证代码，未发布计划。')
      slot.content = r.ok ? r.summary : (r.error || 'AI 调用失败；没有计划。')
      slot.result = { ...r, request_id, message_id: id, status: isPlan ? 'ready' : r.ok ? r.kind : 'failed',
        context, expires_at: Date.now() + Math.min(120, Number(r.ttl_s) || 120) * 1000 }
      if (isPlan) {
        w.aiPlan = slot.result
        armFeedbackExpiry()
        timer = setTimeout(() => { void discard('计划已过期，请重新生成并确认。') }, Math.max(0, slot.result.expires_at - Date.now()))
        timer?.unref?.()
      }
      if (!r.ok) w.error = slot.content
      return r
    } catch (e) {
      const error = e?.message || 'AI 请求失败；未生成执行许可。'
      if (activeRequest === request_id) await discard('请求失败，执行许可已撤销。')
      slot.content = error; slot.result = { message_id: id, status: 'failed', reason: error }
      w.error = error
      return { ok: false, error }
    } finally { pending = false; w.aiPending = false }
  }
  async function run(message) {
    const why = reason(message)
    if (why) return { ok: false, why }
    const p = w.aiPlan
    const request = revision, context = aiContext(w)
    // Consume the local permission BEFORE the request, including failed requests.
    running = true; w.aiExecuting = true; p.status = 'submitting'; p.reason = '提交中；不会自动重试。'
    clearTimeout(timer); clearTimeout(feedbackTimer); w.aiPlan = null
    try {
      const r = await post('/api/ai/run', { plan: p.plan, plan_id: p.plan_id })
      if (request !== revision || context !== aiContext(w)) {
        p.status = 'invalid'; p.reason = '提交期间现场已变化；旧请求结果不能作为当前执行许可，请核对执行反馈。'
        return { ok: false, why: p.reason }
      }
      p.status = r.ok ? 'submitted' : 'failed'
      p.reason = r.ok ? '已提交；是否到达请核对执行反馈。' : (r.why || '执行被拒，请重新生成。')
      if (!r.ok) w.error = p.reason
      return r
    } catch (e) {
      p.status = 'failed'; p.reason = '执行请求失败或结果未知；不会重发，请核对现场。'
      w.error = p.reason
      return { ok: false, why: p.reason }
    } finally { running = false; w.aiExecuting = false }
  }
  const api = { say, run, reason, invalidate, discard }
  controllers.set(w, api)
  return api
}
