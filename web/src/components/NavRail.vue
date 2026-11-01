<script setup>
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { view, workTab } = storeToRefs(w)
defineProps({ expanded: { type: Boolean, default: true } })
const emit = defineEmits(['toggle-side', 'reveal-side'])

/* 点位 / 程序 / AI 保留独立入口；工程内集中工程、日志、遥测、设置和关于。 */
const ITEMS = [
  ['control', null, '控制', 'M9 2 V9 M5.5 5 a5 5 0 1 0 7 0'],
  ['work', 'points', '点位', 'M9 3 A6 6 0 1 0 9 15 A6 6 0 1 0 9 3 M9 6 V12 M6 9 H12'],
  ['hand-follow', null, '有趣', 'M5.5 5 H12.5 C14 5 15 6 15.5 8 L16.5 12.5 C17 15 14.5 16 13 13.5 L11.8 12 H6.2 L5 13.5 C3.5 16 1 15 1.5 12.5 L2.5 8 C3 6 4 5 5.5 5 Z M5 7.5 V10.5 M3.5 9 H6.5 M11.5 8 H11.51 M14 10 H14.01'],
  ['sorting', null, '分拣', 'M3 3 H15 V7 H3 Z M5 10 H8 V15 H3 V10 Z M11 10 H15 V15 H10 V10 Z'],
  ['vision', null, '视觉', 'M2 5 H16 V14 H2 Z M6 5 L7 3 H11 L12 5 M12 9 A3 3 0 1 0 6 9 A3 3 0 1 0 12 9'],
  ['work', 'program', '程序', 'M5 4 L2 9 L5 14 M13 4 L16 9 L13 14 M10.5 3 L7.5 15'],
  ['work', 'ai', 'AI', 'M9 2 L10.4 6.6 L15 8 L10.4 9.4 L9 14 L7.6 9.4 L3 8 L7.6 6.6 Z'],
  ['objects', null, '场景', 'M4 5 H14 V15 H4 Z M6 3 H12 V7 H6 Z M6.5 10 H11.5 M6.5 12.5 H10'],
  ['fence', null, '围栏', 'M2 14 H16 M3 14 V4 H15 V14 M7 4 V14 M11 4 V14'],
  ['engineering', null, '系统', 'M9 6 a3 3 0 1 0 0 6 a3 3 0 1 0 0 -6 M9 2 V4 M9 14 V16 M2 9 H4 M14 9 H16'],
]

function openItem(targetView, targetTab) {
  w.view = targetView
  if (targetView === 'work' && targetTab) w.workTab = targetTab
  emit('reveal-side')
}
function isActive(targetView, targetTab) {
  return view.value === targetView && (!targetTab || workTab.value === targetTab)
}
</script>

<template>
  <nav class="rail panel corner" aria-label="工作区导航">
    <div class="rail-items">
    <button v-for="[targetView, targetTab, label, d] in ITEMS" :key="targetView + ':' + (targetTab || '')"
            class="ri" :class="{ on: isActive(targetView, targetTab), 'system-entry': targetView === 'engineering' }"
            :title="label" :aria-current="isActive(targetView, targetTab) ? 'page' : undefined"
            @click="openItem(targetView, targetTab)">
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <path :d="d" fill="none" stroke="currentColor" stroke-width="1.35"
              stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span>{{ label }}</span>
    </button>
    </div>
    <button type="button" class="ri rail-toggle" :aria-expanded="expanded" :aria-label="expanded ? '收起左侧栏' : '展开左侧栏'"
            :title="expanded ? '收起左侧栏' : '展开左侧栏'"
            @click="emit('toggle-side')">
      <svg viewBox="0 0 18 18" aria-hidden="true"><path :d="expanded ? 'M11 4 L6 9 L11 14' : 'M7 4 L12 9 L7 14'"
        fill="none" stroke="currentColor" stroke-width="1.5" /></svg>
      <span>{{ expanded ? '收起' : '展开' }}</span>
    </button>
  </nav>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 58px;
  flex: 0 0 58px;
  padding: 4px 3px;
  min-height: 0;
  background: linear-gradient(180deg, var(--surface-2), var(--surface-1));
  border: 1px solid var(--line);
  border-radius: 0;
}
.ri {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  width: 100%;
  min-height: 46px;
  flex: 0 0 auto;
  padding: 5px 0;
  font: inherit;
  font-size: 10.5px;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid transparent;
  border-left: 2px solid transparent;
  border-radius: 0;
  cursor: pointer;
  transition: color .16s, background .16s, border-color .16s;
}
.ri svg { width: 18px; height: 18px; }
.ri:hover { color: var(--text-primary); background: var(--surface-3); border-color: var(--line-2); }
.ri:focus-visible { outline: 1px solid var(--acc); outline-offset: -2px; box-shadow: none; }
.ri.on {
  color: var(--acc);
  background: var(--acc-soft);
  border-left-color: var(--acc);
  font-weight: 600;
}
.rail-items { display: flex; flex-direction: column; gap: 2px; flex: 1; min-height: 0; overflow-y: auto; scrollbar-width: thin; }
.system-entry { margin-top: auto; }
.rail-toggle { flex: none; border: 1px solid var(--line-2); margin-top: 4px; padding: 5px 0; min-height: 42px;
               background: var(--bg-inset); clip-path: var(--clip-br); }
.rail-toggle:hover { color: var(--acc); border-color: var(--line-3); background: var(--surface-3); }
</style>
