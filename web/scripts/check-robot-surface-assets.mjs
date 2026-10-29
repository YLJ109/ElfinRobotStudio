// Real GLTFLoader round-trip tests, offline and read-only. No WebGL/build/server.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { performance } from 'node:perf_hooks'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'meshoptimizer'
import { Box3, Float32BufferAttribute, MeshStandardMaterial } from 'three'
import { applyJointCaps, applyRobotFinishes } from '../src/jointCaps.js'
import { ROBOT_BANDS, smoothRobotSurface, createRobotSurfaceMaterial } from '../src/robotSurface.js'
import { ROBOT_SURFACE_ASSET_VERSION, ROBOT_SURFACE_RECIPE, hydrateRobotSurfaceGeometry,
  robotSurfaceAssetUrl } from '../src/robotSurfaceAsset.js'
import { readDisplayGeometry, prepareSurfaceGeometry, sha256 } from './prepare-robot-surfaces.mjs'

const root = new URL('../public/models/elfin5/', import.meta.url)
const spec = JSON.parse(readFileSync(new URL('robot.json', root), 'utf8'))
const report = JSON.parse(readFileSync(new URL('surface-report.json', root), 'utf8'))
const metrics = []
const floatArray = attribute => Float32Array.from({ length: attribute.count * attribute.itemSize },
  (_, i) => attribute.getComponent(Math.floor(i / attribute.itemSize), i % attribute.itemSize))

test('manifest version, all fourteen source/output hashes, generation-source hashes', () => {
  assert.equal(report.version, ROBOT_SURFACE_ASSET_VERSION)
  assert.equal(report.recipe, ROBOT_SURFACE_RECIPE)
  assert.equal(report.variant, 'elfin5')
  assert.equal(report.assets.length, 14)
  assert.equal(new Set(report.assets.map(a => a.file)).size, 14)
  // Full source hashes remain provenance. Runtime ring/shader-only edits do
  // not invalidate baked black/body geometry; smoothing/caps edits do.
  assert.equal(sha256(readFileSync(new URL('../src/jointCaps.js', import.meta.url))), report.recipe_sources.caps_sha256)
  assert.equal(sha256(smoothRobotSurface.toString()), report.recipe_sources.smoothing_sha256)
  for (const asset of report.assets) {
    assert.equal(sha256(readFileSync(new URL(asset.source_file, root))), asset.source_sha256)
    assert.equal(sha256(readFileSync(new URL(asset.file, root))), asset.sha256)
  }
})

for (const asset of report.assets) test(`${asset.file}: exact decoded attributes, oriented faces, groups and live shader`, async () => {
  const source = readFileSync(new URL(asset.source_file, root))
  const expected = await readDisplayGeometry(source)
  const link = spec.links[asset.link]
  const options = { sourceSha256: asset.source_sha256, quality: asset.quality }
  let actual
  try {
    const start = performance.now()
    prepareSurfaceGeometry(expected, asset.link, link, { sourceBytes: source, quality: asset.quality })
    const processing_ms = performance.now() - start
    const bytes = readFileSync(new URL(asset.file, root))
    const metadata = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'))
    assert.equal(metadata.meshes.length, 1)
    assert.equal(metadata.meshes[0].primitives.length, 1, 'one primitive, no material splitting')
    assert.ok(metadata.extensionsRequired.includes('EXT_meshopt_compression'))
    for (const view of metadata.bufferViews) {
      const extension = view.extensions?.EXT_meshopt_compression
      if (extension) assert.ok(!extension.filter || extension.filter === 'NONE', 'no second lossy filter')
    }
    const parseStart = performance.now()
    actual = await readDisplayGeometry(bytes)
    const parse_ms = performance.now() - parseStart
    assert.deepEqual(actual.userData.groups, expected.userData.groups, 'extras survive GLTFLoader')
    const hydrateStart = performance.now()
    assert.equal(hydrateRobotSurfaceGeometry(actual, asset.link, link, options), true)
    const hydrate_ms = performance.now() - hydrateStart
    assert.deepEqual(actual.groups, expected.groups)
    assert.ok(actual.groups.length <= 2)
    assert.deepEqual(actual.groups.map(g => g.materialIndex), [0, 2])
    for (const name of ['position', 'normal', 'color']) {
      const a=floatArray(actual.getAttribute(name)), b=floatArray(expected.getAttribute(name))
      assert.equal(a.length,b.length)
      const mismatch=a.findIndex((v,i)=>!Object.is(v,b[i]))
      assert.equal(mismatch,-1,`${name}: exact float32 mismatch at ${mismatch}: ${a[mismatch]} != ${b[mismatch]}`)
    }
    assert.equal(actual.index.count, expected.index.count)
    // Meshopt's TRIANGLES codec may cyclically rotate a face's three corners.
    // It must never move a face across a group range, flip winding, or reorder
    // triangles. Rotation is lossless and leaves per-vertex colors intact.
    for (let i = 0; i < actual.index.count; i += 3) {
      const a = [0,1,2].map(k => actual.index.getX(i+k))
      const e = [0,1,2].map(k => expected.index.getX(i+k))
      assert.ok([0,1,2].some(offset => a.every((v,k) => v === e[(k+offset)%3])), 'face order/winding '+i)
    }
    actual.computeBoundingBox()
    expected.computeBoundingBox()
    // Original normalized integer accessors expose JS doubles; the serialized
    // float32 representation must match the exact GPU-visible coordinates.
    const gpuBounds = new Box3().setFromBufferAttribute(new Float32BufferAttribute(floatArray(expected.getAttribute('position')),3))
    assert.deepEqual(actual.boundingBox, gpuBounds)
    assert.ok(actual.boundingBox.min.distanceTo(expected.boundingBox.min)<1e-7)
    assert.ok(actual.boundingBox.max.distanceTo(expected.boundingBox.max)<1e-7)
    assert.ok(actual.userData.robotSurface.changedNormals > 0)
    if (asset.link === 'elfin_link6') {
      assert.equal(actual.userData.robotFinishes.portTriangles, asset.quality === 'low' ? 1812 : 4474)
    }
    const color = actual.getAttribute('color')
    for (let i = 0; i < color.count; i++) {
      const c = [color.getX(i),color.getY(i),color.getZ(i)]
      assert.ok(c.every(v => v === 1) || c.every(v => v >= .01 && v <= .025), 'only white/black, never cyan')
    }
    // All original processing entry points now fast-return on metadata.
    const index = actual.index, normals = actual.getAttribute('normal'), colors = actual.getAttribute('color')
    applyJointCaps(actual, asset.link, link)
    applyRobotFinishes(actual, asset.link)
    smoothRobotSurface(actual)
    assert.equal(actual.index, index)
    assert.equal(actual.getAttribute('normal'), normals)
    assert.equal(actual.getAttribute('color'), colors)
    // Cyan remains a live shader decision, with current Maxwell ring constants.
    const body = new MeshStandardMaterial({ vertexColors: true })
    const material = createRobotSurfaceMaterial(body, actual, asset.link)
    if (asset.link !== 'elfin_base' && material !== body) {
      const shader = { uniforms: {}, vertexShader: '#include <common>\n#include <begin_vertex>',
        fragmentShader: '#include <common>\n#include <color_fragment>\n#include <roughnessmap_fragment>\n#include <metalnessmap_fragment>' }
      material.onBeforeCompile(shader)
      assert.ok(shader.fragmentShader.includes('robotRingMask'))
      material.dispose()
    }
    body.dispose()
    metrics.push({ link: asset.link, quality: asset.quality, source_bytes: source.length,
      surface_bytes: bytes.length, processing_ms, surface_parse_clone_ms: parse_ms, hydrate_ms })
  } finally { expected.dispose(); actual?.dispose() }
})

test('hydrate rejects stale/foreign/malformed extras transactionally', async () => {
  const asset = report.assets.find(a => a.link === 'elfin_link1' && a.quality === 'full')
  const original = await readDisplayGeometry(readFileSync(new URL(asset.file, root)))
  const link = spec.links[asset.link], options = { sourceSha256: asset.source_sha256, quality: 'full' }
  try {
    for (const change of [
      g => { g.userData.robotSurfaceAsset.version++ },
      g => { g.userData.robotSurfaceAsset.variant = 'elfin10' },
      g => { g.userData.robotSurfaceAsset.linkName = 'elfin_link6' },
      g => { g.userData.robotSurfaceAsset.quality = 'low' },
      g => { g.userData.robotSurfaceAsset.source.sha256 = '0'.repeat(64) },
      g => { g.userData.robotSurfaceAsset.source.min[0] += .01 },
      g => { g.userData.robotSurfaceAsset.min[0] += .01 },
      g => { g.userData.groups[0].count -= 3 },
      g => { g.userData.groups[1].materialIndex = 1 },
      g => { g.userData.groups[0].start = 3 },
      g => { g.userData.jointCaps.applied = false },
      g => { g.userData.robotFinishes.linkName = 'elfin_link2' },
      g => { g.userData.robotSurface.changedNormals = Infinity },
      g => { g.deleteAttribute('normal') },
      g => { g.userData = {} },
    ]) {
      const geometry = original.clone()
      change(geometry)
      const groups = structuredClone(geometry.groups), data = structuredClone(geometry.userData)
      assert.equal(hydrateRobotSurfaceGeometry(geometry, asset.link, link, options), false)
      assert.deepEqual(geometry.groups, groups)
      assert.deepEqual(geometry.userData, data)
      geometry.dispose()
    }
    assert.equal(hydrateRobotSurfaceGeometry(original, asset.link, link), false, 'expected source hash required')
  } finally { original.dispose() }
})

test('surface asset naming preserves LOD and query; rejects double transforms', () => {
  assert.equal(robotSurfaceAssetUrl('/models/elfin5/elfin_link1.glb'), '/models/elfin5/elfin_link1.surface.glb')
  assert.equal(robotSurfaceAssetUrl('/models/elfin5/elfin_link1.low.glb?v=2'), '/models/elfin5/elfin_link1.low.surface.glb?v=2')
  assert.throws(() => robotSurfaceAssetUrl('already.surface.glb'))
  assert.throws(() => robotSurfaceAssetUrl('file.STL'))
})

test('changing cyan bands cannot change baked body/black geometry or normals', async () => {
  const asset=report.assets.find(a=>a.link==='elfin_link1' && a.quality==='low')
  const bytes=readFileSync(new URL(asset.source_file,root))
  const baseline=await readDisplayGeometry(bytes), changed=await readDisplayGeometry(bytes)
  const bands=ROBOT_BANDS.elfin_link1
  try {
    prepareSurfaceGeometry(baseline,asset.link,spec.links[asset.link],{sourceBytes:bytes,quality:'low'})
    // Turn the diagnostic band into a huge off-robot volume: visible cyan is
    // still exclusively the live shader's job, never serialized face colors.
    ROBOT_BANDS.elfin_link1=[[2,10,11,20,20,20,30,31]]
    prepareSurfaceGeometry(changed,asset.link,spec.links[asset.link],{sourceBytes:bytes,quality:'low'})
    assert.deepEqual(changed.groups,baseline.groups)
    assert.deepEqual(changed.index.array,baseline.index.array)
    assert.deepEqual(changed.userData,baseline.userData)
    for(const name of ['position','color','normal']) {
      assert.deepEqual(floatArray(changed.getAttribute(name)),floatArray(baseline.getAttribute(name)))
    }
  } finally {
    ROBOT_BANDS.elfin_link1=bands
    baseline.dispose()
    changed.dispose()
  }
})

test.after(() => {
  for (const quality of ['full','low']) {
    const rows = metrics.filter(row => row.quality === quality)
    console.log('SURFACE_MEASURED '+JSON.stringify({ quality, assets: rows.length,
      ...Object.fromEntries(['source_bytes','surface_bytes','processing_ms','surface_parse_clone_ms','hydrate_ms']
        .map(key => [key, rows.reduce((sum,row) => sum+row[key],0)])) }))
  }
})
