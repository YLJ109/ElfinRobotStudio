<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import CameraFrame from './CameraFrame.vue'
import { useCameraSource, virtualPreview } from '../cameraSource.js'
import { visionWorkspace, localVirtualPreview, virtualRecognitionFps } from '../visionWorkspace.js'
import { onHandFollowVisionRequest, getHandFollowInput, subscribeHandFollowInput } from '../handFollowInput.js'

const w = useWorld()
const { cameras } = storeToRefs(w)
const open = ref(localStorage.getItem('huayan.camera.open') === '1')
const {busy,selected,message,virtual,options,running,refresh,select,toggle}=useCameraSource(w)
const virtualImage=computed(()=>virtualPreview(visionWorkspace))
const localPreview=localVirtualPreview(visionWorkspace),previewCanvas=ref(null)
const trigger = ref(null)
const anchor = ref(null)
const popup = ref(null)
const panelStyle = ref({})
const frameResolution = ref(null)
const visionInput = ref(getHandFollowInput())
const removeVisionInput = subscribeHandFollowInput(value => { visionInput.value = value })
const currentVision = computed(() => visionInput.value?.session === active.value?.session ? visionInput.value : null)
const pageVisible = ref(!document.hidden)
let boundsObserver = null
let statusTimer = null, disposed = false
watch(open, (v) => localStorage.setItem('huayan.camera.open', v ? '1' : '0'))

// 使用 body 浮层避开顶部动画、舞台裁切和告警动画建立的层叠上下文。
function positionPanel() {
  if (!open.value || !anchor.value) return
  const button = anchor.value.getBoundingClientRect()
  const stage = anchor.value.closest('.stage')?.getBoundingClientRect()
  const header = document.querySelector('.top')?.getBoundingClientRect()
  const footer = document.querySelector('.statusbar')?.getBoundingClientRect()
  const width = Math.max(0, Math.min(332, (stage?.width || innerWidth) - 20, innerWidth - 20))
  const top = Math.max(button.top, (header?.bottom || 0) + 8)
  const dock = document.querySelector('.stage .dock')?.getBoundingClientRect()
  const left = Math.max(10, Math.min(button.right - width, innerWidth - width - 10))
  const logLimit = dock && dock.left < left + width && dock.right > left ? dock.top - 8 : innerHeight
  const bottom = Math.min(stage?.bottom || innerHeight, footer?.top || innerHeight, logLimit, innerHeight) - 8
  const image = virtual.value?virtualImage.value:frameResolution.value || active.value?.images?.[mainStream.value]
  const ratio = image?.width > 0 && image?.height > 0 ? image.width/image.height : 4/3
  panelStyle.value = {
    left: left + 'px',
    top: top + 'px', width: width + 'px', maxHeight: Math.max(0, bottom - top) + 'px',
    '--preview-ratio': String(ratio),
  }
}
async function togglePanel() {
  open.value = !open.value
  if (open.value) {
    // Keep the preview visible even before capture.
    await nextTick(); positionPanel()
  }
}
async function closePanel() {
  open.value = false
  await nextTick()
  trigger.value?.focus()
}
function escapePanel(ev) {
  if (!open.value || ev.key !== 'Escape') return
  ev.preventDefault()
  ev.stopPropagation()
  closePanel()
}

const active = computed(() => cameras.value.active)
const hasCamera = running
const cameraLive = computed(() => virtual.value?!!virtualImage.value:active.value?.state === 'streaming')
const imageStreams = computed(() => Object.keys(active.value?.images || {}))
const rememberedStream=localStorage.getItem('huayan.camera.stream')
const hands = ref(localStorage.getItem('huayan.camera.hands') === '1'), shownStream = ref(['color','depth','infrared1','infrared2'].includes(rememberedStream)?rememberedStream:'color')
watch(shownStream,v=>localStorage.setItem('huayan.camera.stream',v))
const followView = computed(() => w.view === 'hand-follow' && w.mode === 'sim')
const detectHands = computed(() => !virtual.value && w.view === 'hand-follow' && hands.value)
// Opens the existing visual window and detector only, never the USB hardware.
async function enableFollowVision() {
  open.value = true; hands.value = true; shownStream.value = 'color'
  await nextTick()
  if (!disposed) positionPanel()
}
const removeVisionRequest = onHandFollowVisionRequest(enableFollowVision)
watch(() => [followView.value, active.value?.session], () => {
  if (followView.value) void enableFollowVision()
}, {immediate:true})
watch(hands, (v) => localStorage.setItem('huayan.camera.hands', v ? '1' : '0'))
const mainStream = computed(() => imageStreams.value.includes(shownStream.value) ? shownStream.value : imageStreams.value[0])
watch(() => [active.value?.images?.[mainStream.value]?.width,active.value?.images?.[mainStream.value]?.height],
  async () => { await nextTick(); if (!disposed) positionPanel() })
const mapping = computed(() => followView.value && mainStream.value === 'color')
function receivedResolution(size) {
  frameResolution.value = size
  void nextTick().then(() => { if (!disposed) positionPanel() })
}
watch(() => [active.value?.session, mainStream.value], () => { frameResolution.value = null })
const notice = computed(() => message.value || (virtual.value?visionWorkspace.error:active.value?.error || cameras.value.error) || '')
async function pollState() {
  try { if (pageVisible.value && (active.value || open.value)) await w.cameraState() } catch(e) { if (!disposed) message.value = e.message }
  finally { if (!disposed) statusTimer = setTimeout(pollState, 700) }
}
function visibilityChanged() { pageVisible.value = !document.hidden }
watch(() => active.value?.session, () => {
  if(active.value){open.value=true;void nextTick().then(()=>{if(!disposed)positionPanel()})}
})
watch(()=>[virtual.value,visionWorkspace.config?.enabled],()=>{
  if(virtual.value && running.value){open.value=true;void nextTick().then(()=>{if(!disposed)positionPanel()})}
})
watch(()=>[open.value,virtual.value,running.value,pageVisible.value],()=>{
  localPreview.visible=!!(open.value && virtual.value && running.value && pageVisible.value)
  if(!localPreview.visible)localPreview.frame=null
},{immediate:true,flush:'sync'})
watch(virtualImage,async frame=>{
  if(!frame)return
  await nextTick()
  if(disposed || virtualImage.value!==frame || !previewCanvas.value)return
  const canvas=previewCanvas.value
  if(canvas.width!==frame.width || canvas.height!==frame.height){canvas.width=frame.width;canvas.height=frame.height;positionPanel()}
  canvas.getContext('2d',{alpha:false})?.drawImage(frame.canvas,0,0)
},{flush:'post'})

async function toggleCamera() {
  await toggle()
}
onMounted(async () => {
  document.addEventListener('visibilitychange', visibilityChanged)
  window.addEventListener('keydown', escapePanel, true)
  window.addEventListener('resize', positionPanel)
  document.addEventListener('scroll', positionPanel, true)
  boundsObserver = new ResizeObserver(positionPanel)
  for (const el of [anchor.value?.closest('.stage'), document.querySelector('.top'), document.querySelector('.statusbar'), document.querySelector('.stage .dock')]) {
    if (el) boundsObserver.observe(el)
  }
  if (open.value) { await nextTick(); positionPanel() }
  if (!disposed) pollState()
})
onBeforeUnmount(() => {
  localPreview.visible=false;localPreview.frame=null
  removeVisionRequest()
  removeVisionInput()
  disposed = true; clearTimeout(statusTimer)
  boundsObserver?.disconnect()
  window.removeEventListener('keydown', escapePanel, true)
  window.removeEventListener('resize', positionPanel)
  document.removeEventListener('scroll', positionPanel, true)
  document.removeEventListener('visibilitychange', visibilityChanged)
})
</script>

<template>
  <div ref="anchor" class="camera-wrap">
    <button v-if="!open" ref="trigger" class="camera-trigger" :class="{active:cameraLive}" :aria-expanded="open" aria-controls="camera-panel" @click="togglePanel"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 6h3l1-2h6l1 2h3v10H3z"/><circle cx="10" cy="11" r="3"/></svg>摄像头</button>
    <Teleport to="body">
      <section v-if="open" id="camera-panel" ref="popup" class="camera-pop panel no-fold" :style="panelStyle" aria-label="摄像头设备与数据流">
        <header class="camera-head panel-hd"><span class="panel-title">视觉窗口</span><span class="panel-sub">CAMERA</span><span class="grow"></span><span class="tag" :class="cameraLive?'tag--ok':active?.state==='error'?'tag--bad':'tag--mute'">{{ cameraLive?'LIVE':active?.state==='starting'?'连接中':'STANDBY' }}</span><button class="close-button btn btn--xs no-fold" aria-label="收起摄像头面板" @click="closePanel">×</button></header>
        <div class="camera-viewport">
          <canvas v-if="virtualImage && virtual" ref="previewCanvas" class="virtual-preview" role="img" aria-label="末端仿真相机实时画面"></canvas>
          <CameraFrame v-else-if="!virtual && cameraLive && mainStream" :key="active.session + mainStream" :session="active.session" :stream="mainStream" :mapping="!!mapping" :measure="mainStream==='depth'" :hands="detectHands && mainStream==='color'" :frame-seq="active.images[mainStream].seq" :frame-at="active.last_frame_at" :actual-fps="active.actual_fps_by_stream?.[mainStream] ?? active.actual_fps" @resolution="receivedResolution" />
          <div v-else class="camera-placeholder"><svg viewBox="0 0 48 48" aria-hidden="true"><rect x="7" y="12" width="34" height="26" rx="5"/><circle cx="24" cy="25" r="8"/><path d="M15 12l4-5h10l4 5"/></svg><b>{{ virtual?'末端仿真相机':active?.state==='starting'?'正在等待画面':'摄像头画面' }}</b><span>{{ virtual?(running?'等待主场景仿真画面（仅仿真模式）':'仿真相机已关闭'):'选择下方来源后开启；属性在视觉 → 摄像头' }}</span></div>
        </div>
        <div class="camera-controls">
          <p v-if="virtual" class="virtual-rates dim tiny">本地预览目标 30 fps · 识别采样 {{ virtualRecognitionFps(visionWorkspace.config) }} fps（上传忙时跳帧）</p>
          <label for="preview-camera-source" class="device-label">来源</label><select id="preview-camera-source" class="select" :value="selected" :disabled="busy" @change="select($event.target.value)"><option value="" disabled>选择摄像头</option><option v-for="item in options" :key="item.id" :value="item.id">{{ item.name }}</option></select>
          <div class="camera-toolbar"><span class="dim tiny grow">{{ virtual?'仿真 · 末端视角':cameraLive?'真实摄像头 · 采集中':'请选择来源' }}</span><button class="btn btn--xs" :disabled="busy" title="重新扫描已连接摄像头，不自动开启设备" @click="refresh">重新加载</button><button class="capture-button btn btn--xs" :class="hasCamera?'btn--danger':'btn--primary'" :disabled="busy || !selected" @click="toggleCamera">{{busy?'处理中…':hasCamera?'关闭':'开启'}}</button></div>
        </div>
        <div v-if="notice" class="camera-msg" role="status">{{notice}}</div>
        <div v-if="!virtual && currentVision?.hands && !notice" class="vision-status" :class="{holding:currentVision.trackingState==='holding'}" role="status">{{currentVision.detectorStatus}}{{currentVision.trackingState==='lost' && !currentVision.detectorError ? ' · 跟随平滑回中心等待' : ''}}</div>
      </section>
    </Teleport>
  </div>
</template>
<style scoped>
.camera-wrap { min-width:79px; min-height:30px; }
.virtual-preview { width:100%;height:100%;object-fit:contain;display:block; }
.virtual-rates { grid-column:1/-1;margin:0; }
.vision-status { padding:0 10px 7px; color:var(--tx-3); font-size:10px; }
.vision-status.holding { color:var(--text-secondary); }
.camera-trigger { display:flex; align-items:center; gap:var(--s3); height:30px; padding:0 var(--s5); background:var(--bg-inset); border:1px solid var(--line); border-radius:0; clip-path:var(--clip-br); color:var(--tx-2); font:inherit; font-size:12px; cursor:pointer; }
.camera-trigger svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.4; }
.camera-trigger.active { border-color:var(--state-running); }
.camera-pop { position:fixed; z-index:40; box-sizing:border-box; overflow:auto; overscroll-behavior:contain; color:var(--text-primary); backdrop-filter:blur(6px); }
.camera-head { cursor:default; }
.camera-head::after { display:none; }
.camera-head .tag { font-family:var(--font-mono); font-size:10px; padding:var(--s1) var(--s2); }
.close-button { flex-shrink:0; padding:0 var(--s2); min-width:22px; font-size:16px; line-height:1; }
.camera-viewport { aspect-ratio:var(--preview-ratio,1.333333); margin:var(--s4) var(--s4) 0; overflow:hidden; position:relative; background:var(--surface-0); border:1px solid var(--line); }
.camera-placeholder { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:var(--s5); background:var(--bg-inset); }
.camera-placeholder svg { width:30px; height:30px; fill:none; stroke:var(--text-disabled); stroke-width:1.1; }
.camera-placeholder b { font-size:12px; color:var(--text-secondary); font-weight:400; }
.camera-placeholder span { font-size:10px; color:var(--text-disabled); }
.camera-controls { padding:var(--s4) var(--s5); display:grid; grid-template-columns:42px minmax(0,1fr); align-items:center; gap:var(--s4); }
.device-label { font-size:11px; color:var(--text-secondary); }
.camera-controls select, .view-stream select { height:28px; font-size:10px; }
.camera-toolbar { grid-column:1/-1; display:flex; align-items:center; gap:var(--s3); }
.hand-switch { display:flex; align-items:center; gap:var(--s2); font-size:10px; cursor:pointer; }
.more-button { margin-left:auto; }
.capture-button { min-width:58px; }
.camera-msg { margin:0 var(--s5) var(--s4); font-size:10px; line-height:1.6; color:var(--state-warn); overflow-wrap:anywhere; }
.camera-settings { border-top:1px solid var(--line); padding:var(--s4) var(--s5); display:flex; flex-direction:column; gap:var(--s4); }
.settings-head { display:flex; align-items:center; justify-content:space-between; font-size:11px; }
.stream-options { display:flex; flex-wrap:wrap; gap:var(--s5); }
.stream-options label { display:flex; gap:var(--s2); align-items:center; font-size:11px; }
.camera-settings p { margin:0; color:var(--text-secondary); font-size:10px; line-height:1.6; }
.camera-settings p.warn { color:var(--state-warn); }
.view-stream { display:flex; align-items:center; gap:var(--s4); font-size:11px; }
</style>
