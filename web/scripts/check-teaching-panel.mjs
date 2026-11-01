// Real compiled SFC/lifecycle/reactivity, deterministic timers, fake HTTP only.
// node --test web/scripts/check-teaching-panel.mjs
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import * as Vue from 'vue'
import { parse, compileScript, compileStyle } from '@vue/compiler-sfc'
import * as Helpers from '../src/teachingDraft.js'
import { targetFor } from '../src/targetState.js'

// Vue's real v-model directive checks these browser types during updates.
const browserTypes = { Document: globalThis.Document, ShadowRoot: globalThis.ShadowRoot }
globalThis.Document = class Document {}
globalThis.ShadowRoot = class ShadowRoot {}
after(() => { for (const [name, value] of Object.entries(browserTypes)) {
  if (value === undefined) delete globalThis[name]; else globalThis[name] = value
} })

const file = new URL('../src/panels/TeachingPanel.vue', import.meta.url)
const { descriptor, errors } = parse(readFileSync(file, 'utf8'))
assert.deepEqual(errors, [])
const code = compileScript(descriptor, { id: 'teaching-offline', inlineTemplate: true }).content
  .replace(/import \{([^}]+)\} from ['"]vue['"];?/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = Vue;`)
  .replace(/import \{\s*useWorld\s*\} from ['"]\.\.\/store['"];?/, '')
  .replace(/import \{([^}]+)\} from ['"]\.\.\/teachingDraft.js['"];?/g, (_, names) => `const {${names}} = Helpers;`)
  .replace(/import \{ targetFor \} from ['"]\.\.\/targetState.js['"];?/, '')
  .replace('export default', 'globalThis.Component =')
const q = [10, 20, 30, 40, 50, 60]
const clone = value => JSON.parse(JSON.stringify(value))
const solution = (joints = q) => ({ ok: true, joints: [...joints] })
const fkResult = { ok: true, xyz_mm: [100, -400, 700], rpy_deg: [90, 0, 0] }
const initialDraft = (rows = [{ name: '离线点 1', joints: q }]) => ({ version: 1, pose: [100, -400, 700, 90, 0, 0], label: '离线点', rows, saveUnconfirmed: false })
const settle = async () => { for (let i = 0; i < 12; i++) { await Promise.resolve(); await Vue.nextTick() } }

function hub() {
  const listeners = new Map()
  return {
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn) },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn) },
    dispatchEvent(event) { for (const fn of [...listeners.get(event.type) || []]) fn(event) },
    listenerCount: () => [...listeners.values()].reduce((n, set) => n + set.size, 0),
  }
}

function clock() {
  let now = 0, id = 0
  const jobs = new Map()
  return { jobs, setTimeout(fn, delay = 0) { jobs.set(++id, { at: now + delay, fn }); return id }, clearTimeout(id) { jobs.delete(id) },
    async advance(ms) {
      await settle()
      const end = now + ms
      for (let n = 0; ; n++) {
        assert.ok(n < 100, 'timers must remain bounded')
        const [key, job] = [...jobs].sort((a, b) => a[1].at - b[1].at)[0] || []
        if (!job || job.at > end) break
        now = job.at; jobs.delete(key); job.fn(); await settle()
      }
      now = end; await settle()
    } }
}

function element(type, text = '') {
  return { ...hub(), type, tagName: type.toUpperCase(), text, children: [], parent: null, props: {}, value: '',
    getRootNode: () => ({ activeElement: null }) }
}
const renderer = Vue.createRenderer({
  createElement: element, createText: text => element('#text', text), createComment: text => element('#comment', text),
  insert(child, parent, anchor = null) {
    if (child.parent) this.remove(child)
    child.parent = parent
    const at = anchor ? parent.children.indexOf(anchor) : -1
    if (at < 0) parent.children.push(child); else parent.children.splice(at, 0, child)
  },
  remove(child) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1); child.parent = null },
  setText(el, value) { el.text = value }, setElementText(el, value) { el.text = value; el.children = [] },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] || null,
  patchProp(el, key, old, value) { el.props[key] = value; if (key === 'value') el.value = value },
})
const all = root => [root, ...root.children.flatMap(all)]
const text = el => el.text + el.children.map(text).join('')
const event = (type, target) => ({ type, target, currentTarget: target, preventDefault() {}, stopPropagation() {} })

function fakeHTTP() {
  const calls = []
  let active = 0, maxActive = 0
  const request = (path, options = {}) => {
    assert.ok(['/api/ik', '/api/fk', '/api/preview', '/api/teaching/batch'].includes(path), `Hardware/unknown endpoint forbidden: ${path}`)
    assert.equal(options.method, 'POST')
    const call = { path, body: JSON.parse(options.body), done: false }
    calls.push(call)
    if (path === '/api/preview') return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) })
    active++; maxActive = Math.max(maxActive, active)
    return new Promise((resolve, reject) => {
      call.reply = (body, status = 200) => {
        assert.equal(call.done, false); call.done = true; active--
        resolve({ ok: status < 400, status, json: async () => clone(body) })
      }
      call.fail = (error = new Error('fake network failure')) => { assert.equal(call.done, false); call.done = true; active--; reject(error) }
      call.badJSON = () => { call.done = true; active--; resolve({ ok: true, status: 200, json: async () => { throw new Error('invalid JSON') } }) }
    })
  }
  return { request, calls, by: path => calls.filter(c => c.path === path), latest: path => calls.filter(c => c.path === path).at(-1), get maxActive() { return maxActive } }
}

async function fixture(t, options = {}) {
  const time = clock(), window = hub(), http = options.http || fakeHTTP()
  const storage = options.storage || new Map()
  if (options.draft) storage.set(Helpers.TEACHING_DRAFT_KEY, JSON.stringify(options.draft))
  const storageAPI = options.storageAPI || { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }
  window.localStorage = storageAPI
  const handles = []
  if (options.sceneReady !== false) window.__elfinScene = {
    setTeachTarget(pose, callback) { handles.push({ pose: pose && [...pose], callback }) },
  }
  const previews = []
  const world = options.world || Vue.reactive({ connected: true, preview: null, points: [{ id: 'old' }],
    snap: { mode: 'sim', real: null, session_id: 'offline', config_rev: 1, joints: { q: [...q] },
      tcp_frame: { translation_mm: [0, 0, 20], rpy_deg: [0, 0, 0] }, motion_granted: false },
    tools: { mounted: { tool_id: 'none', revision: 1 }, tool: { tcp: { translation_mm: [0, 0, 0] } } },
    async ik(target, options) { return (await http.request('/api/ik', { method: 'POST', body: JSON.stringify({ target, ...options }) })).json() },
    async fk(joints) { return (await http.request('/api/fk', { method: 'POST', body: JSON.stringify({ joints }) })).json() },
    setPreview(joints, options) { return targetFor(this).preview(joints, options) },
    async previewRequest(joints) {
      previews.push([...joints]); this.preview = [...joints]
      return (await http.request('/api/preview', { method: 'POST', body: JSON.stringify({ joints }) })).json()
    },
    moveJ() { assert.fail('No motion allowed') }, jog() { assert.fail('No jog allowed') }, call() { assert.fail('No IO allowed') },
  })
  const context = vm.createContext({ Vue, Helpers, targetFor, useWorld: () => world, window, fetch: http.request,
    setTimeout: time.setTimeout, clearTimeout: time.clearTimeout,
    WebSocket: class { constructor() { assert.fail('No WebSocket allowed') } } })
  vm.runInContext(code, context, { filename: file.pathname })
  const root = element('root'), app = renderer.createApp(context.Component)
  app.config.errorHandler = error => { throw error }
  app.mount(root)
  let mounted = true
  const h = { root, world, http, previews, handles, window, storage, time,
    button: name => all(root).find(el => el.type === 'button' && text(el).trim() === name),
    input: name => all(root).find(el => el.type === 'input' && el.props['aria-label'] === name),
    text: () => text(root),
    stored: () => JSON.parse(storage.get(Helpers.TEACHING_DRAFT_KEY)),
    async click(name, force = false) {
      const el = this.button(name); assert.ok(el, name)
      if (!force) assert.equal(!!el.props.disabled, false, `${name} must be enabled`)
      el.props.onClick(event('click', el)); await settle()
    },
    async inputValue(name, value, force = false) {
      const el = this.input(name); assert.ok(el, name)
      if (!force) assert.equal(!!el.props.disabled, false, name)
      el.value = value; const ev = event('input', el)
      el.dispatchEvent(ev); el.props.onInput?.(ev); await settle()
    },
    async reply(path, result, status = 200) { http.latest(path).reply(result, status); await settle() },
    unmount() { if (mounted) { app.unmount(); mounted = false } },
  }
  t.after(() => { h.unmount(); assert.equal(time.jobs.size, 0); assert.equal(window.listenerCount(), 0) })
  await settle()
  return h
}

async function solve(h, joints = q) { await h.click('求解预览'); await h.reply('/api/ik', solution(joints)) }
async function add(h) { await solve(h); await h.click('加入点位草稿') }

test('SFC style compiles; mount/refresh restores drafts but never IK, preview or permission', async t => {
  assert.deepEqual(compileStyle({ source: descriptor.styles[0].content, id: 'teaching-offline', scoped: true }).errors, [])
  const draft = { ...initialDraft(), solved: q, motion_granted: true, past: [q], busy: true }
  const h = await fixture(t, { draft })
  assert.equal(h.http.calls.length, 0)
  assert.equal(h.input('X / mm').value, 100)
  assert.equal(h.input('草稿点位1名称').value, '离线点 1')
  assert.equal(h.button('加入点位草稿').props.disabled, true)
  assert.equal(h.button('撤销').props.disabled, true)
  assert.equal(h.world.snap.motion_granted, false)
  assert.deepEqual(h.handles.at(-1).pose, draft.pose)
  await h.inputValue('点位名称前缀', '恢复')
  assert.deepEqual(Object.keys(h.stored()).sort(), ['version', 'pose', 'label', 'rows', 'saveUnconfirmed'].sort())
  h.unmount()
  const restored = await fixture(t, { storage: h.storage })
  assert.equal(restored.http.calls.length, 0)
  assert.equal(restored.input('点位名称前缀').value, '恢复')
  assert.equal(restored.button('加入点位草稿').props.disabled, true)
})

test('scene callback is already mm xyz, debounces changes and invalidates immediately on input', async t => {
  const h = await fixture(t)
  h.handles.at(-1).callback([123, -456, 789]); await settle()
  assert.equal(h.input('X / mm').value, 123)
  assert.equal(h.input('Y / mm').value, -456)
  await h.time.advance(160)
  assert.deepEqual(h.http.latest('/api/ik').body.target, [123, -456, 789, 90, 0, 0])
  await h.reply('/api/ik', solution())
  assert.equal(h.button('加入点位草稿').props.disabled, false)
  await h.inputValue('X / mm', '321')
  assert.equal(h.button('加入点位草稿').props.disabled, true)
  assert.equal(h.world.preview, null)
  assert.equal(h.stored().pose[0], 321)
})

test('连续拖动沿用最近有效逆解，失败不污染种子，环境变化后重新使用实际关节', async t => {
  const h = await fixture(t)
  const branch = [15, -60, 80, 20, 70, -25]
  await solve(h, branch)
  await h.inputValue('X / mm', '200'); await h.time.advance(160)
  assert.deepEqual(h.http.latest('/api/ik').body.seed, branch)
  await h.reply('/api/ik', { ok: false, why: '未找到满足完整位姿的解' })
  await h.inputValue('X / mm', '201'); await h.time.advance(160)
  assert.deepEqual(h.http.latest('/api/ik').body.seed, branch)
  await h.reply('/api/ik', solution(branch))
  h.world.snap.config_rev++
  await h.click('求解预览')
  assert.deepEqual(h.http.latest('/api/ik').body.seed, q)
  await h.reply('/api/ik', solution())
})

test('自动姿态必须显式选择，显示实际 A/B/C；切回固定姿态后重新求解', async t => {
  const h = await fixture(t)
  await h.click('自动姿态'); await h.time.advance(160)
  assert.equal(h.http.latest('/api/ik').body.position_only, true)
  assert.equal(h.input('A / °').props.disabled, true)
  const achieved = [0, -400, 700, 25, -15, 60]
  await h.reply('/api/ik', { ...solution(), achieved, orientation_constrained: false })
  assert.equal(h.input('A / °').value, 25)
  assert.equal(h.input('B / °').value, -15)
  assert.equal(h.button('加入点位草稿').props.disabled, false)
  assert.equal(h.http.by('/api/ik').length, 1, '成功回填不能重新触发求解')
  await h.click('固定姿态'); await h.time.advance(160)
  assert.equal(h.button('加入点位草稿').props.disabled, true)
  assert.equal(h.http.latest('/api/ik').body.position_only, undefined)
  assert.deepEqual(h.http.latest('/api/ik').body.target, achieved)
  await h.reply('/api/ik', solution())
})

test('自动姿态切换使旧请求失效，缺少实际位姿的响应不能发布', async t => {
  const h = await fixture(t)
  await h.click('求解预览')
  await h.click('自动姿态'); await h.time.advance(160)
  await h.reply('/api/ik', solution())
  assert.equal(h.previews.length, 0)
  assert.equal(h.http.latest('/api/ik').body.position_only, true)
  await h.reply('/api/ik', solution())
  assert.equal(h.previews.length, 0)
  assert.match(h.text(), /自动姿态响应不完整/)
})

test('late scene-ready event binds restored mm pose without polling, rebuilding or automatic IK', async t => {
  const h = await fixture(t, { draft: initialDraft(), sceneReady: false })
  assert.equal(h.handles.length, 0)
  assert.equal(h.time.jobs.size, 0)
  h.window.__elfinScene = { setTeachTarget(pose, callback) { h.handles.push({ pose: pose && [...pose], callback }) } }
  h.window.dispatchEvent({ type: 'elfin-scene-ready' }); await settle()
  assert.deepEqual(h.handles.at(-1).pose, initialDraft().pose)
  assert.equal(h.http.calls.length, 0)
  assert.equal(h.time.jobs.size, 0)
  h.handles.at(-1).callback([123, -456, 789]); await settle()
  assert.deepEqual(h.stored().pose, [123, -456, 789, 90, 0, 0])
  h.unmount()
  assert.equal(h.handles.at(-1).pose, null)
  const count = h.handles.length
  h.window.dispatchEvent({ type: 'elfin-scene-ready' }); await settle()
  assert.equal(h.handles.length, count)
  assert.equal(h.time.jobs.size, 0)
})

test('IK singleflight coalesces latest target; old success and error never publish', async t => {
  const h = await fixture(t)
  await h.click('求解预览')
  await h.inputValue('X / mm', '100'); await h.time.advance(160)
  await h.inputValue('X / mm', '200'); await h.time.advance(160)
  assert.equal(h.http.by('/api/ik').length, 1)
  await h.reply('/api/ik', solution([1, 1, 1, 1, 1, 1]))
  assert.equal(h.previews.length, 0)
  assert.equal(h.http.by('/api/ik').length, 2)
  assert.equal(h.http.latest('/api/ik').body.target[0], 200)
  await h.inputValue('X / mm', '300'); await h.time.advance(160)
  h.http.latest('/api/ik').fail(new Error('old IK error')); await settle()
  assert.doesNotMatch(h.text(), /old IK error/)
  assert.equal(h.http.by('/api/ik').length, 3)
  await h.reply('/api/ik', solution())
  assert.deepEqual(h.previews, [q]); assert.equal(h.http.maxActive, 1)
})

for (const [name, mutate] of [
  ['mode roundtrip', w => { w.snap.mode = 'real'; w.snap.mode = 'sim' }],
  ['real connection roundtrip', w => { w.snap.real = { host: 'stub' }; w.snap.real = null }],
  ['session roundtrip', w => { w.snap.session_id = 'new'; w.snap.session_id = 'offline' }],
  ['connection roundtrip', w => { w.connected = false; w.connected = true }],
  ['TCP', w => { w.snap.tcp_frame.translation_mm[2]++ }],
  ['tool revision', w => { w.tools.mounted.revision++ }],
  ['tool profile', w => { w.tools.tool.tcp.translation_mm[0]++ }],
  ['snapshot tool', w => { w.snap.tools = { mounted: { tool_id: 'new', revision: 1 } } }],
  ['configuration revision', w => { w.snap.config_rev++ }],
]) test(`pending IK invalidated by ${name}, including same-tick ABA`, async t => {
  const h = await fixture(t)
  await h.click('求解预览'); mutate(h.world)
  await h.reply('/api/ik', solution())
  assert.equal(h.previews.length, 0)
  assert.equal(h.button('加入点位草稿').props.disabled, true)
  assert.equal(h.http.by('/api/ik').length, 1)
  await solve(h)
  assert.equal(h.previews.length, 1)
})

test('FK uses xyz_mm/rpy_deg; stale FK cannot overwrite a newer target', async t => {
  const h = await fixture(t)
  await h.click('取当前 TCP'); await h.click('取当前 TCP', true)
  assert.equal(h.http.by('/api/fk').length, 1)
  await h.reply('/api/fk', fkResult)
  assert.equal(h.input('X / mm').value, 100)
  assert.equal(h.previews.length, 0)
  await h.time.advance(160); await h.reply('/api/ik', solution())
  await h.click('取当前 TCP')
  await h.inputValue('X / mm', '222'); await h.time.advance(160)
  await h.reply('/api/fk', { ...fkResult, xyz_mm: [999, 999, 999] })
  assert.equal(h.stored().pose[0], 222)
  assert.equal(h.http.latest('/api/ik').body.target[0], 222)
  await h.reply('/api/ik', solution())
  assert.equal(h.http.maxActive, 1)
})

test('FK errors, stale joints, malformed poses and invalid IK release busy', async t => {
  const h = await fixture(t)
  await h.click('取当前 TCP'); h.http.latest('/api/fk').fail(new Error('FK failed')); await settle()
  assert.match(h.text(), /FK failed/)
  await h.click('取当前 TCP'); h.world.snap.joints.q[0]++
  await h.reply('/api/fk', fkResult)
  assert.equal(h.input('X / mm').value, 0)
  assert.equal(h.http.by('/api/ik').length, 0)
  await h.click('取当前 TCP'); await h.reply('/api/fk', { ok: true, xyz_mm: [1, 2], rpy_deg: [3, 4, 5, 6] })
  assert.equal(h.button('加入点位草稿').props.disabled, true)
  for (const result of [{ ok: true, q }, { ok: true, joints: [1, 2] }, { ok: false, why: 'unreachable' }]) {
    await h.click('求解预览'); await h.reply('/api/ik', result)
    assert.equal(h.button('加入点位草稿').props.disabled, true)
    assert.equal(h.button('求解预览').props.disabled, false)
  }
  assert.equal(h.previews.length, 0)
})

test('undo/redo restores deep snapshots, supports renames, and invalidates redo branches', async t => {
  const h = await fixture(t)
  await add(h)
  await h.inputValue('草稿点位1名称', 'renamed')
  await h.click('撤销'); assert.equal(h.input('草稿点位1名称').value, '示教点 1')
  await h.click('重做'); assert.equal(h.input('草稿点位1名称').value, 'renamed')
  await h.click('移除'); assert.equal(h.input('草稿点位1名称'), undefined)
  await h.click('撤销'); assert.equal(h.input('草稿点位1名称').value, 'renamed')
  await h.inputValue('草稿点位1名称', 'branch')
  assert.equal(h.button('重做').props.disabled, true)
  await h.click('撤销'); assert.equal(h.stored().rows[0].name, 'renamed')
  await h.click('撤销'); assert.equal(h.stored().rows[0].name, '示教点 1')
  await h.click('撤销'); assert.deepEqual(h.stored().rows, [])
  assert.equal(h.button('撤销').props.disabled, true)
})

test('save is singleflight and locks scene, inputs, history and forced stale handlers', async t => {
  const h = await fixture(t, { draft: initialDraft() })
  await h.inputValue('草稿点位1名称', '待保存')
  const callback = h.handles.at(-1).callback
  await h.click('批量保存仿真点位')
  assert.match(h.text(), /正在批量保存/)
  assert.equal(h.handles.at(-1).pose, null)
  for (const name of ['撤销', '重做', '移除', '加入点位草稿', '取当前 TCP', '求解预览', '批量保存仿真点位']) assert.equal(h.button(name).props.disabled, true, name)
  assert.equal(h.input('X / mm').props.disabled, true)
  assert.equal(h.input('草稿点位1名称').props.disabled, true)
  await h.click('批量保存仿真点位', true); await h.click('撤销', true); await h.click('移除', true)
  await h.inputValue('草稿点位1名称', 'forbidden', true)
  callback([999, 999, 999]); await settle()
  assert.equal(h.http.by('/api/teaching/batch').length, 1)
  assert.equal(h.stored().rows[0].name, '待保存')
  assert.equal(h.stored().pose[0], 100)
  await h.reply('/api/teaching/batch', { ok: false, why: 'disk failure' }, 500)
  assert.equal(h.stored().rows[0].name, '待保存')
  assert.match(h.text(), /草稿已保留/)
  assert.equal(h.button('撤销').props.disabled, false)
  await h.click('撤销'); assert.equal(h.stored().rows[0].name, '离线点 1')
  assert.equal(h.http.maxActive, 1)
})

for (const [name, finish] of [
  ['network', call => call.fail()], ['non-JSON', call => call.badJSON()],
  ['HTTP failure even with ok JSON', call => call.reply({ ok: true, items: [{}], points: [] }, 500)],
  ['incomplete items', call => call.reply({ ok: true, items: [], points: [] })],
  ['missing points', call => call.reply({ ok: true, items: [{}] })],
]) test(`batch ${name} never loses persisted drafts`, async t => {
  const h = await fixture(t, { draft: initialDraft() })
  await h.click('批量保存仿真点位'); finish(h.http.latest('/api/teaching/batch')); await settle()
  assert.deepEqual(h.stored().rows, initialDraft().rows)
  assert.equal(h.button('批量保存仿真点位').props.disabled, false)
  assert.deepEqual(clone(h.world.points), [{ id: 'old' }])
  h.unmount()
  const next = await fixture(t, { storage: h.storage })
  assert.equal(next.input('草稿点位1名称').value, '离线点 1')
  assert.match(next.text(), /保存结果尚未确认/)
  assert.equal(next.http.calls.length, 0)
})

test('successful explicit batch applies items/points, clears durable drafts and undo history', async t => {
  const h = await fixture(t, { draft: initialDraft() })
  await h.inputValue('草稿点位1名称', 'saved')
  await h.click('批量保存仿真点位')
  assert.deepEqual(h.http.latest('/api/teaching/batch').body, { points: [{ name: 'saved', joints: q }] })
  await h.reply('/api/teaching/batch', { ok: true, items: [{ id: 'new' }], points: [{ id: 'new' }] })
  assert.deepEqual(h.stored().rows, []); assert.equal(h.stored().saveUnconfirmed, false)
  assert.deepEqual(clone(h.world.points), [{ id: 'new' }])
  assert.equal(h.button('撤销').props.disabled, true)
  assert.equal(h.button('重做').props.disabled, true)
  assert.match(h.text(), /已保存 1/)
})

test('context change during save retains drafts and discards stale points result', async t => {
  const h = await fixture(t, { draft: initialDraft() })
  await h.click('批量保存仿真点位')
  h.world.snap.session_id = 'new'; h.world.snap.session_id = 'offline'
  await h.reply('/api/teaching/batch', { ok: true, items: [{}], points: [{ id: 'stale' }] })
  assert.deepEqual(h.stored().rows, initialDraft().rows)
  assert.deepEqual(clone(h.world.points), [{ id: 'old' }])
  assert.match(h.text(), /未采纳/)
})

test('navigation while saving recovers drafts, keeps shared busy and ignores late success', async t => {
  const h = await fixture(t, { draft: initialDraft() })
  await h.click('批量保存仿真点位'); h.unmount()
  const next = await fixture(t, { storage: h.storage, world: h.world, http: h.http })
  assert.equal(next.button('批量保存仿真点位').props.disabled, true)
  assert.equal(next.button('加入点位草稿').props.disabled, true)
  assert.equal(next.http.calls.length, 1)
  await h.reply('/api/teaching/batch', { ok: true, items: [{}], points: [{ id: 'late' }] })
  assert.deepEqual(next.stored().rows, initialDraft().rows)
  assert.equal(next.button('批量保存仿真点位').props.disabled, false)
  assert.deepEqual(clone(h.world.points), [{ id: 'old' }])
})

test('navigation while IK pending never publishes; the remount waits for singleflight release', async t => {
  const h = await fixture(t)
  const callback = h.handles.at(-1).callback
  await h.click('求解预览'); h.unmount()
  const next = await fixture(t, { storage: h.storage, world: h.world, http: h.http })
  callback([999, 999, 999]); await settle()
  assert.equal(next.button('求解预览').props.disabled, true)
  await next.inputValue('X / mm', '500'); await next.time.advance(160)
  assert.equal(h.http.by('/api/ik').length, 1)
  await h.reply('/api/ik', solution())
  assert.equal(h.previews.length, 0)
  assert.equal(h.http.by('/api/ik').length, 2)
  assert.equal(h.http.latest('/api/ik').body.target[0], 500)
  await h.reply('/api/ik', solution())
  assert.equal(h.http.maxActive, 1)
})

test('corrupt storage is not overwritten on mount; denied storage is visible and blocks save', async t => {
  const storage = new Map([[Helpers.TEACHING_DRAFT_KEY, '{broken']])
  const h = await fixture(t, { storage })
  assert.equal(storage.get(Helpers.TEACHING_DRAFT_KEY), '{broken')
  assert.match(h.text(), /损坏/)
  const denied = await fixture(t, { storageAPI: { getItem() { throw new Error('denied') }, setItem() { throw new Error('quota') } } })
  await add(denied)
  await denied.click('批量保存仿真点位')
  assert.equal(denied.http.by('/api/teaching/batch').length, 0)
  assert.ok(denied.input('草稿点位1名称'))
  assert.match(denied.text(), /尚未可靠持久化/)
})

test('local cleanup failure after server save retains drafts with an unconfirmed warning', async t => {
  const storage = new Map([[Helpers.TEACHING_DRAFT_KEY, JSON.stringify(initialDraft())]])
  let fail = false
  const h = await fixture(t, { storage, storageAPI: { getItem: key => storage.get(key) ?? null,
    setItem(key, value) { if (fail) throw new Error('quota'); storage.set(key, value) } } })
  await h.click('批量保存仿真点位'); fail = true
  await h.reply('/api/teaching/batch', { ok: true, items: [{}], points: [] })
  assert.deepEqual(h.stored().rows, initialDraft().rows)
  assert.ok(h.input('草稿点位1名称'))
  assert.match(h.text(), /本地草稿清理失败/)
  assert.equal(h.stored().saveUnconfirmed, true)
})

test('100 row cap, blank names and nonfinite inputs never create invalid requests', async t => {
  const draft = initialDraft(Array.from({ length: 100 }, (_, i) => ({ name: `p${i}`, joints: [...q] })))
  const h = await fixture(t, { draft })
  await solve(h)
  assert.equal(h.button('加入点位草稿').props.disabled, true)
  await h.click('加入点位草稿', true); assert.equal(h.stored().rows.length, 100)
  await h.inputValue('草稿点位1名称', ' ')
  assert.equal(h.button('批量保存仿真点位').props.disabled, true)
  await h.click('批量保存仿真点位', true)
  assert.equal(h.http.by('/api/teaching/batch').length, 0)
  await h.inputValue('X / mm', '')
  await h.time.advance(1000)
  assert.equal(h.http.by('/api/ik').length, 1)
  assert.equal(h.handles.at(-1).pose, null)
  assert.equal(h.button('求解预览').props.disabled, true)
})

test('invalid-mode/disconnected/missing-session state blocks forced FK/IK/save without hardware', async t => {
  const h = await fixture(t, { draft: initialDraft() })
  for (const mutate of [w => { w.snap.mode = 'invalid' }, w => { w.snap.mode = 'sim'; w.connected = false }, w => { w.connected = true; w.snap.session_id = '' }]) {
    mutate(h.world); await settle()
    for (const name of ['取当前 TCP', '求解预览', '批量保存仿真点位', '加入点位草稿']) await h.click(name, true)
    assert.equal(h.handles.at(-1).pose, null)
  }
  assert.equal(h.http.calls.length, 0)
  assert.deepEqual(h.stored().rows, initialDraft().rows)
})
test('real teaching permits preview and IK, never motion from dragging, but keeps sim-only batch disabled',async t=>{
  const h=await fixture(t,{draft:initialDraft()})
  h.world.snap.mode='real';h.world.snap.real={quality:'valid'};await settle()
  assert.equal(h.button('求解预览').props.disabled,false)
  assert.equal(typeof h.handles.at(-1).callback,'function')
  await h.click('求解预览');h.http.calls.at(-1).reply(solution());await settle()
  assert.ok(h.previews.length>0);assert.equal(h.button('批量保存仿真点位').props.disabled,true)
  assert.ok(h.button('执行目标'));assert.ok(h.button('暂停'));assert.ok(h.button('取消'))
  assert.equal(h.http.calls.some(call=>/movej|motion\/(pause|resume|cancel)/.test(call.path)),false)
})

test('settled IK and old scene callbacks lose validity on context changes; another preview is not cleared', async t => {
  const h = await fixture(t)
  await solve(h)
  const callback = h.handles.at(-1).callback
  h.world.preview = [1, 2, 3, 4, 5, 6]
  h.world.tools.mounted.revision++
  callback([999, 999, 999]); await settle()
  assert.equal(h.button('加入点位草稿').props.disabled, true)
  assert.equal(h.input('X / mm').value, 0)
  assert.deepEqual(clone(h.world.preview), [1, 2, 3, 4, 5, 6])
  await h.time.advance(1000)
  assert.equal(h.http.by('/api/ik').length, 1)
})

test('undo during IK never resets busy; history is bounded and cannot alias restored rows', async t => {
  const h = await fixture(t, { draft: initialDraft() })
  for (let i = 0; i < 35; i++) await h.inputValue('草稿点位1名称', `edit${i}`)
  await h.click('求解预览')
  await h.click('撤销')
  assert.equal(h.stored().rows[0].name, 'edit33')
  assert.equal(h.button('批量保存仿真点位').props.disabled, true)
  await h.reply('/api/ik', solution())
  for (let i = 0; i < 29; i++) await h.click('撤销')
  assert.equal(h.stored().rows[0].name, 'edit4')
  assert.equal(h.button('撤销').props.disabled, true)
  await h.click('重做'); assert.equal(h.stored().rows[0].name, 'edit5')
  await h.inputValue('草稿点位1名称', 'new branch')
  await h.click('撤销'); assert.equal(h.stored().rows[0].name, 'edit5')
})

test('Teaching reports maximum-axis residuals, achieved pose, reason code and solve time', async t => {
  const h = await fixture(t)
  await h.click('求解预览')
  await h.reply('/api/ik', { ...solution(), reason_code: 'solved', solve_ms: 12.34,
    pos_err_mm: .125, rot_err_deg: .25, achieved: [1, 2, 3, 4, 5, 6] })
  assert.match(h.text(), /solved/); assert.match(h.text(), /12.3 ms/)
  assert.match(h.text(), /位置最大轴残差 0.13 mm/)
  assert.match(h.text(), /姿态最大轴残差 0.25 °/)
  assert.match(h.text(), /实际求得 XYZ/)
  const nodes = root => [root, ...root.children.flatMap(nodes)]
  const details = nodes(h.root).find(node => node.type === 'details')
  assert.ok(details)
  assert.notEqual(details.props.open, true, 'diagnostics are collapsed by default')
  assert.ok(details.children.some(node => node.type === 'summary' && node.text === '求解详情'))
})

test('automatic orientation shows achieved ABC without treating requested rotation error as a violation', async t => {
  const h = await fixture(t)
  await h.click('自动姿态'); await h.time.advance(160)
  await h.reply('/api/ik', { ...solution(), orientation_constrained: false, reason_code: 'solved',
    solve_ms: 8, achieved: [0, -400, 700, 25, -15, 60], pos_err_mm: .1, rot_err_deg: 0, requested_rot_err: 179 })
  assert.equal(h.input('A / °').value, 25)
  assert.match(h.text(), /25.00 \/ -15.00 \/ 60.00/)
  assert.doesNotMatch(h.text(), /179|姿态最大轴残差/)
  assert.equal(h.button('加入点位草稿').props.disabled, false)
})

for (const code of ['solve_timeout', 'not_converged', 'ik_busy', 'context_changed', 'cancelled']) {
  test(`Teaching ${code} remains a visible search failure, without proving unreachable or retrying automatically`, async t => {
    const h = await fixture(t)
    await h.click('求解预览')
    await h.reply('/api/ik', { ok: false, reason_code: code, solve_ms: 900,
      why: '本轮搜索未成功，不代表目标已被证明不可达', pos_err_mm: 2, rot_err_deg: 3 })
    assert.match(h.text(), new RegExp(code)); assert.match(h.text(), /本轮搜索未成功/)
    assert.equal(h.button('加入点位草稿').props.disabled, true)
    assert.equal(h.http.calls.filter(call => call.path === '/api/ik').length, 1)
    assert.equal(h.previews.length, 0)
    assert.equal(h.world.targetState.stage, 'failed')
  })
}

for (const code of ['unreachable_pose', 'unreachable_position']) {
  test(`geometric ${code} renders proof and missing residuals as not computed, never NaN`, async t => {
    const h = await fixture(t)
    await h.click('求解预览')
    await h.reply('/api/ik', { ok: false, reason_code: code, solve_ms: .5, proof: '几何外包络证明',
      gap_mm: 12, upper_bound_mm: 900, tolerance_mm: .2, why: '目标超出经证明的几何上界' })
    assert.match(h.text(), /几何外包络证明/)
    assert.match(h.text(), /位置最大轴残差 未计算/)
    assert.match(h.text(), /姿态最大轴残差 未计算/)
    assert.match(h.text(), /间隙 12.00 mm/)
    assert.doesNotMatch(h.text(), /NaN|undefined/)
    assert.equal(h.previews.length, 0)
    assert.equal(h.button('加入点位草稿').props.disabled, true)
  })
}
