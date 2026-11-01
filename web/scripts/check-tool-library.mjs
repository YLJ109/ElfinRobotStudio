// Completely offline: real meshes/GLTFLoader + real compiled Vue setup, no HTTP,
// no controller, no running service or browser. Run: node --test this-file.mjs
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { computed, effectScope, nextTick, reactive, ref, watch } from 'vue'
import * as THREE from 'three'
import { BUILTIN_TOOLS, ROBOT_MODELS, DEFAULT_VACUUM, TOOL_LIBRARY_ASSETS,
  vacuumDimensions, validateToolCatalog, loadToolCatalog, libraryToolEntry } from '../src/visualAssets.js'
import { disposeAsset, inspectLibraryGlb, readLibraryTool, applyToolSkin, TOOL_FINISHES, buildVacuumReference } from '../src/toolVisual.js'

const toolsRoot = new URL('../public/tools/', import.meta.url)
const read = (path) => readFileSync(new URL(path, toolsRoot))
const catalog = JSON.parse(read('catalog.json'))
const source = readFileSync(new URL('../src/components/ModelLibrary.vue', import.meta.url), 'utf8')
const arrayBuffer = (data) => data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)
const sha = (data) => createHash('sha256').update(data).digest('hex')

async function offlineFetch(fn, transform = (path, bytes) => bytes) {
  const previous = globalThis.fetch
  const requests = []
  globalThis.fetch = async (path, options) => {
    assert.equal(typeof path, 'string')
    assert.ok(path === '/tools/catalog.json' || Object.values(TOOL_LIBRARY_ASSETS).includes(path), `No external URL allowed: ${path}`)
    assert.equal(options?.redirect, 'error')
    requests.push(path)
    const bytes = transform(path, read(path.slice('/tools/'.length)))
    return new Response(bytes, { status: 200, headers: { 'content-length': bytes.length } })
  }
  try { return await fn(requests) } finally { globalThis.fetch = previous }
}

function syntheticGlb(document) {
  const text = Buffer.from(JSON.stringify(document))
  const padded = Buffer.concat([text, Buffer.alloc((-text.length) & 3, 32)])
  const bytes = Buffer.alloc(28 + padded.length)
  bytes.writeUInt32LE(0x46546c67, 0); bytes.writeUInt32LE(2, 4); bytes.writeUInt32LE(bytes.length, 8)
  bytes.writeUInt32LE(padded.length, 12); bytes.writeUInt32LE(0x4e4f534a, 16)
  padded.copy(bytes, 20)
  bytes.writeUInt32LE(0, 20 + padded.length); bytes.writeUInt32LE(0x004e4942, 24 + padded.length)
  return arrayBuffer(bytes)
}

test('catalog contains six distinct real separately assembled open-source end tools', () => {
  const valid = validateToolCatalog(catalog)
  assert.deepEqual(valid.tools.map((entry) => entry.id), ['robotiq-2f85', 'robotiq-2f140', 'robotiq-3f', 'franka-panda-hand', 'schunk-sdh', 'schunk-wsg50'])
  assert.deepEqual(BUILTIN_TOOLS.map((entry) => entry.id), ['vacuum-reference', 'none'])
  assert.ok(Object.isFrozen(valid.tools))
  for (const entry of valid.tools) {
    assert.equal(entry.asset, TOOL_LIBRARY_ASSETS[entry.id])
    assert.equal(entry.pose, 'articulated-simulation')
    assert.equal(entry.calibrated, false)
    assert.equal(entry.units, 'm')
    assert.match(entry.license, /^(BSD-(2|3)-Clause|Apache-2.0)$/)
  }
})

test('byte-identical originals, pinned URLs, full license and hashes are locally retained', () => {
  const manifest = JSON.parse(read('source/robotiq/manifest.json'))
  assert.equal(manifest.commit, '45196f6558fe8ba9d89bc8a105396c68c3e7e892')
  assert.ok(manifest.files.length >= 30)
  for (const file of manifest.files) {
    const bytes = read('source/robotiq/' + file.path)
    assert.equal(sha(bytes), file.sha256, file.path)
    assert.equal(bytes.length, file.bytes)
    assert.equal(file.url, `https://raw.githubusercontent.com/ros-industrial-attic/robotiq/${manifest.commit}/${file.path}`)
  }
  assert.match(read('source/robotiq/LICENSE').toString(), /Copyright \(c\) 2013, ROS-Industrial/)
  assert.match(read('source/robotiq/LICENSE').toString(), /Redistribution and use in source and binary forms/)
})

for (const tool of catalog.tools) {
  test(`${tool.id}: embedded GLB uses source geometry, flange datum, metre units and no external sidecars`, () => {
    const bytes = read(tool.asset.slice('/tools/'.length))
    assert.equal(sha(bytes), tool.sha256)
    const document = inspectLibraryGlb(arrayBuffer(bytes))
    assert.equal(document.asset.extras.commit, tool.commit)
    assert.equal(document.nodes[0].extras.mount, 'flange')
    assert.equal(document.nodes[0].extras.forward, '+Z')
    const minimumParts = { 'robotiq-2f85': 11, 'robotiq-2f140': 11, 'robotiq-3f': 13,
      'franka-panda-hand': 9, 'schunk-sdh': 10, 'schunk-wsg50': 3 }[tool.id]
    assert.equal(document.meshes.length, minimumParts)
    const triangleCount = document.meshes.reduce((sum, mesh) => sum + mesh.primitives.reduce((n, primitive) =>
      n + document.accessors[primitive.indices].count / 3, 0), 0)
    assert.ok(triangleCount > 1000, 'Actual source meshes, not a box placeholder')
    for (const node of document.nodes.filter((node) => node.mesh !== undefined)) {
      assert.ok(node.extras.original_mesh || node.name.endsWith('inner_finger_pad'))
    }
    assert.equal(read(`${tool.id}.png`).subarray(0, 8).toString('hex'), '89504e470d0a1a0a')
  })
  test(`${tool.id}: actual GLTFLoader creates a disposable scene with canonical TCP and verified bounds`, async () => {
    await offlineFetch(async (requests) => {
      const root = await readLibraryTool({ kind: 'library', id: tool.id, tcp_mm: [999, 999, 999], bounds_mm: null })
      const bounds = new THREE.Box3().setFromObject(root)
      assert.deepEqual(root.userData.tcp_mm, tool.tcp_mm)
      assert.deepEqual(root.userData.bounds_mm, tool.bounds_mm)
      assert.equal(root.userData.calibrated, false)
      assert.deepEqual(root.position.toArray(), [0, 0, 0])
      assert.deepEqual(root.scale.toArray(), [1, 1, 1])
      for (let i = 0; i < 3; i++) {
        assert.ok(Math.abs(bounds.min.getComponent(i) * 1000 - tool.bounds_mm.min[i]) < .001)
        assert.ok(Math.abs(bounds.max.getComponent(i) * 1000 - tool.bounds_mm.max[i]) < .001)
      }
      let geometries = 0, disposed = 0
      root.traverse((node) => { if (node.geometry) { geometries++; node.geometry.addEventListener('dispose', () => disposed++) } })
      assert.ok(geometries >= 3)
      disposeAsset(root)
      assert.equal(disposed, geometries)
      assert.ok(requests.includes(tool.asset))
    })
  })
}

test('2F TCP is the contact-pad centre instead of a guessed flange/tip position', () => {
  assert.deepEqual(catalog.tools[0].tcp_mm.map((v) => v || 0), [0, 0, 130.324])
  assert.deepEqual(catalog.tools[1].tcp_mm, [0, 0, 176.963104])
  for (const entry of catalog.tools.slice(0, 2)) assert.match(entry.tcp_reference, /two URDF contact-pad centres/)
})

for (const [name, mutate] of [
  ['schema', (v) => { v.schema_version = 2 }],
  ['remote asset', (v) => { v.tools[0].asset = 'https://example.com/model.glb' }],
  ['relative traversal', (v) => { v.tools[0].asset = '/tools/../model.glb' }],
  ['unknown id', (v) => { v.tools[0].id = '__proto__' }],
  ['duplicate id', (v) => { v.tools.push(v.tools[0]) }],
  ['invalid bounds', (v) => { v.tools[0].bounds_mm.min[0] = Infinity }],
  ['inverted bounds', (v) => { v.tools[0].bounds_mm.min = [...v.tools[0].bounds_mm.max] }],
  ['invalid rotation', (v) => { v.tools[0].default_rotation_deg[0] = 900 }],
  ['bad license', (v) => { v.tools[0].license = 'unknown' }],
  ['unverified origin', (v) => { v.tools[0].source = 'https://example.com/' }],
]) test(`catalog rejects ${name}`, () => {
  const value = structuredClone(catalog)
  mutate(value)
  assert.throws(() => validateToolCatalog(value), /目录/)
})

test('loader rejects arbitrary asset URLs and unknown ids before any download', async () => {
  await offlineFetch(async (requests) => {
    await assert.rejects(readLibraryTool({ kind: 'library', id: '../bad' }), /未授权/)
    await assert.rejects(readLibraryTool({ kind: 'library', id: 'robotiq-2f85', asset: 'https://example.com/tool.glb' }), /未授权/)
    assert.deepEqual(requests, [])
  })
})

test('catalog lookup ignores caller TCP/envelope, and simultaneous consumers share local metadata', async () => {
  await offlineFetch(async () => {
    const [a, b] = await Promise.all([loadToolCatalog(), loadToolCatalog()])
    assert.equal(a, b)
    const entry = await libraryToolEntry({ id: 'robotiq-2f85', tcp_mm: [900, 900, 900] })
    assert.equal(entry, a.tools[0])
    assert.notDeepEqual(entry.tcp_mm, [900, 900, 900])
  })
})

test('corrupted GLB fails integrity validation before rendering', async () => {
  await offlineFetch(async () => {
    await assert.rejects(readLibraryTool({ id: 'robotiq-3f' }), /校验失败/)
  }, (path, bytes) => {
    if (!path.endsWith('.glb')) return bytes
    const altered = Buffer.from(bytes); altered[altered.length - 5] ^= 1
    return altered
  })
})

for (const [name, extra] of [
  ['external buffer', { buffers: [{ uri: 'https://example.com/mesh.bin', byteLength: 8 }] }],
  ['external image', { images: [{ uri: 'https://example.com/texture.png' }] }],
  ['additional decoder', { extensionsRequired: ['KHR_draco_mesh_compression'] }],
]) test(`GLB preflight rejects ${name}`, () => {
  assert.throws(() => inspectLibraryGlb(syntheticGlb({ asset: { version: '2.0' }, buffers: [{ byteLength: 0 }], ...extra })), /自包含/)
})

test('empty, malformed and oversized GLB buffers are rejected', () => {
  assert.throws(() => inspectLibraryGlb(new ArrayBuffer(0)), /为空/)
  assert.throws(() => inspectLibraryGlb(new ArrayBuffer(100)), /头部/)
  assert.throws(() => inspectLibraryGlb(new ArrayBuffer(26 * 1024 * 1024)), /25 MB/)
})

function modelLibrary(props = {}) {
  const storage = new Map()
  const scope = effectScope(), cleanup = [], events = []
  const deps = { computed, ref, watch, nextTick, onMounted:()=>{}, onBeforeUnmount: (fn) => cleanup.push(fn),window:{addEventListener(){},removeEventListener(){}},
    ROBOT_MODELS, DEFAULT_VACUUM, BUILTIN_TOOLS, vacuumDimensions,
    loadToolCatalog: async () => validateToolCatalog(catalog),
    localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) } }
  const compiled = compileScript(parse(source).descriptor, { id: 'tool-library' }).content
    .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
  const component = new Function(...Object.keys(deps), compiled)(...Object.values(deps))
  const activeProps = reactive({ selected: 'elfin5', selectedToolId: 'vacuum-reference', busy: false, toolLocked: false, ...props })
  const state = scope.run(() => component.setup(activeProps, { expose() {}, emit: (...args) => events.push(args) }))
  return { state, props: activeProps, events, dispose: () => { cleanup.forEach((fn) => fn()); scope.stop() } }
}

test('model library SFC, template and theme-aware styles compile', () => {
  const descriptor = parse(source).descriptor
  compileScript(descriptor, { id: 'tool-library' })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'ModelLibrary.vue', id: 'tool-library' }).errors, [])
  assert.deepEqual(compileStyle({ source: descriptor.styles[0].content, filename: 'ModelLibrary.vue', id: 'tool-library', scoped: true }).errors, [])
  assert.doesNotMatch(source, /type="file"|跟随使用独立双吸盘|w\.(move|jog|call|toolAction)/)
})

test('dropdown selection is a draft; only the common switch button emits minimal library intent', async () => {
  const vm = modelLibrary()
  try {
    await vm.state.refreshCatalog()
    assert.equal(vm.state.options.value.length, 8)
    vm.state.choice.value = 'robotiq-2f140'
    await nextTick()
    assert.equal(vm.events.length, 0)
    assert.equal(vm.props.selectedToolId, 'vacuum-reference')
    vm.state.switchTool()
    assert.deepEqual(vm.events, [['tool', { kind: 'library', id: 'robotiq-2f140', rotation: [0, 0, 0] }]])
    assert.equal(vm.props.selectedToolId, 'vacuum-reference', 'Do not pretend the parent has mounted it yet')
    vm.props.selectedToolId = 'robotiq-2f140'; await nextTick()
    assert.equal(vm.state.currentName.value, 'Robotiq 2F-140')
  } finally { vm.dispose() }
})

test('built-in vacuum and no-tool use the exact same switch action without uploads', async () => {
  const vm = modelLibrary()
  try {
    vm.state.switchTool()
    assert.equal(vm.events[0][1].kind, 'vacuum-reference')
    vm.state.choice.value = 'none'; await nextTick(); vm.state.switchTool()
    assert.deepEqual(vm.events[1], ['tool', null])
  } finally { vm.dispose() }
})

test('实拍尺寸只更新草稿，显式应用后才提交40mm连接和30mm吸盘，锁定时不能覆盖', () => {
  const vm=modelLibrary()
  try {
    vm.state.dimensions.value={...DEFAULT_VACUUM,mountHeight:0,cupHeight:50}
    vm.state.loadPhotoDimensions()
    assert.equal(vm.events.length,0)
    assert.equal(vm.state.dimensions.value.mountHeight,40)
    assert.equal(vm.state.dimensions.value.cupHeight,30)
    vm.state.switchTool()
    assert.equal(vm.events[0][1].dimensions.mountHeight,40)
    assert.equal(vm.events[0][1].dimensions.cupHeight,30)
    vm.props.toolLocked=true
    vm.state.dimensions.value.mountHeight=12
    vm.state.loadPhotoDimensions()
    assert.equal(vm.state.dimensions.value.mountHeight,12)
  } finally {vm.dispose()}
})

test('busy/active lock, invalid angle and not-yet-installed selection cannot switch tools', async () => {
  const vm = modelLibrary()
  try {
    vm.props.busy = true; vm.state.switchTool(); assert.equal(vm.events.length, 0)
    vm.props.busy = false; vm.props.toolLocked = true; vm.state.switchTool(); assert.equal(vm.events.length, 0)
    vm.props.toolLocked = false; vm.state.rotation.value = [NaN, 0, 0]; vm.state.switchTool()
    assert.match(vm.state.error.value, /安装角/)
    vm.state.choice.value = 'not-installed'; await nextTick(); vm.state.switchTool()
    assert.match(vm.state.error.value, /末端显示模型/)
    assert.equal(vm.events.length, 0)
  } finally { vm.dispose() }
})

test('selected-tool prop updates label and default angle when parent confirms canonical state', async () => {
  const vm = modelLibrary({ selectedToolId: 'robotiq-2f85' })
  try {
    await vm.state.refreshCatalog()
    assert.deepEqual(vm.state.rotation.value, [0, 0, 0])
    vm.props.selectedToolId = 'none'; await nextTick()
    assert.equal(vm.state.choice.value, 'none')
    assert.equal(vm.state.currentName.value, '无工具（法兰）')
    assert.deepEqual(vm.events, [])
  } finally { vm.dispose() }
})

test('unmounted model library ignores a late catalog result', async () => {
  const vm = modelLibrary()
  const loading = vm.state.refreshCatalog()
  vm.dispose()
  await loading
  assert.deepEqual(vm.state.catalog.value, [])
  assert.deepEqual(vm.events, [])
})

test('additional original assets retain exact bytes, notices, license, URL and commit', () => {
  for (const key of ['franka', 'schunk', 'wsg']) {
    const manifest = JSON.parse(read(`source/${key}/manifest.json`))
    for (const file of manifest.files) {
      const bytes = read(`source/${key}/${file.path}`)
      assert.equal(sha(bytes), file.sha256)
      assert.equal(bytes.length, file.bytes)
      assert.ok(file.url.includes('/' + manifest.commit + '/'))
    }
    assert.match(read(key === 'wsg' ? 'source/wsg/wsg_50_description/LICENSE' : `source/${key}/LICENSE`).toString(),
      key === 'wsg' ? /Toyota Research Institute/ : /Apache License/)
  }
  assert.match(read('source/franka/NOTICE').toString(), /Franka Robotics/)
})

test('all six tools have neutral silver PBR highlights and retain dark rubber where present', async () => {
  for (const tool of catalog.tools) await offlineFetch(async () => {
    const root = await readLibraryTool({ id: tool.id })
    const seen = new Set()
    root.traverse((o) => { if (o.material) seen.add(o.material) })
    assert.ok([...seen].some((m) => m.userData.surface === 'silver' && m.metalness >= .6 && m.roughness <= .35))
    for (const m of seen) {
      assert.ok(m.isMeshStandardMaterial)
      assert.equal(m.emissiveIntensity, 0)
      assert.equal(m.envMapIntensity, 1)
      if (m.userData.surface === 'rubber') { assert.ok(m.roughness >= .75); assert.ok(m.metalness < .1) }
    }
    applyToolSkin(root, 'cyber')
    for (const m of seen) {
      if (m.userData.surface === 'silver') {
        assert.equal(m.color.getHexString(), '8d9aaa'); assert.equal(m.metalness, .6); assert.equal(m.roughness, .3)
      } else {
        const expected = TOOL_FINISHES[m.userData.surface]
        assert.equal(m.color.getHexString(), expected.color.slice(1))
        assert.equal(m.metalness, expected.metalness); assert.equal(m.roughness, expected.roughness)
      }
    }
    applyToolSkin(root, 'factory')
    for (const m of seen) if (m.userData.surface === 'silver') assert.equal(m.color.getHexString(), 'c2c9cc')
    assert.throws(() => applyToolSkin(root, 'glowing'), /皮肤/)
    disposeAsset(root)
  })
})

test('skin switching handles an empty mount and restores factory vacuum rubber without geometry changes', () => {
  assert.equal(applyToolSkin(null, 'cyber'), null)
  const root = buildVacuumReference({})
  const materials = new Set(), geometries = new Set()
  root.traverse((o) => { if (o.material) materials.add(o.material); if (o.geometry) geometries.add(o.geometry) })
  for (let i = 0; i < 3; i++) {
    applyToolSkin(root, 'cyber')
    for (const m of materials) {
      if (m.userData.surface === 'silver') {
        assert.equal(m.color.getHexString(), '8d9aaa'); assert.equal(m.metalness, .6); assert.equal(m.roughness, .3)
      } else {
        const expected = TOOL_FINISHES[m.userData.surface]
        assert.equal(m.color.getHexString(), expected.color.slice(1))
        assert.equal(m.metalness, expected.metalness); assert.equal(m.roughness, expected.roughness)
      }
    }
    applyToolSkin(root, 'factory')
    for (const m of materials) assert.equal(m.color.getHexString(), TOOL_FINISHES[m.userData.surface].color.slice(1))
  }
  root.traverse((o) => { if (o.geometry) assert.ok(geometries.has(o.geometry)) })
  disposeAsset(root)
})

for (const surface of ['silver', 'black-metal', 'white-rubber', 'rubber']) {
  test(`cyber/factory cycles preserve semantic surface ${surface}, never recolor the whole tool`, () => {
    const material = new THREE.MeshStandardMaterial()
    material.userData.surface = surface
    const root = new THREE.Group()
    root.add(new THREE.Mesh(new THREE.BoxGeometry(.01, .01, .01), material))
    const original = TOOL_FINISHES[surface]
    for (let i = 0; i < 3; i++) {
      applyToolSkin(root, 'cyber')
      assert.equal(material.color.getHexString(), surface === 'silver' ? '8d9aaa' : original.color.slice(1))
      assert.equal(material.metalness, surface === 'silver' ? .6 : original.metalness)
      assert.equal(material.roughness, surface === 'silver' ? .3 : original.roughness)
      assert.equal(material.envMapIntensity, 1)
      applyToolSkin(root, 'factory')
      assert.equal(material.color.getHexString(), original.color.slice(1))
      assert.equal(material.metalness, original.metalness)
      assert.equal(material.roughness, original.roughness)
    }
    disposeAsset(root)
  })
}

test('six articulated GLBs stay under 8 MiB total and are fetched only individually on selection', async () => {
  // Independent movable links preserve source joints instead of merging away
  // animation. The measured 7.63 MB remains lazy-loaded, one tool at a time.
  assert.ok(catalog.tools.reduce((n, t) => n + read(t.asset.slice(7)).length, 0) < 8 * 1024 * 1024)
  await offlineFetch(async (requests) => {
    const root = await readLibraryTool({ id: 'schunk-wsg50' }); disposeAsset(root)
    assert.deepEqual(requests.filter((path) => path.endsWith('.glb')), ['/tools/schunk-wsg50.glb'])
  })
})

test('new TCP references follow explicit upstream frames and stay inside assembled envelopes', () => {
  assert.deepEqual(catalog.tools.find((t) => t.id === 'franka-panda-hand').tcp_mm, [0, 0, 103.4])
  assert.deepEqual(catalog.tools.find((t) => t.id === 'schunk-sdh').tcp_mm, [0, 0, 150])
  const wsg = catalog.tools.find((t) => t.id === 'schunk-wsg50')
  assert.ok(Math.abs(wsg.tcp_mm[2] - 144.383) < .001)
  for (const tool of catalog.tools.slice(3)) for (let i = 0; i < 3; i++) {
    assert.ok(tool.tcp_mm[i] >= tool.bounds_mm.min[i] && tool.tcp_mm[i] <= tool.bounds_mm.max[i])
  }
})
