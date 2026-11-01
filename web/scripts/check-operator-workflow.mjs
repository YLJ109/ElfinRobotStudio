// Actual TeachingPanel/PointsPanel + Pinia actions, mocked HTTP only. No browser,
// service, robot, camera, numerical IK claim, or modification of existing tests.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { createPinia, storeToRefs } from 'pinia'
import { parse, compileScript } from '@vue/compiler-sfc'
import { useWorld } from '../src/store.js'
import * as teaching from '../src/teachingDraft.js'
import { targetFor } from '../src/targetState.js'

const read = path => readFileSync(new URL('../src/' + path,import.meta.url),'utf8')
const q0 = [0,0,0,0,0,0], goal = [0,0,0,0,0,3], pose = [10,-400,700,90,0,3]
const clone = value => JSON.parse(JSON.stringify(value))
const settle = async () => {for(let i=0;i<12;i++){await Promise.resolve();await Vue.nextTick()}}
function fixture(t) {
  const w = useWorld(createPinia()), requests = [], hooks = [], timers = new Map(), listeners = new Map(), memory = new Map()
  let timer = 0, solve = async () => ({ok:true,joints:[...goal]}), move = async () => ({ok:true,accepted:true})
  let planSequence=0,currentPlanId=null
  w.connected = true; w.stateFresh = true
  w.snap = {t:1,session_id:'offline-ui-session',config_rev:1,mode:'sim',real:null,motion_granted:true,moving:false,paused:false,
    settings:{ghost_mode:'ghost',ghost_align_tol_deg:.3,ghost_align_hold_s:.5,sim_speed_pct:10},
    joints:{q:[...q0],known:true,lower:Array(6).fill(-180),upper:Array(6).fill(180),vmax:Array(6).fill(90)},
    preview:null,preview_safety:null,tcp:{xyz_mm:[0,-400,700],rpy_deg:[90,0,0]},tcp_frame:{translation_mm:[0,0,-15],rpy_deg:[0,0,90]},exec:{state:'idle'},free:{on:false}}
  const storage = {getItem:key => memory.get(key) ?? null,setItem:(key,value) => memory.set(key,value),removeItem:key => memory.delete(key)}
  const window = {localStorage:storage,__elfinScene:{setTeachTarget() {}},addEventListener:(name,fn) => listeners.set(name,fn),removeEventListener:name => listeners.delete(name)}
  const document = {hidden:false,addEventListener() {},removeEventListener() {}}
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis,'fetch')
  Object.defineProperty(globalThis,'fetch',{configurable:true,value:async (url,options = {}) => {
    const body = JSON.parse(options.body || '{}');requests.push({url,body})
    let value
    if(url === '/api/ik') {value = await solve(body);if(value?.ok)value={...value,plan_id:`ik-${++planSequence}`}}
    else if(url === '/api/fk') value = {ok:true,xyz_mm:pose.slice(0,3),rpy_deg:pose.slice(3)}
    else if(url === '/api/preview') {currentPlanId=body.plan_id || `joint-${++planSequence}`;
      value = {ok:true,preview:body.joints,plan_id:currentPlanId}}
    else if(url === '/api/movej') value = await move(body)
    else if(url === '/api/estop') {w.snap.moving = false;value = {ok:true,settled:true}}
    else throw new Error(`Unexpected service/hardware request: ${url}`)
    return {ok:true,status:200,json:async () => value}
  }})
  const vue = {...Vue,onMounted:fn => hooks.push({mount:fn}),onBeforeUnmount:fn => hooks.push({unmount:fn})}
  const deps = {window,document,localStorage:storage,setTimeout:(fn,ms) => {timers.set(++timer,{fn,ms});return timer},clearTimeout:id => timers.delete(id),
    setInterval:(fn,ms) => {timers.set(++timer,{fn,ms});return timer},clearInterval:id => timers.delete(id)}
  function load(path) {
    const code = compileScript(parse(read(path),{filename:path}).descriptor,{id:path}).content
      .replace(/^import ([\s\S]*?) from ['"]([^'"]+)['"];?\s*$/gm,(_match,bindings,name) => `const ${bindings.replace(/\bas\b/g,':')} = modules[${JSON.stringify(name)}];`)
      .replace('export default','return')
    const modules = {vue,pinia:{storeToRefs},'../store':{useWorld:() => w},'../teachingDraft.js':teaching,'../targetState.js':{targetFor}}
    if(path.endsWith('PointsPanel.vue')) modules['./TeachingPanel.vue'] = load('panels/TeachingPanel.vue')
    return new Function('modules',...Object.keys(deps),code)(modules,...Object.values(deps))
  }
  const scope = Vue.effectScope()
  let points
  const instructor = scope.run(() => load('panels/TeachingPanel.vue').setup({}, {expose() {},emit(name,offer) {
    assert.equal(name,'execution-target');points?.acceptTeachingTarget(offer)
  }}))
  points = scope.run(() => load('panels/PointsPanel.vue').setup({}, {expose() {}}))
  points.teachingPanel.value = {handoffToExecution:instructor.handoffToExecution}
  t.after(() => {
    for(const hook of hooks)hook.unmount?.()
    targetFor(w).dispose();scope.stop();assert.equal(timers.size,0)
    originalFetch ? Object.defineProperty(globalThis,'fetch',originalFetch) : delete globalThis.fetch
  })
  const count = url => requests.filter(row => row.url === url).length
  function ack() {w.snap = {...w.snap,t:w.snap.t+1,preview:clone(w.preview),target_plan_id:currentPlanId,
    preview_safety:{level:'green',joint_level:'green',collisions:[],reasons:[]}}}
  return {w,instructor,points,requests,count,ack,memory,timers,
    setSolve:fn => {solve = fn},setMove:fn => {move = fn},
    async handoff() {points.pointTab.value = 'teaching';instructor.solveNow();await settle();points.pointTab.value = 'execute';await settle()},
  }
}
async function stage(t,rows,name,fn) {
  const start = performance.now(),row = {name,status:'fail'};rows.push(row)
  try {await fn();row.status = 'pass'} finally {row.wall_ms = Math.round((performance.now()-start)*100)/100;t.diagnostic(JSON.stringify(row))}
}

test('operator workflow: real SFC IK → preview → acknowledged handoff → explicit MoveJ → stop → tool invalidation',async t => {
  const h = fixture(t),stages = []
  await stage(t,stages,'示教求解和交接仅发送IK/preview，MoveJ等待新的快照确认',async () => {
    await h.handoff()
    assert.equal(h.count('/api/ik'),1);assert.ok(h.count('/api/preview') >= 1);assert.equal(h.count('/api/movej'),0)
    await h.points.runTarget();assert.equal(h.count('/api/movej'),0)
    h.ack();await settle();assert.equal(h.points.executionBlockReason.value,'')
  })
  await stage(t,stages,'点击执行才发送精确目标；停止使用真实Pinia停止action',async () => {
    await h.points.runTarget();assert.equal(h.count('/api/movej'),1)
    assert.deepEqual(h.requests.find(row => row.url === '/api/movej').body,{joints:goal,plan_id:h.w.targetState.planId})
    h.w.snap = {...h.w.snap,t:++h.w.snap.t,moving:true};await settle()
    await h.w.estop();assert.equal(h.count('/api/estop'),1)
    h.w.snap = {...h.w.snap,t:++h.w.snap.t,moving:false};await settle()
  })
  await stage(t,stages,'工具/TCP变化使旧示教目标失效，不继续发送MoveJ',async () => {
    h.w.snap.config_rev++;h.w.snap.tcp_frame = {translation_mm:[0,0,100],rpy_deg:[0,0,0]}
    h.w.tools = {items:[],mounted:{tool_id:'offline-gripper',revision:2},tool:{tcp:{translation_mm:[0,0,100]}}}
    await settle();await h.points.runTarget();assert.equal(h.count('/api/movej'),1)
    assert.notEqual(h.points.executionBlockReason.value,'')
    assert.equal(h.instructor.solved.value,null)
  })
  await stage(t,stages,'显式重新求解/交接/快照确认才能恢复，不因工具状态刷新自动执行',async () => {
    await h.handoff();assert.equal(h.count('/api/movej'),1)
    h.ack();await settle();assert.equal(h.points.executionBlockReason.value,'');await h.points.runTarget();assert.equal(h.count('/api/movej'),2)
  })
  assert.equal(stages.filter(row => row.status === 'pass').length,4)
})
test('operator failure recovery: session/disconnect revokes old target and late IK cannot republish preview',async t => {
  const h = fixture(t)
  for(const change of [() => {h.w.snap.session_id = 'new-offline-session'},() => {h.w.connected = false}]) {
    await h.handoff();h.ack();await settle()
    const moves = h.count('/api/movej');change();await settle();await h.points.runTarget()
    assert.equal(h.count('/api/movej'),moves)
    assert.notEqual(h.points.executionBlockReason.value,'')
    h.w.connected = true;await settle()
  }
  let finish
  h.setSolve(() => new Promise(resolve => {finish = resolve}))
  h.points.pointTab.value = 'teaching';h.instructor.solveNow();await settle()
  const previews = h.count('/api/preview')
  h.w.snap.session_id = 'changed-during-ik';await settle()
  finish({ok:true,joints:goal});await settle()
  assert.equal(h.count('/api/preview'),previews);assert.equal(h.instructor.solved.value,null)
  assert.equal(h.instructor.canAdd.value,false)
  h.setSolve(async () => ({ok:true,joints:goal}));await h.handoff();h.ack();await settle()
  assert.equal(h.points.executionBlockReason.value,'')
})
test('operator solve failure clears eligibility; recovery and rejected MoveJ never auto-retry',async t => {
  const h = fixture(t)
  h.setSolve(async () => ({ok:false,why:'离线注入：目标不可达'}))
  h.points.pointTab.value = 'teaching';h.instructor.solveNow();await settle()
  assert.equal(h.instructor.solved.value,null);assert.equal(h.instructor.canAdd.value,false)
  assert.equal(h.count('/api/preview'),0);assert.match(h.instructor.message.value,/目标不可达/)
  h.setSolve(async () => ({ok:true,joints:goal}));await h.handoff();h.ack();await settle()
  h.setMove(async () => ({ok:false,why:'离线注入：执行被拒绝'}))
  await h.points.runTarget();await settle();assert.equal(h.count('/api/movej'),1)
  // Advancing reactive frames must not replay a rejected command.
  h.ack();await settle();assert.equal(h.count('/api/movej'),1)
  h.setMove(async () => ({ok:true,accepted:true}));await h.points.runTarget();assert.equal(h.count('/api/movej'),1)
  await h.handoff();h.ack();await settle();await h.points.runTarget();assert.equal(h.count('/api/movej'),2)
})
