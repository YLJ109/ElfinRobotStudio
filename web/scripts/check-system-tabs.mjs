// Offline: real SFC compilation and setup/routing, fake storage, no HTTP or hardware.
// Run: node web/scripts/check-system-tabs.mjs
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createPinia, storeToRefs } from 'pinia'
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { useWorld, normalizeNavigation, NAV_VIEWS, WORK_TABS, SYSTEM_TABS } from '../src/store.js'
import { filterLogRows, readTaskTab, saveTaskTab, tabFocusIndex } from '../src/workspaceInsights.js'
import { createPerformanceComparison, performanceIdentity } from '../src/performanceComparison.js'

const read = (path) => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
const appSource = read('App.vue')
const setup = (path, deps, props = {}, ctx = {}) => {
  const code = compileScript(parse(read(path)).descriptor, { id: path }).content
    .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
  return new Function(...Object.keys(deps), code)(...Object.values(deps)).setup(props, { expose() {}, ...ctx })
}
// Use the actual shared component, including persistence and keyboard handlers.
const taskTabsCode = compileScript(parse(read('components/TaskTabs.vue')).descriptor, {id:'TaskTabs'}).content
  .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
const TaskTabs = new Function('computed','ref','watch','readTaskTab','saveTaskTab','tabFocusIndex',taskTabsCode)(computed,ref,watch,readTaskTab,saveTaskTab,tabFocusIndex)
const world = () => useWorld(createPinia())
const withStorage = (values, fn) => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const valuesMap = new Map(Object.entries(values))
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: key => valuesMap.get(key) ?? null, setItem: (key, value) => valuesMap.set(key, value),
  } })
  try { return fn(valuesMap) } finally {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous)
    else delete globalThis.localStorage
  }
}
const routing = (w, hash = '') => {
  const location = { hash }
  const history = { replaceState(_state, _title, value) { location.hash = value } }
  const writes = new Map()
  const code = appSource.slice(appSource.indexOf('const VIEWS = NAV_VIEWS'), appSource.indexOf('readHash()                       //'))
  const deps = { w, location, history, normalizeNavigation, NAV_VIEWS, WORK_TABS, SYSTEM_TABS,
    savePreference: (key,value)=>writes.set(key,value),
    localStorage: { setItem: (key, value) => writes.set(key, value) } }
  const actions = new Function(...Object.keys(deps), code + '\nreturn { readHash, writeHash }')(...Object.values(deps))
  return { ...actions, location, writes }
}

test('merged tabs use all six pages, in the requested order', () => {
  assert.deepEqual(SYSTEM_TABS, ['engineering', 'fence', 'logs', 'tele', 'settings', 'about'])
})
for (const legacy of ['logs', 'tele', 'settings', 'about']) {
  test(`legacy saved page ${legacy} opens its corresponding merged tab`, () => {
    assert.deepEqual(normalizeNavigation({ view: legacy, workTab: 'program' }),
      { view: 'engineering', workTab: 'program', systemTab: legacy })
    for (const key of ['view', 'huayan.view']) withStorage({ [key]: legacy }, () => {
      const w = world()
      assert.equal(w.view, 'engineering')
      assert.equal(w.systemTab, legacy)
    })
  })
  test(`legacy #/${legacy} is canonicalized without any device call`, () => {
    const w = world()
    w.call = () => { throw new Error('navigation must not call an API') }
    const route = routing(w, '#/' + legacy)
    route.readHash(); route.writeHash()
    assert.equal(w.view, 'engineering')
    assert.equal(w.systemTab, legacy)
    assert.equal(route.location.hash, '#/engineering/' + legacy)
    assert.equal(route.writes.get('huayan.systemTab'), legacy)
  })
}
test('old work/engineering route and saved workTab migrate to engineering', () => {
  withStorage({ view: 'work', workTab: 'engineering' }, () => {
    assert.equal(world().view, 'engineering')
    assert.equal(world().systemTab, 'engineering')
  })
  const w = world()
  const route = routing(w, '#/work/engineering')
  route.readHash(); route.writeHash()
  assert.equal(route.location.hash, '#/engineering/engineering')
  assert.equal(w.workTab, 'points')
})
test('new merged deep links select every tab', () => {
  for (const id of SYSTEM_TABS) {
    const w = world(), route = routing(w, '#/engineering/' + id)
    route.readHash(); route.writeHash()
    assert.equal(w.systemTab, id)
    assert.equal(route.location.hash, '#/engineering/' + id)
  }
})
test('returning from points retains selected system tab and saved work tab', () => {
  const w = world()
  w.systemTab = 'logs'; w.workTab = 'program'; w.view = 'engineering'
  const route = routing(w)
  w.view = 'work'; route.writeHash()
  assert.equal(route.location.hash, '#/work/program')
  route.location.hash = '#/engineering'; route.readHash(); route.writeHash()
  assert.equal(w.systemTab, 'logs')
  assert.equal(route.location.hash, '#/engineering/logs')
  withStorage(Object.fromEntries(route.writes), () => {
    assert.equal(world().systemTab, 'logs')
    assert.equal(world().workTab, 'program')
  })
})
test('invalid saved values and malformed hashes cannot break navigation', () => {
  assert.deepEqual(normalizeNavigation({ view: 'invalid', workTab: 'invalid', systemTab: 'invalid' }),
    { view: 'control', workTab: 'points', systemTab: 'engineering' })
  const w = world(), route = routing(w, '#/%invalid')
  assert.doesNotThrow(() => route.readHash())
  route.location.hash = '#/not-a-page'; route.readHash()
  assert.equal(w.view, 'control')
})
test('independent work, vision, interaction and scene routes survive normalization', () => {
  for (const tab of WORK_TABS) {
    const w = world(), route = routing(w, '#/work/' + tab)
    route.readHash(); route.writeHash()
    assert.equal(w.view, 'work'); assert.equal(w.workTab, tab)
    assert.equal(route.location.hash, '#/work/' + tab)
  }
  const vision = world(), visionRoute = routing(vision, '#/vision')
  visionRoute.readHash(); visionRoute.writeHash()
  assert.equal(vision.view, 'vision'); assert.equal(visionRoute.location.hash, '#/vision')
  for (const [legacy, expected] of [['hand-follow', 'interaction'], ['hand-track', 'interaction'],
    ['draw-write', 'interaction'], ['objects', 'scene'], ['sorting', 'scene']]) {
    const w = world(), route = routing(w, '#/' + legacy)
    route.readHash(); route.writeHash()
    assert.equal(w.view, expected)
    assert.equal(route.location.hash, '#/' + expected)
  }
  const fence = world(), fenceRoute = routing(fence, '#/fence')
  fenceRoute.readHash(); fenceRoute.writeHash()
  assert.equal(fence.view, 'engineering'); assert.equal(fence.systemTab, 'fence')
  assert.equal(fenceRoute.location.hash, '#/engineering/fence')
})
test('navigation rail has one engineering entry and no duplicate legacy pages', () => {
  const w = world(), events = []
  const panel = setup('components/NavRail.vue', { useWorld: () => w, storeToRefs, computed,
    defineProps() {}, defineEmits: () => (...args) => events.push(args) }, {}, { emit: (...args) => events.push(args) })
  const items = panel.GROUPS.flatMap(group => group.items)
  assert.deepEqual(items.map(item => item[2]), ['控制', '点位', '程序', 'AI', '视觉', '娱乐', '互动', '场景', '系统'])
  assert.equal(items.filter(([view, , label]) => view === 'engineering' && label === '系统').length, 1)
  assert.equal(items.at(-1)[0], 'engineering')
  assert.equal(items.some(([view]) => ['logs', 'tele', 'settings', 'about', 'fence', 'objects', 'sorting'].includes(view)), false)
  assert.deepEqual(items.filter(([view]) => view === 'work').map(([, tab]) => tab), ['points', 'program', 'ai'])
  w.systemTab = 'settings'
  panel.openItem('engineering', null)
  assert.equal(w.systemTab, 'settings')
  assert.equal(panel.isActive('engineering', null), true)
  assert.deepEqual(events, [['reveal-side']])
})

function systemFixture(t) {
  const w = world(), scope = effectScope()
  w.systemTab = 'engineering'
  const panel = scope.run(() => setup('panels/SystemPanel.vue', { useWorld: () => w,
    storeToRefs, defineAsyncComponent: loader => ({ loader }), nextTick, reactive, ref, watch,
    onActivated() {}, onDeactivated() {} }))
  t.after(() => scope.stop())
  return { w, panel }
}
test('tabs keep visited pages mounted and do not eagerly open untouched pages', async t => {
  const { panel, w } = systemFixture(t)
  assert.deepEqual([...panel.visited], ['engineering'])
  assert.deepEqual(panel.TABS.map(tab => tab.label), ['工具', '围栏', '日志', '遥测', '设置', '关于'])
  await panel.selectTab('settings')
  assert.equal(w.systemTab, 'settings')
  assert.deepEqual([...panel.visited], ['engineering', 'settings'])
  await panel.selectTab('engineering')
  assert.deepEqual([...panel.visited], ['engineering', 'settings'])
  const source = read('panels/SystemPanel.vue')
  assert.match(source, /v-show="systemTab === tab\.id"/)
  assert.match(source, /v-if="visited\.has\(tab\.id\)"/)
  for (const file of ['EngineeringPanel', 'FencePanel', 'LogsPanel', 'TelemetryPanel', 'SettingsPanel', 'AboutPanel']) {
    assert.ok(source.includes(`import('./${file}.vue')`))
  }
})
test('arrow/Home/End move focus without changing tab or starting devices', t => {
  const { panel, w } = systemFixture(t)
  let focused = -1, prevented = 0
  const buttons = panel.TABS.map((_, i) => ({ focus() { focused = i } }))
  const event = key => ({ key, preventDefault() { prevented++ },
    currentTarget: { parentElement: { querySelectorAll: () => buttons } } })
  for (const [key, index, expected] of [['ArrowRight', 4, 5], ['ArrowLeft', 0, 5], ['Home', 3, 0], ['End', 1, 5]]) {
    panel.focusTab(event(key), index)
    assert.equal(focused, expected)
    assert.equal(w.systemTab, 'engineering')
  }
  panel.focusTab(event('Tab'), 0)
  panel.focusTab({ ...event('Home'), ctrlKey: true }, 0)
  assert.equal(prevented, 4)
})
test('tab/panel ARIA, roving tabindex and native button activation are present', () => {
  const source = read('panels/SystemPanel.vue')
  for (const marker of ['role="tablist"', 'role="tab"', 'role="tabpanel"', ':aria-selected=',
    ':aria-controls=', ':aria-labelledby=', ':tabindex=', 'type="button"', '@keydown="focusTab']) assert.ok(source.includes(marker))
  assert.doesNotMatch(source, /fetch\(|w\.(?:call|jog|moveJ|connectReal|control|readySequence)\(/)
})
test('App caches system and persistent hand follow, but no other motion panels', () => {
  const caches = [...appSource.matchAll(/<KeepAlive\b([^>]*)>([\s\S]*?)<\/KeepAlive>/g)]
  assert.equal(caches.length, 1)
  const include = caches[0][1].match(/\binclude\s*=\s*["']([^"']+)["']/)?.[1]
  assert.ok(include, 'cache must explicitly allowlist named panels')
  assert.deepEqual(include.split(',').map(name => name.trim()).sort(), ['GamesHub', 'InteractionHub', 'SceneHub', 'SystemPanel'])
  assert.match(caches[0][2], /<component\b[^>]*:is="Panel"/)
  assert.match(appSource, /interaction: InteractionHub/)
  assert.match(appSource, /engineering: SystemPanel/)
  assert.doesNotMatch(appSource, /const (?:LogsPanel|SettingsPanel|TelemetryPanel|EngineeringPanel) =/)
  assert.match(appSource, /scene\.setFenceSafety\(s\.safety\?\.fence_level \|\| 'green'\)/)
  assert.match(appSource, /scene\.setGroundSafety\(s\.safety\?\.ground_level \|\| 'green'\)/)
})
function lifecycleFixture(t, path, initialActive = true) {
  const w = world(), scope = effectScope(), props = reactive({ active: initialActive })
  const hooks = { mounted: [], activated: [], deactivated: [], unmounted: [] }, timers = new Map(), observers = [], requests = []
  const eventTarget = () => {
    const listeners = new Map()
    return {
      addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn) },
      removeEventListener(type, fn) { listeners.get(type)?.delete(fn) },
      dispatchEvent(event) { for (const fn of [...(listeners.get(event.type) || [])]) fn(event) },
      listenerCount() { return [...listeners.values()].reduce((n, set) => n + set.size, 0) },
    }
  }
  const window = eventTarget(), document = { ...eventTarget(), hidden: false }
  let timerId = 0
  const deps = { useWorld: () => w, storeToRefs, computed, ref, reactive, watch, nextTick, Spark: {}, TaskTabs, filterLogRows, AbortController, window, document, createPerformanceComparison, performanceIdentity,
    Date: class extends Date { static now() { return 0 } },
    onMounted: fn => hooks.mounted.push(fn), onActivated: fn => hooks.activated.push(fn),
    onDeactivated: fn => hooks.deactivated.push(fn), onBeforeUnmount: fn => hooks.unmounted.push(fn),
    setInterval: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId },
    setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId },
    clearTimeout: id => timers.delete(id),
    clearInterval: id => timers.delete(id), fetch: async (url,options) => { requests.push({url,options}); return {ok:true,status:200,json:async () => ({n:0})} },
    ResizeObserver: class { constructor(fn) { this.fn = fn; observers.push(this) }
      observe() { this.connected = true } disconnect() { this.connected = false } },
  }
  // Link the real helper, not a no-op stub, to this SFC's fake hooks and DOM.
  // A direct module import would register hooks outside a mounted instance.
  if (path === 'panels/LogsPanel.vue') {
    const helper = read('logFollow.js').replace(/^import[\s\S]*?from ['"]vue['"];?\r?\n/gm, '')
      .replace(/^export /gm, '')
    deps.useLogFollow = new Function(...Object.keys(deps), helper + '\nreturn useLogFollow')(...Object.values(deps))
  }
  const panel = scope.run(() => setup(path, deps, props))
  t.after(() => {
    hooks.unmounted.forEach(fn => fn()); scope.stop()
    assert.equal(timers.size, 0)
    assert.equal(window.listenerCount(), 0); assert.equal(document.listenerCount(), 0)
    assert.ok(observers.every(observer => !observer.connected))
  })
  return { w, panel, props, hooks, timers, observers, requests, eventTarget, window, document,
    mount: () => hooks.mounted.forEach(fn => fn()) }
}
test('health never polls; visible trend alone polls, playback stops on hide and never resumes automatically', async t => {
  const h = lifecycleFixture(t, 'panels/TelemetryPanel.vue')
  h.w.play.loaded = true; h.w.play.n = 5
  h.mount()
  assert.equal(h.panel.tab.value,'health'); assert.equal(h.timers.size,0); assert.equal(h.requests.length,0)
  await h.panel.togglePlay(); assert.equal(h.w.play.on,false)
  h.panel.tab.value = 'trend'; await nextTick()
  assert.deepEqual([...h.timers.values()].map(timer => timer.ms),[5000]); assert.equal(h.requests.length,1)
  assert.equal(h.requests[0].url,'/api/trend')
  h.props.active = false; await nextTick()
  assert.equal(h.timers.size, 0); assert.equal(h.w.play.on, false)
  h.props.active = true; await nextTick()
  assert.deepEqual([...h.timers.values()].map(timer => timer.ms), [5000])
  assert.equal(h.w.play.on, false)
  h.panel.tab.value = 'playback'; await nextTick()
  assert.equal(h.timers.size,0)
  await h.panel.togglePlay()
  assert.deepEqual([...h.timers.values()].map(timer => timer.ms),[50])
  h.props.active = false; await nextTick()
  assert.equal(h.timers.size,0); assert.equal(h.w.play.on,false)
  h.props.active = true; await nextTick()
  assert.equal(h.timers.size,0); assert.equal(h.w.play.on,false)
  await h.panel.togglePlay()
  h.hooks.deactivated.forEach(fn => fn())
  assert.equal(h.timers.size, 0); assert.equal(h.w.play.on, false)
  h.hooks.activated.forEach(fn => fn())
  assert.equal(h.timers.size,0)
  assert.equal(h.w.play.on, false)
  h.panel.tab.value = 'trend'; await nextTick()
  assert.deepEqual([...h.timers.values()].map(timer => timer.ms),[5000])
  const count = h.requests.length
  h.panel.tab.value = 'io'; await nextTick()
  assert.equal(h.timers.size,0); assert.equal(h.requests.length,count)
})
test('leaving telemetry while playback data loads cannot start a late hidden playback', async t => {
  const h = lifecycleFixture(t, 'panels/TelemetryPanel.vue')
  let finish
  h.w.playLoad = () => new Promise(resolve => { finish = () => { h.w.play.n = 5; resolve() } })
  h.mount(); h.panel.tab.value = 'playback'; await nextTick()
  const pending = h.panel.togglePlay()
  h.props.active = false; await nextTick()
  finish(); await pending
  assert.equal(h.timers.size, 0); assert.equal(h.w.play.on, false)
})
test('logs measure after visibility/activation/resize, never keep a hidden zero height', async t => {
  const h = lifecycleFixture(t, 'panels/LogsPanel.vue')
  h.panel.box.value = { ...h.eventTarget(), clientHeight: 320, scrollHeight: 2400, scrollTop: 0 }
  h.mount(); await nextTick()
  assert.equal(h.panel.view.value, 320)
  h.props.active = false; await nextTick()
  h.panel.box.value.clientHeight = 0; h.panel.measure()
  assert.equal(h.panel.view.value, 320)
  assert.equal(h.observers.some(observer => observer.connected), false)
  h.panel.box.value.clientHeight = 480
  h.props.active = true; await nextTick(); await nextTick()
  assert.equal(h.panel.view.value, 480)
  h.panel.box.value.clientHeight = 600
  h.observers.at(-1).fn()
  assert.equal(h.panel.view.value, 600)
  h.hooks.deactivated.forEach(fn => fn())
  assert.equal(h.observers.some(observer => observer.connected), false)
  h.panel.box.value.clientHeight = 240
  h.hooks.activated.forEach(fn => fn()); await nextTick()
  assert.equal(h.panel.view.value, 240)
})
test('logs fixture runs real follow behavior and clears its visibility timer/listeners', async t => {
  const h = lifecycleFixture(t, 'panels/LogsPanel.vue')
  const box = { ...h.eventTarget(), clientHeight: 320, scrollHeight: 2400, scrollTop: 0 }
  h.panel.box.value = box
  h.mount(); await nextTick(); await nextTick()
  assert.equal(box.scrollTop, 2080)
  assert.equal(h.panel.following.value, true)
  box.dispatchEvent({ type: 'wheel', deltaY: -120 }); box.scrollTop = 96
  assert.equal(h.panel.following.value, false)
  assert.deepEqual([...h.timers.values()].map(timer => timer.ms), [10000])
  h.document.hidden = true; h.document.dispatchEvent({ type: 'visibilitychange' })
  assert.equal(h.timers.size, 0)
  h.document.hidden = false; h.document.dispatchEvent({ type: 'visibilitychange' }); await nextTick()
  assert.equal(box.scrollTop, 96)
  h.panel.goLatest(); await nextTick()
  assert.equal(box.scrollTop, 2080); assert.equal(h.panel.following.value, true)
  h.hooks.unmounted.forEach(fn => fn())
  assert.equal(box.listenerCount(), 0)
})
for (const path of ['App.vue', 'components/NavRail.vue', 'panels/WorkPanel.vue', 'panels/SystemPanel.vue',
  'panels/TelemetryPanel.vue', 'panels/LogsPanel.vue']) {
  test(`${path}: script/template/scoped styles compile`, () => {
    const { descriptor, errors } = parse(read(path), { filename: path })
    assert.deepEqual(errors, [])
    const script = compileScript(descriptor, { id: path })
    const template = compileTemplate({ source: descriptor.template.content, filename: path, id: path,
      compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(template.errors, [])
    for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, id: path, scoped: style.scoped }).errors, [])
  })
}


test('unified control maps legacy overview/drive routes to meaningful single-level tabs',()=>{
 for(const [hash,tab] of [['#/sim','run'],['#/power','connection'],['#/drive','connection'],['#/control/kinematics','kinematics']]){
  const w=world(),h=routing(w,hash);h.readHash();h.writeHash()
  assert.equal(w.view,'control');assert.equal(w.controlTab,tab);assert.equal(h.location.hash,'#/control/'+tab)
 }
})
