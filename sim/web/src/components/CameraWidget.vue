<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, defineAsyncComponent } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import CameraFrame from './CameraFrame.vue'
const CameraCloud = defineAsyncComponent(() => import('./CameraCloud.vue'))

const w = useWorld()
const { cameras } = storeToRefs(w)
const open = ref(false)
const busy = ref(false)
const selected = ref('')
const chosen = ref(['color', 'depth'])
const message = ref('')
const trigger = ref(null)
const popup = ref(null)
const panelStyle = ref({})
let boundsObserver = null
let statusTimer = null, disposed = false
const showCloud = ref(false)
const showSettings = ref(false)

// 使用 body 浮层避开顶部动画、舞台裁切和告警动画建立的层叠上下文。
function positionPanel() {
  if (!open.value || !trigger.value) return
  const button = trigger.value.getBoundingClientRect()
  const stage = trigger.value.closest('.stage')?.getBoundingClientRect()
  const header = document.querySelector('.top')?.getBoundingClientRect()
  const footer = document.querySelector('.statusbar')?.getBoundingClientRect()
  const width = Math.max(0, Math.min(350, (stage?.width || innerWidth) - 20, innerWidth - 20))
  const top = Math.max(button.bottom + 6, (header?.bottom || 0) + 8)
  const bottom = Math.min(stage?.bottom || innerHeight, footer?.top || innerHeight, innerHeight) - 10
  panelStyle.value = {
    left: Math.max(10, Math.min(button.right - width, innerWidth - width - 10)) + 'px',
    top: top + 'px', width: width + 'px', maxHeight: Math.max(0, bottom - top) + 'px',
  }
}
async function togglePanel() {
  open.value = !open.value
  if (open.value) {
    if (!cameras.value.active) showSettings.value = true
    await nextTick(); positionPanel()
  }
}
function closePanel() {
  open.value = false
  trigger.value?.focus()
}
function outsideClick(ev) {
  if (open.value && !popup.value?.contains(ev.target) && !trigger.value?.contains(ev.target)) open.value = false
}
function escapePanel(ev) {
  if (!open.value || ev.key !== 'Escape') return
  ev.preventDefault()
  ev.stopPropagation()
  closePanel()
}

const active = computed(() => cameras.value.active)
const selectedDevice = computed(() => cameras.value.devices.find((d) => d.id === selected.value))
const hasCamera = computed(() => !!active.value)
const imageStreams = computed(() => Object.keys(active.value?.images || {}))
async function pollState() {
  try { if (active.value || open.value) await w.cameraState() } catch(e) { if (!disposed) message.value = e.message }
  finally { if (!disposed) statusTimer = setTimeout(pollState, 700) }
}
watch(() => active.value?.session, (session) => {
  showCloud.value = false
  if (session) showSettings.value = false
})

function selectDevice(device) {
  selected.value = device.id
  if (device.kind !== 'realsense') chosen.value = ['color']
  else chosen.value = ['color', 'depth'].filter(s => device.streams.includes(s))
  message.value = ''
}
function streamsFor(device) {
  return [
    ['color', '彩色', '彩色图像'],
    ['depth', '深度', '距离/深度图'],
    ['infrared', '红外', '双目红外图像'],
    ['imu', 'IMU', '陀螺仪 + 加速度计'],
  ].filter(([key]) => key === 'imu' ? device.kind === 'realsense' && (device.streams || []).some((s) => s === 'gyro' || s === 'accel') : (device.streams || []).includes(key))
}
function normalizedStreams() {
  return chosen.value.flatMap((s) => s === 'imu' ? ['gyro', 'accel'].filter(v => selectedDevice.value?.streams.includes(v)) : [s])
}
async function scan() {
  busy.value = true; message.value = ''
  try {
    const r = await w.refreshCameras()
    if (!selected.value && r.devices?.length) selectDevice(r.devices[0])
    if (!r.devices?.length) message.value = r.error || '未发现可用摄像头'
  } catch (e) { message.value = '设备扫描失败：' + e.message }
  finally { busy.value = false }
}
async function toggleCamera() {
  if (busy.value) return
  busy.value = true; message.value = ''
  try {
    if (active.value) {
      const result = await w.closeCamera()
      message.value = result.ok ? '摄像头已关闭' : result.why
      return
    }
    const d = selectedDevice.value || cameras.value.devices[0]
    if (!d) { message.value = '请先刷新并选择摄像头'; return }
    selected.value = d.id
    const r = await w.openCamera(d.id, normalizedStreams())
    if (!r.ok) message.value = r.why || '摄像头开启失败'
    else showSettings.value = false
  } catch (e) { message.value = '摄像头操作失败：' + e.message }
  finally { busy.value = false }
}
onMounted(async () => {
  document.addEventListener('pointerdown', outsideClick)
  window.addEventListener('keydown', escapePanel, true)
  window.addEventListener('resize', positionPanel)
  document.addEventListener('scroll', positionPanel, true)
  boundsObserver = new ResizeObserver(positionPanel)
  for (const el of [trigger.value?.closest('.stage'), document.querySelector('.top'), document.querySelector('.statusbar')]) {
    if (el) boundsObserver.observe(el)
  }
  if (!cameras.value.devices.length) await scan()
  if (!disposed) pollState()
})
onBeforeUnmount(() => {
  disposed = true; clearTimeout(statusTimer)
  boundsObserver?.disconnect()
  document.removeEventListener('pointerdown', outsideClick)
  window.removeEventListener('keydown', escapePanel, true)
  window.removeEventListener('resize', positionPanel)
  document.removeEventListener('scroll', positionPanel, true)
})
</script>

<template>
  <div class="camera-wrap">
    <button ref="trigger" class="camera-trigger" :class="{ active: hasCamera }" @click="togglePanel"
            :aria-expanded="open" aria-controls="camera-panel" title="摄像头设备与数据流">
      <svg class="cam-icon" viewBox="0 0 20 20" aria-hidden="true">
        <path d="M3 6.5h3l1.3-2h5.4l1.3 2H17v9H3z" fill="none" stroke="currentColor" stroke-width="1.4" />
        <circle cx="10" cy="11" r="2.7" fill="none" stroke="currentColor" stroke-width="1.4" />
      </svg>
      <span>摄像头</span>
    </button>
    <Teleport to="body">
    <section v-if="open" ref="popup" id="camera-panel" class="camera-pop no-fold"
             :style="panelStyle" aria-label="摄像头设备与数据流">
      <div class="camera-head">
        <div class="camera-title"><b>摄像头</b><span v-if="active" class="live-name">{{ active.name || selectedDevice?.name || '实时画面' }}</span><span v-else class="sub">设备与数据流</span></div>
        <div class="camera-head-actions">
          <button class="btn btn--sm settings-toggle" :class="{ 'btn--on': showSettings }"
                  :aria-expanded="showSettings" aria-controls="camera-settings" @click="showSettings = !showSettings">
            {{ showSettings ? '收起设置' : '设备设置' }}
          </button>
          <button v-if="hasCamera" class="btn btn--sm btn--danger" :disabled="busy" @click="toggleCamera">
            {{ busy ? '关闭中…' : '关闭' }}
          </button>
          <button class="camera-close" aria-label="收起摄像头面板" @click="closePanel">×</button>
        </div>
      </div>
      <div v-if="active?.error" class="camera-msg">{{ active.error }}</div>
      <template v-if="active?.state === 'streaming'">
        <CameraFrame v-for="stream in imageStreams" :key="active.session + stream" :session="active.session" :stream="stream" :measure="stream === 'depth'" />
        <div v-if="active.streams.includes('depth')" class="cloud-toggle"><button class="btn btn--xs" @click="showCloud = !showCloud">{{ showCloud ? '收起 3D 点云' : '显示 3D 点云' }}</button></div>
        <CameraCloud v-if="showCloud" :key="active.session" :session="active.session" />
        <div v-if="Object.keys(active.imu || {}).length" class="camera-imu"><div v-for="(data, name) in active.imu" :key="name">{{ name }}：{{ data.xyz.map(v => Number(v).toFixed(3)).join(' / ') }} {{ data.unit }}</div></div>
      </template>
      <div v-if="showSettings" id="camera-settings" class="camera-settings">
        <div class="settings-head"><span>设备设置</span><button class="btn btn--sm" :disabled="busy" @click="scan">刷新设备</button></div>
        <div class="sdk-note" :class="{ bad: !cameras.sdk?.realsense }">
          RealSense SDK：{{ cameras.sdk?.realsense ? '已就绪' : '未安装' }}
          <span v-if="!cameras.sdk?.realsense">（pyrealsense2）</span>
        </div>
        <div v-if="!cameras.devices.length" class="empty">
          {{ cameras.error || '未发现摄像头。请连接设备后刷新。' }}
        </div>
        <div v-else class="device-list">
          <button v-for="device in cameras.devices" :key="device.id" class="device"
                  :class="{ selected: selected === device.id, streaming: active?.id === device.id }"
                  @click="selectDevice(device)">
            <span class="device-dot"></span>
            <span class="device-main"><b>#{{ device.number }} · {{ device.name }}</b><small>
              {{ device.kind === 'realsense' ? 'RealSense · ' + (device.serial || '无序列号') : '系统视频设备' }}
            </small></span>
            <em>{{ active?.id === device.id ? '使用中' : '选择' }}</em>
          </button>
        </div>
        <div v-if="selectedDevice" class="stream-box">
          <div class="stream-title">启用数据流</div>
          <label v-for="[key, label, hint] in streamsFor(selectedDevice)" :key="key" class="stream">
            <input type="checkbox" :value="key" v-model="chosen" :disabled="!!active" />
            <span>{{ label }}</span><small>{{ hint }}</small>
          </label>
        </div>
        <details v-if="selectedDevice?.kind === 'realsense'" class="camera-attributes"><summary>设备属性</summary><div>固件 {{ selectedDevice.firmware || '未提供' }} · USB {{ selectedDevice.usb || '未提供' }}</div><div v-for="profile in active?.id === selectedDevice.id ? active.profiles : []" :key="profile.stream + profile.index">{{ profile.stream }} {{ profile.width ? profile.width + '×' + profile.height : '' }} · {{ profile.fps }}Hz</div><div v-if="active?.depth_scale_m">深度单位 {{ active.depth_scale_m }}m</div></details>
        <div v-if="!hasCamera" class="camera-actions">
          <button class="btn btn--primary" :disabled="busy || !selectedDevice" @click="toggleCamera">
            {{ busy ? '处理中…' : '开启摄像头' }}
          </button>
        </div>
      </div>
      <div v-if="message" class="camera-msg" role="status">{{ message }}</div>
    </section>
    </Teleport>
  </div>
</template>

<style scoped>
.camera-wrap { z-index: 10; }
.camera-trigger { display: inline-flex; align-items: center; gap: 5px; height: 30px; padding: 0 9px;
  border: 1px solid var(--line); border-radius: 7px; background: var(--bg-inset); color: var(--tx-2);
  font: inherit; font-size: 11.5px; cursor: pointer; }
.camera-trigger:hover, .camera-trigger.active { color: var(--tx-1); border-color: var(--acc); background: rgba(47,214,240,.09); }
.cam-icon { width: 17px; height: 17px; color: var(--acc); flex: 0 0 17px; }
.camera-pop { position: fixed; z-index: 40; box-sizing: border-box; overflow: auto; overscroll-behavior: contain;
  padding: 10px; border: 1px solid var(--line-3); border-radius: 8px; background: var(--bg-panel);
  box-shadow: 0 16px 40px rgba(0,0,0,.48); }
.camera-head-actions { display: flex; align-items: center; gap: 6px; }
.camera-close { width: 28px; height: 28px; border: 1px solid var(--line-2); border-radius: 5px;
  font: inherit; font-size: 18px; color: var(--tx-2); background: var(--bg-inset); cursor: pointer; }
.camera-close:hover { color: var(--acc); border-color: var(--acc); }
.camera-head { display: flex; justify-content: space-between; align-items: center; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
.camera-head b { font-family: var(--font-display); font-size: 13px; }
.camera-title { min-width:0; display:flex; align-items:center; gap:7px; }
.live-name { min-width:0; max-width:135px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--ok); font-size:10px; }
.sub { margin-left: 7px; color: var(--tx-3); font-size: 10px; }
.btn--sm { min-height: 25px; padding: 2px 8px; font-size: 10.5px; }
.settings-toggle { white-space:nowrap; }
.camera-settings { margin-top:9px; padding-top:8px; border-top:1px solid var(--line); }
.settings-head { display:flex; align-items:center; justify-content:space-between; color:var(--tx-2); font-size:11px; }
.sdk-note { margin-top: 8px; color: var(--ok); font-size: 10.5px; }
.sdk-note.bad { color: var(--warn); }
.empty { padding: 18px 8px; color: var(--tx-3); font-size: 11.5px; line-height: 1.55; }
.device-list { display: grid; gap: 5px; margin-top: 8px; max-height: 180px; overflow: auto; }
.device { display: flex; align-items: center; gap: 7px; width: 100%; padding: 7px; text-align: left; color: var(--tx-2); background: var(--bg-inset);
  border: 1px solid var(--line); border-radius: 6px; font: inherit; cursor: pointer; }
.device:hover, .device.selected { border-color: var(--acc); background: rgba(47,214,240,.08); }
.device.streaming { box-shadow: inset 2px 0 var(--ok); }
.device-dot { width: 7px; height: 7px; flex: none; border-radius: 50%; background: var(--tx-3); }
.device.streaming .device-dot { background: var(--ok); box-shadow: 0 0 7px var(--ok); }
.device-main { display: grid; gap: 2px; min-width: 0; flex: 1; }
.device-main b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11.5px; }
.device-main small { color: var(--tx-3); font-size: 9.5px; }
.device em { color: var(--tx-3); font-style: normal; font-size: 9.5px; }
.stream-box { margin-top: 9px; padding: 7px; border: 1px solid var(--line); border-radius: 6px; }
.stream-title { color: var(--tx-3); font-size: 10px; margin-bottom: 5px; }
.stream { display: grid; grid-template-columns: 16px 46px 1fr; align-items: center; gap: 4px; min-height: 24px; font-size: 11px; }
.stream small { color: var(--tx-3); font-size: 9.5px; }
.camera-actions { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.camera-actions .btn { flex: none; }
.active-note { color: var(--ok); font: 10px var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.camera-msg { margin-top: 7px; padding: 5px 7px; color: var(--warn); border-left: 2px solid var(--warn); background: rgba(255,176,32,.08); font-size: 10.5px; }
.cloud-toggle, .camera-imu { margin-top: 7px; font: 10px var(--font-mono); color: var(--tx-3); }
.camera-attributes { margin-top: 7px; font-size: 10px; line-height: 1.7; color: var(--tx-3); }
</style>
