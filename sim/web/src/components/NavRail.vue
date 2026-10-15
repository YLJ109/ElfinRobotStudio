<script setup>
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { view, workTab } = storeToRefs(w)
defineProps({ expanded: { type: Boolean, default: true } })
const emit = defineEmits(['toggle-side', 'reveal-side'])

/* 点位 / 程序 / AI / 工程是一级工作入口。它们共用作业工作区容器，
   但导航上直接呈现，避免用户还要先进入含义模糊的“作业”。 */
const ITEMS = [
  ['sim', null, '总览', 'M3 15 L9 4 L15 15 M3 15 H15'],
  ['power', null, '驱动', 'M9 2 V9 M5.5 5 a5 5 0 1 0 7 0'],
  ['work', 'points', '点位', 'M9 3 A6 6 0 1 0 9 15 A6 6 0 1 0 9 3 M9 6 V12 M6 9 H12'],
  ['work', 'program', '程序', 'M5 4 L2 9 L5 14 M13 4 L16 9 L13 14 M10.5 3 L7.5 15'],
  ['work', 'ai', 'AI', 'M9 2 L10.4 6.6 L15 8 L10.4 9.4 L9 14 L7.6 9.4 L3 8 L7.6 6.6 Z'],
  ['work', 'engineering', '工程', 'M9 2 L10.8 5.6 L14.8 6.2 L11.9 9 L12.6 13 L9 11.2 L5.4 13 L6.1 9 L3.2 6.2 L7.2 5.6 Z'],
  ['objects', null, '摆放', 'M4 5 H14 V15 H4 Z M6 3 H12 V7 H6 Z M6.5 10 H11.5 M6.5 12.5 H10'],
  // 遥测：温度/电流/力/IO/脚本/偏差/体检/回放，全是从官方 Data Sheet 直接读的
  ['tele', null, '遥测', 'M2 12 L5 7 L8 10 L11 4 L14 9 M2 15 H16'],
  ['logs', null, '日志', 'M4 3 H14 V15 H4 Z M6.5 6.5 H11.5 M6.5 9.5 H11.5 M6.5 12.5 H9.5'],
  ['settings', null, '设置', 'M9 6 a3 3 0 1 0 0 6 a3 3 0 1 0 0 -6 M9 2 V4 M9 14 V16 M2 9 H4 M14 9 H16'],
  ['about', null, '关于', 'M9 2 a7 7 0 1 0 0 14 a7 7 0 1 0 0 -14 M9 7.5 V12 M9 5 V5.4'],
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
  <nav class="rail" aria-label="工作区导航">
    <div class="rail-items">
    <button v-for="[targetView, targetTab, label, d] in ITEMS" :key="targetView + ':' + (targetTab || '')"
            class="ri" :class="{ on: isActive(targetView, targetTab) }"
            :title="label" :aria-current="isActive(targetView, targetTab) ? 'page' : undefined"
            @click="openItem(targetView, targetTab)">
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <path :d="d" fill="none" stroke="currentColor" stroke-width="1.35"
              stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span>{{ label }}</span>
    </button>
    </div>
    <button class="ri rail-toggle" :aria-expanded="expanded" :aria-label="expanded ? '收起左侧栏' : '展开左侧栏'"
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
  padding: 4px 2px;
  background: linear-gradient(180deg, rgba(12, 19, 32, .9), rgba(7, 11, 20, .6));
  border: 1px solid var(--line);
  border-radius: var(--r-md);
}
.ri {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 5px 2px;
  font: inherit;
  font-size: 10.5px;
  color: var(--tx-3);
  background: transparent;
  border: none;
  border-left: 2px solid transparent;
  border-radius: 0 6px 6px 0;
  cursor: pointer;
  transition: color .16s, background .16s, border-color .16s;
}
.ri svg { width: 18px; height: 18px; }
.ri:hover { color: var(--tx-1); background: rgba(122, 162, 220, .07); }
.ri.on {
  color: var(--acc);
  background: linear-gradient(90deg, rgba(47, 214, 240, .16), transparent);
  border-left-color: var(--acc);
  font-weight: 600;
}
.rail-items { display: flex; flex-direction: column; gap: 2px; flex: 1; min-height: 0; overflow-y: auto; }
.rail-toggle { flex: none; border-top: 1px solid var(--line-2); margin-top: auto; padding-top: 8px; min-height: 42px; }
</style>
