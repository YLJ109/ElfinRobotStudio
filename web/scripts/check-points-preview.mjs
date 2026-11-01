// Offline component regression. No HTTP/WebSocket, running simulator or hardware.
// Run: node web/scripts/check-points-preview.mjs
import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import * as Vue from 'vue'
import * as Pinia from 'pinia'
import { parse, compileScript, compileStyle } from '@vue/compiler-sfc'
import * as TeachingHelpers from '../src/teachingDraft.js'
import { targetFor, targetContext } from '../src/targetState.js'

const browserTypes = { Document: globalThis.Document, ShadowRoot: globalThis.ShadowRoot }
globalThis.Document = class Document {}
globalThis.ShadowRoot = class ShadowRoot {}
after(() => { for (const [key, value] of Object.entries(browserTypes)) {
  if (value === undefined) delete globalThis[key]; else globalThis[key] = value
} })

const filename = new URL('../src/panels/PointsPanel.vue', import.meta.url)
const { descriptor, errors } = parse(readFileSync(filename, 'utf8'), { filename: filename.pathname })
assert.deepEqual(errors, [])
// Compile the real SFC, including its template. Only its imports/exports are
// linked to the test runtime; no component methods or lifecycle hooks are faked.
const componentCode = compileScript(descriptor, { id: 'points-offline', inlineTemplate: true }).content
  .replace(/^import \{([^}]+)\} from ['"](vue|pinia)['"]\s*$/gm,
    (_, names, module) => `const {${names.replace(/\bas\b/g, ':')}} = ${module === 'vue' ? 'Vue' : 'Pinia'};`)
  .replace(/^import \{ useWorld \} from ['"]\.\.\/store['"]\s*$/m, '')
  .replace(/^import TeachingPanel from ['"]\.\/TeachingPanel.vue['"]\s*$/m, 'const TeachingPanel = globalThis.RealTeachingPanel || {};')
  .replace(/^import \{ teachingContext \} from ['"]\.\.\/teachingDraft.js['"]\s*$/m, 'const { teachingContext } = TeachingHelpers;')
  .replace(/^import \{ targetFor(?:, targetContext)? \} from ['"]\.\.\/targetState.js['"]\s*$/m, '')
  .replace(/^import SuctionControls from ['"]\.\.\/components\/SuctionControls.vue['"]\s*$/m, 'const SuctionControls = { render: () => null };')
  .replace('export default', 'globalThis.Component =')
const teachingDescriptor = parse(readFileSync(new URL('../src/panels/TeachingPanel.vue', import.meta.url), 'utf8')).descriptor
const teachingCode = compileScript(teachingDescriptor, { id: 'teaching-in-points', inlineTemplate: true }).content
  .replace(/import \{([^}]+)\} from ['"]vue['"];?/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = Vue;`)
  .replace(/import \{\s*useWorld\s*\} from ['"]\.\.\/store['"];?/, '')
  .replace(/import \{([^}]+)\} from ['"]\.\.\/teachingDraft.js['"];?/g, (_, names) => `const {${names}} = TeachingHelpers;`)
  .replace(/import \{ targetFor(?:, targetContext)? \} from ['"]\.\.\/targetState.js['"];?/, '')
  .replace('export default', 'globalThis.RealTeachingPanel =')

function eventHub() {
  const listeners = new Map()
  return {
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn) },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn) },
    dispatchEvent(event) { for (const fn of [...(listeners.get(event.type) || [])]) fn(event) },
    listenerCount() { return [...listeners.values()].reduce((sum, set) => sum + set.size, 0) },
  }
}
async function settle() { for (let i = 0; i < 6; ++i) { await Promise.resolve(); await Vue.nextTick() } }
function fakeClock() {
  let now = 1_000_000, next = 0
  const jobs = new Map()
  const clock = {
    jobs, now: () => now,
    setTimeout(fn, delay = 0) { const id = ++next; jobs.set(id, { at: now + delay, fn }); return id },
    clearTimeout(id) { jobs.delete(id) },
    setInterval() { throw new Error('Unexpected free-drive heartbeat in preview tests') },
    clearInterval(id) { jobs.delete(id) },
    async advance(ms) {
      const end = now + ms
      await settle()
      for (let count = 0; ; ++count) {
        assert.ok(count < 1000, 'timer loop must remain bounded')
        const [id, job] = [...jobs].sort((a, b) => a[1].at - b[1].at)[0] || []
        if (!job || job.at > end) break
        jobs.delete(id); now = job.at; job.fn(); await settle()
      }
      now = end; await settle()
    },
  }
  return clock
}
function node(type, text = '') {
  return {
    ...eventHub(), type, tagName: type.toUpperCase(), text, children: [], parent: null, props: {},
    value: '', selected: false, selectedIndex: -1, setPointerCapture() {},
    getRootNode: () => ({ activeElement: null }),
    get options() { return this.children.filter(child => child.type === 'option') },
  }
}
const renderer = Vue.createRenderer({
  createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
  insert(child, parent, anchor = null) {
    if (child.parent) this.remove(child)
    child.parent = parent
    const at = anchor ? parent.children.indexOf(anchor) : -1
    if (at < 0) parent.children.push(child); else parent.children.splice(at, 0, child)
  },
  remove(child) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1); child.parent = null },
  setText(el, value) { el.text = value },
  setElementText(el, value) { el.text = value; el.children = [] },
  parentNode: el => el.parent,
  nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] || null,
  patchProp(el, key, old, value) { el.props[key] = value; if (key === 'value') { el.value = value; el._value = value } },
})
const all = (root, predicate) => [root, ...root.children.flatMap(child => all(child, () => true))].filter(predicate)
const text = el => el.text + el.children.map(text).join('')
const hasClass = (el, name) => String(el.props.class || '').split(' ').includes(name)
function event(type, el, extra = {}) {
  return { type, target: el, currentTarget: el, button: 0, isPrimary: true, pointerId: 1, detail: 1,
    defaultPrevented: false, preventDefault() { this.defaultPrevented = true }, stopPropagation() {}, ...extra }
}
async function emit(el, type, extra) {
  assert.ok(el, `element for ${type} exists`)
  const ev = event(type, el, extra)
  const handler = el.props['on' + type[0].toUpperCase() + type.slice(1)]
  for (const fn of Array.isArray(handler) ? handler : handler ? [handler] : []) fn(ev)
  await settle()
  return ev
}
async function down(el, extra) {
  const ev = await emit(el, 'pointerdown', extra)
  // Native compatibility mouse events make this reproduce the old mousedown
  // implementation too; pointerdown.prevent suppresses them in the new one.
  if (!ev.defaultPrevented && (extra?.button ?? 0) === 0) await emit(el, 'mousedown', extra)
}
async function up(h, el) {
  h.window.dispatchEvent(event('pointerup', el))
  await emit(el, 'pointerup'); await emit(el, 'mouseup'); await emit(el, 'click')
}
const pose = joints => ({ xyz_mm: [joints[0], joints[1], 700], rpy_deg: joints.slice(3) })
async function fixture(t, options = {}) {
  const clock = fakeClock(), window = eventHub(), document = { ...eventHub(), hidden: false, activeElement: null }
  const preferences = new Map(Object.entries(options.preferences || {}))
  const storage = { getItem: key => preferences.get(key) ?? null, setItem: (key, value) => preferences.set(key, value) }
  window.localStorage = storage
  const handles = []
  window.__elfinScene = { setTeachTarget(pose, callback) { handles.push({ pose: pose && [...pose], callback }) } }
  const calls = { preview: [], move: [], jog: [], save: [], free: [], ik: [], fk: [], cancel: 0 }
  const initial = {
    mode: 'real', motion_granted: true, session_id: 'offline',
    joints: { q: [10, 20, 30, 40, 50, 60], lower: Array(6).fill(-180), upper: Array(6).fill(180) },
    tcp: pose([10, 20, 30, 40, 50, 60]), preview: null, preview_safety: { level: 'green' },
    ...options.snap,
  }
  const pinia = Pinia.createPinia()
  Pinia.setActivePinia(pinia)
  const useWorld = Pinia.defineStore('points-preview', {
    state: () => ({ connected: true, tools: { mounted: { tool_id: 'none', revision: 1 } },
      points: options.points || [], snap: initial, tele: null, error: '', preview: null }),
    actions: {
      setPreview(q, options) { return targetFor(this).preview(q, options) },
      async previewRequest(q) { if (q) calls.preview.push([...q]); return options.http ? await options.http.request('/api/preview', { joints: q && [...q] }) : { ok: true } },
      async moveJ(q) { return await targetFor(this).execute(q) },
      async moveTargetRequest(q) { calls.move.push([...q]); return options.http ? await options.http.request('/api/movej', { joints: [...q] }) : { ok: true } },
      async jog(...args) { calls.jog.push(args); return { ok: true } },
      async fk(q) { calls.fk.push([...q]); return options.http ? await options.http.request('/api/fk', { joints: [...q] }) : options.fk ? await options.fk(q) : { ok: true, ...pose(q) } },
      async ik(goal, opts) {
        calls.ik.push({ goal: [...goal], opts })
        return options.http ? await options.http.request('/api/ik', { target: [...goal], ...opts }) : options.ik ? await options.ik(goal, opts) : { ok: true, joints: [...goal], achieved: [...goal] }
      },
      cancelPreview() { ++calls.cancel; return targetFor(this).preview(null) },
      async addPoint(name, joints) { calls.save.push({ name, joints: [...joints] }); return options.save ? await options.save(name, joints) : { ok: true } },
      async free(...args) { calls.free.push(args); return { ok: true } },
      async call() { throw new Error('Unexpected IO or motion API') },
    },
  })
  const context = vm.createContext({
    Vue, Pinia, TeachingHelpers, targetFor, targetContext, useWorld, window, document,
    localStorage: storage,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval, clearInterval: clock.clearInterval,
    Date: class extends Date { static now() { return clock.now() } },
    fetch() { throw new Error('Network is forbidden in offline component tests') },
    WebSocket: class { constructor() { throw new Error('WebSocket is forbidden') } },
  })
  if (options.teaching) vm.runInContext(`(() => {\n${teachingCode}\n})()`, context, { filename: 'TeachingPanel.vue' })
  vm.runInContext(`(() => {\n${componentCode}\n})()`, context, { filename: filename.pathname })
  const root = node('root'), app = renderer.createApp(context.Component), renderErrors=[]
  app.config.errorHandler=error=>renderErrors.push(error)
  app.use(pinia)
  app.mount(root)
  let mounted = true
  const h = {
    root, calls, clock, window, document, world: useWorld(), preferences, handles, renderErrors,
    unmount() { if (mounted) { mounted = false; app.unmount() } },
    button(label) { return all(root, el => el.type === 'button' && text(el).trim() === label)[0] },
    byLabel(label) { return all(root, el => el.props['aria-label'] === label)[0] },
    joint(axis = 0, sign = '+') { return all(root, el => hasClass(el, 'ax'))[axis]?.children.find(el => el.type === 'button' && text(el) === sign) },
    acknowledge(level = 'green') { this.world.snap.preview = [...calls.preview.at(-1)]; this.world.snap.preview_safety = { level } },
    assertNoMotion() { assert.deepEqual(calls.jog, [], 'neither jog-on nor jog-stop is allowed'); assert.deepEqual(calls.move, []) },
  }
  t.after(() => { h.unmount(); assert.deepEqual(renderErrors,[],'real component must not throw while rendering or handling updates'); assert.equal(clock.jobs.size, 0, 'unmount clears every pending timer'); assert.equal(window.listenerCount(), 0); assert.equal(document.listenerCount(), 0) })
  await settle()
  return h
}

test('real component compiles together with its existing themed styles', () => {
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, id: 'points-offline', scoped: style.scoped }).errors, [])
})

test('joint long hold edits the latest ghost only, never jog-on or jog-stop', async t => {
  const h = await fixture(t), plus = h.joint()
  await down(plus); await h.clock.advance(560)
  h.assertNoMotion()
  assert.ok(h.calls.preview.length >= 4)
  assert.deepEqual(h.calls.preview.map(q => q[0]), h.calls.preview.map((_, i) => 11 + i))
  assert.deepEqual(h.world.snap.joints.q, [10, 20, 30, 40, 50, 60])
  await up(h, plus)
  h.assertNoMotion()
})

test('light click makes exactly one preview step; trailing click does not double it', async t => {
  const h = await fixture(t), plus = h.joint()
  await down(plus); await up(h, plus)
  assert.deepEqual(h.calls.preview, [[11, 20, 30, 40, 50, 60]])
  await h.clock.advance(2000)
  assert.equal(h.calls.preview.length, 1); assert.equal(h.clock.jobs.size, 0); h.assertNoMotion()
})

for (const ending of ['global pointerup', 'global pointercancel', 'pointerleave', 'lostpointercapture', 'button blur', 'window blur', 'hidden page', 'storage tab', 'cartesian mode', 'grant revoked', 'new session', 'unmount']) {
  test(`${ending} ends joint repetition without leaked timers or stop commands`, async t => {
    const h = await fixture(t), plus = h.joint()
    await down(plus); await h.clock.advance(400)
    if (ending.startsWith('global ')) h.window.dispatchEvent(event(ending.slice(7), plus))
    else if (ending === 'button blur') await emit(plus, 'blur')
    else if (ending === 'window blur') h.window.dispatchEvent({ type: 'blur' })
    else if (ending === 'hidden page') { h.document.hidden = true; h.document.dispatchEvent({ type: 'visibilitychange' }) }
    else if (ending === 'storage tab') await emit(h.button('存储'), 'click')
    else if (ending === 'cartesian mode') await emit(h.button('笛卡尔'), 'click')
    else if (ending === 'grant revoked') h.world.snap.motion_granted = false
    else if (ending === 'new session') h.world.snap.session_id = 'new-offline-session'
    else if (ending === 'unmount') h.unmount()
    else await emit(plus, ending)
    await settle()
    const count = h.calls.preview.length
    await h.clock.advance(2000)
    assert.equal(h.calls.preview.length, count); assert.equal(h.clock.jobs.size, 0); h.assertNoMotion()
  })
}

test('keyboard hold uses the same preview path; key repeat and synthetic click do not double step', async t => {
  const h = await fixture(t), plus = h.joint()
  await emit(plus, 'keydown', { key: 'Enter', detail: 0 })
  await emit(plus, 'keydown', { key: 'Enter', repeat: true, detail: 0 })
  assert.equal(h.calls.preview.length, 1)
  await h.clock.advance(400)
  h.window.dispatchEvent(event('keyup', plus, { key: 'Enter' }))
  await settle()
  const count = h.calls.preview.length
  await h.clock.advance(1000)
  assert.equal(h.calls.preview.length, count); h.assertNoMotion()
  await emit(plus, 'click', { detail: 0 }) // assistive technology activation
  assert.equal(h.calls.preview.length, count + 1)
})

test('secondary pointer/right button cannot edit; unrelated pointer cannot release primary hold', async t => {
  const h = await fixture(t), plus = h.joint()
  await down(plus, { button: 2 }); await down(plus, { isPrimary: false })
  assert.equal(h.calls.preview.length, 0)
  await down(plus, { pointerId: 4 })
  h.window.dispatchEvent(event('pointerup', plus, { pointerId: 9 }))
  await h.clock.advance(400)
  assert.ok(h.calls.preview.length > 1)
  h.window.dispatchEvent(event('pointerup', plus, { pointerId: 4 }))
  await settle(); const count = h.calls.preview.length
  await h.clock.advance(1000); assert.equal(h.calls.preview.length, count); h.assertNoMotion()
})

test('latest local target survives delayed/stale snapshots and clamps to joint limits', async t => {
  const h = await fixture(t, { snap: { joints: { q: [178, 0, 0, 0, 0, 0], lower: Array(6).fill(-180), upper: Array(6).fill(180) } } })
  const plus = h.joint()
  await down(plus)
  h.world.snap.preview = [178, 0, 0, 0, 0, 0]
  h.world.snap.joints.q = [0, 0, 0, 0, 0, 0]
  await h.clock.advance(560); await up(h, plus)
  assert.equal(h.calls.preview.at(-1)[0], 180)
  assert.ok(h.calls.preview.every(q => q.every(Number.isFinite) && q[0] <= 180))
  h.assertNoMotion()
})

test('only explicit execute moves after the newest preview is acknowledged; holding disables it', async t => {
  const h = await fixture(t), plus = h.joint()
  await down(plus)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  h.acknowledge(); await settle()
  assert.ok(h.button('执行目标').props.disabled, 'cannot execute an actively changing target')
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  await up(h, plus)
  assert.equal(h.button('执行目标').props.disabled, false)
  await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [[11, 20, 30, 40, 50, 60]])
  assert.deepEqual(h.calls.jog, [])
  assert.deepEqual(h.world.snap.preview, [11, 20, 30, 40, 50, 60], 'executing does not prematurely remove the ghost')
})

test('an older acknowledgement cannot execute the newer local preview', async t => {
  const h = await fixture(t), plus = h.joint()
  await down(plus); await up(h, plus); h.acknowledge(); await settle()
  await down(plus); await up(h, plus)
  assert.equal(h.calls.preview.at(-1)[0], 12)
  assert.ok(h.button('执行目标').props.disabled)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  h.acknowledge(); await settle(); h.world.snap.motion_granted = false; await settle()
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  h.world.snap.motion_granted = true; await settle()
  await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [[12, 20, 30, 40, 50, 60]])
})

test('preview labels, zero target and slider all retain preview-only semantics', async t => {
  const h = await fixture(t)
  assert.equal(h.byLabel('J1+ 预览'), h.joint())
  assert.equal(h.byLabel('J1− 预览'), h.joint(0, '−'))
  // 「预览零位」按钮已按要求删除：不再存在第二条零位入口。
  assert.equal(h.button('预览零位'), undefined)
  // 零位现在只能由滑块给出，而且仍然只是预览，绝不移动机器人。
  await emit(h.byLabel('J1 目标角'), 'input', { target: { value: '0' } })
  assert.deepEqual(h.calls.preview.at(-1), [0, 20, 30, 40, 50, 60]); h.assertNoMotion()
  await down(h.joint())
  await emit(h.byLabel('J1 目标角'), 'input', { target: { value: '25.5' } })
  const count = h.calls.preview.length
  await h.clock.advance(2000)
  assert.equal(h.calls.preview.length, count)
  assert.equal(h.calls.preview.at(-1)[0], 25.5); h.assertNoMotion()
})

test('one cancel action resets the preview without redundant sync button or motion stop', async t => {
  const h = await fixture(t)
  for (const label of ['取消']) {
    await down(h.joint()); await h.clock.advance(400)
    await emit(h.button(label), 'click')
    const count = h.calls.preview.length
    await h.clock.advance(2000)
    assert.equal(h.calls.preview.length, count); assert.equal(h.clock.jobs.size, 0); h.assertNoMotion()
  }
  assert.equal(h.calls.cancel, 1)
  assert.ok(!descriptor.template.content.includes('同步回实际'))
})

test('switching the held axis keeps the newest target and only one repeating timer', async t => {
  const h = await fixture(t)
  await emit(h.button('0.5°'), 'click')
  await down(h.joint()); await h.clock.advance(400)
  const firstAxis = h.calls.preview.at(-1)[0]
  await down(h.joint(1, '−')); await h.clock.advance(400); await up(h, h.joint(1, '−'))
  assert.equal(h.calls.preview.at(-1)[0], firstAxis)
  assert.ok(h.calls.preview.at(-1)[1] < 20)
  const count = h.calls.preview.length
  await h.clock.advance(2000)
  assert.equal(h.calls.preview.length, count); assert.equal(h.clock.jobs.size, 0); h.assertNoMotion()
})

test('stale pointer events retained after unmount cannot restart preview timers', async t => {
  const h = await fixture(t), plus = h.joint()
  await down(plus); h.unmount()
  const count = h.calls.preview.length
  await down(plus); await emit(plus, 'click', { detail: 0 }); await h.clock.advance(1000)
  assert.equal(h.calls.preview.length, count); assert.equal(h.clock.jobs.size, 0); h.assertNoMotion()
})

for (const level of ['red', 'alarm', 'yellow']) {
  test(`explicit execute retains ${level} preview safety handling`, async t => {
    const h = await fixture(t), plus = h.joint()
    await down(plus); await up(h, plus); h.acknowledge(level); await settle()
    await emit(h.button('执行目标'), 'click')
    assert.equal(h.calls.move.length, level === 'yellow' ? 1 : 0)
    assert.deepEqual(h.calls.jog, [])
  })
}

test('saved points load a preview only and save preserves the latest edited target', async t => {
  const point = { id: 'old-sim-point', name: 'P1', joints: [1, 2, 3, 4, 5, 6], source: 'sim' }
  const h = await fixture(t, { points: [point] })
  await emit(h.button('存储'), 'click'); await emit(h.button('载入预览'), 'click')
  assert.deepEqual(h.calls.preview.at(-1), point.joints); h.assertNoMotion()
  await down(h.joint()); await up(h, h.joint())
  await emit(h.button('存储'), 'click'); await emit(h.button('存目标值'), 'click')
  assert.deepEqual(h.calls.save, [{ name: 'P2', joints: [2, 2, 3, 4, 5, 6] }])
  assert.deepEqual(point.joints, [1, 2, 3, 4, 5, 6]); h.assertNoMotion()
})

test('saving a target renews its backend preview registration without moving or changing its joints',async t=>{
  const h=await fixture(t)
  await down(h.joint());await up(h,h.joint());h.acknowledge('green');await settle()
  const joints=h.calls.preview.at(-1).slice(),before=h.calls.preview.length
  await emit(h.button('存储'),'click');await emit(h.button('存目标值'),'click')
  assert.equal(h.calls.preview.length,before+1,'point storage invalidates the old backend context')
  assert.deepEqual(h.calls.preview.at(-1),joints)
  assert.deepEqual(h.calls.save[0].joints,joints)
  h.assertNoMotion()
})

test('old snapshots without joint limits still produce finite clamped previews', async t => {
  const h = await fixture(t, { snap: { joints: { q: [179.5, 0, 0, 0, 0, 0] } } })
  await down(h.joint()); await up(h, h.joint())
  assert.deepEqual(h.calls.preview.at(-1), [180, 0, 0, 0, 0, 0]); h.assertNoMotion()
})

for (const preview of ['invalid', [1, 2], [null, 0, 0, 0, 0, 0], ['1', 0, 0, 0, 0, 0]]) {
  test(`invalid legacy preview ${JSON.stringify(preview)} never enables execution`, async t => {
    const h = await fixture(t, { snap: { preview } })
    assert.ok(h.button('执行目标').props.disabled)
    await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
    await down(h.joint()); await up(h, h.joint())
    assert.deepEqual(h.calls.preview.at(-1), [11, 20, 30, 40, 50, 60])
  })
}

test('temporarily missing joint state cannot fabricate a preview or execute', async t => {
  const h = await fixture(t, { snap: { joints: null } })
  await down(h.joint()); await h.clock.advance(1000)
  assert.equal(h.calls.preview.length, 0); h.assertNoMotion()
  h.world.snap.joints = { q: [1, 2, 3, 4, 5, 6] }; await settle()
  await down(h.joint()); await up(h, h.joint())
  assert.deepEqual(h.calls.preview.at(-1), [2, 2, 3, 4, 5, 6])
})

test('invalid stored joint data is refused; legacy TCP without orientation does not break storage view', async t => {
  const h = await fixture(t, { preferences: { 'huayan.pointTab': 'storage' }, points: [
    { id: 'bad', name: 'Bad', joints: [null, 2, 3, 4, 5, 6], tcp: { xyz_mm: [0, 0, 700] } },
  ] })
  await emit(h.button('预览残影'), 'click')
  assert.equal(h.calls.preview.length, 0); assert.match(h.world.error, /无效/); h.assertNoMotion()
})

// 笛卡尔回归直接驱动真实模板事件，延迟响应只由离线替身控制。
async function cartInput(h, axis, value, commit = false) {
  const input = h.byLabel(`${axis} 目标`)
  input.value = value
  await emit(input, 'input')
  if (commit) await emit(input, 'change')
}
async function readyCart(h) {
  await emit(h.button('笛卡尔'), 'click')
  const plus = h.byLabel('X+ 预览')
  await down(plus); await up(h, plus); await h.clock.advance(40)
  h.acknowledge(); await settle()
  assert.equal(h.button('执行目标').props.disabled, false)
}

for (const raw of ['', ' ', '-', 'NaN', 'Infinity', '1e999']) {
  test(`笛卡尔无效输入 ${JSON.stringify(raw)} 立即作废旧目标，旧快照不能恢复执行`, async t => {
    const h = await fixture(t)
    await readyCart(h)
    await cartInput(h, 'X', raw, true)
    assert.equal(h.button('执行目标').props.disabled, true)
    h.acknowledge(); await settle()
    await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
    await h.clock.advance(2000)
    assert.equal(h.calls.ik.length, 1)
    assert.equal(h.calls.preview.length, 1)
  })
}

test('恢复笛卡尔偏好及已有残影时先以残影关节 FK 初始化，不恢复执行授权', async t => {
  const joints = [65, 25, 30, 40, 50, 60]
  const h = await fixture(t, { preferences: { 'huayan.cartMode': '1' }, snap: { preview: joints } })
  assert.deepEqual(h.calls.fk, [joints])
  assert.equal(Number(h.byLabel('X 目标').value), 65)
  assert.equal(Number(h.byLabel('Z 目标').value), 700)
  assert.equal(h.button('执行目标').props.disabled, true)
  assert.equal(h.calls.preview.length, 0); h.assertNoMotion()
  await down(h.byLabel('X+ 预览')); await up(h, h.byLabel('X+ 预览')); await h.clock.advance(40)
  assert.equal(h.calls.ik[0].goal[0], 66)
})

test('手输草稿跨按键和反馈保留，提交才求解，并等待新确认后显式执行', async t => {
  const h = await fixture(t)
  await readyCart(h)
  for (const value of ['1', '12', '123.']) {
    await cartInput(h, 'X', value)
    await h.clock.advance(1600)
    h.world.snap = { ...h.world.snap, preview: null, joints: { ...h.world.snap.joints, q: [1, 2, 3, 4, 5, 6] }, tcp: pose([1, 2, 3, 4, 5, 6]) }
    await settle()
    assert.equal(String(h.byLabel('X 目标').value), value)
    assert.equal(h.calls.ik.length, 1)
    assert.equal(h.button('执行目标').props.disabled, true)
  }
  await cartInput(h, 'Y', '42')
  await emit(h.byLabel('Y 目标'), 'change'); await h.clock.advance(40)
  assert.deepEqual(h.calls.ik.at(-1).goal.slice(0, 2), [123, 42])
  assert.equal(h.button('执行目标').props.disabled, true)
  h.acknowledge(); await settle(); h.assertNoMotion()
  await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [h.calls.preview.at(-1)])
})

test('手输 Enter 提交一次，随后 change 不重复求解', async t => {
  const h = await fixture(t)
  await emit(h.button('笛卡尔'), 'click'); await cartInput(h, 'X', '123')
  await h.clock.advance(200)
  assert.equal(h.calls.ik.length, 0)
  await emit(h.byLabel('X 目标'), 'keydown', { key: 'Enter' })
  await h.clock.advance(40)
  assert.equal(h.calls.ik.length, 1)
  assert.equal(h.calls.ik[0].goal[0], 123)
  await emit(h.byLabel('X 目标'), 'change'); await h.clock.advance(40)
  assert.equal(h.calls.ik.length, 1); h.assertNoMotion()
})

test('任一坐标草稿无效时其他轴提交或加减都不能求解，修正后可重新预览', async t => {
  const h = await fixture(t)
  await readyCart(h)
  await cartInput(h, 'X', '')
  await cartInput(h, 'Y', '42', true)
  await down(h.byLabel('Y+ 预览')); await up(h, h.byLabel('Y+ 预览')); await h.clock.advance(400)
  assert.equal(h.calls.ik.length, 1)
  assert.equal(String(h.byLabel('X 目标').value), '')
  await cartInput(h, 'X', '123', true); await h.clock.advance(40)
  assert.deepEqual(h.calls.ik.at(-1).goal.slice(0, 2), [123, 42])
  assert.equal(h.button('执行目标').props.disabled, true)
  h.acknowledge(); await settle()
  assert.equal(h.button('执行目标').props.disabled, false); h.assertNoMotion()
})

test('有效手输草稿接续加减从草稿步进，取消后延迟 change 不复活草稿', async t => {
  const h = await fixture(t)
  await emit(h.button('笛卡尔'), 'click'); await cartInput(h, 'X', '123')
  await down(h.byLabel('X+ 预览')); await up(h, h.byLabel('X+ 预览')); await h.clock.advance(40)
  assert.equal(h.calls.ik.at(-1).goal[0], 124)
  await cartInput(h, 'X', '456')
  await emit(h.button('取消'), 'click'); await emit(h.byLabel('X 目标'), 'change'); await h.clock.advance(200)
  assert.equal(Number(h.byLabel('X 目标').value), 10)
  assert.equal(h.calls.ik.length, 1)
  assert.equal(h.button('执行目标').props.disabled, true); h.assertNoMotion()
})

for (const ending of ['编辑', '清空', '关节模式', '模式往返', '存储页', '会话', 'TCP', '连接往返', '取消']) {
  test(`待决 IK 在${ending}后不得覆盖输入或发布旧残影`, async t => {
    let resolve
    const h = await fixture(t, { ik: () => new Promise(yes => { resolve = yes }) })
    await emit(h.button('笛卡尔'), 'click')
    await down(h.byLabel('X+ 预览')); await up(h, h.byLabel('X+ 预览')); await h.clock.advance(40)
    assert.equal(h.calls.ik.length, 1)
    if (ending === '编辑' || ending === '清空') await cartInput(h, 'X', ending === '编辑' ? '123' : '')
    else if (ending === '关节模式' || ending === '模式往返') {
      await emit(h.button('关节'), 'click')
      if (ending === '模式往返') await emit(h.button('笛卡尔'), 'click')
    } else if (ending === '存储页') await emit(h.button('存储'), 'click')
    else if (ending === '会话') h.world.snap.session_id = 'changed'
    else if (ending === 'TCP') h.world.snap.tcp_frame = { translation_mm: [0, 0, 20] }
    else if (ending === '连接往返') { h.world.connected = false; h.world.connected = true }
    else await emit(h.button('取消'), 'click')
    await settle()
    resolve({ ok: true, joints: [99, 2, 3, 4, 5, 6], achieved: [99, 2, 700, 4, 5, 6] })
    await settle()
    assert.equal(h.calls.preview.length, 0)
    if (ending === '编辑' || ending === '清空') assert.equal(String(h.byLabel('X 目标').value), ending === '编辑' ? '123' : '')
    await h.clock.advance(2000)
    assert.equal(h.calls.ik.length, 1); h.assertNoMotion()
  })
}

for (const ending of ['模式往返', '会话', '取消', '卸载']) {
  test(`待决 FK 在${ending}后不得覆盖较新的坐标`, async t => {
    const pending = []
    const h = await fixture(t, { fk: () => new Promise(resolve => pending.push(resolve)) })
    await emit(h.button('笛卡尔'), 'click')
    assert.equal(pending.length, 1)
    if (ending === '模式往返') { await emit(h.button('关节'), 'click'); await emit(h.button('笛卡尔'), 'click') }
    else if (ending === '会话') { h.world.snap.session_id = 'changed'; await settle() }
    else if (ending === '取消') await emit(h.button('取消'), 'click')
    else h.unmount()
    if (pending.length === 2) { pending[1]({ ok: true, ...pose([25, 20, 30, 40, 50, 60]) }); await settle() }
    pending[0]({ ok: true, ...pose([99, 20, 30, 40, 50, 60]) }); await settle()
    if (ending !== '卸载') assert.equal(Number(h.byLabel('X 目标').value), ending === '取消' ? 10 : 25)
    assert.equal(h.calls.preview.length, 0); h.assertNoMotion()
  })
}

test('Cartesian long hold still previews continuously, releases cleanly and moves only on explicit execute', async t => {
  const h = await fixture(t)
  await emit(h.button('笛卡尔'), 'click')
  const plus = h.byLabel('X+ 预览')
  await down(plus); await h.clock.advance(560); await up(h, plus)
  await h.clock.advance(120)
  assert.ok(h.calls.ik.length >= 3); assert.ok(h.calls.preview.length >= 3); h.assertNoMotion()
  h.acknowledge(); await settle(); await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [h.calls.preview.at(-1)])
  assert.ok(h.clock.jobs.size<=1,'执行期间最多保留一个只读运动回读计时器'); assert.deepEqual(h.calls.jog, [])
})

test('late Cartesian IK completion after unmount cannot publish a preview', async t => {
  let resolve
  const pending = new Promise(yes => { resolve = yes })
  const h = await fixture(t, { ik: () => pending })
  await emit(h.button('笛卡尔'), 'click'); await down(h.byLabel('X+ 预览')); await h.clock.advance(40)
  assert.equal(h.calls.ik.length, 1)
  h.unmount()
  resolve({ ok: true, joints: [1, 2, 3, 4, 5, 6], achieved: [1, 2, 3, 4, 5, 6] })
  await settle(); assert.equal(h.calls.preview.length, 0); assert.equal(h.clock.jobs.size, 0); h.assertNoMotion()
})

// Complete parent/child integration. Only service transport and the scene handle
// are fake; both SFCs, emitted events, exposed refs and lifecycle run unchanged.
function teachingHTTP() {
  const requests = [], pending = []
  let backendPreview = null
  return { requests, pending, get preview() { return backendPreview && [...backendPreview] },
    async request(path, body) {
      assert.ok(['/api/ik', '/api/fk', '/api/preview', '/api/movej'].includes(path), `Unexpected endpoint: ${path}`)
      requests.push({ path, body: structuredClone(body) })
      if (path === '/api/ik') return await new Promise(resolve => pending.push(resolve))
      if (path === '/api/preview' && this.onPreview) {
        const result = await this.onPreview(body)
        if (result?.ok) backendPreview = body.joints && [...body.joints]
        return result
      }
      if (path === '/api/movej' && this.onMove) return await this.onMove(body)
      if (path === '/api/preview') backendPreview = body.joints && [...body.joints]
      if (path === '/api/fk') return { ok: true, ...pose(body.joints) }
      return { ok: true, plan_id:'offline-plan' }
    },
    async resolve(result) { assert.ok(pending.length); pending.shift()(result); await settle() },
  }
}
const taught = [35, 22, -12, 10, 20, 5]
async function teachingFixture(t, options = {}) {
  const http = teachingHTTP()
  const h = await fixture(t, { ...options, teaching: true, http,
    snap: { mode: 'sim', real: null, config_rev: 1, tcp_frame: { translation_mm: [0, 0, 20], rpy_deg: [0, 0, 0] }, ...options.snap } })
  return Object.assign(h, { http,
    async openPreview(mode = this.preferences.get('huayan.cartMode') === '1' ? '笛卡尔' : '关节') {
      await emit(this.button(mode), 'click')
    },
    async dragAndSolve(result = { ok: true, joints: taught }) {
      await emit(this.button('三维示教'), 'click')
      assert.equal(typeof this.handles.at(-1).callback, 'function')
      this.handles.at(-1).callback([200, -350, 700])
      await this.clock.advance(160)
      await http.resolve(result)
      this.assertNoMotion()
    },
    async confirm(safety = { level: 'green' }, joints = http.preview) {
      // Real websocket shape: replace the current snapshot, not the local ghost.
      this.world.snap = { ...this.world.snap, preview: joints && [...joints], preview_safety: safety,
        target_plan_id:this.world.targetState.planId }
      await settle()
    },
  })
}

test('顶部仅执行与存储；三维示教位于目标预览内，点击已选顶部页不退出示教', async t => {
  const h = await teachingFixture(t)
  const tabs = all(h.root, el => el.props['aria-label'] === '点位工作区')[0]
  assert.deepEqual(tabs.children.filter(el => el.type === 'button').map(el => text(el)), ['直接执行', '存储'])
  const modes = all(h.root, el => el.props['aria-label'] === '目标预览模式')[0]
  assert.deepEqual(all(modes, el => el.type === 'button').map(el => text(el)), ['关节', '笛卡尔', '三维示教'])
  await h.dragAndSolve()
  assert.equal(h.button('直接执行').props['aria-selected'], true)
  assert.equal(h.button('三维示教').props['aria-pressed'], true)
  assert.equal(h.button('关节').props['aria-pressed'], false)
  assert.equal(h.button('笛卡尔').props['aria-pressed'], false)
  const panel = all(h.root, el => el.props.id === 'point-pane-execute')[0]
  assert.ok(all(panel, el => el.props.id === 'point-pane-teaching').length)
  const count = h.calls.preview.length, callback = h.handles.at(-1).callback
  await emit(h.button('直接执行'), 'click')
  assert.equal(h.handles.at(-1).callback, callback)
  assert.equal(h.calls.preview.length, count)
  assert.ok(h.button('执行目标'),'三维示教内必须有显式执行按钮')
  h.assertNoMotion()
})

for (const mode of ['关节', '笛卡尔']) test(`从示教切换内层${mode}交接同一目标，必须等待新确认`, async t => {
  const h = await teachingFixture(t, { preferences: { 'huayan.cartMode': mode === '关节' ? '1' : '0' } })
  await h.dragAndSolve(); await h.confirm()
  const fkCount = h.calls.fk.length
  await h.openPreview(mode)
  assert.equal(h.button(mode).props['aria-pressed'], true)
  assert.equal(h.button('三维示教').props['aria-pressed'], false)
  assert.deepEqual(h.world.preview, taught)
  assert.equal(h.button('执行目标').props.disabled, true)
  if (mode === '笛卡尔') {
    assert.equal(Number(h.byLabel('X 目标').value), 200)
    assert.equal(h.calls.fk.length, fkCount)
  }
  assert.equal(h.calls.ik.length, 1)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  await h.confirm(); await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [taught])
})

test('旧示教页偏好恢复为执行页内的示教模式，不恢复运动授权', async t => {
  const h = await teachingFixture(t, { preferences: { 'huayan.pointTab': 'teaching' } })
  assert.equal(h.button('直接执行').props['aria-selected'], true)
  assert.equal(h.button('三维示教').props['aria-pressed'], true)
  assert.ok(all(h.root, el => el.props.id === 'point-pane-execute').length)
  assert.equal(h.calls.preview.length, 0); assert.equal(h.calls.ik.length, 0)
  h.assertNoMotion()
})

test('real Points+Teaching: drag → IK → inner preview mode transfers same q → fresh acknowledgement → explicit move', async t => {
  const h = await teachingFixture(t)
  // Reproduce the bug: parent tgt already contains an unrelated preview.
  await down(h.joint()); await up(h, h.joint())
  await h.dragAndSolve()
  assert.deepEqual(h.http.preview, taught)
  // Even an earlier matching acknowledgement cannot bypass the new handoff gate.
  await h.confirm()
  await h.openPreview()
  assert.deepEqual(h.world.preview, taught, 'child unmount must preserve claimed preview')
  assert.deepEqual(h.http.preview, taught)
  assert.equal(h.byLabel('J1 目标角（只读）').value, '35.00')
  assert.equal(h.button('执行目标').props.disabled, true)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  await h.confirm()
  assert.equal(h.button('执行目标').props.disabled, false)
  h.assertNoMotion()
  await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [taught])
  assert.deepEqual(h.http.requests.filter(r => r.path === '/api/movej').map(r => r.body.joints), [taught])
  assert.deepEqual(h.calls.jog, [])
})

test('real parent/child handoff retains Cartesian tab and adopts taught TCP without another IK', async t => {
  const h = await teachingFixture(t, { preferences: { 'huayan.cartMode': '1' } })
  await h.dragAndSolve()
  await h.openPreview()
  await h.confirm()
  assert.equal(h.http.requests.filter(r => r.path === '/api/ik').length, 1)
  assert.equal(h.button('执行目标').props.disabled, false)
  h.assertNoMotion()
  await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [taught])
})

test('delayed preview acknowledgement cannot let actual-joint snapshots overwrite the handed-off target', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve(); await h.openPreview()
  await h.clock.advance(1500)
  h.world.snap = { ...h.world.snap, preview: null, joints: { ...h.world.snap.joints, q: [11, 21, 31, 41, 51, 61] } }
  await settle()
  assert.equal(h.byLabel('J1 目标角（只读）').value, '35.00')
  assert.equal(h.button('执行目标').props.disabled, true)
  await h.confirm()
  assert.equal(h.button('执行目标').props.disabled, false)
  h.assertNoMotion()
  await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [taught])
})

for (const [name, safety] of [
  ['red', { level: 'red' }], ['alarm', { level: 'alarm' }], ['unknown', { level: 'unknown' }], ['missing', null],
  ['collision despite green', { level: 'green', collisions: [{}] }],
]) test(`real handoff refuses ${name} safety, even forced execute clicks`, async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve(); await h.openPreview(); await h.confirm(safety)
  assert.equal(h.button('执行目标').props.disabled, true)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
})

for (const [name, mutate] of [
  ['mode ABA', w => { w.snap.mode = 'real'; w.snap.mode = 'sim' }],
  ['session ABA', w => { w.snap.session_id = 'new'; w.snap.session_id = 'offline' }],
  ['real link', w => { w.snap.real = { host: 'fake' } }],
  ['TCP', w => { w.snap.tcp_frame.translation_mm[2]++ }],
  ['tool revision', w => { w.tools.mounted.revision++ }],
  ['connection ABA', w => { w.connected = false; w.connected = true }],
]) test(`real handoff cannot revive after ${name} with a delayed matching snapshot`, async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve(); await h.openPreview(); await h.confirm()
  assert.equal(h.button('执行目标').props.disabled, false)
  mutate(h.world); await h.confirm()
  assert.equal(h.button('执行目标').props.disabled, true)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
})

test('editing again then switching tab while IK is pending cannot inherit the prior taught preview', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve()
  h.handles.at(-1).callback([201, -350, 700]); await h.clock.advance(160)
  await h.openPreview()
  await h.confirm({ level: 'green' }, taught)
  assert.equal(h.button('执行目标').props.disabled, true)
  const count = h.calls.preview.length
  await h.http.resolve({ ok: true, joints: [60, 22, -12, 10, 20, 5] })
  assert.equal(h.calls.preview.length, count)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
})

test('unreachable teaching target cannot fall back to an old matching backend ghost', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve()
  h.handles.at(-1).callback([99999, -350, 700]); await h.clock.advance(160)
  await h.http.resolve({ ok: false, why: 'unreachable' })
  await h.openPreview(); await h.confirm({ level: 'green' }, taught)
  assert.equal(h.button('执行目标').props.disabled, true)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
})

test('context change before leaving teaching invalidates the offer; no stale handoff is registered', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve(); h.world.tools.mounted.revision++
  const count = h.calls.preview.length
  await h.openPreview(); await h.confirm({ level: 'green' }, taught)
  assert.equal(h.calls.preview.length, count)
  assert.equal(h.button('执行目标').props.disabled, true)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
})

test('persisted teaching rows and a legacy preview never restore a claim or execution permission', async t => {
  const draft = { version: 1, pose: [200, -350, 700, 90, 0, 0], label: 'draft', rows: [{ name: 'old', joints: taught }],
    solved: taught, motion_granted: true, saveUnconfirmed: false }
  const h = await teachingFixture(t, { preferences: { 'huayan.pointTab': 'teaching', [TeachingHelpers.TEACHING_DRAFT_KEY]: JSON.stringify(draft) },
    snap: { preview: taught, motion_granted: false } })
  assert.equal(h.calls.ik.length, 0)
  await h.openPreview(); await h.confirm({ level: 'green' }, taught)
  assert.equal(h.calls.preview.length, 0)
  assert.equal(h.world.snap.motion_granted, false)
  assert.equal(h.button('执行目标').props.disabled, true)
  h.world.snap.motion_granted = true; await settle()
  assert.equal(h.button('执行目标').props.disabled, true)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
})

test('teaching through storage tab does not revive an unclaimed goal; direct editing can create a new preview', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve(); await emit(h.button('存储'), 'click'); await emit(h.button('直接执行'), 'click')
  await h.confirm({ level: 'green' }, taught)
  assert.equal(h.button('执行目标').props.disabled, true)
  await down(h.joint()); await up(h, h.joint())
  await h.confirm({ level: 'green' }, h.calls.preview.at(-1))
  assert.equal(h.button('执行目标').props.disabled, false)
  h.assertNoMotion()
})

const screenshotTarget = [18.85, -108.7, -92.2, -140.7, 102.9, 10.33]

test('自动姿态示教的有效解交接至点位，确认预览后仅显式点击才执行', async t => {
  const h = await teachingFixture(t)
  await emit(h.button('三维示教'), 'click')
  await emit(h.button('自动姿态'), 'click'); await h.clock.advance(160)
  assert.equal(h.calls.ik.at(-1).opts.position_only, true)
  const joints = [0.436013, -51.591029, -20.86576, 0.007459, -9.292333, 0]
  const achieved = [570.8147, 4.33966, 966.05136, 21.43294, -0.003806, 90.4418]
  await h.http.resolve({ ok: true, joints, achieved, orientation_constrained: false })
  await h.openPreview()
  assert.equal(h.button('执行目标').props.disabled, true)
  await h.confirm({ level: 'green', collisions: [] })
  assert.equal(h.button('执行目标').props.disabled, false)
  h.assertNoMotion()
  await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [joints])
})

const simulationSafety = (level = 'green', jointLevel = 'red') => ({
  level, max_pct: 98, clearance_pct: 0, per_joint: [10, 60, 52, 98, 57, 6],
  joint_level: jointLevel, joint_out_of_bounds: false, fence_level: 'green', ground_level: 'green', workspace_level: 'green',
  fence_gap_mm: 200, ground_gap_mm: 300, collisions: [], reasons: ['J4 接近限位（仿真提示，不拦截）'],
})

for (const level of ['green', 'yellow']) {
  for (const jointLevel of ['green', 'yellow', 'red', 'alarm']) test(`screenshot q/zero actual: authoritative ${level} permits diagnostic joint ${jointLevel}`, async t => {
    const h = await teachingFixture(t, { snap: { joints: { q: [0, 0, 0, 0, 0, 0] } } })
    await h.dragAndSolve({ ok: true, joints: screenshotTarget })
    await h.openPreview(); await h.confirm(simulationSafety(level, jointLevel))
    assert.equal(h.button('执行目标').props.disabled, false)
    h.assertNoMotion()
    await emit(h.button('执行目标'), 'click')
    assert.deepEqual(h.calls.move, [screenshotTarget])
  })
}

test('same snapshot object with same preview/safety references acknowledges a new backend timestamp', async t => {
  const h = await teachingFixture(t, { snap: { t: 1 } })
  await h.dragAndSolve({ ok: true, joints: screenshotTarget })
  await h.confirm(simulationSafety())
  await h.openPreview()
  assert.equal(h.button('执行目标').props.disabled, true)
  h.world.snap.t = 2 // Fresh state, no new preview/safety array or object.
  await settle()
  assert.equal(h.button('执行目标').props.disabled, false)
  h.assertNoMotion()
})

test('mode roundtrip explains stale ownership and a fresh valid teaching handoff recovers execution', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve(); await h.openPreview(); await h.confirm(simulationSafety())
  h.world.snap.mode = 'real'; h.world.snap.mode = 'sim'; await h.confirm(simulationSafety())
  assert.equal(h.button('执行目标').props.disabled, true)
  assert.match(text(h.root), /目标已失效，请重新预览/)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  await emit(h.button('三维示教'), 'click')
  // The restored pose is unchanged: explicitly re-solve it, never restore IK.
  await emit(h.button('求解预览'), 'click')
  await h.http.resolve({ ok: true, joints: screenshotTarget })
  await h.openPreview(); await h.confirm(simulationSafety())
  assert.equal(h.button('执行目标').props.disabled, false)
  await emit(h.button('执行目标'), 'click')
  assert.deepEqual(h.calls.move, [screenshotTarget])
})

for (const [level, reason] of [['red', /风险|禁止|安全/], ['alarm', /风险|禁止|安全/], ['unknown', /安全状态未知/], [null, /安全状态未知/]]) {
  test(`authoritative ${level} disables with a visible execution reason, not a silent grey button`, async t => {
    const h = await teachingFixture(t)
    await h.dragAndSolve(); await h.openPreview()
    await h.confirm(level === null ? null : { ...simulationSafety(level), reasons: ['目标安全风险，禁止执行'] })
    assert.equal(h.button('执行目标').props.disabled, true)
    const status = all(h.root, el => el.props.id === 'point-execution-status')[0]
    assert.ok(status, 'execution blocker must be visible')
    assert.match(text(status), reason)
    await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  })
}

test('waiting for preview acknowledgement is explicit; no matching preview cannot execute', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve(); await h.openPreview()
  assert.match(text(h.root), /等待.*快照|等待.*预览/)
  await h.confirm(simulationSafety(), [0, 0, 0, 0, 0, 0])
  assert.equal(h.button('执行目标').props.disabled, true)
  assert.match(text(h.root), /等待.*预览|目标.*不一致/)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
})

test('real SFC preview HTTP rejection stays failed after old snapshots; explicit new preview recovers', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve()
  let finish
  h.http.onPreview = () => new Promise(resolve => { finish = resolve })
  await h.openPreview()
  assert.deepEqual([...h.world.preview], taught)
  await h.confirm()
  assert.match(text(h.root), /等待预览/)
  assert.equal(h.button('执行目标').props.disabled, true)
  finish({ ok: false, reason_code: 'ik_busy', why: '预览计算忙，请手动重试' }); await settle()
  await h.confirm()
  assert.match(text(h.root), /失败/); assert.match(text(h.root), /计算忙/)
  await emit(h.button('执行目标'), 'click'); h.assertNoMotion()
  h.http.onPreview = null
  await down(h.joint()); await up(h, h.joint())
  await h.confirm()
  assert.equal(h.button('执行目标').props.disabled, false)
  h.assertNoMotion()
})

test('real SFC execute is singleflight through HTTP and remains executing until state settles', async t => {
  const h = await teachingFixture(t)
  await h.dragAndSolve(); await h.openPreview(); await h.confirm()
  let finish
  h.http.onMove = () => new Promise(resolve => { finish = resolve })
  await emit(h.button('执行目标'), 'click'); await emit(h.button('执行目标'), 'click')
  assert.equal(h.calls.move.length, 1); assert.match(text(h.root), /执行中/)
  finish({ ok: true }); await settle()
  await emit(h.button('执行目标'), 'click'); assert.equal(h.calls.move.length, 1)
  h.world.snap = { ...h.world.snap, t: 100, moving: true }; await settle()
  h.world.snap = { ...h.world.snap, t: 101, moving: false, joints: { ...h.world.snap.joints, q: taught },
    target_evidence:{plan_id:'offline-plan',status:'reached',reached:true} }; await settle()
  assert.match(text(h.root), /目标预览已到达/)
  assert.equal(h.button('执行目标').props.disabled, true)
  h.world.snap={...h.world.snap,config_rev:2};await settle()
  assert.match(text(h.root),/目标预览已失效/)
  assert.equal(h.button('执行目标').props.disabled,true)
})

test('storage uses the same current target; save failure retains its name and prevents duplicate submission', async t => {
  let finish
  const h = await teachingFixture(t, { save: () => new Promise(resolve => { finish = resolve }) })
  await h.dragAndSolve(); await h.openPreview(); await h.confirm()
  await emit(h.button('存储'), 'click')
  const input = h.byLabel('目标点位名称')
  input.value = '保留名称'; input.dispatchEvent(event('input', input)); await settle()
  await emit(h.button('存目标值'), 'click'); await emit(h.button('保存中…'), 'click')
  assert.equal(h.calls.save.length, 1)
  assert.deepEqual(h.calls.save[0], { name: '保留名称', joints: taught })
  finish({ ok: false, why: '磁盘写入失败' }); await settle()
  assert.equal(h.byLabel('目标点位名称').value, '保留名称')
  assert.match(text(h.root), /磁盘写入失败/)
  h.world.snap.session_id = 'another-session'; await settle()
  assert.equal(h.button('存目标值').props.disabled, true)
  await emit(h.button('存目标值'), 'click'); assert.equal(h.calls.save.length, 1)
  h.assertNoMotion()
})


test('simulation omits permanent preview instructions and unknown arrival metrics',async t=>{
 const h=await fixture(t,{snap:{mode:'sim'}})
 assert.doesNotMatch(text(h.root),/轻点 ±|帧龄|关节 未知|TCP 未知|姿态 未知/)
})


test('actual rendered point panel survives ghost removal across completed/outside/settling/reached and clears arrival on new target',async t=>{
 const zero=[0,0,0,0,0,0],goal=[0,0,0,0,0,2]
 const oldExec={state:'aborted',execution:{epoch:1,started:10,state:'cancelled',busy:false}}
 const h=await fixture(t,{snap:{mode:'sim',joints:{q:zero,lower:Array(6).fill(-180),upper:Array(6).fill(180)},exec:oldExec},http:{request:async(path,body)=>path==='/api/preview'?{ok:true,plan_id:'render-plan'}:path==='/api/movej'?{ok:true,execution:{epoch:1,started:20,state:'running',busy:true}}:{ok:true,...pose(body.joints||zero)}}})
 await down(h.joint(5));await up(h,h.joint(5));await down(h.joint(5));await up(h,h.joint(5))
 assert.deepEqual(h.calls.preview.at(-1),goal)
 h.world.snap={...h.world.snap,t:1,preview:goal,target_plan_id:'render-plan',preview_safety:{level:'green'},target_evidence:{plan_id:'render-plan',status:'preview',reached:false}}
 await settle();assert.equal(h.button('执行目标').props.disabled,false)
 // Preview may disappear between the acknowledged UI projection and execution.
 h.world.snap={...h.world.snap,t:2,preview:null};await settle()
 assert.ok(h.button('执行目标'));assert.deepEqual(h.renderErrors,[])
 h.world.snap={...h.world.snap,t:3,preview:goal};await settle()
 await emit(h.button('执行目标'),'click');assert.deepEqual(h.calls.move,[goal])
 h.world.snap={...h.world.snap,t:4,moving:true,joints:{...h.world.snap.joints,q:[0,0,0,0,0,1]},exec:{...oldExec,execution:{epoch:1,started:20,state:'running',busy:true}},target_evidence:{plan_id:'render-plan',status:'moving',reached:false}};await settle()
 // Backend consumes the ghost before the arrival hold interval completes.
 h.world.snap={...h.world.snap,t:5,preview:null,moving:false,joints:{...h.world.snap.joints,q:goal},exec:{...oldExec,execution:{epoch:1,started:20,state:'completed',busy:false}},target_evidence:{plan_id:'render-plan',status:'outside',reached:false}};await settle()
 assert.equal(targetFor(h.world).state.executing,true);assert.ok(h.button('执行目标').props.disabled)
 assert.doesNotMatch(text(h.root),/后端已确认目标到达并停稳|运动已中途停止/)
 h.world.snap={...h.world.snap,t:6,target_evidence:{plan_id:'render-plan',status:'settling',reached:false}};await settle()
 assert.equal(targetFor(h.world).state.executing,true);assert.deepEqual(h.renderErrors,[])
 h.world.snap={...h.world.snap,t:7,target_evidence:{plan_id:'render-plan',status:'reached',reached:true}};await settle()
 assert.equal(targetFor(h.world).state.executionOutcome,'reached');assert.match(text(h.root),/后端已确认目标到达并停稳/)
 assert.ok(h.button('执行目标'));assert.deepEqual(h.renderErrors,[])
 await down(h.joint(5));await up(h,h.joint(5));await settle()
 assert.notEqual(targetFor(h.world).state.executionOutcome,'reached');assert.equal(targetFor(h.world).state.arrivalBinding,null)
 assert.doesNotMatch(text(h.root),/后端已确认目标到达并停稳/)
 assert.equal(h.calls.move.length,1,'only the explicit execution may move')
})
