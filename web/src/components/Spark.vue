<script setup>
import { ref, watch, onMounted, onBeforeUnmount, computed } from 'vue'

/* 极简 canvas 多线图：不引图表库（多 40KB 只为几条折线不值），
   只画"值随时间"，网格用两条参考线代替，异常段自动变红。 */
const props = defineProps({
  series: { type: Array, default: () => [] },      // [{name, color, data:[..], unit}]
  height: { type: Number, default: 62 },
  ref_lines: { type: Array, default: () => [] },   // [{v, label, color}]
})
const cv = ref(null)
let raf = null, observer = null, disposed = false

// 测量值不做隐式转换；无效采样保留索引，绘制时断线而非连接缺失区间。
const finite = (v) => typeof v === 'number' && Number.isFinite(v)
const rows = (value) => (Array.isArray(value) ? value : [])
  .filter((row) => row !== null && typeof row === 'object' && !Array.isArray(row))
const series = computed(() => rows(props.series))
const references = computed(() => rows(props.ref_lines).filter((r) => finite(r.v)))
const samples = (s) => Array.isArray(s.data) ? s.data : []

const span = computed(() => {
  let lo = Infinity, hi = -Infinity
  for (const s of series.value) for (const v of samples(s)) {
    if (!finite(v)) continue
    if (v < lo) lo = v
    if (v > hi) hi = v
  }
  for (const r of references.value) { if (r.v < lo) lo = r.v; if (r.v > hi) hi = r.v }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null
  if (hi - lo < 1e-6) { hi += 0.5; lo -= 0.5 }
  return { lo, hi }
})

function draw() {
  const c = cv.value
  if (!c) return
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const w = c.clientWidth, h = props.height
  if (!w) return
  if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr }
  const g = c.getContext('2d')
  g.setTransform(dpr, 0, 0, dpr, 0, 0)
  g.clearRect(0, 0, w, h)
  const sp = span.value
  g.strokeStyle = 'rgba(122,162,220,.13)'; g.lineWidth = 1
  g.beginPath(); g.moveTo(0, h - .5); g.lineTo(w, h - .5); g.stroke()
  if (!sp) {
    g.fillStyle = 'rgba(125,141,173,.75)'; g.font = '11px Cascadia Mono, Consolas, monospace'
    g.fillText('暂无数据', 6, h / 2 + 3)
    return
  }
  const y = (v) => h - 4 - ((v - sp.lo) / (sp.hi - sp.lo)) * (h - 10)
  for (const r of references.value) {
    g.strokeStyle = r.color || 'rgba(255,176,32,.45)'
    g.setLineDash([3, 3]); g.beginPath(); g.moveTo(0, y(r.v)); g.lineTo(w, y(r.v)); g.stroke()
    g.setLineDash([])
  }
  for (const s of series.value) {
    const d = samples(s)
    if (d.length < 2) continue
    g.strokeStyle = s.color; g.lineWidth = 1.4; g.beginPath()
    let started = false
    d.forEach((v, i) => {
      if (!finite(v)) { started = false; return }
      const x = (i / (d.length - 1)) * w
      if (!started) { g.moveTo(x, y(v)); started = true } else g.lineTo(x, y(v))
    })
    g.stroke()
  }
  g.fillStyle = 'rgba(125,141,173,.85)'; g.font = '9.5px Cascadia Mono, Consolas, monospace'
  g.fillText(sp.hi.toFixed(1), 2, 9)
  g.fillText(sp.lo.toFixed(1), 2, h - 4)
}

function scheduleDraw() {
  if (disposed || raf !== null) return
  raf = requestAnimationFrame(() => { raf = null; if (!disposed) draw() })
}
watch(() => [props.series, props.ref_lines, props.height], scheduleDraw, { deep: true })
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(scheduleDraw)
    if (cv.value) observer.observe(cv.value)
  }
  scheduleDraw()
})
onBeforeUnmount(() => {
  disposed = true
  observer?.disconnect()
  if (raf !== null) cancelAnimationFrame(raf)
  raf = null
})

const legend = computed(() => series.value.map((s) => ({
  name: s.name, color: s.color, unit: s.unit,
  last: (() => { const d = samples(s).filter(finite)
    return d.length ? d[d.length - 1].toFixed(1) : '--' })(),
})))
</script>

<template>
  <div class="spark">
    <canvas ref="cv" :style="{ height: height + 'px' }" />
    <div class="lg">
      <span v-for="l in legend" :key="l.name" class="li">
        <i :style="{ background: l.color }"></i>{{ l.name }}
        <b class="mono">{{ l.last }}<u v-if="l.unit">{{ l.unit }}</u></b>
      </span>
    </div>
  </div>
</template>

<style scoped>
.spark { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
canvas { width: 100%; display: block; }
.lg { display: flex; flex-wrap: wrap; gap: 6px; }
.li { display: inline-flex; align-items: center; gap: 3px; font-size: 10.5px; color: var(--tx-3); }
.li i { width: 8px; height: 2px; border-radius: 1px; }
.li b { color: var(--tx-1); font-size: 11px; font-variant-numeric: tabular-nums; }
.li u { text-decoration: none; color: var(--tx-3); font-size: 9px; margin-left: 1px; }
</style>
