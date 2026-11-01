<script setup>
import { ref, defineAsyncComponent } from 'vue'
import TaskTabs from '../components/TaskTabs.vue'
import FunctionsPanel from './FunctionsPanel.vue'

/* 场景：一个一级入口，页内 tab 覆盖场景物体 / 分拣；视觉另有独立入口。 */
const ObjectsPanel = defineAsyncComponent(() => import('./ObjectsPanel.vue'))

const MODES = [
  { id: 'objects', label: '场景' },
  { id: 'sorting', label: '分拣' },
]
const mode = ref('objects')
</script>

<template>
  <div class="col scene-hub">
    <TaskTabs v-model="mode" :tabs="MODES" id="scene" label="场景" />
    <KeepAlive>
      <ObjectsPanel v-if="mode === 'objects'" />
    </KeepAlive>
    <FunctionsPanel v-if="mode === 'sorting'" category="sorting" />
  </div>
</template>

<style scoped>
.scene-hub { min-width: 0; }
.scene-hub :deep(.task-tabs > button) { flex: 1 1 0; min-width: 0; }
</style>
