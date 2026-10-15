<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { snap, logs, connected, cameras } = storeToRefs(w)

const PERM_CN = { readonly: '只读', power: '电源', motion: '运动' }
const LV = {
  green: ['正常', 'ok'], yellow: ['注意', 'warn'], red: ['危险', 'err'], alarm: ['告警', 'err'],
}
const level = computed(() => (snap.value ? snap.value.safety.level : 'green'))
const real = computed(() => (snap.value ? snap.value.real : null))
const isReal = computed(() => snap.value && snap.value.mode === 'real')
const last = computed(() => (logs.value.length ? logs.value[logs.value.length - 1] : null))
const f = (v, d = 0) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(d))

const reason = computed(() => {
  const s = snap.value
  if (!s) return '等待后端…'
  if (s.safety.reasons.length) return s.safety.reasons.join('；')
  return '六轴占用、围栏、自碰撞、地面全部正常'
})
const paused = computed(() => !!(snap.value && snap.value.paused))
const primary = computed(() => (((snap.value || {}).ctrl || {}).primary || { label: '连接中…' }))

const clock = ref('')
let t = null
onMounted(() => { t = setInterval(() => (clock.value = new Date().toTimeString().slice(0, 8)), 1000) })
onBeforeUnmount(() => clearInterval(t))
</script>

<template>
  <footer class="statusbar rise d3" :class="'lv-' + level">
    <div class="sb-scroll">
      <span class="sb-cap lead" :class="LV[level][1]">
        <i class="sb-dot"></i>{{ LV[level][0] }}
        <b>{{ snap ? snap.safety.max_pct.toFixed(1) : '--' }}%</b>
      </span>

      <span class="sb-cap" :class="isReal ? 'ok' : 'warn'">
        <i class="sb-dot"></i>{{ isReal ? '真机' : '仿真' }}
      </span>
      <span class="sb-cap" :class="connected ? 'ok' : 'err'">
        <i class="sb-dot" :class="connected ? 'ok' : 'err'"></i>{{ connected ? '后端在线' : '后端重连中' }}
      </span>
      <span class="sb-cap" :class="cameras.active?.state === 'streaming' ? 'ok' : ''">
        <i class="sb-dot" :class="cameras.active?.state === 'streaming' ? 'ok' : ''"></i>
        {{ cameras.active ? '摄像头 #' + cameras.active.number + ' ' + ({starting:'等待首帧',streaming:'采集中',error:'采集失败'})[cameras.active.state] : '摄像头未启用' }}
      </span>

      <span v-if="paused" class="sb-cap err" @click="w.resume()">
        <i class="sb-dot"></i>已暂停 <b>点此恢复</b>
      </span>

      <span class="sb-sep"></span>

      <template v-if="isReal && real">
        <span class="sb-cap">
          取流<i class="sb-dot" :class="real.stream_up ? 'ok' : 'err'"></i>
          命令<i class="sb-dot" :class="real.cmd_up ? 'ok' : 'err'"></i>
        </span>
        <span class="sb-cap">FSM <b>{{ f(real.fsm) }}</b></span>
        <span class="sb-cap">使能 <b>{{ f(real.enabled) }}</b></span>
        <span class="sb-cap" :class="real.err_code ? 'err' : ''">错误 <b>{{ f(real.err_code) }}</b></span>
        <span class="sb-cap">帧率 <b>{{ f(real.rate_hz, 1) }}</b>Hz</span>
        <span class="sb-cap" :class="(real.lag_ms || 0) > 150 ? 'warn' : ''">
          滞后 <b>{{ f(real.lag_ms) }}</b>ms
        </span>
      </template>

      <span class="sb-cap">{{ isReal ? '真机速度' : '仿真速度' }}
        <b>{{ snap ? snap.speed_pct.toFixed(0) : '--' }}</b>%
      </span>
      <span class="sb-cap">许可 <b>{{ PERM_CN[snap ? snap.perm_name : ''] || '--' }}</b></span>
      <span class="sb-cap" :class="snap && snap.motion_granted ? 'ok' : ''">
        <i class="sb-dot" :class="snap && snap.motion_granted ? 'ok' : ''"></i><b>{{ primary.label }}</b>
      </span>

      <span class="sb-sep"></span>
      <span class="sb-cap why" :title="reason">{{ reason }}</span>
    </div>

    <div class="sb-right">
      <button class="sb-cap btn" @click="w.view = 'logs'" :title="last ? last.text : ''">
        <i class="sb-dot" :class="last && last.kind === 'alarm' ? 'err' : 'ok'"></i>
        <span class="ellip">{{ last ? last.text : '暂无日志' }}</span>
      </button>
      <span class="sb-info mono">{{ clock || '--:--:--' }}</span>
    </div>
  </footer>
</template>

<style scoped>
.statusbar {
  display: flex; align-items: center; gap: 8px; height: 36px; flex: 0 0 36px;
  padding: 0 8px;
  background: linear-gradient(180deg, rgba(8, 12, 20, .78), rgba(6, 9, 16, .96));
  border-top: 1px solid var(--line);
  backdrop-filter: blur(10px);
}
.sb-scroll { display: flex; align-items: center; gap: 4px; min-width: 0; flex: 1;
             overflow-x: auto; scrollbar-width: none; }
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
</style>
