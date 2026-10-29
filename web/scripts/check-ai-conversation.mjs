// Real store AI actions + fake HTTP only. No server, model or hardware.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useWorld } from '../src/store.js'

const plan = () => ({ ok: true, kind: 'plan', plan_id: 'token', ttl_s: 120,
  summary: '待确认', plan: [{ op: 'wait', sec: 1 }], code: 'WAIT 1', warnings: [] })
function harness(handler = () => plan(), { revoke = () => ({ ok: true }) } = {}) {
  setActivePinia(createPinia())
  const w = useWorld()
  w.connected = true
  w.snap = { t: Date.now()/1000, mode: 'sim', session_id: 's1', config_rev: 1, motion_granted: true,
    moving: false, safety: { level: 'green' }, joints: { known: true, q: [0, 0, 0, 0, 0, 0] } }
  const calls = [], previous = globalThis.fetch
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body); calls.push([url, body])
    const r = await (url.endsWith('/revoke') ? revoke(body) : handler(url, body))
    return { ok: true, status: 200, json: async () => r }
  }
  return { w, calls, close() { w.aiDiscard(); globalThis.fetch = previous } }
}
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }

test('real AI store publishes plan inside its own assistant message and never sends current input twice', async () => {
  const h = harness()
  try {
    await h.w.aiSay('等待1秒')
    assert.equal(h.calls.length, 1); assert.deepEqual(h.calls[0][1].history, [])
    assert.equal(h.w.aiHistory.length, 2)
    assert.equal(h.w.aiHistory[1].result, h.w.aiPlan)
    assert.equal(h.w.aiReason(h.w.aiPlan), '')
    assert.match(h.w.aiReason({ ...h.w.aiPlan }), /历史消息/)
  } finally { h.close() }
})

for (const kind of ['chat', 'clarify', 'failure', 'empty']) test(`${kind} never inherits previous plan`, async () => {
  let turn = 0
  const h = harness(() => ++turn === 1 ? plan() : kind === 'failure' ? { ok: false, error: '失败' }
    : kind === 'empty' ? { ...plan(), plan: [] } : { ok: true, kind, summary: '回复', plan: [], questions: ['请补充'] })
  try {
    await h.w.aiSay('等待1秒'); const old = h.w.aiPlan
    await h.w.aiSay('你好')
    assert.equal(h.w.aiPlan, null); assert.equal(old.status, 'invalid')
    assert.equal((await h.w.aiRun(old)).ok, false)
    assert.equal(h.calls.filter(c => c[0].endsWith('/run')).length, 0)
  } finally { h.close() }
})

for (const change of ['mode', 'session', 'tool', 'scene', 'config', 'disconnect', 'fault']) test(`${change} invalidates ready and late plans without resurrection`, async () => {
  const late = deferred(); let turn = 0
  const h = harness(() => ++turn === 1 ? plan() : late.promise)
  const mutate = () => {
    if (change === 'mode') h.w.snap.mode = 'real'
    if (change === 'session') h.w.snap.session_id = 's2'
    if (change === 'tool') h.w.tools = { mounted: { tool_id: 'new', revision: 1 } }
    if (change === 'scene') h.w.snap.objects = [{ id: 1, xyz: [1, 2, 3] }]
    if (change === 'config') h.w.snap.config_rev++
    if (change === 'disconnect') h.w.connected = false
    if (change === 'fault') h.w.snap.safety.level = 'red'
  }
  try {
    await h.w.aiSay('等待1秒'); const old = h.w.aiPlan
    const pending = h.w.aiSay('等待2秒'); mutate()
    late.resolve(plan()); const r = await pending
    assert.equal(r.ok, false); assert.equal(h.w.aiPlan, null); assert.equal(old.status, 'invalid')
    assert.equal((await h.w.aiRun(old)).ok, false)
  } finally { h.close() }
})

test('duplicate chat/run singleflight, no automatic motion or permission revival after error', async () => {
  const chat = deferred(), run = deferred()
  const h = harness(url => url.endsWith('/chat') ? chat.promise : run.promise)
  try {
    const first = h.w.aiSay('等待1秒')
    assert.equal((await h.w.aiSay('重复')).ok, false); assert.equal(h.calls.length, 1)
    chat.resolve(plan()); await first
    const p = h.w.aiPlan
    assert.equal(h.calls.filter(c => !c[0].endsWith('/revoke')).length, 1)
    const executing = h.w.aiRun(p)
    assert.equal(h.w.aiPlan, null)
    assert.equal((await h.w.aiRun(p)).ok, false); assert.equal(h.calls.length, 2)
    run.resolve({ ok: false, why: '路径风险' }); await executing
    assert.equal(p.status, 'failed'); assert.equal(p.reason, '路径风险')
    assert.equal(h.w.aiPlan, null); assert.equal((await h.w.aiRun(p)).ok, false)
    assert.equal(h.calls.length, 2)
  } finally { h.close() }
})

test('unknown feedback and safety, missing permission, moving and expiry refuse with visible reason', async () => {
  const h = harness()
  try {
    for (const [field, value, pattern] of [
      ['motion_granted', false, /没有运动许可/], ['moving', undefined, /静止反馈/],
      ['safety', { level: 'unknown' }, /安全状态未知/], ['joints', { known: false, q: [0, 0, 0, 0, 0, 0] }, /反馈未知/],
    ]) {
      await h.w.aiSay('等待1秒'); const p = h.w.aiPlan
      const before = h.w.snap[field]; h.w.snap[field] = value
      assert.match(h.w.aiReason(p), pattern); assert.equal((await h.w.aiRun(p)).ok, false)
      h.w.snap[field] = before
    }
    await h.w.aiSay('等待1秒'); const p = h.w.aiPlan
    p.expires_at = Date.now()-1
    assert.match(h.w.aiReason(p), /过期/); assert.equal(h.w.aiPlan, null)
    assert.equal(h.calls.filter(c => c[0].endsWith('/run')).length, 0)
  } finally { h.close() }
})

test('transport failures are explicit; failed run never retries even after context changes', async () => {
  const h = harness(url => { if (url.endsWith('/chat')) return plan(); throw new Error('offline') })
  try {
    await h.w.aiSay('等待1秒'); const p = h.w.aiPlan
    const r = await h.w.aiRun(p)
    assert.equal(r.ok, false); assert.match(p.reason, /结果未知/)
    h.w.snap.session_id = 'changed'
    assert.equal((await h.w.aiRun(p)).ok, false); assert.equal(h.calls.filter(c => !c[0].endsWith('/revoke')).length, 2)
  } finally { h.close() }
})

test('explicit discard revokes exact server request and blocks history without motion', async () => {
  const h = harness()
  try {
    await h.w.aiSay('等待1秒'); const p = h.w.aiPlan
    await h.w.aiDiscard('丢弃')
    const revoke = h.calls.find(c => c[0].endsWith('/revoke'))
    assert.equal(revoke[1].plan_id, p.plan_id); assert.equal(revoke[1].request_id, p.request_id)
    assert.equal((await h.w.aiRun(p)).ok, false)
    assert.equal(h.calls.filter(c => c[0].endsWith('/run')).length, 0)
  } finally { h.close() }
})

test('stale feedback, active follow and paused debug reject with explicit reasons', async () => {
  const h = harness()
  try {
    await h.w.aiSay('等待1秒'); const p = h.w.aiPlan
    h.w.snap.t = (Date.now()-5000)/1000
    assert.match(h.w.aiReason(p), /反馈未知或过期/)
    assert.equal((await h.w.aiRun(p)).ok, false)
    h.w.snap.t = Date.now()/1000
    h.w.snap.hand_follow = { active: true }
    assert.match(h.w.aiReason(p), /占用/)
    h.w.snap.hand_follow.active = false; h.w.snap.exec = { state: 'paused' }
    assert.match(h.w.aiReason(p), /占用/)
    assert.equal(h.calls.filter(c => c[0].endsWith('/run')).length, 0)
  } finally { h.close() }
})

test('late run reply cannot revive permission after a context change', async () => {
  const pending = deferred()
  const h = harness(url => url.endsWith('/run') ? pending.promise : plan())
  try {
    await h.w.aiSay('等待1秒'); const p = h.w.aiPlan, task = h.w.aiRun(p)
    h.w.snap.session_id = 'new'
    pending.resolve({ ok: true }); const r = await task
    assert.equal(r.ok, false); assert.equal(p.status, 'invalid'); assert.equal(h.w.aiPlan, null)
    assert.equal((await h.w.aiRun(p)).ok, false)
    assert.equal(h.calls.filter(c => c[0].endsWith('/run')).length, 1)
  } finally { h.close() }
})

test('next chat revokes server token first, and revoke failure publishes no new plan', async () => {
  let revokes = 0
  const h = harness(() => plan(), { revoke: () => ++revokes === 1 ? { ok: false } : { ok: true } })
  try {
    await h.w.aiSay('等待1秒'); const old = h.w.aiPlan
    const r = await h.w.aiSay('你好')
    assert.equal(r.ok, false); assert.equal(h.w.aiPlan, null); assert.equal(old.status, 'invalid')
    assert.equal(h.calls.filter(c => c[0].endsWith('/chat')).length, 1)
    assert.equal(h.calls[1][0], '/api/ai/revoke')
    assert.match(h.w.aiHistory.at(-1).content, /无法确认旧计划已撤销/)
    assert.equal((await h.w.aiRun(old)).ok, false)
  } finally { h.close() }
})

test('model transport failure after a previous plan cannot leave the previous server token usable', async () => {
  let turn = 0
  const h = harness(() => { if (++turn === 1) return plan(); throw new Error('chat offline') })
  try {
    await h.w.aiSay('等待1秒'); const previous = h.w.aiPlan
    await h.w.aiSay('你好')
    assert.equal(h.w.aiPlan, null)
    assert.equal(h.calls[1][0], '/api/ai/revoke')
    assert.equal(h.calls[1][1].plan_id, previous.plan_id)
    assert.equal(h.calls[2][0], '/api/ai/chat')
    assert.equal(h.calls[3][0], '/api/ai/revoke')
    assert.equal((await h.w.aiRun(previous)).ok, false)
  } finally { h.close() }
})
