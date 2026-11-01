<script setup>
import { computed } from 'vue'

/* 2D 棋盘只负责渲染后端给的局面、最后一手、合法落点和非法原因；
   规则不在前端复制。棋盘本身不判胜负，也不发请求。 */
const props = defineProps({
  state: { type: Object, required: true },
  selected: { type: Array, default: null },
  legalTargets: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['pick'])

const MARGIN = 0.9
const rows = computed(() => Number(props.state?.rows) || 0)
const cols = computed(() => Number(props.state?.cols) || 0)
const game = computed(() => props.state?.game || '')
const layout = computed(() => props.state?.layout === 'points' ? 'points' : 'cells')
const stoneGame = computed(() => ['gomoku', 'go'].includes(game.value))
const vbW = computed(() => layout.value === 'cells' ? cols.value : Math.max(0, cols.value - 1) + MARGIN * 2)
const vbH = computed(() => layout.value === 'cells' ? rows.value : Math.max(0, rows.value - 1) + MARGIN * 2)
const viewBox = computed(() => `0 0 ${vbW.value} ${vbH.value}`)

function cx(c) { return layout.value === 'cells' ? c + 0.5 : MARGIN + c }
function cy(r) { return layout.value === 'cells' ? r + 0.5 : MARGIN + r }

const hits = computed(() => {
  const out = []
  for (let r = 0; r < rows.value; r += 1) for (let c = 0; c < cols.value; c += 1) out.push({ r, c })
  return out
})
const pieces = computed(() => props.state?.cells || [])
const pieceMap = computed(() => {
  const map = new Map()
  for (const cell of pieces.value) map.set(`${cell.r}:${cell.c}`, cell.piece)
  return map
})
function pieceAt(r, c) { return pieceMap.value.get(`${r}:${c}`) || null }

function squareClass(r, c) {
  if (game.value === 'ttt') return 'ttt'
  return (r + c) % 2 === 0 ? 'light' : 'dark'
}

const starPoints = computed(() => {
  if (game.value === 'gomoku' && rows.value === 15) return [[3, 3], [3, 11], [11, 3], [11, 11], [7, 7]]
  if (game.value === 'go' && rows.value === 15) return [[3, 3], [3, 11], [11, 3], [11, 11], [7, 7]]
  return []
})

const gridSegments = computed(() => {
  const segments = []
  if (layout.value !== 'points') return segments
  for (let c = 0; c < cols.value; c += 1) {
    const from = MARGIN + c
    const top = MARGIN
    const bottom = MARGIN + Math.max(0, rows.value - 1)
    if (game.value === 'xiangqi' && c > 0 && c < cols.value - 1) {
      segments.push([from, top, from, MARGIN + 4])
      segments.push([from, MARGIN + 5, from, bottom])
    } else {
      segments.push([from, top, from, bottom])
    }
  }
  for (let r = 0; r < rows.value; r += 1) {
    const at = MARGIN + r
    segments.push([MARGIN, at, MARGIN + Math.max(0, cols.value - 1), at])
  }
  return segments
})

const palaceLines = computed(() => {
  if (game.value !== 'xiangqi') return []
  const x = c => MARGIN + c
  const y = r => MARGIN + r
  return [
    [x(3), y(0), x(5), y(2)], [x(5), y(0), x(3), y(2)],
    [x(3), y(7), x(5), y(9)], [x(5), y(7), x(3), y(9)],
  ]
})

const lastMove = computed(() => props.state?.last_move || null)
const checkCell = computed(() => props.state?.check || null)
const suggestion = computed(() => props.state?.suggest || null)
const illegalMap = computed(() => {
  const map = new Map()
  for (const item of props.state?.illegal || []) if (item?.to) map.set(`${item.to[0]}:${item.to[1]}`, item.reason || '不可落子')
  return map
})
const illegalCells = computed(() => {
  const out = []
  for (const [key, reason] of illegalMap.value) {
    const [r, c] = key.split(':').map(Number)
    out.push({ r, c, reason })
  }
  return out
})
function targetOccupied(r, c) { return !!pieceAt(r, c) }

function pick(point) {
  if (props.disabled) return
  emit('pick', [point.r, point.c])
}
function label(point) {
  const piece = pieceAt(point.r, point.c)
  const base = `第${point.r + 1}行第${point.c + 1}列`
  return piece ? `${base}：${piece.glyph || (piece.code === 'black' ? '黑子' : '白子')}` : `${base}：空`
}
</script>

<template>
  <svg class="board2d" :viewBox="viewBox" role="img" :aria-label="`${state.title}棋盘`"
       :class="[`layout-${layout}`, { disabled }]">
    <!-- 方形格棋盘：井字棋 / 国际象棋 -->
    <g v-if="layout === 'cells'" class="squares">
      <rect v-for="cell in hits" :key="'sq' + cell.r + '-' + cell.c"
            :x="cell.c" :y="cell.r" width="1" height="1" :class="['sq', squareClass(cell.r, cell.c)]" />
      <g v-if="game === 'ttt'" class="ttt-grid">
        <line v-for="i in 2" :key="'tv' + i" :x1="i" y1="0" :x2="i" :y2="rows" />
        <line v-for="i in 2" :key="'th' + i" x1="0" :y1="i" :x2="cols" :y2="i" />
      </g>
    </g>

    <!-- 交叉点棋盘：围棋 / 五子棋 / 中国象棋 -->
    <g v-else class="lines">
      <line v-for="(segment, index) in gridSegments" :key="'ln' + index"
            :x1="segment[0]" :y1="segment[1]" :x2="segment[2]" :y2="segment[3]" />
      <line v-for="(segment, index) in palaceLines" :key="'pg' + index"
            :x1="segment[0]" :y1="segment[1]" :x2="segment[2]" :y2="segment[3]" />
      <circle v-for="(point, index) in starPoints" :key="'st' + index"
              :cx="cx(point[1])" :cy="cy(point[0])" r="0.09" class="star" />
      <text v-if="game === 'xiangqi'" :x="cx(2)" :y="cy(4) + 0.62" class="river">楚 河</text>
      <text v-if="game === 'xiangqi'" :x="cx(6)" :y="cy(4) + 0.62" class="river">汉 界</text>
    </g>

    <!-- 棋子 -->
    <g v-for="cell in pieces" :key="'pc' + cell.r + '-' + cell.c" class="piece"
       :transform="`translate(${cx(cell.c)} ${cy(cell.r)})`">
      <circle v-if="stoneGame" r="0.44" :class="['stone', cell.piece.code]" />
      <g v-else-if="game === 'ttt'" class="ttt-mark" :class="cell.piece.side" aria-hidden="true">
        <circle v-if="cell.piece.code === 'O' || cell.piece.glyph === 'O'" r="0.31" />
        <path v-else d="M -.31 -.31 L .31 .31 M .31 -.31 L -.31 .31" />
      </g>
      <g v-else-if="game === 'xiangqi'">
        <circle r="0.45" :class="['xiangqi-disc', cell.piece.side]" />
        <text class="glyph xiangqi-glyph" :class="cell.piece.side">{{ cell.piece.glyph }}</text>
      </g>
      <text v-else class="glyph" :class="[game, cell.piece.side]">{{ cell.piece.glyph }}</text>
    </g>

    <!-- 最后一手 -->
    <g v-if="lastMove && lastMove.from" class="last-from">
      <rect v-if="layout === 'cells'" :x="lastMove.from[1] + 0.06" :y="lastMove.from[0] + 0.06" width="0.88" height="0.88" />
      <circle v-else :cx="cx(lastMove.from[1])" :cy="cy(lastMove.from[0])" r="0.46" />
    </g>
    <g v-if="lastMove && lastMove.to" class="last">
      <rect v-if="layout === 'cells'" :x="lastMove.to[1]" :y="lastMove.to[0]" width="1" height="1" class="last-cell" />
      <circle v-else :cx="cx(lastMove.to[1])" :cy="cy(lastMove.to[0])" r="0.5" class="last-point" />
    </g>

    <!-- 被将军的将/帅/王 -->
    <g v-if="checkCell" class="check">
      <rect v-if="layout === 'cells'" :x="checkCell[1] + 0.08" :y="checkCell[0] + 0.08" width="0.84" height="0.84" />
      <circle v-else :cx="cx(checkCell[1])" :cy="cy(checkCell[0])" r="0.46" />
    </g>

    <!-- 合法目标：圆点 = 空位，圆环 = 可吃子/落点重叠 -->
    <g v-for="item in legalTargets" :key="'lg' + item[0] + '-' + item[1]" class="legal">
      <circle :cx="cx(item[1])" :cy="cy(item[0])" :r="targetOccupied(item[0], item[1]) ? 0.42 : 0.15" />
    </g>

    <!-- 非法落点（围棋自杀/劫争） -->
    <g v-for="cell in illegalCells" :key="'il' + cell.r + '-' + cell.c" class="illegal">
      <circle :cx="cx(cell.c)" :cy="cy(cell.r)" r="0.14" />
    </g>

    <!-- 建议 -->
    <g v-if="suggestion && suggestion.to" class="suggest">
      <circle v-if="layout === 'points'" :cx="cx(suggestion.to[1])" :cy="cy(suggestion.to[0])" r="0.52" />
      <rect v-else :x="suggestion.to[1] + 0.04" :y="suggestion.to[0] + 0.04" width="0.92" height="0.92" />
    </g>

    <!-- 选中 -->
    <g v-if="selected" class="selected">
      <circle v-if="layout === 'points'" :cx="cx(selected[1])" :cy="cy(selected[0])" r="0.5" />
      <rect v-else :x="selected[1] + 0.03" :y="selected[0] + 0.03" width="0.94" height="0.94" />
    </g>

    <!-- 透明点击区放在最上层，保证点在棋子上也能选中 -->
    <g v-if="layout === 'cells'" class="hits">
      <rect v-for="point in hits" :key="'ht' + point.r + '-' + point.c"
            :x="point.c" :y="point.r" width="1" height="1"
            :aria-label="label(point)" role="button" tabindex="-1" @click="pick(point)" />
    </g>
    <g v-else class="hits">
      <circle v-for="point in hits" :key="'ht' + point.r + '-' + point.c"
              :cx="cx(point.c)" :cy="cy(point.r)" r="0.46"
              :aria-label="label(point)" role="button" tabindex="-1" @click="pick(point)" />
    </g>
  </svg>
</template>

<style scoped>
.board2d { display: block; width: 100%; height: auto; max-height: 100%; user-select: none;
           background: radial-gradient(120% 120% at 30% 0%, #16242f, #0b141c); border-radius: 4px; }
.board2d.disabled { opacity: .75; }
.sq.light { fill: #24384a; }
.sq.dark { fill: #16232f; }
.sq.ttt { fill: #122029; }
.ttt-grid line { stroke: #5f7f90; stroke-width: .06; }
.lines line { stroke: #748c9d; stroke-width: .055; }
.star { fill: #6f8797; }
.river { fill: #6f8797; font-size: .52px; text-anchor: middle; letter-spacing: .18em; }
.last-cell { fill: none; stroke: var(--warn, #e0a33b); stroke-width: .1; }
.last-point { fill: none; stroke: var(--warn, #e0a33b); stroke-width: .07; }
.last-from rect, .last-from circle { fill: none; stroke: var(--warn, #e0a33b); stroke-width: .055; opacity: .45; }
.check rect, .check circle { fill: none; stroke: var(--danger, #e4553f); stroke-width: .09; }
.legal circle { fill: var(--ok, #40c979); opacity: .55; }
.illegal circle { fill: var(--danger, #e4553f); opacity: .75; }
.suggest circle, .suggest rect { fill: none; stroke: var(--acc, #2fd6f0); stroke-width: .08; stroke-dasharray: .18 .12; }
.selected circle, .selected rect { fill: none; stroke: var(--acc, #2fd6f0); stroke-width: .1; }
.stone { stroke-width: .03; }
.stone.black { fill: #101820; stroke: #4c6273; }
.stone.white { fill: #e8eef4; stroke: #8ea3b3; }
.xiangqi-disc { fill: #e8dcc2; stroke-width: .04; }
.xiangqi-disc.human { stroke: #a33a32; }
.xiangqi-disc.robot { stroke: #1c2733; }
.glyph { font-size: .78px; text-anchor: middle; dominant-baseline: central; font-family: var(--font-display, serif); }
.ttt-mark { fill: none; stroke-width: .075px; stroke-linecap: round; stroke-linejoin: round; }
.ttt-mark.human { stroke: var(--acc, #2fd6f0); }
.ttt-mark.robot { stroke: var(--warn, #e0a33b); }
.glyph.chess { font-size: .72px; paint-order: stroke; stroke-width: .025; }
.glyph.chess.human { fill: #f2f7fb; stroke: #0a1220; }
.glyph.chess.robot { fill: #16212e; stroke: #e6eef7; }
.glyph.xiangqi-glyph { font-size: .58px; font-weight: 700; }
.glyph.xiangqi-glyph.human { fill: #b23a32; }
.glyph.xiangqi-glyph.robot { fill: #1c2733; }
/* SVG 的原生 :focus 外圈按棋盘坐标放大；点击也会触发，不能只覆盖 :focus-visible。 */
.hits > * { fill: transparent; cursor: pointer; outline: none; }
.hits > *:focus { outline: none; }
.board2d.disabled .hits > * { cursor: default; }
.hits > *:hover { fill: #ffffff14; }
.hits > *:focus-visible { outline: none; fill: #2fd6f022; }
</style>
