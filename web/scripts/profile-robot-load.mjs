// Read-only real-asset CPU profile. No server, renderer, build or asset writes.
// node --expose-gc web/scripts/profile-robot-load.mjs --rounds=3
import { readFileSync } from 'node:fs'
import { performance } from 'node:perf_hooks'
import { createHash } from 'node:crypto'
import os from 'node:os'

const stages = ['read', 'parse', 'cloneTransform', 'applyJointCaps', 'applyRobotFinishes', 'smoothRobotSurface']
const rounds = Number(process.argv.find(v => v.startsWith('--rounds='))?.split('=')[1] ?? 3)
if (!Number.isInteger(rounds) || rounds < 1 || rounds > 10) throw new Error('--rounds must be 1..10')
const begin = () => ({ wall: performance.now(), cpu: process.cpuUsage() })
const end = start => {
  const cpu = process.cpuUsage(start.cpu)
  return { wall_ms: performance.now() - start.wall, cpu_ms: (cpu.user + cpu.system) / 1000 }
}
const importsStart = begin()
const [{ GLTFLoader }, { MeshoptDecoder }, caps, surface] = await Promise.all([
  import('three/addons/loaders/GLTFLoader.js'), import('meshoptimizer/decoder'),
  import('../src/jointCaps.js'), import('../src/robotSurface.js'),
])
const imports = end(importsStart)
const decoderStart = begin()
await MeshoptDecoder.ready
const decoderReady = end(decoderStart)
const root = new URL('../public/models/elfin5/', import.meta.url)
const spec = JSON.parse(readFileSync(new URL('robot.json', root), 'utf8'))
const links = Object.entries(spec.links).filter(([, link]) => link.visual?.mesh)
const sourceHashes = Object.fromEntries(['jointCaps.js', 'robotSurface.js', 'scene.js'].map(name => [name,
  createHash('sha256').update(readFileSync(new URL('../src/' + name, import.meta.url))).digest('hex')]))
const samples = []
const median = values => {
  const v = values.slice().sort((a, b) => a - b)
  return v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2
}
const rounded = v => Math.round(v * 1000) / 1000
const glbJson = bytes => {
  if (bytes.readUInt32LE(0) !== 0x46546c67 || bytes.readUInt32LE(4) !== 2
      || bytes.readUInt32LE(16) !== 0x4e4f534a) throw new Error('Not a GLB 2 JSON chunk')
  const json = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'))
  if (json.images?.length || json.buffers?.some(buffer => buffer.uri)) {
    throw new Error('External/image resources require a separate browser profile')
  }
  return json
}
const geometryBytes = geometry => {
  const buffers = new Set()
  for (const a of [...Object.values(geometry.attributes), geometry.index].filter(Boolean)) {
    buffers.add((a.isInterleavedBufferAttribute ? a.data.array : a.array).buffer)
  }
  return [...buffers].reduce((sum, buffer) => sum + buffer.byteLength, 0)
}

for (let round = 0; round < rounds; round++) {
  // Alternate quality order after the first pass to reduce ordering bias.
  for (const quality of round % 2 ? ['low', 'full'] : ['full', 'low']) {
    globalThis.gc?.() // Outside all timings; keep prior sample garbage out.
    const rows = [], geometries = []
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder)
    for (const [name, link] of links) {
      const file = link.visual.mesh.replace(/\.STL$/i, quality === 'low' ? '.low.glb' : '.glb')
      const row = { name, file, quality, round, stages: {} }
      let start = begin()
      const bytes = readFileSync(new URL(file, root))
      // ArrayBuffer conversion is charged to reading, as in response.arrayBuffer.
      const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
      row.stages.read = end(start)
      row.file_bytes = bytes.byteLength
      row.sha256 = createHash('sha256').update(bytes).digest('hex')
      const metadata = glbJson(bytes)
      row.extensions = metadata.extensionsUsed ?? []
      start = begin()
      const loaded = await loader.parseAsync(buffer, '')
      row.stages.parse = end(start)
      start = begin()
      loaded.scene.updateMatrixWorld(true)
      let geometry
      loaded.scene.traverse(object => {
        if (object.isMesh && !geometry) geometry = object.geometry.clone().applyMatrix4(object.matrixWorld)
        object.geometry?.dispose()
        for (const material of (Array.isArray(object.material) ? object.material : [object.material]).filter(Boolean)) material.dispose()
      })
      row.stages.cloneTransform = end(start)
      if (!geometry) throw new Error('No geometry in ' + file)
      row.vertices_before = geometry.getAttribute('position').count
      row.triangles = (geometry.index?.count ?? row.vertices_before) / 3
      row.decoded_buffer_bytes_before = geometryBytes(geometry)
      geometries.push({ geometry, name, link, row })
      rows.push(row)
    }
    // Preserve production ordering: all parsing, then cap/finish/smooth per link.
    for (const { geometry, name, link, row } of geometries) {
      for (const [stage, action] of [
        ['applyJointCaps', () => caps.applyJointCaps(geometry, name, link)],
        ['applyRobotFinishes', () => caps.applyRobotFinishes(geometry, name)],
        ['smoothRobotSurface', () => surface.smoothRobotSurface(geometry)],
      ]) {
        const start = begin()
        action()
        row.stages[stage] = end(start)
      }
      if (!geometry.userData.jointCaps?.applied || !geometry.userData.robotFinishes || !geometry.userData.robotSurface) {
        throw new Error('Profiling a skipped processing stage: ' + JSON.stringify({ name, quality, data: geometry.userData }))
      }
      row.vertices_after = geometry.getAttribute('position').count
      row.decoded_buffer_bytes_after = geometryBytes(geometry)
      row.changed_normals = geometry.userData.robotSurface.changedNormals
      // Idempotent second calls quantify the existing in-instance cache only;
      // they are excluded from initial-load timings and do not survive refresh.
      const cached = begin()
      caps.applyJointCaps(geometry, name, link)
      caps.applyRobotFinishes(geometry, name)
      surface.smoothRobotSurface(geometry)
      row.cached_three_stages = end(cached)
      geometry.dispose()
    }
    const totals = Object.fromEntries(stages.map(stage => [stage,
      Object.fromEntries(['cpu_ms', 'wall_ms'].map(metric => [metric, rounded(rows.reduce((sum, row) => sum + row.stages[stage][metric], 0))]))]))
    samples.push({ quality, round, totals, rows })
    console.error(JSON.stringify({ progress: `${quality} sample ${round + 1}/${rounds}`, totals }))
  }
}

const summaries = ['full', 'low'].map(quality => {
  const group = samples.filter(sample => sample.quality === quality)
  const first = group[0].rows
  const timing = Object.fromEntries(stages.map(stage => [stage, Object.fromEntries(['cpu_ms', 'wall_ms'].map(metric => [metric, {
    first: group[0].totals[stage][metric], median: rounded(median(group.map(s => s.totals[stage][metric]))),
    min: Math.min(...group.map(s => s.totals[stage][metric])), max: Math.max(...group.map(s => s.totals[stage][metric])),
  }]))]))
  return { quality, file_bytes: first.reduce((s, r) => s + r.file_bytes, 0),
    triangles: first.reduce((s, r) => s + r.triangles, 0),
    vertices: first.reduce((s, r) => s + r.vertices_before, 0),
    decoded_buffer_bytes_before: first.reduce((s, r) => s + r.decoded_buffer_bytes_before, 0),
    decoded_buffer_bytes_after: first.reduce((s, r) => s + r.decoded_buffer_bytes_after, 0), timing,
    links: first.map(row => ({ name: row.name, file_bytes: row.file_bytes, triangles: row.triangles,
      vertices: row.vertices_before, timing: Object.fromEntries(stages.map(stage => [stage,
        rounded(median(group.map(s => s.rows.find(r => r.name === row.name).stages[stage].cpu_ms)))])) })),
    cached_three_stages_cpu_ms: rounded(median(group.map(s => s.rows.reduce((sum, row) => sum + row.cached_three_stages.cpu_ms, 0)))),
  }
})
console.log(JSON.stringify({ metadata: { node: process.version, platform: process.platform, arch: process.arch,
  cpu: os.cpus()[0]?.model, logical_cpus: os.cpus().length, rounds, explicit_gc: !!globalThis.gc,
  imports, decoder_ready: decoderReady, source_hashes: sourceHashes,
  limitations: 'Offline Node CPU work, sequential parse per asset; warm OS cache possible; no HTTP, renderer, GPU or shader timings. CPU includes GC within stage; not peak memory.' },
  summaries, samples }, null, 2))
