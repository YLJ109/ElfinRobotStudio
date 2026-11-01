// Offline only: pure helpers, real SFC compilation and mocked component requests.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { ref, computed, watch, reactive, effectScope } from 'vue'
import * as helpers from '../src/taskStudio.js'

const source = readFileSync(new URL('../src/panels/TaskStudioPanel.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor
const defaults = { center: '350, 0, 300', rpy: '180, 0, 0', width: 100, height: 60, spacing: 10, radius: 50, segments: 32, speed: 10,
  pick: [300, -100, 300, 180, 0, 0], place: [300, 100, 300, 180, 0, 0], lift: 50,
  rows: 2, cols: 3, layers: 2, step_x: 40, step_y: 40, step_z: 30 }

test('payloads contain only fields used by each supported generator', () => {
  for (const kind of ['raster', 'circle', 'pick-place', 'pallet']) {
    const result = helpers.buildGeneratePayload(kind, ' 测试 ', defaults)
    assert.equal(result.name, '测试'); assert.equal(result.kind, kind)
    assert.equal(result.options.speed, 10)
    if (kind === 'circle') { assert.equal(result.options.radius, 50); assert.equal(result.options.pick, undefined); assert.equal(result.options.width, undefined) }
    if (kind === 'pallet') assert.equal(result.options.cols, 3)
  }
  assert.deepEqual(helpers.buildGeneratePayload('raster', '扫描', defaults).options.center, [350, 0, 300])
})
test('malformed numbers, vectors, sizes and excessive trajectories are rejected', () => {
  const build = patch => helpers.buildGeneratePayload('raster', '扫描', { ...defaults, ...patch })
  for (const patch of [{ center: '1,,3' }, { rpy: '1,2' }, { speed: '' }, { speed: 101 }, { width: -1 }, { spacing: 0 }, { height: 10000, spacing: .1 }]) assert.throws(() => build(patch))
  assert.throws(() => helpers.buildGeneratePayload('unknown', '测试', defaults))
  assert.throws(() => helpers.buildGeneratePayload('circle', '', defaults))
  assert.throws(() => helpers.buildGeneratePayload('pallet', '测试', { ...defaults, rows: 1.2 }))
  assert.throws(() => helpers.buildGeneratePayload('pallet', '测试', { ...defaults, rows: 100, cols: 100 }))
  assert.throws(() => helpers.parseVector([null, 2, 3], 3))
})
test('display filtering preserves source indexes and never mutates data', () => {
  const recordings = [{ id: 'a', name: '轨迹 Alpha' }, null, { id: 'b', name: 'Beta' }, {}]
  assert.deepEqual(helpers.filterRecordings(recordings, 'ALPHA').map(v => v.id), ['a'])
  assert.deepEqual(helpers.filterRecordings(null), [])
  const points = [[1, 2], [3, 4], [5, 6]]
  assert.deepEqual(helpers.filterTrajectory(points, '', [2], true).map(v => v.index), [2])
  assert.deepEqual(helpers.filterTrajectory(points, '3,4').map(v => v.index), [1])
  assert.equal(points.length, 3)
})
test('simplification keeps endpoints and inspection rejects invalid six-axis data', () => {
  const points = Array.from({ length: 6 }, (_, i) => Array(6).fill(i))
  assert.deepEqual(helpers.simplifyTrajectory(points, 2), [points[0], points[2], points[4], points[5]])
  assert.deepEqual(helpers.inspectionPayload({ joints: points }), { joints: points, speed: 10 })
  assert.throws(() => helpers.inspectionPayload({ poses: [[1, 2]] }))
  assert.throws(() => helpers.inspectionPayload({ poses: [] }))
})
test('revision payload validates and copies the loaded revision with edited trajectory only', () => {
  const item = { revision: 7, joints: [Array(6).fill(1)], poses: [Array(6).fill(2)], points: [{ t: 0 }], count: 1 }
  assert.deepEqual(helpers.buildRevisionPayload(item, ' 编辑 '), { name: '编辑', revision: 7, joints: item.joints })
  for (const revision of [undefined, null, '7', -1, 1.5, Infinity]) assert.throws(() => helpers.buildRevisionPayload({ ...item, revision }, '编辑'))
  assert.throws(() => helpers.buildRevisionPayload(item, ' '))
})
test('execution requires connected known simulation, explicit confirmation and a clean report', () => {
  const good = { connected: true, mode: 'sim', confirmed: true, busy: false, inspection: { plan_id: 'checked', report: { risk_detected: false } } }
  assert.equal(helpers.canRunStudio(good), true)
  for (const patch of [{ connected: false }, { mode: 'real' }, { mode: undefined }, { confirmed: false }, { busy: true }, { inspection: { plan_id: 'x', report: {} } }, { inspection: { plan_id: 'x', report: { risk_detected: true } } }]) assert.equal(helpers.canRunStudio({ ...good, ...patch }), false)
})
test('Vue script, template and scoped styles compile', () => {
  const script = compileScript(descriptor, { id: 'task-studio' })
  assert.equal(compileTemplate({ source: descriptor.template.content, filename: 'TaskStudioPanel.vue', id: 'task-studio', compilerOptions: { bindingMetadata: script.bindings } }).errors.length, 0)
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: 'TaskStudioPanel.vue', id: 'task-studio', scoped: true }).errors, [])
})

function harness(fetchImpl, overrides = {}) {
  const mounted = [], unmounted = [], activated = [], deactivated = [], calls = [], paths = []
  const world = reactive({ connected: true, snap: { mode: 'sim' }, call: async (...args) => { calls.push(args); return { ok: true } } })
  const scope = effectScope()
  const code = compileScript(descriptor, { id: 'task-studio' }).content
    .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
  const deps = { ...helpers, ref, computed, watch, useWorld: () => world,
    onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn), onActivated: fn => activated.push(fn), onDeactivated: fn => deactivated.push(fn),
    fetch: fetchImpl, window: { __elfinScene: { setPlanPath: p => paths.push(p) } }, ...overrides }
  const component = new Function(...Object.keys(deps), code)(...Object.values(deps))
  const api = scope.run(() => component.setup({ initialTab: 'recordings' }, { expose() {} }))
  return { api, mounted, activated, deactivated, world, calls, paths, dispose() { unmounted.forEach(fn => fn()); scope.stop() } }
}
const response = body => ({ ok: true, json: async () => body })
test('mount reads catalog only; checking previews then one explicit sim run consumes its plan', async () => {
  const requests = []
  const h = harness(async (path, options) => {
    requests.push([path, options])
    if (path.endsWith('/inspect')) return response({ ok: true, plan_id: 'p1', report: { risk_detected: false, tcp_path: [[1, 2, 3]] } })
    return response({ ok: true, recordings: [], recording: { active: false, count: 0 } })
  })
  try {
    await h.mounted[0]()
    assert.deepEqual(requests.map(r => r[0]), ['/api/studio'])
    h.api.adopt({ name: 'test', poses: [Array(6).fill(0)] })
    h.api.inspect(); await new Promise(resolve => setImmediate(resolve))
    assert.equal(h.api.inspection.value.plan_id, 'p1')
    assert.equal(h.api.tab.value, 'acceptance')
    assert.deepEqual(h.paths.at(-1), [[1, 2, 3]])
    h.api.run(); assert.equal(requests.length, 2)
    h.api.confirmed.value = true; h.api.run(); await new Promise(resolve => setImmediate(resolve))
    assert.equal(requests.at(-1)[0], '/api/studio/run')
    assert.deepEqual(JSON.parse(requests.at(-1)[1].body), { plan_id: 'p1' })
    assert.equal(h.api.inspection.value, null)
    h.api.run(); assert.equal(requests.length, 3)
    await h.api.stop(); assert.deepEqual(h.calls, [['/api/program/stop', {}]])
  } finally { h.dispose() }
})
test('cancel and unmount abort outstanding requests and reject late responses', async () => {
  let finish, signal
  const h = harness((_path, options) => { signal = options.signal; return new Promise(resolve => { finish = resolve }) })
  const pending = h.mounted[0]()
  h.api.cancel(); assert.equal(signal.aborted, true)
  finish(response({ ok: true, recordings: [{ id: 'stale' }] })); await pending
  assert.deepEqual(h.api.catalog.value.recordings, [])
  const next = h.api.refresh(); h.dispose(); assert.equal(signal.aborted, true)
  finish(response({ ok: true, recordings: [{ id: 'disposed' }] })); await next
  assert.deepEqual(h.api.catalog.value.recordings, [])
})
test('HTTP failures remain visible and mode changes revoke inspected plans', async () => {
  const h = harness(async () => ({ ok: false, status: 409, json: async () => ({ ok: false, why: '服务未就绪' }) }))
  try {
    await h.mounted[0](); assert.equal(h.api.error.value, '服务未就绪')
    h.api.inspection.value = { plan_id: 'old', report: { risk_detected: false } }; h.api.confirmed.value = true
    h.world.snap.mode = 'real'
    await Promise.resolve()
    assert.equal(h.api.runnable.value, false)
    assert.equal(h.api.inspection.value, null)
  } finally { h.dispose() }
})
test('saved and edited recordings inspect local points with explicit speed and save a new revision', async () => {
  const requests = [], original = { id: 'r/a', name: '录制', revision: 1, joints: [Array(6).fill(0), Array(6).fill(1), Array(6).fill(2)] }
  const h = harness(async (path, options) => {
    requests.push([path, options])
    if (path.endsWith('/inspect')) return response({ ok: true, plan_id: 'checked', report: { risk_detected: false, tcp_path: [] } })
    if (options.method === 'PUT') return response({ ok: true, recording: { ...original, ...JSON.parse(options.body), revision: 2 } })
    return response({ ok: true, recordings: [], recording: { active: false } })
  })
  const settle = () => new Promise(resolve => setImmediate(resolve))
  try {
    h.api.adopt(original, original.id)
    h.api.inspect(); await settle()
    assert.equal(requests.at(-1)[0], '/api/studio/inspect')
    h.api.selected.value = [1]; h.api.removeSelected()
    assert.equal(h.api.inspection.value, null)
    assert.equal(h.api.points.value.length, 2)
    assert.equal(h.api.dirty.value, true)
    h.api.inspect(); await settle()
    assert.equal(requests.at(-1)[0], '/api/studio/inspect')
    assert.deepEqual(JSON.parse(requests.at(-1)[1].body), { joints: [original.joints[0], original.joints[2]], speed: 10 })
    h.api.saveRevision(); await settle()
    const put = requests.find(r => r[1].method === 'PUT')
    assert.equal(put[0], '/api/studio/recordings/r%2Fa')
    assert.equal(JSON.parse(put[1].body).revision, 1)
    assert.equal(h.api.draft.value.revision, 2)
    assert.equal(h.api.dirty.value, false)
    assert.equal(h.api.inspection.value, null)
  } finally { h.dispose() }
})
for (const status of [409, 422]) test(`${status} preserves local draft and name; latest revision is read without saving or replacing edits`, async () => {
  const requests = []
  const latest = { id: 'r1', name: '服务端名称', revision: 4, joints: [Array(6).fill(9)], poses: [Array(6).fill(8)], points: [{ t: 0, q: Array(6).fill(9), tcp: Array(6).fill(8) }], count: 1, duration: 0 }
  let puts = 0, failLatest = false
  const h = harness(async (path, options) => {
    requests.push([path, options])
    if (options.method === 'PUT') {
      if (++puts === 1) return { ok: false, status, json: async () => ({ detail: status === 422 ? '轨迹已被修改，请重新读取后再保存' : 'revision conflict' }) }
      const body = JSON.parse(options.body)
      return response({ ok: true, item: { ...latest, ...body, revision: body.revision + 1 } })
    }
    if (path.endsWith('/r1')) {
      if (failLatest) throw new Error('网络中断')
      return response({ ok: true, item: latest })
    }
    return response({ ok: true, recordings: [], recording: { active: false } })
  })
  try {
    h.api.adopt({ id: 'r1', name: '旧名', revision: 2, joints: [Array(6).fill(1)] }, 'r1')
    h.api.name.value = '本地改名'; h.api.dirty.value = true
    const before = JSON.stringify(h.api.draft.value)
    h.api.inspection.value = { plan_id: 'stale', report: { risk_detected: false } }
    await h.api.saveRevision()
    assert.equal(h.api.revisionConflict.value, true)
    assert.match(h.api.error.value, /revision 冲突/)
    assert.equal(JSON.stringify(h.api.draft.value), before)
    assert.equal(h.api.name.value, '本地改名')
    assert.equal(h.api.inspection.value, null)
    await h.api.saveRevision(); assert.equal(puts, 1)
    failLatest = true; await h.api.loadLatestRevision()
    assert.equal(h.api.error.value, '网络中断')
    assert.equal(JSON.stringify(h.api.draft.value), before)
    failLatest = false; await h.api.loadLatestRevision()
    assert.equal(h.api.latestRecording.value.revision, 4)
    assert.equal(JSON.stringify(h.api.draft.value), before)
    assert.equal(puts, 1)
    h.api.keepLocalRevision()
    assert.equal(h.api.draft.value.revision, 4)
    assert.deepEqual(h.api.draft.value.joints, [Array(6).fill(1)])
    assert.equal(h.api.name.value, '本地改名')
    assert.equal(puts, 1) // choosing recovery never writes automatically
    await h.api.saveRevision()
    assert.equal(JSON.parse(requests.filter(r => r[1].method === 'PUT').at(-1)[1].body).revision, 4)
    assert.equal(h.api.draft.value.revision, 5)
    assert.equal(h.api.revisionConflict.value, false)
  } finally { h.dispose() }
})
test('generator bounds match backend waypoint, dimensions, segments and pallet limits', () => {
  const build = (kind, patch) => helpers.buildGeneratePayload(kind, '边界', { ...defaults, ...patch })
  assert.doesNotThrow(() => build('raster', { width: 3000, height: 127, spacing: 1 }))
  assert.throws(() => build('raster', { height: 128, spacing: 1 }), /256/)
  for (const patch of [{ width: 3001 }, { height: 3001 }, { spacing: .9 }]) assert.throws(() => build('raster', patch))
  assert.equal(build('circle', { radius: 1500, segments: 128 }).options.segments, 128)
  assert.equal(build('circle', { segments: 8 }).options.spacing, undefined)
  for (const patch of [{ radius: 1501 }, { segments: 7 }, { segments: 129 }, { segments: 8.5 }]) assert.throws(() => build('circle', patch))
  assert.doesNotThrow(() => build('pallet', { rows: 6, cols: 7, layers: 1, step_x: 2000 }))
  for (const patch of [{ rows: 43, cols: 1, layers: 1 }, { step_x: 0 }, { step_y: 2001 }, { layers: 33 }, { lift: 1001 }]) assert.throws(() => build('pallet', patch))
})
test('inspection limits 256 points and carries speed, while editing allows long recordings', () => {
  const joints = Array.from({ length: 256 }, () => Array(6).fill(0))
  assert.equal(helpers.inspectionPayload({ joints, speed: 37 }).speed, 37)
  for (const speed of [0, 101, '10', NaN]) assert.throws(() => helpers.inspectionPayload({ joints, speed }))
  joints.push(Array(6).fill(1))
  assert.throws(() => helpers.inspectionPayload({ joints, speed: 10 }), /256/)
  assert.equal(helpers.buildRevisionPayload({ joints, revision: 1 }, '长录制').joints.length, 257)
  assert.equal(helpers.isRevisionConflict({ status: 422, message: '参数必须是有限数值' }), false)
})
test('navigation remount restores draft, parameters and conflict backup without restoring execution authority', async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  const values = new Map()
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) } })
  let h, restored
  try {
    h = harness(async () => response({ ok: true, recordings: [], recording: { active: false } }))
    h.api.adopt({ name: '本地', revision: 6, speed: 27, joints: [Array(6).fill(1)] }, 'r1')
    h.api.name.value = '未保存改名'; h.api.form.value.width = 277; h.api.tab.value = 'scan'
    h.api.dirty.value = true; h.api.revisionConflict.value = true
    h.api.localRecovery.value = { name: '备份', id: 'r1', draft: { revision: 5, joints: [Array(6).fill(2)] } }
    h.api.inspection.value = { plan_id: 'do-not-restore', report: { risk_detected: false } }; h.api.confirmed.value = true
    h.dispose(); h = null
    assert.doesNotMatch(values.get(helpers.STUDIO_SESSION_KEY), /do-not-restore|plan_id|confirmed/)
    restored = harness(async () => response({ ok: true, recordings: [], recording: { active: false } }))
    assert.equal(restored.api.draft.value.revision, 6)
    assert.equal(restored.api.draft.value.speed, 27)
    assert.equal(restored.api.tab.value, 'scan')
    assert.equal(restored.api.form.value.width, 277)
    assert.equal(restored.api.name.value, '未保存改名')
    assert.equal(restored.api.revisionConflict.value, true)
    assert.equal(restored.api.localRecovery.value.draft.revision, 5)
    assert.equal(restored.api.inspection.value, null)
    assert.equal(restored.api.confirmed.value, false)
  } finally {
    h?.dispose(); restored?.dispose()
    if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous)
    else delete globalThis.sessionStorage
  }
})
test('KeepAlive deactivation aborts requests and clears previews; activation needs a fresh inspection', async () => {
  let finish, signal
  const h = harness((_path, options) => { signal = options.signal; return new Promise(resolve => { finish = resolve }) })
  try {
    h.api.adopt({ poses: [Array(6).fill(1)], speed: 20 })
    const pending = h.mounted[0]()
    h.api.inspection.value = { plan_id: 'old', report: { risk_detected: false } }; h.api.confirmed.value = true
    h.deactivated[0]()
    assert.equal(signal.aborted, true)
    assert.deepEqual(h.paths.at(-1), [])
    assert.equal(h.api.inspection.value, null)
    finish(response({ ok: true, recordings: [{ id: 'late' }] })); await pending
    assert.deepEqual(h.api.catalog.value.recordings, [])
    h.activated[0]()
    assert.equal(signal.aborted, false)
    assert.equal(h.api.draft.value.speed, 20)
    assert.equal(h.api.confirmed.value, false)
    finish(response({ ok: true, recordings: [], recording: { active: false } }))
    await new Promise(resolve => setImmediate(resolve))
  } finally { h.dispose() }
})
test('server-version recovery retains the local draft and its original revision for restoration', async () => {
  const latest = { id: 'r1', name: '服务端', revision: 3, joints: [Array(6).fill(3)] }
  const h = harness(async () => response({ ok: true, item: latest }))
  try {
    h.api.adopt({ id: 'r1', name: '本地', revision: 1, joints: [Array(6).fill(1)] }, 'r1')
    h.api.name.value = '未保存的名称'; h.api.dirty.value = true; h.api.revisionConflict.value = true
    await h.api.loadLatestRevision(); h.api.useLatestRevision()
    assert.equal(h.api.draft.value.revision, 3)
    assert.equal(h.api.name.value, '服务端')
    assert.equal(h.api.localRecovery.value.draft.revision, 1)
    h.api.restoreLocalDraft()
    assert.equal(h.api.draft.value.revision, 1)
    assert.deepEqual(h.api.draft.value.joints, [Array(6).fill(1)])
    assert.equal(h.api.name.value, '未保存的名称')
    assert.equal(h.api.dirty.value, true)
    assert.equal(h.api.revisionConflict.value, true)
  } finally { h.dispose() }
})
test('recording edit keeps spatial previews but clears original sampling times; empty joints use poses', async () => {
  const h = harness(async () => response({ ok: true }))
  try {
    const joints = [0, 1, 2].map(i => Array(6).fill(i))
    const poses = [3, 4, 5].map(i => Array(6).fill(i))
    const original = { revision: 2, joints, poses, points: joints.map((q, i) => ({ t: i, q, tcp: poses[i] })), count: 3, duration: 2 }
    h.api.adopt(original, 'r1')
    h.api.revisionConflict.value = true
    h.api.selected.value = [1]; h.api.removeSelected()
    assert.deepEqual(h.api.draft.value.joints, [joints[0], joints[2]])
    assert.deepEqual(h.api.draft.value.poses, [poses[0], poses[2]])
    assert.deepEqual(h.api.draft.value.points, [])
    assert.equal(h.api.draft.value.duration, null)
    assert.equal(original.points.length, 3)
    assert.equal(original.duration, 2)
    assert.equal(h.api.draft.value.revision, 2)
    assert.equal(h.api.revisionConflict.value, true)
    h.api.adopt({ joints: [], poses }, 'r2')
    assert.equal(h.api.points.value.length, 3)
  } finally { h.dispose() }
})
test('recording status distinguishes sampling, interruption and full capacity without promising history', () => {
  assert.match(helpers.recordingStatus({ active: true, count: 4 }).text, /^采样中/)
  const interrupted = helpers.recordingStatus({ active: true, count: 4, interrupted: '模式变化' })
  assert.equal(interrupted.warning, true)
  assert.match(interrupted.text, /录制已中断/)
  assert.doesNotMatch(interrupted.text, /采样中/)
  const full = helpers.recordingStatus({ active: true, count: 6000, full: true })
  assert.match(full.text, /录制已满/)
  assert.equal(full.warning, true)
  assert.match(helpers.recordingStatus({ active: false }).text, /没有活动录制/)
  assert.doesNotMatch(source, /原始采样保留在历史/)
  assert.match(source, /编辑会替换录制，请先导出原始 JSON/)
})
test('only an active recording in the recording tab schedules a bounded refresh', async () => {
  const scheduled = new Map(), cleared = [], requests = []
  let active = false, timerId = 0
  const h = harness(async path => {
    requests.push(path)
    return response({ ok: true, recordings: [], recording: { active, count: 2 } })
  }, {
    setTimeout: (fn, delay) => { assert.equal(delay, 2000); scheduled.set(++timerId, fn); return timerId },
    clearTimeout: id => { if (id) { cleared.push(id); scheduled.delete(id) } },
  })
  try {
    await h.mounted[0](); assert.equal(scheduled.size, 0)
    active = true; await h.api.refresh(); assert.equal(scheduled.size, 1)
    const [id, tick] = [...scheduled.entries()][0]; scheduled.delete(id)
    active = false; await tick(); assert.equal(scheduled.size, 0)
    assert.equal(requests.length, 3)
    active = true; await h.api.refresh(); assert.equal(scheduled.size, 1)
    h.api.tab.value = 'scan'; assert.equal(scheduled.size, 0)
    assert.ok(cleared.length)
  } finally { h.dispose() }
})
