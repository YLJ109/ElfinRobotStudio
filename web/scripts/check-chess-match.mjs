import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive } from 'vue'
import { createChessMatch, chessMove } from '../src/chessMatch.js'
import { gameResult } from '../src/functionPlay.js'

// Entirely offline: transport acknowledgements and independently delivered world snapshots.
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }

test('pending preflight reports real progress and cancellation discards late poll without running', async () => {
  const planning = deferred(), reading = deferred()
  let polls = 0
  const f = fixture({ transport: action => {
    if (action === 'generate') return planning.promise
    if (action === 'state' && f.match.state.phase === 'planning') {
      if (++polls === 1) return { ok: true, planning: true, progress: { step: 5, total: 21, line: 8 }, elapsed_s: 9.5, timeout_s: 90 }
      return reading.promise
    }
    return null
  } })
  await f.match.start(); const move = f.match.play(4); await flush()
  await f.tick()
  assert.deepEqual({...f.match.state.planningProgress},{step:5,total:21,line:8})
  assert.equal(f.match.state.planningElapsed,9.5);assert.equal(f.match.state.planningTimeout,90)
  assert.equal(f.calls.filter(c=>c.action==='run').length,0)
  await f.tick();f.match.cancel();await move
  reading.resolve({ok:true,progress:{step:21,total:21},elapsed_s:40});planning.resolve({ok:true})
  await flush();assert.equal(f.match.state.phase,'cancelled')
  assert.equal(f.match.state.planningProgress.step,5);assert.equal(f.calls.filter(c=>c.action==='run').length,0)
  assert.equal(f.timers.length,0)
})
test('preflight state failure is visible and cannot authorize a move or restart planning', async () => {
 const gate=deferred()
 const f=fixture({transport:action=>{
  if(action==='generate')return gate.promise
  if(action==='state'&&f.match.state.phase==='planning')throw Error('状态读取超时')
  return null
 }})
 await f.match.start();const move=f.match.play(4);await f.tick()
 assert.match(f.match.state.planningReadError,/状态读取超时/)
 assert.equal(f.match.state.planningProgress,null);assert.equal(f.match.state.phase,'planning')
 assert.equal(f.calls.filter(c=>c.action==='generate').length,1);assert.equal(f.calls.filter(c=>c.action==='run').length,0)
 f.match.cancel();await move;gate.resolve({ok:true});await flush();assert.equal(f.timers.length,0)
})
function fixture(options = {}) {
  const world = reactive({ connected: true, snap: { session_id: 's1', mode: 'sim', real: null,
    moving: false, exec: { state: 'idle', queue_state: 'idle', execution: { epoch: 7, busy: false, state: 'idle', started: 0 } }, hand_follow: { active: false }, objects: [
      ...['zone_a', 'zone_b'].map((role, i) => ({ id: role, task: { mode: 'chess', group: 'g1', role, slot: -1, home: -i-1 } })),
      ...Array.from({ length: 9 }, (_, i) => ({ id: 'piece' + i, xyz: [100, 200, 10],
        task: { mode: 'chess', group: 'g1', role: 'piece', slot: -1, home: i, symbol: i < 5 ? 'O' : 'X' } }))
    ] } })
  const calls = [], timers = []
  let current = null, running = false, polls = 0
  const finished = () => ({ ok: true, active: false, planning: false, held: null, execution: { state: 'finished' } })
  function snapshot() {
    const piece = world.snap.objects.find(item => item.id === current.id)
    piece.task.slot = current.slot; piece.xyz = [400, 500, 20]
    world.snap.moving = false; world.snap.exec.state = 'finished'
    Object.assign(world.snap.exec.execution, { busy: false, state: 'completed' })
  }
  const request = async (action, payload, config) => {
    calls.push({ action, payload, config })
    if (options.transport) { const value = await options.transport(action, payload, config); if (value) return value }
    if (action === 'stop') { running = false; return { ok: true } }
    if (action === 'generate') {
      const piece = world.snap.objects.find(item => item.task.role === 'piece' && item.task.symbol === payload.symbol && item.task.slot === -1)
      current = { ...payload, id: piece.id }
      return { ok: true, plan_id: 'plan' + calls.length, group: payload.group, action: 'chess', slot: payload.slot,
        cycles: 1, points: [], source: `SIM_PICK ${piece.id}\nSIM_PLACE ${piece.id}` }
    }
    if (action === 'run') { running = true; world.snap.moving = true; world.snap.exec.state = 'running'; Object.assign(world.snap.exec.execution, { busy: true, state: 'running', started: world.snap.exec.execution.started + 1 }); return { ok: true } }
    if (action === 'state') {
      if (!running) return { ok: true, active: false, planning: false, held: null, execution: { state: 'idle' } }
      polls++
      if (options.poll) return options.poll({ polls, snapshot, finished, current, world })
      snapshot(); running = false; return finished()
    }
    throw Error('unexpected action ' + action)
  }
  const match = createChessMatch(world, { request, random: () => options.random ?? 0,
    schedule: fn => { timers.push(fn); return fn }, clear: fn => { const i = timers.indexOf(fn); if (i >= 0) timers.splice(i, 1) },
    maxPolls: options.maxPolls ?? 5 })
  const tick = async () => { await flush(); timers.shift()?.(); await flush() }
  return { match, world, calls, timers, tick, snapshot }
}

test('human O starts, each side gets a separate physical plan, O always precedes X', async () => {
  const { match, calls, world } = fixture()
  assert.equal(await match.play(4), false)
  assert.equal(await match.start(), true); assert.equal(match.state.human, 'O')
  assert.equal(calls.filter(c => c.action === 'generate').length, 0)
  assert.equal(await match.play(4), true)
  assert.deepEqual(calls.filter(c => c.action === 'generate').map(c => c.payload.symbol), ['O', 'X'])
  assert.equal(calls.filter(c => c.action === 'run').length, 2)
  assert.equal(match.state.board[4], 'O'); assert.equal(match.state.board.filter(Boolean).length, 2)
  assert.equal(match.state.phase, 'human'); assert.equal(match.canPlay(4), false)
  assert.equal(world.snap.objects.filter(p => p.task.slot >= 0).length, 2)
})

test('AI O opens automatically; human X is unlocked only after O snapshot and finish', async () => {
  const f = fixture({ random: .99 })
  const start = f.match.start()
  assert.equal(f.match.canPlay(0), false)
  assert.equal(await start, true)
  assert.equal(f.match.state.human, 'X'); assert.equal(f.match.state.board[4], 'O')
  assert.equal(f.match.canPlay(0), true)
  await f.match.play(0)
  assert.deepEqual(f.calls.filter(c => c.action === 'generate').map(c => c.payload.symbol), ['O', 'X', 'O'])
})

test('finished response alone does not commit, moving/paused lock and no duplicate submission', async () => {
  const f = fixture({ poll: ({ polls, snapshot, finished, world }) => {
    if (polls === 1) return finished() // WS snapshot still running; not a completed move.
    if (polls === 2) { snapshot(); world.snap.exec.state = 'paused'; return { ...finished(), active: true, execution: { state: 'paused' } } }
    snapshot(); return finished()
  } })
  await f.match.start()
  const move = f.match.play(0)
  assert.equal(await f.match.play(1), false)
  await flush(); assert.equal(f.match.state.board[0], null); assert.equal(f.match.canPlay(1), false)
  await f.tick(); assert.equal(f.match.state.board[0], null)
  await f.tick(); await move
  assert.equal(f.match.state.board[0], 'O')
  assert.equal(f.calls.filter(c => c.action === 'run').length, 2)
})

test('slot snapshot alone cannot commit before execution finished', async () => {
  const f = fixture({ poll: ({ polls, snapshot, finished }) => {
    snapshot()
    return polls === 1 ? { ...finished(), active: true, held: 'piece0', execution: { state: 'running' } } : finished()
  } })
  await f.match.start(); const move = f.match.play(0); await flush()
  assert.equal(f.world.snap.objects.find(p => p.id === 'piece0').task.slot, 0)
  assert.equal(f.match.state.board[0], null)
  await f.tick(); await move
  assert.equal(f.match.state.phase, 'human')
})

test('cancellation while planning invalidates late response and never runs a plan', async () => {
  const gate = deferred()
  const f = fixture({ transport: action => action === 'generate' ? gate.promise : null })
  await f.match.start(); const move = f.match.play(0); f.match.cancel()
  gate.resolve({ ok: true, plan_id: 'late' }); await move
  assert.equal(f.match.state.phase, 'cancelled'); assert.equal(f.match.state.board.filter(Boolean).length, 0)
  assert.equal(f.calls.some(c => c.action === 'run'), false)
  assert.equal(f.calls.filter(c => c.action === 'stop').length, 1)
})

test('cancel in flight leaves committed board untouched and suppresses AI', async () => {
  const gate = deferred()
  const f = fixture({ transport: action => action === 'run' ? gate.promise : null })
  await f.match.start(); const move = f.match.play(0); await flush(); f.match.cancel()
  gate.resolve({ ok: true }); await move
  assert.equal(f.match.state.phase, 'cancelled')
  assert.equal(f.match.state.board.filter(Boolean).length, 0)
  assert.equal(f.calls.filter(c => c.action === 'generate').length, 1)
})

test('execution errors, cancelled execution and missing snapshots never become completed games', async () => {
  for (const state of ['error', 'aborted', 'finished']) {
    const f = fixture({ maxPolls: 2, poll: ({ finished }) => ({ ...finished(), execution: { state } }) })
    await f.match.start(); const move = f.match.play(0); await f.tick(); await f.tick(); await move
    assert.equal(f.match.state.phase, 'error'); assert.equal(f.match.state.board.filter(Boolean).length, 0)
    assert.equal(f.calls.filter(c => c.action === 'run').length, 1)
  }
})

test('session, mode, connection, follow or task switch invalidates late work without stopping another session/real mode', async () => {
  for (const change of [w => { w.snap.session_id = 's2' }, w => { w.snap.mode = 'real' },
    w => { w.snap.real = {} }, w => { w.connected = false }, w => { w.snap.hand_follow.active = true },
    w => { w.snap.objects = [] }]) {
    const gate = deferred(), f = fixture({ transport: action => action === 'generate' ? gate.promise : null })
    await f.match.start(); const move = f.match.play(0); change(f.world); f.match.observe()
    gate.resolve({ ok: true }); await move
    assert.equal(f.match.state.phase, 'cancelled')
    assert.equal(f.calls.some(c => ['run', 'stop'].includes(c.action)), false)
    assert.equal(f.match.state.board.filter(Boolean).length, 0)
  }
})

test('rejects legacy two-piece plans, wrong piece, transport failures and external chess changes', async () => {
  for (const kind of ['legacy', 'wrong', 'network', 'changed']) {
    const f = fixture({ transport: (action, payload) => {
      if (action !== 'generate') return null
      if (kind === 'network') throw Error('offline failure')
      if (kind === 'changed') f.world.snap.objects.find(p => p.id === 'piece1').task.slot = 2
      return { ok: true, plan_id: 'p', group: 'g1', action: 'chess', slot: payload.slot,
        cycles: kind === 'legacy' ? 2 : 1, source: kind === 'wrong' ? 'SIM_PICK piece5\nSIM_PLACE piece5' : 'SIM_PICK piece0\nSIM_PLACE piece0' }
    } })
    await f.match.start(); assert.equal(await f.match.play(0), false)
    assert.equal(f.match.state.phase, 'error'); assert.equal(f.calls.some(c => c.action === 'run'), false)
  }
})

test('requires an empty 5 O / 4 X scene; busy conditions never send requests', async () => {
  for (const patch of [{ mode: 'real' }, { moving: true }, { exec: { state: 'paused' } }, { real: {} }]) {
    const f = fixture(); Object.assign(f.world.snap, patch)
    assert.equal(await f.match.start(), false); assert.equal(f.calls.length, 0)
  }
  const f = fixture(); f.world.snap.objects[2].task.slot = 0
  assert.equal(await f.match.start(), false); assert.equal(f.calls.length, 0)
  f.world.snap.objects = []; assert.equal(await f.match.start(), false)
})

test('AI minimax works as O or X without overwriting and stops on wins/draw', () => {
  for (const ai of ['O', 'X']) {
    const human = ai === 'O' ? 'X' : 'O'
    let leaves = 0
    function visit(board, turn) {
      const result = gameResult(board)
      if (result) { assert.notEqual(result, human); leaves++; return }
      const choices = turn === ai ? [chessMove(board, ai)] : board.flatMap((v, i) => v ? [] : [i])
      for (const slot of choices) { assert.equal(board[slot], null); const next = board.slice(); next[slot] = turn; visit(next, turn === 'O' ? 'X' : 'O') }
    }
    visit(Array(9).fill(null), 'O'); assert.ok(leaves > 10)
    assert.equal(chessMove(['O', 'O', 'O', null, null, null, null, null, null], ai), null)
  }
})

test('complete matches for both assignments stop at terminal outcome, no extra robot move', async () => {
  for (const random of [0, .99]) {
    const f = fixture({ random })
    await f.match.start()
    for (let turns = 0; f.match.state.phase === 'human' && turns < 5; turns++) {
      const slot = f.match.state.board.findIndex(cell => !cell)
      await f.match.play(slot)
    }
    assert.equal(f.match.state.phase, 'finished')
    const result = gameResult(f.match.state.board)
    assert.ok(result); assert.notEqual(result, f.match.state.human)
    const count = f.calls.length
    assert.equal(await f.match.play(0), false); assert.equal(f.calls.length, count)
    assert.equal(f.calls.filter(c => c.action === 'run').length, f.match.state.board.filter(Boolean).length)
    assert.equal(await f.match.start(), false, 'occupied physical board must be reset before another game')
  }
})

test('duplicate starts and pending stop cannot launch another game or cancel its future program', async () => {
  const planning = deferred(), stopping = deferred()
  const f = fixture({ transport: action => action === 'generate' ? planning.promise : action === 'stop' ? stopping.promise : null })
  await f.match.start()
  assert.equal(await f.match.start(), false)
  const move = f.match.play(0); f.match.cancel()
  assert.equal(f.match.busy(), true)
  assert.equal(await f.match.start(), false)
  f.match.cancel(); assert.equal(f.calls.filter(c => c.action === 'stop').length, 1)
  planning.resolve({ ok: true }); stopping.resolve({ ok: true }); await move; await flush()
  assert.equal(f.match.busy(), true, 'stop ACK alone does not clear uncertainty')
  assert.equal(await f.match.verifyIdle(), true)
  assert.equal(f.match.busy(), false)
  assert.equal(await f.match.start(), true)
})

test('external piece-slot mutation during execution invalidates match, never commits expected move', async () => {
  const f = fixture({ poll: ({ snapshot, finished, world }) => {
    snapshot(); world.snap.objects.find(p => p.id === 'piece1').task.slot = 2; return finished()
  } })
  await f.match.start(); await f.match.play(0)
  assert.equal(f.match.state.phase, 'error'); assert.equal(f.match.state.board[0], null)
  assert.equal(f.calls.filter(c => c.action === 'run').length, 1)
})

test('World queue_state and nested execution busy/preflighting prevent new work', async () => {
  for (const patch of [{ queue_state: 'running' }, { queue_state: 'paused' },
    { execution: { epoch: 7, state: 'preflighting', busy: false } },
    { execution: { epoch: 7, state: 'idle', busy: true } }]) {
    const f = fixture(); Object.assign(f.world.snap.exec, patch)
    assert.equal(await f.match.start(), false); assert.equal(f.calls.length, 0)
  }
  for (const status of [{ phase: 'preflighting' }, { execution: { state: 'preflighting' } },
    { execution: { state: 'idle', queue_state: 'running' } }]) {
    const f = fixture({ transport: action => action === 'state' ? { ok: true, ...status } : null })
    assert.equal(await f.match.start(), false)
    assert.equal(f.match.state.phase, 'error'); assert.equal(f.calls.some(c => c.action === 'generate'), false)
  }
})

test('finished runner does not commit until World queue and executor both settle; old aborted queue is terminal', async () => {
  const f = fixture({ poll: ({ polls, snapshot, finished, world }) => {
    snapshot()
    world.snap.exec.queue_state = polls === 1 ? 'running' : 'aborted'
    Object.assign(world.snap.exec.execution, { busy: polls === 2, state: polls === 2 ? 'running' : 'completed' })
    return finished()
  } })
  await f.match.start(); const move = f.match.play(0); await flush()
  assert.equal(f.match.state.board[0], null)
  await f.tick(); assert.equal(f.match.state.board[0], null)
  await f.tick(); await move
  assert.equal(f.match.state.phase, 'human'); assert.equal(f.match.state.board[0], 'O')
})

test('preflight timeout releases local waiter but requires explicit idle readback; no retry or resume', async () => {
  let preflighting = false
  const f = fixture({ transport: action => {
    if (action === 'generate') { preflighting = true; throw new DOMException('planner timeout', 'TimeoutError') }
    if (action === 'state' && preflighting) return { ok: true, phase: 'preflighting', planning: true }
    return null
  } })
  await f.match.start(); assert.equal(await f.match.play(0), false)
  assert.equal(f.match.state.phase, 'error'); assert.equal(f.match.state.recoveryRequired, true)
  const count = f.calls.length
  assert.equal(await f.match.start(), false); assert.equal(await f.match.play(1), false)
  assert.equal(f.calls.length, count)
  assert.equal(await f.match.verifyIdle(), false); assert.equal(f.match.busy(), true)
  preflighting = false
  assert.equal(await f.match.verifyIdle(), true); assert.equal(f.match.busy(), false)
  assert.equal(f.match.state.phase, 'error', 'idle readback cannot resume the match')
  assert.equal(f.calls.filter(c => c.action === 'generate').length, 1)
  assert.equal(f.calls.some(c => c.action === 'run'), false)
})

test('transport ignoring abort cannot hold local turn or let late plan hijack the next match', async () => {
  const gate = deferred()
  const f = fixture({ transport: action => action === 'generate' ? gate.promise : null })
  await f.match.start(); const move = f.match.play(0)
  f.match.cancel(); await flush()
  assert.equal(await move, false, 'cancel settles without waiting for the uncooperative transport')
  await f.match.verifyIdle(); assert.equal(await f.match.start(), true)
  gate.resolve({ ok: true, plan_id: 'old-plan', cycles: 1, group: 'g1', action: 'chess', slot: 0,
    source: 'SIM_PICK piece0\nSIM_PLACE piece0' })
  await flush()
  assert.equal(f.match.state.phase, 'human'); assert.equal(f.match.state.board.filter(Boolean).length, 0)
  assert.equal(f.calls.some(c => c.action === 'run'), false)
})

test('World execution epoch revocation invalidates plan without stopping the replacement owner', async () => {
  const gate = deferred(), f = fixture({ transport: action => action === 'generate' ? gate.promise : null })
  await f.match.start(); const move = f.match.play(0)
  f.world.snap.exec.execution.epoch++
  f.match.observe(); await move
  gate.resolve({ ok: true }); await flush()
  assert.equal(f.match.state.phase, 'cancelled'); assert.equal(f.match.state.recoveryRequired, true)
  assert.equal(f.calls.some(c => ['run', 'stop'].includes(c.action)), false)
  assert.equal(await f.match.verifyIdle(), true)
  assert.equal(f.match.state.phase, 'cancelled')
})

test('nested executor failed is terminal even when runner and slots say finished', async () => {
  const f = fixture({ poll: ({ snapshot, finished, world }) => {
    snapshot(); world.snap.exec.execution.state = 'failed'; return finished()
  } })
  await f.match.start(); await f.match.play(0)
  assert.equal(f.match.state.phase, 'error'); assert.equal(f.match.state.board[0], null)
  assert.equal(f.match.state.recoveryRequired, true)
})

test('stale failure snapshot from before run ACK cannot fail the new turn', async () => {
  let firstRun = true
  const f = fixture({ transport: action => {
    if (action === 'run' && firstRun) {
      firstRun = false
      // ACK precedes the next World snapshot; this is still the old executor.
      f.world.snap.exec.state='aborted';f.world.snap.exec.execution.state='failed'
      return {ok:true}
    }
    if (action === 'state' && !firstRun && f.match.state.phase==='moving' && f.match.state.board.every(v=>!v)) {
      return {ok:true,active:true,planning:false,held:null,execution:{state:'running'}}
    }
    return null
  } })
  await f.match.start(); const move=f.match.play(0); await flush()
  assert.equal(f.match.state.phase,'moving');assert.equal(f.match.state.board[0],null)
  f.match.cancel();assert.equal(await move,false)
})
