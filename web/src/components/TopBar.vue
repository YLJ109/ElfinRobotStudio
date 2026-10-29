<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { speedState } from '../connectionState.js'

const w = useWorld()
const { snap } = storeToRefs(w)
const desktop = globalThis.elfinDesktop || null
const windowState = ref({ maximized: false, focused: true })
let unsubscribeWindow = null
onMounted(async () => {
  if (!desktop) return
  unsubscribeWindow = desktop.onWindowState(value => { windowState.value = value })
  windowState.value = await desktop.getWindowState()
})
onBeforeUnmount(() => unsubscribeWindow?.())
function titleDoubleClick(event) {
  if (desktop && !event.target.closest('button,input,select,textarea,a')) desktop.toggleMaximize()
}

const isReal = computed(() => snap.value && snap.value.mode === 'real')
const speedInfo = computed(() => speedState(snap.value, w.connected))
const speed = computed(() => speedInfo.value.actualPct)
const shown = ref(speed.value ?? 1)
const editing = ref(false)
const busy = ref(false)
const editMode = ref(null)
const message = ref('')
watch(speed, (v) => { if (!editing.value && !busy.value) shown.value = v ?? 1 })
const fill = computed(() => ({ '--fill': shown.value + '%' }))
function drag(ev) {
  if (!editing.value) editMode.value = snap.value?.mode
  editing.value = true
  shown.value = Number(ev.target.value)
}
function cancelEdit() { editing.value = false; shown.value = speed.value ?? 1 }
async function commit(ev) {
  if (busy.value) return
  const target = Number(ev.target.value)
  const changedMode = editing.value && editMode.value !== snap.value?.mode
  editing.value = false
  message.value = ''
  if (changedMode || !snap.value || !w.connected) { cancelEdit(); message.value = '连接或模式已变化，未提交倍率'; return }
  if (!Number.isFinite(target) || target < 1 || target > 100) { cancelEdit(); message.value = '倍率必须为 1–100%'; return }
  busy.value = true
  try {
    const result = await w.setSpeed(target)
    if (result?.ok === false) message.value = result.why || '倍率设置失败'
  } catch (error) { message.value = error.message || '倍率请求失败' }
  finally { busy.value = false; shown.value = speed.value ?? 1 }
}
const speedBad = computed(() => !!message.value || (isReal.value
  && (speedInfo.value.rejected || speedInfo.value.mismatch || speed.value === null)))
const speedNote = computed(() => message.value || (editing.value ? `目标 ${shown.value}%（未提交）`
  : busy.value ? `正在提交目标 ${shown.value}%` : speedInfo.value.why))
const speedText = computed(() => speed.value === null ? '--' : Number.isInteger(speed.value) ? speed.value.toFixed(0) : speed.value.toFixed(1))

</script>

<template>
  <header class="top rise" :class="{ desktop: !!desktop, unfocused: desktop && !windowState.focused }" @dblclick="titleDoubleClick">
    <div class="brand">
      <img class="mark" src="/elfin-logo-arm-v1.png" width="34" height="34" alt="ElfinRobotStudio 机械臂标志" />
      <div class="btxt">
        <div class="t1">玖枢</div>
        <div class="t2 mono">E05-1195_Pro</div>
      </div>
    </div>

    <small v-if="speedNote" class="speed-note" :class="{bad:speedBad}" role="status">{{ speedNote }}</small>
    <span class="grow"></span>

    <div class="speed" :class="{ bad: speedBad }"
         :title="isReal
           ? '显示控制器实际速度倍率，不是关节角速度；拖动只预设，松开后下发并等待状态回读'
           : '仿真速度：只改本地插值快慢，与真机无关'">
      <span class="slbl">{{ isReal ? '实际倍率' : '仿真速度' }}</span>
      <input type="range" min="1" max="100" step="1" :value="shown" :style="fill"
             :disabled="!snap || !w.connected || busy || speedInfo.pending || (isReal && speed === null)"
             @input="drag" @change="commit" @pointercancel="cancelEdit" @blur="cancelEdit" aria-label="速度百分比" />
      <b class="sval mono">{{ speedText }}<i>%</i></b>
    </div>

    <button class="btn btn--danger estop" @click="w.estop()"
            title="软件停止（Esc），不替代物理急停。">急停</button>
    <div v-if="desktop" class="window-controls" aria-label="窗口控制" @dblclick.stop>
      <button type="button" class="window-control" aria-label="最小化窗口" title="最小化" @click="desktop.minimize()"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 10.5h10" /></svg></button>
      <button type="button" class="window-control" :aria-label="windowState.maximized ? '还原窗口' : '最大化窗口'" :title="windowState.maximized ? '还原' : '最大化'" @click="desktop.toggleMaximize()">
        <svg v-if="windowState.maximized" viewBox="0 0 16 16" aria-hidden="true"><path d="M5 5V3h8v8h-2M3 5h8v8H3z" /></svg>
        <svg v-else viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 3.5h9v9h-9z" /></svg>
      </button>
      <button type="button" class="window-control window-close" aria-label="关闭窗口" title="关闭" @click="desktop.close()"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8" /></svg></button>
    </div>
  </header>
</template>

<style scoped>
.top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 6px 8px;
       border-bottom: 1px solid var(--line);
       background: linear-gradient(180deg, rgba(12, 19, 32, .92), rgba(7, 11, 20, .6)); }
.brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
.mark { width:34px; height:34px; object-fit:contain; flex-shrink:0; }
.btxt { min-width: 0; }
.t1 { font-family: var(--font-display); font-size: 14.5px; font-weight: 600; letter-spacing: .04em; }
.t2 { font-size: 10.5px; color: var(--tx-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.speed { display: flex; align-items: center; gap: 8px; padding: 2px 8px;
         background: none; border: 0; }
.speed.bad .slbl { color:var(--warn); }
.speed-note { max-width:360px; color:var(--tx-3); font-size:11px; text-align:left; }
.speed-note.bad { color:var(--warn); }
.slbl { font-size: 11px; color: var(--tx-3); white-space: nowrap; }
.speed input { width: 104px; }
.sval { font-size: 15px; font-weight: 700; color: var(--acc); min-width: 34px; text-align: right;
        font-variant-numeric: tabular-nums; }
.sval i { font-style: normal; font-size: 10px; color: var(--tx-3); margin-left: 1px; }
.estop { font-weight: 700; letter-spacing: .12em; padding: 0 10px; }
.top.desktop { -webkit-app-region:drag; user-select:none; flex-wrap:nowrap; min-height:48px; box-shadow:inset 0 1px 0 #ffffff09,0 3px 10px #0005; }
.desktop :is(button,input,select,a,.speed,.window-controls) { -webkit-app-region:no-drag; }
.top.desktop.unfocused { filter:saturate(.75); }
.window-controls { display:flex; align-self:stretch; align-items:stretch; gap:2px; padding-left:9px; margin-left:4px; border-left:1px solid var(--line); }
.window-control { display:grid; place-items:center; width:40px; min-height:32px; padding:0; border:1px solid transparent; border-radius:0; clip-path:none; color:var(--tx-2); background:linear-gradient(#14223080,#0a152280); box-shadow:inset 0 1px 0 #ffffff08,0 1px 2px #0003; cursor:pointer; }
.window-control:hover { color:var(--acc); border-color:var(--line-2); background:#173140; }
.window-control:active { box-shadow:inset 0 1px 4px #0008; background:#091923; }
.window-control:focus-visible { outline:2px solid var(--acc); outline-offset:-3px; }
.window-control svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.2; }
.window-close:hover { color:#fff; background:#a13950; border-color:#b64960; }
</style>
