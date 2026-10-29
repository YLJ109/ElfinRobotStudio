// Offline display assets only. Original GLBs and all production sources stay intact.
// node web/scripts/prepare-robot-surfaces.mjs
import { readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { performance } from 'node:perf_hooks'
import { Document, NodeIO } from '@gltf-transform/core'
import { EXTMeshoptCompression } from '@gltf-transform/extensions'
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { Matrix4 } from 'three'
import { applyJointCaps, applyRobotFinishes } from '../src/jointCaps.js'
import { smoothRobotSurface } from '../src/robotSurface.js'
import { ROBOT_SURFACE_ASSET_VERSION, ROBOT_SURFACE_RECIPE } from '../src/robotSurfaceAsset.js'

export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')
const arrayBuffer = bytes => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)

export async function readDisplayGeometry(bytes) {
  const loaded = await new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).parseAsync(arrayBuffer(bytes), '')
  loaded.scene.updateMatrixWorld(true)
  let geometry, count = 0
  loaded.scene.traverse(object => {
    if (object.isMesh) {
      count++
      if (!geometry) {
        geometry = object.geometry.clone()
        // Applying even identity normalizes normals again, changing the last
        // float32 bit. Preserve exact transport values for identity surface GLB.
        if (!object.matrixWorld.equals(new Matrix4())) geometry.applyMatrix4(object.matrixWorld)
      }
    }
    object.geometry?.dispose()
    for (const material of (Array.isArray(object.material) ? object.material : [object.material]).filter(Boolean)) material.dispose()
  })
  if (!geometry || count !== 1) {
    geometry?.dispose()
    throw new Error('Expected exactly one original display primitive')
  }
  return geometry
}

export function prepareSurfaceGeometry(geometry, linkName, specLink, { sourceBytes, quality }) {
  applyJointCaps(geometry, linkName, specLink)
  if (!geometry.userData.jointCaps?.applied) throw new Error('Unknown cap geometry: ' + linkName)
  // Capture the source triangle order AFTER cap ownership splits vertices.
  const sourceIndices = geometry.index ? Array.from(geometry.index.array) :
    Array.from({ length: geometry.getAttribute('position').count }, (_, i) => i)
  applyRobotFinishes(geometry, linkName)
  if (!geometry.userData.robotFinishes?.applied) throw new Error('Unknown finishes: ' + linkName)
  // Finishes also detects the original socket inserts. Retain only its black
  // ownership; discard cyan diagnostic ordering so mask edits cannot affect the
  // serialized body group. No cyan color/material/shader is serialized.
  const key = (a, b, c) => [a, b, c].sort((x, y) => x - y).join(',')
  const black = new Set(), current = geometry.index
  for (const group of geometry.groups) if (group.materialIndex === 2) {
    for (let i = group.start; i < group.start + group.count; i += 3) {
      black.add(key(current.getX(i), current.getX(i + 1), current.getX(i + 2)))
    }
  }
  const bodyIndices = [], blackIndices = []
  for (let i = 0; i < sourceIndices.length; i += 3) {
    const [a, b, c] = sourceIndices.slice(i, i + 3)
    const destination = black.has(key(a, b, c)) ? blackIndices : bodyIndices
    destination.push(a, b, c)
  }
  geometry.setIndex([...bodyIndices, ...blackIndices])
  geometry.clearGroups()
  if (bodyIndices.length) geometry.addGroup(0, bodyIndices.length, 0)
  if (blackIndices.length) geometry.addGroup(bodyIndices.length, blackIndices.length, 2)
  const portTriangles = geometry.userData.robotFinishes.portTriangles || 0
  geometry.userData.robotFinishes = { version: 2, applied: true, linkName,
    boundary: 'analytic-local-fragment', paintTriangles: bodyIndices.length / 3,
    chromeTriangles: 0, blackTriangles: blackIndices.length / 3, portTriangles }
  smoothRobotSurface(geometry)
  if (!geometry.userData.robotSurface) throw new Error('Smoothing skipped: ' + linkName)
  geometry.computeBoundingBox()
  geometry.userData.groups = geometry.groups.map(group => ({ ...group }))
  const declared = specLink.visual.bbox
  geometry.userData.robotSurfaceAsset = {
    version: ROBOT_SURFACE_ASSET_VERSION, recipe: ROBOT_SURFACE_RECIPE,
    variant: 'elfin5', linkName, quality,
    source: { file: specLink.visual.mesh.replace(/\.STL$/i, quality === 'low' ? '.low.glb' : '.glb'),
      sha256: sha256(sourceBytes), triangles: declared.triangles, min: declared.min, max: declared.max },
    min: geometry.boundingBox.min.toArray(), max: geometry.boundingBox.max.toArray(),
    vertexCount: geometry.getAttribute('position').count, indexCount: geometry.index.count,
  }
  return geometry
}

// Read normalized/interleaved attributes as exact decoded float32s. No second
// quantize/simplify/reorder transform. Meshopt QUANTIZE here is an encoder mode,
// not a call to the lossy quantize() transform; no FILTER option is used.
function floatAttribute(attribute) {
  const result = new Float32Array(attribute.count * attribute.itemSize)
  for (let i = 0; i < attribute.count; i++) for (let k = 0; k < attribute.itemSize; k++) {
    result[i * attribute.itemSize + k] = attribute.getComponent(i, k)
  }
  return result
}

export async function encodeSurfaceGeometry(geometry) {
  await MeshoptEncoder.ready
  const doc = new Document(), buffer = doc.createBuffer()
  const primitive = doc.createPrimitive().setExtras(structuredClone(geometry.userData))
  for (const [semantic, name] of [['POSITION', 'position'], ['NORMAL', 'normal'], ['COLOR_0', 'color']]) {
    const attribute = geometry.getAttribute(name)
    primitive.setAttribute(semantic, doc.createAccessor().setType('VEC3').setBuffer(buffer)
      .setArray(floatAttribute(attribute)))
  }
  primitive.setIndices(doc.createAccessor().setType('SCALAR').setBuffer(buffer)
    .setArray(new Uint32Array(geometry.index.array)))
  const mesh = doc.createMesh(geometry.userData.robotSurfaceAsset.linkName).addPrimitive(primitive)
  doc.createScene().addChild(doc.createNode().setMesh(mesh))
  doc.createExtension(EXTMeshoptCompression).setRequired(true)
    .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE })
  return new NodeIO().registerExtensions([EXTMeshoptCompression])
    .registerDependencies({ 'meshopt.encoder': MeshoptEncoder }).writeBinary(doc)
}

async function main() {
  if (process.argv.length > 2) throw new Error('No arguments: only the verified elfin5 assets are supported')
  const root = new URL('../public/models/elfin5/', import.meta.url)
  const spec = JSON.parse(readFileSync(new URL('robot.json', root), 'utf8'))
  // Fail rather than mix generations when another owner edits source mid-run.
  const sources = Object.fromEntries(['jointCaps.js', 'robotSurface.js', 'robotSurfaceAsset.js'].map(name =>
    [name, sha256(readFileSync(new URL('../src/' + name, import.meta.url)))]))
  const assets = []
  for (const quality of ['full', 'low']) for (const [name, link] of Object.entries(spec.links)) {
    if (!link.visual?.mesh) continue
    const file = link.visual.mesh.replace(/\.STL$/i, quality === 'low' ? '.low.glb' : '.glb')
    const original = readFileSync(new URL(file, root)), start = performance.now()
    const geometry = await readDisplayGeometry(original)
    try {
      prepareSurfaceGeometry(geometry, name, link, { sourceBytes: original, quality })
      const bytes = await encodeSurfaceGeometry(geometry)
      const output = file.replace(/\.glb$/, '.surface.glb')
      writeFileSync(new URL(output, root), bytes)
      assets.push({ link: name, quality, file: output, source_file: file,
        source_sha256: sha256(original), sha256: sha256(bytes), source_bytes: original.length,
        bytes: bytes.length, triangles: geometry.index.count / 3,
        groups: geometry.groups, elapsed_ms: performance.now() - start })
      console.log(output, bytes.length, 'bytes')
    } finally { geometry.dispose() }
  }
  for (const [name, hash] of Object.entries(sources)) {
    if (sha256(readFileSync(new URL('../src/' + name, import.meta.url))) !== hash) {
      throw new Error('Source changed while generating; discard this generation and rerun: ' + name)
    }
  }
  const report = { version: ROBOT_SURFACE_ASSET_VERSION, recipe: ROBOT_SURFACE_RECIPE,
    variant: 'elfin5', generated_at: new Date().toISOString(), sources,
    recipe_sources: { caps_sha256: sources['jointCaps.js'],
      smoothing_sha256: sha256(smoothRobotSurface.toString()) }, assets }
  writeFileSync(new URL('surface-report.json', root), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify({ assets: assets.length, report: fileURLToPath(new URL('surface-report.json', root)) }))
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main()
