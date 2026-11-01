<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { MARKER_DICTIONARIES, visionRequest, readMarkerFile, cameraMarkerPayload, markerPoints, markerPreview } from '../visionMarkers.js'

const busy = ref(false), error = ref(''), capability = ref(null), active = ref(null)
const dictionary = ref(MARKER_DICTIONARIES[0]), stream = ref('color'), result = ref(null)
const preview = ref(''), message = ref('上传小图，或刷新后读取已开启的相机。'), fileInput = ref(null)
const dictionaries = computed(() => capability.value?.dictionaries || MARKER_DICTIONARIES)
const available = computed(() => capability.value?.available === true)
const cameraReady = computed(() => active.value?.state === 'streaming' && active.value?.streams?.includes(stream.value))
let controller = null, disposed = false

async function run(action) {
  if (busy.value || disposed) return
  busy.value = true; error.value = ''; controller = new AbortController()
  try { await action(controller.signal) }
  catch (err) {
    if (!disposed && err.name !== 'AbortError') {
      error.value = err.message
      if (err.code === 'capability_unavailable') capability.value = { available: false }
    }
  } finally { if (!disposed) busy.value = false; controller = null }
}

function checkCapability() {
  return run(async signal => {
    capability.value = null
    capability.value = await visionRequest('capabilities', { signal })
    if (!dictionaries.value.includes(dictionary.value)) dictionary.value = dictionaries.value[0]
  })
}

function refreshCamera() {
  return run(async signal => {
    active.value = null
    const response = await fetch('/api/cameras', { signal, cache: 'no-store' })
    if (!response.ok) throw new Error('读取相机状态失败')
    const current = (await response.json()).active || null
    active.value = current
    if (current?.state === 'error') {
      // 打不开/断流/被占用必须作为错误显示，不能降级成“没有开启相机”。
      error.value = '相机不可用：' + (current.error || '设备状态异常')
      message.value = ''
      return
    }
    message.value = current?.state === 'streaming' ? '相机已开启，可手动检测一帧。'
      : current?.state === 'starting' ? '相机正在启动，请稍候…' : '没有已开启的相机；可上传小图。'
  })
}

function detect(payloadFactory) {
  return run(async signal => {
    result.value = null; preview.value = ''
    const payload = await payloadFactory(signal)
    const value = await visionRequest('detect', { payload, signal })
    const src = markerPreview(value)
    if (disposed) return
    preview.value = src; result.value = value
    message.value = value.markers.length ? `检测到 ${value.markers.length} 个标记` : '未检测到标记，请确认字典、清晰度与完整边框。'
  })
}

function upload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (file) return detect(async signal => ({ source: 'upload', dictionary: dictionary.value, image_base64: await readMarkerFile(file, signal) }))
}

onMounted(checkCapability)
onBeforeUnmount(() => { disposed = true; controller?.abort() })
</script>

<template>
  <section class="panel vision-panel" :aria-busy="busy" aria-label="ArUco 视觉标记">
    <div class="panel-hd"><span class="panel-title">ArUco 视觉标记</span><span class="tag tag--mute">只读检测</span></div>
    <div class="panel-bd">
      <p class="boundary">未标定 · 仅输出 ID、角点和中心像素。不能作为机器人坐标，不下发运动。</p>
      <div class="vision-controls">
        <label>标记字典<select v-model="dictionary" class="select" :disabled="busy"><option v-for="name in dictionaries" :key="name">{{ name }}</option></select></label>
        <label>现有图像流<select v-model="stream" class="select" :disabled="busy"><option value="color">彩色</option><option value="infrared">红外</option></select></label>
      </div>
      <div class="vision-actions">
        <button class="btn" :disabled="busy" @click="checkCapability">检查能力</button>
        <button class="btn" :disabled="busy" @click="refreshCamera">刷新相机状态</button>
        <button class="btn" :disabled="busy || !available || !cameraReady" @click="detect(() => cameraMarkerPayload(active, stream, dictionary))">检测现有帧</button>
        <button class="btn" :disabled="busy || !available" @click="fileInput?.click()">上传图片检测</button>
        <input ref="fileInput" class="file-input" type="file" accept="image/jpeg,image/png" aria-label="上传 JPEG 或 PNG 小图" :disabled="busy || !available" @change="upload" />
      </div>
      <p class="hint">JPEG / PNG ≤ 1 MiB，最长边 ≤ 2048，总像素 ≤ 200 万。不会自动开启相机。</p>
      <p v-if="busy" role="status">正在处理，请稍候…</p>
      <p v-if="error" class="vision-error" role="alert">{{ error }}</p>
      <p v-if="capability?.available === false" role="status">ArUco 能力不可用，可检查当前环境配置后重试。</p>
      <p v-if="!busy && !error" role="status">{{ message }}</p>
      <template v-if="result">
        <p class="hint">{{ result.source === 'camera' ? `相机快照 · 帧 ${result.frame?.seq}` : '上传图片' }} · {{ result.image.width }} × {{ result.image.height }} px · {{ result.dictionary }} · 静态结果</p>
        <svg class="vision-preview" :viewBox="`0 0 ${result.image.width} ${result.image.height}`" role="img" aria-label="检测原图及标记边界，坐标原点为左上角">
          <image :href="preview" :width="result.image.width" :height="result.image.height" />
          <g v-for="(marker, index) in result.markers" :key="index">
            <polygon :points="markerPoints(marker.corners)" />
            <circle :cx="marker.centroid[0]" :cy="marker.centroid[1]" r="4" />
            <text :x="marker.centroid[0]" :y="marker.centroid[1] - 8" :font-size="Math.max(12, result.image.width / 50)">ID {{ marker.id }}</text>
          </g>
        </svg>
        <p class="hint">原图方向，无镜像；左上角为 (0, 0)，X 向右、Y 向下。边界与当前快照一致。</p>
        <div v-if="result.markers.length" class="vision-table"><table>
          <thead><tr><th>ID</th><th>中心 (px)</th><th>四角 (px，标记方向顺时针)</th></tr></thead>
          <tbody><tr v-for="(marker, index) in result.markers" :key="index"><td>{{ marker.id }}</td><td>{{ marker.centroid.map(v => v.toFixed(1)).join(', ') }}</td><td>{{ marker.corners.map(p => `(${p.map(v => v.toFixed(1)).join(', ')})`).join(' · ') }}</td></tr></tbody>
        </table></div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.vision-panel { min-width: 0; }
.panel-bd { display: grid; gap: 8px; padding: 10px; }
p { margin: 0; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; }
.boundary { color: var(--warn); }
.hint { color: var(--tx-3); font-size: 11px; }
.vision-controls { display: flex; flex-wrap: wrap; gap: 8px; }
.vision-controls label { display: grid; gap: 4px; font-size: 12px; min-width: 0; flex: 1 1 160px; }
.select { width: 100%; min-width: 0; }
.vision-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.file-input { display: none; }
.vision-error { color: var(--danger); }
.vision-preview { width: 100%; max-height: 520px; display: block; background: var(--bg-inset); }
.vision-preview image { image-orientation: none; }
.vision-preview polygon { fill: none; stroke: var(--acc); stroke-width: 2; vector-effect: non-scaling-stroke; }
.vision-preview circle { fill: var(--acc); }
.vision-preview text { fill: white; stroke: black; stroke-width: 3; paint-order: stroke; text-anchor: middle; }
.vision-table { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; text-align: left; font-size: 11px; }
th, td { padding: 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--tx-3); }
</style>
