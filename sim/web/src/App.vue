<script setup>
import { onMounted, onBeforeUnmount, ref, watch, nextTick, computed, defineAsyncComponent } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld, setFrameHandler, latestFrame } from './store'
import TopBar from './components/TopBar.vue'
import NavRail from './components/NavRail.vue'
import WorkPanel from './panels/WorkPanel.vue'
import StatusStrip from './components/StatusStrip.vue'
import LogDock from './components/LogDock.vue'
import ObjectProps from './components/ObjectProps.vue'
import CameraWidget from './components/CameraWidget.vue'
import SimPanel from './panels/SimPanel.vue'
import PowerPanel from './panels/PowerPanel.vue'
import AboutPanel from './panels/AboutPanel.vue'
/* 不常进的页拆成异步块：首屏只下载真在用的那几个，遥测/日志/设置/关于按需取。
   写死路径而不是模板字符串 —— 模板字符串会被打成 glob，把所有面板都当成动态入口，
   结果静态引用的那几个反而拆不出去（vite 会直接警告）。 */
const lazy = (loader) => defineAsyncComponent(loader)
const ObjectsPanel = lazy(() => import('./panels/ObjectsPanel.vue'))
const EngineeringPanel = lazy(() => import('./panels/EngineeringPanel.vue'))
const LogsPanel = lazy(() => import('./panels/LogsPanel.vue'))
const SettingsPanel = lazy(() => import('./panels/SettingsPanel.vue'))
const TelemetryPanel = lazy(() => import('./panels/TelemetryPanel.vue'))

const w = useWorld()
const { snap, view, workTab, preview, selObj } = storeToRefs(w)
const canvasBox = ref(null)
const sideEl = ref(null)
const sideOpen = ref(localStorage.getItem('huayan.side.open') !== '0')
const sideWidth = ref(Math.max(300, Math.min(560, Number(localStorage.getItem('huayan.side.width')) || 384)))
const resizingSide = ref(false)
let scene = null
let disposed = false
const sceneLoading = ref(true)

const PANELS = { sim: SimPanel, power: PowerPanel, work: WorkPanel, objects: ObjectsPanel, engineering: EngineeringPanel, tele: TelemetryPanel,
                 logs: LogsPanel, settings: SettingsPanel, about: AboutPanel }
const Panel = computed(() => PANELS[view.value] || SimPanel)
/* 自己管滚动的页（程序 3/4+1/4、AI 整栏、日志拉伸）外壳就不能再滚，否则双层滚动条 */
const selfScroll = computed(() =>
  view.value === 'logs' || view.value === 'engineering' || (view.value === 'work' && ['program', 'ai'].includes(workTab.value)))

const level = computed(() => (snap.value ? snap.value.safety.level : 'green'))
const tcp = computed(() => (snap.value ? snap.value.tcp : null))
/* 真机链路没通时关节角是"未知"，不是零位 —— 这时候 HUD 与三维都不许装作在读数 */
const poseKnown = computed(() => !snap.value || snap.value.joints.known !== false)

/* 位姿口径：基座(控制器 PCS_Base) / 工具(PCS_Tool) / 法兰(本软件正解 FK)。
   三个数来自不同源，混着看会差一个工具偏置，所以做成显式切换并标在 HUD 上。 */
const FRAMES = [['base', '基座', 'PCS_Base'], ['tool', '工具', 'PCS_Tool'], ['fk', '法兰正解', 'FK(q)']]
const frame = ref('base')
const poseShown = computed(() => {
  const s = snap.value
  if (!s) return null
  if (frame.value === 'fk') return s.tcp
  const raw = (s.real || {})[frame.value === 'tool' ? 'pcs_tool' : 'pcs_base']
  if (!raw || raw.length < 6) return s.tcp        // 没读到就退回 FK，但下面会标出来
  return { xyz_mm: raw.slice(0, 3), rpy_deg: raw.slice(3, 6) }
})
const poseSrc = computed(() => {
  const s = snap.value
  if (!s) return ''
  if (frame.value === 'fk') return 'FK 正解'
  const raw = (s.real || {})[frame.value === 'tool' ? 'pcs_tool' : 'pcs_base']
  return raw && raw.length >= 6 ? '控制器直读' : '无读数·退回 FK'
})
const f = (v, d = 1) => (v === null || v === undefined ? '--' : Number(v).toFixed(d))

const VIEWS = ['sim', 'power', 'work', 'objects', 'engineering', 'tele', 'logs', 'settings', 'about']
const TABS = ['points', 'program', 'ai', 'objects', 'engineering']
function readHash() {
  const [v, t] = decodeURIComponent(location.hash.replace(/^#\/?/, '')).split('/')
  if (v === 'work' && t === 'engineering') { w.view = 'work'; w.workTab = 'engineering'; return }
  if (VIEWS.includes(v)) w.view = v
  if (TABS.includes(t)) w.workTab = t
}
function writeHash() {
  const h = '#/' + view.value + (view.value === 'work' ? '/' + workTab.value : '')
  if (location.hash !== h) history.replaceState(null, '', h)
}
readHash()                       // 先吃 URL，再挂同步，否则 immediate 会把 #/power 覆回 #/sim
watch([view, workTab], writeHash, { immediate: true })

onMounted(async () => {
  await nextTick()
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onKey)
  window.addEventListener('hashchange', readHash)
  document.addEventListener('click', onFoldClick, true)
  w.boot()
  try {
    const { createScene } = await import('./scene')
    scene = await createScene(canvasBox.value)
    if (disposed) { scene.dispose(); return }
    scene.onPick = (id) => { w.selObj = id }
    setFrameHandler(feed3d)
    if (latestFrame()) feed3d(latestFrame())
  } catch (error) { w.error = '三维加载失败：' + error.message }
  finally { sceneLoading.value = false }
})
function onResize() { scene && scene.resize() }
function toggleSide() {
  sideOpen.value = !sideOpen.value
  localStorage.setItem('huayan.side.open', sideOpen.value ? '1' : '0')
  requestAnimationFrame(() => scene && scene.resize())
}
function beginSideResize(ev) {
  if (!sideOpen.value || !sideEl.value) return
  resizingSide.value = true
  ev.currentTarget.setPointerCapture?.(ev.pointerId)
  window.addEventListener('pointermove', resizeSide)
  window.addEventListener('pointerup', endSideResize, { once: true })
  window.addEventListener('pointercancel', endSideResize, { once: true })
  ev.preventDefault()
}
function resizeSide(ev) {
  if (!resizingSide.value || !sideEl.value) return
  const left = sideEl.value.getBoundingClientRect().left
  sideWidth.value = Math.max(300, Math.min(560, Math.round(ev.clientX - left)))
  scene && scene.resize()
}
function endSideResize() {
  resizingSide.value = false
  localStorage.setItem('huayan.side.width', String(sideWidth.value))
  window.removeEventListener('pointermove', resizeSide)
  window.removeEventListener('pointerup', endSideResize)
  window.removeEventListener('pointercancel', endSideResize)
}
/* 键盘通路：Esc 急停（不变）· 1-9 切页 · f 切位姿口径 · r 复位视角 · 空格 收残影。
   输入框里打字时一律不拦，否则敲个数字就跳页。 */
function onKey(e) {
  const t = e.target
  if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable || t.closest?.('.cm-editor'))) return
  if (e.key === 'Escape') { w.estop(); return }
  if (e.key === ' ') { if (w.snap && w.snap.preview) { w.cancelPreview(); e.preventDefault() } return }
  const n = Number(e.key)
  if (Number.isInteger(n) && n >= 1 && n <= VIEWS.length) { w.view = VIEWS[n - 1]; return }
  const k = e.key.toLowerCase()
  if (k === 'f') {
    const i = FRAMES.findIndex(([id]) => id === frame.value)
    frame.value = FRAMES[(i + 1) % FRAMES.length][0]
  } else if (k === 'r') scene && scene.resetView()
}

/* 点卡片标题折叠。落在按钮/输入框上的点击一律不算，否则一按就收起来。 */
function onFoldClick(e) {
  const el = e.target
  if (!el || !el.closest) return
  if (el.closest('button, input, select, textarea, a, label, .no-fold')) return
  const hd = el.closest('.panel-hd')
  if (hd && hd.parentElement) hd.parentElement.classList.toggle('folded')
}

/* 3D 走非响应式通道：WS 每帧直接喂进来，不经过 Vue 的依赖追踪；
   UI 那份 snap 在 store 里限到 20Hz，两边互不拖累。
   假手（同一台半透明模型）有三个用途，优先级从高到低：
     残影预览（后端登记的 preview_q） > 时间轴回放帧 > 不显示。
   "什么时候收残影"不由前端判 —— 后端等真机真的站上去（±0.3° 且站住 0.5s）才清 preview_q，
   前端只是如实渲染后端给的那一份真相，避免出现"界面以为收了、后端还留着"的两份状态。 */
let sampleIdentity = null
function feed3d(s) {
  if (!scene) return
  // 链路断了就不更新姿态：宁可让臂停在"最后一次已知位置"，也不要跳回零位装作在读真机
  const identity = `${s.session_id}:${s.mode}:${s.real?.connection_epoch ?? 0}`
  if (s.joints.known !== false) {
    if (identity !== sampleIdentity) scene.setImmediate(s.joints.q)
    else scene.setAngles(s.joints.q)
    sampleIdentity = identity
  }
  scene.setFence(s.settings.fence)
  scene.ghostOpacity = s.settings.ghost_opacity
  scene.setCapsules(s.safety.capsules || [])
  scene.setObjects(s.objects || [], selObj.value)
  scene.markCollisions(s.obj_hits || [])
  scene.setTrailLen(s.settings.trail_len)
  scene.setHealth((s.telemetry || {}).health ? s.telemetry.health.axes : null)
  const g = s.preview || w.playbackGhost || (w.play.on ? w.playFrame() : null)
  if (g) scene.setGhost(g, s.preview ? 'ghost' : 'playback')
  else scene.clearGhosts()
}
watch(preview, (p) => { if (!p && scene && !w.play.on) scene.clearGhosts() })
onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('hashchange', readHash)
  window.removeEventListener('pointermove', resizeSide)
  window.removeEventListener('pointerup', endSideResize)
  window.removeEventListener('pointercancel', endSideResize)
  document.removeEventListener('click', onFoldClick, true)
  setFrameHandler(null)
  scene && scene.dispose()
})
</script>

<template>
  <div class="app">
    <TopBar />

    <div class="main" :class="{ 'side-closed': !sideOpen }">
      <NavRail :expanded="sideOpen" @toggle-side="toggleSide" @reveal-side="!sideOpen && toggleSide()" />

      <aside v-show="sideOpen" ref="sideEl" class="side" :class="{ resizing: resizingSide }"
             :style="sideOpen ? { '--side-width': sideWidth + 'px' } : undefined">
        <component :is="Panel" class="pane" :class="{ tight: selfScroll }" />
        <div v-if="sideOpen" class="side-resizer" @pointerdown="beginSideResize" title="拖动调整面板宽度"></div>
      </aside>

      <section class="stage" :class="{ alarm: level === 'alarm' }">
        <span v-if="sceneLoading" class="hud" role="status">三维模型加载中…</span>
        <div ref="canvasBox" class="gl"></div>
        <CameraWidget class="stage-camera" />
        <div class="hud tl mono">
          <button class="fr no-fold" :title="'当前口径：' + poseSrc + '（按 f 切换）'"
                  @click="frame = FRAMES[(FRAMES.findIndex(([id]) => id === frame) + 1) % FRAMES.length][0]">
            TCP {{ FRAMES.find(([id]) => id === frame)[1] }}
          </button>
          <template v-if="poseKnown && poseShown">
            <span>X {{ f(poseShown.xyz_mm[0]) }}</span>
            <span>Y {{ f(poseShown.xyz_mm[1]) }}</span>
            <span>Z {{ f(poseShown.xyz_mm[2]) }}</span>
            <span class="sep">|</span>
            <span>A {{ f(poseShown.rpy_deg[0], 2) }}</span>
            <span>B {{ f(poseShown.rpy_deg[1], 2) }}</span>
            <span>C {{ f(poseShown.rpy_deg[2], 2) }}</span>
          </template>
          <span v-else class="bad">姿态数据不可用</span>
        </div>
        <div v-if="preview || w.play.on" class="hud tr">
          <span v-if="preview" class="tag tag--warn">目标残影</span>
          <span v-else class="tag tag--info">回放游标 · 第 {{ w.play.idx + 1 }} 帧</span>
        </div>
        <div class="hud bl">
          <button v-if="preview" class="btn btn--xs btn--primary" @click="w.cancelPreview()">
            收掉残影（滑块回真值）
          </button>
          <button class="btn btn--xs" @click="scene && scene.resetView()">复位视角</button>
          <button class="btn btn--xs" @click="scene && scene.clearTrail()">清除轨迹</button>
        </div>

        <ObjectProps v-if="selObj" class="props" />
        <LogDock class="dock" />
      </section>
    </div>

    <StatusStrip />
  </div>
</template>

<style scoped>
.app { position: relative; z-index: 1; display: flex; flex-direction: column; height: 100%; }
.main { display: flex; flex: 1; min-height: 0; gap: 6px; padding: 6px; }

/* 导轨在左上，工作栏紧贴导轨右侧，三维舞台占剩下的宽度 */
.side { position: relative; width: var(--side-width, 384px); flex: 0 0 var(--side-width, 384px); display: flex; min-height: 0; transition: width .18s ease, flex-basis .18s ease; }
.side.resizing { transition: none; user-select: none; }
.side-resizer { position: absolute; top: 0; right: -3px; bottom: 0; z-index: 7; width: 7px; cursor: col-resize; touch-action: none; }
.side-resizer:hover, .side-resizer:active { background: var(--acc-soft); }
.pane { flex: 1; min-width: 0; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
.pane.tight { overflow: hidden; }

.stage { flex: 1 1 auto; position: relative; min-width: 260px; overflow: hidden;
         background: #05090f; border: 1px solid var(--line); border-radius: var(--r-md); }
.stage::before { content: ''; position: absolute; inset: 0 0 auto 0; height: 1px; z-index: 2;
                 pointer-events: none;
                 background: linear-gradient(90deg, transparent, rgba(47, 214, 240, .5), transparent); }
.stage.alarm { animation: pulse-bad .55s ease-in-out infinite; }
.gl { position: absolute; inset: 0; }
.gl :deep(canvas) { display: block; }

.hud { position: absolute; display: flex; align-items: center; gap: 8px;
       font-size: 11.5px; color: var(--tx-2); text-shadow: 0 1px 3px #000; }
.hud.tl { top: 8px; left: 10px; right: 100px; flex-wrap: wrap; pointer-events: none; }
.hud.tr { top: 44px; right: 10px; pointer-events: none; }
.stage-camera { position: absolute; top: 8px; right: 10px; z-index: 10; }
.hud.bl { bottom: 8px; left: 10px; font-size: 11px; }
.hud .k { color: var(--acc); font-weight: 700; letter-spacing: .1em; }
.hud .sep { opacity: .35; }
.hud .fr { font: inherit; font-size: 11.5px; color: var(--acc); background: var(--bg-inset);
           border: 1px solid var(--line-2); border-radius: 5px; padding: 1px 6px; cursor: pointer;
           letter-spacing: .06em; }
.hud .fr:hover { border-color: var(--acc); }
.hud .src { color: var(--tx-3); font-size: 10px; letter-spacing: .04em; }
.hud .bad { color: var(--danger); max-width: 60%; line-height: 1.4; }

/* 物体属性盒子贴在舞台右上，日志栏钉在右下 */
.props { position: absolute; top: 76px; right: 10px; z-index: 4; width: 262px; }
.dock { position: absolute; right: 10px; bottom: 12px; z-index: 4; width: 460px; max-width: 48%; }

.mask { position: fixed; inset: 0; z-index: 60; background: rgba(2, 5, 10, .74);
        display: flex; align-items: center; justify-content: center; backdrop-filter: blur(3px); }
.dlg { width: 440px; max-width: 92vw; }
.chk { display: flex; gap: 8px; align-items: flex-start; line-height: 1.55; font-size: 12.5px; cursor: pointer; }

@media (max-width: 1240px) {
  .side { width: min(var(--side-width, 384px), 36vw); flex-basis: min(var(--side-width, 384px), 36vw); }
  .dock { display: none; }
}
@media (max-width: 800px) {
  .main { display: grid; grid-template-columns: 58px minmax(0, 1fr);
          grid-template-rows: minmax(0, 1fr) minmax(0, .7fr); }
  .main.side-closed { grid-template-rows: minmax(0, 1fr); }
  .rail { grid-column: 1; grid-row: 1 / 3; width: 58px; min-height: 0; overflow-y: auto; }
  .stage { grid-column: 2; grid-row: 1; min-width: 0; }
  .side { grid-column: 2; grid-row: 2; width: 100%; min-width: 0; }
  .side-resizer { display: none; }
}
</style>
