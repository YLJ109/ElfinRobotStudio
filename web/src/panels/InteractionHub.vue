<script setup>
import { ref, watch, defineAsyncComponent } from 'vue'
import TaskTabs from '../components/TaskTabs.vue'
import FunctionsPanel from './FunctionsPanel.vue'

/* 互动：一个一级入口，页内 tab 覆盖跟随 / 追踪 / 绘制 / 写字。
   跟随与追踪仍使用各自原有面板和状态机，只在当前 tab 显示。 */
const DrawingTask = defineAsyncComponent(() => import('../components/DrawingTask.vue'))
const TextWritingTask = defineAsyncComponent(() => import('../components/TextWritingTask.vue'))
const props = defineProps({
  visualModel: String,
  direction: Number,
  fence: Object,
})
const emit = defineEmits(['direction', 'box-preview', 'interaction-visible'])

const MODES = [
  { id: 'follow', label: '跟随' },
  { id: 'track', label: '追踪' },
  { id: 'drawing', label: '绘制' },
  { id: 'writing', label: '写字' },
]
const mode = ref('follow')
watch(mode, (value) => emit('interaction-visible', value === 'follow'), { immediate: true, flush: 'sync' })

function forward(name, value) {
  emit(name, value)
}
</script>

<template>
  <div class="col interaction-hub">
    <TaskTabs v-model="mode" :tabs="MODES" id="interaction" label="互动" />
    <FunctionsPanel v-if="mode === 'follow' || mode === 'track'"
                    :visual-model="visualModel" :direction="direction" :fence="fence" :category="mode"
                    @direction="forward('direction', $event)"
                    @box-preview="forward('box-preview', $event)"
                    @interaction-visible="forward('interaction-visible', $event)" />
    <KeepAlive>
      <DrawingTask v-if="mode === 'drawing'" />
    </KeepAlive>
    <KeepAlive>
      <TextWritingTask v-if="mode === 'writing'" />
    </KeepAlive>
  </div>
</template>

<style scoped>
.interaction-hub { min-width: 0; }
.interaction-hub :deep(.task-tabs > button) { flex: 1 1 0; min-width: 0; }
</style>
