import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { displayNumber, displayNumericText } from '../src/numberDisplay.js'

const source = readFileSync(new URL('../src/panels/GamesPanel.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Vue.nextTick() }
function snapshot(game = 'ttt', extra = {}) {
  return { game, title: '棋局', rows: 3, cols: 3, layout: 'cells', drop: true,
    turn: 0, turn_owner: 'human', cells: [], moves: [], illegal: [], history: [], over: false, ...extra }
}
function harness(t, overrides = {}, game = 'ttt', runtime = {}) {
  const calls = [], mounted = [], unmounted = [], deactivated = [], activated = [], sceneStates = [], viewChanges = []
  const state = snapshot(game)
  const physical = { active: true, paused: false, game, session_id: `${game}-session`,
    layout: { board_center: [-500, -200], board_z: 70 } }
  const result = () => ({ ok: true, state: structuredClone(state), physical: structuredClone(physical) })
  const world = Vue.reactive({ snap: { objects: [] },
    gameState: async key => { calls.push(['state', key]); return result() },
    gamePhysical: async key => { calls.push(['physical', key]); return result() },
    gameNew: async (...args) => { calls.push(['new', ...args]); return result() },
    gameMove: async (...args) => { calls.push(['move', ...args]); return result() },
    gameStep: async (...args) => { calls.push(['step', ...args]); return { ...result(), done: true } },
    gameAbort: async (...args) => { calls.push(['abort', ...args]); return { ...result(), physical: { ...physical, active: false } } },
    gamePause: async (...args) => { calls.push(['pause', ...args]); return { ...result(), physical: { ...physical, paused: true } } },
    gameResume: async (...args) => { calls.push(['resume', ...args]); return result() },
    gameSuggest: async () => result(), gameUndo: async () => result(), ...overrides })
  const previousWindow = globalThis.window
  globalThis.window = { addEventListener() {}, removeEventListener() {}, __elfinScene: {
    setGameState: value => sceneStates.push(value), setGamePhysical() {}, focusGame: () => viewChanges.push('focus'),
  } }
  const dependencies = { ref: Vue.ref, computed: Vue.computed, watch: Vue.watch,
    useWorld: () => world, Board2D: {}, displayNumber, displayNumericText,
    onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn),
    onDeactivated: fn => deactivated.push(fn), onActivated: fn => activated.push(fn),
    robotPlayRequest: async () => ({ active: false, planning: false, execution: { state: 'finished' } }), ...runtime }
  const code = compileScript(descriptor, { id: 'board-panel' }).content
    .replace(/^import[^\n]*\n/gm, '').replace('export default', 'return')
  const component = new Function(...Object.keys(dependencies), code)(...Object.values(dependencies))
  const scope = Vue.effectScope(), props = Vue.reactive({ game })
  const api = scope.run(() => component.setup(props, { expose() {} }))
  api.apply(result())
  t.after(() => { unmounted.forEach(fn => fn()); scope.stop(); globalThis.window = previousWindow })
  return { api, world, calls, props, sceneStates, viewChanges, mounted, deactivated, activated, result }
}

test('board panel and 2D board compile with game lifecycle controls', () => {
  for (const path of ['../src/panels/GamesPanel.vue', '../src/components/Board2D.vue', '../src/panels/GamesHub.vue']) {
    const contents = readFileSync(new URL(path, import.meta.url), 'utf8')
    const d = parse(contents).descriptor
    const script = compileScript(d, { id: 'board-compile' })
    assert.deepEqual(compileTemplate({ source: d.template.content, filename: path, id: 'board-compile',
      compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
    for (const style of d.styles) assert.deepEqual(compileStyle({ source: style.content, filename: path, id: 'board-compile', scoped: style.scoped }).errors, [])
  }
  assert.match(source, /started \? '终止' : '开始'/)
  assert.match(source, /paused \|\| recoveryNeeded \? '继续' : '暂停'/)
  assert.doesNotMatch(source, /正在请求规则引擎|落子由机械臂仿真完成|state\?\.note/)
  const board = readFileSync(new URL('../src/components/Board2D.vue', import.meta.url), 'utf8')
  assert.match(board, /\.hits > \*:focus\s*\{\s*outline:\s*none/,
    'pointer focus also needs an explicit SVG outline reset; focus-visible alone leaves the giant native ring')
})

for (const game of ['ttt', 'gomoku', 'go']) {
  test(`${game}: first click selects, second same-cell click submits exactly one move`, async t => {
    const h = harness(t, {}, game)
    await flush()
    h.api.onPick([1, 1]); await flush()
    assert.deepEqual(h.api.selected.value, [1, 1])
    assert.equal(h.calls.filter(row => row[0] === 'move').length, 0)
    h.api.onPick([1, 1]); await flush()
    assert.equal(h.calls.filter(row => row[0] === 'move').length, 1)
    assert.deepEqual(h.calls.find(row => row[0] === 'move'),
      ['move', game, null, [1, 1], { session_id: `${game}-session` }])
  })
}

test('selecting another empty point replaces pending confirmation without moving', async t => {
  const h = harness(t)
  await flush()
  h.api.onPick([1, 1]); h.api.onPick([2, 1]); await flush()
  assert.deepEqual(h.api.selected.value, [2, 1]); assert.equal(h.calls.length, 0)
  h.api.onPick([2, 1]); await flush()
  assert.equal(h.calls.filter(row => row[0] === 'move').length, 1)
})

for (const game of ['chess', 'xiangqi']) {
  test(`${game}: select own piece then legal target is exactly two clicks`, async t => {
    const h = harness(t, {}, game)
    h.api.state.value = snapshot(game, { drop: false, cells: [{ r: 2, c: 0, piece: { code: 'P', side: 'human' } }],
      moves: [{ from: [2, 0], to: [1, 0] }] })
    h.api.onPick([2, 0]); h.api.onPick([1, 0]); await flush()
    assert.equal(h.calls.filter(row => row[0] === 'move').length, 1)
    assert.deepEqual(h.calls[0].slice(0, 4), ['move', game, [2, 0], [1, 0]])
  })
}

test('illegal or occupied points never trigger a move', async t => {
  const h = harness(t)
  h.api.state.value = snapshot('go', { cells: [{ r: 0, c: 0, piece: { code: 'black' } }], illegal: [{ to: [1, 1], reason: '禁入' }] })
  h.api.onPick([0, 0]); h.api.onPick([0, 0]); h.api.onPick([1, 1]); h.api.onPick([1, 1]); await flush()
  assert.equal(h.calls.length, 0); assert.equal(h.api.error.value, '禁入')
})

test('pause/resume also govern waiting for user and preserve current session', async t => {
  const h = harness(t)
  await flush()
  h.api.onPick([1, 1]); await h.api.togglePause()
  assert.equal(h.api.paused.value, true); assert.equal(h.api.statusLine.value, '已暂停')
  h.api.onPick([1, 1]); assert.equal(h.calls.filter(row => row[0] === 'move').length, 0)
  await h.api.togglePause(); h.api.onPick([1, 1]); await flush()
  assert.deepEqual(h.calls.slice(0, 2), [['pause', 'ttt', 'ttt-session'], ['resume', 'ttt', 'ttt-session']])
  assert.equal(h.calls.filter(row => row[0] === 'move').length, 1)
})

test('abort waiting game disables moves; failed abort remains active with a visible error', async t => {
  const h = harness(t)
  await h.api.stopPhysical()
  assert.equal(h.api.started.value, false)
  assert.deepEqual(h.calls[0], ['abort', 'ttt', 'ttt-session'])
  h.api.onPick([1, 1]); h.api.onPick([1, 1]); assert.equal(h.calls.length, 1)
  h.api.started.value = true
  h.world.gameAbort = async () => ({ ok: false, why: '停止未确认' })
  await h.api.stopPhysical()
  assert.equal(h.api.started.value, true); assert.equal(h.api.error.value, '停止未确认')
})

test('abort invalidates late move response and prevents any follow-up robot step', async t => {
  let complete
  const h = harness(t, { gameMove: () => new Promise(resolve => { complete = resolve }) })
  const moving = h.api.play({ to: [1, 1] })
  await h.api.stopPhysical()
  complete({ ...h.result(), physical: { ...h.result().physical, queued: 1 } })
  await moving
  assert.equal(h.api.started.value, false); assert.equal(h.api.executing.value, false)
  assert.equal(h.calls.filter(row => row[0] === 'step').length, 0)
})

test('deactivation ignores old move response without aborting a different game', async t => {
  let complete
  const h = harness(t, { gameMove: () => new Promise(resolve => { complete = resolve }) })
  const moving = h.api.play({ to: [1, 1] })
  h.deactivated.forEach(fn => fn())
  complete({ ...h.result(), physical: { ...h.result().physical, queued: 1 } })
  await moving
  assert.equal(h.calls.length, 0)
  assert.equal(h.api.executing.value, false)
})

test('backend layout reaches scene unchanged and other game session does not leak', t => {
  const h = harness(t)
  h.api.pushScene()
  assert.deepEqual(h.sceneStates.at(-1).physical_layout, h.result().physical.layout)
  h.api.applyLifecycle({ game: 'chess', active: true, session_id: 'other', layout: { unrelated: true } })
  assert.equal(h.api.started.value, false); assert.equal(h.api.sessionId.value, null)
  assert.equal(h.api.physicalLayout.value, null)
})

test('difficulty change does not start or rebuild a game before explicit start', async t => {
  const h = harness(t)
  h.api.started.value = false
  await h.api.changeLevel({ target: { value: 'hard' } })
  assert.equal(h.calls.length, 0)
  await h.api.startNew()
  assert.deepEqual(h.calls[0], ['new', 'ttt', 'hard'])
})

test('new game waits for actual ready-position runner, even before a robot-play action is active', async t => {
  let steps = 0, statusReads = 0
  let readyWorld
  const h = harness(t, { gameStep: async () => ++steps === 1
    ? { ok: true, running: true, action: { label: '回到起点', items: [] } }
    : { ok: true, done: true } }, 'chess', {
    robotPlayRequest: async () => {
      const state = ++statusReads === 1 ? 'running' : 'finished'
      readyWorld.snap.exec = { state }
      return { active: false, planning: false, execution: { state } }
    },
  })
  readyWorld = h.world
  await h.api.runPhysical()
  assert.equal(steps, 2); assert.equal(statusReads, 2)
  assert.equal(h.api.error.value, '')
  assert.equal(h.api.executing.value, false)
})

test('new games and completed moves preserve user view; only explicit framing changes it', async t => {
  const h = harness(t)
  h.api.started.value = false
  await h.api.startNew()
  await h.api.runPhysical()
  assert.deepEqual(h.viewChanges, [])
  h.api.focusBoard()
  assert.deepEqual(h.viewChanges, ['focus'])
  const hub = readFileSync(new URL('../src/panels/GamesHub.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(hub, /focusGame|frameBoard/)
})

test('loading a pending round never replays automatically; resume requires explicit action', async t => {
  const h = harness(t)
  h.world.gamePhysical = async () => ({ ok: true, physical: { ...h.result().physical,
    queued: 1, physical_pending: true, actual_state: snapshot() } })
  await h.api.load(); await flush()
  assert.equal(h.api.recoveryNeeded.value, true)
  assert.equal(h.api.playable.value, false)
  assert.equal(h.calls.filter(row => row[0] === 'step').length, 0)
  await h.api.togglePause(); await flush()
  assert.equal(h.calls.filter(row => row[0] === 'resume').length, 1)
  assert.equal(h.calls.filter(row => row[0] === 'step').length, 1)
})

test('failed physical move uses actual board state and never displays the committed target as complete', async t => {
  const h = harness(t)
  const actual = snapshot('ttt', { cells: [] })
  h.api.state.value = snapshot('ttt', { cells: [{ r: 1, c: 1, piece: { code: 'X', side: 'robot' } }] })
  h.api.display.value = actual
  h.world.gameStep = async () => ({ ok: false, why: '物体碰撞' })
  h.world.gamePhysical = async () => ({ ok: true, physical: { ...h.result().physical,
    active: false, lifecycle: 'failed', actual_state: actual, physical_pending: true } })
  await h.api.runPhysical()
  assert.equal(h.api.failed.value, true)
  assert.equal(h.api.statusLine.value, '本手未完成，请重新开始')
  assert.deepEqual(h.api.boardState.value.cells, [])
  assert.equal(h.api.error.value, '物体碰撞')
  assert.equal(h.api.playable.value, false)
  h.api.onPick([0, 0]); h.api.onPick([0, 0]); await h.api.togglePause()
  assert.equal(h.calls.filter(row => ['move', 'resume'].includes(row[0])).length, 0)
})

test('tic tac toe uses equally sized SVG rings and round crosses, not font glyphs', () => {
  const board = readFileSync(new URL('../src/components/Board2D.vue', import.meta.url), 'utf8')
  assert.match(board, /v-else-if="game === 'ttt'" class="ttt-mark"/)
  assert.match(board, /r="0\.31"/)
  assert.match(board, /M -\.31 -\.31 L \.31 \.31/)
  assert.match(board, /\.ttt-mark \{[^}]*stroke-width: \.075px;[^}]*stroke-linecap: round/)
})

function fakeStateTimers() {
  const timers = new Set()
  return { timers, runtime: {
    setTimeout: fn => { timers.add(fn); return fn },
    clearTimeout: fn => { timers.delete(fn) },
  } }
}

test('motion completion frame starts next step immediately without ticking any timer', async t => {
  const clock = fakeStateTimers()
  let reads = 0
  const h = harness(t, {}, 'ttt', { ...clock.runtime,
    robotPlayRequest: async () => ({ active: reads++ === 0,
      planning: false, execution: { state: reads === 1 ? 'running' : 'finished' } }),
  })
  h.world.snap.exec = { state: 'running' }
  const waiting = h.api.waitRobot(() => true)
  await flush()
  assert.equal(reads, 1); assert.equal(clock.timers.size, 1)
  h.world.snap.exec.state = 'finished'
  assert.equal(await waiting, true)
  assert.equal(reads, 2); assert.equal(clock.timers.size, 0)
})

test('resume wakes paused game immediately without a 100ms pause poll', async t => {
  const clock = fakeStateTimers()
  const h = harness(t, {}, 'ttt', clock.runtime)
  h.api.paused.value = true
  const running = h.api.runPhysical()
  await flush()
  assert.equal(h.calls.filter(row => row[0] === 'step').length, 0)
  assert.equal(clock.timers.size, 1)
  await h.api.togglePause()
  await running
  assert.equal(h.calls.filter(row => row[0] === 'step').length, 1)
  assert.equal(clock.timers.size, 0)
})

test('stop cancels state waiter immediately and cannot submit the computer reply', async t => {
  const clock = fakeStateTimers()
  let steps = 0
  const h = harness(t, { gameStep: async () => { steps++; return { ok: true, running: true } } }, 'ttt', {
    ...clock.runtime,
    robotPlayRequest: async () => ({ active: true, planning: false, execution: { state: 'running' } }),
  })
  h.world.snap.exec = { state: 'running' }
  const running = h.api.runPhysical()
  await flush()
  assert.equal(steps, 1)
  assert.equal(clock.timers.size, 1)
  await h.api.stopPhysical()
  await running
  assert.equal(clock.timers.size, 0)
  assert.equal(steps, 1, 'stopping must not advance to any further human or computer action')
  assert.equal(h.api.executing.value, false)
})

test('a replaced backend session wakes and cancels the old round without sending another step', async t => {
  const clock = fakeStateTimers()
  let steps = 0
  const h = harness(t, { gameStep: async () => { steps++; return { ok: true, running: true } } }, 'ttt', {
    ...clock.runtime,
    robotPlayRequest: async () => ({ active: true, planning: false, execution: { state: 'running' } }),
  })
  h.world.snap.exec = { state: 'running' }
  h.world.snap.session_id = 'backend-old'
  const running = h.api.runPhysical()
  await flush()
  assert.equal(clock.timers.size, 1)
  h.world.snap.session_id = 'backend-new'
  await running
  assert.equal(steps, 1)
  assert.equal(clock.timers.size, 0)
  assert.equal(h.api.executing.value, false)
  assert.equal(h.api.started.value, false)
  assert.equal(h.api.error.value, '连接已更新，请重新开始')
})

test('successful scene-clear revision erases 2D/3D game state and invalidates old pending responses', async t => {
  let complete
  const h = harness(t, { gameMove: () => new Promise(resolve => { complete = resolve }) })
  h.world.sceneClearRevision = 0
  await flush()
  h.api.apply(h.result())
  const pending = h.api.play({ to: [1, 1] })
  h.world.sceneClearRevision++
  await flush()
  assert.equal(h.api.state.value, null)
  assert.equal(h.api.started.value, false)
  assert.equal(h.api.executing.value, false)
  assert.equal(h.sceneStates.at(-1), null)
  assert.deepEqual(h.viewChanges, [])
  complete({ ...h.result(), physical: { ...h.result().physical, queued: 1 } })
  await pending
  assert.equal(h.api.state.value, null)
  assert.equal(h.calls.filter(row => row[0] === 'step').length, 0)
})


test('an executing physical move keeps 2D on observed cells until the robot completes', async t => {
 const h=harness(t)
 const before=snapshot('ttt',{cells:[],history:[]})
 const future=snapshot('ttt',{cells:[{r:1,c:1,piece:{code:'O',side:'robot'}}],history:['robot O']})
 h.api.state.value=before;h.api.display.value=before;h.api.executing.value=true
 h.api.apply({ok:true,state:future,physical:{...h.result().physical,physical_pending:true,actual_state:before,confirmed_state:before}})
 assert.deepEqual(h.api.boardState.value.cells,[])
 assert.deepEqual(h.api.historyText.value,'')
})
test('failed response consumes physical truth even when ok is false and never caches an unexecuted AI move',async t=>{
 const h=harness(t)
 const before=snapshot('ttt',{cells:[],history:[]})
 const future=snapshot('ttt',{cells:[{r:1,c:1,piece:{code:'O',side:'robot'}}],history:['robot O'],last_move:{to:[1,1]}})
 h.api.apply({ok:false,why:'实体落子预检失败 第19行 bishop / end_link -0.05mm',state:future,
  physical:{...h.result().physical,active:false,lifecycle:'failed',physical_pending:true,actual_state:{...future,cells:[]},confirmed_state:before}})
 assert.equal(h.api.failed.value,true)
 assert.deepEqual(h.api.boardState.value.cells,[])
 assert.equal(h.api.historyText.value,'')
 assert.deepEqual(h.api.cache.get('ttt').cells,[])
 assert.equal(h.api.cache.get('ttt').history.length,0)
 assert.equal(h.api.playable.value,false)
 assert.match(h.api.statusLine.value,/未完成.*重新开始/)
 assert.match(source,/v-if="error && !failed"/,'physical failures appear once in the concise status line')
})
test('loading a failed game keeps the observed board and confirmed history instead of rule-engine future history',async t=>{
 const before=snapshot('chess',{drop:false,cells:[{r:0,c:0,piece:{code:'r',side:'robot'}}],history:['confirmed human move']})
 const future=snapshot('chess',{drop:false,cells:[{r:0,c:1,piece:{code:'r',side:'robot'}}],history:['confirmed human move','ra8-b8'],last_move:{from:[0,0],to:[0,1]}})
 const physical={active:false,paused:false,game:'chess',session_id:'chess-session',lifecycle:'failed',physical_pending:true,actual_state:{...future,cells:before.cells},confirmed_state:before}
 const h=harness(t,{gameState:async()=>({ok:true,state:future,physical}),gamePhysical:async()=>({ok:true,physical})},'chess')
 await h.api.load();await flush()
 assert.deepEqual(h.api.boardState.value.cells,before.cells)
 assert.equal(h.api.historyText.value,'confirmed human move')
 assert.deepEqual(h.api.cache.get('chess').cells,before.cells)
 assert.deepEqual(h.viewChanges,[])
})


test('human intent renders immediately, while an automatic AI reply is absent until physical completion',async t=>{
 const before=snapshot()
 const human=snapshot('ttt',{cells:[{r:1,c:1,piece:{code:'X',side:'human'}}],history:['human X']})
 const future=snapshot('ttt',{cells:[...human.cells,{r:0,c:0,piece:{code:'O',side:'robot'}}],history:['human X','robot O']})
 let finishRobot,steps=0
 const h=harness(t,{
  gameMove:async()=>({ok:true,state:human,physical:{game:'ttt',active:true,session_id:'ttt-session',queued:1,physical_pending:true,actual_state:before,confirmed_state:before}}),
  gameStep:async()=>++steps===1?{ok:true,running:true,state:future,action:{items:[{to:[0,0],code:'O',side:'robot'}]}}:{ok:true,done:true,state:future},
 },'ttt',{robotPlayRequest:()=>new Promise(resolve=>{finishRobot=resolve})})
 const pending=h.api.play({to:[1,1]})
 for(let i=0;i<6;i++)await flush()
 assert.equal(h.api.executing.value,true)
 assert.deepEqual(h.api.boardState.value.cells,human.cells,'user intention stays immediate')
 assert.equal(h.api.historyText.value,'human X','planned computer move is not a completed history entry')
 finishRobot({active:false,planning:false,execution:{state:'finished'}})
 await pending
 assert.deepEqual(h.api.boardState.value.cells,future.cells)
 assert.equal(h.api.historyText.value,'human X · robot O')
 assert.deepEqual(h.viewChanges,[])
})
test('confirming a physically completed user-intent destination never duplicates its piece',t=>{
 const h=harness(t,{},'chess')
 const intended=snapshot('chess',{drop:false,cells:[{r:2,c:2,piece:{code:'R',side:'human'}}]})
 h.api.display.value=intended
 h.api.applyAction({items:[{from:[2,1],to:[2,2],code:'R',side:'human'}]})
 assert.equal(h.api.boardState.value.cells.length,1)
 assert.deepEqual(h.api.boardState.value.cells[0],{r:2,c:2,piece:{code:'R',side:'human',glyph:'♖'}})
})


test('game display rounds error text and scores without changing source values',t=>{
 const h=harness(t)
 h.api.error.value='距离 -0.054567 mm'
 assert.equal(h.api.displayNumericText(h.api.error.value),'距离 -0.05 mm')
 assert.equal(h.api.error.value,'距离 -0.054567 mm')
 assert.equal(h.api.displayNumber(8.12345),'8.12')
 assert.match(source,/displayNumber\(boardState.score.black\)/)
 assert.match(source,/displayNumber\(boardState.score.white\)/)
})
