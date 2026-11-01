// Offline only: real production helpers/Vue compiler, injected requests and timers.
// Run from repository root: node --test web/scripts/check-vision-workspace.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { createVisionPolling, mappedVisionObjects, visionCall, applyVisionSnapshot, visionSnapshotEpoch } from '../src/visionWorkspace.js'

const flush = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const mapped = (id, status = 'observed') => ({ id, status,
  mapped: { xyz_mm: [10, 20, 30], size_mm: [40, 50, 20], height_estimated: true } })
const snapshot = (patch = {}) => ({ ok: true, revision: 1, stale: false,
  config: { enabled: true, mapping: true }, result: { calibrated: true, objects: [mapped('V1')] }, ...patch })

function polling(t, options = {}) {
  const state = reactive({ revision: -1, stale: false, connected: false, result: null })
  const pending = [], timers = new Map(), delays = [], applied = [], signals = []
  let requests = 0, inFlight = 0, peak = 0, timerId = 0
  const stop = createVisionPolling({ state, visible: () => true,
    request: (path, payload, signal) => {
      assert.equal(path, 'state'); assert.equal(payload, undefined)
      requests++; inFlight++; peak = Math.max(peak, inFlight); signals.push(signal)
      const task = deferred(); pending.push(task)
      return task.promise.finally(() => { inFlight-- })
    },
    schedule: (fn, delay) => { delays.push(delay); timers.set(++timerId, fn); return timerId },
    cancel: id => timers.delete(id), apply: value => applied.push(value), ...options })
  t.after(stop)
  return { state, pending, timers, delays, applied, signals, stop,
    get requests() { return requests }, get peak() { return peak },
    fire() {
      const [id, fn] = timers.entries().next().value
      timers.delete(id)
      return fn()
    } }
}

test('real polling waits for each response before scheduling the next request', async t => {
  const p = polling(t)
  assert.equal(p.requests, 1); assert.equal(p.timers.size, 0)
  await flush(); assert.equal(p.requests, 1); assert.equal(p.timers.size, 0)
  p.pending[0].resolve(snapshot()); await flush()
  assert.equal(p.state.connected, true); assert.equal(p.timers.size, 1)
  assert.deepEqual(p.delays, [200]); assert.equal(p.applied.length, 1)
  const second = p.fire()
  assert.equal(p.requests, 2); assert.equal(p.timers.size, 0)
  await flush(); assert.equal(p.requests, 2)
  p.pending[1].resolve(snapshot()); await second
  assert.equal(p.peak, 1); assert.equal(p.applied.length, 1, 'same revision must not reapply')
  assert.equal(p.timers.size, 1)
})

test('hidden page uses slower scheduling and stale changes apply even at same revision', async t => {
  const p = polling(t, { visible: () => false })
  p.pending[0].resolve(snapshot()); await flush()
  assert.deepEqual(p.delays, [1000])
  const next = p.fire(); p.pending[1].resolve(snapshot({ stale: true })); await next
  assert.equal(p.applied.length, 2); assert.equal(p.state.stale, true)
  assert.deepEqual(mappedVisionObjects(p.state), [])
})

for (const outcome of ['resolve', 'reject']) {
  test(`dispose aborts request and ignores late ${outcome}, with no new timer`, async t => {
    const p = polling(t)
    p.stop()
    assert.equal(p.signals[0].aborted, true)
    assert.equal(p.state.connected, false)
    assert.deepEqual(p.applied, [null])
    p.pending[0][outcome](outcome === 'resolve' ? snapshot() : new Error('late network failure'))
    await flush()
    assert.equal(p.timers.size, 0); assert.equal(p.delays.length, 0)
    assert.equal(p.requests, 1); assert.equal(p.state.result, null)
    assert.deepEqual(p.applied, [null]); assert.equal(p.state.connected, false)
  })
}

test('dispose cancels an already scheduled timer; retained callback cannot restart polling', async t => {
  const p = polling(t)
  p.pending[0].resolve(snapshot()); await flush()
  const callback = [...p.timers.values()][0]
  p.stop(); assert.equal(p.timers.size, 0)
  await callback()
  assert.equal(p.requests, 1); assert.equal(p.timers.size, 0)
  assert.equal(p.applied.at(-1), null)
})

test('connection failure clears mapped results and emits null, then recovers', async t => {
  const p = polling(t)
  p.pending[0].resolve(snapshot()); await flush()
  assert.equal(mappedVisionObjects(p.state).length, 1)
  const failed = p.fire(); p.pending[1].reject(new Error('offline')); await failed
  assert.equal(p.state.connected, false); assert.equal(p.state.result, null)
  assert.match(p.state.error, /offline/); assert.equal(p.applied.at(-1), null)
  assert.deepEqual(mappedVisionObjects(p.state), [])
  assert.equal(p.timers.size, 1)
  const retry = p.fire(); p.pending[2].resolve(snapshot({ revision: 2 })); await retry
  assert.equal(p.state.connected, true); assert.equal(mappedVisionObjects(p.state).length, 1)
})

test('reconnection restores an unchanged uploaded result after clearing it on failure', async t => {
  const p = polling(t)
  p.pending[0].resolve(snapshot()); await flush()
  const failed = p.fire(); p.pending[1].reject(new Error('temporary disconnect')); await failed
  assert.equal(p.state.result, null)
  // Static uploads legitimately retain the same revision for their entire lifetime.
  const recovered = p.fire(); p.pending[2].resolve(snapshot()); await recovered
  assert.equal(p.state.connected, true)
  assert.equal(mappedVisionObjects(p.state).length, 1,
    'same revision must be restored when reconnecting after the client cleared its result')
})

test('mapping requires enabled mapping, fresh calibrated results and valid track status', () => {
  const state = snapshot()
  assert.deepEqual(mappedVisionObjects(state).map(v => v.id), ['V1'])
  for (const value of [null, {}, snapshot({ config: null }), snapshot({ stale: true }),
    snapshot({ config: { enabled: false, mapping: true } }),
    snapshot({ config: { enabled: true, mapping: false } }), snapshot({ result: null }),
    snapshot({ result: { calibrated: false, objects: [mapped('old')] } })]) {
    assert.deepEqual(mappedVisionObjects(value), [])
  }
  state.result.objects = [mapped('seen'), mapped('held', 'holding'), mapped('old', 'expired'),
    mapped('unknown', 'unknown'), { id: 'pixels', status: 'observed', mapped: null }]
  assert.deepEqual(mappedVisionObjects(state).map(v => v.id), ['seen', 'held'])
  state.result.objects = Array.from({ length: 80 }, (_, i) => mapped(`V${i}`))
  assert.equal(mappedVisionObjects(state).length, 50)
  assert.equal(state.result.objects.length, 80, 'mapping must not mutate the received result')
})

test('monotonic gate accepts equal/new revision and rejects earlier revision or invalid snapshots',()=>{
  const state=reactive({instance_id:'A',revision:10,config:{input:'virtual'}})
  for(const value of [snapshot({instance_id:'A',revision:9}),snapshot({instance_id:'A',revision:NaN}),snapshot({instance_id:'A',revision:1.5}),null])assert.equal(applyVisionSnapshot(state,value),false)
  assert.equal(state.config.input,'virtual')
  assert.equal(applyVisionSnapshot(state,snapshot({instance_id:'A',revision:10})),true)
  assert.equal(applyVisionSnapshot(state,snapshot({instance_id:'A',revision:11})),true)
})
test('new process accepts reset revision but retired process and pre-restart unknown response cannot return',()=>{
  const state=reactive({instance_id:'A',revision:100}),oldEpoch=visionSnapshotEpoch(state)
  assert.equal(applyVisionSnapshot(state,snapshot({instance_id:'B',revision:0}),oldEpoch),true)
  assert.equal(state.instance_id,'B');assert.equal(state.revision,0)
  assert.equal(applyVisionSnapshot(state,snapshot({instance_id:'A',revision:999}),visionSnapshotEpoch(state)),false)
  assert.equal(applyVisionSnapshot(state,snapshot({instance_id:'unseen-old',revision:999}),oldEpoch),false)
  assert.equal(applyVisionSnapshot(state,snapshot({instance_id:'C',revision:0}),visionSnapshotEpoch(state)),true)
  assert.equal(applyVisionSnapshot(state,snapshot({revision:999})),false,'missing identity cannot replace identified process')
})
test('late poll cannot replace newer config revision, including during recovery',async t=>{
  const p=polling(t)
  applyVisionSnapshot(p.state,snapshot({instance_id:'A',revision:20,config:{input:'virtual'}}))
  p.pending[0].resolve(snapshot({instance_id:'A',revision:19,config:{input:'camera'}}));await flush()
  assert.equal(p.state.config.input,'virtual');assert.equal(p.state.revision,20);assert.equal(p.applied.length,0)
  const next=p.fire();p.pending[1].resolve(snapshot({instance_id:'A',revision:20,config:{input:'virtual'}}));await next
  assert.equal(p.state.connected,true);assert.equal(p.applied.length,1)
})
test('poll accepts restart and ignores retired high-revision response without applying old results',async t=>{
  const p=polling(t);p.pending[0].resolve(snapshot({instance_id:'A',revision:50}));await flush()
  const restart=p.fire();p.pending[1].resolve(snapshot({instance_id:'B',revision:0}));await restart
  assert.equal(p.state.instance_id,'B');assert.equal(p.state.revision,0)
  const old=p.fire();p.pending[2].resolve(snapshot({instance_id:'A',revision:100}));await old
  assert.equal(p.state.instance_id,'B');assert.equal(p.applied.length,2)
})
test('config already applied to shared state still publishes cleared result to scene exactly once',async t=>{
  const p=polling(t);p.pending[0].resolve(snapshot({instance_id:'A',revision:1}));await flush()
  const updated=snapshot({instance_id:'A',revision:2,config:{enabled:false,mapping:false},result:null})
  applyVisionSnapshot(p.state,updated)
  const next=p.fire();p.pending[1].resolve(updated);await next
  assert.equal(p.applied.length,2);assert.equal(p.applied.at(-1).result,null)
  const unchanged=p.fire();p.pending[2].resolve(updated);await unchanged
  assert.equal(p.applied.length,2)
})

test('real client enforces fixed routes, handles HTTP/JSON errors and propagates cancellation', async t => {
  const calls = []
  t.mock.method(globalThis, 'fetch', async (...args) => {
    calls.push(args); return { ok: true, json: async () => ({ ok: true }) }
  })
  await visionCall('state')
  assert.equal(calls[0][0], '/api/vision/workspace/state')
  assert.equal(calls[0][1].method, 'GET'); assert.equal(calls[0][1].cache, 'no-store')
  await visionCall('config', { mapping: false })
  assert.equal(calls[1][1].method, 'POST')
  assert.deepEqual(JSON.parse(calls[1][1].body), { mapping: false })
  await assert.rejects(visionCall('http://external.invalid'), /无效/)
  assert.equal(calls.length, 2)
  globalThis.fetch = async () => ({ ok: false, json: async () => ({ ok: false, why: 'busy' }) })
  await assert.rejects(visionCall('state'), /busy/)
  globalThis.fetch = async () => ({ ok: false, status:429, json: async () => ({ ok: false, why: '视觉配置任务繁忙' }) })
  await assert.rejects(visionCall('virtual',{session:'s',image_base64:'AAAA'}),error=>error.status===429 && error.why==='视觉配置任务繁忙')
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: false, why: 'bad image' }) })
  await assert.rejects(visionCall('image', { image_base64: 'AAAA' }), /bad image/)
  globalThis.fetch = async () => ({ ok: true, json: async () => { throw new SyntaxError('broken JSON') } })
  await assert.rejects(visionCall('state'), /broken JSON/)
  const controller = new AbortController()
  globalThis.fetch = async (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true })
  })
  const task = visionCall('state', undefined, controller.signal)
  controller.abort(); await assert.rejects(task, error => error.name === 'AbortError')
})

for (const file of ['panels/VisionPanel.vue', 'components/CameraSettings.vue']) {
  test(`${file}: real Vue script, template and every style compile`, () => {
    const source = readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8')
    const parsed = parse(source, { filename: file })
    assert.deepEqual(parsed.errors, [])
    const descriptor = parsed.descriptor
    const script = compileScript(descriptor, { id: 'vision-workspace-regression' })
    assert.ok(script.content.length > 0)
    assert.ok(descriptor.template, 'component template missing')
    const template = compileTemplate({ source: descriptor.template.content, filename: file,
      id: 'vision-workspace-regression', compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(template.errors, [])
    for (const style of descriptor.styles) {
      assert.deepEqual(compileStyle({ source: style.content, filename: file,
        id: 'vision-workspace-regression', scoped: style.scoped }).errors, [])
    }
  })
}
