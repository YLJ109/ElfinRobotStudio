import { reactive, watch, effectScope } from 'vue'

export const validTarget = value => Array.isArray(value) && value.length === 6 && Array.from(value).every(Number.isFinite)
export const sameTarget = (a, b) => validTarget(a) && validTarget(b) && a.every((v, i) => Math.abs(v - b[i]) < .001)
export function targetContext(world, frame = world.snap) {
  const tool = value => value ? [value.mounted?.tool_id, value.mounted?.revision, value.tool?.tcp, value.visual] : null
  return JSON.stringify([world.connected, world.stateFresh !== false, frame?.mode, !!frame?.real, frame?.session_id,
    frame?.config_rev, frame?.tcp_frame, tool(frame?.tools), tool(world.tools)])
}
export const createTargetState = () => ({ revision: 0, stage: 'idle', context: '', source: '', joints: null, planId: null,
  pose: null, reason: '尚无目标，请先预览。', pending: false, acknowledged: false, executing: false,
  executionOutcome: 'idle', executionMessage: '', arrivalBinding: null, controlPending: '', controlMessage: '', motionStatus: null,
  failure: null })
export const TARGET_LABELS = Object.freeze({ idle:'待预览', solving: '求解中', waiting: '等待预览', ready: '可执行', invalid: '已失效', executing: '执行中', failed: '失败' })
const controllers = new WeakMap()
const executionTerminals = new Set(['failed', 'aborted', 'stopped', 'cancelled'])
const REACHED_REASON='已到达目标，请重新预览后再提交运动。'
const executionSnapshot = status => status ? { epoch: status.epoch, started: status.started,
  state: status.state, action: status.action, error: status.error, busy: status.busy } : null
const executionSignature = status => JSON.stringify(executionSnapshot(status))

function terminalBelongsToRequest(request, status, observedMoving) {
  const before = request.beforeExecution, accepted = request.acceptedExecution
  const expected = accepted || before
  if (Number.isFinite(expected?.epoch) && Number.isFinite(status?.epoch)) {
    if (status.epoch < expected.epoch) return false
    // MotionExecutor.revoke increments epoch; submit reuses epoch and changes started.
    if (status.epoch > expected.epoch) return true
    if (Number.isFinite(expected.started) && status.started !== expected.started) return false
    if (accepted && (!executionTerminals.has(accepted.state)
        || executionSignature(accepted) !== executionSignature(before))) return true
  }
  if (Number.isFinite(accepted?.started) && Number.isFinite(status?.started)
      && status.started !== accepted.started) return false
  // Legacy replies may lack executor identity. An unchanged historical terminal
  // is not evidence about a new request, even in a freshly received snapshot.
  return observedMoving || executionSignature(status) !== executionSignature(before)
}

// One store owns one session-local target. Neither authority nor transport work
// is persisted. All actions resolve explicit results, so fire-and-forget callers
// still get a visible failure without an unhandled rejection.
export function targetFor(world) {
  if (controllers.has(world)) return controllers.get(world)
  if (!world.targetState) world.targetState = reactive(createTargetState())
  const state = world.targetState
  // SIM06: one notice per failed/cancelled motion request, keyed by run identity
  // (plan_id + executor epoch + started + revision). Never keyed by message text.
  // The owning panel renders this single line; the top banner must not repeat it.
  let failureSeq = 0
  const motionKey = request => `${request?.planId || 'no-plan'}|${request?.acceptedExecution?.epoch ?? request?.beforeExecution?.epoch ?? 'na'}|${request?.acceptedExecution?.started ?? request?.beforeExecution?.started ?? 'na'}|${request?.revision ?? 'na'}`
  function noteFailure({ key, kind, detail = '', text }) {
    if (state.failure && state.failure.key === key) return state.failure
    state.failure = { key, seq: ++failureSeq, kind, detail, text }
    return state.failure
  }
  function clearFailure() { if (state.failure) state.failure = null }
  let owner = null, pending = null, pumping = false, observations = 0, registeredAt = -1
  let registeredRevision = -1, latestFrame = world.snap, execution = null, movePending = false
  let executionObservedMoving = false, lastObserved = null, lastTime, lastPreview, lastSafety, lastExecution, lastMoving
  let motionControl=null,pauseAwaiting='',controlVersion=0,stopping=false,motionRead=null,lastPaused,resumeToken=null
  const motionPhase=value=>value?.status || value?.state
  const defaultMotionControl={
    supported:()=>!state.motionStatus?.pending && !state.motionStatus?.terminal
      && (motionPhase(state.motionStatus)==='paused'?state.motionStatus?.can_resume!==false:motionPhase(state.motionStatus)==='running' && state.motionStatus?.can_pause!==false),
    paused:()=>motionPhase(state.motionStatus)==='paused'?true:motionPhase(state.motionStatus)==='running'?false:null,
    pause:()=>world.call('/api/motion/pause',{}),resume:()=>world.call('/api/motion/resume',resumeToken?{token:resumeToken}:{})}
  const control=()=>motionControl || world.pointMotionControl || defaultMotionControl
  const pausedFeedback=()=>{
    const value=control()?.paused?.(world.snap)
    return typeof value==='boolean'?value:null
  }
  function pauseReason(){
    const transport=control()
    if(!execution || execution.invalidated || !state.executing)return '没有可暂停或继续的本页执行目标。'
    if(stopping || state.controlPending)return '控制请求尚未确认，请等待回读。'
    if(!transport || typeof transport.pause!=='function' || typeof transport.resume!=='function'
      || transport.supported?.(world.snap?.mode,world.snap)!==true)return '当前单点链路未提供可验证的暂停/继续接口；不能仅在界面假停。'
    if(pausedFeedback()===null)return '暂停状态回读未知，不能暂停或继续旧目标。'
    if(transport===defaultMotionControl && pausedFeedback()===true && state.motionStatus?.status && !resumeToken)return '本页没有有效暂停 token，不能恢复其他来源的暂停绑定。'
    return ''
  }
  const scope = effectScope(true)
  const connected = () => world.connected !== false && world.stateFresh !== false && !!world.snap?.session_id
  const current = token => !!token && token.revision === state.revision && token.context === state.context
    && token.context === targetContext(world) && connected()
  const token = () => ({ revision: state.revision, context: state.context })
  function discardPending(why = '目标已被更新') {
    if (pending) { pending.resolve({ ok: false, status: 'superseded', why }); pending = null }
  }
  function clearArrival() {
    state.arrivalBinding = null
    if (state.executionOutcome === 'reached') { state.executionOutcome = 'idle'; state.executionMessage = '' }
  }
  function invalidate(why = '模式、工具、TCP、会话或连接已变化，请重新求解或调整目标后预览。', expectedOwner) {
    if (expectedOwner !== undefined && owner !== expectedOwner) return false
    if (why !== REACHED_REASON) clearArrival()
    discardPending(why)
    clearFailure()
    state.revision++; state.stage = 'invalid'; state.reason = why
    state.planId = null
    state.acknowledged = false; state.pending = false; registeredRevision = -1
    controlVersion++;pauseAwaiting='';state.controlPending=''
    state.motionStatus=null;resumeToken=null
    if (execution) {
      execution.invalidated = true
      state.executing = false
      state.executionOutcome = 'unknown'
      state.executionMessage = movePending ? '旧运动请求仍在处理中，结果未知；不会在新上下文重发。' : '旧运动目标已失效，是否到达未知；请核对后端反馈。'
    }
    owner = null
    if (sameTarget(world.preview, state.joints)) world.preview = null
    return true
  }
  function contextChanged() {
    const hasIntent=!!state.source || state.joints!==null || !!state.planId || !!owner
      || !!pending || !!execution || movePending || state.stage==='solving'
    if(hasIntent)invalidate()
    else {
      clearFailure()
      state.stage='idle';state.context='';state.reason='尚无目标，请先预览。'
      state.acknowledged=false
    }
  }
  function fail(why, expected, identity = null) {
    if (expected && !current(expected)) return false
    discardPending(why)
    const text = why || '目标处理失败，请重新预览。'
    state.stage = 'failed'; state.reason = text
    state.acknowledged = false; state.pending = false; registeredRevision = -1
    noteFailure({ key: identity?.key || `target|${state.revision}|${state.context}`, kind: identity?.kind || 'target',
      detail: identity?.detail || text, text })
    // A target failure is shown once, in the panel that owns the target; it never
    // mirrors into the global request banner (SIM06).
    return true
  }
  function beginSolve({ source = 'teaching', pose = null, owner: nextOwner = null } = {}) {
    invalidate('目标正在求解，旧预览已失效。')
    state.context = targetContext(world); state.source = source; state.pose = validTarget(pose) ? [...pose] : null
    state.joints = null; owner = nextOwner
    if (!connected()) { state.reason = '连接已断开，无法求解目标。'; return token() }
    state.stage = 'solving'; state.reason = '正在求解目标。'
    return token()
  }
  function safetyReason(frame) {
    const risk = frame?.preview_safety
    if (!risk || !['green', 'yellow', 'red', 'alarm'].includes(risk.level)) return '目标安全状态未知，等待后端安全检查结果，禁止执行。'
    if (risk.collisions?.length || ['red', 'alarm'].includes(risk.level)) return '目标安全风险，禁止执行：' + (risk.reasons?.join('；') || '请调整目标。')
    // joint_level in simulation is diagnostic; level is the backend decision.
    return ''
  }
  function blockReason() {
    void state.reason // Subscribe renderers to changes in a raw websocket frame's decision.
    if(stopping)return '取消停止尚未确认，不能提交新运动。'
    if (state.executing || movePending) return state.executionMessage || '执行中，请等待当前运动确认完成；不会重复提交。'
    if (!connected()) return '连接已断开，目标已失效，请重新连接后预览。'
    if (['idle', 'failed', 'invalid', 'solving'].includes(state.stage)) return state.reason
    if (state.context !== targetContext(world)) return '目标上下文已变化，请重新求解或预览。'
    if (!validTarget(state.joints)) return '目标关节值无效，请重新预览。'
    if (state.pending || registeredRevision !== state.revision) return '等待目标预览请求完成。'
    if (!state.acknowledged || !sameTarget(latestFrame?.preview, state.joints)) return '等待新的后端快照确认最新目标预览。'
    if (state.planId && latestFrame?.target_plan_id !== state.planId) return '等待后端确认本次目标计划。'
    if (!latestFrame?.motion_granted) return '当前只读，请先在顶部完成就绪。'
    if (latestFrame?.joints?.known === false || !validTarget(latestFrame?.joints?.q)) return '当前关节状态无效，等待后端有效状态。'
    if (latestFrame?.moving || latestFrame?.exec?.execution?.busy) return '执行中，请等待当前运动完成。'
    return safetyReason(latestFrame)
  }
  function refreshStage() {
    if (state.executing || movePending) return
    if (!['waiting', 'ready'].includes(state.stage)) return
    const why = blockReason()
    state.stage = why ? 'waiting' : 'ready'
    state.reason = why || '后端已确认目标，可显式执行。'
  }
  function observe(frame, arrival = false) {
    if (!frame) return
    if (state.arrivalBinding && (frame.target_plan_id !== state.arrivalBinding.planId
        || targetContext(world, frame) !== state.arrivalBinding.context)) clearArrival()
    const executor = executionSignature(frame.exec?.execution)
    if (!arrival && frame === lastObserved && frame.t === lastTime && frame.preview === lastPreview
        && frame.preview_safety === lastSafety && executor === lastExecution && frame.moving === lastMoving && lastPaused===pausedFeedback()) return
    lastObserved = frame; lastTime = frame.t; lastPreview = frame.preview; lastSafety = frame.preview_safety
    lastExecution = executor; lastMoving = frame.moving
    lastPaused=pausedFeedback()
    latestFrame = frame; observations++
    if (state.context && state.context !== targetContext(world, frame) && state.stage !== 'invalid') invalidate()
    if (registeredRevision === state.revision && !state.pending && observations > registeredAt
        && state.context === targetContext(world, frame) && sameTarget(frame.preview, state.joints)
        && (!state.planId || frame.target_plan_id === state.planId)
        && ['waiting', 'ready'].includes(state.stage)) state.acknowledged = true
    if (execution && !movePending && !execution.invalidated && observations > execution.sentAt) {
      executionObservedMoving ||= !!frame.moving || !!frame.exec?.execution?.busy
      const motionPaused=pausedFeedback()===true
      const resumeObserved=frame.moving===true || (frame.moving===false && frame.joints?.known!==false
        && validTarget(frame.joints?.q) && frame.joints.q.every((value,index)=>Math.abs(value-execution.joints[index])<=.3))
      if(pauseAwaiting && ((pauseAwaiting==='pause' && motionPaused) || (pauseAwaiting==='resume' && pausedFeedback()===false && resumeObserved))){pauseAwaiting='';state.controlPending=''}
      const settled = frame.moving === false && !frame.exec?.execution?.busy
      const positionKnown = frame.joints?.known !== false && validTarget(frame.joints?.q)
      const reached = !!execution.planId && frame.target_evidence?.plan_id === execution.planId
        && frame.target_evidence?.reached === true && frame.target_evidence?.status === 'reached'
      // Arrival evidence is a sampled proof, not a terminal outcome. A final
      // executor snapshot may precede the fresh pose/settling proof by a frame.
      const awaitingProof = frame.target_plan_id === execution.planId
        && frame.target_evidence?.plan_id === execution.planId
        && ['accepted','unknown','moving','outside','settling'].includes(frame.target_evidence.status)
      const jointsOutside = positionKnown && frame.joints.q.some((v,i)=>Math.abs(v-execution.joints[i])>.3)
      const status = frame.exec?.execution
      const failed = executionTerminals.has(status?.state) && terminalBelongsToRequest(execution, status, executionObservedMoving)
      if(!failed && (motionPaused || pauseAwaiting)){
        state.executionOutcome=motionPaused?'paused':'unknown'
        state.executionMessage=motionPaused?'后端已确认暂停；可继续当前目标，取消将终结目标。':'暂停/继续请求已接受，等待后端状态回读；未确认物理暂停。'
      } else if (failed || (settled && executionObservedMoving && !reached && !awaitingProof
          && (!positionKnown || jointsOutside || (frame.target_evidence?.plan_id === execution.planId && frame.target_evidence?.status === 'outside')))) {
        const request = execution
        const detail = status?.error || status?.why || status?.state || ''
        execution = null
        state.executing = false; state.executionOutcome = failed ? 'failed' : positionKnown ? 'stopped' : 'unknown'
        state.executionMessage = failed ? (status.state === 'cancelled'
          ? `运动已取消：${detail || '执行已取消'}；是否到达未确认。`
          : `运动失败：${detail || status.state}；是否到达未确认。`)
          : positionKnown ? '运动已中途停止，尚未到达目标。'
            : '运动已停止，但关节反馈不可用，到达状态未知。'
        if (current(request)) fail(state.executionMessage, request, {
          key: `motion|${request.planId || 'no-plan'}|${status?.epoch ?? 'na'}|${status?.started ?? 'na'}|${request.revision}`,
          kind: failed ? 'motion' : 'motion-stopped', detail })
      } else if (settled && reached) {
        const request = execution
        execution = null
        state.executing = false
        state.executionOutcome = 'reached'; state.executionMessage = request.planId ? '后端已确认目标到达并停稳。' : '后端六轴关节反馈已确认到达目标。'
        if (current(request)) {
          invalidate(REACHED_REASON)
          state.arrivalBinding = {planId:request.planId,context:request.context}
        }
      } else if (typeof frame.moving !== 'boolean') {
        state.executionOutcome = 'unknown'; state.executionMessage = '运动反馈未知，停稳及到达目标均未确认。'
      } else if (settled && awaitingProof) {
        state.executionOutcome = 'unknown'; state.executionMessage = '等待后端确认到达并停稳。'
      } else if (executionObservedMoving) {
        state.executionOutcome = 'running'; state.executionMessage = '后端报告运动中，尚未确认到达目标。'
      } else {
        state.executionOutcome = 'unknown'; state.executionMessage = '请求已接受，尚未观察到运动或到达目标；等待后端反馈确认。'
      }
    }
    refreshStage()
  }
  async function pump() {
    if (pumping) return
    pumping = true
    try {
      while (pending) {
        const job = pending; pending = null
        if (!current(job)) { job.resolve({ ok: false, status: 'stale', why: '目标已失效' }); continue }
        let result
        try {
          result = await world.previewRequest(job.joints, job.planId)
          if (result?.ok !== true) throw new Error(result?.why || (result?.reason_code === 'ik_busy'
            ? '预览计算忙（ik_busy），请手动重试。' : '后端拒绝目标预览，请手动重试。'))
          if (current(job)) {
            if (typeof result.plan_id === 'string') state.planId = result.plan_id
            state.pending = false
            registeredRevision = job.revision; registeredAt = observations
            state.acknowledged = false
            if (job.joints) { state.stage = 'waiting'; state.reason = '等待新的后端快照确认目标预览。' }
          }
        } catch (error) {
          result = { ok: false, status: 'preview_failed', why: error.message || '目标预览请求失败' }
          if (current(job)) fail(result.why, job)
        }
        job.resolve(current(job) ? result : { ok: false, status: 'superseded', why: '目标已被更新，旧响应未采纳' })
      }
    } finally { pumping = false }
  }
  function preview(joints, { source = 'joint', pose = null, owner: nextOwner = null, from, planId = null } = {}) {
    if (from && !current(from)) return Promise.resolve({ ok: false, status: 'stale', why: '求解目标已失效' })
    if (joints !== null && !validTarget(joints)) {
      invalidate('目标关节值无效。'); fail('目标关节值无效。')
      return Promise.resolve({ ok: false, why: state.reason })
    }
    clearArrival()
    discardPending()
    clearFailure()
    state.revision++; state.context = targetContext(world); state.source = source
    state.joints = joints && [...joints]; state.pose = validTarget(pose) ? [...pose] : null
    state.planId = planId
    state.acknowledged = false; registeredRevision = -1; owner = nextOwner
    world.preview = joints && [...joints] // Local rendering never waits for HTTP.
    state.stage = joints ? 'waiting' : 'invalid'; state.reason = joints ? '等待目标预览请求完成。' : '目标已取消。'
    state.pending = true
    if (!connected()) { fail('连接已断开，目标预览未发送。'); return Promise.resolve({ ok: false, why: state.reason }) }
    const promise = new Promise(resolve => { pending = { ...token(), joints: joints && [...joints], planId, resolve } })
    pump()
    return promise
  }
  async function execute(joints) {
    if(stopping)return {ok:false,why:'取消停止尚未确认，不能提交新运动。'}
    const why = blockReason()
    if (why || !sameTarget(joints, state.joints)) return { ok: false, why: why || '执行目标与当前预览不一致。' }
    // Set the lock before awaiting transport; never queue or replay a motion.
    movePending = true; state.executing = true; state.stage = 'executing'
    state.executionOutcome = 'submitting'; state.executionMessage = '执行请求处理中；不会重复提交。'
    execution = { ...token(), joints: [...state.joints], planId: state.planId, sentAt: observations,
      beforeExecution: executionSnapshot(latestFrame?.exec?.execution) }; executionObservedMoving = false
    const request = execution
    try {
      const result = await world.moveTargetRequest([...request.joints], request.planId)
      if (result?.ok !== true) throw new Error(result?.why || '运动请求被拒绝')
      request.acceptedExecution = executionSnapshot(result.execution)
      if (current(request)) {
        state.reason = '执行请求已接受，等待后端确认到达目标。'
        state.executionOutcome = 'unknown'; state.executionMessage = '请求已接受，是否到达未知，等待后端反馈。'
      }
      return result
    } catch (error) {
      state.executing = false; execution = null
      state.executionOutcome = 'unknown'
      const why = `执行请求未通过：${error.message || '请求失败'}；未开始运动。`
      state.executionMessage = why
      if (current(request)) fail(why, request, { key: motionKey(request), kind: 'motion', detail: error.message || '' })
      return { ok: false, why }
    } finally {
      movePending = false
      // Context/ownership invalidation cannot free an active HTTP slot. Once
      // transport actually settles, forget only that old execution, never replay.
      if (request.invalidated || !current(request)) {
        if (execution === request) execution = null
        state.executing = false
        state.acknowledged = false; registeredAt = observations
        if (state.stage === 'ready') state.stage = 'waiting'
        state.executionOutcome = 'unknown'
        state.executionMessage = '旧运动请求已返回，但原目标已失效；到达结果未知，未重发运动。'
      }
    }
  }
  async function pauseOrResume(){
    const why=pauseReason();if(why){state.controlMessage=why;return {ok:false,why}}
    const request=execution,version=++controlVersion,action=pausedFeedback()===true?'resume':'pause'
    state.controlPending=action;state.controlMessage='正在请求'+(action==='pause'?'暂停':'继续')+'，以控制器回读为准。'
    try{
      const result=await control()[action]({revision:request.revision,context:request.context,execution:request.acceptedExecution || request.beforeExecution})
      if(version!==controlVersion || request!==execution || request.invalidated)return {ok:false,why:'旧执行绑定已终结，不采纳迟到控制响应。'}
      if(result?.ok!==true)throw new Error(result?.why || '后端未确认控制请求')
      pauseAwaiting=action
      if(action==='pause' && typeof result.token==='string' && result.token)resumeToken=result.token
      if(action==='resume')resumeToken=null
      if(['paused','running','idle','cancelled'].includes(motionPhase(result)))state.motionStatus={...result}
      state.controlMessage='请求已接受，等待后端回读；未确认物理暂停或继续。'
      observe(world.snap,true)
      return result
    }catch(error){if(version===controlVersion){state.controlPending='';pauseAwaiting='';state.controlMessage=error.message}return {ok:false,why:error.message}}
  }
  async function cancelExecution(){
    if(stopping)return {ok:false,why:'取消处理中'}
    const owned=!!execution || movePending
    const request=execution
    const key=`cancel|${motionKey(request)}|${state.revision}`
    invalidate('目标已取消，请重新预览。')
    execution=null;state.executing=false;state.executionOutcome='cancelled'
    if(!owned)return {ok:true,preview_only:true}
    // One line for the whole cancel, including the uncertain arrival. The notice is
    // upgraded in place (same key) when the stop itself cannot be confirmed.
    noteFailure({key,kind:'cancel',text:'已取消，未确认到达；请重新预览。'})
    stopping=true;state.controlPending='cancel'
    try{
      const result=await (typeof world.motionCancel==='function' ? world.motionCancel() : world.call('/api/motion/cancel',{}))
      if(result?.ok!==true)throw new Error(result?.why || '普通停止未确认')
      return result
    }catch(error){
      if(state.failure?.key===key)state.failure={...state.failure,kind:'cancel-unconfirmed',
        text:'已取消，但停止未确认：'+(error.message||'请求失败')}
      return {ok:false,why:state.failure?.text || error.message}
    }
    finally{stopping=false;state.controlPending=''}
  }
  async function refreshMotionState(){
    if(motionRead || !execution || execution.invalidated)return
    const request=execution,version=controlVersion,context=targetContext(world)
    motionRead=(async()=>{
      try{
        const response=await fetch('/api/motion/state',{cache:'no-store',signal:AbortSignal.timeout(5000)})
        const value=await response.json()
        if(request!==execution || request.invalidated || version!==controlVersion || context!==targetContext(world))return
        if(!response.ok || value?.ok!==true || typeof motionPhase(value)!=='string'
          || (value.session_id && value.session_id!==world.snap?.session_id))throw new Error(value?.why || '运动状态读取失败或会话不匹配')
        state.motionStatus=value;observe(world.snap,true)
      }catch(error){if(request===execution && version===controlVersion){state.motionStatus=null;state.controlMessage='运动控制回读不可用：'+error.message}}
      finally{motionRead=null}
    })()
    return motionRead
  }
  const api = { state, preview, execute, observe, invalidate, beginSolve, fail, current,
    pauseOrResume,cancelExecution,pauseReason,refreshMotionState,isPaused:()=>pausedFeedback()===true,
    setMotionControl(transport){motionControl=transport},
    blockReason, isOwner: value => owner === value,
    // Single failure/cancel line for the owning panel (SIM06).
    notice: () => state.failure?.text || '',
    usable: () => connected() && state.context === targetContext(world) && validTarget(state.joints)
      && ['waiting', 'ready'].includes(state.stage),
    label: () => state.executionOutcome==='reached' && state.reason===REACHED_REASON
      ? '已到达' : TARGET_LABELS[state.executing ? 'executing' : state.stage] || '已失效',
    dispose() { invalidate('目标会话已结束。'); scope.stop() },
  }
  controllers.set(world, api)
  scope.run(() => {
    watch(() => targetContext(world), contextChanged, { flush: 'sync' })
    watch(() => [world.snap, world.snap?.t, world.snap?.preview, world.snap?.preview_safety, world.snap?.motion_granted,
      world.snap?.moving, world.snap?.paused, world.snap?.real?.paused, pausedFeedback(),executionSignature(world.snap?.exec?.execution)], () => observe(world.snap), { flush: 'sync' })
  })
  return api
}
