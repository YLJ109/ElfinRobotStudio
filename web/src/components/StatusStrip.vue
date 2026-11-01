<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { taskProjection, readinessProjection } from '../domain/readiness.js'

const props=defineProps({interactionVisible:{type:Boolean,default:false}})
const w = useWorld()
const { snap, view } = storeToRefs(w)

const PERM_CN = { readonly: '只读', power: '电源', motion: '运动' }
const LV = {
  unknown: ['状态未知', 'warn'], green: ['正常', 'ok'], yellow: ['注意', 'warn'], red: ['危险', 'err'], alarm: ['告警', 'err'],
}
const handScene = computed(() => props.interactionVisible && view.value==='hand-follow' && snap.value?.mode==='sim' && !snap.value?.real)
const now = ref(Date.now())
const currentState = computed(() => w.connected && w.stateFresh !== false && w.lastFrameAt > 0 && now.value - w.lastFrameAt <= 1500)
const taskStatus = computed(() => taskProjection(snap.value, currentState.value))
// 取放路径预检是唯一会让操作者干等十几秒的环节，后端本来就按"第几步/共几步"上报，
// 这里把它画成一条真实进度条；只读现有字段，不新增轮询，也不伪造进度。
const preflightBar = computed(() => {
  const play = snap.value?.robot_play
  const p = play?.progress
  if (!play?.planning || !Number.isInteger(p?.step) || !Number.isInteger(p?.total) || p.total <= 0) return null
  const step = Math.max(0, Math.min(p.total, p.step))
  return { pct: Math.round(step / p.total * 100), text: `${step}/${p.total}` }
})
const readiness = computed(() => readinessProjection(snap.value, currentState.value))
const detailsOpen = ref(false)
function locate(item) {
  w.view = item.view
  if (item.systemTab) w.systemTab = item.systemTab
  detailsOpen.value = false
}
const level = computed(() => {
  if (!currentState.value) return 'unknown'
  const safety = snap.value?.safety?.level || 'green'
  if (safety !== 'green') return safety
  return handScene.value && snap.value.hand_follow?.status === '保持位置' ? 'yellow' : 'green'
})
const real = computed(() => (snap.value ? snap.value.real : null))
const isReal = computed(() => snap.value && snap.value.mode === 'real')
const f = (v, d = 0) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(d))

const reason = computed(() => {
  const s = snap.value
  if (!s) return '等待后端…'
  if (!currentState.value) return `最后快照 · ${w.lastFrameAt ? Math.max(0, Math.floor((now.value - w.lastFrameAt) / 1000)) + '秒前' : '未收到有效状态'}`
  if (s.safety?.reasons?.length) return s.safety.reasons.join('；')
  if (handScene.value) return s.hand_follow?.status==='保持位置' ? s.hand_follow.why : ''
  return ''
})
const paused = computed(() => !!(snap.value && snap.value.paused))
const primary = computed(() => currentState.value ? (((snap.value || {}).ctrl || {}).primary || { label: '连接中…' }) : { label: '等待有效状态' })

const clock = ref('')
let t = null
onMounted(() => { t = setInterval(() => (now.value = Date.now(), clock.value = new Date().toTimeString().slice(0, 8)), 1000) })
onBeforeUnmount(() => clearInterval(t))
</script>

<template>
  <footer class="statusbar rise d3" :class="'lv-' + level">
    <div class="sb-scroll">
      <span class="sb-cap lead" :class="LV[level][1]">
        <i class="sb-dot"></i>{{ handScene && !snap?.hand_follow?.configured && level === 'green' ? '待开始' : LV[level][0] }}
      </span>

      <span class="sb-cap" :class="currentState && isReal ? 'ok' : 'warn'">
        <i class="sb-dot"></i>{{ isReal ? '真机' : '仿真' }}
      </span>

      <button v-if="paused && !handScene" type="button" class="sb-cap btn btn--xs err" :disabled="!currentState" @click="w.resume()">
        <i class="sb-dot"></i>已暂停 <b>恢复</b>
      </button>

      <span class="sb-sep"></span>

      <template v-if="isReal && real">
        <span class="sb-cap">
          取流<i class="sb-dot" :class="currentState && real.stream_up ? 'ok' : 'err'"></i>
          命令<i class="sb-dot" :class="currentState && real.cmd_up ? 'ok' : 'err'"></i>
        </span>
        <span class="sb-cap">FSM <b>{{ f(real.fsm) }}</b></span>
        <span class="sb-cap">使能 <b>{{ f(real.enabled) }}</b></span>
        <span class="sb-cap" :class="real.err_code ? 'err' : ''">错误 <b>{{ f(real.err_code) }}</b></span>
        <span class="sb-cap">帧率 <b>{{ f(real.rate_hz, 1) }}</b>Hz</span>
        <span class="sb-cap" :class="(real.lag_ms || 0) > 150 ? 'warn' : ''">
          滞后 <b>{{ f(real.lag_ms) }}</b>ms
        </span>
      </template>

      <span v-if="!handScene" class="sb-cap">许可 <b>{{ PERM_CN[snap ? snap.perm_name : ''] || '--' }}</b></span>
      <span v-if="!handScene" class="sb-cap" :class="currentState && snap && snap.motion_granted ? 'ok' : ''">
        <i class="sb-dot" :class="currentState && snap && snap.motion_granted ? 'ok' : ''"></i><b>{{ primary.label }}</b>
      </span>

      <span v-if="reason" class="sb-cap why" :title="reason">{{ reason }}</span>
      <span class="sb-cap" :class="taskStatus.stop === 'pending' ? 'warn' : ''" :title="taskStatus.next">
        {{ taskStatus.owner }} · {{ taskStatus.phase }} <b v-if="taskStatus.count">{{ taskStatus.count }}</b>
      </span>
      <span v-if="preflightBar" class="sb-cap sb-bar" role="progressbar"
            :aria-valuenow="preflightBar.pct" aria-valuemin="0" aria-valuemax="100"
            :aria-label="`路径预检 ${preflightBar.text} 步`" :title="`路径预检 ${preflightBar.text} 步`">
        <i><em :style="{ width: preflightBar.pct + '%' }"></em></i>
        <b>{{ preflightBar.text }}</b>
      </span>
    </div>

    <div class="sb-right">
      <button type="button" class="sb-cap btn btn--xs" :aria-expanded="detailsOpen" @click="detailsOpen = !detailsOpen">启动条件</button>
      <span class="sb-info mono">{{ clock || '--:--:--' }}</span>
    </div>
    <Teleport to="body">
    <section v-if="detailsOpen" class="readiness-popover" aria-label="启动条件与故障定位">
      <div class="readiness-heading"><b>启动条件</b><button type="button" class="btn btn--xs" @click="detailsOpen = false">关闭</button></div>
      <article v-for="item in readiness" :key="item.id">
        <div><b>{{ item.label }}</b><span>{{ item.state === 'ready' ? '已核对' : item.state === 'blocked' ? '需处理' : '待核验' }}</span></div>
        <p>{{ item.reason }}</p><p>下一步：{{ item.next }}</p><p>完成条件：{{ item.done }}</p>
        <button type="button" class="btn btn--xs" @click="locate(item)">前往处理</button>
      </article>
    </section>
    </Teleport>
  </footer>
</template>

<style scoped>
.statusbar {
  position: relative;
  /* The footer owns a backdrop-filter stacking context: its children cannot
     escape it. Keep diagnostics above camera (40), below safety masks (60). */
  z-index: 50;
  display: flex; align-items: center; gap: 8px; height: 36px; flex: 0 0 36px;
  padding: 0 8px;
  background: linear-gradient(180deg, rgba(8, 12, 20, .78), rgba(6, 9, 16, .96));
  border-top: 1px solid var(--line);
  backdrop-filter: blur(10px);
}
.sb-scroll { display: flex; align-items: center; gap: 4px; min-width: 0; flex: 1;
             overflow-x: auto; scrollbar-width: none; }
/* 预检进度：底栏是固定深色底，所以用固定浅色轨道，不跟随主题变量。 */
.sb-bar { gap: 6px; flex-shrink: 0; }
.sb-bar i { display: block; width: 72px; height: 6px; border-radius: 3px;
            background: rgba(255, 255, 255, .16); overflow: hidden; }
.sb-bar i em { display: block; height: 100%; border-radius: 3px;
               background: #5aa9ff; transition: width .3s linear; }
.sb-scroll::-webkit-scrollbar { height: 0; }
.sb-cap { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0;
          padding: 3px 9px; border: 1px solid transparent; border-radius: 12px;
          font-size: 11.5px; color: var(--tx-2); white-space: nowrap; background: none; }
.sb-cap b { font-family: var(--font-mono); font-weight: 700; color: var(--tx-1); }
.sb-cap.ok b { color: var(--ok); }
.sb-cap.warn { color: #ffc861; }
.sb-cap.warn b { color: #ffc861; }
.sb-cap.err { color: #ff8fa3; background: rgba(255, 77, 109, .08); border-color: rgba(255, 77, 109, .3); }
.sb-cap.err b { color: #ffb3c1; }
.sb-cap.lead { font-weight: 600; }
.sb-cap.lead.ok { color: var(--ok); }
.sb-cap.lead.ok .sb-dot { background: var(--ok); box-shadow: 0 0 7px var(--ok); }
.sb-cap.lead.warn .sb-dot { background: var(--warn); box-shadow: 0 0 7px var(--warn); }
.sb-cap.lead.err { color: #ff8fa3; background: rgba(255, 77, 109, .1); border-color: rgba(255, 77, 109, .38); }
.sb-cap.lead.err .sb-dot { background: var(--danger); box-shadow: 0 0 8px var(--danger);
                           animation: breathe 1s ease-in-out infinite; }
.sb-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
          background: var(--tx-3); }
.sb-dot.ok { background: var(--ok); box-shadow: 0 0 7px var(--ok); }
.sb-dot.warn { background: var(--warn); box-shadow: 0 0 7px var(--warn); }
.sb-dot.err { background: var(--danger); box-shadow: 0 0 7px var(--danger); }
@keyframes breathe { 50% { opacity: .35; } }
.sb-sep { width: 1px; height: 15px; background: var(--line-2); flex-shrink: 0; }
.why { color: var(--tx-3); max-width: 46%; overflow: hidden; text-overflow: ellipsis; }
.sb-right { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.sb-cap.btn { cursor: pointer; border-color: var(--line); background: rgba(255, 255, 255, .015);
              max-width: 300px; font-family: var(--font-mono); font-size: 11px; }
.sb-cap.btn:hover { background: rgba(122, 162, 220, .1); border-color: var(--line-3); }
.sb-info { display: inline-flex; align-items: center; padding: 3px 8px; border-radius: 12px;
           border: 1px solid var(--line); background: rgba(255, 255, 255, .015);
           font-size: 11.5px; color: var(--tx-2); }
.readiness-popover { position: fixed; right: 8px; bottom: 42px; width: min(480px, calc(100vw - 24px)); max-height: min(620px, 75vh); overflow-y: auto; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--bg-1, #101724); box-shadow: 0 8px 30px #0008; z-index: 50; }
.readiness-heading, .readiness-popover article > div { display: flex; justify-content: space-between; gap: 12px; }
.readiness-popover article { padding: 10px 0; border-bottom: 1px solid var(--line); }
.readiness-popover p { margin: 5px 0; color: var(--tx-2); font-size: 12px; line-height: 1.5; }
</style>
