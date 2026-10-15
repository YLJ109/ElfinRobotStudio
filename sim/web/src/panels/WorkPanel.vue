<script setup>
import { computed, defineAsyncComponent } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import PointsPanel from './PointsPanel.vue'
import ProgramPanel from './ProgramPanel.vue'
import AiPanel from './AiPanel.vue'
const EngineeringPanel = defineAsyncComponent(() => import('./EngineeringPanel.vue'))

const w = useWorld()
const { workTab } = storeToRefs(w)
const CHILD = { points: PointsPanel, program: ProgramPanel, ai: AiPanel, engineering: EngineeringPanel }
const Child = computed(() => CHILD[workTab.value] || PointsPanel)
const tight = computed(() => ['program', 'ai', 'engineering'].includes(workTab.value))
</script>

<template>
  <div class="work rise">
    <div class="wbody" :class="{ tight }">
      <component :is="Child" :key="workTab" />
    </div>
  </div>
</template>

<style scoped>
.work { display: flex; flex-direction: column; gap: 6px; flex: 1; min-height: 0; }
.wbody { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 6px; overflow: auto; }
.wbody.tight { overflow: hidden; }
.wbody.tight > :deep(.ai), .wbody.tight > :deep(.prog) { height: 100%; min-height: 0; }
.wbody > :deep(.col) { flex: 1; min-height: 0; }
/* 点位执行内容较长时由外层滚动；存储页仍可占满侧栏，把点位库延伸到底部。 */
.wbody:not(.tight) > :deep(.col) { flex: 1 0 auto; min-height: 100%; padding-bottom: 2px; }
.wbody:not(.tight) > :deep(.col:not(.storage-view)) > .panel { flex: 0 0 auto; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }

@media (max-width: 520px) {
  .wbody:not(.tight) { overflow-y: auto; overflow-x: hidden; padding-right: 1px; }
  .wbody:not(.tight) > :deep(.col) { gap: 5px; }
}
</style>
