// Offline only: real Pinia store, fake transport, no services or hardware.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { createPinia, defineStore } from 'pinia'
import { parseResponse } from '../src/connectionState.js'
import { targetFor, createTargetState } from '../src/targetState.js'

const read = name => readFileSync(new URL('../src/' + name, import.meta.url), 'utf8')
const settle = async () => { for (let i = 0; i < 16; i++) await Promise.resolve() }
const q = [18.85, -108.7, -92.2, -140.7, 102.9, 10.33]
const response = (body, status = 200) => ({ ok: status < 400, status,
  json: async () => body.ok === true ? {plan_id:'offline-plan',...body} : body })

test('first connection context without target intent stays idle; solving intent still invalidates',async()=>{
  const world=loadStore(async()=>response({ok:true})),target=targetFor(world)
  world.connected=false;world.snap=null
  world.connected=true
  world.snap={session_id:'first-session',mode:'sim',config_rev:1,tcp_frame:{translation_mm:[0,0,20]}}
  assert.equal(target.state.stage,'idle')
  assert.match(target.blockReason(),/先预览/)
  assert.doesNotMatch(target.state.reason,/变化|失效/)
  const request=target.beginSolve({source:'cartesian',pose:[0,0,0,0,0,0]})
  world.snap={...world.snap,config_rev:2}
  assert.equal(target.state.stage,'invalid')
  assert.equal(target.current(request),false)
})

test('matching joints, foreign plan and settling evidence never confirm arrival',async()=>{
  const world=loadStore(async()=>response({ok:true}))
  await world.setPreview(q);acknowledge(world);await world.moveJ(q)
  for(const evidence of [null,{plan_id:'foreign',status:'reached',reached:true},
      {plan_id:'offline-plan',status:'settling',reached:false}]){
    world.snap={...world.snap,t:(world.snap.t||0)+1,moving:false,joints:{q:[...q],known:true},target_evidence:evidence}
    assert.equal(world.targetState.executing,true)
    assert.notEqual(world.targetState.executionOutcome,'reached')
  }
  world.snap={...world.snap,t:99,target_evidence:{plan_id:'offline-plan',status:'reached',reached:true}}
  assert.equal(world.targetState.executionOutcome,'reached')
})

test('same joint branch in an old plan snapshot cannot acknowledge the new plan',async()=>{
  const world=loadStore(async()=>response({ok:true}))
  await world.setPreview(q);acknowledge(world)
  world.snap={...world.snap,t:world.snap.t+1,target_plan_id:'old-plan'}
  assert.equal((await world.moveJ(q)).ok,false)
  assert.match(targetFor(world).blockReason(),/计划/)
  acknowledge(world)
  assert.equal(targetFor(world).blockReason(),'')
})

test('consumed reached target displays success, remains unusable, and later context change displays invalid',async()=>{
  const world=loadStore(async()=>response({ok:true})),target=targetFor(world)
  await world.setPreview(q);acknowledge(world);await world.moveJ(q)
  world.snap={...world.snap,t:20,moving:false,joints:{q:[...q],known:true},
    target_evidence:{plan_id:'offline-plan',status:'reached',reached:true}}
  assert.equal(target.state.stage,'invalid')
  assert.equal(target.label(),'已到达')
  assert.equal(target.usable(),false)
  assert.equal((await world.moveJ(q)).ok,false)
  world.snap={...world.snap,config_rev:2}
  assert.equal(target.label(),'已失效')
  assert.equal(target.usable(),false)
})
function loadStore(fetcher, extra = {}) {
  const scope = vm.createContext({ defineStore, parseResponse, targetFor, createTargetState, fetch: fetcher,
    location: { protocol: 'http:', host: 'offline.invalid' }, ...extra })
  vm.runInContext(read('store.js').replace(/^import .*$/gm, '').replace(/^export /gm, '') + '\nglobalThis.store = useWorld;', scope)
  const world = scope.store(createPinia())
  world.connected = true; world.stateFresh = true
  world.snap = { mode: 'sim', real: null, session_id: 'offline', config_rev: 1, t: 1,
    motion_granted: true, joints: { q: [0, 0, 0, 0, 0, 0], known: true }, preview: null,
    preview_safety: { level: 'green', joint_level: 'red', joint_out_of_bounds: false, collisions: [] } }
  return world
}

async function motionFixture(t){
  let motion='running';const calls=[]
  const world=loadStore(async(path,options)=>{
    calls.push(path)
    if(path==='/api/motion/pause')motion='paused'
    if(path==='/api/motion/resume')motion='running'
    if(path==='/api/motion/cancel')motion='cancelled'
    return response({ok:true,state:motion,execution:{epoch:1,started:10,state:'running',busy:true}})
  })
  t.mock.method(globalThis,'fetch',async path=>{assert.equal(path,'/api/motion/state');calls.push(path);return response({ok:true,state:motion})})
  const target=targetFor(world);await world.setPreview(q);acknowledge(world);world.snap={...world.snap,moving:false};await world.moveJ(q)
  world.snap={...world.snap,t:10,moving:true,exec:{execution:{epoch:1,started:10,state:'running',busy:true}}}
  await target.refreshMotionState()
  return {world,target,calls,setMotion:value=>{motion=value}}
}
test('single target pauses and resumes with real backend acknowledgement, never reissues MOVEJ',async t=>{
  const {world,target,calls}=await motionFixture(t)
  assert.equal(target.pauseReason(),'');await target.pauseOrResume();assert.equal(target.isPaused(),true)
  world.snap={...world.snap,t:11,moving:false,exec:{execution:{epoch:1,started:10,state:'paused',busy:false}}}
  assert.equal(target.state.executing,true,'paused motion is not a failed short stop')
  await target.pauseOrResume();assert.equal(target.state.executing,true,'old stopped snapshot cannot end resumed target')
  world.snap={...world.snap,t:12,moving:true,exec:{execution:{epoch:1,started:10,state:'running',busy:true}}}
  assert.equal(target.state.controlPending,'');assert.equal(target.isPaused(),false)
  assert.equal(calls.filter(path=>path==='/api/movej').length,1)
  assert.deepEqual(calls.filter(path=>/motion\/(pause|resume)/.test(path)),['/api/motion/pause','/api/motion/resume'])
})
test('cancel after pause terminates binding, uses ordinary cancel, and refuses resume until new explicit target',async t=>{
  const {world,target,calls}=await motionFixture(t);await target.pauseOrResume();await target.cancelExecution()
  assert.equal(target.state.executing,false);assert.equal(target.state.stage,'invalid')
  assert.equal((await target.pauseOrResume()).ok,false)
  assert.equal((await world.moveJ(q)).ok,false)
  assert.equal(calls.filter(path=>path==='/api/motion/cancel').length,1)
  assert.equal(calls.some(path=>/estop|motion\/resume/.test(path)),false)
})
test('cancel preview without an execution binding sends no stop or estop request',async()=>{
  const calls=[],world=loadStore(async path=>{calls.push(path);return response({ok:true})}),target=targetFor(world)
  await world.setPreview(q);await target.cancelExecution()
  assert.deepEqual(calls,['/api/preview']);assert.equal(target.state.stage,'invalid')
})
test('rejected hardware pause never becomes a fake UI paused state',async t=>{
  const {world,target}=await motionFixture(t)
  world.call=async()=>({ok:false,why:'GrpInterrupt 被拒绝'})
  assert.equal((await target.pauseOrResume()).ok,false);assert.equal(target.isPaused(),false)
  assert.match(target.state.controlMessage,/被拒绝/);assert.equal(target.state.controlPending,'')
})
test('unknown readback disables pause rather than invoking program resume or pretending support',async t=>{
  const {target}=await motionFixture(t);target.state.motionStatus=null
  assert.notEqual(target.pauseReason(),'');assert.equal((await target.pauseOrResume()).ok,false)
})
test('cancelled binding ignores delayed pause acknowledgement and never permits continuing',async t=>{
  const {world,target,calls}=await motionFixture(t);let finish
  world.call=async path=>{calls.push(path);if(path==='/api/motion/pause')return await new Promise(resolve=>{finish=resolve});return {ok:true,state:'cancelled'}}
  const pending=target.pauseOrResume();await target.cancelExecution();finish({ok:true,state:'paused'})
  assert.equal((await pending).ok,false);assert.equal(target.state.stage,'invalid');assert.equal(target.isPaused(),false)
  assert.equal((await target.pauseOrResume()).ok,false)
})
test('canonical status contract resumes only the token returned by this target pause, not a later foreign GET token',async t=>{
  const {world,target,calls}=await motionFixture(t);const payloads=[]
  world.call=async(path,body)=>{calls.push(path);payloads.push(body);return path.endsWith('/pause')
    ?{ok:true,status:'paused',token:'owned-token',can_resume:true,session_id:'offline'}
    :{ok:true,status:'running',token:null,can_pause:true,session_id:'offline'}}
  await target.pauseOrResume();assert.equal(target.isPaused(),true)
  target.state.motionStatus={ok:true,status:'paused',token:'foreign-token',can_resume:true}
  await target.pauseOrResume();assert.deepEqual(payloads,[{},{token:'owned-token'}])
})
test('canonical paused status without own token and pending/terminal sessions cannot resume',async t=>{
  const {target}=await motionFixture(t)
  for(const status of [{status:'paused',token:'foreign',can_resume:true},{status:'paused',pending:true},{status:'paused',terminal:true},{status:'failed',can_resume:false}]){
    target.state.motionStatus={ok:true,...status};assert.notEqual(target.pauseReason(),'');assert.equal((await target.pauseOrResume()).ok,false)
  }
})
test('store preview writes are singleflight and local latest wins', async () => {
  const calls = []
  const world = loadStore((path, options) => new Promise(resolve => calls.push({ path, body: JSON.parse(options.body), resolve })))
  world.setPreview(q)
  world.setPreview([1, 2, 3, 4, 5, 6])
  await settle()
  assert.deepEqual([...world.preview], [1, 2, 3, 4, 5, 6])
  assert.equal(calls.length, 1, 'second preview must wait for the active HTTP operation')
  calls[0].resolve(response({ ok: true })); await settle()
  assert.equal(calls.length, 2)
  calls[1].resolve(response({ ok: true })); await settle()
})

test('store preview rejection is visible in the owning panel, not the global banner', async () => {
  const world = loadStore(async () => response({ ok: false, why: 'preview rejected' }))
  world.setPreview(q); await settle()
  // SIM06: one failure surface. The target notice carries it; the banner stays clear.
  assert.match(targetFor(world).notice(), /preview rejected/)
  assert.equal(world.error, '')
})

function acknowledge(world, preview = q, safety = { level: 'green', joint_level: 'alarm', joint_out_of_bounds: false }) {
  world.snap = { ...world.snap, t: world.snap.t + 1, preview: preview && [...preview], preview_safety: safety,
    target_plan_id: world.targetState.planId }
}

test('latest-only queue coalesces B, sends C after A; A reply cannot confirm C', async () => {
  const calls = []
  const world = loadStore((path, options) => new Promise(resolve => calls.push({ path, body: JSON.parse(options.body), resolve })))
  const a = world.setPreview(q), b = world.setPreview([1, 2, 3, 4, 5, 6]), c = world.setPreview([2, 3, 4, 5, 6, 7])
  assert.equal((await b).status, 'superseded')
  acknowledge(world, q)
  calls[0].resolve(response({ ok: true })); await settle()
  assert.equal((await a).status, 'superseded')
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[1].body.joints, [2, 3, 4, 5, 6, 7])
  acknowledge(world, [2, 3, 4, 5, 6, 7]) // Before the POST result, not an acknowledgement yet.
  calls[1].resolve(response({ ok: true })); await c
  assert.equal(world.targetState.stage, 'waiting')
  assert.equal(world.targetState.acknowledged, false)
  acknowledge(world, [2, 3, 4, 5, 6, 7])
  assert.equal(world.targetState.stage, 'ready')
})

test('cancel is serialized after active preview, clears local immediately and never moves', async () => {
  const calls = []
  const world = loadStore((path, options) => new Promise(resolve => calls.push({ path, body: JSON.parse(options.body), resolve })))
  const first = world.setPreview(q), cancel = world.cancelPreview()
  assert.equal(world.preview, null); assert.equal(calls.length, 1)
  calls[0].resolve(response({ ok: true })); await settle()
  assert.equal(calls.length, 2); assert.equal(calls[1].body.joints, null)
  calls[1].resolve(response({ ok: true })); await Promise.all([first, cancel])
  acknowledge(world)
  assert.equal(world.targetState.stage, 'invalid')
  assert.match(targetFor(world).blockReason(), /取消/)
})

for (const [label, failure] of [
  ['HTTP', async () => response({ why: 'http denied' }, 503)],
  ['okfalse ik_busy', async () => response({ ok: false, reason_code: 'ik_busy', why: '预览计算忙，请手动重试' })],
  ['network', async () => { throw new Error('connection lost') }],
  ['non-JSON', async () => ({ ok: true, status: 200, json: async () => { throw new Error('bad json') } })],
]) test(`${label} preview failure remains failed despite delayed matching snapshots`, async () => {
  let count = 0
  const world = loadStore(async (...args) => { count++; return failure(...args) })
  assert.equal((await world.setPreview(q)).ok, false)
  acknowledge(world); acknowledge(world)
  assert.equal(world.targetState.stage, 'failed')
  assert.ok(targetFor(world).notice())
  assert.equal(world.error, '')
  assert.equal((await world.moveJ(q)).ok, false)
  assert.equal(count, 1, 'no automatic retry or motion')
})

for (const [name, change] of [
  ['mode ABA', w => { w.snap.mode = 'real'; w.snap.mode = 'sim' }],
  ['session ABA', w => { w.snap.session_id = 'new'; w.snap.session_id = 'offline' }],
  ['tool', w => { w.tools.mounted = { tool_id: 'new', revision: 2 } }],
  ['TCP', w => { w.snap.tcp_frame = { translation_mm: [0, 0, 99] } }],
  ['disconnect ABA', w => { w.connected = false; w.connected = true }],
]) test(`${name}: in-flight response, queued preview and old snapshot cannot revive authority`, async () => {
  const calls = []
  const world = loadStore((path, options) => new Promise(resolve => calls.push({ path, body: JSON.parse(options.body), resolve })))
  const a = world.setPreview(q), b = world.setPreview([1, 2, 3, 4, 5, 6])
  change(world)
  assert.equal((await b).ok, false)
  calls[0].resolve(response({ ok: true })); await a
  acknowledge(world)
  assert.equal(world.targetState.stage, 'invalid'); assert.equal(calls.length, 1)
  assert.equal((await world.moveJ(q)).ok, false)
  const fresh = world.setPreview(q)
  calls[1].resolve(response({ ok: true })); await fresh
  acknowledge(world)
  assert.equal(world.targetState.stage, 'ready')
})

test('fresh UI/store cannot adopt backend preview or persisted permission as a target', async () => {
  let requests = 0
  const world = loadStore(async () => { requests++; return response({ ok: true }) }, {
    localStorage: { getItem: () => JSON.stringify({ joints: q, stage: 'ready', motion_granted: true }) },
  })
  acknowledge(world)
  assert.equal((await world.moveJ(q)).ok, false)
  assert.equal(world.targetState.stage, 'idle')
  assert.equal(targetFor(world).usable(),false)
  assert.equal(requests, 0)
})

for (const [level, allowed] of [['green', true], ['yellow', true], ['red', false], ['alarm', false], ['unknown', false], [null, false]]) {
  test(`target safety ${level} follows backend level, never the diagnostic joint_level`, async () => {
    const calls = []
    const world = loadStore(async (path, options) => { calls.push(path); return response({ ok: true }) })
    await world.setPreview(q)
    acknowledge(world, q, level === null ? null : { level, joint_level: 'alarm', joint_out_of_bounds: false, collisions: [] })
    const result = await world.moveJ(q)
    assert.equal(result.ok, allowed)
    assert.equal(calls.filter(p => p === '/api/movej').length, allowed ? 1 : 0)
  })
}

test('duplicate movement is neither queued nor replayed, and execution waits for settling', async () => {
  let finish, moves = 0
  const world = loadStore(async path => {
    if (path === '/api/movej') { moves++; return await new Promise(resolve => { finish = resolve }) }
    return response({ ok: true })
  })
  await world.setPreview(q); acknowledge(world)
  const first = world.moveJ(q)
  assert.equal((await world.moveJ(q)).ok, false)
  finish(response({ ok: true })); assert.equal((await first).ok, true)
  assert.equal((await world.moveJ(q)).ok, false)
  assert.equal(moves, 1)
  world.snap = { ...world.snap, t: 5, moving: true }; acknowledge(world)
  world.snap = { ...world.snap, t: 6, moving: false, joints: { q: [...q] },
    target_evidence:{plan_id:'offline-plan',reached:true,status:'reached'} }; acknowledge(world)
  assert.equal(world.targetState.executing, false)
  assert.equal(world.targetState.stage, 'invalid')
  assert.equal((await world.moveJ(q)).ok, false)
})

test('editing while a move HTTP request is pending cannot release its singleflight lock', async () => {
  let finish, moves = 0
  const world = loadStore(async path => {
    if (path === '/api/movej') { moves++; return await new Promise(resolve => { finish = resolve }) }
    return response({ ok: true })
  })
  await world.setPreview(q); acknowledge(world)
  const move = world.moveJ(q)
  await world.setPreview([1, 2, 3, 4, 5, 6]); acknowledge(world, [1, 2, 3, 4, 5, 6])
  assert.equal((await world.moveJ([1, 2, 3, 4, 5, 6])).ok, false)
  finish(response({ ok: true })); await move
  assert.equal((await world.moveJ([1, 2, 3, 4, 5, 6])).ok, false)
  assert.equal(moves, 1)
})

test('movement failure is visible and cannot be retried by another execute click', async () => {
  let moves = 0
  const world = loadStore(async path => {
    if (path === '/api/movej') { moves++; throw new Error('ambiguous move response') }
    return response({ ok: true })
  })
  await world.setPreview(q); acknowledge(world)
  assert.equal((await world.moveJ(q)).ok, false)
  acknowledge(world)
  assert.equal(world.targetState.stage, 'failed')
  assert.match(world.targetState.failure.text, /执行请求未通过：ambiguous move response；未开始运动/)
  assert.equal(targetFor(world).notice(), world.targetState.failure.text)
  assert.equal(world.error, '')
  assert.equal((await world.moveJ(q)).ok, false); assert.equal(moves, 1)
})

test('one identity-keyed notice per motion failure; the raw cause stays in the record', async () => {
  const world = loadStore(async path => response(path === '/api/movej'
    ? { ok: true, execution: { epoch: 5, started: 200, state: 'running', busy: true } } : { ok: true }))
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: false,
    exec: { execution: { epoch: 6, started: 200, state: 'failed', busy: false, error: '关节跟随超差' } } }
  const target = targetFor(world), failure = target.state.failure
  assert.equal(failure.key, 'motion|offline-plan|6|200|1')
  assert.equal(failure.text, '运动失败：关节跟随超差；是否到达未确认。')
  assert.equal(failure.detail, '关节跟随超差', 'raw reason stays available for the run log')
  assert.equal(target.notice(), failure.text)
  target.fail(failure.text, null, { key: failure.key, kind: 'motion', detail: '关节跟随超差' })
  assert.equal(target.state.failure.seq, failure.seq, 'same run identity must not stack a second notice')
  target.fail('运动失败：另一个执行；是否到达未确认。', null, { key: 'motion|offline-plan|7|200|1' })
  assert.equal(target.state.failure.seq, failure.seq + 1, 'a different executor run is a new notice')
  assert.equal(world.error, '', 'a target failure never mirrors into the global banner')
})

test('cancel reports one line and upgrades it in place when the stop is unconfirmed', async t => {
  const { world, target } = await motionFixture(t)
  world.motionCancel = async () => ({ ok: false, why: '停止回执超时' })
  const result = await target.cancelExecution()
  assert.equal(result.ok, false)
  assert.equal(target.state.controlMessage, '', 'cancel must not add a second panel line')
  assert.equal(target.notice(), '已取消，但停止未确认：停止回执超时')
  assert.equal(world.error, '')
})

test('cancel keeps a single confirmed line and still refuses to claim arrival', async t => {
  const { target } = await motionFixture(t)
  await target.cancelExecution()
  assert.equal(target.notice(), '已取消，未确认到达；请重新预览。')
  assert.doesNotMatch(target.notice(), /已到达|已完成/)
})

test('metrics cover HTTP/IK/preview and state timing; throwing metrics never affect control', async () => {
  const metrics = [], handlers = []
  let time = 0
  const world = loadStore(async () => response({ ok: true }), {
    performance: { now: () => ++time }, recordMetric(name, ms) { metrics.push([name, ms]); throw new Error('metrics failure') },
    createStateConnection(options) { handlers.push(options); return { connect() {}, stop() {} } },
  })
  await world.ik(q); await world.setPreview(q)
  world.connectWs()
  handlers[0].onOpen()
  const data = { mode: 'sim', session_id: 'offline', joints: { q }, safety: { level: 'green' } }
  handlers[0].onMessage({ data: JSON.stringify({ type: 'state', data }) })
  handlers[0].onMessage({ data: JSON.stringify({ type: 'state', data: { ...data, t: 2 } }) })
  for (const name of ['http.request', 'ik.roundtrip', 'preview.roundtrip', 'state.decode', 'state.interval']) assert.ok(metrics.some(([n, ms]) => n === name && Number.isFinite(ms)))
  assert.equal(world.connected, true); assert.deepEqual([...world.snap.joints.q], q)
  world.disconnectWs()
})

test('moving then stopped with two axes short is not completed or reached', async () => {
  let moves = 0
  const world = loadStore(async path => { if (path === '/api/movej') moves++; return response({ ok: true }) })
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: true }
  const short = [...q]; short[0] -= 10; short[4] += 20
  world.snap = { ...world.snap, t: 4, moving: false, joints: { q: short, known: true } }
  assert.equal(world.targetState.stage, 'failed')
  assert.equal(world.targetState.executionOutcome, 'stopped')
  assert.match(world.targetState.executionMessage, /中途停止.*尚未到达/)
  assert.doesNotMatch(world.targetState.executionMessage, /已完成|已确认到达/)
  acknowledge(world)
  assert.equal((await world.moveJ(q)).ok, false); assert.equal(moves, 1)
})

test('HTTP success and stationary feedback alone leave arrival unknown', async () => {
  const world = loadStore(async () => response({ ok: true }))
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: false }
  assert.equal(world.targetState.executionOutcome, 'unknown')
  assert.match(world.targetState.executionMessage, /尚未观察到运动或到达/)
  assert.doesNotMatch(world.targetState.executionMessage, /已完成|已确认到达/)
})

test('stopped motion with unavailable joint feedback reports unknown arrival, not reached or not-reached', async () => {
  const world = loadStore(async () => response({ ok: true }))
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: true }
  world.snap = { ...world.snap, t: 4, moving: false, joints: { q: [...q], known: false } }
  assert.equal(world.targetState.executionOutcome, 'unknown')
  assert.match(world.targetState.executionMessage, /关节反馈不可用.*到达状态未知/)
  assert.doesNotMatch(world.targetState.executionMessage, /尚未到达|已完成|已确认到达/)
})

test('known-false joints cannot confirm arrival and explicit backend failure stays failed', async () => {
  const world = loadStore(async () => response({ ok: true }))
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: false, joints: { q: [...q], known: false } }
  assert.equal(world.targetState.executionOutcome, 'unknown')
  world.snap = { ...world.snap, t: 4, exec: { execution: { state: 'failed', error: 'stopped by safety' } } }
  assert.equal(world.targetState.executionOutcome, 'failed')
  assert.equal(world.targetState.stage, 'failed')
  assert.match(world.targetState.executionMessage, /stopped by safety/)
})

test('context switch with missing old reply clears executing status but holds HTTP slot until that reply', async () => {
  let finish, moves = 0
  const world = loadStore(async path => {
    if (path === '/api/movej') { moves++; return await new Promise(resolve => { finish = resolve }) }
    return response({ ok: true })
  })
  await world.setPreview(q); acknowledge(world)
  const old = world.moveJ(q)
  world.snap.session_id = 'new-session'
  assert.equal(world.targetState.executing, false)
  assert.equal(world.targetState.executionOutcome, 'unknown')
  await world.setPreview([1, 2, 3, 4, 5, 6]); acknowledge(world, [1, 2, 3, 4, 5, 6])
  assert.equal((await world.moveJ([1, 2, 3, 4, 5, 6])).ok, false)
  assert.equal(moves, 1)
  assert.match(targetFor(world).blockReason(), /旧运动请求仍在处理中/)
  finish(response({ ok: true })); await old
  assert.equal(world.targetState.executing, false)
  assert.equal(world.targetState.acknowledged, false)
  assert.equal(moves, 1, 'old request must never be resent into the new session')
  acknowledge(world, [1, 2, 3, 4, 5, 6])
  assert.equal(world.targetState.stage, 'ready', 'fresh target can recover, no permanent execution lock')
})

test('dispose invalidates execution without claiming completion or releasing a pending request early', async () => {
  let finish, moves = 0
  const world = loadStore(async path => {
    if (path === '/api/movej') { moves++; return await new Promise(resolve => { finish = resolve }) }
    return response({ ok: true })
  })
  await world.setPreview(q); acknowledge(world)
  const move = world.moveJ(q)
  targetFor(world).dispose()
  assert.equal(world.targetState.executing, false)
  assert.equal((await world.moveJ(q)).ok, false)
  finish(response({ ok: true })); await move
  assert.equal(world.targetState.stage, 'invalid')
  assert.equal(world.targetState.executing, false)
  assert.equal(moves, 1)
  assert.match(world.targetState.executionMessage, /结果未知/)
})

test('fresh cancelled executor terminates a request even if moving was never observed', async () => {
  let moves = 0
  const world = loadStore(async path => {
    if (path === '/api/movej') { moves++; return response({ ok: true, execution: { epoch: 4, started: 200, state: 'running', busy: true } }) }
    return response({ ok: true })
  })
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: false,
    exec: { execution: { epoch: 5, started: 200, state: 'cancelled', busy: false, error: '用户停止' } } }
  assert.equal(world.targetState.executing, false)
  assert.equal(world.targetState.stage, 'failed')
  assert.equal(world.targetState.executionOutcome, 'failed')
  assert.match(world.targetState.executionMessage, /已取消.*是否到达未确认/)
  assert.doesNotMatch(world.targetState.executionMessage, /已完成|已确认到达/)
  assert.equal((await world.moveJ(q)).ok, false); assert.equal(moves, 1)
})

test('historical cancelled snapshot cannot cancel a new request with the same executor epoch and new started identity', async () => {
  const old = { epoch: 7, started: 100, state: 'cancelled', busy: false, error: '上次停止' }
  const world = loadStore(async path => response(path === '/api/movej'
    ? { ok: true, execution: { epoch: 7, started: 200, state: 'running', busy: true } } : { ok: true }))
  world.snap = { ...world.snap, exec: { execution: old } }
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: false, exec: { execution: { ...old } } }
  assert.equal(world.targetState.executing, true)
  assert.equal(world.targetState.executionOutcome, 'unknown')
  assert.notEqual(world.targetState.stage, 'failed')
  world.snap = { ...world.snap, t: 4, exec: { execution: { epoch: 8, started: 200, state: 'cancelled', busy: false, error: '本次停止' } } }
  assert.equal(world.targetState.executing, false)
  assert.match(world.targetState.executionMessage, /本次停止/)
})

test('legacy reply without executor identity ignores an unchanged initial cancelled terminal', async () => {
  const old = { state: 'cancelled', busy: false, error: '上次停止' }
  const world = loadStore(async () => response({ ok: true }))
  world.snap = { ...world.snap, exec: { execution: old } }
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: false, exec: { execution: { ...old } } }
  assert.equal(world.targetState.executing, true)
  assert.equal(world.targetState.executionOutcome, 'unknown')
  world.snap = { ...world.snap, t: 4, exec: { execution: { ...old, error: '本次停止' } } }
  assert.equal(world.targetState.executing, false)
  assert.equal(world.targetState.stage, 'failed')
})

test('current cancelled terminal takes precedence over a coincidentally matching joint position', async () => {
  const world = loadStore(async path => response(path === '/api/movej'
    ? { ok: true, execution: { epoch: 3, started: 200, state: 'running' } } : { ok: true }))
  await world.setPreview(q); acknowledge(world); await world.moveJ(q)
  world.snap = { ...world.snap, t: 3, moving: false, joints: { q: [...q], known: true },
    exec: { execution: { epoch: 4, started: 200, state: 'cancelled', busy: false } } }
  assert.equal(world.targetState.executionOutcome, 'failed')
  assert.match(world.targetState.executionMessage, /已取消.*是否到达未确认/)
  assert.doesNotMatch(world.targetState.executionMessage, /已确认到达/)
})

test('cancelled feedback cannot release an HTTP motion slot before its response settles', async () => {
  let finish, moves = 0
  const world = loadStore(async path => {
    if (path === '/api/movej') { moves++; return await new Promise(resolve => { finish = resolve }) }
    return response({ ok: true })
  })
  await world.setPreview(q); acknowledge(world)
  const move = world.moveJ(q)
  const cancelled = { epoch: 5, started: 200, state: 'cancelled', busy: false, error: '用户停止' }
  world.snap = { ...world.snap, t: 3, moving: false, exec: { execution: cancelled } }
  assert.equal((await world.moveJ(q)).ok, false); assert.equal(moves, 1)
  finish(response({ ok: true, execution: { epoch: 4, started: 200, state: 'running' } })); await move
  world.snap = { ...world.snap, t: 4, exec: { execution: { ...cancelled } } }
  assert.equal(world.targetState.executing, false)
  assert.equal(world.targetState.stage, 'failed')
  assert.equal(moves, 1)
})

test('finite joints marked known=false cannot authorize a move; undefined legacy known remains compatible', async () => {
  let moves = 0
  const world = loadStore(async path => { if (path === '/api/movej') moves++; return response({ ok: true }) })
  await world.setPreview(q); acknowledge(world)
  world.snap = { ...world.snap, t: 3, joints: { q: [0, 0, 0, 0, 0, 0], known: false } }
  assert.match(targetFor(world).blockReason(), /关节状态无效/)
  assert.equal((await world.moveJ(q)).ok, false)
  assert.equal(moves, 0)
  world.snap = { ...world.snap, t: 4, joints: { q: [0, 0, 0, 0, 0, 0] } }
  assert.equal((await world.moveJ(q)).ok, true)
  assert.equal(moves, 1)
})

for (const moving of [undefined, null, 0, '']) {
  test(`unknown moving=${String(moving)} cannot confirm settled or arrival even with matching joints`, async () => {
    const world = loadStore(async () => response({ ok: true }))
    await world.setPreview(q); acknowledge(world); await world.moveJ(q)
    world.snap = { ...world.snap, t: 3, moving: true }
    world.snap = { ...world.snap, t: 4, moving, joints: { q: [...q], known: true } }
    assert.equal(world.targetState.executionOutcome, 'unknown')
    assert.equal(world.targetState.executing, true)
    assert.match(world.targetState.executionMessage, /运动反馈未知.*均未确认/)
    assert.doesNotMatch(world.targetState.executionMessage, /已确认到达|已完成|已停止/)
    world.snap = { ...world.snap, t: 5, moving: false,
      target_evidence:{plan_id:'offline-plan',reached:true,status:'reached'} }
    assert.equal(world.targetState.executionOutcome, 'reached')
    assert.equal(world.targetState.executing, false)
  })
}


test('stale feedback blocks new preview and movement even while the socket remains connected',async()=>{
 const calls=[],world=loadStore(async path=>{calls.push(path);return response({ok:true})})
 const target=targetFor(world);await world.setPreview(q);acknowledge(world)
 world.stateFresh=false
 assert.equal(target.usable(),false);assert.equal((await world.setPreview(q)).ok,false)
 assert.equal((await world.moveJ(q)).ok,false)
 assert.deepEqual(calls,['/api/preview'])
 world.stateFresh=true
 assert.equal(target.usable(),false,'fresh state cannot rearm an old target')
})


test('arrival conclusion is bound to its plan/context and cleared by new preview or connection change',async()=>{
 for(const action of ['preview','solve','connection','foreign-plan']){
  const world=loadStore(async()=>response({ok:true})),target=targetFor(world)
  await world.setPreview(q);acknowledge(world);await world.moveJ(q)
  world.snap={...world.snap,t:99,moving:false,joints:{q:[...q],known:true},target_evidence:{plan_id:'offline-plan',status:'reached',reached:true}}
  assert.equal(target.state.executionOutcome,'reached')
  assert.equal(target.state.arrivalBinding.planId,'offline-plan')
  if(action==='preview')await world.setPreview(q.map((v,i)=>v+(i===0?1:0)))
  else if(action==='solve')target.beginSolve({source:'teaching'})
  else if(action==='foreign-plan')world.snap={...world.snap,target_plan_id:'another-plan'}
  else world.connected=false
  assert.notEqual(target.state.executionOutcome,'reached',action)
  assert.equal(target.state.executionMessage,'',action)
  assert.equal(target.state.arrivalBinding,null,action)
 }
})


test('after stopped WAIT a completed new MOVEJ waits for same-plan settling evidence instead of retiring the request',async()=>{
 const world=loadStore(async path=>response(path==='/api/movej'?{ok:true,execution:{epoch:1,started:20,state:'running',busy:true,action:'手动 MOVEJ'}}:{ok:true}))
 world.snap={...world.snap,exec:{state:'aborted',queue_state:'aborted',execution:{epoch:1,started:10,state:'cancelled',busy:false,action:'WAIT'}}}
 const start=[0,0,0,0,0,0],goal=[0,0,0,0,0,2]
 world.snap={...world.snap,joints:{q:start,known:true},moving:false}
 await world.setPreview(goal);acknowledge(world,goal);await world.moveJ(goal)
 world.snap={...world.snap,t:3,moving:true,joints:{q:[0,0,0,0,0,1],known:true},target_evidence:{plan_id:'offline-plan',status:'moving',reached:false},exec:{...world.snap.exec,execution:{epoch:1,started:20,state:'running',busy:true,action:'手动 MOVEJ'}}}
 world.snap={...world.snap,t:4,moving:false,joints:{q:goal,known:true},target_evidence:{plan_id:'offline-plan',status:'outside',reached:false},exec:{...world.snap.exec,execution:{epoch:1,started:20,state:'completed',busy:false,action:'手动 MOVEJ'}}}
 assert.equal(targetFor(world).state.executing,true,'nonterminal evidence must not retire a valid current request')
 assert.notEqual(targetFor(world).state.executionOutcome,'reached')
 world.snap={...world.snap,t:5,target_evidence:{plan_id:'offline-plan',status:'settling',reached:false}}
 assert.equal(targetFor(world).state.executing,true)
 world.snap={...world.snap,t:6,target_evidence:{plan_id:'offline-plan',status:'reached',reached:true}}
 assert.equal(targetFor(world).state.executionOutcome,'reached')
 assert.match(targetFor(world).state.executionMessage,/后端已确认目标到达并停稳/)
 assert.equal(targetFor(world).state.arrivalBinding.planId,'offline-plan')
 assert.equal(world.snap.exec.state,'aborted','unrelated old Runner state must never be forged')
})


test('current-plan nonterminal arrival proof never suppresses explicit executor failure or cancellation',async()=>{
 for(const state of ['failed','cancelled']){
  const world=loadStore(async path=>response(path==='/api/movej'?{ok:true,execution:{epoch:1,started:20,state:'running',busy:true}}:{ok:true}))
  await world.setPreview(q);acknowledge(world);await world.moveJ(q)
  world.snap={...world.snap,t:3,moving:true,target_evidence:{plan_id:'offline-plan',status:'moving',reached:false},exec:{execution:{epoch:1,started:20,state:'running',busy:true}}}
  world.snap={...world.snap,t:4,moving:false,target_evidence:{plan_id:'offline-plan',status:'outside',reached:false},exec:{execution:{epoch:1,started:20,state,busy:false,error:'正式安全停止'}}}
  assert.equal(targetFor(world).state.executing,false);assert.equal(targetFor(world).state.executionOutcome,'failed')
  assert.match(targetFor(world).state.executionMessage,/正式安全停止/)
  assert.equal(targetFor(world).state.arrivalBinding,null)
 }
})
