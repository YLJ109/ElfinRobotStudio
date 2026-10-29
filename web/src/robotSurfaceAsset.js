import { Box3 } from 'three'

// Increment when the baked geometry contract changes, NOT when cyan shader
// bands change. The shader is always supplied by the current robotSurface.js.
export const ROBOT_SURFACE_ASSET_VERSION = 1
export const ROBOT_SURFACE_RECIPE = 'body-black-smooth-v1'
const SHA256 = /^[a-f0-9]{64}$/
const LINKS = new Set(['elfin_base', ...Array.from({ length: 6 }, (_, i) => `elfin_link${i + 1}`)])
const vector = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite)
const close = (a, b, tolerance) => vector(a) && vector(b) && a.every((v, i) => Math.abs(v - b[i]) <= tolerance)

export function robotSurfaceAssetUrl(url) {
  if (typeof url !== 'string' || !/\.glb(?:[?#]|$)/i.test(url) || /\.surface\.glb(?:[?#]|$)/i.test(url)) {
    throw new Error('Expected an original .glb URL')
  }
  return url.replace(/\.glb(?=[?#]|$)/i, '.surface.glb')
}

/**
 * Restore only the supported display contract. Call on the cloned mesh-local
 * geometry, before material creation. expected.sourceSha256 must come from the
 * current generation report (assets[name/quality].source_sha256), never from
 * geometry.userData itself. Return false => discard and load the original GLB.
 * Does not import or execute caps/finishes/smoothing, or bake a cyan band.
 */
export function hydrateRobotSurfaceGeometry(geometry, linkName, specLink,
  { sourceSha256, quality, variant = 'elfin5' } = {}) {
  const data = geometry?.userData, meta = data?.robotSurfaceAsset
  const declared = specLink?.visual?.bbox
  if (!meta || variant !== 'elfin5' || !LINKS.has(linkName)
      || !SHA256.test(sourceSha256 || '') || !['full', 'low'].includes(quality)
      || meta.version !== ROBOT_SURFACE_ASSET_VERSION || meta.recipe !== ROBOT_SURFACE_RECIPE
      || meta.variant !== variant || meta.linkName !== linkName || meta.quality !== quality
      || meta.source?.sha256 !== sourceSha256 || meta.source?.triangles !== declared?.triangles
      || meta.source?.file !== specLink?.visual?.mesh?.replace(/\.STL$/i, quality === 'low' ? '.low.glb' : '.glb')
      || !close(meta.source?.min, declared?.min, 0) || !close(meta.source?.max, declared?.max, 0)) return false
  const position = geometry.getAttribute('position'), normal = geometry.getAttribute('normal')
  const color = geometry.getAttribute('color'), index = geometry.index
  if (!position || position.itemSize !== 3 || !normal || normal.itemSize !== 3
      || !color || color.itemSize !== 3 || normal.count !== position.count || color.count !== position.count
      || !index || index.count % 3 || meta.vertexCount !== position.count || meta.indexCount !== index.count
      || !Number.isSafeInteger(position.count) || position.count < 3 || position.count > 2000000) return false
  const groups = data.groups
  if (!Array.isArray(groups) || groups.length < 1 || groups.length > 2) return false
  let end = 0
  const slots = new Set()
  for (const group of groups) {
    if (!group || !Number.isSafeInteger(group.start) || !Number.isSafeInteger(group.count)
        || group.start !== end || group.count <= 0 || group.start % 3 || group.count % 3
        || ![0, 2].includes(group.materialIndex) || slots.has(group.materialIndex)) return false
    end += group.count
    slots.add(group.materialIndex)
  }
  if (end !== index.count || !slots.has(0)) return false
  const caps = data.jointCaps, finishes = data.robotFinishes, surface = data.robotSurface
  if (caps?.version !== 1 || caps.applied !== true || caps.linkName !== linkName
      || finishes?.version !== 2 || finishes.applied !== true || finishes.linkName !== linkName
      || surface?.version !== 1 || surface.creaseDegrees !== 35
      || !Number.isSafeInteger(surface.changedNormals) || surface.changedNormals < 0
      || surface.changedNormals > position.count) return false
  const box = new Box3().setFromBufferAttribute(position)
  if (!close(box.min.toArray(), meta.min, 1e-7) || !close(box.max.toArray(), meta.max, 1e-7)
      || !close(meta.min, declared.min, .0004) || !close(meta.max, declared.max, .0004)) return false
  // Whitelist supported metadata; arbitrary primitive extras are not promoted.
  geometry.clearGroups()
  for (const group of groups) geometry.addGroup(group.start, group.count, group.materialIndex)
  geometry.userData = structuredClone({ robotSurfaceAsset: meta, jointCaps: caps,
    robotFinishes: finishes, robotSurface: surface })
  return true
}
