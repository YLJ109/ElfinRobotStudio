import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref } from 'vue'
import { createPinia } from 'pinia'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { useWorld } from '../src/store.js'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
function action(world, scene) {
  const code = source.slice(source.indexOf('const clearingScene ='), source.indexOf('// 只有 Elfin 5'))
  return new Function('ref', 'w', 'scene', code + '\nreturn { clearScene, clearingScene }')(ref, world, scene)
}

test('scene-clear button compiles immediately to the right of clear-trail, with no confirmation', () => {
  const d = parse(source).descriptor
  const script = compileScript(d, { id: 'app-scene-clear' })
  assert.deepEqual(compileTemplate({ source: d.template.content, id: 'app-scene-clear',
    compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  for (const style of d.styles) assert.deepEqual(compileStyle({ source: style.content,
    id: 'app-scene-clear', scoped: style.scoped }).errors, [])
  assert.match(d.template.content, /清除轨迹<\/button>\s*<button[^>]*@click="clearScene">清空场景<\/button>/)
  const code = source.slice(source.indexOf('async function clearScene'), source.indexOf('// 只有 Elfin 5'))
  assert.doesNotMatch(code, /confirm|resetView|setView|setFence|openCamera|closeCamera|clearTrail/)
})

test('successful clear reuses one API request, removes only scene visuals and does not reset view', async () => {
  let finish, calls = 0
  const events = []
  const world = { clearObjects: () => { calls++; return new Promise(resolve => { finish = resolve }) } }
  const scene = Object.fromEntries(['cancelPlacement', 'setObjects', 'setGamePhysical', 'setGameState']
    .map(name => [name, (...args) => events.push([name, ...args])]))
  const api = action(world, scene)
  const first = api.clearScene()
  await api.clearScene()
  assert.equal(calls, 1); assert.deepEqual(events, [])
  finish({ ok: true }); await first
  assert.deepEqual(events, [['cancelPlacement'], ['setObjects', [], null], ['setGamePhysical', []], ['setGameState', null]])
  assert.equal(api.clearingScene.value, false)
})

test('busy rejection or transport failure preserves every visible object', async () => {
  const events = []
  const scene = { cancelPlacement: () => events.push('changed'), setObjects: () => events.push('changed') }
  const world = { clearObjects: async () => ({ ok: false, why: '请先停止' }) }
  const api = action(world, scene)
  await api.clearScene()
  assert.deepEqual(events, [])
  world.clearObjects = async () => { throw new Error('请求中断') }
  await api.clearScene()
  assert.deepEqual(events, []); assert.equal(world.error, '请求中断')
  assert.equal(api.clearingScene.value, false)
})

test('all scene clear entrances share the authoritative store revision and preserve robot and camera state', async t => {
  const previous = globalThis.fetch, calls = []
  t.after(() => { globalThis.fetch = previous })
  const w = useWorld(createPinia())
  w.selObj = 'selected'; w.activeScene = 'saved-scene'
  w.snap = { mode: 'sim', session_id: 'world-session', joints: { q: [1, 2, 3, 4, 5, 6] }, settings: { fence: { enabled: true } } }
  w.cameras = { active: { session: 'camera-session' } }
  const pose = JSON.stringify(w.snap), camera = JSON.stringify(w.cameras)
  globalThis.fetch = async (url, init) => {
    calls.push([url, init.method, JSON.parse(init.body)])
    return { ok: true, json: async () => ({ ok: true, removed: 8 }) }
  }
  const response = await w.clearObjects()
  assert.equal(response.ok, true)
  assert.deepEqual(calls, [['/api/objects/clear', 'POST', {}]])
  assert.equal(w.sceneClearRevision, 1); assert.equal(w.selObj, null); assert.equal(w.activeScene, '')
  assert.equal(JSON.stringify(w.snap), pose); assert.equal(JSON.stringify(w.cameras), camera)
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: false, why: '场景忙' }) })
  w.selObj = 'keep'; w.activeScene = 'keep-scene'
  assert.equal((await w.clearObjects()).ok, false)
  assert.equal(w.sceneClearRevision, 1); assert.equal(w.selObj, 'keep'); assert.equal(w.activeScene, 'keep-scene')
  assert.equal(w.error, '场景忙')
})
