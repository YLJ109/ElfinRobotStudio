// Explicit single shots only. This module has no robot/store dependencies.
export const MARKER_LIMITS = Object.freeze({ maxBytes: 1024 * 1024, maxPixels: 2_000_000, maxSide: 2048 })
export const MARKER_DICTIONARIES = Object.freeze(['DICT_4X4_50', 'DICT_5X5_100', 'DICT_6X6_250', 'DICT_7X7_50'])
const ROOT = '/api/vision/markers'

export async function visionRequest(path, { payload, signal, fetcher = globalThis.fetch } = {}) {
  if (!['capabilities', 'detect'].includes(path)) throw new Error('无效视觉接口')
  const response = await fetcher(`${ROOT}/${path}`, {
    method: payload === undefined ? 'GET' : 'POST', signal, cache: 'no-store',
    ...(payload === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  })
  let result
  try { result = await response.json() } catch { throw new Error('视觉接口未返回 JSON；请检查路由是否接入') }
  if (!response.ok || result?.ok !== true) {
    const error = new Error(result?.why || `视觉请求失败 (${response.status})`)
    error.code = result?.status || 'request_failed'
    throw error
  }
  return result
}

export function validateMarkerFile(file) {
  if (!file || !['image/jpeg', 'image/png'].includes(file.type)) throw new Error('请选择 JPEG / PNG 图片')
  if (!Number.isFinite(file.size) || file.size <= 0 || file.size > MARKER_LIMITS.maxBytes) throw new Error('图片须大于 0 且不超过 1 MiB')
  return file
}

export function readMarkerFile(file, signal) {
  validateMarkerFile(file)
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    const cleanup = () => signal?.removeEventListener('abort', cancel)
    const cancel = () => { reader.abort(); cleanup(); reject(new DOMException('已取消', 'AbortError')) }
    if (signal?.aborted) return cancel()
    signal?.addEventListener('abort', cancel, { once: true })
    reader.onload = () => { cleanup(); resolve(String(reader.result).split(',')[1]) }
    reader.onerror = () => { cleanup(); reject(new Error('图片读取失败')) }
    reader.onabort = () => { cleanup(); reject(new DOMException('已取消', 'AbortError')) }
    reader.readAsDataURL(file)
  })
}

export function cameraMarkerPayload(active, stream, dictionary) {
  if (!active || active.state !== 'streaming' || typeof active.session !== 'string' || !active.session
      || !['color', 'infrared'].includes(stream) || !active.streams?.includes(stream)) {
    throw new Error('请先在相机面板显式开启所选流，再刷新相机状态')
  }
  return { source: 'camera', session: active.session, stream, dictionary }
}

export function markerPoints(corners) {
  return corners.map(([x, y]) => `${x},${y}`).join(' ')
}

export function markerPreview(result) {
  const image = result?.image
  if (!image || !['image/jpeg', 'image/png'].includes(image.mime)
      || !Number.isInteger(image.width) || !Number.isInteger(image.height)
      || image.width <= 0 || image.height <= 0 || image.width > MARKER_LIMITS.maxSide || image.height > MARKER_LIMITS.maxSide
      || image.width * image.height > MARKER_LIMITS.maxPixels
      || typeof image.base64 !== 'string' || image.base64.length > 4 * Math.ceil(MARKER_LIMITS.maxBytes / 3)
      || !/^[A-Za-z0-9+/]+={0,2}$/.test(image.base64)) throw new Error('视觉预览无效')
  return `data:${image.mime};base64,${image.base64}`
}
