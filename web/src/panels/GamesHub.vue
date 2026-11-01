<script setup>
import { useWorld } from '../store'
import GamesPanel from './GamesPanel.vue'

/* 娱乐页：五种棋合并为一个入口，顶部只保留一条主题化 tab 栏。
   每个 tab 设定自己的强调色，实际规则/机器人执行仍由 GamesPanel 负责。 */
const props = defineProps({ game: { type: String, required: true } })
const w = useWorld()

const TABS = [
  { key: 'ttt', label: '井字棋', mark: '井', accent: '#2fd6f0' },
  { key: 'gomoku', label: '五子棋', mark: '五', accent: '#e0a33b' },
  { key: 'chess', label: '国际象棋', mark: '♞', accent: '#dfe7ef' },
  { key: 'xiangqi', label: '中国象棋', mark: '帥', accent: '#e4553f' },
  { key: 'go', label: '围棋', mark: '●', accent: '#40c979' },
]

function select(key) {
  const target = `game-${key}`
  if (w.view !== target) w.view = target
}
</script>

<template>
  <div class="col games-hub">
    <div class="hub-hd">
      <span class="hub-title">娱乐</span>
      <span class="hub-note">人机对局 · 机械臂落子</span>
    </div>
    <nav class="game-tabs" role="tablist" aria-label="娱乐项目">
      <button v-for="item in TABS" :key="item.key" type="button" role="tab"
              :aria-selected="game === item.key" :class="{ on: game === item.key }"
              :style="{ '--tab-accent': item.accent }" :title="item.label"
              @click="select(item.key)">
        <span class="mark" aria-hidden="true">{{ item.mark }}</span>
        <span class="label">{{ item.label }}</span>
      </button>
    </nav>
    <GamesPanel :key="game" :game="game" />
  </div>
</template>

<style scoped>
.games-hub { min-width: 0; gap: 6px; }
.hub-hd { display: flex; align-items: baseline; gap: 8px; padding: 2px 2px 0; }
.hub-title { font-size: 13px; font-weight: 700; letter-spacing: .18em; color: var(--acc); }
.hub-note { font-size: 10px; color: var(--tx-3); }
.game-tabs {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 4px;
  padding: 3px;
  background: var(--bg-inset);
  border: 1px solid var(--line);
}
.game-tabs button {
  display: grid;
  justify-items: center;
  gap: 3px;
  padding: 6px 2px 5px;
  font: inherit;
  color: var(--tx-2);
  background: transparent;
  border: 1px solid transparent;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color .16s, background .16s, border-color .16s;
}
.game-tabs .mark { font-size: 15px; line-height: 1; color: var(--tx-3); transition: color .16s; }
.game-tabs .label { font-size: 10px; line-height: 1.2; white-space: nowrap; }
.game-tabs button:hover { color: var(--text-primary); background: var(--surface-3); }
.game-tabs button:focus-visible { outline: 1px solid var(--acc); outline-offset: -2px; }
.game-tabs button.on {
  color: var(--tab-accent);
  border-color: color-mix(in srgb, var(--tab-accent) 38%, transparent);
  border-bottom-color: var(--tab-accent);
  background: color-mix(in srgb, var(--tab-accent) 12%, transparent);
}
.game-tabs button.on .mark { color: var(--tab-accent); }
</style>
