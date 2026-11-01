<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import TaskTabs from '../components/TaskTabs.vue'
import { workspaceRun } from '../workspaceInsights.js'

const w = useWorld()
const { snap } = storeToRefs(w)
const s = computed(() => snap.value)
const props=defineProps({section:String})
const localTab = ref('run')
const tab = computed({get:()=>props.section || localTab.value,set:value=>{localTab.value=value}})
const tabs = [{ id: 'run', label: '运行' }, { id: 'kinematics', label: '运动学' }]
const run = computed(() => workspaceRun(snap.value, w.connected))
const q = computed(() => (snap.value ? snap.value.joints.q : []))
const lo = computed(() => (snap.value ? snap.value.joints.lower : []))
const hi = computed(() => (snap.value ? snap.value.joints.upper : []))
const vmax = computed(() => (snap.value ? snap.value.joints.vmax : []))
const ratios = computed(() => (snap.value ? snap.value.safety.per_joint : []))
const caps = computed(() => (snap.value ? snap.value.safety.capsules.length : 0))
/* 真机链路没通时关节角是未知的：表里必须显示 --，不能拿仿真的 0.000 冒充读数 */
const known = computed(() => run.value.available && !!snap.value?.tcp)
const cls = r => r*100 >= (snap.value?.settings?.safety_red_pct ?? 95) ? 'bad' : r*100 >= (snap.value?.settings?.safety_yellow_pct ?? 90) ? 'warn' : 'acc'
</script>

<template>
  <div class="col" v-if="s">
    <TaskTabs v-if="!section" v-model="tab" :tabs="tabs" id="overview" label="总览分类" storage-key="huayan.tabs.overview.v1" />
    <div id="overview-panel" class="col" :role="section?undefined:'tabpanel'" :aria-labelledby="section?undefined:`overview-tab-${tab}`" tabindex="0">
    <section v-if="tab === 'run'" class="panel">
      <div class="panel-hd"><span class="panel-title">任务与目标反馈</span></div>
      <div class="panel-bd">
        <div class="note" :class="w.connected ? '' : 'warn'">{{ s.mode === 'real' ? '真机' : '仿真' }} · {{ run.source }}</div>
        <dl class="kv"><dt>程序状态</dt><dd>{{ run.state }} · {{ s.exec?.completed_index ?? '--' }}/{{ s.exec?.total ?? '--' }}</dd>
          <dt>执行器动作</dt><dd>{{ run.action }}</dd><dt>当前程序行</dt><dd>{{ run.line ?? '--' }}</dd>
          <dt>目标来源</dt><dd>{{ run.target ? '快照中的预览目标（不代表已下发）' : '当前无六轴预览目标' }}</dd>
          <dt>最大目标差</dt><dd>{{ run.maxError === null ? '--' : run.maxError.toFixed(3) + '°' }}</dd></dl>
        <div v-if="run.error" class="note bad">{{ run.error }}</div>
        <table><thead><tr><th>轴</th><th>预览目标 °</th><th>{{ s.mode === 'real' ? '控制器反馈 °' : '仿真反馈 °' }}</th><th>目标−反馈 °</th></tr></thead>
          <tbody><tr v-for="i in 6" :key="i"><td>J{{ i }}</td><td>{{ run.target?.[i-1]?.toFixed(3) ?? '--' }}</td><td>{{ run.feedback?.[i-1]?.toFixed(3) ?? '--' }}</td><td>{{ run.errors?.[i-1]?.toFixed(3) ?? '--' }}</td></tr></tbody></table>
        <div class="note tiny">执行器未上报独立命令目标，不能把预览差当作伺服跟随误差。真机反馈失效或断开时差值显示 --；角度差不折回 ±180°。</div>
      </div>
    </section>
    <section v-if="tab === 'kinematics'" class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">运动学总览</span><span class="panel-sub">{{ caps }} 根碰撞包络</span>
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

    <section v-if="tab === 'kinematics'" class="panel">
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



    </div>
  </div>
</template>

<style scoped>
.al { font-size: 11.5px; color: var(--tx-3); width: 34px; }
table { font-size: 12px; }
</style>
