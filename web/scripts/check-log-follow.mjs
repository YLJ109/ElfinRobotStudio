// Offline real-component regression: fake DOM, fake clocks, no browser/service/IO.
// Run: node --test web/scripts/check-log-follow.mjs
import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync, readFileSync } from 'node:fs'
import vm from 'node:vm'
import * as Vue from 'vue'
import * as Pinia from 'pinia'
import { parse, compileScript, compileStyle } from '@vue/compiler-sfc'
import { summarizeLog, logKindLabel } from '../src/logSummary.js'
import { createLogFollow } from '../src/logFollow.js'
import { filterLogRows } from '../src/workspaceInsights.js'

const src = path => new URL('../src/' + path, import.meta.url)
const read = path => readFileSync(src(path), 'utf8')
async function settle() { for (let i = 0; i < 6; ++i) { await Promise.resolve(); await Vue.nextTick() } }
function clock() {
  let now = 0, id = 0
  const jobs = new Map()
  return {
    jobs, now: () => now,
    setTimeout(fn, ms) { jobs.set(++id, { at: now + ms, fn }); return id },
    clearTimeout(id) { jobs.delete(id) },
    async advance(ms) {
      const end = now + ms
      await settle()
      for (let n = 0; ; ++n) {
        assert.ok(n < 1000, 'no runaway timer loop')
        const [id, job] = [...jobs].sort((a, b) => a[1].at - b[1].at)[0] || []
        if (!job || job.at > end) break
        now = job.at; jobs.delete(id); job.fn(); await settle()
      }
      now = end; await settle()
    },
  }
}
function hub() {
  const listeners = new Map()
  return {
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn) },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn) },
    dispatchEvent(event) { for (const fn of [...(listeners.get(event.type) || [])]) fn(event) },
    listenerCount() { return [...listeners.values()].reduce((n, set) => n + set.size, 0) },
  }
}
const all = root => [root, ...root.children.flatMap(all)]
const text = el => el.text + el.children.map(text).join('')
const has = (el, name) => String(el.props.class || '').split(' ').includes(name)
function node(type, value = '') {
  let scrollTop = 0
  const events = hub()
  return {
    ...events, dispatchLocal: events.dispatchEvent,
    dispatchEvent(event) {
      const path = []
      for (let el = this; el; el = el.parent) path.unshift(el)
      for (const el of path) el.dispatchLocal(event)
    },
    type, tagName: type.toUpperCase(), text: value, props: {}, children: [], parent: null,
    value: '', selected: false, selectedIndex: -1, clientHeight: 96, writes: 0, heightGrowth: 0,
    getAttribute(name) { return this.props[name] === undefined ? null : String(this.props[name]) },
    querySelectorAll(selector) { assert.equal(selector, '[data-log-seq]'); return all(this).filter(el => el.props['data-log-seq'] !== undefined) },
    getBoundingClientRect() {
      if (has(this, 'ln') || has(this, 'lrow')) {
        let box = this.parent
        while (box && !has(box, 'body') && !has(box, 'tscroll')) box = box.parent
        const offset = has(this, 'lrow') ? 26 + Number.parseFloat(this.props.style.transform.slice(11))
          : this.parent.children.filter(el => has(el, 'ln')).indexOf(this) * 24
        const top = offset - (box?.scrollTop || 0)
        return { top, bottom: top + 24 }
      }
      return { top: 0, bottom: this.clientHeight }
    },
    get scrollHeight() {
      const spacer = this.children.find(el => has(el, 'spacer'))
      if (spacer) return 26 + Number.parseFloat(spacer.props.style?.height || '0')
      const lines = this.children.find(el => has(el, 'log-lines'))
      if (lines) return lines.scrollHeight
      return this.children.length * 24 + this.heightGrowth + this.children.reduce((n, el) => n + el.heightGrowth, 0)
    },
    get scrollTop() { return scrollTop },
    set scrollTop(value) { ++this.writes; scrollTop = Math.max(0, Math.min(Number(value), this.scrollHeight - this.clientHeight)) },
    userScroll(value) { scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)) },
    get options() { return this.children.filter(el => el.type === 'option') },
  }
}
const renderer = Vue.createRenderer({
  createElement: type => node(type), createText: value => node('#text', value), createComment: value => node('#comment', value),
  insert(el, parent, anchor = null) {
    if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1)
    el.parent = parent
    const at = anchor ? parent.children.indexOf(anchor) : -1
    if (at < 0) parent.children.push(el); else parent.children.splice(at, 0, el)
  },
  remove(el) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); el.parent = null },
  setText(el, value) { el.text = value }, setElementText(el, value) { el.text = value; el.children = [] },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] || null,
  patchProp(el, key, _old, value) { el.props[key] = value; if (key === 'value') { el.value = value; el._value = value } },
})
async function emit(el, type, extra = {}) {
  assert.ok(el, `target for ${type} exists`)
  const event = { type, target: el, currentTarget: el, button: 0, pointerId: 1, key: '',
    preventDefault() {}, stopPropagation() {}, ...extra }
  // Native listeners and compiled Vue listeners both execute, as in the DOM.
  el.dispatchEvent(event)
  const prefix = 'on' + type[0].toUpperCase() + type.slice(1)
  for (const [name, handler] of Object.entries(el.props)) {
    if (name.replace(/(?:Passive|Capture|Once)+$/g, '') !== prefix) continue
    for (const fn of Array.isArray(handler) ? handler : handler ? [handler] : []) fn(event)
  }
  await settle()
}
const records = (n = 30) => Array.from({ length: n }, (_, i) => ({ seq: i + 1, t: i, kind: i % 2 ? 'event' : 'sys', text: `raw-${i + 1}` }))
async function fixture(t, path, options = {}) {
  const time = clock(), window = hub(), document = { ...hub(), hidden: false }, observers = []
  const preferences = new Map(), state = Vue.reactive({ active: options.active ?? true, present: true })
  const pinia = Pinia.createPinia()
  const useWorld = Pinia.defineStore('logs-offline', { state: () => ({ logs: options.logs || records() }),
    actions: { call() { throw new Error('No backend call is allowed') } } })
  Pinia.setActivePinia(pinia)
  const env = vm.createContext({ Vue, Pinia, useWorld, summarizeLog, logKindLabel, filterLogRows, window, document,
    localStorage: { getItem: key => preferences.get(key) ?? null, setItem: (key, value) => preferences.set(key, value) },
    setTimeout: time.setTimeout, clearTimeout: time.clearTimeout,
    Date: class extends Date { static now() { return time.now() } },
    ResizeObserver: class {
      constructor(fn) { this.fn = fn; observers.push(this) }
      observe(target) { this.connected = true; (this.targets ||= new Set()).add(target) }
      disconnect() { this.connected = false; this.targets?.clear() }
    },
    fetch() { throw new Error('Network is forbidden') },
    WebSocket: class { constructor() { throw new Error('WebSocket is forbidden') } },
  })
  if (existsSync(src('logFollow.js'))) {
    const code = read('logFollow.js').replace(/^import[\s\S]*?from ['"]vue['"];?\r?\n/gm, match =>
      match.replace(/^import \{([\s\S]*?)\} from ['"]vue['"];?/, 'const {$1} = Vue;'))
      .replace(/^export /gm, '')
    vm.runInContext(code, env)
  }
  const { descriptor, errors } = parse(read(path), { filename: path })
  assert.deepEqual(errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, id: 'log-test', scoped: style.scoped }).errors, [])
  const code = compileScript(descriptor, { id: 'log-test', inlineTemplate: true }).content
    .replace(/^import \{([^}]+)\} from ['"](vue|pinia)['"]\s*$/gm,
      (_, names, mod) => `const {${names.replace(/\bas\b/g, ':')}} = ${mod === 'vue' ? 'Vue' : 'Pinia'};`)
    .replace(/^import .* from ['"]\.\.\/(?:store|logSummary\.js|logFollow\.js|workspaceInsights\.js)['"]\s*$/gm, '')
    .replace('export default', 'globalThis.Component =')
  // Vue imports use const; isolate from the helper module's lexical imports.
  vm.runInContext('{\n' + code + '\n}', env)
  const renderPanel = () => Vue.h(env.Component, { active: state.active })
  const root = node('root'), app = renderer.createApp({ setup: () => () => options.keepAlive
    ? Vue.h(Vue.KeepAlive, null, { default: () => state.present ? renderPanel() : Vue.h('aside') }) : renderPanel() })
  app.use(pinia); app.mount(root)
  let mounted = true
  const h = { root, time, window, document, observers, state, world: useWorld(),
    get box() { return all(root).find(el => has(el, 'body') || has(el, 'tscroll')) },
    button(label) { return all(root).find(el => el.type === 'button' && text(el).trim() === label) },
    append(kind = 'event') { const seq = (this.world.logs.at(-1)?.seq || 0) + 1; this.world.logs.push({ seq, t: seq, kind, text: `raw-${seq}` }) },
    async up(top = 48) { await emit(this.box, 'wheel', { deltaY: -120 }); this.box.userScroll(top); await emit(this.box, 'scroll') },
    bottom() { return Math.max(0, this.box.scrollHeight - this.box.clientHeight) },
    unmount() { if (mounted) { mounted = false; app.unmount() } },
  }
  t.after(() => { const surfaces = all(root).filter(el => has(el, 'logs') || has(el, 'body') || has(el, 'tscroll'))
    h.unmount(); assert.equal(time.jobs.size, 0, 'all timers cleaned');
    assert.equal(window.listenerCount(), 0); assert.equal(document.listenerCount(), 0);
    // Vue owns v-model's native select handler; only our bound surfaces must
    // explicitly unbind. Detached select nodes are collected with the DOM.
    assert.ok(surfaces.every(el => el.listenerCount() === 0)); assert.ok(observers.every(o => !o.connected)) })
  await settle(); return h
}

for (const path of ['components/LogDock.vue', 'panels/LogsPanel.vue']) {
  test(`${path}: initial content and new rows follow the bottom`, async t => {
    const h = await fixture(t, path)
    assert.equal(h.box.scrollTop, h.bottom())
    h.append(); await settle()
    assert.equal(h.box.scrollTop, h.bottom())
    assert.ok(text(h.root).includes('raw-31'))
  })
  test(`${path}: scrolling up is not interrupted by new records`, async t => {
    const h = await fixture(t, path)
    await h.up(); h.append(); await settle()
    assert.equal(h.box.scrollTop, 48)
    assert.ok(h.button('回到最新'))
    assert.match(text(h.root), /查看历史/)
  })
  test(`${path}: returning to the bottom resumes following on the next row`, async t => {
    const h = await fixture(t, path)
    await h.up()
    h.box.userScroll(h.bottom()); await emit(h.box, 'scroll')
    assert.equal(h.button('回到最新'), undefined)
    assert.equal(h.time.jobs.size, 0)
    h.append(); await settle()
    assert.equal(h.box.scrollTop, h.bottom())
  })
  test(`${path}: 10 seconds is measured from the LAST log interaction, not new logs`, async t => {
    const h = await fixture(t, path)
    await h.up(); await h.time.advance(9000)
    await emit(h.box, 'wheel', { deltaY: -20 })
    for (let i = 0; i < 9; ++i) { h.append(); await h.time.advance(1000) }
    assert.equal(h.box.scrollTop, 48)
    await h.time.advance(999); assert.equal(h.box.scrollTop, 48)
    await h.time.advance(1)
    assert.equal(h.box.scrollTop, h.bottom())
    assert.equal(h.button('回到最新'), undefined)
    assert.equal(h.time.jobs.size, 0)
  })
  test(`${path}: explicit return button immediately follows latest`, async t => {
    const h = await fixture(t, path)
    await h.up(); h.append(); await settle()
    await emit(h.button('回到最新'), 'click')
    assert.equal(h.box.scrollTop, h.bottom())
    h.append(); await settle(); assert.equal(h.box.scrollTop, h.bottom())
  })
  for (const input of ['pointer', 'touch', 'keyboard']) {
    test(`${path}: ${input} interaction pauses and a held input cannot be stolen by the idle timeout`, async t => {
      const h = await fixture(t, path), box = h.box
      if (input === 'pointer') await emit(box, 'pointerdown', { pointerId: 7 })
      if (input === 'touch') await emit(box, 'touchstart', { touches: [{}] })
      if (input === 'keyboard') await emit(box, 'keydown', { key: 'PageUp', code: 'PageUp' })
      box.userScroll(48); await emit(box, 'scroll')
      h.append(); await h.time.advance(20000)
      assert.equal(box.scrollTop, 48)
      assert.equal(h.time.jobs.size, 0, 'no timer can fire during a held input')
      const type = input === 'pointer' ? 'pointerup' : input === 'touch' ? 'touchend' : 'keyup'
      h.window.dispatchEvent({ type, pointerId: 7, key: 'PageUp', code: 'PageUp', touches: [] })
      await h.time.advance(9999); assert.equal(box.scrollTop, 48)
      await h.time.advance(1); assert.equal(box.scrollTop, h.bottom())
    })
  }
  test(`${path}: unrelated window input cannot extend the log's idle deadline`, async t => {
    const h = await fixture(t, path)
    await h.up(); await h.time.advance(9000)
    for (const type of ['pointerup', 'touchend', 'keyup']) h.window.dispatchEvent({ type, pointerId: 99, key: 'a', touches: [] })
    await h.time.advance(1000)
    assert.equal(h.box.scrollTop, h.bottom())
  })
  test(`${path}: programmatic scroll events do not misclassify the reader`, async t => {
    const h = await fixture(t, path)
    // A delayed programmatic scroll arrives after content growth but before the
    // next bottom write. Being temporarily above bottom is not manual input.
    h.append(); h.box.dispatchEvent({ type: 'scroll', target: h.box })
    await settle()
    assert.equal(h.box.scrollTop, h.bottom())
    assert.equal(h.button('回到最新'), undefined)
  })
  test(`${path}: user input cancels an already queued follow before it can write`, async t => {
    const h = await fixture(t, path)
    h.append()
    await Vue.nextTick() // follow has queued nextTick, not executed it yet
    h.box.dispatchEvent({ type: 'wheel', deltaY: -100, target: h.box })
    h.box.userScroll(48)
    await settle()
    assert.equal(h.box.scrollTop, 48)
    assert.ok(h.button('回到最新'))
  })
  test(`${path}: hidden document cancels timers; quick return preserves history and the original deadline`, async t => {
    const h = await fixture(t, path)
    await h.up(); await h.time.advance(2000)
    h.document.hidden = true; h.document.dispatchEvent({ type: 'visibilitychange' })
    assert.equal(h.time.jobs.size, 0)
    const writes = h.box.writes
    h.append(); await h.time.advance(3000)
    assert.equal(h.box.writes, writes)
    h.document.hidden = false; h.document.dispatchEvent({ type: 'visibilitychange' }); await settle()
    assert.equal(h.box.scrollTop, 48)
    await h.time.advance(4999); assert.equal(h.box.scrollTop, 48)
    await h.time.advance(1); assert.equal(h.box.scrollTop, h.bottom())
  })
  test(`${path}: a long hidden interval resumes only when visible again`, async t => {
    const h = await fixture(t, path)
    await h.up(); h.document.hidden = true; h.document.dispatchEvent({ type: 'visibilitychange' })
    await h.time.advance(20000); assert.equal(h.box.scrollTop, 48)
    h.document.hidden = false; h.document.dispatchEvent({ type: 'visibilitychange' }); await settle()
    assert.equal(h.box.scrollTop, h.bottom())
  })
  test(`${path}: a held pointer remains protected across hidden/visible transitions`, async t => {
    const h = await fixture(t, path)
    await emit(h.box, 'pointerdown', { pointerId: 8 })
    h.box.userScroll(48)
    h.document.hidden = true; h.document.dispatchEvent({ type: 'visibilitychange' })
    await h.time.advance(20000)
    h.document.hidden = false; h.document.dispatchEvent({ type: 'visibilitychange' }); await settle()
    assert.equal(h.box.scrollTop, 48); assert.equal(h.time.jobs.size, 0)
    h.window.dispatchEvent({ type: 'pointerup', pointerId: 8 })
    await h.time.advance(9999); assert.equal(h.box.scrollTop, 48)
    await h.time.advance(1); assert.equal(h.box.scrollTop, h.bottom())
  })
  test(`${path}: KeepAlive deactivation clears timers and pending work`, async t => {
    const h = await fixture(t, path, { keepAlive: true })
    await h.up(); const box = h.box, writes = box.writes
    h.state.present = false; await settle()
    assert.equal(h.time.jobs.size, 0)
    h.append(); await h.time.advance(20000)
    assert.equal(box.writes, writes)
    h.state.present = true; await settle()
    assert.equal(h.box.scrollTop, h.bottom())
  })
  test(`${path}: resize follows when live but never dislodges historical reading`, async t => {
    const h = await fixture(t, path)
    h.box.clientHeight = 120
    h.observers.filter(o => o.connected).forEach(o => o.fn())
    await settle(); assert.equal(h.box.scrollTop, h.bottom())
    await h.up(); h.box.clientHeight = 144
    h.observers.filter(o => o.connected).forEach(o => o.fn())
    await settle(); assert.equal(h.box.scrollTop, 48)
  })
  test(`${path}: a CSS-hidden viewport suspends its timeout until measurable again`, async t => {
    const h = await fixture(t, path)
    await h.up(); h.box.clientHeight = 0
    h.observers.filter(o => o.connected).forEach(o => o.fn())
    assert.equal(h.time.jobs.size, 0)
    const writes = h.box.writes
    h.append(); await h.time.advance(20000)
    assert.equal(h.box.writes, writes)
    h.box.clientHeight = 96
    h.observers.filter(o => o.connected).forEach(o => o.fn())
    await settle(); assert.equal(h.box.scrollTop, h.bottom())
  })
  test(`${path}: bounded buffer eviction preserves the same visible record`, async t => {
    const h = await fixture(t, path, { logs: records(150) })
    await h.up(240)
    const visible = () => h.box.querySelectorAll('[data-log-seq]').find(row => row.getBoundingClientRect().bottom > 0)
    const seq = visible().getAttribute('data-log-seq'), offset = visible().getBoundingClientRect().top
    h.append()
    if (path.includes('LogsPanel')) h.world.logs = h.world.logs.slice(1)
    await settle()
    assert.equal(visible().getAttribute('data-log-seq'), seq)
    assert.equal(visible().getBoundingClientRect().top, offset)
    assert.ok(h.button('回到最新'))
  })
}

test('dock follows late text wrapping without a new log or viewport resize', async t => {
  const h = await fixture(t, 'components/LogDock.vue')
  const content = h.box.children.find(el => has(el, 'log-lines')) || h.box.children.at(-1)
  content.heightGrowth += 120
  for (const observer of h.observers) {
    // 自定义渲染器的普通节点会被Vue代理，浏览器原生Element不会。
    if (observer.connected && [...(observer.targets || [])].some(target => Vue.toRaw(target) === content)) observer.fn([{ target: content }])
  }
  await settle()
  assert.equal(h.box.scrollTop, h.bottom(), '字体或换行使内容增高后仍应贴住最新一行')
  assert.match(text(h.root), /自动跟随最新/)
})

test('late dock content resize does not pull a reader away from history', async t => {
  const h = await fixture(t, 'components/LogDock.vue')
  await h.up(48)
  const content = h.box.children.find(el => has(el, 'log-lines'))
  content.heightGrowth += 120
  for (const observer of h.observers) {
    if (observer.connected && [...(observer.targets || [])].some(target => Vue.toRaw(target) === content)) observer.fn([{ target: content }])
  }
  await settle()
  assert.equal(h.box.scrollTop, 48)
  assert.match(text(h.root), /查看历史/)
  await h.time.advance(10000)
  assert.equal(h.box.scrollTop, h.bottom())
})

test('ordinary log page is chronological, keeps raw records and filtering', async t => {
  const input = records(), h = await fixture(t, 'panels/LogsPanel.vue', { logs: input })
  await h.up(0)
  const rows = all(h.root).filter(el => has(el, 'lrow'))
  assert.ok(text(rows[0]).includes('raw-1'), 'oldest row is first; latest is at the bottom')
  assert.deepEqual(h.world.logs.map(row => ({ ...row })), input)
  const select = all(h.root).find(el => el.type === 'select')
  select.options.forEach(option => { option.selected = option._value === 'sys' })
  await emit(select, 'change')
  const filtered = all(h.root).filter(el => has(el, 'lrow'))
  assert.ok(filtered.length > 0)
  assert.ok(filtered.every(el => !text(el).includes('event')))
  assert.deepEqual(h.world.logs.map(row => ({ ...row })), input, 'filter never modifies records')
})

test('filter toolbar interaction also protects history and resets the idle deadline', async t => {
  const h = await fixture(t, 'panels/LogsPanel.vue')
  await h.up(); await h.time.advance(9000)
  const select = all(h.root).find(el => el.type === 'select')
  await emit(select, 'keydown', { key: 'ArrowDown', code: 'ArrowDown' })
  await h.time.advance(2000)
  assert.equal(h.box.scrollTop, 48, 'editing the log filter is still log interaction')
  assert.equal(h.time.jobs.size, 0, 'held filter key blocks timeout')
  h.window.dispatchEvent({ type: 'keyup', key: 'ArrowDown', code: 'ArrowDown' })
  await h.time.advance(9999); assert.equal(h.box.scrollTop, 48)
  await h.time.advance(1); assert.equal(h.box.scrollTop, h.bottom())
})

test('filtering a deep historical position never leaves a populated virtual list blank', async t => {
  const h = await fixture(t, 'panels/LogsPanel.vue', { logs: records(2000) })
  await h.up(47000)
  const select = all(h.root).find(el => el.type === 'select')
  select.options.forEach(option => { option.selected = option._value === 'sys' })
  await emit(select, 'change')
  // A shorter spacer can clamp scrollTop before the native scroll event.
  // The virtual range must be valid even before that delayed event arrives.
  h.box.userScroll(h.box.scrollTop) // native layout clamp, no scroll event yet
  const rows = all(h.root).filter(el => has(el, 'lrow'))
  assert.ok(rows.length > 0)
  assert.ok(rows.some(row => { const rect = row.getBoundingClientRect(); return rect.bottom > 0 && rect.top < h.box.clientHeight }))
  assert.ok(rows.every(row => !text(row).includes('event')))
  assert.deepEqual(h.world.logs.map(row => ({ ...row })), records(2000))
})

test('logs inactive tab retains measured height, cancels timeout, remeasures on return', async t => {
  const h = await fixture(t, 'panels/LogsPanel.vue')
  await h.up(); h.state.active = false; await settle()
  assert.equal(h.time.jobs.size, 0)
  assert.ok(h.observers.every(o => !o.connected))
  const writes = h.box.writes
  h.box.clientHeight = 0; h.append(); await h.time.advance(20000)
  assert.equal(h.box.writes, writes)
  h.box.clientHeight = 240; h.state.active = true; await settle()
  assert.equal(h.box.scrollTop, h.bottom())
  assert.ok(h.observers.some(o => o.connected))
})

test('large ordinary log page still virtualizes while following the newest record', async t => {
  const h = await fixture(t, 'panels/LogsPanel.vue', { logs: records(2000) })
  h.append(); await settle()
  const rows = all(h.root).filter(el => has(el, 'lrow'))
  assert.ok(rows.length < 100, 'virtualization remains bounded')
  assert.ok(text(rows.at(-1)).includes('raw-2001'))
  assert.equal(h.box.scrollTop, h.bottom())
})

test('ordinary log measurement survives hidden zero-height and real resize observations', async t => {
  const h = await fixture(t, 'panels/LogsPanel.vue', { logs: records(2000) })
  await h.up(0)
  const count = () => all(h.root).filter(el => has(el, 'lrow')).length
  assert.equal(count(), 44)
  h.box.clientHeight = 480
  h.observers.filter(o => o.connected).forEach(o => o.fn())
  await settle(); assert.equal(count(), 60)
  h.state.active = false; await settle(); h.box.clientHeight = 0
  h.append(); await settle(); assert.equal(count(), 60, 'hidden zero height does not overwrite the measurement')
  h.box.clientHeight = 240; h.state.active = true; await settle()
  assert.equal(count(), 50, 'return measures the new nonzero viewport')
  assert.equal(h.box.scrollTop, 0, 'a quick tab return preserves historical reading')
})

for (const path of ['components/LogDock.vue', 'panels/LogsPanel.vue']) {
  test(`${path}: first incoming record after an empty mount is displayed and followed`, async t => {
    const h = await fixture(t, path, { logs: [] })
    assert.match(text(h.root), /暂无/)
    h.append(); await settle()
    assert.ok(text(h.root).includes('raw-1'))
    assert.equal(h.box.scrollTop, h.bottom())
    assert.equal(h.time.jobs.size, 0)
  })
}

test('closing the dock clears its timeout and reopening rebinds without duplicate listeners', async t => {
  const h = await fixture(t, 'components/LogDock.vue')
  await h.up(); const old = h.box
  const close = all(h.root).find(el => el.props['aria-label'] === '收起运行日志')
  await emit(close, 'click')
  assert.equal(h.time.jobs.size, 0); assert.equal(old.listenerCount(), 0)
  await h.time.advance(20000); h.append(); await settle()
  await emit(h.button('运行日志'), 'click')
  assert.equal(h.box.scrollTop, h.bottom())
  await h.up(); assert.ok(h.button('回到最新'))
})

test('dock preserves expandable original text while paused', async t => {
  const original = '模型 elfin5 载入，限位与几何来自官方 URDF'
  const h = await fixture(t, 'components/LogDock.vue', { logs: [...records(), { seq: 31, t: 31, kind: 'sys', text: original }] })
  const details = all(h.root).find(el => el.type === 'details' && text(el).includes(original))
  const summary = details.children.find(el => el.type === 'summary')
  assert.equal(summary.props.title, original)
  await h.up(); await emit(h.box, 'pointerdown')
  details.props.open = true; await emit(h.box, 'toggle', { target: details })
  h.append(); await settle()
  assert.equal(h.box.scrollTop, 48)
  assert.ok(text(details).includes(original))
  assert.equal(h.world.logs[30].text, original)
})

for(const path of ['components/LogDock.vue','panels/LogsPanel.vue'])test(`${path}: Chinese category and compact arrival preserve raw text and error metadata`,async t=>{
  const original='仿真已到达残影目标（六轴残差≤0.3°），残影自动收掉'
  const extra={cmd:'MoveJ,0,1,2,3,4,5,6,;',code:20904,error:'TimeoutError FSM=14'}
  const h=await fixture(t,path,{logs:[{seq:1,t:1,kind:'event',text:original,extra}]})
  assert.ok(text(h.root).includes('事件'))
  assert.ok(text(h.root).includes('仿真已到位，已收起预览'))
  const target=all(h.root).find(el=>el.props.title===original)
  assert.ok(target)
  if(path.includes('LogsPanel'))await emit(target,'click')
  for(const detail of [original,'MoveJ,0,1,2,3,4,5,6,;','20904','TimeoutError FSM=14'])assert.ok(text(h.root).includes(detail))
  assert.equal(h.world.logs[0].kind,'event');assert.equal(h.world.logs[0].text,original)
})

test('controller ignores a cancelled timer even if its callback was already queued', async () => {
  const time = clock(), box = { scrollTop: 0, scrollHeight: 1000, clientHeight: 100 }
  const follow = createLogFollow({ getElement: () => box, now: time.now, schedule: time.setTimeout, cancel: time.clearTimeout })
  follow.setActive(true); await settle(); follow.handlers.wheel()
  box.scrollTop = 48
  const stale = [...time.jobs.values()][0].fn
  await time.advance(9000); follow.handlers.wheel()
  stale(); await settle(); assert.equal(box.scrollTop, 48)
  await time.advance(10000); assert.equal(box.scrollTop, 900)
  follow.dispose(); assert.equal(time.jobs.size, 0)
})

test('controller cancels a queued initial write on disposal or inactivity', async () => {
  for (const ending of ['dispose', 'inactive']) {
    const time = clock(), queue = [], box = { scrollTop: 48, scrollHeight: 1000, clientHeight: 100 }
    const follow = createLogFollow({ getElement: () => box, afterRender: fn => queue.push(fn),
      now: time.now, schedule: time.setTimeout, cancel: time.clearTimeout })
    follow.setActive(true)
    if (ending === 'dispose') follow.dispose(); else follow.setActive(false)
    queue.forEach(fn => fn()); assert.equal(box.scrollTop, 48); assert.equal(time.jobs.size, 0)
    follow.dispose()
  }
})

test('only the matching pointer release ends a scrollbar hold; cancellation also releases it', async () => {
  const time = clock(), box = { scrollTop: 0, scrollHeight: 1000, clientHeight: 100 }
  const follow = createLogFollow({ getElement: () => box, now: time.now, schedule: time.setTimeout, cancel: time.clearTimeout })
  follow.setActive(true); await settle()
  follow.handlers.pointerdown({ pointerId: 3, button: 0 }); box.scrollTop = 48
  follow.handlers.pointerup({ pointerId: 4 }); await time.advance(20000)
  assert.equal(box.scrollTop, 48); assert.equal(time.jobs.size, 0)
  follow.handlers.pointercancel({ pointerId: 3 }); await time.advance(10000)
  assert.equal(box.scrollTop, 900)
  follow.dispose()
})
