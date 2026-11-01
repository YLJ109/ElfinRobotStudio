import { expandedCameraFence, constrainCameraOrbit } from '../src/cameraFence.js'
// Real scene.js + real GLTFLoader/STLLoader. Reuse the performance harness DOM,
// renderer and scheduling substitutes. No GPU/network/service/build/capture.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { performance as timer } from 'node:perf_hooks'
import { createHash } from 'node:crypto'
import * as Real from 'three'
import { TransformControls } from 'three/addons/controls/TransformControls.js'
import { GLTFLoader as ActualGLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { STLLoader as ActualSTLLoader } from 'three/addons/loaders/STLLoader.js'
import { MeshoptDecoder } from 'meshoptimizer/decoder'
import { buildVacuumReference, readToolFile, readLibraryTool, disposeAsset, applyToolSkin, createToolAnimation } from '../src/toolVisual.js'
import { robotAsset } from '../src/visualAssets.js'
import { applyJointCaps, applyRobotFinishes } from '../src/jointCaps.js'
import { createRobotSurfaceMaterial, smoothRobotSurface } from '../src/robotSurface.js'
import { robotSurfaceAssetUrl, hydrateRobotSurfaceGeometry, ROBOT_SURFACE_ASSET_VERSION, ROBOT_SURFACE_RECIPE } from '../src/robotSurfaceAsset.js'
import { GROUND_DIRECTIONS } from '../src/groundDirections.js'
import { recordMetric, snapshotMetrics, resetMetrics } from '../src/runtimeMetrics.js'
import { createObjectTeaching } from '../src/objectTeaching.js'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { OBJECT_CATALOG, OBJECT_SURFACES, catalogEntry, catalogSpec } from '../src/objectCatalog.js'
import { createJointPresentation } from '../src/jointPresentation.js'
import { objectGroundHeight } from '../src/objectGround.js'
import { createVirtualCamera } from '../src/virtualCamera.js'
import { createDrawingVisual } from '../src/drawingVisual.js'

const harnessUrl = new URL('./check-scene-performance.mjs', import.meta.url)
const harnessSource = readFileSync(harnessUrl, 'utf8')
const marker = '\nlet scene,disposeControls,disposeCancelled=0'
assert.ok(harnessSource.includes(marker), 'performance harness setup boundary changed; update integration adapter')
const preamble = harnessSource.slice(0, harnessSource.indexOf(marker))
  .replace(/^import .*from ['"][^'"]+['"]\r?\n/gm, '')
  .replaceAll('import.meta.url', 'harnessUrl')
const bindings = { assert, readFileSync, timer, Real, TransformControls,
  buildVacuumReference, readToolFile, readLibraryTool, disposeAsset, applyToolSkin, createToolAnimation,
  robotAsset, applyJointCaps, applyRobotFinishes, createRobotSurfaceMaterial, smoothRobotSurface,
  robotSurfaceAssetUrl, hydrateRobotSurfaceGeometry, ROBOT_SURFACE_ASSET_VERSION, ROBOT_SURFACE_RECIPE,
  GROUND_DIRECTIONS, recordMetric, snapshotMetrics, resetMetrics, createObjectTeaching,
  mergeGeometries, OBJECT_CATALOG, OBJECT_SURFACES, catalogEntry, catalogSpec, createJointPresentation,
  objectGroundHeight, createVirtualCamera, createDrawingVisual, expandedCameraFence, constrainCameraOrbit, harnessUrl }

function environment() {
  // Execute setup only: the unrelated object-editing/performance assertions in
  // the original harness are NOT run. scene.js itself is loaded fresh each time.
  return new Function(...Object.keys(bindings), preamble + `
    return {deps,body,frames,events,step,
      get renderedScene(){return lastScene},get canvas(){return canvas},
      get observers(){return longObservers},
      restore(){for(const restore of originals.reverse())restore()}}
  `)(...Object.values(bindings))
}

const root = new URL('../public/models/elfin5/', import.meta.url)
const report = JSON.parse(readFileSync(new URL('surface-report.json', root), 'utf8'))
const spec = JSON.parse(readFileSync(new URL('robot.json', root), 'utf8'))
const links = Object.entries(spec.links).filter(([,link]) => link.visual?.mesh).map(([name]) => name)
const targetLink = 'elfin_link1'
const results = []
const hash = value => createHash('sha256').update(value).digest('hex')
console.log('SCENE_SURFACE_SOURCES '+JSON.stringify({
  scene: hash(readFileSync(new URL('../src/scene.js',import.meta.url))), harness: hash(harnessSource),
  recipe: report.recipe, assetVersion: report.version,
}))

async function scenario(name, { quality = 'full', fault = null, work = 0 } = {}) {
  const env = environment(), requests = [], parsed = [], clones = [], stls = []
  const disposals = new Map(), bufferRequests = new WeakMap(), liveResources = new Set()
  const calls = { caps: 0, finishes: 0, smooth: 0, capsWork: 0, finishesWork: 0, smoothWork: 0,
    hydrate: 0, hydrated: 0, hydrateRejected: 0, gltfParsed: 0, stlParsed: 0 }
  const tracked = new Set()
  const watch = resource => {
    if (!resource || tracked.has(resource)) return
    tracked.add(resource)
    resource.addEventListener('dispose', () => disposals.set(resource, (disposals.get(resource) || 0) + 1))
  }
  const isTarget = path => path.includes('/' + targetLink + (quality === 'low' ? '.low' : '') + '.')
  const isBaked = path => path.endsWith('.surface.glb')
  const isOriginal = path => path.endsWith('.glb') && !isBaked(path)
  const localFetch = async url => {
    assert.equal(typeof url, 'string')
    assert.ok(url.startsWith('/models/elfin5/'), 'unexpected external resource: ' + url)
    const file = url.slice('/models/elfin5/'.length)
    assert.ok(!file.includes('/') && !file.includes('..'))
    requests.push(file)
    const missing = file === 'surface-report.json' && fault === 'missing-report'
      || isTarget(url) && isBaked(file) && ['missing-surface','stl-fallback','missing-link'].includes(fault)
      || isTarget(url) && isOriginal(file) && ['stl-fallback','missing-link'].includes(fault)
      || file === targetLink + '.STL' && fault === 'missing-link'
    if (missing) return { ok: false, status: 404 }
    const bytes = readFileSync(new URL(file, root))
    return { ok: true,
      json: async () => {
        const value = JSON.parse(bytes.toString('utf8'))
        if (file === 'surface-report.json' && fault === 'old-report') value.version++
        if (file === 'surface-report.json' && fault === 'report-error') throw new Error('report decode failed')
        if (file === 'surface-report.json' && fault === 'wrong-hash') {
          value.assets.find(a => a.link === targetLink && a.quality === quality).source_sha256 = '0'.repeat(64)
        }
        return value
      },
      arrayBuffer: async () => {
        const buffer = fault === 'corrupt-surface' && isTarget(url) && isBaked(file)
          ? new Uint8Array([1,2,3,4]).buffer
          : bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
        bufferRequests.set(buffer, file)
        return buffer
      },
    }
  }
  class Loader {
    constructor() { this.actual = new ActualGLTFLoader() }
    setMeshoptDecoder(decoder) { this.actual.setMeshoptDecoder(decoder); return this }
    async parseAsync(buffer, path) {
      const file = bufferRequests.get(buffer)
      const result = await this.actual.parseAsync(buffer, path)
      calls.gltfParsed++
      result.scene.traverse(object => {
        if (object.geometry) {
          const geometry = object.geometry
          watch(geometry)
          const materials = (Array.isArray(object.material) ? object.material : [object.material]).filter(Boolean)
          materials.forEach(watch)
          parsed.push({ file, geometry, materials })
          const clone = geometry.clone
          geometry.clone = function () {
            const copy = clone.call(this)
            watch(copy)
            clones.push({ file, geometry: copy })
            return copy
          }
        }
      })
      return result
    }
  }
  class STLLoader {
    parse(buffer) {
      const geometry = new ActualSTLLoader().parse(buffer)
      watch(geometry); stls.push(geometry); calls.stlParsed++
      return geometry
    }
  }
  const wrap = (key, real, alreadyPrepared) => (geometry, ...args) => {
    calls[key]++
    const ready = alreadyPrepared(geometry)
    if (!ready) calls[key + 'Work']++
    const index = geometry.index, position = geometry.getAttribute('position')
    const normal = geometry.getAttribute('normal'), color = geometry.getAttribute('color')
    const groups = JSON.stringify(geometry.groups)
    const result = real(geometry, ...args)
    if (ready) {
      assert.equal(geometry.index, index, key + ' reindexed a hydrated geometry')
      assert.equal(geometry.getAttribute('position'), position)
      assert.equal(geometry.getAttribute('normal'), normal, key + ' recalculated normals')
      assert.equal(geometry.getAttribute('color'), color, key + ' repainted vertex colors')
      assert.equal(JSON.stringify(geometry.groups), groups)
    }
    return result
  }
  env.deps.fetch = localFetch
  env.deps.localStorage = { getItem: key => key === 'huayan.modelQuality' ? quality : null,
    setItem: () => { throw new Error('integration test must not persist state') } }
  env.deps.applyJointCaps = wrap('caps', applyJointCaps, g => g.userData.jointCaps?.applied === true)
  env.deps.applyRobotFinishes = wrap('finishes', applyRobotFinishes, g => g.userData.robotFinishes?.version === 2)
  env.deps.smoothRobotSurface = wrap('smooth', smoothRobotSurface, g => !!g.userData.robotSurface)
  env.deps.hydrateRobotSurfaceGeometry = (...args) => {
    calls.hydrate++
    const ready = hydrateRobotSurfaceGeometry(...args)
    calls[ready ? 'hydrated' : 'hydrateRejected']++
    return ready
  }
  const fallbackModule = env.deps.loadModule
  env.deps.loadModule = async name => name.includes('GLTFLoader') ? { GLTFLoader: Loader }
    : name.includes('STLLoader') ? { STLLoader }
    : name.includes('meshoptimizer') ? { MeshoptDecoder } : fallbackModule(name)
  const createScene = new Function(...Object.keys(env.deps), env.body)(...Object.values(env.deps))
  let scene
  const start = timer.now()
  try {
    resetMetrics()
    if(fault==='missing-link') {
      await assert.rejects(createScene({clientWidth:800,clientHeight:600,appendChild(){}},{model:'elfin5'}),/模型资源缺失：elfin_link1/)
      assert.equal(calls.hydrated,6,'other valid links must finish parsing before failure cleanup')
      for(const row of parsed) {
        assert.equal(disposals.get(row.geometry),1,'temporary parsed geometry leaked: '+row.file)
        for(const material of row.materials)assert.equal(disposals.get(material),1,'loader material leaked')
      }
      results.push({name,quality,fault,calls:{...calls},requests,rejected:true})
      return
    }
    scene = await createScene({ clientWidth: 800, clientHeight: 600, appendChild() {} }, { model: 'elfin5' })
    assert.ok(env.renderedScene, 'scene did not render its initial frame')
    const robotMeshes = []
    env.renderedScene.traverse(object => {
      if (!object.userData.link) return
      if (object.isMesh) robotMeshes.push(object)
      if (object.geometry) { liveResources.add(object.geometry); watch(object.geometry) }
      for (const material of (Array.isArray(object.material) ? object.material : [object.material]).filter(Boolean)) {
        liveResources.add(material); watch(material)
      }
    })
    const visibleLinks = links
    assert.equal(robotMeshes.length, visibleLinks.length * 2, 'real and ghost links must both survive loading')
    for (const link of visibleLinks) {
      const meshes = robotMeshes.filter(mesh => mesh.userData.link === link)
      assert.equal(meshes.length, 2)
      assert.equal(meshes[0].geometry, meshes[1].geometry, 'robot/ghost must share prepared geometry')
      assert.ok(meshes[0].geometry.groups.length <= 2)
      assert.ok(meshes[0].geometry.userData.robotSurface, 'fallback must also prepare shading')
    }
    for (const key of ['capsWork','finishesWork','smoothWork']) assert.equal(calls[key], work, key)
    const suffix = quality === 'low' ? '.low.glb' : '.glb'
    if (!fault) {
      for (const key of ['caps','finishes','smooth']) assert.equal(calls[key], 0,
        'baked scene must bypass the processing entry point: '+key)
      assert.equal(calls.hydrated, 7)
      assert.equal(calls.gltfParsed, 7)
      assert.equal(requests.filter(isOriginal).length, 0, 'baked success fetched original GLB')
      assert.equal(requests.filter(file => file.endsWith('.STL')).length, 0)
    }
    if (['missing-report','old-report','report-error'].includes(fault)) {
      assert.equal(calls.hydrate, 0)
      assert.equal(requests.filter(isBaked).length, 0)
      assert.equal(requests.filter(isOriginal).length, 7)
    }
    if (['wrong-hash','missing-surface','corrupt-surface'].includes(fault)) {
      assert.equal(calls.hydrated, 6)
      assert.equal(requests.filter(isOriginal).length, 1, 'fallback should be per-link, not all meshes')
      assert.ok(requests.includes(targetLink + suffix))
    }
    if (fault === 'wrong-hash') {
      assert.equal(calls.hydrateRejected, 1)
      const rejected = clones.find(row => row.file === targetLink + suffix.replace('.glb','.surface.glb'))
      assert.ok(rejected)
      assert.equal(disposals.get(rejected.geometry), 1, 'hydrate-rejected clone leaked')
    }
    if (fault === 'stl-fallback') {
      assert.equal(calls.stlParsed, 1)
      assert.ok(requests.includes(targetLink + '.STL'))
    }
    // Every successful parse releases temporary loader-owned resources before
    // scene construction returns. Candidate clones stay alive only if retained.
    for (const row of parsed) {
      assert.equal(disposals.get(row.geometry), 1, 'temporary parsed geometry leaked: '+row.file)
      for (const material of row.materials) assert.equal(disposals.get(material), 1, 'loader material leaked')
    }
    scene.setImmediate([0,25,70,0,25,0])
    scene.setGhost([0,25,70,0,25,0], 'ghost')
    env.step(16)
    results.push({ name, quality, fault, calls: { ...calls }, requests,
      scene_ms: timer.now() - start, prepare_metric: snapshotMetrics()['scene.prepare'],
      parsed_resources: parsed.length, candidate_clones: clones.length })
  } finally {
    try {
      scene?.dispose()
      scene?.dispose()
      for (const resource of liveResources) assert.equal(disposals.get(resource), 1,
        'shared robot/ghost resource must dispose exactly once')
      for (const { geometry } of clones) assert.equal(disposals.get(geometry), 1,
        'accepted/rejected geometry clone must be freed exactly once')
      for (const geometry of stls) assert.equal(disposals.get(geometry), 1)
      assert.equal(env.frames.size, 0, 'RAF survives scene disposal')
      assert.equal(env.canvas?.listenerCount(), 0, 'canvas listeners survive disposal')
      assert.equal(env.events.size, 0, 'window/document listeners survive disposal')
      assert.equal(env.deps.window.__elfinScene, undefined, 'disposed scene remains globally referenced')
      assert.ok(env.observers.every(observer => !observer.connected), 'long-task observer survives disposal')
    } finally { env.restore() }
  }
}

for (const quality of ['full','low']) test(`${quality}: actual baked scene, zero reprocessing, shared disposal`, async () => {
  await scenario('baked-' + quality, { quality })
})
for (const fault of ['missing-report','old-report','report-error']) test(`${fault}: original GLB fallback and cleanup`, async () => {
  await scenario(fault, { quality: 'low', fault, work: 7 })
})
for (const fault of ['missing-surface','corrupt-surface','wrong-hash']) test(`${fault}: only failed link falls back`, async () => {
  await scenario(fault, { quality: 'low', fault, work: 1 })
})
test('original GLB unavailable: actual STL fallback and cleanup', async () => {
  await scenario('stl-fallback', { quality: 'low', fault: 'stl-fallback', work: 1 })
})
test('all candidates unavailable for one link: incomplete model rejected and all six parsed links disposed', async () => {
  await scenario('missing-link', { quality: 'low', fault: 'missing-link', work: 0 })
})
test.after(() => console.log('SCENE_SURFACE_INTEGRATION '+JSON.stringify(results)))
