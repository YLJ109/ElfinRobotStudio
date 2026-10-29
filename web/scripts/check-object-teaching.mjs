// 离线检查：真实 Vue 组件，场景/保存为替身，不访问服务或设备。
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { createObjectTeaching, objectTeachingContext, validObjectPosition } from '../src/objectTeaching.js'
import * as objectCatalog from '../src/objectCatalog.js'

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
function fakeScene() {
  const calls = []
  return { calls, callbacks: null,
    setObjectTarget(id, callbacks) { calls.push(['attach', id]); this.callbacks = callbacks || null; return true },
    finishObjectMove(xyz) { calls.push(['finish', xyz]) },
    cancelObjectMove() { calls.push(['cancel']); this.callbacks?.cancel() },
  }
}
function fixture() {
  const scene = fakeScene(), state = {}, requests = [], reply = deferred()
  let enabled = true
  const editor = createObjectTeaching({ state, allowed: () => enabled,
    save: (id, patch) => { requests.push({ id, patch }); return reply.promise } })
  editor.attach(scene, 'box', 'session')
  return { scene, state, editor, requests, reply, disable() { enabled = false } }
}
const ack = xyz => ({ ok: true, object: { id: 'box', xyz } })

test('物体示教只在未连接真机的仿真会话可用', () => {
  const w = { mode: 'sim', connected: true, snap: { mode: 'sim', session_id: 's', real: null } }
  assert.equal(objectTeachingContext(w), 's')
  for (const patch of [{ connected: false }, { mode: 'real' }, { snap: { ...w.snap, real: {} } },
    { snap: { ...w.snap, mode: 'real' } }, { snap: { ...w.snap, session_id: '' } }]) {
    assert.equal(objectTeachingContext({ ...w, ...patch }), '')
  }
})
test('物体 XYZ 有限值与后端边界一致', () => {
  assert.equal(validObjectPosition([1e6, -1e6, 0]), true)
  for (const xyz of [[1, 2], [0, NaN, 0], [0, Infinity, 0], [0, '1', 0], [0, true, 0], [0, 1e6 + 1, 0]]) {
    assert.equal(validObjectPosition(xyz), false)
  }
})
test('拖动百次只本地更新，松开单次保存且采用实际回执坐标', async () => {
  const h = fixture(), cb = h.scene.callbacks
  cb.start([0, 0, 0])
  for (let i = 0; i < 100; i++) cb.change([i, -100, 80])
  assert.deepEqual(h.state.xyz, [99, -100, 80]); assert.equal(h.requests.length, 0)
  const pending = cb.commit([99, -100, 80])
  await cb.commit([100, -100, 80])
  assert.deepEqual(h.requests, [{ id: 'box', patch: { xyz: [99, -100, 80] } }])
  assert.equal(h.state.busy, true)
  h.reply.resolve(ack([99, -100, 81])); await pending
  assert.deepEqual(h.scene.calls.at(-1), ['finish', [99, -100, 81]])
  assert.equal(h.state.busy, false); assert.equal(h.state.xyz, null)
})
for (const reason of ['拒绝', '网络', '错误物体', '无效坐标']) {
  test(`保存${reason}会回滚并给出错误`, async () => {
    const h = fixture(), pending = h.scene.callbacks.commit([1, 2, 3])
    if (reason === '网络') h.reply.reject(new Error('网络失败'))
    else if (reason === '错误物体') h.reply.resolve({ ok: true, object: { id: 'other', xyz: [1, 2, 3] } })
    else if (reason === '无效坐标') h.reply.resolve(ack([1, 2, NaN]))
    else h.reply.resolve({ ok: false, why: '拒绝更新' })
    await pending
    assert.deepEqual(h.scene.calls.at(-1), ['cancel'])
    assert.ok(h.state.error); assert.equal(h.state.xyz, null); assert.equal(h.state.busy, false)
  })
}
test('越界目标不会发请求', async () => {
  const h = fixture(); await h.scene.callbacks.commit([1e7, 0, 0])
  assert.equal(h.requests.length, 0); assert.equal(h.state.error, '坐标超出范围')
})
test('切物体/会话后旧回执和旧拖动回调不能影响新目标', async () => {
  const h = fixture(), old = h.scene.callbacks, pending = old.commit([1, 2, 3])
  h.editor.attach(h.scene, 'sphere', 'new-session')
  const count = h.scene.calls.length
  h.reply.resolve(ack([1, 2, 3])); await pending
  old.start([3, 4, 5]); old.change([5, 6, 7]); await old.commit([5, 6, 7])
  assert.equal(h.scene.calls.length, count); assert.equal(h.state.xyz, null)
  assert.equal(h.requests.length, 1)
})
test('上下文失效后不发送请求；保存期间不能取消并启动第二次保存', async () => {
  const h = fixture(), cb = h.scene.callbacks
  const pending = cb.commit([1, 2, 3]); h.editor.cancel()
  assert.equal(h.scene.calls.filter(c => c[0] === 'cancel').length, 0)
  h.disable(); await cb.commit([5, 6, 7])
  h.reply.resolve(ack([1, 2, 3])); await pending
  assert.equal(h.requests.length, 1)
  assert.equal(h.scene.calls.filter(c => c[0] === 'finish').length, 0)
  h.editor.detach(); assert.equal(h.state.busy, false)
})

const read = path => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
async function settle() { for (let i = 0; i < 4; i++) await Vue.nextTick() }
function component(t) {
  const object = id => ({ id, name: id, kind: 'box', xyz: [1, 2, 3], size: [100, 100, 100] })
  const calls = [], scene = fakeScene(), listeners = new Map(), mounted = [], unmounted = []
  const w = Vue.reactive({ connected: true, mode: 'sim', view: 'objects', selObj: 'box',
    snap: { mode: 'sim', session_id: 's', objects: [object('box'), object('sphere')] },
    async patchObject(id, patch) { calls.push([id, patch]); return { ok: true, object: { id, xyz: patch.xyz } } },
    async removeObject(id) { calls.push(['remove', id]); return { ok: true } },
  })
  const window = { __elfinScene: scene,
    addEventListener(key, fn) { listeners.set(key, fn) }, removeEventListener(key) { listeners.delete(key) } }
  const deps = { ...Vue, ...objectCatalog, createObjectTeaching, objectTeachingContext, useWorld: () => w,
    storeToRefs: w => Object.fromEntries(Object.keys(w).map(k => [k, Vue.toRef(w, k)])), window,
    onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn) }
  const { descriptor } = parse(read('components/ObjectProps.vue'))
  const script = compileScript(descriptor, { id: 'object-props' })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'ObjectProps.vue', id: 'object-props',
    compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  const code = script.content.replace(/^import[^\n]*\n/gm, '').replace('export default', 'return')
  const keys = Object.keys(deps).filter(k => k !== 'default' && /^[\w$]+$/.test(k))
  const scope = Vue.effectScope()
  const panel = scope.run(() => new Function(...keys, code)(...keys.map(k => deps[k])).setup({}, { expose() {} }))
  mounted.forEach(fn => fn())
  t.after(() => { unmounted.forEach(fn => fn()); scope.stop() })
  return { w, panel, scene, calls, window, listeners, unmount: () => unmounted.forEach(fn => fn()) }
}
test('真实物体属性组件：选中自动出现坐标轴，快照更新不反复挂载或打断拖动', async t => {
  const h = component(t); await settle()
  const count = h.scene.calls.length
  const cb = h.scene.callbacks
  cb.start([1, 2, 3]); cb.change([20, 30, 40])
  for (let i = 0; i < 50; i++) {
    h.w.snap.objects = h.w.snap.objects.map(o => ({ ...o, xyz: [i, 2, 3] })); await settle()
  }
  assert.equal(h.scene.calls.length, count)
  assert.deepEqual(h.panel.xyzShown.value, [20, 30, 40]); assert.equal(h.panel.editing.value, true)
  await cb.commit([20, 30, 40])
  assert.deepEqual(h.calls, [['box', { xyz: [20, 30, 40] }]])
})
test('真实物体属性组件：切换选中和离开摆放页清理坐标轴与旧草稿', async t => {
  const h = component(t); await settle()
  h.panel.begin('xyz', 0, '90'); const old = h.scene.callbacks
  h.w.selObj = 'sphere'; await settle()
  assert.equal(h.panel.draft.xyz.length, 0)
  await old.commit([30, 40, 50]); assert.equal(h.calls.length, 0)
  assert.deepEqual(h.scene.calls.at(-1), ['attach', 'sphere'])
  h.w.view = 'work'; await settle()
  assert.deepEqual(h.scene.calls.at(-1), ['attach', null])
  assert.equal(h.panel.move.active, false)
})
test('真实物体属性组件：主动切三维移动可进入摆放，停止后不被快照重开', async t => {
  const h = component(t); await settle()
  await h.panel.toggleMove(); await settle()
  assert.equal(h.panel.move.active, false)
  h.w.snap.objects = h.w.snap.objects.map(o => ({ ...o, xyz: [4, 5, 6] })); await settle()
  assert.equal(h.panel.move.active, false)
  h.w.view = 'sim'; await settle(); await h.panel.toggleMove(); await settle()
  assert.equal(h.w.view, 'objects'); assert.equal(h.panel.move.active, true)
})
test('真实物体属性组件：模型更换重新绑定，真机模式/删除选中/卸载清理', async t => {
  const h = component(t); await settle()
  const next = fakeScene(); h.window.__elfinScene = next
  h.listeners.get('elfin-scene-ready')(); await settle()
  assert.deepEqual(h.scene.calls.at(-1), ['attach', null]); assert.deepEqual(next.calls.at(-1), ['attach', 'box'])
  h.w.mode = 'real'; await settle(); assert.equal(h.panel.move.active, false)
  h.w.mode = 'sim'; await settle(); h.w.snap.objects = []; await settle()
  assert.equal(h.panel.move.active, false)
  h.unmount(); assert.equal(h.listeners.size, 0)
})
test('属性面板位于舞台左上，沿用主题直角样式，字段拖动时禁用', () => {
  const app = read('App.vue'), props = read('components/ObjectProps.vue')
  const rule = app.match(/\.props\s*\{([^}]+)\}/)?.[1] || ''
  assert.match(rule, /left:\s*10px/); assert.match(rule, /top:\s*48px/); assert.doesNotMatch(rule, /right:/)
  assert.match(rule, /max-width:/); assert.match(props, /<fieldset[^>]+:disabled="editing"/)
  assert.doesNotMatch(props, /border-radius:\s*[1-9]/)
})
test('模型先就绪但物体尚未加入时，成员就绪重试一次，普通成员事件不打断已绑定手势', async t => {
  const h=component(t);await settle()
  h.window.__elfinScene=undefined;h.listeners.get('elfin-scene-ready')();await settle()
  assert.equal(h.panel.move.active,false)
  let meshReady=false
  const scene=fakeScene(),originalAttach=scene.setObjectTarget.bind(scene)
  scene.setObjectTarget=(id,callbacks)=>id && !meshReady?false:originalAttach(id,callbacks)
  h.window.__elfinScene=scene;h.listeners.get('elfin-scene-ready')();await settle()
  assert.equal(h.panel.move.active,false)
  meshReady=true;h.listeners.get('elfin-objects-ready')();await settle()
  assert.equal(h.panel.move.active,true)
  const calls=scene.calls.length
  scene.callbacks.start([1,2,3])
  for(let i=0;i<20;i++){h.listeners.get('elfin-objects-ready')();await settle()}
  assert.equal(scene.calls.length,calls)
  assert.equal(h.panel.move.dragging,true)
})
test('物体属性材质与颜色显式保存，移动期间不提交属性更改', async t => {
  const h=component(t);await settle()
  await h.panel.put({surface:'steel',color:'#112233'})
  assert.deepEqual(h.calls,[['box',{surface:'steel',color:'#112233'}]])
  h.scene.callbacks.start([1,2,3])
  await h.panel.put({surface:'wood'})
  assert.equal(h.calls.length,1)
})
