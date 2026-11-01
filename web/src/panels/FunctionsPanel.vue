<script setup>
import { ref, computed, defineAsyncComponent, watch, onActivated, onDeactivated, onBeforeUnmount } from 'vue'
import { useWorld } from '../store'
import TaskTabs from '../components/TaskTabs.vue'
import RobotPlayProgram from '../components/RobotPlayProgram.vue'
import HandlingChallenge from '../components/HandlingChallenge.vue'
import PracticalTask from '../components/PracticalTask.vue'
import { scenePlay, playAllowed, playFor, sortingCounts } from '../functionPlay.js'
import { robotPlayFor } from '../robotPlayProgram.js'
import { visionWorkspace, ensureVirtualVision } from '../visionWorkspace.js'
const HandFollowPanel = defineAsyncComponent(() => import('./HandFollowPanel.vue'))
const TrackPanel = defineAsyncComponent(() => import('../components/TrackPanel.vue'))

/* 二级栏按一级入口分三种：跟随（外置/电脑相机）、追踪（末端相机）、分拣。
   跟随与追踪保持独立组件和独立状态机，不合并、不互相停止。 */
const props = defineProps({
  visualModel: String,
  direction: Number,
  fence: Object,
  category: { type: String, default: 'follow' }, // follow | track | sorting
})
const emit = defineEmits(['direction', 'box-preview', 'interaction-visible'])
const w = useWorld()
const panelActive = ref(true)
const SORTING_TABS = [{ id: 'sorting', label: '分拣' }, { id: 'handling', label: '搬运闯关' },
  { id: 'kit', label: '配套装盒' }, { id: 'pattern', label: '图案拼摆' }]
const tab = ref('sorting')
const showTabs = computed(() => props.category === 'sorting')
const tabs = computed(() => showTabs.value ? SORTING_TABS : [])

watch([tab, panelActive, () => props.category], ([, active, category]) => {
  emit('interaction-visible', active && category === 'follow')
}, { immediate: true, flush: 'sync' })
watch(() => props.category, () => {
  if (props.category !== 'sorting') panelActive.value = true
}, { flush: 'sync' })

onActivated(() => { panelActive.value = true })
onDeactivated(() => { panelActive.value = false })
onBeforeUnmount(() => { panelActive.value = false })

const realMode = computed(() => w.snap?.mode === 'real')
// 跟随与分拣都要"看得见"：进入时自动打开仿真摄像头；绘制/写字不在本页。
const needsCamera = computed(() => ['follow', 'sorting'].includes(props.category))
function ensureSimCamera() {
  if (!panelActive.value || realMode.value || !needsCamera.value) return
  if (visionWorkspace.config?.enabled && visionWorkspace.config?.input) return
  void ensureVirtualVision()
}
watch([() => props.category, panelActive, realMode], ensureSimCamera, { immediate: true, flush: 'post' })

/* 分拣页沿用现有场景与 RobotPlay 预检/执行，不新增运动通道。 */
const objects = computed(() => w.snap?.objects || [])
const sorting = computed(() => scenePlay(objects.value, 'sorting'))
const task = playFor(w), taskState = task.state
const robotProgram = robotPlayFor(w)
const counts = ref({ red: 3, green: 3, blue: 3 }), sortingCriterion = ref('color')
const shapeCounts = ref({ rectangle: 3, circle: 3 })
const sortingPerception = ref('scene')
const countError = computed(() => {
  try {
    if (sortingCriterion.value === 'shape') {
      const values = Object.values(shapeCounts.value)
      if (values.some(v => !Number.isInteger(v) || v < 0 || v > 9) || values.reduce((a, b) => a + b, 0) < 1)
        throw Error('矩形和圆形分别需要0～9个，总数至少1个')
    } else sortingCounts(counts.value)
    return ''
  } catch (error) { return error.message }
})
const total = computed(() => Object.values(sortingCriterion.value === 'shape' ? shapeCounts.value : counts.value)
  .reduce((sum, value) => sum + (Number(value) || 0), 0))
const busy = computed(() => taskState.busy || robotProgram.busy.value)
const allowed = computed(() => playAllowed(w))
async function setupSorting() {
  if (busy.value || !allowed.value || countError.value) return
  robotProgram.invalidate()
  return task.run('setup', { mode: 'sorting', criterion: sortingCriterion.value,
    ...(sortingCriterion.value === 'shape' ? { shape_counts: shapeCounts.value } : { counts: counts.value }) })
}
</script>

<template>
  <div class="col functions-panel">
    <TaskTabs v-if="showTabs" v-model="tab" :tabs="tabs" id="functions" label="分拣与装盒" />
    <div v-if="showTabs && (taskState.busy || taskState.message)" class="note" role="status" aria-live="polite">
      {{ taskState.busy ? '处理中' : taskState.message }}
    </div>

    <div v-if="category === 'follow'" class="col" role="region" aria-label="跟随：电脑相机与末端位置">
      <p class="mode-note">相机：电脑 / 外置相机（固定）。控制量：末端位置追手掌。</p>
      <KeepAlive>
        <HandFollowPanel :visual-model="visualModel" :direction="direction" :fence="fence"
                         @direction="emit('direction', $event)" @box-preview="emit('box-preview', $event)" />
      </KeepAlive>
    </div>

    <div v-else-if="category === 'track'" class="col" role="region" aria-label="追踪：机器人末端相机">
      <p class="mode-note">相机：机器人末端相机（随臂移动）。控制量：让手掌回到画面中心。</p>
      <TrackPanel :visual-model="visualModel" :direction="direction" :fence="fence" @direction="emit('direction',$event)" @box-preview="emit('box-preview',$event)" />
    </div>

    <div v-else id="functions-panel" class="col" role="tabpanel" :aria-labelledby="`functions-tab-${tab}`">
      <section v-if="tab === 'sorting'" class="panel">
        <div class="panel-hd"><span class="panel-title">机器人分拣</span><span class="tag">{{ realMode ? '真机不可用' : '仅仿真' }}</span></div>
        <div class="panel-bd">
          <div v-if="sortingCriterion === 'color'" class="piece-counts" title="每色≤3 且合计≤9 用 72mm，其余 38mm；A 区随机乱序">
            <label v-for="(label, color) in { red: '红色', green: '绿色', blue: '蓝色' }" :key="color">
              {{ label }}<input v-model.number="counts[color]" class="input" type="number" min="0" max="9" step="1" :disabled="busy" />
            </label>
          </div>
          <p class="dim tiny">共 {{ total }} 件</p>
          <p v-if="countError" class="note" role="alert">{{ countError }}</p>
          <label class="row">分拣依据
            <select v-model="sortingCriterion" class="select grow" :disabled="busy" @change="robotProgram.invalidate()">
              <option value="color">颜色</option><option value="shape">形状 · 矩形/圆形</option>
            </select>
          </label>
          <label class="row">物料来源
            <select v-model="sortingPerception" class="select grow" :disabled="busy" @change="robotProgram.invalidate()">
              <option value="scene">场景物料</option><option value="camera">相机识别</option>
            </select>
          </label>
          <div v-if="sortingCriterion === 'shape'" class="piece-counts">
            <label>矩形<input v-model.number="shapeCounts.rectangle" type="number" min="0" max="9" :disabled="busy" /></label>
            <label>圆形<input v-model.number="shapeCounts.circle" type="number" min="0" max="9" :disabled="busy" /></label>
          </div>
        </div>
      </section>
      <RobotPlayProgram v-if="tab === 'sorting'" :group="sorting.group" action="sorting" :criterion="sortingCriterion"
                        :perception="sortingPerception" :blocked="taskState.busy">
        <template #placement>
          <button class="btn" :disabled="busy || !allowed || !!countError" @click="setupSorting">摆放</button>
        </template>
      </RobotPlayProgram>
      <PracticalTask v-if="['kit', 'pattern'].includes(tab)" :key="tab" :kind="tab" />
      <HandlingChallenge v-if="tab === 'handling'" />
    </div>
  </div>
</template>

<style scoped>
.functions-panel { min-width: 0; }
.functions-panel :deep(.task-tabs) { flex-wrap: nowrap; }
.functions-panel :deep(.task-tabs > button) { flex: 1 1 0; min-width: 0; white-space: nowrap; padding-left: 4px; padding-right: 4px; }
.mode-note { margin: 0; padding: 2px 2px 0; font-size: 11px; line-height: 1.5; color: var(--tx-3); }
.piece-counts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.piece-counts label { display: grid; gap: 4px; font-size: 12px; }
.piece-counts input { width: 100%; min-width: 0; }
</style>
