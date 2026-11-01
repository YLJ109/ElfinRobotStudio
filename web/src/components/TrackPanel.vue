<script setup>
// 云台追踪：机械臂不看画面以外的东西，只把末端转过去，让摄像头中心重新落回手掌上。
// 没有真摄像头时用这块区域模拟手掌：鼠标位置就是手掌在画面里的位置。
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useWorld } from '../store'
import { getHandFollowInput } from '../handFollowInput.js'

const w = useWorld()

const active = ref(false)
const busy = ref(false)
const message = ref('')
const showHand = ref(true)
// 范围沿用跟随那套表示法：中心坐标 + 范围尺寸，盒子 = 中心 ± 尺寸/2。
// 但默认值**不能**照搬跟随：跟随的默认框在工作台前上方（y=-400、z=700），
// 追踪要悬到框中心上方 80mm，那个位置这条臂根本够不到，去中心会直接卡在
// "逆解未收敛"，看起来就是"机器人不动"。追踪给自己的工作区默认值：
// 机器人正前方、台面上方，整框可达。
const TRACK_RANGE_KEY = 'huayan.track.range'
// 和跟随用同一套框：位置、尺寸、朝向都一致。跟随是外置相机看着手、末端过去；
// 追踪是末端相机看着手、末端过去。工作区是同一个，不该各写一套。
const TRACK_CENTER = [-500, 0, 700]
const TRACK_SIZE = [900, 300, 500]
const TRACK_DIRECTION = 270
function readTrackRange() {
  try {
    const value = JSON.parse(globalThis.localStorage?.getItem(TRACK_RANGE_KEY) || 'null')
    if (Array.isArray(value?.center) && value.center.length === 3 && Array.isArray(value?.size) && value.size.length === 3
        && value.center.every(Number.isFinite) && value.size.every((n) => Number(n) > 0)) return value
  } catch { /* 存坏了就用内置默认 */ }
  return null
}
const savedRange = readTrackRange()
const DEFAULT_CENTER = savedRange ? savedRange.center.map(Number) : [...TRACK_CENTER]
const DEFAULT_SIZE = savedRange ? savedRange.size.map(Number) : [...TRACK_SIZE]
const axisCenter = ref([...DEFAULT_CENTER])
const axisSize = ref([...DEFAULT_SIZE])
const rangeNotice = ref('')

const enclosure = () => ({
  min: axisCenter.value.map((v, i) => Number(v) - Number(axisSize.value[i]) / 2),
  max: axisCenter.value.map((v, i) => Number(v) + Number(axisSize.value[i]) / 2),
})
const rangeError = () => axisSize.value.some((v) => !(Number(v) > 0)) ? '范围尺寸必须大于 0' : ''

// 两种输入：模拟（面板里用鼠标摆手掌）和 摄像头（用真实手掌检测结果）。
// 和跟随的「手掌 / 鼠标」是一回事，只是这边叫得更直白。
const inputMode = ref('sim')
const depth = ref(50)                    // 模拟"手离镜头远近"，0 近 100 远
const hand = ref({ u: .5, v: .5 })
const track = ref({ turned_deg: 0, range_deg: 120, offset: 0, reason: '', centered: false })

// 面板上的手掌位置 → 场景里的 3D 点。必须和后端 _point_from_pixel 用同一套
// 公式：先在盒子自己的局部坐标里算偏移，再按 direction 转到世界方向。
// 少了这一步，盒子一转，手掌就跑到框外面去了。
const handPoint = () => {
  const [cx, cy, cz] = axisCenter.value.map(Number)
  const [sx, sy, sz] = axisSize.value.map(Number)
  const lx = (hand.value.u - .5) * sx
  const ly = (depth.value / 100 - .5) * sy
  const a = (TRACK_DIRECTION * Math.PI) / 180
  return [cx + lx * Math.cos(a) - ly * Math.sin(a),
          cy + lx * Math.sin(a) + ly * Math.cos(a),
          cz + (.5 - hand.value.v) * sz]
}

function saveRangeDefault() {
  if (rangeError()) { rangeNotice.value = rangeError(); return }
  // 只存本地，不写回跟随共用的服务器设置：两个框的默认位置本来就不同，
  // 互相覆盖会让对方下次进来落在一个够不到的地方。
  try {
    globalThis.localStorage?.setItem(TRACK_RANGE_KEY,
      JSON.stringify({ center: axisCenter.value, size: axisSize.value }))
    rangeNotice.value = '已设为默认'
  } catch {
    rangeNotice.value = '本地存储不可用，本次修改只在当前页面生效'
  }
}

function resetRange() {
  axisCenter.value = [...DEFAULT_CENTER]
  axisSize.value = [...DEFAULT_SIZE]
  rangeNotice.value = '已恢复初始范围'
}

// "应用范围"只把范围交给后端备用，不启动追踪：configure 之后立刻停掉，
// 免得点一下应用镜头就自己转起来。
async function applyRange() {
  if (rangeError()) { rangeNotice.value = rangeError(); return }
  rangeNotice.value = ''
  try {
    const result = await post('configure', { center: axisCenter.value, size: axisSize.value, direction: TRACK_DIRECTION, enclosure: enclosure() })
    if (!result.ok) { rangeNotice.value = result.why || '范围应用失败'; return }
    await post('stop')
    active.value = false
    rangeNotice.value = '范围已应用'
  } catch (error) {
    rangeNotice.value = error.message
  }
}

let timer = null
let latest = null
let sending = false

async function post(action, body = {}) {
  const response = await fetch(`/api/sim/hand-track/${action}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body), signal: AbortSignal.timeout(8000),
  })
  const value = await response.json()
  if (value?.state) track.value = value.state
  return value
}

function stopLoop() {
  if (timer) { clearInterval(timer); timer = null }
  latest = null
}

// 追踪是连续小步：定时器按固定节拍推进一步。这里**必须**用当前手掌位置兜底，
// 不能只在鼠标移动后才发 —— 先去中心要转几十步，人不动鼠标就永远停在第一步。
function loop() {
  stopLoop()
  timer = setInterval(async () => {
    if (!active.value || sending) return
    let u, v, d
    if (inputMode.value === 'camera') {
      const input = getHandFollowInput()
      const point = input?.point
      if (!input?.hands || !point) {
        message.value = '摄像头模式：未检测到手掌（先在视觉面板开启检测）'
        return
      }
      u = point.x; v = point.y
      // palm_scale 越大表示手越近，映射到盒子的 Y 深度。
      d = Math.max(0, Math.min(1, (point.palm_scale ?? .12) / .3))
      hand.value = { u, v }          // 场景里的手掌跟着真实检测结果走
    } else {
      const now = latest || hand.value
      u = now.u; v = now.v
      d = depth.value / 100
    }
    latest = null
    sending = true
    try {
      const result = await post('sample', { u, v, depth: d })
      if (!result.ok) message.value = result.why || '追踪失败'
      else if (result.state?.reason) message.value = result.state.reason
    } catch (error) {
      message.value = error.message
    } finally {
      sending = false
    }
  }, 120)
}

async function toggle() {
  if (busy.value) return
  if (active.value) {
    stopLoop()
    active.value = false
    try { await post('stop') } catch { /* 停止失败也要让界面回到可再点的状态 */ }
    message.value = '已停止追踪'
    return
  }
  busy.value = true
  message.value = ''
  try {
    const result = await post('configure', { center: axisCenter.value, size: axisSize.value, direction: TRACK_DIRECTION, enclosure: enclosure() })
    if (!result.ok) { message.value = result.why || '追踪启动失败'; return }
    active.value = true
    message.value = '追踪中：在上面区域移动鼠标模拟手掌'
    latest = { ...hand.value }
    loop()
  } catch (error) {
    message.value = error.message
  } finally {
    busy.value = false
  }
}

function move(event) {
  const rect = event.currentTarget.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  hand.value = { u: (event.clientX - rect.left) / rect.width, v: (event.clientY - rect.top) / rect.height }
  latest = { ...hand.value }
}

function recenter() {
  hand.value = { u: .5, v: .5 }
  latest = { ...hand.value }
}

// 三维场景里同步画出范围盒和模拟手掌：仿真摄像头就是把场景渲染出去的，
// 所以在场景里画了，摄像头画面里也就有了。
const scene = () => globalThis.window?.__elfinScene
function paintScene() {
  const api = scene()
  if (!api) return
  api.setTrackWorkspace?.(enclosure(), true, TRACK_DIRECTION)
  // 手掌要一直看得见：原来只在"追踪中"才画，进页面时框里空空的，
  // 根本分不清机器人到底在追什么。现在只要这块面板在，手就在场景里。
  api.setTrackHand?.(handPoint(), showHand.value)
}
watch([active, showHand, depth, hand, axisCenter, axisSize], paintScene, { deep: true, immediate: true })
// 场景可能比这个面板晚就绪，挂载后再补画一次，否则第一次进页面看不到框。
onMounted(() => { paintScene(); globalThis.requestAnimationFrame?.(paintScene) })

onBeforeUnmount(() => {
  stopLoop()
  if (active.value) { void post('stop').catch(() => {}) }
  active.value = false
  const api = scene()
  api?.setTrackWorkspace?.(null, false)
  api?.setTrackHand?.(null, false)
})
</script>

<template>
  <section class="panel track-panel">
    <div class="panel-hd">
      <span class="panel-title">追踪</span>
      <span class="tag" :class="active ? 'tag--ok' : ''">{{ active ? '追踪中' : '未开始' }}</span>
    </div>
    <div class="panel-bd col">
      <div class="row">
        <button class="btn btn--xs" :class="inputMode === 'sim' ? 'btn--primary' : ''" :disabled="active" @click="inputMode = 'sim'">模拟</button>
        <button class="btn btn--xs" :class="inputMode === 'camera' ? 'btn--primary' : ''" :disabled="active" @click="inputMode = 'camera'">摄像头</button>
        <span class="tiny dim grow">{{ inputMode === 'sim' ? '鼠标摆手掌' : '真实手掌检测' }}</span>
      </div>
      <div class="row">
        <button class="btn btn--primary grow" :disabled="busy" @click="toggle">{{ active ? '停止追踪' : '开始追踪' }}</button>
        <button v-if="inputMode === 'sim'" class="btn" @click="showHand = !showHand">{{ showHand ? '隐藏手掌' : '显示手掌' }}</button>
      </div>
      <!-- 模拟画面只在模拟模式出现；摄像头模式直接用真实手掌检测，不需要这块。 -->
      <div v-if="inputMode === 'sim'" class="stage" role="application" aria-label="模拟摄像头画面" @mousemove="move" @dblclick="recenter">
        <div class="cross" aria-hidden="true"></div>
        <div v-if="showHand" class="hand" :style="{ left: (hand.u * 100) + '%', top: (hand.v * 100) + '%', fontSize: (14 + (100 - depth) * 0.16) + 'px' }"
             :title="`手掌位置 ${(hand.u * 100).toFixed(0)}% , ${(hand.v * 100).toFixed(0)}%`">✋</div>
        <span class="hint">移动鼠标 = 手掌在画面里移动</span>
      </div>
      <label v-if="inputMode === 'sim'" class="tiny row">远近<input v-model.number="depth" type="range" min="0" max="100" class="grow" /></label>
      <p v-else class="tiny dim">摄像头模式：用真实手掌检测，需先在视觉面板开启手掌检测。</p>
      <div class="range-grid" :class="{ locked: active }">
        <span>轴 / mm</span><span>中心坐标</span><span>范围尺寸</span>
        <template v-for="(axis, i) in ['X · 宽', 'Y · 深', 'Z · 高']" :key="axis">
          <span>{{ axis }}</span>
          <div class="range-field">
            <input v-model.number="axisCenter[i]" type="range" :min="i === 2 ? 0 : -2000" max="2000" step="1" :disabled="active" :aria-label="axis + '中心滑块'" />
            <input v-model.number="axisCenter[i]" type="number" step="1" :disabled="active" :aria-label="axis + '中心坐标'" />
          </div>
          <div class="range-field">
            <input v-model.number="axisSize[i]" type="range" :min="i === 1 ? 20 : 100" :max="i === 1 ? 1000 : 3000" step="1" :disabled="active" :aria-label="axis + '尺寸滑块'" />
            <input v-model.number="axisSize[i]" type="number" min="1" max="5000" step="1" :disabled="active" :aria-label="axis + '范围尺寸'" />
          </div>
        </template>
      </div>
      <div class="row">
        <button class="btn btn--xs" :disabled="active || !!rangeError()" @click="applyRange">应用范围</button>
        <button class="btn btn--xs" :disabled="!!rangeError()" @click="saveRangeDefault">设为默认</button>
        <button class="btn btn--xs" @click="resetRange">恢复初始范围</button>
      </div>
      <p v-if="rangeError() || rangeNotice" class="tiny" role="status">{{ rangeError() || rangeNotice }}</p>
      <p class="tiny" role="status">{{ message || '点「开始追踪」，在上面区域移动鼠标' }} · {{ track.centered ? '已在中心' : '尚未到中心' }} · 偏离 {{ track.turned_deg ?? 0 }}°<template v-if="track.reason"> · {{ track.reason }}</template></p>
    </div>
  </section>
</template>

<style scoped>
.track-panel { min-width: 0; }
.track-panel .panel-bd { gap: 6px; }
.track-panel .panel-bd > p { margin: 0; }
.track-panel .row { gap: 6px; }
.stage { position: relative; height: 190px; border: 1px solid var(--line, #2a3340); border-radius: 6px;
  background: radial-gradient(circle at 50% 45%, rgba(90,140,200,.14), rgba(10,14,20,.9) 70%); overflow: hidden; cursor: crosshair; }
.cross { position: absolute; left: 50%; top: 50%; width: 26px; height: 26px; transform: translate(-50%, -50%);
  border: 1px dashed rgba(150,190,240,.55); border-radius: 50%; pointer-events: none; }
.hand { position: absolute; transform: translate(-50%, -50%); pointer-events: none; line-height: 1;
  filter: drop-shadow(0 0 6px rgba(120,200,255,.65)); }
.hint { position: absolute; left: 8px; bottom: 6px; font-size: 11px; opacity: .55; pointer-events: none; }
.track-panel input[type="number"] { width: 100%; min-width: 0; }
/* 与跟随面板同一套网格：轴名一列（48px），中心与尺寸各占一列并排。 */
.range-grid { display: grid; grid-template-columns: 48px minmax(0, 1fr) minmax(0, 1fr);
  gap: 7px 6px; align-items: center; font-size: 11px; }
.range-grid > span { white-space: nowrap; opacity: .85; }
.range-grid input { width: 100%; min-width: 0; box-sizing: border-box; }
.range-field { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.range-field input[type="range"] { padding: 0; height: 16px; accent-color: var(--acc); }
.track-panel .row .btn--xs { flex: 0 0 auto; }
</style>
