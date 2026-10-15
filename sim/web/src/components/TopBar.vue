<script setup>
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { snap } = storeToRefs(w)

const isReal = computed(() => snap.value && snap.value.mode === 'real')
const ack = computed(() => (snap.value ? snap.value.speed_ack : null))
/* 仿真速度和真机速度各存各的：这一根滑条只驱动当前模式那一路，切模式后数值跟着切。 */
const speed = computed(() => (snap.value ? snap.value.speed_pct : 10))
const shown = ref(speed.value)
watch(speed, (v) => { shown.value = v })
const fill = computed(() => ({ '--fill': shown.value + '%' }))
function drag(ev) { shown.value = Number(ev.target.value) }
async function commit(ev) { await w.setSpeed(ev.target.value) }
const speedBad = computed(() => isReal.value && ack.value && ack.value.ok === false)

</script>

<template>
  <header class="top rise">
    <div class="brand">
      <svg class="mark" viewBox="0 0 28 28" aria-hidden="true">
        <path d="M4 22 L14 4 L24 22" fill="none" stroke="currentColor" stroke-width="1.6" />
        <circle cx="14" cy="4" r="2.4" fill="currentColor" />
        <circle cx="4" cy="22" r="1.9" fill="none" stroke="currentColor" stroke-width="1.4" />
        <circle cx="24" cy="22" r="1.9" fill="none" stroke="currentColor" stroke-width="1.4" />
        <path d="M2 26 h24" stroke="currentColor" stroke-width="1.2" opacity=".45" />
      </svg>
      <div class="btxt">
        <div class="t1">ElfinRobotStudio</div>
        <div class="t2 mono">华沿机器人仿真与监控台 · E05-1195_Pro</div>
      </div>
    </div>

    <span class="grow"></span>

    <div class="speed" :class="{ bad: speedBad }"
         :title="isReal
           ? '真机速度：下发 SetOverride（协议 p72，0.01~1.0），控制器实际值见下方读数'
           : '仿真速度：只改本地插值快慢，与真机无关'">
      <span class="slbl">{{ isReal ? '真机速度' : '仿真速度' }}</span>
      <input type="range" min="1" max="100" step="1" :value="shown" :style="fill"
             @input="drag" @change="commit" aria-label="速度百分比" />
      <b class="sval mono">{{ shown.toFixed(0) }}<i>%</i></b>
      <i v-if="speedBad" class="warnmark" :title="ack.why">!</i>
    </div>

    <button class="btn btn--danger estop" @click="w.estop()"
            title="GrpStop：停止运动，不抱闸、不断电。Esc 同样有效。">急停</button>
  </header>
</template>

<style scoped>
.top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 6px 8px;
       border-bottom: 1px solid var(--line);
       background: linear-gradient(180deg, rgba(12, 19, 32, .92), rgba(7, 11, 20, .6)); }
.brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
.mark { width: 26px; height: 26px; color: var(--acc); flex-shrink: 0;
        filter: drop-shadow(0 0 6px rgba(47, 214, 240, .45)); }
.btxt { min-width: 0; }
.t1 { font-family: var(--font-display); font-size: 14.5px; font-weight: 600; letter-spacing: .04em; }
.t2 { font-size: 10.5px; color: var(--tx-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.speed { display: flex; align-items: center; gap: 8px; padding: 2px 8px;
         background: var(--bg-inset); border: 1px solid var(--line); border-radius: 8px; }
.speed.bad { border-color: rgba(255, 176, 32, .55); }
.slbl { font-size: 11px; color: var(--tx-3); white-space: nowrap; }
.speed input { width: 104px; }
.sval { font-size: 15px; font-weight: 700; color: var(--acc); min-width: 34px; text-align: right;
        font-variant-numeric: tabular-nums; }
.sval i { font-style: normal; font-size: 10px; color: var(--tx-3); margin-left: 1px; }
.warnmark { width: 14px; height: 14px; line-height: 14px; text-align: center; font-style: normal;
            font-size: 11px; font-weight: 700; color: #04131a; background: var(--warn);
            border-radius: 50%; }

.estop { font-weight: 700; letter-spacing: .12em; padding: 0 10px; }
</style>
