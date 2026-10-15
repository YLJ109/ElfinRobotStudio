<script setup>
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { snap } = storeToRefs(w)
const s = computed(() => snap.value)
const q = computed(() => (snap.value ? snap.value.joints.q : []))
const lo = computed(() => (snap.value ? snap.value.joints.lower : []))
const hi = computed(() => (snap.value ? snap.value.joints.upper : []))
const vmax = computed(() => (snap.value ? snap.value.joints.vmax : []))
const ratios = computed(() => (snap.value ? snap.value.safety.per_joint : []))
const caps = computed(() => (snap.value ? snap.value.safety.capsules.length : 0))
/* 真机链路没通时关节角是未知的：表里必须显示 --，不能拿仿真的 0.000 冒充读数 */
const known = computed(() => !snap.value || snap.value.joints.known !== false)
const cls = (r) => (r >= 0.9 ? 'bad' : r >= 0.8 ? 'warn' : 'acc')
</script>

<template>
  <div class="col" v-if="s">
    <section class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">运动学总览</span>
      </div>
      <div class="panel-bd">
        <table>
          <thead><tr><th>轴</th><th>当前 °</th><th>限位 °</th><th>°/s</th><th>占用</th></tr></thead>
          <tbody>
            <tr v-for="i in 6" :key="i">
              <td class="disp">J{{ i }}</td>
              <td v-if="known" class="mono" :class="cls(ratios[i - 1] || 0)">{{ q[i - 1].toFixed(3) }}</td>
              <td v-else class="mono dim">--</td>
              <td class="mono dim tiny">[{{ lo[i - 1].toFixed(0) }}, {{ hi[i - 1].toFixed(0) }}]</td>
              <td class="mono tiny">{{ vmax[i - 1].toFixed(0) }}</td>
              <td v-if="known" class="mono" :class="cls(ratios[i - 1] || 0)">{{ ((ratios[i - 1] || 0) * 100).toFixed(0) }}%</td>
              <td v-else class="mono dim">--</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-hd"><span class="panel-title">末端位姿</span>
        <span class="panel-sub">{{ known ? '基座坐标系 · 由当前关节角正解' : '关节角未知 · 不算位姿' }}</span></div>
      <div class="panel-bd">
        <dl class="kv" v-if="known">
          <dt>位置 X / Y / Z</dt><dd>{{ s.tcp.xyz_mm.map((v) => v.toFixed(2)).join('   ') }} mm</dd>
          <dt>姿态 A / B / C</dt><dd>{{ s.tcp.rpy_deg.map((v) => v.toFixed(3)).join('   ') }} °</dd>
        </dl>
        <div v-else class="note bad">
          姿态数据不可用。
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-hd"><span class="panel-title">显示与安全</span><span class="panel-sub">{{ caps }} 根碰撞包络</span></div>
      <div class="panel-bd">
        <div class="row nowrap"><span class="al">残影</span>
          <input class="grow" type="range" min="0.1" max="0.7" step="0.02"
                 :style="{ '--fill': ((s.settings.ghost_opacity - 0.1) / 0.6 * 100) + '%' }"
                 :value="s.settings.ghost_opacity"
                 @change="w.setSettings({ ghost_opacity: Number($event.target.value) })" />
          <b class="mono acc">{{ (s.settings.ghost_opacity * 100).toFixed(0) }}%</b>
        </div>
        <label class="sw"><input type="checkbox" :checked="s.settings.fence.enabled"
               @change="w.setSettings({ fence: { ...s.settings.fence, enabled: $event.target.checked } })" />
          <span>安全围栏（越界即告警）</span></label>
        <label class="sw"><input type="checkbox" :checked="s.settings.self_collision"
               @change="w.setSettings({ self_collision: $event.target.checked })" /><span>自碰撞检测</span></label>
        <label class="sw"><input type="checkbox" :checked="s.settings.ground_enabled"
               @change="w.setSettings({ ground_enabled: $event.target.checked })" /><span>地面碰撞检测</span></label>
        <label class="sw"><input type="checkbox" :checked="s.settings.tts"
               @change="w.setSettings({ tts: $event.target.checked })" /><span>告警语音播报</span></label>
      </div>
    </section>

  </div>
</template>

<style scoped>
.al { font-size: 11.5px; color: var(--tx-3); width: 34px; }
.sw { display: flex; align-items: center; gap: 6px; font-size: 12.5px; cursor: pointer; }
table { font-size: 12px; }
</style>
