<script setup>
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import TaskTabs from '../components/TaskTabs.vue'

const w = useWorld()
const { snap, meta } = storeToRefs(w)
const tab = ref('range')
const tabs = [{id:'range',label:'范围'},{id:'detection',label:'检测'},{id:'thresholds',label:'阈值'},{id:'voice',label:'播报'}]
const settings = computed(() => snap.value?.settings || {})
const isReal = computed(() => snap.value?.mode === 'real')
const safetyName = level => ({green:'正常',yellow:'预警',red:'限制',alarm:'告警'}[level] || '--')
const gapText = value => Number.isFinite(value) ? `${value.toFixed(1)} mm` : '--'
const busy = ref(false)
const message = ref('')
const failed = ref(false)
const axes = [['x', 'X'], ['y', 'Y'], ['z', 'Z · 显示高度']]
const thresholdRows = [
  ['safety_yellow_pct', '黄色预警', '接近边界，仍可执行', 'warn'],
  ['safety_red_pct', '红色限制', '拦截新运动', 'bad'],
  ['safety_alarm_pct', '硬边界告警', '100%红色闪烁并停止，原因分开显示', 'alarm'],
]
const copyFence = value => ({ ...value, ...Object.fromEntries(axes.map(([key]) => [key, key==='z'?[0,value?.z?.[1] ?? '']:[...(value?.[key] || ['', ''])]])) })
const range = ref(copyFence(null))
const thresholds = ref({})
const limits = ref(Array.from({ length: 6 }, () => ['', '']))
const numberValid = value => typeof value === 'number' && Number.isFinite(value)
watch(() => JSON.stringify(settings.value.fence), () => { range.value = copyFence(settings.value.fence) }, { immediate: true })
watch(() => JSON.stringify(thresholdRows.map(([key]) => settings.value[key])), () => {
  const values = thresholdRows.map(([key]) => settings.value[key])
  thresholds.value = Object.fromEntries(thresholdRows.map(([key], i) => [key, values[i] ?? [90, 95, 100][i]]))
}, { immediate: true })
watch(() => JSON.stringify(settings.value.custom_limits), () => {
  const value = settings.value.custom_limits
  limits.value = Array.from({ length: 6 }, (_, i) => [...(value?.[i] || ['', ''])])
}, { immediate: true })

const rangeValid = computed(() => axes.every(([key]) => range.value[key].every(numberValid) && range.value[key][0] < range.value[key][1]))
const rangeDirty = computed(() => axes.some(([key]) => JSON.stringify(range.value[key]) !== JSON.stringify(settings.value.fence?.[key])))
const thresholdValid = computed(() => {
  const values = thresholdRows.map(([key]) => thresholds.value[key])
  return values.every(numberValid) && values[0] > 0 && values[0] < values[1] && values[1] < values[2] && values[2] <= 100
})
const thresholdDirty = computed(() => thresholdRows.some(([key]) => thresholds.value[key] !== settings.value[key]))
const limitsValid = computed(() => limits.value.every(pair => pair.every(numberValid) && pair[0] < pair[1]))
const limitsDirty = computed(() => JSON.stringify(limits.value) !== JSON.stringify(settings.value.custom_limits))
const limitOptions = computed(() => Object.entries(meta.value?.limit_sources || {}))

async function patch(value, label) {
  if (busy.value) return
  // Moving existing controls must not introduce a way to disable real protection.
  if (isReal.value && ((value.fence?.enabled === false && settings.value.fence?.enabled !== false)
    || value.ground_enabled === false || value.self_collision === false)) return
  busy.value = true
  message.value = ''
  failed.value = false
  try {
    const result = await w.setSettings(value)
    failed.value = !result?.ok
    message.value = result?.ok ? `${label}已保存` : result?.why || result?.error || '保存失败'
  } catch (error) {
    failed.value = true
    message.value = error.message || '保存失败'
  } finally { busy.value = false }
}
function saveRange() {
  if (!rangeValid.value || !rangeDirty.value) return
  // Read the enabled flag from the current snapshot, never an unsaved draft.
  patch({ fence: { ...settings.value.fence, ...Object.fromEntries(axes.map(([key]) => [key, [...range.value[key]]])) } }, '围栏范围')
}
function saveThresholds() {
  if (thresholdValid.value && thresholdDirty.value) patch({ ...thresholds.value }, '安全阈值')
}
function saveLimits() {
  if (limitsValid.value && limitsDirty.value) patch({ custom_limits: limits.value.map(pair => [...pair]) }, '六轴限位')
}
function changeDetection(key, event) {
  if ((isReal.value && !event.target.checked) || busy.value) { event.target.checked = key === 'fence' ? settings.value.fence?.enabled !== false : settings.value[key] !== false; return }
  patch(key === 'fence' ? { fence: { ...settings.value.fence, enabled: event.target.checked } } : { [key]: event.target.checked }, '检测配置')
}
</script>

<template>
  <div v-if="snap" class="col fence-workspace">
    <TaskTabs v-model="tab" :tabs="tabs" id="fence" label="围栏分类" storage-key="huayan.tabs.fence.v1" />
    <div v-if="message" class="note" :class="{ bad: failed }" :role="failed ? 'alert' : 'status'">{{ message }}</div>
    <div id="fence-panel" class="col" role="tabpanel" :aria-labelledby="`fence-tab-${tab}`" tabindex="0">
    <section v-show="tab === 'range'" class="panel corner">
      <div class="panel-hd"><span class="panel-title">工作空间围栏</span></div>
      <div class="panel-bd">
        <label class="fence-switch no-fold"><input v-model="w.fenceVisible" type="checkbox" /><span>显示围栏</span><small>仅影响三维显示</small></label>
        <form class="fence-form" @submit.prevent="saveRange">
          <div class="range-head"><span>基座坐标轴</span><span>最小 mm</span><span>最大 mm</span></div>
          <div v-for="[key, label] in axes" :key="key" class="range-row">
            <span class="axis-name">{{ label }}</span>
            <input v-for="i in 2" :key="i" v-model.number="range[key][i - 1]" class="input mono" type="number" step="10"
                   :aria-label="`${key.toUpperCase()} ${i === 1 ? '最小值' : '最大值'}（毫米）`" :disabled="busy || (key==='z' && i===1)" />
          </div>
          <div class="ground-coordinate"><span>实际地面 Z</span><output class="mono">0 mm</output></div>
          <p class="fence-help">X、Y 定义四墙，Z 仅控制显示高度。地面固定 Z=0，顶面开放。</p>
          <div v-if="!rangeValid" class="note bad tiny">每个轴须填写有限数值，且最小值小于最大值。</div>
          <button class="btn" type="submit" :disabled="busy || !rangeValid || !rangeDirty">保存范围</button>
        </form>
      </div>
    </section>

    <section v-show="tab === 'detection'" class="panel">
      <div class="panel-hd"><span class="panel-title">检测与限位</span></div>
      <div class="panel-bd">
        <label class="fence-switch no-fold"><input type="checkbox" :checked="settings.fence?.enabled !== false" :disabled="busy || (isReal && settings.fence?.enabled !== false)" @change="changeDetection('fence', $event)" /><span>四墙越界检测</span></label>
        <label class="fence-switch no-fold"><input type="checkbox" :checked="settings.ground_enabled !== false" :disabled="busy || (isReal && settings.ground_enabled !== false)" @change="changeDetection('ground_enabled', $event)" /><span>地面碰撞检测</span></label>
        <p class="fence-help">四墙与物体检测末端工具；地面还检测可动连杆，不含安装座。{{ isReal ? '真机模式不可关闭必要检测。' : '' }}</p>
        <dl class="kv">
          <dt>四墙</dt><dd>{{ safetyName(snap.safety?.fence_level) }} · 间隙 {{ gapText(snap.safety?.fence_gap_mm) }}</dd>
          <dt>地面</dt><dd>{{ safetyName(snap.safety?.ground_level) }} · 间隙 {{ gapText(snap.safety?.ground_gap_mm) }}</dd>
          <dt>关节限位</dt><dd>{{ safetyName(snap.safety?.joint_level) }}</dd>
        </dl>
        <label class="field"><span>关节限位来源</span><select class="select" :value="settings.limit_source" :disabled="busy" aria-label="关节限位来源" @change="patch({ limit_source: $event.target.value }, '限位来源')">
          <option v-if="!limitOptions.some(([key]) => key === settings.limit_source)" :value="settings.limit_source">{{ settings.limit_source || '等待配置' }}</option>
          <option v-for="[key, label] in limitOptions" :key="key" :value="key">{{ label }}</option>
        </select></label>
        <details class="custom-limits">
          <summary>六轴自定义限位 <small>{{ settings.limit_source === 'manual' ? '当前生效' : '选择示教器实测来源后生效' }}</small></summary>
          <form class="fence-form" @submit.prevent="saveLimits">
            <div class="range-head"><span>关节</span><span>最小 °</span><span>最大 °</span></div>
            <div v-for="(pair, i) in limits" :key="i" class="range-row"><span class="mono">J{{ i + 1 }}</span>
              <input v-for="j in 2" :key="j" v-model.number="pair[j - 1]" class="input mono" type="number" step="0.1" :disabled="busy" :aria-label="`J${i + 1} ${j === 1 ? '最小' : '最大'}限位（度）`" />
            </div>
            <p v-if="!limitsValid" class="fence-help">填写全部六轴，且每轴最小值小于最大值。</p>
            <button class="btn" type="submit" :disabled="busy || !limitsValid || !limitsDirty">保存六轴限位</button>
          </form>
        </details>
      </div>
    </section>

    <section v-show="tab === 'thresholds'" class="panel">
      <div class="panel-hd"><span class="panel-title">安全阈值</span></div>
      <form class="panel-bd" @submit.prevent="saveThresholds">
        <label v-for="[key, label, help, tone] in thresholdRows" :key="key" class="threshold-row">
          <span class="threshold-status" :class="tone"><i></i>{{ label }}<small>{{ help }}</small></span>
          <input v-model.number="thresholds[key]" class="input mono" type="number" min="1" max="100" step="1" :aria-label="label + '阈值（百分比）'" :disabled="busy" /><span class="dim">%</span>
        </label>
        <div v-if="!thresholdValid" class="note bad tiny">阈值须满足：0 &lt; 黄色 &lt; 红色 &lt; 告警 ≤ 100。</div>
        <p class="fence-help">阈值表示碰撞接近度，不是概率；接触红闪并停止。四墙、地面按几何距离判断，关节限位单独提示。</p>
        <button class="btn" type="submit" :disabled="busy || !thresholdValid || !thresholdDirty">保存阈值</button>
      </form>
    </section>

    <section v-show="tab === 'voice'" class="panel">
      <div class="panel-hd"><span class="panel-title">告警播报</span></div>
      <div class="panel-bd">
        <label class="fence-switch no-fold"><input type="checkbox" :checked="!!settings.tts" :disabled="busy" @change="patch({ tts: $event.target.checked }, '语音播报')" /><span>语音播报告警</span></label>
        <label class="voice-rate"><span>语速</span><input class="grow" type="range" min="0.6" max="1.6" step="0.1" :value="settings.tts_rate ?? 1" :disabled="busy" aria-label="告警播报语速"
          :style="{ '--fill': ((Number(settings.tts_rate ?? 1) - 0.6) * 100) + '%' }" @change="patch({ tts_rate: Number($event.target.value) }, '播报语速')" /><output class="mono">×{{ Number(settings.tts_rate ?? 1).toFixed(1) }}</output></label>
      </div>
    </section>
    </div>
  </div>
</template>

<style scoped>
.fence-workspace { gap: 6px; }
.panel-hd { gap: 6px; padding: 8px 10px; }
.panel-bd { gap: 8px; padding: 8px 10px; }
.fence-form { display: grid; gap: 6px; min-width: 0; }
.fence-switch { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: 12px; }
.fence-switch input { flex: 0 0 auto; margin: 0; }
.fence-switch small { margin-left: auto; color: var(--tx-3); font-size: 10px; }
.range-head, .range-row { display: grid; grid-template-columns: minmax(64px, .8fr) repeat(2, minmax(0, 1fr)); gap: 6px; align-items: center; }
.range-head { color: var(--tx-3); font-size: 10px; }
.range-row { color: var(--tx-2); font-size: 11px; }
.range-row .input { padding: 0 6px; font-size: 12px; }
.axis-name { line-height: 1.4; }
.ground-coordinate { display: flex; align-items: center; justify-content: space-between; color: var(--tx-2); font-size: 11px; }
.fence-help { margin: 0; color: var(--tx-3); font-size: 10.5px; line-height: 1.6; overflow-wrap: anywhere; }
.field > span { color: var(--tx-2); font-size: 11px; }
.custom-limits { border-top: 1px solid var(--line); padding-top: 6px; }
.custom-limits summary { color: var(--tx-2); font-size: 11px; cursor: pointer; }
.custom-limits summary small { color: var(--tx-3); font-size: 10px; }
.custom-limits .fence-form { padding-top: 8px; }
.threshold-row { display: grid; grid-template-columns: minmax(0, 1fr) 64px auto; gap: 6px; align-items: center; min-height: 44px; border-bottom: 1px solid var(--line); padding-bottom: 6px; font-size: 12px; }
.threshold-status { display: grid; grid-template-columns: 7px minmax(0, 1fr); gap: 2px 6px; align-items: center; }
.threshold-status i { width: 6px; height: 6px; background: currentColor; }
.threshold-status small { grid-column: 2; color: var(--tx-3); font-size: 10px; }
.threshold-status.alarm { color: var(--state-alarm); }
.voice-rate { display: flex; align-items: center; gap: 6px; color: var(--tx-2); font-size: 11px; }
.voice-rate output { flex: 0 0 34px; text-align: right; }
</style>
