<script setup>
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { view, workTab, displayMode } = storeToRefs(w)
defineProps({ expanded: { type: Boolean, default: true } })
const emit = defineEmits(['toggle-side', 'reveal-side'])

/* 左侧栏只保留九个一级入口，不显示“作业 / 游戏”这类分组标题：
   控制、点位、程序、AI、视觉、娱乐、互动、场景、系统。
   程序/AI/点位保持独立；五个棋种收进“娱乐”，跟随/追踪/绘制/写字收进“互动”，
   场景/分拣收进“场景”，围栏作为“系统”的一个页签，左侧保持干净。 */
const GAME_VIEWS = ['game-ttt', 'game-gomoku', 'game-chess', 'game-xiangqi', 'game-go']
const ITEMS = [
  ['control', null, '控制', 'M9 2 V9 M5.5 5 a5 5 0 1 0 7 0'],
  ['work', 'points', '点位', 'M9 3 A6 6 0 1 0 9 15 A6 6 0 1 0 9 3 M9 6 V12 M6 9 H12'],
  ['work', 'program', '程序', 'M5 4 L2 9 L5 14 M13 4 L16 9 L13 14 M10.5 3 L7.5 15'],
  ['work', 'ai', 'AI', 'M9 2 L10.4 6.6 L15 8 L10.4 9.4 L9 14 L7.6 9.4 L3 8 L7.6 6.6 Z'],
  ['vision', null, '视觉', 'M2 5 H16 V14 H2 Z M6 5 L7 3 H11 L12 5 M12 9 A3 3 0 1 0 6 9 A3 3 0 1 0 12 9'],
  ['games', null, '娱乐', 'M3 7 H15 V13 H3 Z M2 9 H3 M15 9 H16 M5 9 H7 M6 8 V10 M11.5 9.5 H11.51 M13.5 11 H13.51'],
  ['interaction', null, '互动', 'M5.5 5 H12.5 C14 5 15 6 15.5 8 L16.5 12.5 C17 15 14.5 16 13 13.5 L11.8 12 H6.2 L5 13.5 C3.5 16 1 15 1.5 12.5 L2.5 8 C3 6 4 5 5.5 5 Z M5 7.5 V10.5 M3.5 9 H6.5 M11.5 8 H11.51 M14 10 H14.01'],
  ['scene', null, '场景', 'M4 5 H14 V15 H4 Z M6 3 H12 V7 H6 Z M6.5 10 H11.5 M6.5 12.5 H10'],
  ['engineering', null, '系统', 'M9 6 a3 3 0 1 0 0 6 a3 3 0 1 0 0 -6 M9 2 V4 M9 14 V16 M2 9 H4 M14 9 H16'],
]
const GROUPS = [{ id: 'main', label: '', items: ITEMS }]

const compact = computed(() => displayMode.value === 'simple')
const groups = computed(() => compact.value
  ? GROUPS.map(group => ({ ...group, label: '' }))
  : GROUPS)

function openItem(targetView, targetTab) {
  if (targetView === 'games') {
    // 合并后的娱乐入口保持当前棋种；从别的页进来时默认井字棋。
    w.view = GAME_VIEWS.includes(w.view) ? w.view : 'game-ttt'
  } else {
    w.view = targetView
    if (targetView === 'work' && targetTab) w.workTab = targetTab
  }
  emit('reveal-side')
}
function isActive(targetView, targetTab) {
  if (targetView === 'games') return GAME_VIEWS.includes(view.value)
  return view.value === targetView && (!targetTab || workTab.value === targetTab)
}
</script>

<template>
  <nav class="rail panel corner" :class="{ compact }" aria-label="工作区导航">
    <div class="rail-items">
      <section v-for="group in groups" :key="group.id" class="rail-group">
        <div v-if="group.label" class="grp">{{ group.label }}</div>
        <button v-for="[targetView, targetTab, label, d] in group.items" :key="targetView + ':' + (targetTab || '')"
                class="ri" :class="{ on: isActive(targetView, targetTab) }"
                :title="label" :aria-current="isActive(targetView, targetTab) ? 'page' : undefined"
                @click="openItem(targetView, targetTab)">
          <svg viewBox="0 0 18 18" aria-hidden="true">
            <path :d="d" fill="none" stroke="currentColor" stroke-width="1.35"
                  stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span>{{ label }}</span>
        </button>
      </section>
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
  width: 62px;
  flex: 0 0 62px;
  padding: 4px 3px;
  min-height: 0;
  background: linear-gradient(180deg, var(--surface-2), var(--surface-1));
  border: 1px solid var(--line);
  border-radius: 0;
}
.rail.compact { width: 48px; flex-basis: 48px; }
.rail-items { display: flex; flex-direction: column; gap: 2px; flex: 1; min-height: 0; overflow-y: auto; scrollbar-width: thin; }
.rail-group { display: flex; flex-direction: column; gap: 2px; }
.rail-group + .rail-group { margin-top: 5px; }
.grp { font-size: 9.5px; letter-spacing: .12em; color: var(--tx-3); padding: 3px 2px 1px; }
.ri {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  width: 100%;
  min-height: 38px;
  flex: 0 0 auto;
  padding: 3px 0;
  font: inherit;
  font-size: 10px;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid transparent;
  border-left: 2px solid transparent;
  border-radius: 0;
  cursor: pointer;
  transition: color .16s, background .16s, border-color .16s;
}
.compact .ri { min-height: 32px; font-size: 0; gap: 0; }
.ri svg { width: 17px; height: 17px; }
.ri:hover { color: var(--text-primary); background: var(--surface-3); border-color: var(--line-2); }
.ri:focus-visible { outline: 1px solid var(--acc); outline-offset: -2px; box-shadow: none; }
.ri.on {
  color: var(--acc);
  background: var(--acc-soft);
  border-left-color: var(--acc);
  font-weight: 600;
}
.rail-toggle { flex: none; border: 1px solid var(--line-2); margin-top: 4px; padding: 4px 0; min-height: 36px;
               background: var(--bg-inset); clip-path: var(--clip-br); }
.compact .rail-toggle { font-size: 0; }
.rail-toggle:hover { color: var(--acc); border-color: var(--line-3); background: var(--surface-3); }
</style>
