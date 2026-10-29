import { reactive } from 'vue'
import { gameResult, robotMove, scenePlay, playAllowed } from './functionPlay.js'
import { robotPlayRequest } from './robotPlayProgram.js'

const other = symbol => symbol === 'O' ? 'X' : 'O'
const activeStates = ['running', 'waiting', 'paused', 'preflighting', 'planning']
function executionBusy(exec) {
  return activeStates.includes(exec?.state) || activeStates.includes(exec?.phase)
    || activeStates.includes(exec?.queue_state) || exec?.execution?.busy === true
    || activeStates.includes(exec?.execution?.state) || activeStates.includes(exec?.execution?.phase)
}
function serverBusy(status) {
  return status.active || status.planning || status.held || activeStates.includes(status.phase) || executionBusy(status.execution)
}
export function chessMove(board, symbol) {
  // The existing minimax maximizes O; swapping labels also lets it play X.
  return robotMove(symbol === 'O' ? board : board.map(cell => cell ? other(cell) : null))
}

export function createChessMatch(world, { request = robotPlayRequest, random = Math.random,
  blocked = () => false, schedule = setTimeout, clear = clearTimeout, maxPolls = 900 } = {}) {
  const state = reactive({ phase: 'idle', board: Array(9).fill(null), human: '', ai: '',
    turn: 'O', group: '', session: '', motionEpoch: null, pending: null, stopping: false,
    verifying: false, recoveryRequired: false, message: '请先布置三维棋局，再启动仿真自动取放。' })
  let epoch = 0, abort = null, timer = null, wake = null, submitted = false, planningUncertain = false
  const scene = () => scenePlay(world.snap?.objects || [], 'chess')
  const context = () => world.connected && world.snap?.mode === 'sim' && !world.snap.real
    && !world.snap.hand_follow?.active && world.snap.session_id === state.session && scene().group === state.group
  const stationary = () => playAllowed(world) && !executionBusy(world.snap?.exec)
  const busy = () => state.stopping || state.verifying || state.recoveryRequired || ['checking', 'planning', 'submitting', 'moving'].includes(state.phase)
  const canPlay = index => state.phase === 'human' && state.turn === state.human && !blocked()
    && stationary() && !state.recoveryRequired && context() && Number.isInteger(index) && index >= 0 && index < 9
    && !state.board[index] && !gameResult(state.board) && !scene().error
  function cancel(message = '棋局已取消；已确认棋步保留，请检查三维场景后重新布置。') {
    const ownedWork = submitted || planningUncertain
    const stop = !state.stopping && ownedWork && context() && !epochChanged()
    if (ownedWork) state.recoveryRequired = true
    const token = ++epoch; abort?.abort(); clear(timer); wake?.(); wake = null; submitted = false; planningUncertain = false
    if (state.phase !== 'idle') { state.phase = 'cancelled'; state.pending = null; state.message = message }
    // Never stop a new session, and never switch the mode or reconnect real hardware.
    if (stop) {
      state.stopping = true
      void request('stop', {}).then(value => {
        if (value?.ok !== true) throw Error(value?.why || '停止请求失败')
      }).catch(error => { if (token === epoch) state.message += ' 停止未确认：' + error.message })
        .finally(() => { state.stopping = false })
    }
  }
  function observe() {
    if (!['idle', 'cancelled', 'error', 'finished'].includes(state.phase) && (!context() || epochChanged()))
      cancel('会话、模式或任务组已变化，本局作废；不会自动恢复或连接真机。')
  }
  const epochChanged = () => Number.isInteger(state.motionEpoch)
    && world.snap?.exec?.execution?.epoch !== state.motionEpoch
  const valid = token => {
    if (token !== epoch || abort?.signal.aborted) throw Error('棋局已取消')
    if (!context()) throw Error('仿真会话或任务组已变化')
    if (epochChanged()) throw Error('执行器 epoch 已变化，本局作废，请检查停止状态')
    if (scene().error) throw Error(scene().error)
  }
  async function call(action, payload, token) {
    valid(token)
    // A transport may deliver a response after cancellation. Settle the local
    // waiter immediately and discard that response without owning another turn.
    const signal = abort.signal
    let aborted
    const cancelled = new Promise((_, reject) => {
      aborted = () => reject(new DOMException('棋局已取消', 'AbortError'))
      signal.addEventListener('abort', aborted, { once: true })
    })
    let value
    try { value = await Promise.race([request(action, payload, { signal }), cancelled]) }
    finally { signal.removeEventListener('abort', aborted) }
    valid(token)
    if (value?.ok !== true) throw Error(value?.why || '取放请求未确认')
    return value
  }
  const delay = () => new Promise(resolve => { wake = resolve; timer = schedule(() => { wake = null; resolve() }, 700) })
  function sameBoard(a, b) { return a.every((cell, index) => cell === b[index]) }
  async function move(slot, token) {
    valid(token)
    if (!stationary() || blocked()) throw Error('机器人或队列忙碌，不能规划棋步')
    const before = scene()
    const initialPieces = before.pieces.map(({ id, task }) => ({ id, slot: task.slot, symbol: task.symbol }))
    if (!sameBoard(before.board, state.board)) throw Error('棋盘被外部改变，请重新布置')
    const symbol = state.turn, expected = state.board.slice()
    expected[slot] = symbol
    state.pending = { slot, symbol }; state.phase = 'planning'
    state.message = `正在预检 ${symbol} → 第 ${slot + 1} 格，随后自动仿真取放…`
    planningUncertain = true
    const plan = await call('generate', { group: state.group, action: 'chess', symbol, slot, clearance: 120, speed: 15 }, token)
    planningUncertain = false
    const picks = [...(plan.source || '').matchAll(/^\s*SIM_PICK\s+(\S+)\s*$/gm)].map(match => match[1])
    const places = [...(plan.source || '').matchAll(/^\s*SIM_PLACE\s+(\S+)\s*$/gm)].map(match => match[1])
    const piece = initialPieces.find(item => item.id === picks[0])
    if (!plan.plan_id || plan.cycles !== 1 || picks.length !== 1 || places.length !== 1
      || picks[0] !== places[0] || !piece || piece.symbol !== symbol || piece.slot !== -1
      || plan.group !== state.group || plan.action !== 'chess' || plan.slot !== slot)
      throw Error('后端必须支持 symbol 指定的单枚棋步（cycles=1）；未执行不匹配或双枚计划')
    if (!stationary() || blocked() || !sameBoard(scene().board, state.board)) throw Error('预检后场景或运行条件变化')
    state.phase = 'submitting'; submitted = true
    const previousExecutorStart = world.snap.exec?.execution?.started
    await call('run', { plan_id: plan.plan_id, source: plan.source }, token)
    state.phase = 'moving'; state.message = `机器人正在搬运 ${symbol}，等待执行完成及场景槽位确认…`
    for (let poll = 0; poll < maxPolls; poll++) {
      const status = await call('state', undefined, token), execution = status.execution?.state
      if (['aborted', 'error'].includes(execution) || (world.snap.exec?.execution?.state === 'failed'
        && world.snap.exec.execution.started !== previousExecutorStart))
        throw Error(status.execution?.error || '取放被取消或执行失败')
      const actual = scene(), placed = actual.pieces.find(item => item.id === piece.id)
      const slotsUnchanged = initialPieces.every(old => old.id === piece.id ||
        actual.pieces.some(item => item.id === old.id && item.task.slot === old.slot && item.task.symbol === old.symbol))
      if (!slotsUnchanged || actual.pieces.length !== initialPieces.length) throw Error('其他棋子已变化，本局停止')
      if (execution === 'finished' && !serverBusy(status)
        && world.snap.exec?.state === 'finished' && stationary()
        && !['failed','cancelled'].includes(world.snap.exec?.execution?.state)
        && placed?.task.slot === slot && placed.task.symbol === symbol && sameBoard(actual.board, expected)) {
        state.board = expected; state.pending = null; submitted = false; state.turn = other(symbol)
        return
      }
      await delay(); valid(token)
    }
    throw Error('等待执行完成或场景槽位同步超时；不重复提交棋步，请检查场景')
  }
  async function advance(slot, token) {
    try {
      if (slot !== undefined) await move(slot, token)
      if (!gameResult(state.board) && state.turn === state.ai) await move(chessMove(state.board, state.ai), token)
      valid(token)
      const result = gameResult(state.board)
      state.phase = result ? 'finished' : 'human'
      state.message = result === 'draw' ? '平局' : result ? `${result === state.human ? '你' : 'AI'}获胜` : `轮到你（${state.human}），点击空格后机器人自动取放双方棋子。`
      return true
    } catch (error) {
      if (token === epoch) {
        if (submitted || planningUncertain) state.recoveryRequired = true
        state.phase = 'error'; state.pending = null
        state.message = error.message + (submitted ? '；执行结果未确认，请勿重复落子，检查场景后重新布置。' : '')
      }
      return false
    }
  }
  async function start() {
    if (busy() || state.phase === 'human' || blocked() || !stationary()) return false
    const current = scene()
    if (!current.group || current.error || current.board.some(Boolean) || current.pieces.length !== 9
      || current.pieces.filter(item => item.task.symbol === 'O').length !== 5
      || current.pieces.filter(item => item.task.symbol === 'X').length !== 4) {
      state.message = current.error || '请先布置完整空棋局（5圈4叉）；已有棋步需先回 A 区。'; return false
    }
    const token = ++epoch
    abort = new AbortController(); submitted = false
    Object.assign(state, { phase: 'checking', group: current.group, session: world.snap.session_id,
      motionEpoch: world.snap.exec?.execution?.epoch ?? null,
      human: random() < .5 ? 'O' : 'X', turn: 'O', board: current.board.slice(), pending: null })
    state.ai = other(state.human)
    state.message = `你执 ${state.human}，AI 执 ${state.ai}；O 固定先行。正在检查仿真状态…`
    try {
      const status = await call('state', undefined, token)
      if (serverBusy(status))
        throw Error('服务端仍有运行、规划或持件，请先处理再开局')
    } catch (error) {
      if (token === epoch) { state.phase = 'error'; state.message = error.message }
      return false
    }
    return advance(undefined, token)
  }
  async function verifyIdle() {
    if (state.verifying || state.stopping || ['checking','planning','submitting','moving','human'].includes(state.phase)
      || !stationary()) return false
    const session = world.snap.session_id, token = epoch
    state.verifying = true
    try {
      const status = await request('state', undefined)
      if (token !== epoch || session !== world.snap?.session_id || !stationary()) return false
      if (status?.ok !== true || serverBusy(status)) throw Error('服务端仍在预检、执行或持件，不能解除锁定')
      state.recoveryRequired = false; submitted = false; planningUncertain = false
      state.message = '已确认仿真与队列静止；本局不续行，请检查棋子并回 A 区或重新布置后开局。'
      return true
    } catch (error) { if (token === epoch) state.message = '状态确认失败：' + error.message; return false }
    finally { state.verifying = false }
  }
  function play(slot) { return canPlay(slot) ? advance(slot, epoch) : Promise.resolve(false) }
  return { state, start, play, cancel, observe, canPlay, busy, verifyIdle }
}
