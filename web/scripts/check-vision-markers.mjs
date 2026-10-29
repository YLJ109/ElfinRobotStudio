// Offline: injected network/files, real Vue reactivity and SFC compilation.
// Run: node --test web/scripts/check-vision-markers.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as helpers from '../src/visionMarkers.js'

const source = readFileSync(new URL('../src/panels/VisionMarkerPanel.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor
const response = (value, status = 200) => ({ ok: status < 400, status, json: async () => value })
const detected = () => ({ ok: true, source: 'upload', dictionary: 'DICT_4X4_50', status: 'detected',
  image: { width: 40, height: 40, mime: 'image/png', base64: 'AAAA' },
  markers: [{ id: 12, corners: [[4, 4], [24, 4], [24, 24], [4, 24]], centroid: [14, 14] }] })

function fixture(t, overrides = {}) {
  const hooks = { mounted: [], unmounted: [] }, calls = []
  const deps = { ref: Vue.ref, computed: Vue.computed, onMounted: fn => hooks.mounted.push(fn),
    onBeforeUnmount: fn => hooks.unmounted.push(fn), ...helpers, AbortController,
    fetch: async (...args) => { calls.push(args); throw new Error('Unexpected network') }, ...overrides }
  const script = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const scope = Vue.effectScope()
  const panel = scope.run(() => new Function(...Object.keys(deps), script + '\nreturn {busy,error,capability,active,dictionary,stream,result,preview,message,available,cameraReady,checkCapability,refreshCamera,detect,upload}')(...Object.values(deps)))
  t.after(() => { hooks.unmounted.forEach(fn => fn()); scope.stop() })
  return { panel, hooks, calls }
}

test('client uses only fixed read-only routes and preserves status errors', async () => {
  const calls = []
  const fetcher = async (...args) => { calls.push(args); return response(detected()) }
  await helpers.visionRequest('detect', { payload: { source: 'upload' }, fetcher })
  assert.equal(calls[0][0], '/api/vision/markers/detect')
  assert.equal(calls[0][1].method, 'POST')
  assert.deepEqual(JSON.parse(calls[0][1].body), { source: 'upload' })
  await assert.rejects(helpers.visionRequest('http://elsewhere', { fetcher }), /无效/)
  assert.equal(calls.length, 1)
  await assert.rejects(helpers.visionRequest('capabilities', { fetcher: async () => response({ ok: false, status: 'capability_unavailable', why: 'missing aruco' }, 503) }), err => err.code === 'capability_unavailable')
  await assert.rejects(helpers.visionRequest('capabilities', { fetcher: async () => ({ ok: true, json: async () => { throw new Error() } }) }), /JSON/)
})

test('files, previews and camera payloads enforce input boundaries', () => {
  for (const file of [null, { type: 'image/svg+xml', size: 10 }, { type: 'image/png', size: 0 }, { type: 'image/png', size: 2 ** 22 }]) {
    assert.throws(() => helpers.validateMarkerFile(file))
  }
  assert.doesNotThrow(() => helpers.validateMarkerFile({ type: 'image/jpeg', size: 2 ** 20 }))
  assert.throws(() => helpers.validateMarkerFile({ type: 'image/jpeg', size: 2 ** 20 + 1 }))
  assert.throws(() => helpers.cameraMarkerPayload(null, 'color', 'DICT_4X4_50'))
  const active = { state: 'streaming', session: 'existing', streams: ['color', 'depth'] }
  assert.deepEqual(helpers.cameraMarkerPayload(active, 'color', 'DICT_4X4_50'), { source: 'camera', session: 'existing', stream: 'color', dictionary: 'DICT_4X4_50' })
  assert.throws(() => helpers.cameraMarkerPayload(active, 'depth', 'DICT_4X4_50'))
  assert.throws(() => helpers.cameraMarkerPayload({ ...active, state: 'starting' }, 'color'))
  assert.equal(helpers.markerPoints([[4, 4], [24, 4]]), '4,4 24,4')
  assert.equal(helpers.markerPreview(detected()), 'data:image/png;base64,AAAA')
  for (const patch of [{ width: 2049 }, { width: 1500, height: 1500 }, { mime: 'image/svg+xml' }, { base64: 'https://bad' }]) {
    const value = detected(); Object.assign(value.image, patch)
    assert.throws(() => helpers.markerPreview(value))
  }
})

test('file reading returns base64, reports errors and supports cancellation', async t => {
  const previous = globalThis.FileReader
  const readers = []
  globalThis.FileReader = class {
    constructor() { readers.push(this) }
    readAsDataURL(file) { this.file = file }
    abort() { this.onabort?.() }
  }
  t.after(() => { if (previous === undefined) delete globalThis.FileReader; else globalThis.FileReader = previous })
  const file = { type: 'image/png', size: 10 }
  const success = helpers.readMarkerFile(file)
  readers[0].result = 'data:image/png;base64,AAAA'; readers[0].onload()
  assert.equal(await success, 'AAAA')
  const failed = helpers.readMarkerFile(file)
  readers[1].onerror(); await assert.rejects(failed, /读取失败/)
  const controller = new AbortController()
  const cancelled = helpers.readMarkerFile(file, controller.signal)
  controller.abort(); await assert.rejects(cancelled, err => err.name === 'AbortError')
  await assert.rejects(helpers.readMarkerFile(file, controller.signal), err => err.name === 'AbortError')
})

test('mount checks capability only, never camera or robot', async t => {
  const paths = []
  const { panel, hooks, calls } = fixture(t, { visionRequest: async path => { paths.push(path); return { ok: true, available: true, dictionaries: helpers.MARKER_DICTIONARIES } } })
  assert.equal(panel.result.value, null)
  assert.equal(panel.cameraReady.value, false)
  await hooks.mounted[0]()
  assert.deepEqual(paths, ['capabilities'])
  assert.equal(calls.length, 0)
  assert.equal(panel.available.value, true)
})

test('loading prevents duplicate requests; detection shows aligned result and empty state', async t => {
  let finish, calls = 0
  const { panel } = fixture(t, { visionRequest: () => { calls++; return new Promise(resolve => { finish = resolve }) } })
  const task = panel.detect(async () => ({ source: 'upload' }))
  await Promise.resolve()
  assert.equal(panel.busy.value, true)
  await panel.detect(async () => { throw new Error('Duplicate') })
  assert.equal(calls, 1)
  finish(detected()); await task
  assert.equal(panel.busy.value, false)
  assert.equal(panel.result.value.markers[0].id, 12)
  assert.equal(panel.preview.value, 'data:image/png;base64,AAAA')
  const emptyTask = panel.detect(() => ({ source: 'upload' }))
  await Promise.resolve()
  assert.equal(panel.result.value, null)
  const empty = detected(); empty.markers = []; empty.status = 'empty'
  finish(empty); await emptyTask
  assert.match(panel.message.value, /未检测到/)
})

test('missing capability and detection errors clear previous results and permit retry', async t => {
  const failure = Object.assign(new Error('缺少 aruco'), { code: 'capability_unavailable' })
  const { panel } = fixture(t, { visionRequest: async () => { throw failure } })
  panel.result.value = detected(); panel.preview.value = 'old'
  await panel.detect(() => ({ source: 'upload' }))
  assert.equal(panel.result.value, null); assert.equal(panel.preview.value, '')
  assert.equal(panel.error.value, '缺少 aruco'); assert.equal(panel.available.value, false)
  assert.equal(panel.busy.value, false)
})

test('camera refresh is explicit GET and failures discard old session', async t => {
  let fail = false
  const calls = []
  const { panel } = fixture(t, { fetch: async (...args) => {
    calls.push(args)
    return response({ active: { state: 'streaming', session: 'existing', streams: ['color'] } }, fail ? 500 : 200)
  } })
  assert.equal(calls.length, 0)
  await panel.refreshCamera(); assert.equal(panel.cameraReady.value, true)
  assert.equal(calls[0][0], '/api/cameras'); assert.equal(calls[0][1].method, undefined)
  fail = true
  await panel.refreshCamera(); assert.equal(panel.active.value, null)
})

test('upload reads only the selected file and resets the input', async t => {
  const file = { type: 'image/png', size: 16 }
  const reads = [], payloads = []
  const { panel } = fixture(t, { readMarkerFile: async selected => { reads.push(selected); return 'AAAA' },
    visionRequest: async (path, options) => { payloads.push(options.payload); return detected() } })
  const event = { target: { files: [file], value: 'chosen.png' } }
  await panel.upload(event)
  assert.equal(event.target.value, '')
  assert.deepEqual(reads, [file])
  assert.deepEqual(payloads, [{ source: 'upload', dictionary: 'DICT_4X4_50', image_base64: 'AAAA' }])
})

test('unmount aborts the request and suppresses late detection result', async t => {
  let finish, signal
  const { panel, hooks } = fixture(t, { visionRequest: (path, options) => { signal = options.signal; return new Promise(resolve => { finish = resolve }) } })
  const task = panel.detect(() => ({ source: 'upload' }))
  await Promise.resolve(); hooks.unmounted.forEach(fn => fn())
  assert.equal(signal.aborted, true)
  finish(detected()); await task
  assert.equal(panel.result.value, null)
})

test('SFC script, template and style compile; initial UI renders without service access', async () => {
  const script = compileScript(descriptor, { id: 'vision-marker-test' })
  const template = compileTemplate({ source: descriptor.template.content, filename: 'VisionMarkerPanel.vue', id: 'vision-marker-test', compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  assert.deepEqual(compileStyle({ source: descriptor.styles[0].content, id: 'vision-marker-test', scoped: true }).errors, [])
  const executable = template.code.replace(/^import (.+) from "vue"$/gm, (_, imports) => `const ${imports.replace(/ as /g, ': ')} = Vue`).replace('export function render', 'function render')
  const render = new Function('Vue', executable + '\nreturn render')(Vue)
  const html = await renderToString(Vue.createSSRApp({ setup: () => ({ busy: false, error: '', capability: null, dictionary: helpers.MARKER_DICTIONARIES[0], dictionaries: helpers.MARKER_DICTIONARIES,
    stream: 'color', available: false, cameraReady: false, result: null, message: '上传小图', fileInput: null }), render }))
  assert.match(html, /ArUco 视觉标记/)
  assert.match(html, /不能作为机器人坐标/)
  assert.match(html, /上传图片检测/)
  const state = { busy: false, error: '', capability: { available: true }, dictionary: helpers.MARKER_DICTIONARIES[0], dictionaries: helpers.MARKER_DICTIONARIES,
    stream: 'color', available: true, cameraReady: false, result: detected(), preview: helpers.markerPreview(detected()),
    message: '检测到 1 个标记', fileInput: null, markerPoints: helpers.markerPoints }
  const annotated = await renderToString(Vue.createSSRApp({ setup: () => state, render }))
  assert.match(annotated, /viewBox="0 0 40 40"/)
  assert.match(annotated, /points="4,4 24,4 24,24 4,24"/)
  assert.match(annotated, /ID 12/)
})
