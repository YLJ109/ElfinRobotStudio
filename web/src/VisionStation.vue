<script setup>
import { computed, ref, nextTick, onMounted, onBeforeUnmount } from 'vue'
import VisionPanel from './panels/VisionPanel.vue'
import VisionObjectOverlay from './components/VisionObjectOverlay.vue'
import { useWorld } from './store.js'
import { visionWorkspace as v, createVisionPolling } from './visionWorkspace.js'

const world = useWorld(), desktop = globalThis.elfinDesktop
const windowState = ref({ maximized: false, focused: true }), windowError = ref(''), cameraError = ref('')
const controls = ref(null), imageFailed = ref(false)
const calibrationPoints = ref([])
const result = computed(() => v.result)
const stale = computed(() => v.stale || imageFailed.value)
const image = computed(() => result.value?.image)
const imageSrc = computed(() => image.value ? `data:${image.value.mime};base64,${image.value.base64}` : '')
const viewBox = computed(() => image.value ? `0 0 ${image.value.width} ${image.value.height}` : '0 0 640 480')
let disposed = false, stopVision, unsubscribe, cameraTimer, cameraController
async function windowAction(action) {
  try { await action?.(); windowError.value = '' } catch (error) { if (!disposed) windowError.value = error.message }
}
function titleDoubleClick(event) {
  if (!event.target.closest('button')) void windowAction(() => desktop?.toggleMaximize())
}
async function syncCalibrationPoints() {
  await nextTick()
  if (disposed) return
  calibrationPoints.value = Array.from(controls.value?.querySelectorAll('.cal-table tbody tr') || [])
    .flatMap((row, index) => {
      const inputs = row.querySelectorAll('input')
      const x = inputs[0]?.value, y = inputs[1]?.value
      return x !== undefined && y !== undefined && x !== '' && y !== '' && Number.isFinite(Number(x)) && Number.isFinite(Number(y))
        ? [{ index: index + 1, x: Number(x), y: Number(y) }] : []
    })
}
async function pollCameras() {
  if (disposed) return
  const previous = world.cameras
  cameraController = new AbortController()
  const timeout = setTimeout(() => cameraController?.abort(), 5000)
  try {
    const response = await fetch('/api/cameras', { cache: 'no-store', signal: cameraController.signal })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const cameras = await response.json()
    if (disposed) return
    // A user camera action may have returned a newer state while this GET was in flight.
    if (world.cameras === previous) world.cameras = cameras
    cameraError.value = ''
  } catch (error) { if (!disposed) cameraError.value = '相机状态读取失败：' + error.message }
  finally {
    clearTimeout(timeout); cameraController = null
    if (!disposed) cameraTimer = setTimeout(pollCameras, document.hidden ? 3000 : 1000)
  }
}
function pickCalibrationPoint(event) {
  // Keep VisionPanel's six-point form authoritative without changing its component API.
  const row = controls.value?.querySelector('.cal-table tbody tr.selected')
  if (!row || !image.value) return
  const matrix = event.currentTarget.getScreenCTM()
  if (!matrix) return
  const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse())
  if (point.x < 0 || point.y < 0 || point.x >= image.value.width || point.y >= image.value.height) return
  const inputs = row.querySelectorAll('input')
  for (const [index, value] of [point.x, point.y].entries()) {
    if (!inputs[index]) return
    inputs[index].value = String(Math.round(value * 100) / 100)
    inputs[index].dispatchEvent(new Event('input', { bubbles: true }))
  }
  row.nextElementSibling?.querySelector('button')?.click()
  void syncCalibrationPoints()
}
onMounted(() => {
  stopVision = createVisionPolling()
  void pollCameras()
  if (desktop) {
    unsubscribe = desktop.onWindowState(value => { windowState.value = value })
    void windowAction(async () => { const value = await desktop.getWindowState(); if (!disposed) windowState.value = value })
  }
})
onBeforeUnmount(() => {
  disposed = true; stopVision?.(); unsubscribe?.()
  clearTimeout(cameraTimer); cameraController?.abort()
})
</script>

<template>
  <div class="vision-station">
    <header class="station-title" :class="{ desktop: !!desktop, unfocused: !windowState.focused }" @dblclick="titleDoubleClick">
      <img src="/elfin-logo-arm-v1.png" width="34" height="34" alt="玖枢" />
      <div class="brand"><strong>玖枢 · 视觉工作站</strong><small>工业视觉 / 图像与工件</small></div>
      <span class="grow"></span>
      <span class="tag" :class="v.connected && !stale ? 'tag--ok' : 'tag--warn'">{{ !v.connected ? '正在连接视觉服务' : stale ? (imageFailed ? '图片导入失败' : '结果已过期') : '视觉服务已连接' }}</span>
      <div v-if="desktop" class="window-controls" aria-label="窗口控制" @dblclick.stop>
        <button aria-label="最小化窗口" title="最小化" @click="windowAction(() => desktop.minimize())"><svg viewBox="0 0 16 16"><path d="M3 10.5h10" /></svg></button>
        <button :aria-label="windowState.maximized ? '还原窗口' : '最大化窗口'" :title="windowState.maximized ? '还原' : '最大化'" @click="windowAction(() => desktop.toggleMaximize())"><svg viewBox="0 0 16 16"><path v-if="windowState.maximized" d="M5 5V3h8v8h-2M3 5h8v8H3z" /><path v-else d="M3.5 3.5h9v9h-9z" /></svg></button>
        <button class="window-close" aria-label="关闭视觉窗口" title="关闭视觉窗口" @click="windowAction(() => desktop.close())"><svg viewBox="0 0 16 16"><path d="m4 4 8 8m0-8-8 8" /></svg></button>
      </div>
    </header>
    <main class="station-body">
      <aside ref="controls" class="station-controls" aria-label="视觉设置" @input="syncCalibrationPoints" @click="syncCalibrationPoints"><VisionPanel @image-stale="imageFailed=$event" /></aside>
      <section class="station-view" aria-label="大幅视觉预览">
        <div class="view-heading"><strong>图像与识别结果</strong><span class="dim">{{ result ? (result.source === 'remote' ? '远程相机 · 只读' : result.source === 'camera' ? '真实相机' : result.source==='virtual'?'末端仿真相机':'离线图片') : '等待图像' }}</span><span class="grow"></span><span class="tag" :class="stale?'tag--warn':''">{{ result?.objects?.length || 0 }} 个工件{{ stale?' · 过期':'' }}</span></div>
        <div class="image-stage" :class="{ stale }">
          <svg v-if="image" :viewBox="viewBox" preserveAspectRatio="xMidYMid meet" role="img" aria-label="同帧原图、工件轮廓和中心点；标定页可点击大图取点" @pointerdown="pickCalibrationPoint">
            <image :href="imageSrc" :width="image.width" :height="image.height" />
            <VisionObjectOverlay :objects="result.objects" :width="image.width" :height="image.height" detailed />
            <g v-for="point in calibrationPoints" :key="point.index" class="calibration-point"><circle :cx="point.x" :cy="point.y" r="6" /><text :x="point.x+8" :y="point.y+18" :font-size="Math.max(12,image.width/65)">{{ point.index }}</text></g>
          </svg>
          <div v-else class="empty-state"><strong>{{ v.error ? '视觉结果暂不可用' : '等待图像' }}</strong><p>在左侧开启彩色相机，或切换到工件识别导入图片。</p></div>
          <span v-if="stale && image" class="stale-label">{{ imageFailed ? '图片导入失败 · 显示上一张结果' : '结果已过期' }}</span>
        </div>
        <footer class="view-footer"><span v-if="image" class="mono">帧 {{ result.frame_seq }} · {{ image.width }} × {{ image.height }} · {{ Number(result.elapsed_ms).toFixed(1) }} ms</span><span v-else class="dim">图像与轮廓使用同一帧识别结果</span><span class="grow"></span><span>{{ stale ? '旧帧，不用于控制' : result?.calibrated ? '平面标定有效' : '像素坐标' }}</span></footer>
        <p v-if="v.error || cameraError || windowError" class="station-error" role="status">{{ windowError || v.error || cameraError }}</p>
        <p v-if="result?.source==='remote'" class="dim">接收帧龄 {{Math.max(0,Date.now()-result.received_at*1000).toFixed(0)}} ms · 真实采集时间未知 · 不能用于机器人取放</p>
      </section>
    </main>
  </div>
</template>

<style scoped>
.vision-station{height:100%;position:relative;display:flex;flex-direction:column;background:var(--bg-base)}
.station-title{display:flex;align-items:center;gap:8px;min-height:54px;padding:6px 8px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,rgba(12,19,32,.92),rgba(7,11,20,.6));user-select:none;flex-shrink:0}
.station-title.desktop{-webkit-app-region:drag;box-shadow:inset 0 1px 0 #ffffff09,0 3px 10px #0005}.station-title.unfocused{filter:saturate(.75)}
.station-title img{object-fit:contain}.brand{display:flex;flex-direction:column}.brand strong{font-family:var(--font-display);font-size:15px}.brand small{color:var(--tx-3);font-size:10px}
.window-controls{display:flex;align-self:stretch;gap:2px;padding-left:9px;margin-left:4px;border-left:1px solid var(--line);-webkit-app-region:no-drag}
.window-controls button{display:grid;place-items:center;width:40px;padding:0;border:1px solid transparent;border-radius:0;color:var(--tx-2);background:linear-gradient(#14223080,#0a152280);box-shadow:inset 0 1px 0 #ffffff08,0 1px 2px #0003;cursor:pointer}
.window-controls button:hover{color:var(--acc);border-color:var(--line-2);background:#173140}.window-controls .window-close:hover{color:#fff;background:#a13950;border-color:#b64960}.window-controls button:focus-visible{outline:2px solid var(--acc);outline-offset:-3px}.window-controls svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.2}
.station-body{display:grid;grid-template-columns:400px minmax(0,1fr);flex:1;min-height:0}.station-controls{padding:8px;overflow:auto;border-right:1px solid var(--line);min-width:0;background:var(--bg-panel)}
.station-controls :deep(.vision-image){display:none}.station-controls :deep(.task-tabs){flex-wrap:wrap}.station-controls :deep(.vision-workspace){min-width:0}
.image-stage .calibration-point circle{stroke:var(--warn)}.image-stage .calibration-point text{fill:var(--warn)}
.station-view{display:flex;flex-direction:column;min-height:0;min-width:0;padding:8px;gap:8px}.view-heading,.view-footer{display:flex;align-items:center;gap:10px;flex-wrap:wrap;flex-shrink:0}.view-heading{min-height:32px}.view-footer{font-size:11px;color:var(--tx-2)}
.image-stage{position:relative;flex:1;min-height:180px;overflow:hidden;border:1px solid var(--line-2);background:var(--bg-void);display:flex;align-items:center;justify-content:center}.image-stage svg{display:block;width:100%;height:100%;position:absolute;inset:0;touch-action:none}.image-stage polygon,.image-stage circle{fill:none;stroke:var(--acc);stroke-width:2;vector-effect:non-scaling-stroke}.image-stage text{fill:var(--acc);paint-order:stroke;stroke:#000;stroke-width:2px}.held{opacity:.4;stroke-dasharray:5 4}.stale svg{opacity:.6}.stale-label{position:absolute;right:10px;top:10px;color:var(--warn);background:var(--bg-panel);padding:4px 8px}.empty-state{text-align:center;color:var(--tx-2);padding:10px}.empty-state strong{font-size:20px;color:var(--tx-1)}.station-error{margin:0;color:var(--warn);font-size:12px;overflow-wrap:anywhere}
@media(max-width:900px){.station-body{grid-template-columns:360px minmax(420px,1fr);overflow:auto}}
</style>
