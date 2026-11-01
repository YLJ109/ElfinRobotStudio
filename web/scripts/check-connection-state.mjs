import * as numberDisplay from '../src/numberDisplay.js'
// Offline only: all HTTP, WebSockets, clocks and lifecycle hooks below are fakes.
// Run: node web/scripts/check-connection-state.mjs
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import { computed, ref, reactive, toRef } from 'vue'
import { createPinia, setActivePinia, defineStore } from 'pinia'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { createStateConnection, parseResponse, isSixAxis, speedState, displayJoints } from '../src/connectionState.js'
import { ROBOT_MODELS, DEFAULT_VACUUM, vacuumDimensions, robotAsset } from '../src/visualAssets.js'
import { buildVacuumReference, disposeAsset, readToolFile } from '../src/toolVisual.js'
import * as THREE from 'three'
import { fingertipInView, imagePixelAt } from '../src/handGeometry.js'
import { selectHandFollowSnapshot } from '../src/handFollowInput.js'
import { createHandDisplayHold } from '../src/handDisplayHold.js'
import { normalizeNavigation, NAV_VIEWS, WORK_TABS, SYSTEM_TABS } from '../src/store.js'
import { targetFor, createTargetState } from '../src/targetState.js'

const read = (path) => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
const withoutImports = (source) => source.replace(/^import .*$/gm, '')
const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function fakeTransport() {
  const sockets = [], timers = new Map()
  let next = 0
  return {
    sockets, timers,
    createSocket() {
      const socket = { close() { this.closed = true; this.onclose?.({}) } }
      sockets.push(socket)
      return socket
    },
    schedule(fn) { const id = ++next; timers.set(id, fn); return id },
    cancel(id) { timers.delete(id) },
    tick() { const list = [...timers.values()]; timers.clear(); list.forEach((fn) => fn()) },
  }
}
function state(overrides = {}) {
  return {
    mode: 'real', session_id: 'offline', settings: { tts: false, robot_ip: '192.168.0.10' },
    joints: { q: [0, 0, 0, 0, 0, 0], known: true },
    tcp: { xyz_mm: [0, 0, 1205], rpy_deg: [0, 0, 0] },
    real: { stream_up: true, cmd_up: true, joints: [1, 2, 3, 4, 5, 6], frame_seq: 12, lag_ms:30, fsm: 5 },
    safety: { level: 'green', reasons: [] },
    ctrl: { need_ack: false, primary: { enabled: true, key: 'power', label: '上电' } },
    ...overrides,
  }
}
function loadStore(fetchMock = async () => { throw new Error('Unexpected HTTP request') }, extra = {}) {
  const transport = fakeTransport()
  let clock = 0
  setActivePinia(createPinia())
  const context = vm.createContext({
    defineStore, parseResponse, targetFor, createTargetState,
    createStateConnection: (options) => createStateConnection({ ...options, ...transport }),
    location: { protocol: 'http:', host: 'offline.invalid' },
    performance: { now: () => (clock += 60) }, fetch: fetchMock, ...extra,
  })
  vm.runInContext(withoutImports(read('store.js')).replace(/^export /gm, '')
    + '\nglobalThis.out = { useWorld, setFrameHandler, latestFrame };', context)
  const world = context.out.useWorld()
  const send = (data, socket = transport.sockets.at(-1)) => socket.onmessage({ data: JSON.stringify({ type: 'state', data }) })
  return { ...transport, world, send, ...context.out }
}
function loadComponent(path, world, names, globals = {}) {
  const { descriptor, errors } = parse(read(path))
  assert.deepEqual(errors, [])
  const unmount = [], mounted = [], preferences = new Map()
  const context = vm.createContext({
    ...numberDisplay, computed, ref, reactive, targetFor, isSixAxis, displayJoints, speedState, ROBOT_MODELS, DEFAULT_VACUUM, selectHandFollowSnapshot,
    setTimeout, clearTimeout, setInterval, clearInterval,
    useWorld: () => world, defineProps:()=>({}),
    storeToRefs: (w) => Object.fromEntries(Object.keys(w).map((key) => [key, toRef(w, key)])),
    onBeforeUnmount: (fn) => unmount.push(fn), onMounted: (fn) => mounted.push(fn), watch: () => {},
    nextTick: async () => {}, defineAsyncComponent: () => ({}),
    localStorage: { getItem: (key) => preferences.get(key) ?? null,
      setItem: (key, value) => preferences.set(key, value), removeItem: (key) => preferences.delete(key) },
    readPreference:key=>preferences.get(key) ?? null,savePreference:(key,value)=>preferences.set(key,value),
    readTabState:()=>null,saveTabState(){},createWorkspaceUiState:()=>({capture(){},restore(){},dispose(){}}),
    location: { hash: '#/power' }, history: { replaceState() {} },
    ControlPanel: {}, SimPanel: {}, PowerPanel: {}, WorkPanel: {}, AboutPanel: {},
    ...globals,
  })
  vm.runInContext(withoutImports(descriptor.scriptSetup.content)
    + `\nglobalThis.out = { ${names.join(', ')} };`, context)
  return { ...context.out, mounted, preferences, run: (source) => vm.runInContext(source, context),
    unmount: () => unmount.forEach((fn) => fn()) }
}
const mockWorld = (overrides = {}) => reactive({
  connected: true, snap: state(), tele: null, receivedFrames: 1, view:'sim', workTab:'points',
  ...overrides,
})
const ready = (w) => loadComponent('components/ReadyControl.vue', w,
  ['press', 'busy', 'denied', 'label', 'cancelling'])
// Only App fixtures need the merged engineering workspace's route dependencies.
function appRouteFixture(world) {
  world.systemTab ??= 'engineering'
  world.$patch ??= (patch) => Object.assign(world, patch)
  return { normalizeNavigation, NAV_VIEWS, WORK_TABS, SYSTEM_TABS }
}

test('HTTP errors preserve why, status and need_ack payload', async () => {
  await assert.rejects(parseResponse(response(409, { why: '需要重新确认', need_ack: true }), '/api/control'), (e) => {
    assert.match(e.message, /409.*需要重新确认/)
    assert.equal(e.status, 409)
    assert.equal(e.payload.need_ack, true)
    return true
  })
  await assert.rejects(parseResponse(response(422, { detail: [{ msg: '缺少参数' }] }), '/api/control'), /缺少参数/)
  await assert.rejects(parseResponse({ ok: false, status: 502, json() { throw new Error('HTML') } }, '/api/power'), /502.*JSON/)
  assert.deepEqual(await parseResponse(response(200, { ok: false, why: '控制器拒绝' }), '/api/power'), { ok: false, why: '控制器拒绝' })
})

test('one socket and one retry; all obsolete callbacks are ignored', () => {
  const transport = fakeTransport(), events = []
  const connection = createStateConnection({ ...transport, url: 'offline',
    onOpen: () => events.push('open'), onMessage: () => events.push('message'),
    onError: () => events.push('error'), onClose: () => events.push('close') })
  connection.connect(); connection.connect()
  assert.equal(transport.sockets.length, 1)
  const old = transport.sockets[0]
  old.onopen({}); old.onclose({}); old.onclose({})
  connection.connect()
  assert.equal(transport.timers.size, 1)
  assert.equal(transport.sockets.length, 1)
  transport.tick()
  transport.sockets[1].onopen({})
  old.onopen({}); old.onmessage({}); old.onerror({}); old.onclose({})
  assert.deepEqual(events, ['open', 'close', 'open'])
  assert.equal(transport.timers.size, 0)
})

test('stop cancels retries and ignores already queued timer callbacks', () => {
  const transport = fakeTransport()
  const connection = createStateConnection({ ...transport, url: 'offline' })
  connection.connect(); transport.sockets[0].onclose({})
  const staleTimer = [...transport.timers.values()][0]
  connection.stop(); connection.connect()
  staleTimer()
  assert.equal(transport.sockets.length, 2)
  connection.stop()
  assert.equal(transport.sockets[1].closed, true)
  assert.equal(transport.timers.size, 0)
})

test('socket constructor failure can recover on the single scheduled retry', () => {
  const transport = fakeTransport()
  let attempts = 0, errors = 0
  const connection = createStateConnection({ ...transport, url: 'offline',
    createSocket: () => { if (++attempts === 1) throw new Error('unavailable'); return transport.createSocket() },
    onError: () => errors++ })
  connection.connect(); connection.connect()
  assert.equal(errors, 1)
  assert.equal(transport.timers.size, 1)
  transport.tick()
  assert.equal(transport.sockets.length, 1)
})

test('throwing 3D callback cannot block UI, logs, telemetry or following frames', () => {
  const s = loadStore()
  s.world.connectWs(); s.world.connectWs()
  assert.equal(s.sockets.length, 1)
  s.sockets[0].onopen({})
  s.setFrameHandler(() => { throw new Error('render failed') })
  s.send(state({ telemetry: { temps: [25] }, telemetry_seq: 1, log: [{ seq: 1, text: 'received' }], log_seq: 1 }))
  assert.equal(s.world.snap.real.frame_seq, 12)
  assert.equal(s.world.tele.temps[0], 25)
  assert.equal(s.world.logs[0].text, 'received')
  assert.match(s.world.frameError, /render failed/)
  assert.equal(s.world.receivedFrames, 1)
  s.setFrameHandler(() => {})
  s.send(state({ real: { frame_seq: 13 } }))
  assert.equal(s.world.snap.real.frame_seq, 13)
  assert.equal(s.world.tele.temps[0], 25)
  assert.equal(s.world.receivedFrames, 2)
  assert.equal(s.world.frameError, '')
})

test('bad JSON recovers; reconnect clears stale data and rejects old socket frames', () => {
  const s = loadStore()
  s.world.connectWs()
  const old = s.sockets[0]
  old.onopen({})
  old.onmessage({ data: '{' })
  assert.match(s.world.error, /状态消息无效/)
  s.send(state({ telemetry: { temps: [25] }, telemetry_seq: 1 }))
  assert.equal(s.world.canPrimary, true)
  old.onclose({})
  assert.equal(s.world.canPrimary, false)
  s.tick(); s.sockets[1].onopen({})
  assert.equal(s.world.snap, null)
  assert.equal(s.world.tele, null)
  assert.equal(s.world.receivedFrames, 0)
  s.send(state(), old)
  assert.equal(s.world.snap, null)
  s.send(state())
  assert.equal(s.world.receivedFrames, 1)
  s.world.disconnectWs()
  assert.equal(s.world.connected, false)
  assert.equal(s.timers.size, 0)
})

test('initial/reconnected alarm snapshot is silent; a new alarm transition still announces', () => {
  const s=loadStore(), spoken=[]
  s.world.announce=text=>spoken.push(text)
  s.world.connectWs();s.sockets[0].onopen({})
  const alarm=state({safety:{level:'alarm',reasons:['碰撞']}})
  s.send(alarm);s.send(alarm)
  assert.equal(spoken.length,0)
  s.send(state());s.send(alarm)
  assert.equal(spoken.length,1);assert.match(spoken[0],/碰撞/)
  s.sockets[0].onclose({});s.tick();s.sockets[1].onopen({});s.send(alarm)
  assert.equal(spoken.length,1)
  s.world.disconnectWs()
})

test('status strip has one safety reason, no duplicate speed/camera/backend/log preview', () => {
  const source=read('components/StatusStrip.vue')
  const template=parse(source).descriptor.template.content
  for(const text of ['后端在线','摄像头','实际倍率','仿真速度','last.text']) assert.ok(!template.includes(text))
  assert.equal((template.match(/\{\{ displayNumericText\(reason\) \}\}/g)||[]).length,1)
  assert.match(read('components/TopBar.vue'),/>急停<\/button>/)
  assert.match(read('components/TopBar.vue'),/不替代物理急停/)
})

test('real-connect request errors become visible store errors', async () => {
  const s = loadStore(async () => response(409, { why: '网卡未连接' }))
  const result = await s.world.connectReal('offline')
  assert.equal(result.ok, false)
  assert.match(s.world.error, /网卡未连接/)
})

test('unmount while boot requests are pending cannot reopen a stopped connection', async () => {
  const pending = deferred()
  const s = loadStore(async (path) => path === '/api/meta' ? pending.promise : response(200, { items: [] }))
  const boot = s.world.boot()
  s.world.disconnectWs()
  pending.resolve(response(200, {}))
  await boot
  assert.equal(s.sockets.length, 1, 'state connects before resources')
  assert.equal(s.sockets[0].closed, true, 'unmount closes the early connection')
  assert.equal(s.world.connected, false)
})

test('single explicit readiness click exchanges acknowledgement without modal or estop',async()=>{
  let acks=0,controls=0,stops=0
  const w=mockWorld({ackMotion:async flags=>{assert.deepEqual({...flags},{nobody:true,estop:true});acks++;return {ok:true,challenge:'once'}},
    control:async(key,token)=>{assert.equal(key,'power');assert.equal(token,'once');controls++;return {ok:true}},
    estop:async()=>{stops++;throw new Error('readiness must not call emergency stop')}})
  w.snap.ctrl.need_ack=true
  const c=ready(w);await c.press()
  assert.equal(acks,1);assert.equal(controls,1);assert.equal(stops,0);assert.equal(c.busy.value,false)
  assert.doesNotMatch(read('components/ReadyControl.vue'),/type="checkbox"|role="dialog"|w\.estop\(/)
})
test('readiness failures restore button and do not automatically retry',async()=>{
  let controls=0
  const w=mockWorld({control:async()=>{controls++;throw new Error('HTTP 503：不可用')}})
  const c=ready(w);await c.press()
  assert.equal(c.busy.value,false);assert.match(c.denied.value,/503/);assert.equal(controls,1)
  w.snap.ctrl.need_ack=true;w.ackMotion=async()=>{throw new Error('签发失败')}
  await c.press();assert.equal(c.denied.value,'签发失败');assert.equal(controls,1)
})
test('expired acknowledgement is shown inline, never reopens a dialog',async()=>{
  const w=mockWorld({control:()=>parseResponse(response(409,{why:'确认已过期',need_ack:true}),'/api/control')})
  const c=ready(w);await c.press();assert.match(c.denied.value,/确认已过期/);assert.equal(c.busy.value,false)
})
test('duplicate clicks cannot duplicate readiness requests',async()=>{
  const pending=deferred();let acks=0,controls=0
  const w=mockWorld({ackMotion:()=>{acks++;return pending.promise},control:async()=>{controls++;return {ok:true}}})
  w.snap.ctrl.need_ack=true
  const c=ready(w),first=c.press();await c.press()
  assert.equal(c.busy.value,true);assert.equal(acks,1)
  pending.resolve({ok:true,challenge:'once'});await first
  assert.equal(controls,1);assert.equal(c.busy.value,false)
})
test('state changes or unmount during readiness acknowledgement prevent commands',async()=>{
  for(const change of ['state','disconnect','unmount']){
    const pending=deferred();let controls=0
    const w=mockWorld({ackMotion:()=>pending.promise,control:()=>{controls++;return {ok:true}}})
    w.snap.ctrl.need_ack=true
    const c=ready(w),request=c.press()
    if(change==='state')w.snap.ctrl.primary.key='enable'
    if(change==='disconnect')w.connected=false
    if(change==='unmount')c.unmount()
    pending.resolve({ok:true,challenge:'once'});await request
    assert.equal(controls,0,change);assert.equal(c.busy.value,false)
  }
})
test('same button cancels an in-progress readiness, even during a disabled transition',async()=>{
  const calls=[],w=mockWorld({call:async(path,body)=>{calls.push(path);return {ok:true}}})
  w.snap.ctrl.ready_running=true;w.snap.ctrl.primary.enabled=false
  const c=ready(w);assert.equal(c.label.value,'取消就绪');await c.press()
  assert.deepEqual(calls,['/api/ready/cancel'])
})
test('old unconfirmed stop never makes readiness trigger emergency stop',async()=>{
  let stops=0,controls=0
  const w=mockWorld({estop:async()=>{stops++;return {ok:true}},control:async()=>{controls++;return {ok:false,why:'停止尚未确认'}}})
  w.snap.exec={stop_result:{settled:false}}
  const c=ready(w);await c.press()
  assert.equal(stops,0);assert.equal(controls,1);assert.equal(c.denied.value,'停止尚未确认')
})
test('read-only encoder display is independent of motion state and never substitutes simulation zeros',()=>{
  const s=state();s.joints.known=false;s.joints.q=[0,0,0,0,0,0]
  s.real.fsm=15;s.real.enabled=null;s.real.cmd_up=false;s.real.quality='stale'
  assert.deepEqual(displayJoints(s),[1,2,3,4,5,6])
  s.real.lag_ms=501;assert.equal(displayJoints(s),null)
  s.real.lag_ms=20;s.real.joints=null;assert.equal(displayJoints(s),null)
  s.real.joints=[1,2,3,4,5,NaN];assert.equal(displayJoints(s),null)
})

test('manual power rejection restores button; missing RTT cannot break success feedback', async () => {
  const w = mockWorld({ power: async () => { throw new Error('请求失败') } })
  const c = loadComponent('panels/PowerPanel.vue', w, ['act', 'busy', 'msg', 'failed'])
  await c.act('disable')
  assert.equal(c.busy.value, '')
  assert.equal(c.failed.value.disable, true)
  assert.equal(c.msg.value.disable, '请求失败')
  w.power = async () => ({ ok: true, reply: {} })
  await c.act('disable')
  assert.equal(c.failed.value.disable, false)
  assert.match(c.msg.value.disable, /^OK/)
})

test('reported real angles never fall back to simulation; last_command is separate from state error', () => {
  const w = mockWorld()
  w.snap.real.last_command = { name: 'Electrify', ok: false, err_code: 20018, fields: ['20018'], hint: '当前状态不允许', sent: true }
  w.snap.real.err_code = 0
  const c = loadComponent('panels/PowerPanel.vue', w, ['reportedAngles', 'reportAvailable', 'lastCommand', 'commandFields'])
  assert.deepEqual([...c.reportedAngles.value], [1, 2, 3, 4, 5, 6])
  assert.equal(c.lastCommand.value.err_code, 20018)
  assert.equal(c.commandFields.value, '20018')
  w.connected = false
  assert.equal(c.reportAvailable.value, false)
  w.connected = true; w.snap.real.joints = null
  assert.equal(c.reportAvailable.value, false)
  assert.equal(c.reportedAngles.value, null)
})

test('ReadEmergencyInfo uses [0] only; raw field order, zeros and missing values are preserved', async () => {
  let calls = 0
  const w = mockWorld({ askReal: async (name, params) => {
    calls++; assert.equal(name, 'ReadEmergencyInfo'); assert.deepEqual([...params], [0])
    return { ok: true, fields: ['0', '1'] }
  } })
  const c = loadComponent('panels/PowerPanel.vue', w,
    ['readEmergencyInfo', 'emergencyFields', 'EMERGENCY_FIELDS', 'emergencyBusy', 'emergencyError'])
  await c.readEmergencyInfo()
  assert.equal(calls, 1)
  assert.deepEqual([...c.EMERGENCY_FIELDS], ['nESTO_Error', 'nESTO', 'nSafeGuard_Error', 'nSafeGuard'])
  assert.deepEqual([...c.emergencyFields.value], ['0', '1'])
  assert.equal(c.emergencyFields.value[2], undefined)
  assert.equal(c.emergencyBusy.value, false)
  w.askReal = async () => { throw new Error('读取超时') }
  await c.readEmergencyInfo()
  assert.equal(c.emergencyBusy.value, false)
  assert.equal(c.emergencyError.value, '读取超时')
  w.connected = false
  await c.readEmergencyInfo()
  assert.equal(calls, 1)
})

test('HUD uses FK in sim even if stale real PCS remains, and hides disconnected pose', () => {
  const w = mockWorld()
  w.snap.real.pcs_base = [10, 20, 30, 40, 50, 60]
  const c = loadComponent('App.vue', w, ['poseKnown', 'poseShown', 'poseSrc'], appRouteFixture(w))
  assert.equal(c.poseShown.value.xyz_mm[0], 10)
  assert.equal(c.poseSrc.value, '控制器直读')
  w.snap.real.fsm = 24
  assert.equal(c.poseSrc.value, '控制器直读')
  w.snap.real.fsm = 99
  assert.equal(c.poseSrc.value, '控制器直读','FSM does not gate read-only pose display')
  w.snap.mode = 'sim'
  assert.equal(c.poseShown.value.xyz_mm[0], 0)
  assert.equal(c.poseSrc.value, 'FK 正解')
  w.connected = false
  assert.equal(c.poseKnown.value, false)
  assert.equal(isSixAxis([0, 0, 0, 0, 0, NaN]), false)
  assert.equal(isSixAxis([0, 0, 0, 0, 0, null]), false)
})

test('top bar shows actual controller override, never the saved requested setting', () => {
  const w = mockWorld()
  w.snap.speed_pct = w.snap.real_speed_pct = 5
  w.snap.real.override = .01
  w.snap.real.quality = 'valid'
  const c = loadComponent('components/TopBar.vue', w, ['speed'])
  assert.equal(c.speed.value, 1)
})

test('top bar speed notice is a standalone left sibling between brand and grow, outside the unboxed speed control', () => {
  const { descriptor, errors } = parse(read('components/TopBar.vue'))
  assert.deepEqual(errors, [])
  const hasClass = (node, name) => node.type === 1 && node.props.some((p) =>
    p.type === 6 && p.name === 'class' && p.value?.content.split(/\s+/).includes(name))
  const header = descriptor.template.ast.children.find((node) => hasClass(node, 'top'))
  assert.ok(header)
  const children = header.children.filter((node) => node.type === 1)
  const index = (name) => children.findIndex((node) => hasClass(node, name))
  const brand = index('brand'), note = index('speed-note'), grow = index('grow'), speed = index('speed')
  assert.ok(brand >= 0 && brand < note && note < grow && grow < speed,
    'the notice must be a direct header child on the left of the spacer and speed control')
  const notices = (node) => Number(hasClass(node, 'speed-note')) +
    (node.children || []).reduce((sum, child) => sum + notices(child), 0)
  assert.equal(notices(header), 1)
  assert.equal(notices(children[speed]), 0)
  assert.ok(children[note].props.some((p) => p.type === 6 && p.name === 'role' && p.value?.content === 'status'))
  assert.ok(children[note].props.some((p) => p.type === 7 && p.name === 'if' && p.exp?.content === 'speedNote'))
  const css = descriptor.styles.map((style) => style.content).join('\n')
  const speedRule = css.match(/\.speed\s*\{([^}]*)\}/)?.[1]
  assert.ok(speedRule)
  assert.match(speedRule, /border\s*:\s*(?:0(?:px)?|none)\s*;/)
  assert.match(speedRule, /background(?:-color)?\s*:\s*(?:none|transparent)\s*;/)
})

test('camera does not close from unrelated document pointer events', () => {
  const source = read('components/CameraWidget.vue')
  assert.doesNotMatch(source, /addEventListener\('pointerdown', outsideClick\)/)
  assert.match(source, /@click="closePanel"/)
})

test('real speed remains unknown on stale/missing data; pending or rejected targets never replace actual', () => {
  const s = state({speed_pct:5, real_speed_pct:5})
  s.real = {...s.real, quality:'valid', override:.01}
  assert.equal(speedState(s, true).actualPct, 1)
  assert.equal(speedState(s, true).mismatch, true)
  s.speed_ack = {target:'real',req:5,ok:null}
  assert.equal(speedState(s, true).pending, true)
  assert.equal(speedState(s, true).actualPct, 1)
  s.speed_ack = {target:'real',req:5,ok:false,why:'拒绝'}
  assert.equal(speedState(s, true).rejected, true)
  assert.equal(speedState(s, true).actualPct, 1)
  for (const invalid of [null, undefined, NaN, Infinity, '0.01']) {
    s.real.override = invalid
    assert.equal(speedState(s, true).actualPct, null)
  }
  s.real.override = .01; s.real.quality = 'stale'
  assert.equal(speedState(s, true).actualPct, null)
  s.real.quality = 'valid'
  assert.equal(speedState(s, false).actualPct, null)
  s.real.override = .05
  assert.equal(speedState(s, true).actualPct, 5)
  s.mode = 'sim'; s.speed_pct = 20
  assert.equal(speedState(s, true).actualPct, 20)
})

test('dragging only previews target; submit failure cannot change actual, mode change cancels submit', async () => {
  let calls = 0
  const w = mockWorld({setSpeed:async () => {calls++; return {ok:false,why:'设置被拒绝'}}})
  w.snap.real = {...w.snap.real, quality:'valid', override:.01}
  w.snap.real_speed_pct = 5
  const c = loadComponent('components/TopBar.vue', w, ['drag','commit','shown','speed','speedText','speedNote','message','busy'])
  c.drag({target:{value:'5'}})
  assert.equal(c.shown.value, 5)
  assert.equal(c.speed.value, 1)
  assert.equal(c.speedText.value, '1')
  assert.equal(c.speedNote.value, '目标 5%（未提交）')
  assert.equal(calls, 0)
  await c.commit({target:{value:'5'}})
  assert.equal(calls, 1)
  assert.equal(c.shown.value, 1)
  assert.equal(c.message.value, '设置被拒绝')
  assert.equal(c.speedNote.value, '设置被拒绝')
  assert.equal(c.speedText.value, '1')
  assert.equal(c.busy.value, false)
  c.drag({target:{value:'6'}}); w.snap.mode = 'sim'
  await c.commit({target:{value:'6'}})
  assert.equal(calls, 1)
})

test('left speed notice persists while submitting and after a request error without showing target 5 as actual', async () => {
  const request = deferred(), targets = []
  const w = mockWorld({ setSpeed: (value) => { targets.push(value); return request.promise } })
  w.snap.real = { ...w.snap.real, quality:'valid', override:.01 }
  w.snap.real_speed_pct = 5
  const c = loadComponent('components/TopBar.vue', w, ['drag','commit','shown','speed','speedText','speedNote','busy'])
  c.drag({target:{value:'5'}})
  const pending = c.commit({target:{value:'5'}})
  assert.deepEqual(targets, [5])
  assert.equal(c.busy.value, true)
  assert.equal(c.speedNote.value, '正在提交目标 5%')
  assert.equal(c.speed.value, 1)
  assert.equal(c.speedText.value, '1')
  request.reject(new Error('离线模拟请求失败'))
  await pending
  assert.equal(c.busy.value, false)
  assert.equal(c.speedNote.value, '离线模拟请求失败')
  assert.equal(c.speedText.value, '1')
  assert.equal(c.shown.value, 1)
})

test('unknown speed keeps its left notice and -- display instead of falling back to stored 5 percent', () => {
  const cases = [
    ['missing override', (w) => { delete w.snap.real.override }],
    ['stale state', (w) => { w.snap.real.quality = 'stale' }],
    ['controller stream down', (w) => { w.snap.real.stream_up = false }],
    ['disconnected', (w) => { w.connected = false }],
    ['missing snapshot', (w) => { w.snap = null }],
    ['pending target', (w) => { w.snap.real.override = null; w.snap.speed_ack = {target:'real',req:5,ok:null} }],
    ['rejected target', (w) => { w.snap.real.override = null; w.snap.speed_ack = {target:'real',req:5,ok:false,why:'目标被拒绝'} }],
  ]
  for (const [name, invalidate] of cases) {
    const w = mockWorld({ setSpeed: () => { assert.fail('unknown state checks must not submit speed') } })
    w.snap.speed_pct = w.snap.real_speed_pct = 5
    w.snap.real = { ...w.snap.real, quality:'valid', override:.05 }
    invalidate(w)
    const c = loadComponent('components/TopBar.vue', w, ['speed','speedText','speedNote'])
    assert.equal(c.speed.value, null, name)
    assert.equal(c.speedText.value, '--', name)
    assert.match(c.speedNote.value, /实际倍率未知/, name)
    assert.doesNotMatch(c.speedNote.value, /5%/, name)
  }
})

test('camera errors stay local and never create a duplicate top banner or overwrite unrelated errors', async () => {
  let result = {ok:false,why:'相机打开失败'}
  const {world} = loadStore(async () => response(200,result))
  await world.openCamera('test',['color'])
  assert.equal(world.error, '')
  assert.equal(world.cameras.error, '相机打开失败')
  result = {ok:true,active:{state:'streaming'},error:''}
  await world.openCamera('test',['color'])
  assert.equal(world.error, '')
  world.error = '与摄像头无关的错误'
  await world.openCamera('test',['color'])
  assert.equal(world.error, '与摄像头无关的错误')
})

test('Vue scripts/templates/styles compile in memory without building dist', () => {
  for (const filename of ['App.vue', 'components/TopBar.vue', 'components/ModelLibrary.vue', 'components/StatusStrip.vue', 'components/ReadyControl.vue', 'components/CameraWidget.vue', 'components/CameraFrame.vue', 'panels/PowerPanel.vue', 'panels/PointsPanel.vue']) {
    const source = read(filename)
    const { descriptor, errors } = parse(source, { filename })
    assert.deepEqual(errors, [], filename)
    const script = compileScript(descriptor, { id: 'offline-check' })
    const template = compileTemplate({ source: descriptor.template.content, filename, id: 'offline-check',
      compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(template.errors, [], filename)
    for (const style of descriptor.styles) {
      assert.deepEqual(compileStyle({ source: style.content, filename, id: 'offline-check', scoped: style.scoped }).errors, [], filename)
    }
    if (filename.includes('ReadyControl')) {
      assert.doesNotMatch(source, /<Teleport|role="dialog"|type="checkbox"/)
      assert.equal((descriptor.template.content.match(/<button\b/g) || []).length,1,'readiness and cancellation share one button')
    }
  }
})

test('stored point preview stays in storage and never calls movement; invalid points rejected', () => {
  const calls = []
  const w = mockWorld({ points: [], setPreview: (q) => calls.push([...q]), moveJ: () => { throw new Error('Must not move') } })
  const c = loadComponent('panels/PointsPanel.vue', w, ['pointTab','previewPoint','goPoint','tgt'])
  c.pointTab.value = 'storage'
  c.previewPoint({ id: 'p2', joints: [10,20,30,40,50,60] })
  assert.deepEqual(calls, [[10,20,30,40,50,60]])
  assert.equal(c.pointTab.value, 'storage')
  c.goPoint({id:'load-only',joints:[1,2,3,4,5,6]})
  assert.deepEqual(calls.at(-1),[1,2,3,4,5,6]);assert.equal(c.pointTab.value,'execute')
  c.previewPoint({ id: 'invalid', joints: [NaN,0,0,0,0,0] })
  assert.equal(calls.length, 2)
  assert.match(w.error, /角度无效/)
  c.unmount()
})

test('Cartesian buttons generate preview only with separate mm/degree steps', async () => {
  const calls = [], previews = []
  const w = mockWorld({ points: [], setPreview: (q) => previews.push([...q]),
    ik: async (pose) => { calls.push([...pose]); return {ok:true,joints:[1,2,3,4,5,6],achieved:pose} },
    moveJ: () => { throw new Error('Must not move') } })
  const c = loadComponent('panels/PointsPanel.vue', w, ['cartMode','cartTarget','stepCart','linearStep','angularStep','previewCart'])
  c.cartMode.value = true; c.linearStep.value = 5; c.angularStep.value = .5
  c.stepCart(0, 1); c.stepCart(5, -1)
  await c.previewCart()
  assert.deepEqual(calls, [[5,0,1195,0,0,-.5]])
  assert.deepEqual(previews, [[1,2,3,4,5,6]])
  const source = read('panels/PointsPanel.vue')
  assert.doesNotMatch(source.slice(source.indexOf('<div v-else class="cart-buttons">'), source.indexOf('async function teach')), /slideCart/)
  c.unmount()
})

test('vacuum reference has correct declared mm dimensions and honest provenance', () => {
  const d = vacuumDimensions()
  assert.equal(d.spacing, 280)
  const root = buildVacuumReference(DEFAULT_VACUUM)
  root.updateMatrixWorld(true)
  const box = new THREE.Box3().setFromObject(root)
  const size = box.getSize(new THREE.Vector3())
  for (const [actual, expected] of [[size.x,.3],[size.y,.05],[size.z,.08]]) assert.ok(Math.abs(actual-expected)<1e-7)
  assert.equal(root.userData.referenceOnly, true)
  assert.throws(() => vacuumDimensions({ length: Infinity }))
  assert.throws(() => vacuumDimensions({ edge: 0 }))
  assert.throws(() => robotAsset('../outside'))
  disposeAsset(root)
})

test('all official robot asset references and redistributed licenses exist', () => {
  for (const model of ROBOT_MODELS) {
    const base = new URL(`../public${model.base}`, import.meta.url)
    const spec = JSON.parse(readFileSync(new URL('robot.json', base), 'utf8'))
    assert.equal(spec.variant, model.id)
    assert.equal(spec.joints.filter((j) => j.limit).length, 6)
    assert.match(readFileSync(new URL('LICENSE.txt', base), 'utf8'), /BSD 3-Clause/)
    for (const link of Object.values(spec.links)) if (link.visual) {
      assert.ok(readFileSync(new URL(link.visual.mesh, base)).length > 84)
      assert.ok(readFileSync(new URL(link.visual.mesh.replace(/\.STL$/i,'.glb'), base)).length > 20)
    }
  }
})

test('import rejects unsafe extensions, unknown units and oversized tool files', async () => {
  await assert.rejects(readToolFile({ name:'tool.obj', size:20, arrayBuffer:async()=>new ArrayBuffer(20) },'mm'), /只支持/)
  await assert.rejects(readToolFile({ size:26*1024*1024 },'mm'), /25 MB/)
  await assert.rejects(readToolFile({ size:20 },'cm'), /单位/)
})

test('fingertip projection respects letterboxing and rejects invalid landmarks', () => {
  assert.deepEqual(fingertipInView({x:.5,y:.5},400,300,1920,1080),{x:200,y:150})
  assert.deepEqual(fingertipInView({x:0,y:0},400,300,1920,1080),{x:0,y:37.5})
  assert.equal(fingertipInView({x:NaN,y:0},400,300,640,480),null)
  assert.equal(fingertipInView({x:1.1,y:0},400,300,640,480),null)
})

test('expanded camera removes trigger and keeps only one capture toggle and one error notice', () => {
  const source = read('components/CameraWidget.vue')
  assert.match(source, /<button v-if="!open" ref="trigger"/)
  assert.equal((source.match(/@click="toggleCamera"/g)||[]).length,1)
  assert.equal((source.match(/v-if="notice"/g)||[]).length,1)
  assert.match(source,/dock\.top - 8/)
  assert.doesNotMatch(source,/w\.(moveJ|jog|askReal|toolAction)/)
})

test('refresh boot and reconnect only read metadata and state, with no automatic control requests', async () => {
  const requests = []
  const s = loadStore(async (path, options) => {
    requests.push({ path, method: options?.method || 'GET' })
    assert.equal(options?.method || 'GET', 'GET', `Refresh must not write ${path}`)
    assert.ok(['/api/meta', '/api/points', '/api/programs', '/api/tools', '/api/cameras', '/api/scenes'].includes(path))
    return response(200, { items: [], mounted: null, tool: null })
  })
  try {
    await s.world.boot()
    assert.equal(requests.length, 6)
    const first = s.sockets[0]
    first.onopen({})
    s.send(state({ preview: [1, 2, 3, 4, 5, 6], resume: { state: 'running', left: 1 } }))
    first.onclose({}); s.tick(); s.sockets[1].onopen({})
    s.send(state())
    assert.equal(s.sockets.length, 2)
    assert.equal(requests.length, 6)
    assert.equal(s.world.receivedFrames, 1)
    assert.equal(s.timers.size, 0)
  } finally { s.world.disconnectWs() }
})

test('preview and cancel use only preview endpoint without changing reported pose or actual tool', async () => {
  const requests = []
  const s = loadStore(async (path, options) => {
    requests.push({ path, body: JSON.parse(options.body) })
    assert.equal(path, '/api/preview')
    return response(200, { ok: true })
  })
  s.world.snap = state()
  s.world.connected = true; s.world.stateFresh = true
  s.world.tools = { mounted: { tool_id: null, state: 'unknown' }, tool: null }
  const actual = JSON.stringify({ snap: s.world.snap, tools: s.world.tools })
  const target = [10, 20, 30, 40, 50, 60]
  const preview = s.world.setPreview(target)
  target[0] = 90
  assert.equal(s.world.preview[0], 10)
  const cancel = s.world.cancelPreview()
  await Promise.all([preview, cancel])
  assert.deepEqual(requests, [
    { path: '/api/preview', body: { joints: [10, 20, 30, 40, 50, 60] } },
    { path: '/api/preview', body: { joints: null } },
  ])
  assert.equal(s.world.preview, null)
  assert.equal(JSON.stringify({ snap: s.world.snap, tools: s.world.tools }), actual)
})

test('default and changed visual tool only call the scene and local preferences, never actual TCP or IO', async () => {
  const calls = []
  const s = loadStore()
  s.world.snap = state()
  s.world.tools = { mounted: { state: 'unknown', tool_id: null }, tool: null }
  const before = JSON.stringify({ snap: s.world.snap, tools: s.world.tools })
  const c = loadComponent('App.vue', s.world, ['visualToolName', 'selectVisualTool', 'sceneLoading'], {
    ...appRouteFixture(s.world),
    testScene: { setToolVisual: async (value) => calls.push(value) },
  })
  assert.match(c.visualToolName.value, /双吸盘.*参考.*待核/)
  assert.equal(s.world.tools.mounted.tool_id, null)
  c.run('scene = testScene; sceneLoading.value = false')
  const reference = { kind: 'vacuum-reference', dimensions: { ...DEFAULT_VACUUM }, name: 'offline display' }
  await c.selectVisualTool(reference)
  assert.equal(calls.length, 1)
  assert.equal(calls[0], reference)
  assert.equal(c.visualToolName.value, 'offline display')
  assert.equal(c.preferences.get('huayan.visualTool'), JSON.stringify(reference))
  await c.selectVisualTool(null)
  assert.equal(calls[1], null)
  assert.equal(c.visualToolName.value, '')
  assert.equal(c.preferences.has('huayan.visualTool'), false)
  assert.equal(JSON.stringify({ snap: s.world.snap, tools: s.world.tools }), before)
})

test('empty or duplicate log deltas retain the same reactive log array', () => {
  const s = loadStore()
  s.world.connectWs(); s.sockets[0].onopen({})
  try {
    s.send(state({ log_delta: { gap: false, seq: 1, rows: [{ seq: 1, text: '已有日志' }] } }))
    const rows = s.world.logs
    for (let i = 0; i < 30; i++) s.send(state({ log_delta: { gap: false, seq: 1, rows: [] } }))
    assert.equal(s.world.logs, rows, '无新日志不能重建数组并触发整段日志重绘')
    s.send(state({ log_delta: { gap: false, seq: 1, rows: [{ seq: 1, text: '重复' }] } }))
    assert.equal(s.world.logs, rows)
    s.send(state({ log_delta: { gap: true, seq: 2, rows: [] } }))
    assert.equal(s.world.logs.length, 0, '真实缺口仍必须清掉旧段')
  } finally { s.world.disconnectWs() }
})

test('duplicate sequences within one log delta are deduplicated without dropping state', () => {
  const s = loadStore()
  s.world.connectWs(); s.sockets[0].onopen({})
  try {
    s.send(state({ log_delta: { gap: false, seq: 2, rows: [
      { seq: 1, text: '一' }, { seq: 1, text: '重复' }, { seq: 2, text: '二' },
    ] } }))
    assert.deepEqual(Array.from(s.world.logs, row => row.seq), [1, 2])
    assert.equal(s.world.receivedFrames, 1)
  } finally { s.world.disconnectWs() }
})

test('many render failures still deliver newest state and bounded deduplicated logs', () => {
  const s = loadStore()
  s.world.connectWs(); s.sockets[0].onopen({})
  let frames = 0
  s.setFrameHandler(() => { frames++; throw new Error('decorative ring failed') })
  try {
    for (let seq = 1; seq <= 2100; seq++) {
      s.send(state({ real: { frame_seq: seq }, telemetry: { seq }, telemetry_seq: seq,
        log_delta: { gap: false, seq, rows: [{ seq, text: 'frame ' + seq }] } }))
    }
    assert.equal(frames, 2100)
    assert.equal(s.world.snap.real.frame_seq, 2100)
    assert.equal(s.world.tele.seq, 2100)
    assert.equal(s.world.receivedFrames, 2100)
    assert.equal(s.world.logs.length, 2000)
    assert.equal(s.world.logs[0].seq, 101)
    s.send(state({ log_delta: { gap: false, seq: 2100, rows: [{ seq: 2100, text: 'duplicate' }] } }))
    assert.equal(s.world.logs.length, 2000)
    s.send(state({ log_delta: { gap: true, seq: 2500, rows: [{ seq: 2500, text: 'new segment' }] } }))
    assert.equal(s.world.logs.length, 1)
    assert.equal(s.world.logs[0].seq, 2500)
  } finally { s.setFrameHandler(null); s.world.disconnectWs() }
})

test('camera overlay consumes workstation observations with no local inference or device commands',async()=>{
 const source=read('components/CameraFrame.vue')
 assert.doesNotMatch(source,/(?:setFrameHandler\s*\(|latestFrame\s*\(|useWorld\s*\(|\/api\/(?:movej|jog|free|power|control|tools\/action))/)
 assert.match(source,/\.palm-marker\s*\{[^}]*pointer-events:\s*none/s)
 assert.doesNotMatch(source,/new Worker|createImageBitmap|postMessage/)
 assert.match(source,/handPipeline\.subscribe/)
 const {handPipelineObservation}=await import('../src/handPipeline.js')
 const landmarks=Array.from({length:21},()=>({x:.5,y:.3}))
 const packet={requestStartedAt:1000,receivedAt:1000,snapshot:{enabled:true,source:'local',session:'current',epoch:3,server_now:5,
  result:{source:'local',session:'current',epoch:3,seq:7,coords:'image_normalized',captured_at:4.95,received_at:4.96,valid_until:5.25,valid:true,control_valid:true,clock_domain:'server',observations:[{kind:'hand',landmarks}]}}}
 const first=handPipelineObservation(packet,'current',1000),later=handPipelineObservation(packet,'current',1100)
 assert.equal(first.frameSeq,7);assert.equal(first.controlValid,true);assert.equal(first.timestamp,later.timestamp)
 assert.equal(handPipelineObservation(packet,'old',1000).controlValid,false)
 assert.equal(handPipelineObservation(packet,'current',1400).controlValid,false)
})

test('mirrored hand projection and depth pixel inversion agree including letterbox and image edges', () => {
  const geometry = [400, 300, 1920, 1080]
  assert.deepEqual(fingertipInView({x:0,y:.5}, ...geometry, true), {x:400,y:150})
  assert.deepEqual(fingertipInView({x:1,y:.5}, ...geometry, true), {x:0,y:150})
  for (const x of [.1, .25, .5, .75, .9]) {
    const point = fingertipInView({x,y:.5}, ...geometry, true)
    const pixel = imagePixelAt(point.x, point.y, ...geometry, true)
    assert.ok(Math.abs(pixel.x - Math.floor(x * 1920)) <= 1)
    assert.equal(pixel.y, 540)
  }
  assert.deepEqual(imagePixelAt(0, 150, ...geometry, true), {x:1919,y:540})
  assert.equal(imagePixelAt(400, 150, ...geometry, true), null)
  assert.equal(imagePixelAt(200, 0, ...geometry, true), null)
  assert.equal(imagePixelAt(200, 300, ...geometry, true), null)
  assert.equal(imagePixelAt(NaN, 150, ...geometry, true), null)
})

test('top bar never presents startup target five as actual on disconnect, unknown or stale readback', () => {
  const w = mockWorld()
  w.snap.real_speed_pct = 5
  w.snap.speed_pct = 5
  w.snap.real.quality = 'valid'
  w.snap.real.override = .01
  w.snap.speed_ack = {target:'real', req:5, ok:true}
  const c = loadComponent('components/TopBar.vue', w, ['speed', 'speedText', 'speedNote', 'speedBad'])
  assert.equal(c.speed.value, 1)
  assert.equal(c.speedText.value, '1')
  const original = {...w.snap.real}
  const cases = [
    {connected:false, real:original},
    {connected:true, real:{...original, stream_up:false}},
    {connected:true, real:{...original, quality:'stale', override:.05}},
    {connected:true, real:{...original, quality:'unknown', override:.05}},
    {connected:true, real:{...original, override:null}},
    {connected:true, real:null},
  ]
  for (const value of cases) {
    w.connected = value.connected; w.snap.real = value.real
    assert.equal(c.speed.value, null, JSON.stringify(value))
    assert.equal(c.speedText.value, '--')
    assert.equal(c.speedBad.value, true)
    assert.match(c.speedNote.value, /实际倍率未知/)
  }
  w.connected = true; w.snap.real = {...original, override:.05}
  assert.equal(c.speed.value, 5)
  assert.equal(c.speedText.value, '5')
  w.snap.real.override = .075
  assert.equal(c.speed.value, 7.5)
  assert.equal(c.speedText.value, '7.5')
  w.snap.mode = 'sim'; w.snap.speed_pct = 43.5
  assert.equal(c.speedText.value, '43.5')
})


test('open socket with no new frames expires readiness and targets; new feedback does not replay a target',()=>{
 const jobs=new Map();let n=0
 const s=loadStore(undefined,{setTimeout:fn=>{jobs.set(++n,fn);return n},clearTimeout:key=>jobs.delete(key)})
 s.world.connectWs();s.sockets[0].onopen({});s.send(state())
 assert.equal(s.world.stateFresh,true);assert.equal(s.world.canPrimary,true);assert.equal(jobs.size,1)
 s.send(state());assert.equal(jobs.size,1,'old expiry is replaced')
 for(const fn of [...jobs.values()])fn()
 assert.equal(s.world.stateFresh,false);assert.equal(s.world.canPrimary,false);assert.equal(s.world.level,'unknown')
 s.send(state());assert.equal(s.world.stateFresh,true)
 s.world.disconnectWs();assert.equal(jobs.size,0);assert.equal(s.world.stateFresh,false)
})

test('tool action posts explicit side, rejects duplicate same-side requests and never claims rejected actions succeeded',async()=>{
 const pending=deferred(),requests=[]
 const s=loadStore(async(path,options)=>{requests.push([path,JSON.parse(options.body)]);return pending.promise})
 const action=s.world.toolAction('vacuum_on',0,'left')
 assert.equal((await s.world.toolAction('blow_on',0,'left')).ok,false)
 assert.equal(requests.length,1);assert.deepEqual(requests[0],[ '/api/tools/action',{action:'vacuum_on',duration_ms:0,side:'left'}])
 pending.resolve(response(200,{ok:false,why:'mapping missing'}));assert.equal((await action).ok,false)
 assert.match(s.world.error,/mapping missing/);assert.equal(Object.keys(s.world.toolPending).length,0)
})


test('unrelated successful requests do not clear an actionable error; the same source can recover',async()=>{
 let broken=true
 const s=loadStore(async path=>response(200,path==='/api/io/enddo' && broken?{ok:false,why:'output unknown'}:{ok:true}))
 await s.world.call('/api/io/enddo',{});assert.equal(s.world.error,'output unknown')
 await s.world.call('/api/speed',{});assert.equal(s.world.error,'output unknown')
 broken=false;await s.world.call('/api/io/enddo',{});assert.equal(s.world.error,'')
 s.world.error='manual safety issue';await s.world.call('/api/io/enddo',{});assert.equal(s.world.error,'manual safety issue')
})
test('older same-source success cannot erase the newest failure',async()=>{
 const old=deferred();let calls=0
 const s=loadStore(async()=>++calls===1?old.promise:response(200,{ok:false,why:'new failure'}))
 const first=s.world.call('/api/settings',{});await s.world.call('/api/settings',{})
 old.resolve(response(200,{ok:true}));await first
 assert.equal(s.world.error,'new failure')
})


test('sorting route survives navigation normalization and legacy follow remains meaningful',()=>{
 assert.equal(normalizeNavigation({view:'sorting'}).view,'scene')
 assert.equal(normalizeNavigation({view:'hand-follow'}).view,'interaction')
})
