// Offline only. No browser, service, build, filesystem outputs or hardware IO.
// Exports actual pinned source meshes into memory, then uses real GLTFLoader.
// Run: node --test scripts/check-tool-animation.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { createToolAnimation, validateToolAnimation, toolClosure } from '../src/toolKinematics.js'
import { readLibraryTool, disposeAsset, inspectLibraryGlb } from '../src/toolVisual.js'

const project = fileURLToPath(new URL('../../', import.meta.url))
const catalog = JSON.parse(readFileSync(new URL('../public/tools/catalog.json', import.meta.url)))
const python = process.env.TOOL_ANIMATION_PYTHON ||
  (existsSync(resolve(project, '.venv/Scripts/python.exe')) ? resolve(project, '.venv/Scripts/python.exe') : 'python3')
const code = String.raw`
import base64, copy, json, socket
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('No network in animation tests'))
from tools.build_tool_assets import animation_source, animation_matrices, write_glb, np
from backend.core import tool_library, sim_tool
catalog=json.loads(tool_library.CATALOG.read_text(encoding='utf-8'))
result=[]
for item in catalog['tools']:
 parts, rig=animation_source(item['id'])
 assert rig == item['animation'], 'Checked-in animation metadata differs from source exporter: '+item['id']
 poses=[]
 bind=animation_matrices(rig,0)
 for c in (0,.123,.5,.987,1):
  matrices=animation_matrices(rig,c)
  vertices=[]
  for p in parts:
   m=matrices[p['link']]@np.linalg.inv(bind[p['link']])
   vertices.append(p['triangles'].reshape(-1,3)@m[:3,:3].T+m[:3,3])
  vertices=np.concatenate(vertices)
  poses.append({'closure':c,'matrices':{k:v.flatten(order='F').tolist() for k,v in matrices.items()},
   'bounds_m':{'min':vertices.min(axis=0).tolist(),'max':vertices.max(axis=0).tolist()}})
 # Dense off-grid sample coverage, independent of the exporter's sample points.
 for c in np.linspace(.000317,.999713,173):
  matrices=animation_matrices(rig,float(c))
  for p in parts:
   m=matrices[p['link']]@np.linalg.inv(bind[p['link']])
   v=p['triangles'].reshape(-1,3)[::31]@m[:3,:3].T+m[:3,3]
   assert np.all(v.min(axis=0)*1000 >= np.array(rig['collision_bounds_mm']['min'])-1e-6)
   assert np.all(v.max(axis=0)*1000 <= np.array(rig['collision_bounds_mm']['max'])+1e-6)
 model=sim_tool.model_for('elfin5',{'kind':'library','id':item['id'],'rotation':[0,0,0]})
 assert model.library_tool['bounds_mm'] == tool_library.collision_bounds(item)
 assert tool_library.profile(item)['actions'] == {'open':'unmapped','close':'unmapped'}
 assert tool_library.profile(item)['simulation_animation']['io'] is False
 # No action-state-dependent TCP or collision profile.
 assert item['tcp_mm'] == tool_library.profile(item)['tcp']['translation_mm']
 bad=copy.deepcopy(item);bad['animation']['collision_bounds_mm']['min'][0]=float('nan')
 try: tool_library.collision_bounds(bad)
 except ValueError: pass
 else: raise AssertionError('Backend accepted non-finite swept bounds')
 blob=write_glb(None,parts,{'animation':rig,'tcp_mm':item['tcp_mm']})
 result.append({'id':item['id'],'glb':base64.b64encode(blob).decode(),'rig':rig,'poses':poses,
  'backend_bounds_mm':model.library_tool['bounds_mm'],'tcp_mm':item['tcp_mm']})
print(json.dumps(result,separators=(',',':')))
`
const exported = spawnSync(python, ['-B', '-c', code], {
  cwd: project, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, timeout: 90000,
})
assert.equal(exported.status, 0, exported.stderr || exported.error?.message)
const sources = JSON.parse(exported.stdout)
const buffer = (bytes) => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
async function load(source) {
  const bytes = Buffer.from(source.glb, 'base64')
  inspectLibraryGlb(buffer(bytes))
  const root = (await new GLTFLoader().parseAsync(buffer(bytes), '')).scene
  root.userData.tcp_mm = [...source.tcp_mm]
  return root
}
function near(a, b, tolerance = 2e-6) {
  assert.equal(a.length, b.length)
  a.forEach((v, i) => assert.ok(Math.abs(v-b[i]) < tolerance, `${i}: ${v} != ${b[i]}`))
}

test('source pipeline produces six genuine independently articulated tools with unchanged TCPs and no real mappings', () => {
  assert.equal(sources.length, 6)
  assert.equal(sources.find((s) => s.id === 'robotiq-3f').rig.parts.length, 13)
  assert.deepEqual(sources.map((s) => s.tcp_mm), catalog.tools.map((t) => t.tcp_mm))
})

for (const source of sources) {
  test(`${source.id}: actual exported rigid links match independent source FK across entire open/closed preset`, async () => {
    const root = await load(source)
    const before = new Map(), materials = new Set()
    root.traverse((o) => { if (o.isMesh) { before.set(o, o.geometry.attributes.position.array.slice()); materials.add(o.material) } })
    const animation = createToolAnimation(root, source.rig)
    const fixedTcp = [...root.userData.tcp_mm]
    try {
      for (const pose of source.poses) {
        animation.reset({ closure: pose.closure }); root.updateMatrixWorld(true)
        const actual = new THREE.Box3().setFromObject(root, true)
        near(actual.min.toArray(), pose.bounds_m.min)
        near(actual.max.toArray(), pose.bounds_m.max)
        root.traverse((node) => {
          const name = node.userData.toolAnimationLink
          if (name) near(node.matrixWorld.elements, pose.matrices[name], 1e-9)
          if (node.isMesh) {
            assert.deepEqual(node.geometry.attributes.position.array, before.get(node), 'Rigid animation must not deform/replace source vertices')
            assert.ok(materials.has(node.material))
            near(node.scale.toArray(), [1, 1, 1])
          }
        })
        for (const axis of [0, 1, 2]) {
          assert.ok(actual.min.getComponent(axis)*1000 >= source.backend_bounds_mm.min[axis] - .002)
          assert.ok(actual.max.getComponent(axis)*1000 <= source.backend_bounds_mm.max[axis] + .002)
        }
        assert.deepEqual(root.userData.tcp_mm, fixedTcp)
        near(root.position.toArray(), [0, 0, 0]); near(root.scale.toArray(), [1, 1, 1])
      }
    } finally { animation.dispose(); disposeAsset(root) }
  })

  test(`${source.id}: smooth reversible frame-rate-independent updates, fixed envelope, bounded background catch-up`, async () => {
    const a = await load(source), b = await load(source)
    const x = createToolAnimation(a, source.rig), y = createToolAnimation(b, source.rig)
    try {
      const bounds = structuredClone(x.collisionBoundsMm)
      x.update({ action_state: 'active' }, 1/60)
      assert.ok(x.snapshot().closure > 0 && x.snapshot().closure < .2)
      for (let i = 1; i < 60; i++) x.update({ action_state: 'active' }, 1/60)
      for (let i = 0; i < 30; i++) y.update({ mounted: { action_state: 'active' } }, 1/30)
      near([x.snapshot().closure], [y.snapshot().closure], 1e-12)
      assert.ok(x.snapshot().closure > .999)
      const previous = x.snapshot().closure
      x.update({ action_state: 'idle' }, 1/60)
      assert.ok(x.snapshot().closure < previous && x.snapshot().closure > .8)
      const state = x.snapshot()
      for (const dt of [NaN, Infinity, -1, 0, undefined]) x.update(null, dt)
      assert.deepEqual(x.snapshot(), state)
      assert.equal(x.update({ mode: 'real', action_state: 'active' }, .02), false)
      assert.deepEqual(x.snapshot(), state)
      y.reset({ closure: 0 }); y.update({ action_state: 'active' }, 3600)
      assert.ok(y.snapshot().closure > 0 && y.snapshot().closure < .7)
      assert.deepEqual(x.collisionBoundsMm, bounds)
    } finally { x.dispose(); y.dispose(); disposeAsset(a); disposeAsset(b) }
  })

  test(`${source.id}: ghost clones have independent controllers; skin/material/geometry ownership is not stolen`, async () => {
    const root = await load(source), animation = createToolAnimation(root, source.rig)
    animation.reset({ closure: .4 })
    const ghost = root.clone(true), ghostAnimation = createToolAnimation(ghost, animation.metadata)
    try {
      ghostAnimation.reset({ closure: 1 })
      assert.equal(animation.snapshot().closure, .4)
      assert.equal(ghostAnimation.snapshot().closure, 1)
      const again = createToolAnimation(ghost, source.rig)
      assert.equal(ghostAnimation.snapshot().disposed, true)
      again.reset({ closure: .5 })
      ghost.updateMatrixWorld(true)
      const expected = source.poses.find((p) => p.closure === .5).bounds_m
      near(new THREE.Box3().setFromObject(ghost, true).max.toArray(), expected.max)
      let disposedGeometry = 0
      root.traverse((o) => o.geometry?.addEventListener('dispose', () => disposedGeometry++))
      again.dispose(); assert.equal(disposedGeometry, 0)
      assert.equal(again.update({ action_state: 'active' }, .02), false)
      assert.equal(again.reset({ closure: 0 }), false)
    } finally { animation.dispose(); ghostAnimation.dispose(); disposeAsset(root) }
  })
}

test('current action-state interface is authoritative; unknown/error/real state never becomes an open command', () => {
  assert.equal(toolClosure({ action_state: 'active', last_action: { action: 'open' } }), 1)
  assert.equal(toolClosure({ action_state: 'idle', last_action: { action: 'close' } }), 0)
  for (const state of [null, {}, { action_state: 'error' }, { last_action: { action: 'close' } },
    { sim: false, action_state: 'active' }, { mode: 'real', mounted: { action_state: 'active' } }]) assert.equal(toolClosure(state), null)
  assert.equal(toolClosure({ closure: 10 }), 1); assert.equal(toolClosure({ closure: -10 }), 0)
})

test('0.8 second quintic motion is monotone, eases at both ends and stays below source 2F/Panda joint velocity limits', async () => {
  for (const source of sources) {
    const root = await load(source), animation = createToolAnimation(root, source.rig)
    try {
      let previous = 0, firstStep = 0, peak = 0
      for (let i = 0; i < 200; i++) {
        animation.update({ action_state: 'active' }, 1/240)
        const closure = animation.snapshot().closure, step = closure - previous
        assert.ok(step >= -1e-12)
        if (!i) firstStep = step
        peak = Math.max(peak, step * 240)
        for (const link of source.rig.links) {
          const speed = step * 240 * Math.abs(link.closed-link.open)
          if (source.id.startsWith('robotiq-2f') && link.type === 'revolute') assert.ok(speed <= 2)
          if (source.id === 'franka-panda-hand' && link.type === 'prismatic') assert.ok(speed <= .2)
        }
        previous = closure
      }
      assert.equal(previous, 1)
      assert.ok(peak <= 1.875/.8 + 1e-9)
      assert.ok(firstStep*240 < peak/100)
    } finally { animation.dispose(); disposeAsset(root) }
  }
})

test('disposing a mounting wrapper also releases its nested animation controller', async () => {
  const root = await load(sources[0]), animation = createToolAnimation(root, sources[0].rig)
  const wrapper = new THREE.Group(); wrapper.add(root)
  disposeAsset(wrapper)
  assert.equal(animation.snapshot().disposed, true)
})

test('reject broken hierarchy, non-rigid scale, nonfinite data, missing parts and malformed swept envelopes', async () => {
  const rig = sources[0].rig
  for (const mutate of [
    (r) => { r.simulation_only = false }, (r) => { r.links[0].parent = r.links.at(-1).name },
    (r) => { r.links[0].origin[0] = 2 }, (r) => { r.links[0].axis = [0, 0, 0] },
    (r) => { r.links[0].closed = NaN }, (r) => { r.collision_bounds_mm.min[0] = Infinity },
    (r) => { r.parts.push(r.parts[0]) },
  ]) { const value = structuredClone(rig); mutate(value); assert.throws(() => validateToolAnimation(value), /工具动画/) }
  const root = await load(sources[0])
  try {
    const bad = structuredClone(rig); bad.parts[0].name = 'invented'
    assert.throws(() => createToolAnimation(root, bad), /独立可动部件/)
    const old = createToolAnimation(root, null)
    assert.equal(old.supported, false); assert.equal(old.update({ action_state: 'active' }, 1), false)
  } finally { disposeAsset(root) }
})

test('old merged 3F must not pretend to articulate; it explicitly requires source re-export', async () => {
  const bytes = readFileSync(new URL('../public/tools/robotiq-3f.glb', import.meta.url))
  const document = inspectLibraryGlb(buffer(bytes))
  // Once the main agent regenerates assets this fixture is the new rig instead.
  const root = (await new GLTFLoader().parseAsync(buffer(bytes), '')).scene
  try {
    if (document.asset.extras?.animation) {
      assert.equal(createToolAnimation(root, document.asset.extras.animation).supported, true)
    } else {
      assert.throws(() => createToolAnimation(root, sources.find((s) => s.id === 'robotiq-3f').rig), /独立可动部件/)
    }
  } finally { disposeAsset(root) }
})

test('readLibraryTool attaches safe disposable animation handle without serializing it into clone userData', async () => {
  const source = sources[0], bytes = Buffer.from(source.glb, 'base64')
  const { createHash } = await import('node:crypto')
  const localCatalog = structuredClone(catalog)
  localCatalog.tools[0].sha256 = createHash('sha256').update(bytes).digest('hex')
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url) => {
    assert.ok(['/tools/catalog.json', '/tools/robotiq-2f85.glb'].includes(url))
    return new Response(url.endsWith('.json') ? JSON.stringify(localCatalog) : bytes)
  }
  try {
    const root = await readLibraryTool({ id: 'robotiq-2f85' })
    assert.equal(root.animation.supported, true)
    assert.equal(Object.keys(root).includes('animation'), false)
    assert.doesNotThrow(() => JSON.stringify(root.userData))
    assert.doesNotThrow(() => root.clone(true))
    disposeAsset(root)
    assert.equal(root.animation.snapshot().disposed, true)
  } finally { globalThis.fetch = originalFetch }
})
