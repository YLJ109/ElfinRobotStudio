import fs from 'node:fs'
import path from 'node:path'
import { Document, NodeIO } from '@gltf-transform/core'
import { ALL_EXTENSIONS } from '@gltf-transform/extensions'
import { weld, meshopt, simplify, getBounds, normals } from '@gltf-transform/functions'
import { MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier } from 'meshoptimizer'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'

await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready])
const root = path.resolve('public/models/elfin5')
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder,
})
const report = []
for (const filename of fs.readdirSync(root).filter((name) => name.endsWith('.STL'))) {
  const input = fs.readFileSync(path.join(root, filename))
  const geometry = new STLLoader().parse(input.buffer.slice(input.byteOffset, input.byteOffset + input.byteLength))
  geometry.computeVertexNormals()
  geometry.computeBoundingBox()
  for (const low of [false, true]) {
    const document = new Document()
    const buffer = document.createBuffer()
    const primitive = document.createPrimitive()
    for (const [semantic, name] of [['POSITION', 'position'], ['NORMAL', 'normal']]) {
      if (low && semantic === 'NORMAL') continue
      primitive.setAttribute(semantic, document.createAccessor().setType('VEC3')
        .setBuffer(buffer).setArray(new Float32Array(geometry.attributes[name].array)))
    }
    const mesh = document.createMesh().addPrimitive(primitive)
    const scene = document.createScene().addChild(document.createNode().setMesh(mesh))
    document.getRoot().setDefaultScene(scene)
    await document.transform(weld())
    if (low) await document.transform(simplify({ simplifier: MeshoptSimplifier, ratio: .3, error: .001, lockBorder: true }), normals())
    await document.transform(meshopt({ encoder: MeshoptEncoder, level: 'medium' }))
    const output = filename.replace('.STL', low ? '.low.glb' : '.glb')
    await io.write(path.join(root, output), document)
    const decoded = await io.read(path.join(root, output))
    const bounds = getBounds(decoded.getRoot().listScenes()[0])
    const original = geometry.boundingBox
    const err = Math.max(...bounds.min.map((v, i) => Math.abs(v - original.min.getComponent(i))),
      ...bounds.max.map((v, i) => Math.abs(v - original.max.getComponent(i))))
    if (!low && err > .03) throw new Error(`Model bounds changed by ${err}: ${filename}`)
    report.push({ filename: output, source_bytes: input.length, bytes: fs.statSync(path.join(root, output)).size,
      lod: low ? 'low' : 'full', bounds_error_stl_units: err })
  }
  geometry.dispose()
}
fs.writeFileSync(path.join(root, 'asset-report.json'), JSON.stringify(report, null, 2))
console.log(JSON.stringify({ full_bytes: report.filter((r) => r.lod === 'full').reduce((a, r) => a + r.bytes, 0),
  low_bytes: report.filter((r) => r.lod === 'low').reduce((a, r) => a + r.bytes, 0), assets: report.length }))
