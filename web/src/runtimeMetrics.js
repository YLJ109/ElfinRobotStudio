// Local duration-only telemetry. No Vue, network, storage, URLs or request bodies.
export const METRIC_NAMES = Object.freeze([
  'http.request', 'ik.roundtrip', 'preview.roundtrip', 'state.decode', 'state.interval',
  'scene.frame', 'scene.update', 'scene.render', 'longtask', 'scene.load', 'scene.prepare',
])
export const METRIC_WINDOW_SIZE = 512
const windows = new Map(METRIC_NAMES.map(name => [name, { values: new Float64Array(METRIC_WINDOW_SIZE), n: 0, cursor: 0 }]))

export function recordMetric(name, ms) {
  const window = windows.get(name)
  if (!window || typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return false
  window.values[window.cursor] = ms
  window.cursor = (window.cursor + 1) % METRIC_WINDOW_SIZE
  window.n = Math.min(METRIC_WINDOW_SIZE, window.n + 1)
  return true
}

export function snapshotMetrics() {
  return Object.fromEntries(METRIC_NAMES.map(name => {
    const { values, n } = windows.get(name)
    if (!n) return [name, { n: 0, p50: null, p95: null, max: null }]
    const sorted = Array.from(values.subarray(0, n)).sort((a, b) => a - b)
    return [name, { n, p50: sorted[Math.ceil(n * .5) - 1], p95: sorted[Math.ceil(n * .95) - 1], max: sorted[n - 1] }]
  }))
}

export function resetMetrics() {
  for (const window of windows.values()) { window.values.fill(0); window.n = 0; window.cursor = 0 }
}
