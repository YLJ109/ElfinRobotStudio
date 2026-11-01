<script setup>
import { ref, watch, onBeforeUnmount, computed } from 'vue'
import { palmInView, imagePixelAt, imageBoundsInView, palmMarkerDiameter, handLandmarksInView } from '../handGeometry.js'
import { publishHandFollowInput, createCameraMultipartParser, measuredFrameRate, createPalmTracker, createHandOverlayPresentation, getHandFollowDepthRange } from '../handFollowInput.js'
import { createHandDisplayHold } from '../handDisplayHold.js'
import { handPipeline, handPipelineObservation } from '../handPipeline.js'
const props = defineProps({ session: String, stream: String, measure: Boolean, hands: Boolean, mapping: Boolean,
  frameSeq: Number, frameAt: Number, actualFps: Number })
const emit = defineEmits(['resolution'])
const url = ref(''), error = ref(''), measurement = ref(''), image = ref(null)
const frameBox = ref(null), palm = ref(null), handStatus = ref(''), detectionFps = ref(0)
const handLandmarks = ref([]), trackingState = ref('lost')
const palmTracker = createPalmTracker()
const overlayPresentation = createHandOverlayPresentation()
const displayHold = createHandDisplayHold()
const shownPalm = ref(null), shownLandmarks = ref([])
let overlayFrame = null, overlaySequence = 0, displayExpiryTimer = null
const detectorDelegate = ref(''), inferenceMs = ref(null)
const decodeMs = ref(null), roundTripMs = ref(null), inferenceSize = ref(null)
const bounds = ref({width:0,height:0})
const imageSize = ref({width:0,height:0})
const marker = computed(() => palmInView(shownPalm.value, bounds.value.width, bounds.value.height, imageSize.value.width, imageSize.value.height, true))
const markerDiameter = computed(() => palmMarkerDiameter(shownPalm.value?.palm_scale,getHandFollowDepthRange()))
const handOverlay = computed(() => handLandmarksInView(shownLandmarks.value,
  bounds.value.width,bounds.value.height,imageSize.value.width,imageSize.value.height,true))
const contentRect = computed(() => imageBoundsInView(bounds.value.width, bounds.value.height, imageSize.value.width, imageSize.value.height))
const mappingStyle = computed(() => {
  const rect = contentRect.value
  return rect ? {left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px'} : null
})
const hudStyle = computed(() => {
  const rect = contentRect.value, inset = props.mapping ? 24 : 6
  return rect ? {left:rect.left+inset+'px', top:rect.top+6+'px', width:Math.max(0,rect.width-inset*2)+'px'} : null
})
const pageVisible = ref(!document.hidden), inView = ref(true), fresh = ref(false)
const canStream = computed(() => !!props.session && !!props.stream && pageVisible.value && inView.value)
const measuredFps = computed(() => fresh.value && Number.isFinite(props.actualFps) && props.actualFps >= 0 ? props.actualFps.toFixed(1) : '—')
let stopPipeline = null, pipelinePacket = null, detectorReady = false, overlayObserver = null
let visibilityObserver = null, frameTimer = null, retryTimer = null, cursorTimer = null
let revision = 0, disposed = false, lastProgressAt = -Infinity, handFailed = false
let resultTimes = [], latestFrame = null, streamAbort = null
let publishedFrame = null, publishedSize = null
let lastObservationSeq = 0, lastDetectionKey = null, displayedSeq = 0, displayPending = false
const followSource = Symbol('camera-frame')
let handTimestamp = 0, handFrameSeq = 0
const FRAME_TIMEOUT = 1500
function drawOverlay() {
  overlayFrame = null
  if (disposed || !fresh.value) return
  if (!displayHold.read(performance.now()).point) { clearOverlay(); return }
  const visual = overlayPresentation.read(performance.now())
  shownPalm.value = visual.point; shownLandmarks.value = visual.landmarks
  if (visual.animating && typeof requestAnimationFrame === 'function') overlayFrame = requestAnimationFrame(drawOverlay)
}
function clearOverlay() {
  clearTimeout(displayExpiryTimer); displayExpiryTimer = null; displayHold.reset()
  if (overlayFrame != null) cancelAnimationFrame(overlayFrame)
  overlayFrame = null; overlaySequence = 0; overlayPresentation.reset()
  shownPalm.value = null
  if (shownLandmarks.value.length) shownLandmarks.value = []
}
function refreshPalm() {
  const state = palmTracker.read(performance.now())
  palm.value = state.point; handLandmarks.value = state.landmarks; trackingState.value = state.status
  handTimestamp = state.timestamp; handFrameSeq = state.frameSeq
  // Read the display cache independently; motion fields above keep the tracker's
  // original timestamp, frameSeq and loss deadline. Empty reads never renew it.
  const visualState = displayHold.update(state,performance.now())
  if (!visualState.point) clearOverlay()
  else if (visualState.frameSeq !== overlaySequence) {
    overlaySequence = visualState.frameSeq
    clearTimeout(displayExpiryTimer)
    displayExpiryTimer = setTimeout(() => {
      displayExpiryTimer = null
      if (!displayHold.read(performance.now()).point) clearOverlay()
    },Math.max(1,visualState.expiresAt-performance.now()))
    if (typeof requestAnimationFrame === 'function') {
      const visual = overlayPresentation.update(visualState,performance.now())
      shownPalm.value = visual.point; shownLandmarks.value = visual.landmarks
      if (visual.animating && overlayFrame == null) overlayFrame = requestAnimationFrame(drawOverlay)
    } else { shownPalm.value = visualState.point; shownLandmarks.value = visualState.landmarks }
  }
  if (detectorReady && !handFailed) handStatus.value = state.status === 'tracking' ? '掌心已定位'
    : state.status === 'holding' ? '短暂光标保持 · 等待新检测' : '未检测到手掌'
  return state
}
function schedulePalmExpiry() {
  clearTimeout(cursorTimer)
  const state = refreshPalm()
  cursorTimer = state.point ? setTimeout(() => {
    publishFollow(); schedulePalmExpiry()
  },Math.max(1,state.nextUpdateAt-performance.now())) : null
}
function publishFollow() {
  if (disposed || !fresh.value || !frameIsFresh() || props.stream !== 'color') {
    publishedFrame = publishedSize = null
    publishHandFollowInput(null, followSource); return
  }
  const time = performance.now()
  refreshPalm()
  resultTimes = resultTimes.filter(stamp => time - stamp < 1000)
  detectionFps.value = props.hands ? measuredFrameRate(resultTimes, time) : 0
  // Inference runs on the raw image. Publish the one visible horizontal mirror
  // here, so marker, numbered corners and the backend use the same screen order.
  const point = palm.value ? {...palm.value,x:1-palm.value.x,dx:-palm.value.dx} : null
  publishedFrame = latestFrame; publishedSize = imageSize.value
  publishHandFollowInput({ session:props.session, stream:props.stream, hands:props.hands,
    fresh:true, width:imageSize.value.width, height:imageSize.value.height,
    point, timestamp:handTimestamp, trackingState:trackingState.value,
    observationAgeMs:point ? Math.max(0,time-handTimestamp) : null,
    detectionFps:detectionFps.value, detectorStatus:handStatus.value,
    detectorDelegate:detectorDelegate.value, inferenceMs:inferenceMs.value,
    decodeMs:decodeMs.value,roundTripMs:roundTripMs.value,inferenceSize:inferenceSize.value,
    frameSeq:handFrameSeq || latestFrame?.seq, frameUpdatedAt:lastProgressAt,
    detectorError:handFailed ? handStatus.value : '' }, followSource)
}
function stopHands() {
  stopPipeline?.(); stopPipeline = null; pipelinePacket = null; detectorReady = false
  clearTimeout(cursorTimer); cursorTimer = null
  clearOverlay(); palmTracker.reset(); palm.value = null; handLandmarks.value = []; trackingState.value = 'lost'; handStatus.value = ''
  handTimestamp = 0; handFrameSeq = 0; lastObservationSeq = 0; lastDetectionKey = null; resultTimes = []; detectionFps.value = 0
  detectorDelegate.value = ''; inferenceMs.value = null; decodeMs.value = roundTripMs.value = inferenceSize.value = null
  publishFollow()
}
function consumeDetection() {
  if (!props.hands || !frameIsFresh() || !pipelinePacket) return
  const now = performance.now(), observation = handPipelineObservation(pipelinePacket, props.session, now)
  // JPEG delivery/decoding and backend inference have independent latency.
  // Session, capture time and sequence are verified by the result contract and
  // backend; a lagging preview must not starve valid hand input or measured FPS.
  // Image decode, camera delivery and polling can all inspect one result.
  // Only a new result or a change of freshness/error state updates the tracker.
  const detectionKey = JSON.stringify([observation.frameSeq,observation.controlValid,observation.displayValid,observation.error])
  if (detectionKey === lastDetectionKey) return
  lastDetectionKey = detectionKey
  handFailed = !!observation.error
  detectorReady = observation.controlValid
  handStatus.value = observation.error ? '手掌检测不可用：' + observation.error : observation.controlValid ? '掌心已定位' :
    observation.displayValid ? '结果已过期，仅保留显示' : '等待有效手掌检测结果'
  if (observation.controlValid) palmTracker.update(observation, now)
  else palmTracker.reset()
  if (observation.frameSeq > lastObservationSeq) {
    lastObservationSeq = observation.frameSeq
    // Fresh skeletons use the same time-based filtered landmarks as the palm;
    // raw display-only results are never copied into the control tracker.
    if (observation.displayValid && !observation.controlValid) displayHold.observe(observation, now)
    if (pipelinePacket.snapshot?.result?.valid) resultTimes.push(now)
  }
  inferenceMs.value = Number.isFinite(observation.inferenceMs) ? observation.inferenceMs : null
  roundTripMs.value = observation.roundTripMs ?? null; detectorDelegate.value = '工作站'
  publishFollow(); schedulePalmExpiry()
}
function startHands() {
  if (!props.hands || props.stream !== 'color' || !fresh.value || stopPipeline) return
  handStatus.value = '等待工作站检测结果'
  stopPipeline = handPipeline.subscribe(packet => {
    pipelinePacket = packet
    if (!packet) { palmTracker.reset(); handFailed = true; handStatus.value = '检测连接中断或来源已变化'; publishFollow(); return }
    consumeDetection()
  })
}
function trackFrame() { consumeDetection() }
watch(() => [props.hands, props.stream, props.session], () => {
  stopHands(); handFailed = false
}, {flush:'sync'})
watch(() => props.session, () => {
  lastProgressAt = -Infinity; imageSize.value = {width:0,height:0}; fresh.value = false
}, {flush:'sync'})
watch(imageSize, size => {
  if (size.width > 0 && size.height > 0) emit('resolution', {...size})
})
watch(frameBox, (box) => {
  overlayObserver?.disconnect(); visibilityObserver?.disconnect()
  if (!box) return
  overlayObserver = new ResizeObserver(() => { bounds.value = {width:box.clientWidth,height:box.clientHeight} })
  overlayObserver.observe(box)
  visibilityObserver = new IntersectionObserver(([entry]) => { inView.value = entry.isIntersecting })
  visibilityObserver.observe(box)
})
function frameIsFresh() {
  const age = Date.now() - (latestFrame?.stamp || 0) * 1000
  return canStream.value && !!url.value && age >= -100 && age < FRAME_TIMEOUT &&
    performance.now() - lastProgressAt < FRAME_TIMEOUT
}
function showLatestFrame() {
  if (!latestFrame || displayPending || latestFrame.seq <= displayedSeq) return
  const previous = url.value
  displayedSeq = latestFrame.seq; displayPending = true
  url.value = URL.createObjectURL(latestFrame.blob)
  if (previous) URL.revokeObjectURL(previous)
}
function imageLoaded() {
  displayPending = false
  const img = image.value
  if (img?.naturalWidth) {
    if (imageSize.value.width !== img.naturalWidth || imageSize.value.height !== img.naturalHeight)
      imageSize.value = {width:img.naturalWidth,height:img.naturalHeight}
    fresh.value = frameIsFresh()
    if (fresh.value) {
      startHands(); void trackFrame()
      // acceptFrame already published this capture. The independent preview
      // decode must not notify every subscriber a second time. Initial sizing
      // and resolution changes still publish immediately.
      if (latestFrame !== publishedFrame || imageSize.value !== publishedSize) publishFollow()
    }
  }
  showLatestFrame()
}
function acceptFrame(packet) {
  if (disposed || !canStream.value || (latestFrame && packet.seq <= latestFrame.seq)) return
  const receivedAt = performance.now()
  // Keep capture age separate from the strictly increasing MediaPipe clock.
  // A delayed network JPEG must not look like a newly measured palm on arrival.
  const observedAt = receivedAt - Math.max(0,Date.now()-packet.stamp*1000)
  latestFrame = {...packet,receivedAt,observedAt}
  lastProgressAt = latestFrame.receivedAt
  showLatestFrame()
  // 每个新采集帧都可检测；忙时由latestFrame覆盖旧帧，完成后立即取最新帧。
  fresh.value = frameIsFresh() && imageSize.value.width > 0
  if (fresh.value) { startHands(); void trackFrame(); publishFollow() }
}
function tick(seq) {
  if (disposed || seq !== revision || !canStream.value) return
  const img = image.value
  if (img?.naturalWidth && (imageSize.value.width !== img.naturalWidth || imageSize.value.height !== img.naturalHeight)) {
    imageSize.value = {width:img.naturalWidth,height:img.naturalHeight}
  }
  // Each blob URL starts another image decode. naturalWidth can briefly be 0
  // between two healthy frames; that must not tear down the expensive detector.
  // Session changes/clear() reset image dimensions and stream freshness owns
  // the real timeout; image errors still retire the stream explicitly.
  fresh.value = frameIsFresh() && imageSize.value.width>0 && imageSize.value.height>0
  if (!fresh.value) {
    if (stopPipeline) stopHands()
    palm.value = null; measurement.value = ''
    if (!handFailed) handStatus.value = '等待有效图像帧…'
  } else if (props.hands && props.stream === 'color') {
    startHands(); trackFrame()
  }
  publishFollow()
  // Only freshness, retry and HUD housekeeping; dedup prevents re-inference.
  frameTimer = setTimeout(() => tick(seq), 100)
}
function clear() {
  streamAbort?.abort(); streamAbort = null
  clearTimeout(frameTimer); clearTimeout(retryTimer); fresh.value = false; stopHands()
  // Explicitly disconnect the continuous response when hidden or unmounted.
  image.value?.removeAttribute('src')
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''; fresh.value = false; latestFrame = null; displayedSeq = 0; displayPending = false
}
async function startStream() {
  revision++; clear(); error.value = ''; handFailed = false
  if (disposed || !canStream.value) return
  const seq = revision, controller = new AbortController()
  streamAbort = controller
  tick(seq)
  let reader = null
  try {
    const response = await fetch(`/api/cameras/stream.mjpg?session=${encodeURIComponent(props.session)}&stream=${encodeURIComponent(props.stream)}&fps=0`,
      {signal:controller.signal,cache:'no-store'})
    if (disposed || seq !== revision || controller.signal.aborted) {
      await response.body?.cancel().catch(() => {})
      return
    }
    if (!response.ok || !response.body) throw new Error('图像流不可用')
    reader = response.body.getReader()
    const parser = createCameraMultipartParser(packet => {
      if (seq === revision && !controller.signal.aborted) acceptFrame(packet)
    })
    while (!controller.signal.aborted) {
      const {done,value} = await reader.read()
      if (done) break
      if (seq !== revision || controller.signal.aborted) return
      parser.push(value)
    }
    if (seq === revision && !controller.signal.aborted) streamFailed()
  } catch (e) {
    if (!disposed && seq === revision && !controller.signal.aborted) streamFailed()
  } finally {
    if (reader) { try { await reader.cancel() } catch { /* retired transport */ } reader.releaseLock() }
  }
}
function streamFailed() {
  const seq = ++revision
  clear(); error.value = '图像流中断，正在重连…'
  if (canStream.value && !disposed) retryTimer = setTimeout(() => { if (seq === revision) startStream() }, 1000)
}
// 画面组件从不开启检测，只负责渲染；用专属标记声明"我这一方"。开启方是
// 相机面板（cameraSource.js 的 camera-source）。两者来源与会话完全相同，
// 只靠这两项无法分区，卸载画面就会把面板刚拉起的推理一起关掉 —— 跟随因此
// 在切标签页时被取消，再开又要重新等检测器启动。
const FRAME_OWNER = 'camera-frame'
function visibilityChanged() {
  pageVisible.value = !document.hidden
  if(document.hidden && props.hands)void handPipeline.release('local',props.session,FRAME_OWNER)
}
async function measureAt(event) {
  if (!props.measure || !image.value || !fresh.value || !frameIsFresh()) return
  const rect = image.value.getBoundingClientRect()
  const pixel = imagePixelAt(event.clientX-rect.left,event.clientY-rect.top,rect.width,rect.height,image.value.naturalWidth,image.value.naturalHeight,true)
  if (!pixel) return
  const {x,y} = pixel
  const seq = revision
  try {
    const r = await (await fetch(`/api/cameras/measure?session=${encodeURIComponent(props.session)}&x=${x}&y=${y}`)).json()
    if (seq !== revision || !frameIsFresh()) return
    measurement.value = r.ok ? `(${x},${y}) 距离 ${r.distance_mm}mm · XYZ ${r.xyz_mm.join(', ')}mm` : r.why
  } catch (e) { if (seq === revision) measurement.value = e.message }
}
watch(() => [props.session, props.stream, canStream.value], () => { measurement.value = ''; startStream() }, { immediate: true, flush:'sync' })
document.addEventListener('visibilitychange', visibilityChanged)
onBeforeUnmount(() => {
  disposed = true; revision++; clear(); overlayObserver?.disconnect(); visibilityObserver?.disconnect()
  document.removeEventListener('visibilitychange', visibilityChanged)
  // 只在"这份检测本来就是自己开的"时才关：后端 pipeline 是全局单例，
  // 别人的检测被本组件的卸载顺手掐掉，跟随会当场断掉。
  if(props.hands)void handPipeline.release('local',props.session,FRAME_OWNER)
})
</script>
<template>
  <figure class="camera-frame">
    <figcaption v-if="fresh && hudStyle" :style="hudStyle"><span>{{ ({color:'彩色',depth:'深度',infrared1:'左红外',infrared2:'右红外',infrared:'红外'})[stream] || stream }} · 镜像</span><span class="fps">采集 {{measuredFps}} / 检测 {{hands ? detectionFps.toFixed(1) : '—'}} fps</span></figcaption>
    <div ref="frameBox" class="frame-box">
      <img v-if="url" v-show="fresh" ref="image" :src="url" :alt="stream + ' 真实摄像头镜像画面'" @load="imageLoaded" @error="streamFailed" @click="measureAt" />
      <div v-if="!fresh" class="frame-wait">{{ error || '等待有效图像帧…' }}</div>
      <svg v-if="hands && fresh && handOverlay.points.length" class="hand-skeleton" :class="{holding:trackingState!=='tracking'}" :viewBox="`0 0 ${bounds.width} ${bounds.height}`" aria-label="手掌21关键点与骨架">
        <line v-for="bone in handOverlay.bones" :key="bone.id" :x1="bone.from.x" :y1="bone.from.y" :x2="bone.to.x" :y2="bone.to.y" />
        <circle v-for="point in handOverlay.points" :key="point.id" :data-landmark="point.id" :cx="point.x" :cy="point.y" :r="point.palm ? 2.2 : 1.7" />
      </svg>
      <span v-if="hands && marker && fresh" class="palm-marker" :class="{holding:trackingState!=='tracking'}" :style="{left:marker.x+'px',top:marker.y+'px',width:markerDiameter+'px',height:markerDiameter+'px'}" :aria-label="trackingState!=='tracking' ? '仅展示保持的掌心位置，不代表运动输入有效' : '掌心光标'"></span>
    </div>
    <!-- Display overlay is excluded from workstation inference. -->
    <div v-if="mapping && fresh && mappingStyle" class="mapping-layer" :style="mappingStyle" aria-label="映射四角：1左上、2右上、3右下、4左下，十字为中心">
      <span v-for="i in 4" :key="i" class="mapping-corner" :class="'mapping-corner-'+i">{{i}}</span>
      <span class="mapping-center" aria-hidden="true"></span>
    </div>
    <small v-if="measurement" class="measure-note">{{ measurement }}</small><small v-else-if="measure && fresh" class="measure-note">点击深度图测距</small>
  </figure>
</template>
<style scoped>
.camera-frame { margin:0; width:100%; height:100%; position:relative; background:var(--surface-0); }
figcaption { position:absolute; z-index:2; display:flex; justify-content:space-between; gap:4px; font-size:10px; color:var(--text-primary); text-shadow:0 1px 3px var(--surface-0); pointer-events:none; }
.fps { font-family:var(--font-mono); font-variant-numeric:tabular-nums; }
.mapping-layer { position:absolute; pointer-events:none; z-index:1; box-shadow:inset 0 0 0 1px #2fd6f055; }
.mapping-corner { position:absolute; width:22px; height:22px; display:grid; place-items:center; color:var(--acc); background:var(--bg-inset); font:12px var(--font-mono); border:1px solid #2fd6f088; }
.mapping-corner-1 { top:3px; left:3px; }
.mapping-corner-2 { top:3px; right:3px; }
.mapping-corner-3 { bottom:3px; right:3px; }
.mapping-corner-4 { bottom:3px; left:3px; }
.mapping-center { position:absolute; left:50%; top:50%; width:16px; height:16px; transform:translate(-50%,-50%); }
.mapping-center::before,.mapping-center::after { content:''; position:absolute; background:#2fd6f0bb; box-shadow:0 0 2px var(--bg-inset); }
.mapping-center::before { left:0; top:7.5px; width:16px; height:1px; }
.mapping-center::after { left:7.5px; top:0; width:1px; height:16px; }
small { font-size: 10px; color: var(--tx-3); overflow-wrap: anywhere; }
.frame-box { position:absolute; inset:0; }
img { display:block; width:100%; height:100%; object-fit:contain; transform:scaleX(-1); }
.frame-wait { position:absolute; inset:0; display:grid; place-items:center; color:var(--tx-3); font-size:11px; }
.hand-skeleton { position:absolute; inset:0; width:100%; height:100%; overflow:hidden; pointer-events:none; transition:opacity 120ms linear; }
.hand-skeleton line { stroke:var(--acc); stroke-width:1; opacity:.6; vector-effect:non-scaling-stroke; }
.hand-skeleton circle { fill:var(--acc); stroke:var(--surface-0); stroke-width:.7; vector-effect:non-scaling-stroke; }
.hand-skeleton.holding { opacity:.82; }
.palm-marker { position:absolute; box-sizing:border-box; border:2px solid var(--acc); background:#2fd6f033; border-radius:50%; transform:translate(-50%,-50%); transition:width 100ms linear,height 100ms linear,opacity 120ms linear; pointer-events:none; }
.palm-marker.holding { border-style:dashed; opacity:.82; background:transparent; }
.palm-marker::after { content:''; position:absolute; width:3px; height:3px; border-radius:50%; background:var(--acc); left:50%; top:50%; transform:translate(-50%,-50%); }
.measure-note { position:absolute; z-index:2; bottom:8px; left:10px; right:10px; padding:2px 4px; background:var(--surface-1); color:var(--text-secondary); font-size:10px; pointer-events:none; }
</style>
