<script setup>
import { computed, onActivated, onDeactivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useWorld } from '../store'
import { displayNumber, displayNumericText } from '../numberDisplay.js'
import Board2D from '../components/Board2D.vue'
import { robotPlayRequest } from '../robotPlayProgram'

/* 每个棋种独立页面：2D 棋盘 + 轮次 + 最后一手 + 合法位置 + 非法原因 + 建议。
   2D 规则全部来自后端；落子后由机械臂仿真取放，全部动作完成后才结束本回合。
   3D 棋盘在搬运期间用实体工件位置显示被吸住的棋子。 */
const props = defineProps({ game: { type: String, required: true } })
const w = useWorld()

const TITLES = { ttt: '井字棋', gomoku: '五子棋', chess: '国际象棋', xiangqi: '中国象棋', go: '围棋' }
const CHESS_GLYPH = { K: '♔', Q: '♕', R: '♖', B: '♗', N: '♘', P: '♙', k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' }
const XIANGQI_GLYPH = { K: '帥', A: '仕', E: '相', H: '傌', R: '俥', C: '炮', P: '兵',
  k: '將', a: '士', e: '象', h: '馬', r: '車', c: '砲', p: '卒' }

const LEVEL_OPTIONS = [{ id: 'beginner', label: '入门' }, { id: 'easy', label: '简单' },
  { id: 'normal', label: '一般' }, { id: 'hard', label: '困难' }, { id: 'impossible', label: '不可战胜' }]

const state = ref(null)
const display = ref(null)
const error = ref('')
const busy = ref(false)
const executing = ref(false)
const physicalOn = ref(false)
const physical = ref(null)
const execText = ref('')
const selected = ref(null)
const showHint = ref(false)
const level = ref('normal')
const promotion = ref(null)
const started = ref(false)
const paused = ref(false)
const controlling = ref(false)
const sessionId = ref(null)
const physicalLayout = ref(null)
const physicalGroup = ref(null)
const failed = ref(false)
const recoveryNeeded = ref(false)
const PROMOTION_OPTIONS = [{ code: 'Q', label: '后' }, { code: 'R', label: '车' },
  { code: 'B', label: '象' }, { code: 'N', label: '马' }]
const cache = new Map()
let alive = true
let token = 0
let runVersion = 0
let visible = true
const stateWaiters = new Set()

const title = computed(() => state.value?.title || TITLES[props.game] || '棋类')
const localTurn = computed(() => !state.value || state.value.turn_owner === 'human')
const canUndo = computed(() => !!state.value?.can_undo && !busy.value && !executing.value && !physicalOn.value)
const locked = computed(() => busy.value || executing.value || controlling.value || paused.value)
const playable = computed(() => started.value && !locked.value && !failed.value && !recoveryNeeded.value)
const boardState = computed(() => display.value || state.value)

const pieceMap = computed(() => {
  const map = new Map()
  for (const cell of state.value?.cells || []) map.set(`${cell.r}:${cell.c}`, cell.piece)
  return map
})

const legalTargets = computed(() => {
  const current = state.value
  if (!current || current.over || current.drop || !selected.value) return []
  const [fr, fc] = selected.value
  const seen = new Set()
  const targets = []
  for (const move of current.moves || []) {
    if (move.from?.[0] !== fr || move.from?.[1] !== fc) continue
    const key = `${move.to[0]}:${move.to[1]}`
    if (seen.has(key)) continue
    seen.add(key)
    targets.push(move.to)
  }
  return targets
})

const hintText = computed(() => {
  const suggestion = state.value?.suggest
  if (!suggestion) return ''
  const from = suggestion.from ? `第${suggestion.from[0] + 1}行第${suggestion.from[1] + 1}列 → ` : ''
  const to = suggestion.to
  const target = to ? `第${to[0] + 1}行第${to[1] + 1}列` : '停一手'
  return `${from}${target}。${suggestion.reason || ''}`
})

const illegalCount = computed(() => (boardState.value?.illegal || []).length)
const historyText = computed(() => (boardState.value?.history || []).join(' · '))
const statusLine = computed(() => {
  if (failed.value) return '本手未完成，请重新开始'
  if (paused.value) return '已暂停'
  if (recoveryNeeded.value) return '点击继续完成本回合'
  if (!started.value) return '点击开始'
  if (selected.value && state.value?.drop) return '再点一次确认落子'
  if (executing.value) return execText.value || '机械臂正在执行落子…'
  return state.value?.status || '读取中'
})

function glyphOf(code) {
  if (props.game === 'xiangqi') return XIANGQI_GLYPH[code] || ''
  if (props.game === 'chess') return CHESS_GLYPH[code] || ''
  return ''
}

function pushScene() {
  const scene = globalThis.window?.__elfinScene
  if (!scene?.setGameState) return
  const current = display.value || state.value
  if (!current) { scene.setGameState(null); return }
  scene.setGameState({
    ...current,
    physical_layout: physicalLayout.value,
    physical_group: physicalGroup.value,
    selected: selected.value,
    legal: legalTargets.value,
    suggest: showHint.value ? state.value?.suggest : null,
    showHint: showHint.value,
  })
}

function pushPhysical() {
  const scene = globalThis.window?.__elfinScene
  if (!scene?.setGamePhysical) return
  if (!executing.value || !physical.value) { scene.setGamePhysical([]); return }
  const objects = w.snap?.objects || []
  scene.setGamePhysical((physical.value.items || []).map(item => {
    const object = objects.find(row => row.id === item.object_id)
    return { id: item.object_id, code: item.code, side: item.side,
      glyph: glyphOf(item.code), from: item.from, to: item.to,
      xyz: Array.isArray(object?.xyz) ? object.xyz : null }
  }))
}

// Physical readback owns displayed completion; a user intent is published separately by play().
function observedState(value) {
  if (value?.game && value.game !== props.game) return null
  const actual = value?.actual_state || value?.confirmed_state
  if (!actual) return null
  if (!(value.physical_pending || value.lifecycle === 'failed') || !value.confirmed_state) return actual
  const view = { ...actual }
  for (const key of ['history', 'last_move', 'turn', 'turn_owner', 'captures', 'score', 'over', 'winner']) {
    if (key in value.confirmed_state) view[key] = value.confirmed_state[key]
    else delete view[key]
  }
  return view
}

function cacheDisplay() {
  if (display.value) cache.set(props.game, display.value)
}

function apply(result) {
  if (result?.state) state.value = result.state
  if (result?.physical !== undefined) physicalOn.value = !!result.physical
  applyLifecycle(result?.physical)
  const observed = observedState(result?.physical)
  if (observed && !executing.value) display.value = observed
  else if (result?.ok && !executing.value && !failed.value && !recoveryNeeded.value && result.state) display.value = result.state
  cacheDisplay()
  if (!result?.ok) {
    error.value = result?.why || '操作被拒绝'
    return false
  }
  level.value = result.state?.level || level.value
  selected.value = null
  if (!failed.value) error.value = ''
  return true
}

function applyLifecycle(value) {
  if (!value || typeof value !== 'object') return
  if (value.game && value.game !== props.game) {
    started.value = false
    paused.value = false
    sessionId.value = null
    physicalLayout.value = null
    physicalGroup.value = null
    return
  }
  if (value.session_id !== undefined) sessionId.value = value.session_id
  if (value.group !== undefined) physicalGroup.value = value.group
  if (value.layout && typeof value.layout === 'object') physicalLayout.value = value.layout
  if (typeof value.active === 'boolean') started.value = value.active
  if (typeof value.paused === 'boolean') paused.value = value.paused
  if (value.active === false && !value.physical_pending && value.actual_state) {
    state.value = value.actual_state
    display.value = value.actual_state
  }
  if (value.lifecycle === 'failed') {
    selected.value = null
    promotion.value = null
    showHint.value = false
    failed.value = true
    recoveryNeeded.value = false
    const actual = observedState(value)
    if (actual) { state.value = actual; display.value = actual; cacheDisplay() }
    if (!error.value) error.value = value.message || '落子未完成'
  }
}

async function request(action) {
  busy.value = true
  const mine = ++token
  const game = props.game
  try {
    const result = await action()
    if (!alive || !visible || mine !== token || props.game !== game) return null
    apply(result)
    return result
  } catch (caught) {
    if (alive && visible && mine === token && props.game === game) error.value = caught?.message || '请求失败'
    return null
  } finally {
    if (alive && mine === token) busy.value = false
  }
}

async function load() {
  const game = props.game
  const generation = runVersion
  const cached = cache.get(props.game)
  if (cached) state.value = cached
  const result = await request(() => w.gameState(props.game))
  if (!alive || !result?.ok) return
  try {
    const info = await w.gamePhysical(game)
    if (!alive || !visible || props.game !== game || generation !== runVersion) return
    if (info?.ok) applyLifecycle(info.physical)
    if (info?.ok && started.value && (info.physical?.queued || info.physical?.current || info.physical?.physical_pending)) {
      recoveryNeeded.value = true
      const actual = observedState(info.physical)
      if (actual) { display.value = actual; cacheDisplay() }
    }
  } catch { /* 实体状态读取失败不阻断 2D 棋盘 */ }
}

function stateKey() {
  return [w.snap?.exec?.state, w.snap?.robot_play?.planning, paused.value, started.value,
    w.connected, w.snap?.session_id]
}

function wakeStateWaiters() {
  for (const wake of [...stateWaiters]) wake()
}

function waitStateChange(current, before = stateKey()) {
  const changed = () => stateKey().some((value, index) => !Object.is(value, before[index]))
  if (!current() || changed()) return Promise.resolve()
  return new Promise(resolve => {
    let dispose, timer, finished = false
    const finish = () => {
      if (finished) return
      finished = true
      dispose?.()
      clearTimeout(timer)
      stateWaiters.delete(finish)
      resolve()
    }
    stateWaiters.add(finish)
    dispose = watch([() => w.snap?.exec?.state, () => w.snap?.robot_play?.planning,
      paused, started, () => w.connected, () => w.snap?.session_id], finish, { flush: 'sync' })
    // 正常由到位/暂停/停止状态帧立即唤醒；此超时只在状态帧缺失时补读服务端。
    timer = setTimeout(finish, 1000)
    if (!current() || changed()) finish()
  })
}

async function waitRobot(current) {
  let remaining = 180000
  let previous = Date.now()
  while (current() && remaining > 0) {
    const before = stateKey()
    const status = await robotPlayRequest('state')
    if (!current()) return false
    const now = Date.now()
    if (!paused.value && status.execution?.state !== 'paused') remaining -= now - previous
    previous = now
    if (!status.active && !status.planning && !['running', 'waiting', 'paused'].includes(status.execution?.state)) {
      if (status.held) throw new Error('机械臂仍吸附着棋子；请停止后手动处理')
      const run = status.execution?.state
      if (run && run !== 'finished' && run !== 'idle') throw new Error(`实体落子未完成：${run}`)
      return true
    }
    await waitStateChange(current, before)
  }
  if (!current()) return false
  throw new Error('等待机械臂执行超时，可点击停止')
}

function applyAction(action) {
  const base = display.value
  if (!base || !action) return
  const cells = (base.cells || []).map(cell => ({ ...cell, piece: { ...cell.piece } }))
  for (const item of action.items || []) {
    const index = item.from ? cells.findIndex(cell => cell.r === item.from[0] && cell.c === item.from[1]) : -1
    if (index >= 0) {
      const piece = cells[index].piece
      cells.splice(index, 1)
      if (item.to) {
        const destination = cells.findIndex(cell => cell.r === item.to[0] && cell.c === item.to[1])
        if (destination >= 0) cells.splice(destination, 1)
        cells.push({ r: item.to[0], c: item.to[1],
          piece: { code: item.code || piece.code, glyph: glyphOf(item.code) || piece.glyph,
            side: item.side || piece.side } })
      }
    } else if (item.to) {
      const destination = cells.findIndex(cell => cell.r === item.to[0] && cell.c === item.to[1])
      if (destination >= 0) cells.splice(destination, 1)
      cells.push({ r: item.to[0], c: item.to[1],
        piece: { code: item.code, glyph: glyphOf(item.code), side: item.side } })
    }
  }
  display.value = { ...base, cells, last_move: action.items?.[0]?.to ? { from: action.items[0].from, to: action.items[0].to } : base.last_move }
  pushScene()
}

async function runPhysical() {
  if (executing.value || !started.value) return
  const mine = ++runVersion
  const game = props.game
  const worldSession = w.snap?.session_id
  const sameConnection = () => !worldSession || worldSession === w.snap?.session_id
  const owned = () => alive && visible && mine === runVersion && props.game === game
  const current = () => owned() && started.value && sameConnection()
  executing.value = true
  recoveryNeeded.value = false
  failed.value = false
  error.value = ''
  pushScene()
  let guard = 0
  let completed = false
  try {
    while (current() && guard < 140) {
      if (paused.value) { await waitStateChange(current); continue }
      guard++
      execText.value = '准备落子'
      const step = await w.gameStep(game, sessionId.value)
      if (!current()) return
      if (step?.state) state.value = step.state
      applyLifecycle(step?.physical)
      if (!step?.ok || failed.value) { error.value = step?.why || '落子未完成'; failed.value = true; break }
      if (step.action) {
        physical.value = step.action
        execText.value = step.action.label || '落子中'
        pushPhysical()
      }
      if (step.done) { completed = true; break }
      if (!step.running) continue
      if (!await waitRobot(current)) return
      if (step.action) applyAction(step.action)
      physical.value = null
      pushPhysical()
    }
    if (current() && !completed && !failed.value) { failed.value = true; error.value = '本回合未完成' }
  } catch (caught) {
    if (current()) { error.value = caught?.message || '落子未完成'; failed.value = true }
  } finally {
    if (!owned()) return
    if (!sameConnection()) {
      failed.value = true
      started.value = false
      error.value = '连接已更新，请重新开始'
    }
    if (failed.value && sameConnection()) {
      try {
        const info = await w.gamePhysical(game)
        if (!owned()) return
        if (info?.ok) applyLifecycle(info.physical)
      } catch { /* 留下失败状态，不重发动作。 */ }
    }
    executing.value = false
    physical.value = null
    execText.value = ''
    if (completed && !failed.value) display.value = state.value
    cacheDisplay()
    pushPhysical()
    pushScene()
  }
}

async function startNew() {
  if (locked.value) return
  showHint.value = false
  const result = await request(() => w.gameNew(props.game, level.value))
  if (!result?.ok) return
  failed.value = false
  recoveryNeeded.value = false
  error.value = ''
  started.value = result.physical?.active !== false
  paused.value = false
  physical.value = null
  display.value = result.state
  pushScene()
  if (result.physical?.queued || result.physical?.current) void runPhysical()
}

async function changeLevel(event) {
  level.value = event.target.value
}

async function play(move) {
  if (!playable.value) return
  showHint.value = false
  const result = await request(() => w.gameMove(props.game, move.from || null, move.to || null,
    { ...(move.pass ? { pass: true } : (move.promotion ? { promotion: move.promotion } : {})),
      ...(sessionId.value ? { session_id: sessionId.value } : {}) }))
  if (!result?.ok) return
  if (result.physical?.queued) {
    // Show the user's chosen move immediately; automatic robot replies stay physical-only until completed.
    if (result.state) display.value = result.state
    await runPhysical()
  }
}

async function undo() {
  if (!canUndo.value) return
  showHint.value = false
  await request(() => w.gameUndo(props.game))
}

async function toggleHint() {
  if (locked.value) return
  if (showHint.value) { showHint.value = false; return }
  showHint.value = true
  await request(() => w.gameSuggest(props.game))
}

async function passTurn() {
  if (!playable.value) return
  await play({ pass: true })
}

async function stopPhysical() {
  if (!started.value || controlling.value) return
  controlling.value = true
  const game = props.game
  try {
    const result = await w.gameAbort(game, sessionId.value)
    if (!alive || props.game !== game) return
    if (!result?.ok) { error.value = result?.why || '终止失败'; return }
    token++
    runVersion++
    wakeStateWaiters()
    busy.value = false
    executing.value = false
    started.value = false
    paused.value = false
    selected.value = null
    promotion.value = null
    physical.value = null
    execText.value = ''
    if (result.state) state.value = result.state
    if (result.physical?.actual_state) {
      display.value = result.physical.actual_state
      state.value = result.physical.actual_state
    } else if (!failed.value && !recoveryNeeded.value) display.value = state.value
    pushScene()
    pushPhysical()
  } catch (caught) { if (alive && props.game === game) error.value = caught?.message || '终止失败' }
  finally { if (alive && props.game === game) controlling.value = false }
}

async function togglePause() {
  if (!started.value || controlling.value || failed.value) return
  controlling.value = true
  const game = props.game
  const resume = paused.value || recoveryNeeded.value
  try {
    const result = await (resume ? w.gameResume(game, sessionId.value) : w.gamePause(game, sessionId.value))
    if (!alive || props.game !== game) return
    if (!result?.ok) { error.value = result?.why || '操作失败'; return }
    paused.value = !resume
    applyLifecycle(result.physical)
    error.value = ''
    if (resume && !executing.value) void runPhysical()
  } catch (caught) { if (alive && props.game === game) error.value = caught?.message || '操作失败' }
  finally { if (alive && props.game === game) controlling.value = false }
}

function onPick([r, c]) {
  error.value = ''
  const current = state.value
  if (!current || !playable.value || current.over || !localTurn.value) return
  if (current.drop) {
    if (pieceMap.value.has(`${r}:${c}`)) { error.value = '这个位置已经有棋子了'; return }
    const illegal = (current.illegal || []).find(item => item.to && item.to[0] === r && item.to[1] === c)
    if (illegal) { error.value = illegal.reason; return }
    if (selected.value?.[0] === r && selected.value?.[1] === c) {
      selected.value = null
      void play({ to: [r, c] })
    } else selected.value = [r, c]
    return
  }
  const piece = pieceMap.value.get(`${r}:${c}`)
  if (piece && piece.side === current.turn_owner) {
    selected.value = [r, c]
    return
  }
  if (!selected.value) { error.value = '请先选择己方棋子'; return }
  const [fr, fc] = selected.value
  const options = (current.moves || []).filter(move => move.from?.[0] === fr && move.from?.[1] === fc
    && move.to?.[0] === r && move.to?.[1] === c && move.promotion)
  if (options.length) {
    promotion.value = { from: [fr, fc], to: [r, c] }
    return
  }
  if (!legalTargets.value.some(to => to[0] === r && to[1] === c)) { error.value = '不能走到这里'; return }
  void play({ from: selected.value, to: [r, c] })
}

function choosePromotion(code) {
  if (!promotion.value) return
  const { from, to } = promotion.value
  promotion.value = null
  void play({ from, to, promotion: code })
}

function focusBoard() {
  globalThis.window?.__elfinScene?.focusGame?.()
}

function resetForGame() {
  token++
  runVersion++
  wakeStateWaiters()
  busy.value = false
  executing.value = false
  controlling.value = false
  started.value = false
  paused.value = false
  failed.value = false
  recoveryNeeded.value = false
  sessionId.value = null
  physicalLayout.value = null
  physicalGroup.value = null
  state.value = cache.get(props.game) || null
  display.value = state.value
  level.value = state.value?.level || 'normal'
  selected.value = null
  showHint.value = false
  promotion.value = null
  physical.value = null
  physicalOn.value = false
  error.value = ''
  pushScene()
  pushPhysical()
  void load()
}

function clearSceneState() {
  token++
  runVersion++
  wakeStateWaiters()
  busy.value = false
  executing.value = false
  controlling.value = false
  started.value = false
  paused.value = false
  failed.value = false
  recoveryNeeded.value = false
  selected.value = null
  promotion.value = null
  state.value = null
  display.value = null
  physical.value = null
  physicalOn.value = false
  physicalLayout.value = null
  physicalGroup.value = null
  sessionId.value = null
  execText.value = ''
  error.value = ''
  cache.clear()
  pushPhysical()
  pushScene()
}

onMounted(() => {
  globalThis.window?.addEventListener('elfin-scene-ready', pushScene)
  globalThis.window?.addEventListener('elfin-scene-ready', pushPhysical)
  void load()
})
onDeactivated(() => { visible = false; token++; runVersion++; wakeStateWaiters(); busy.value = false; executing.value = false })
onActivated(() => { if (!visible) { visible = true; void load() } })
watch(() => props.game, resetForGame)
watch(() => w.sceneClearRevision, clearSceneState)
watch([state, display, selected, legalTargets, showHint, physicalLayout, physicalGroup], pushScene)
watch(() => w.snap?.objects, pushPhysical)
watch(() => state.value?.turn, () => {
  selected.value = null
  promotion.value = null
  pushScene()
})
onBeforeUnmount(() => {
  alive = false
  token++
  runVersion++
  wakeStateWaiters()
  globalThis.window?.removeEventListener('elfin-scene-ready', pushScene)
  globalThis.window?.removeEventListener('elfin-scene-ready', pushPhysical)
  globalThis.window?.__elfinScene?.setGameState?.(null)
  globalThis.window?.__elfinScene?.setGamePhysical?.([])
})
</script>

<template>
  <div class="col games-panel">
    <section class="panel">
      <div class="panel-hd">
        <span class="panel-title">{{ title }}</span>
        <span class="tag">人机 · {{ state?.level_label || '一般' }}</span>
      </div>
      <div class="panel-bd col games-body">
        <div class="row game-actions">
          <button class="btn btn--primary" :class="{ 'btn--danger': started }" :disabled="controlling || (!started && busy)" @click="started ? stopPhysical() : startNew()">{{ started ? '终止' : '开始' }}</button>
          <button class="btn" :disabled="!started || controlling || failed" @click="togglePause">{{ paused || recoveryNeeded ? '继续' : '暂停' }}</button>
          <button class="btn btn--xs" :class="{ 'btn--primary': showHint }" :disabled="locked || state?.over"
                  @click="toggleHint">{{ showHint ? '收起提示' : '提示' }}</button>
          <button class="btn btn--xs" title="把三维视角移到棋盘上" @click="focusBoard">对准棋盘</button>
          <button v-if="game === 'go'" class="btn btn--xs" :disabled="!playable || state?.over || !localTurn" @click="passTurn">停一手</button>
        </div>
        <label class="row tiny">
          难度
          <select class="select grow" :value="level" :disabled="started || busy" @change="changeLevel">
            <option v-for="item in LEVEL_OPTIONS" :key="item.id" :value="item.id">{{ item.label }}</option>
          </select>
        </label>

        <p class="turn-line" role="status" aria-live="polite">{{ statusLine }}</p>

        <Board2D v-if="boardState" :state="boardState" :selected="selected" :legal-targets="legalTargets"
                 :disabled="!playable || state.over || !localTurn || !!promotion" @pick="onPick" />

        <div v-if="promotion" class="row promotion" role="group" aria-label="选择升变棋子">
          <span class="tiny">升变：</span>
          <button v-for="option in PROMOTION_OPTIONS" :key="option.code" class="btn btn--xs" @click="choosePromotion(option.code)">{{ option.label }}</button>
        </div>

        <p v-if="error && !failed" class="note" role="alert">{{ displayNumericText(error) }}</p>
        <p v-if="hintText" class="hint" role="status">建议：{{ hintText }}</p>
        <p v-if="illegalCount" class="tiny dim">红点：当前不可落子（自杀或劫争）。</p>

        <div v-if="boardState?.captures || boardState?.score" class="tiny dim">
          <span v-if="boardState.captures">提子：黑 {{ boardState.captures.black }} / 白 {{ boardState.captures.white }}</span>
          <span v-if="boardState.score"> · 目数：黑 {{ displayNumber(boardState.score.black) }} / 白 {{ displayNumber(boardState.score.white) }}</span>
        </div>

        <div v-if="boardState?.history?.length" class="history tiny">
          <span class="dim">最近：</span>
          <span>{{ historyText }}</span>
        </div>

      </div>
    </section>
  </div>
</template>

<style scoped>
.games-panel { min-width: 0; }
.games-panel .panel { min-height: 0; }
.games-body { gap: 8px; }
.game-actions { gap: 6px; flex-wrap: wrap; }
.turn-line { margin: 0; font-size: 13px; color: var(--acc); }
.hint { margin: 0; font-size: 12px; color: var(--warn, #e0a33b); line-height: 1.5; }
.history { line-height: 1.6; overflow-wrap: anywhere; }
</style>
