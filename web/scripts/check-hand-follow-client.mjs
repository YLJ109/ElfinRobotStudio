import {frontTaskView} from '../src/taskFrontView.js'
import {createFixtureCameraSource} from './fixture-camera-source.mjs'
import * as interactionRanges from '../src/interactionRange.js'
// Offline only: fake HTTP, camera landmarks, snapshots and component lifecycle.
// Run from any directory: node web/scripts/check-hand-follow-client.mjs
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { computed, ref, reactive, watch, effectScope } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as follow from '../src/handFollowInput.js'
import { createMouseFollowInput } from '../src/mouseFollowInput.js'
import { createInteractionInput, interactionModeIssue } from '../src/interactionInput.js'
import { imageBoundsInView, fingertipInView } from '../src/handGeometry.js'

const read = path => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
const response = body => ({ ok:true, status:200, json:async () => body })
test('panel emits its workspace preview before any configure/start request', t => {
  const h = panelFixture(t)
  assert.equal(h.requests.length, 0)
  assert.deepEqual(h.emitted.find(e => e[0] === 'box-preview')[1],
    {min:[-950,-150,450],max:[-50,150,950]})
})
function deferred() {
  let resolve, reject
  const promise = new Promise((yes,no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const landmark = { x:.2, y:.7, palm_scale:.12, dx:.2, dy:.1 }
const frame = (time = 1000, patch = {}) => ({ session:'camera-a', stream:'color',
  hands:true, fresh:true, width:640, height:480, frameSeq:Math.max(1,Math.round(time*1000)),
  frameUpdatedAt:time, timestamp:time, point:{ ...landmark }, ...patch })
const payload = { session:'camera-a', aspect:4/3, depth:false, rotation:false }
const state = (active = false, token = active ? 'running' : 'configured') => ({
  token, session:'camera-a', configured:true, active, actual:[11,22,33,0,0,0],
})
function clientFixture(t, handler, options = {}) {
  let time = 1000, issue = ''
  const calls = []
  const fetcher = async (path, requestOptions) => {
    assert.ok(path.startsWith((options.endpoint || '/api/sim/hand-follow') + '/'))
    const action = path.split('/').at(-1), body = JSON.parse(requestOptions.body)
    calls.push({ path, action, body, options:requestOptions })
    const custom = handler?.(action, body, requestOptions)
    if (custom) return await custom
    return response({ ok:true, state:state(action === 'start' || action === 'sample') })
  }
  const client = follow.createHandFollowClient({ fetcher, now:() => time, guard:() => issue, ...options })
  t.after(() => client.dispose())
  return { client, calls, setTime:v => { time = v }, setIssue:v => { issue = v },
    async arm() {
      assert.equal(await client.configure(payload), true)
      assert.equal(await client.start('camera-a', options.endpoint === '/api/real/hand-follow' ? {confirm_real:true} : undefined), true)
    } }
}

// The original eighteen input/network cases.
test('input 01: old camera producer cannot clear the newer producer', t => {
  const a = Symbol(), b = Symbol(); let updates = 0
  const off = follow.subscribeHandFollowInput(() => updates++)
  t.after(() => { off(); follow.publishHandFollowInput(null,b) })
  follow.publishHandFollowInput(frame(),a)
  follow.publishHandFollowInput(frame(1100),b)
  follow.publishHandFollowInput(null,a)
  assert.equal(follow.getHandFollowInput().timestamp,1100)
  follow.publishHandFollowInput(null,b)
  assert.equal(follow.getHandFollowInput(),null)
  assert.equal(updates,4)
})
test('input 02: visual-window request listener is removable', () => {
  let n = 0
  const off = follow.onHandFollowVisionRequest(() => n++)
  const registration = n // an earlier mounted panel may already have requested vision
  follow.requestHandFollowVision(); assert.equal(n,registration+1)
  off(); follow.requestHandFollowVision()
  assert.equal(n,registration+1)
})
test('input 03: frame readiness and palm presence are separate contracts', () => {
  const value = frame(1000,{ point:null })
  assert.equal(follow.handFollowFrameIssue(value,1000),'')
  assert.ok(follow.handFollowInputIssue(value,1000))
})
test('configure trusts camera producer freshness without a duplicate 1s timestamp gate', () => {
  assert.equal(follow.handFollowFrameIssue(frame(1000,{point:null,frameUpdatedAt:1}),2000),'')
  assert.equal(follow.handFollowInputIssue(frame(1000,{frameUpdatedAt:1}),1000),'')
})
test('input 04: reject stale/future/nonfinite/outside/degenerate landmarks', () => {
  for (const value of [frame(600),frame(1100),frame(1000,{point:{x:NaN}}),
    frame(1000,{point:{...landmark,x:1.2}}),frame(1000,{point:{...landmark,dx:0,dy:0}})]) {
    assert.ok(follow.handFollowInputIssue(value,1000))
  }
  assert.equal(follow.handFollowInputIssue(frame(),1000),'')
})
test('input 05: reject disabled detection, stale frame, non-color and invalid dimensions', () => {
  for (const patch of [{hands:false},{fresh:false},{stream:'depth'},{width:0},{frameSeq:0}]) {
    assert.ok(follow.handFollowInputIssue(frame(1000,patch),1000))
  }
})
test('input 06: refresh/new client never restores token or sends requests', t => {
  const h = clientFixture(t)
  assert.equal(h.calls.length,0)
  assert.equal(h.client.getState().active,false)
  assert.equal(h.client.getState().configured,false)
})
test('input 07: explicit configure/start acquires the newly rotated token', async t => {
  const h = clientFixture(t); await h.arm()
  assert.equal(h.calls[1].body.token,'configured')
  assert.equal(h.client.getState().server.token,'running')
})
test('input 08: sample contract mirrors once, computes Rz and age, uses owned token', async t => {
  const h = clientFixture(t); await h.arm(); h.setTime(1025)
  await h.client.sample(frame())
  const body = h.calls.find(c => c.action === 'sample').body
  assert.deepEqual(Object.keys(body).sort(),['token','session','seq','u','v','palm_scale','angle','age_ms','hand_present','frame_seq'].sort())
  assert.equal(body.u,.2); assert.equal(body.v,.7); assert.equal(body.token,'running')
  assert.equal(body.hand_present,true)
  assert.equal(body.seq,1); assert.equal(body.age_ms,25)
  assert.equal(body.frame_seq,frame().frameSeq)
  assert.ok(Math.abs(body.angle - Math.atan2(.1,.2)*180/Math.PI) < 1e-10)
})
test('display-space palm scale and direction pass through without a hidden second mirror', async t => {
  const h=clientFixture(t); await h.arm(); await h.client.sample(frame(1000,{point:{...landmark,dx:-.2}}))
  const body=h.calls.find(c=>c.action==='sample').body
  assert.equal(body.u,.2); assert.equal(body.v,.7); assert.equal(body.palm_scale,.12)
  assert.ok(Math.abs(body.angle-Math.atan2(.1,-.2)*180/Math.PI)<1e-10)
})

test('held palm does not masquerade as new captures; expiration explicitly sends absence and center', async t => {
  const h=clientFixture(t);await h.arm();await h.client.sample(frame())
  h.setTime(1067)
  await h.client.sample(frame(1000,{trackingState:'holding'}))
  assert.equal(h.calls.filter(c=>c.action==='sample').length,1)
  h.setTime(1220)
  await h.client.sample(frame(1220,{point:null,trackingState:'lost'}))
  const sent=h.calls.filter(c=>c.action==='sample').at(-1).body
  assert.deepEqual([sent.u,sent.v,sent.hand_present],[.5,.5,false])
  assert.equal(h.client.getState().active,true)
})

test('仿真相机短时漏检按后台桥接状态展示，不误报已经回中', async t => {
  const h = clientFixture(t, action => action === 'sample' ? response({ok:true,
    state:{...state(true),input_phase:'bridging',waiting:false,returning:false}}) : null)
  await h.arm(); await h.client.sample(frame())
  h.setTime(1350); await h.client.sample(frame(1000,{trackingState:'holding'}))
  const sent = h.calls.filter(c=>c.action==='sample').at(-1).body
  assert.equal(sent.hand_present,false,'过期位置仍不得当成新观测')
  assert.equal('frame_seq' in sent,false)
  assert.equal(h.client.getState().waiting,false)
  assert.equal(h.client.getState().why,'')
  assert.equal(h.client.getState().active,true)
})
test('configure forwards an optional calibrated depth range but keeps legacy bodies valid', async t => {
  const h=clientFixture(t)
  await h.client.configure({...payload,depth_range:[.06,.30]})
  assert.deepEqual(h.calls.find(c=>c.action==='configure').body.depth_range,[.06,.30])
  h.client.stop();await h.client.configure(payload)
  assert.equal(Object.hasOwn(h.calls.filter(c=>c.action==='configure').at(-1).body,'depth_range'),false)
})

test('gesture sample forwards zero openness without substituting palm scale', async t => {
  const h=clientFixture(t);await h.arm()
  await h.client.sample(frame(1000,{point:{...landmark,hand_open:0}}))
  assert.equal(h.calls.filter(c=>c.action==='sample').at(-1).body.hand_open,0)
})

test('input 09: fresh 20fps observations are not capped at 10Hz; duplicate capture IDs are ignored', async t => {
  const h = clientFixture(t); await h.arm(); await h.client.sample(frame())
  h.setTime(1050); await h.client.sample(frame(1050))
  h.setTime(1100); await h.client.sample(frame(1100,{frameSeq:1050000})); await h.client.sample(frame(1100))
  const sent = h.calls.filter(c => c.action === 'sample')
  assert.equal(sent.length,3); assert.deepEqual(sent.map(c => c.body.seq),[1,2,3])
})

test('真实采集时间与无手心跳分开排序：9fps延迟检测不能被30fps心跳永久压住',async t=>{
  const h=clientFixture(t);await h.arm()
  await h.client.sample(frame(1000,{point:null,trackingState:'lost'}))
  for(let index=0;index<6;index++){
    const heartbeatAt=1100+index*120
    h.setTime(heartbeatAt)
    await h.client.sample(frame(heartbeatAt,{point:null,trackingState:'lost'}))
    h.setTime(heartbeatAt+40)
    await h.client.sample(frame(heartbeatAt-70,{frameSeq:index+1}))
  }
  const present=h.calls.filter(call=>call.action==='sample' && call.body.hand_present)
  assert.equal(present.length,6,'每张不同的有效检测都应发送，不受此前缺失心跳的发送时间干扰')
  assert.ok(present.every(call=>call.body.age_ms===110))
})
test('input 10: one sample in flight, drop intermediate values and use latest next', async t => {
  const pending = deferred(); let n = 0
  const h = clientFixture(t,action => action === 'sample' && ++n === 1 ? pending.promise : null)
  await h.arm(); const first = h.client.sample(frame())
  h.setTime(1100); await h.client.sample(frame(1100))
  h.setTime(1200); await h.client.sample(frame(1200)); assert.equal(n,1)
  pending.resolve(response({ok:true,state:state(true)})); await first
  await h.client.sample(frame(1200,{point:{...landmark,x:.9}}))
  assert.equal(n,2)
  assert.ok(Math.abs(h.calls.filter(c => c.action === 'sample')[1].body.u-.9) < 1e-10)
})
test('input 11: late configure after stop is retired and never acquires ownership', async t => {
  const pending = deferred()
  const h = clientFixture(t,a => a === 'configure' ? pending.promise : null)
  const configured = h.client.configure(payload); h.client.stop('leave')
  pending.resolve(response({ok:true,state:state(false,'late-config')}))
  assert.equal(await configured,false)
  assert.equal(h.client.getState().configured,false)
  assert.ok(h.calls.some(c => c.action === 'stop' && c.body.token === 'late-config'))
})
test('input 12: late start after stop is retired using the NEW returned token', async t => {
  const pending = deferred()
  const h = clientFixture(t,a => a === 'start' ? pending.promise : null)
  await h.client.configure(payload)
  const started = h.client.start('camera-a'); h.client.stop('hidden')
  pending.resolve(response({ok:true,state:state(true,'late-start')})); await started
  assert.equal(h.client.getState().active,false)
  assert.ok(h.calls.some(c => c.action === 'stop' && c.body.token === 'late-start'))
})
test('input 13: rejected reply cannot acquire or retire another client token', async t => {
  const h = clientFixture(t,a => a === 'configure' ? Promise.resolve(response({ok:false,state:state(true,'other')})) : null)
  await h.client.configure(payload)
  assert.equal(h.client.getState().active,false)
  assert.equal(h.calls.some(c => c.action === 'stop' && c.body.token === 'other'),false)
})
test('input 14: guard rejects start and only dedicated simulation APIs are used', async t => {
  const h = clientFixture(t); await h.client.configure(payload)
  h.setIssue('real connected'); assert.equal(await h.client.start('camera-a'),false)
  assert.equal(h.calls.some(c => c.action === 'start'),false)
  assert.ok(h.calls.every(c => ['configure','start','sample','stop'].includes(c.action)))
})
test('input 15: camera loss or foreign camera waits on the owned token, without importing its palm', async t => {
  for (const value of [null,frame(1000,{session:'camera-b'}),frame(1000,{fresh:false})]) {
    const h = clientFixture(t); await h.arm(); await h.client.sample(value)
    assert.equal(h.client.getState().active,true)
    assert.equal(h.client.getState().waiting,true)
    const sent=h.calls.find(c=>c.action==='sample').body
    assert.equal(sent.hand_present,false);assert.equal(sent.session,'camera-a')
    assert.equal(sent.token,'running')
    assert.equal(h.calls.some(c => c.action === 'stop'),false)
  }
})

test('missing palm sends explicit absence without stopping or pretending detection', async t => {
  const h = clientFixture(t); await h.arm()
  await h.client.sample(frame(1000,{point:null,detectionFps:0}))
  const sent = h.calls.find(c => c.action === 'sample').body
  assert.equal(h.client.getState().active,true)
  assert.deepEqual([sent.u,sent.v,sent.palm_scale,sent.hand_present],[.5,.5,1,false])
  assert.equal(h.calls.some(c => c.action === 'stop'),false)
})

test('stale palm waits then a new captured palm recovers on the same token', async t => {
  const h = clientFixture(t); await h.arm(); await h.client.sample(frame())
  h.setTime(1400); await h.client.sample(frame())
  h.setTime(1450); await h.client.sample(frame(1450,{point:{...landmark,x:.9}}))
  const samples = h.calls.filter(c => c.action === 'sample').map(c => c.body)
  assert.deepEqual(samples.map(c => c.hand_present),[true,false,true])
  assert.deepEqual(samples.map(c => c.token),['running','running','running'])
  assert.deepEqual([samples[1].u,samples[1].v],[.5,.5])
  assert.equal(samples[2].u,.9)
})
test('input 16: old sample success after stop never resumes; signal aborted', async t => {
  const pending = deferred()
  const h = clientFixture(t,a => a === 'sample' ? pending.promise : null)
  await h.arm(); const sent = h.client.sample(frame())
  const call = h.calls.find(c => c.action === 'sample'); h.client.stop('leave')
  assert.equal(call.options.signal.aborted,true)
  pending.resolve(response({ok:true,state:state(true)})); await sent
  assert.equal(h.client.getState().active,false)
})
test('input 17: transient network error waits then recovers, without a new start', async t => {
  let broken=true
  const h = clientFixture(t,a => a === 'sample' && broken ? Promise.reject(new Error('offline')) : null)
  await h.arm(); await h.client.sample(frame())
  assert.equal(h.client.getState().active,true); assert.match(h.client.getState().why,/offline.*等待/)
  assert.equal(h.client.getState().waiting,true)
  broken=false;h.setTime(1100);await h.client.sample(frame(1100))
  assert.equal(h.client.getState().active,true);assert.equal(h.client.getState().waiting,false)
  assert.equal(h.calls.some(c => c.action === 'stop'),false)
})

test('sample timeout releases a hung request; its late success cannot overwrite the next observation', async t => {
  const old=deferred();let count=0
  const h=clientFixture(t,a=>a==='sample' && ++count===1 ? old.promise : null,{timeoutMs:10})
  await h.arm();const pending=h.client.sample(frame())
  await new Promise(resolve=>setTimeout(resolve,25))
  assert.equal(h.client.getState().active,true);assert.equal(h.client.getState().waiting,true)
  assert.equal(h.calls.find(c=>c.action==='sample').options.signal.aborted,true)
  h.setTime(1100);await h.client.sample(frame(1100))
  assert.equal(count,2);assert.equal(h.client.getState().waiting,false)
  old.resolve(response({ok:true,state:state(false,'foreign')}));await pending
  assert.equal(h.client.getState().active,true);assert.equal(h.client.getState().server.token,'running')
  assert.equal(h.calls.some(c=>c.action==='stop'),false)
})
test('input 18: stopped server or mismatching sample token cannot replace ownership', async t => {
  for (const server of [state(false,'running'),state(true,'other')]) {
    const h = clientFixture(t,a => a === 'sample' ? Promise.resolve(response({ok:true,state:server})) : null)
    await h.arm(); await h.client.sample(frame()); assert.equal(h.client.getState().active,false)
  }
})

// Compile the real component setup with fake browser lifecycle/transport. No DOM,
// live camera, backend, World.sim, hardware or production store mutation is used.
const panelScript = compileScript(parse(read('panels/HandFollowPanel.vue')).descriptor,{id:'test'}).content
  .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
function panelFixture(t, opts = {}) {
  const hooks = {}, events = {}, intervals = new Set(), requests = [],centerRequests=[]
  const browser = { addEventListener(k,f) { events[k] = f }, removeEventListener(k) { delete events[k] } }
  const confirmations = []
  const doc = { ...browser, hidden:false }, win = { ...browser,__elfinScene:opts.scene,
    confirm(message) { confirmations.push(message); return opts.confirm?.(message) ?? true } }
  const mode = opts.mode || 'sim'
  const world = reactive({connected:true,mode,snap:{session_id:'backend-a',mode,real:null,hand_follow:null,...opts.snap},
    async call(path,body){centerRequests.push({path,body});return opts.centerCall?opts.centerCall(path,body):{ok:true,startup_id:body.startup_id,phase:'waiting'}},
    cameras:{active:{state:'streaming',session:'camera-a'}}})
  const props = reactive({visualModel:'elfin5',direction:0,fence:null})
  const storage = opts.storage || new Map(), tab = opts.tab || new Map()
  const clients = [],settingsCalls=[]
  world.setSettings=async patch=>{settingsCalls.push(patch);if(opts.settingsFailure)return {ok:false,why:'保存失败'};world.snap.settings={...world.snap.settings,...patch};return {ok:true}}
  const fetcher = async (path, options) => {
    assert.match(path,/^\/api\/(sim|real)\/(hand-follow|mouse-follow)\/(configure|start|sample|stop)$/)
    const action = path.split('/').at(-1), body = JSON.parse(options.body)
    requests.push({path,action,body,options})
    const custom = opts.handler?.(action,body,path,options)
    if (custom) return await custom
    const active = action === 'start' || action === 'sample'
    return response({ok:true,state:{...state(active,path.includes('/real/') ? `real-${active ? 'running' : 'configured'}` : undefined),session:body.session || 'camera-a'}})
  }
  const deadlines=[]
  const cameraSource=opts.cameraSource||createFixtureCameraSource({ref,reactive})
  const deps = { frontTaskView, useCameraSource:()=>cameraSource,setTimeout:(fn,delay)=>{deadlines.push(delay);return setTimeout(fn,0)},clearTimeout,...interactionRanges, computed,ref,watch,useWorld:() => world,document:doc,window:win,
    readTabState:key=>tab.get(key) ?? null,saveTabState:(key,value)=>tab.set(key,value),
    localStorage:{getItem:key => storage.get(key) ?? null,setItem:(key,value) => storage.set(key,value)},
    onMounted:f => { hooks.mount = f },onBeforeUnmount:f => { hooks.unmount = f },
    onActivated:f => { hooks.activate = f },onDeactivated:f => { hooks.deactivate = f },
    setInterval:f => { intervals.add(f); return f }, clearInterval:f => intervals.delete(f),
    ...follow, createMouseFollowInput, createInteractionInput, interactionModeIssue, createHandFollowClient:options => {
      const client = follow.createHandFollowClient({...options,fetcher})
      clients.push(client)
      return client
    } }
  const component = new Function(...Object.keys(deps),panelScript)(...Object.values(deps))
  const scope = effectScope()
  const emitted=[]
  const panel = scope.run(() => component.setup(props,{expose() {},emit:(...args)=>emitted.push(args)}))
  const producer = Symbol('test-camera')
  let disposed = false
  const fixture = {panel,world,props,doc,hooks,events,cameraSource,deadlines,intervals,requests,centerRequests,emitted,confirmations,tab,clients,storage,settingsCalls,
    publish(point = {...landmark}, patch = {}) {
      follow.publishHandFollowInput(frame(performance.now(),{point,...patch}),producer)
    },
    async arm() {
      this.publish(); await panel.start()
      assert.equal(panel.local.value.active,true)
    },
    async begin() { await panel.start() },
    loseCamera() { follow.publishHandFollowInput(null,producer) },
    dispose() {
      if (disposed) return
      disposed = true; hooks.unmount(); scope.stop(); follow.publishHandFollowInput(null,producer)
    } }
  t.after(() => fixture.dispose())
  hooks.mount()
  return fixture
}

// The original nine component lifecycle cases.
test('界面区分局部逆解受限与碰撞，不把停止位置显示为正常跟随', async t => {
  const h = panelFixture(t); await h.arm()
  h.world.snap.hand_follow = { ...h.panel.local.value.server, status: '保持位置', why: '局部连续求解受限', blocked_reason: '' }
  assert.equal(h.panel.solverLimited.value, true)
  assert.match(h.panel.displayStatus.value, /求解受限/)
  assert.doesNotMatch(h.panel.displayStatus.value, /碰撞|不可达/)
  h.world.snap.hand_follow = { ...h.world.snap.hand_follow, status: '局部跟随' }
  assert.equal(h.panel.displayStatus.value, '正在小步跟随')
  h.world.snap.hand_follow = { ...h.world.snap.hand_follow, blocked_reason: '碰撞：物体A' }
  assert.equal(h.panel.solverLimited.value, false)
  assert.equal(h.panel.displayStatus.value, '碰撞：物体A')
})

test('旧骨架保持不显示为新检测，不延长运动有效期', async t => {
  const h = panelFixture(t); await h.arm()
  h.publish({ ...landmark }, { trackingState: 'holding' })
  assert.equal(h.panel.detectionStatus.value, '短暂漏检，保留标记')
  assert.equal(h.panel.local.value.active, true)
})

test('panel 01: valid camera permits configure and explicit start without a palm', async t => {
  const h = panelFixture(t); h.publish(null)
  assert.equal(h.panel.configureBlocked.value,''); assert.ok(h.panel.inputIssue.value)
  await h.panel.configure(); assert.equal(h.panel.local.value.configured,true)
  await h.panel.start(); assert.equal(h.requests.some(c => c.action === 'start'),true)
})

test('开始先独立到中心，再用启动凭据重新绑定同会话工作区', async t => {
  const h=panelFixture(t);h.publish()
  assert.equal(await h.panel.configure(),true)
  const before=h.requests.filter(request=>request.action==='configure').length
  await h.panel.start()
  assert.equal(h.panel.local.value.active,true)
  assert.equal(h.requests.filter(request=>request.action==='configure').length,before+1)
  assert.equal(h.requests.filter(request=>request.action==='configure').at(-1).body.startup_id,h.centerRequests[0].body.startup_id)
  assert.equal(h.requests.filter(request=>request.action==='start').length,1)
})
test('one-click start without a palm immediately configures and starts shared center motion', async t => {
  const h=panelFixture(t); h.publish(null); await h.panel.start()
  assert.equal(h.panel.local.value.active,true)
  assert.deepEqual(h.requests.filter(c=>c.action!=='stop').map(c=>c.action),['configure','start'])
  assert.equal(h.requests[0].body.depth,true)
  assert.equal(h.requests[0].body.depth_mode,'gesture')
  assert.equal(Object.hasOwn(h.requests[0].body,'depth_range'),false)
  assert.equal(h.requests[0].body.rotation,false)
})

test('one-click start accepts an off-center palm; no retired center-sample gate', async t => {
  const h=panelFixture(t); h.publish(); await h.panel.start()
  assert.equal(h.panel.local.value.active,true)
  assert.deepEqual(h.requests.filter(c=>c.action!=='stop').map(c=>c.action),['configure','start'])
})

test('range edits invalidate ownership; invalid dimensions cannot configure or start', async t => {
  const h=panelFixture(t); h.publish(); await h.panel.configure()
  h.panel.rangeCenter.value=[0,0,600];h.panel.rangeSize.value=[1200,900,-1];h.panel.changeRange()
  assert.ok(h.panel.rangeError.value)
  const before=h.requests.length;await h.panel.configure();await h.panel.start()
  assert.equal(h.requests.length,before)
  h.panel.rangeSize.value=[1200,900,1200]
  await h.panel.configure()
  const request=h.requests.filter(c=>c.action==='configure').at(-1)
  assert.deepEqual(request.body.enclosure,{min:[-600,-450,0],max:[600,450,1200]})
  const configuredCount=h.requests.filter(c=>c.action==='configure').length
  await h.panel.resetRange()
  assert.equal(h.requests.filter(c=>c.action==='configure').length,configuredCount)
  assert.deepEqual(h.panel.rangeCenter.value,[-500,0,700])
  assert.deepEqual(h.panel.rangeSize.value,[900,300,500])
  assert.equal(h.panel.local.value.active,false)
})

test('range slider input changes only a display preview until explicit apply',t=>{
  const h=panelFixture(t)
  assert.deepEqual(h.panel.rangeCenter.value,[-500,0,700])
  assert.deepEqual(h.panel.rangeSize.value,[900,300,500])
  h.panel.rangeCenter.value=[400,-50,600];h.panel.rangeSize.value=[1300,120,960]
  h.panel.changeRange()
  assert.equal(h.requests.length,0)
  const preview=h.emitted.filter(e=>e[0]==='box-preview').at(-1)[1]
  assert.deepEqual(preview,{min:[-250,-110,120],max:[1050,10,1080]})
  h.panel.rangeSize.value[1]=80;h.panel.changeRange()
  const next=h.emitted.filter(e=>e[0]==='box-preview').at(-1)[1]
  assert.deepEqual(next,{min:[-250,-90,120],max:[1050,-10,1080]})
  assert.equal(h.requests.length,0)
  const source=read('panels/HandFollowPanel.vue')
  const rangeGrid=source.split('<div class="range-grid">')[1].split('</template>')[0]
  assert.equal((rangeGrid.match(/type="range"/g)||[]).length,2) // 每轴中心与尺寸，不包含新增前后滑条。
  assert.match(source,/@input="changeRange"/)
})

test('mouse input clamps edges, maps slider continuously and emits fresh stationary heartbeats', () => {
  const mouse=createMouseFollowInput('mouse-offline-test')
  assert.deepEqual(mouse.position(),{x:.5,y:.5,depth:.5})
  assert.equal(mouse.move(800,-20,{left:10,top:20,width:200,height:100}),true)
  assert.deepEqual(mouse.position(),{x:1,y:0,depth:.5})
  mouse.depth(0);assert.equal(mouse.frame(100).point.hand_open,0)
  mouse.depth(1);const first=mouse.frame(110),second=mouse.frame(120)
  assert.equal(second.point.hand_open,1);assert.ok(second.frameSeq>first.frameSeq)
  assert.equal(second.timestamp,120)
  assert.equal(mouse.move(1,1,{left:0,top:0,width:0,height:100}),false)
  mouse.depth(NaN);assert.equal(mouse.position().depth,1)
  mouse.center();assert.deepEqual(mouse.position(),{x:.5,y:.5,depth:.5})
  assert.deepEqual(createMouseFollowInput('mouse-refresh',{x:.2,y:.8,depth:.7}).position(),{x:.2,y:.8,depth:.7})
  assert.deepEqual(createMouseFollowInput('mouse-refresh',{x:NaN,y:.8,depth:.7}).position(),{x:.5,y:.5,depth:.5})
})

test('mouse panel starts without camera, sends position/depth only to sim mouse endpoint and stops on source switch',async t=>{
  const h=panelFixture(t)
  h.world.cameras.active=null;h.publish(null)
  h.panel.inputMode.value='mouse'
  assert.equal(h.panel.startBlocked.value,'')
  h.panel.moveMouse({clientX:75,clientY:25,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:100,height:100})}})
  h.panel.mouseDepth({target:{value:'0.8'}})
  await h.panel.start();assert.equal(h.panel.local.value.active,true)
  for(const tick of h.intervals)tick()
  await Promise.resolve()
  const sample=h.requests.find(c=>c.action==='sample')
  assert.equal(sample.path,'/api/sim/mouse-follow/sample')
  assert.deepEqual([sample.body.u,sample.body.v,sample.body.hand_open],[.75,.25,.8])
  assert.ok(h.requests.every(c=>c.path.startsWith('/api/sim/mouse-follow/')))
  h.panel.inputMode.value='camera'
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.at(-1).path,'/api/sim/mouse-follow/stop')
})

test('switching mouse follow to real revokes mouse owner and cannot send synthetic real samples',async t=>{
  const h=panelFixture(t);h.world.cameras.active=null;h.publish(null)
  h.panel.inputMode.value='mouse';await h.panel.start()
  h.world.mode='real'
  assert.equal(h.panel.mouseMode.value,false);assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.at(-1).path,'/api/sim/mouse-follow/stop')
  for(const tick of h.intervals)tick()
  assert.equal(h.requests.some(c=>c.path.startsWith('/api/real/')),false)
})

test('mouse exit requests center without stopping; re-entry resumes fresh input and slider stays inside control area',async t=>{
  const h=panelFixture(t);h.world.cameras.active=null;h.publish(null)
  h.panel.inputMode.value='mouse'
  await h.panel.start()
  assert.equal(h.panel.currentInput(),null,'启动时鼠标不在控制区，应等待在中心')
  h.panel.moveMouse({clientX:80,clientY:20,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:100,height:100})}})
  assert.equal(h.panel.currentInput().point.x,.8)
  h.panel.mouseDepth({target:{value:'.9'}})
  assert.equal(h.panel.currentInput().point.hand_open,.9)
  h.panel.leaveMouse()
  assert.equal(h.panel.currentInput(),null)
  assert.deepEqual(h.panel.mousePosition.value,{x:.5,y:.5,depth:.5})
  assert.equal(h.panel.local.value.active,true)
  for(let i=0;i<5;i++)await Promise.resolve()
  assert.equal(h.requests.filter(c=>c.action==='sample').at(-1).body.hand_present,false)
  assert.equal(h.requests.some(c=>c.action==='stop'),false)
  h.panel.moveMouse({clientX:30,clientY:60,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:100,height:100})}})
  assert.deepEqual([h.panel.currentInput().point.x,h.panel.currentInput().point.y],[.3,.6])
  const source=read('panels/HandFollowPanel.vue')
  assert.match(source, /class="mouse-controls"[^>]+@pointerleave="leaveMouse"/)
})

test('mouse blur and hidden page clear presence so returning cannot replay a stale pointer',async t=>{
  const h=panelFixture(t);h.panel.inputMode.value='mouse';await h.panel.start()
  h.panel.mouseDepth({target:{value:'.9'}})
  h.events.blur();assert.equal(h.panel.currentInput(),null)
  h.panel.mouseDepth({target:{value:'.8'}})
  h.doc.hidden=true;h.panel.visibilityChanged()
  h.doc.hidden=false;h.panel.visibilityChanged()
  assert.equal(h.panel.currentInput(),null)
  assert.equal(h.panel.local.value.active,true)
})

test('panel 02: hidden page retains ownership but sends absence instead of an invisible palm', async t => {
  const h = panelFixture(t); await h.arm(); h.doc.hidden = true; h.events.visibilitychange()
  assert.equal(h.panel.local.value.active,true)
  for(const tick of h.intervals) tick()
  await Promise.resolve()
  assert.equal(h.requests.find(c=>c.action==='sample').body.hand_present,false)
  assert.equal(h.requests.some(c=>c.action==='stop'),false)
})
test('panel 03: CameraFrame loss retains follow, sends absence, and explicit Stop still works', async t => {
  const h = panelFixture(t); await h.arm(); h.loseCamera()
  assert.equal(h.panel.local.value.active,true)
  for(const tick of h.intervals) tick()
  await Promise.resolve()
  assert.equal(h.requests.find(c=>c.action==='sample').body.hand_present,false)
  h.panel.stop();assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.filter(c=>c.action==='stop').at(-1).body.token,'running')
})

test('panel palm loss keeps follow active and its heartbeat explicitly requests waiting', async t => {
  const h = panelFixture(t); await h.arm(); h.publish(null)
  assert.equal(h.panel.local.value.active,true)
  assert.equal(h.panel.detectionStatus.value,'短暂漏检，保持位置')
  for (const tick of h.intervals) tick()
  await Promise.resolve()
  const sent = h.requests.find(c => c.action === 'sample').body
  assert.deepEqual([sent.u,sent.v,sent.hand_present],[.5,.5,false])
})

test('panel shows measured capture/detection separately, never substitutes a requested FPS', t => {
  const h = panelFixture(t)
  h.world.cameras.active.target_fps=60
  h.world.cameras.active.actual_fps=19.82
  h.publish(landmark,{detectionFps:17.5})
  assert.equal(h.panel.frameRates.value,'采集 19.8 / 检测 17.5 fps')
})
test('panel 04: temporary backend disconnect retains armed state; removed TCP card never reappears', async t => {
  const h = panelFixture(t); await h.arm(); h.world.connected = false
  assert.equal(h.panel.local.value.active,true)
  assert.ok(!read('panels/HandFollowPanel.vue').includes('>实际末端<'))
})
test('panel: removed actual TCP content is absent for both configured and unconfigured snapshots', t => {
  const h = panelFixture(t)
  h.world.snap.hand_follow = {...state(false),configured:false,actual:[0,0,0,0,0,0]}
  assert.equal(h.panel.actual,undefined)
  h.world.snap.hand_follow = {...state(false),configured:true,actual:[0,0,0,0,0,0]}
  assert.equal(h.panel.actual,undefined)
  h.world.snap.hand_follow = {...state(false),configured:false,actual:[10,20,30,0,0,0]}
  assert.equal(h.panel.actual,undefined)
})
test('panel 05: switching to real stops sim and requires actual real prerequisites', async t => {
  const h = panelFixture(t); await h.arm(); h.world.mode = 'real'
  assert.equal(h.panel.local.value.active,false); assert.match(h.panel.startBlocked.value,/真机/)
  assert.equal(h.requests.filter(c=>c.action==='stop').at(-1).path,'/api/sim/hand-follow/stop')
})
test('panel 06: real link present or unsupported visual model is disabled', t => {
  const h = panelFixture(t)
  h.world.snap.real = {stream_up:true,cmd_up:false}
  assert.match(h.panel.environmentIssue.value,/断开真机/)
  h.world.snap.real = null; h.props.visualModel = 'elfin3'
  assert.match(h.panel.environmentIssue.value,/Elfin5/)
})
test('panel 07: leaving the follow page stops the owner; returning never resumes motion silently', async t => {
  const h = panelFixture(t); await h.arm(); h.hooks.deactivate()
  // 离开跟随页必须真正停止：hand_follow.active 一直挂着的话，
  // 写字/井字棋/绘制的前置条件（要静止、要没有跟随）就永远是 false。
  assert.equal(h.panel.local.value.active,false)
  const stops = h.requests.filter(c => c.action === 'stop')
  assert.equal(stops.length,1)
  assert.equal(stops.at(-1).body.token,'running')
  for(const tick of h.intervals) tick()
  await Promise.resolve()
  assert.equal(h.requests.some(c=>c.action==='sample'),false,'a stopped owner sends no samples')
  h.hooks.activate()
  // 回到页面不自动恢复运动：必须由用户重新确认后再次点击开始。
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.panel.local.value.configured,false)
  assert.equal(h.requests.filter(c=>c.action==='start').length,1)
  assert.equal(h.intervals.size,1)
})
test('panel 08: active snapshot never acquires local control', t => {
  const h = panelFixture(t)
  h.world.snap.hand_follow = state(true,'foreign')
  assert.equal(h.panel.local.value.active,false)
})
test('panel 09: unmount releases timers/listeners and never calls motion APIs', async t => {
  const h = panelFixture(t); await h.arm(); h.dispose()
  assert.equal(h.intervals.size,0); assert.equal(Object.keys(h.events).length,0)
  assert.ok(h.requests.every(c => ['configure','start','sample','stop'].includes(c.action)))
})

// Regression: abort may be ignored by a transport. New ownership must still work.
test('race: indefinitely pending old sample cannot block new start/sample', async t => {
  const old = deferred(); let samples = 0
  const h = clientFixture(t,a => a === 'sample' && ++samples === 1 ? old.promise : null)
  await h.arm(); void h.client.sample(frame())
  const original = h.calls.find(c => c.action === 'sample')
  h.client.stop(); assert.equal(original.options.signal.aborted,true)
  await h.arm(); h.setTime(1200); await h.client.sample(frame(1200))
  assert.equal(samples,2); assert.equal(h.client.getState().active,true)
  // Intentionally never resolve old: no network or event loop handle is retained.
})
test('race: old sample finally cannot release NEW in-flight sample or allow a third', async t => {
  const old = deferred(), current = deferred(); let n = 0
  const h = clientFixture(t,a => a === 'sample' ? (++n === 1 ? old.promise : n === 2 ? current.promise : null) : null)
  await h.arm(); const first = h.client.sample(frame()); h.client.stop()
  await h.arm(); h.setTime(1200); const second = h.client.sample(frame(1200))
  assert.equal(n,2)
  old.resolve(response({ok:true,state:state(true)})); await first
  h.setTime(1300); await h.client.sample(frame(1300)); assert.equal(n,2)
  assert.equal(h.client.getState().active,true)
  current.resolve(response({ok:true,state:state(true)})); await second
  h.setTime(1400); await h.client.sample(frame(1400)); assert.equal(n,3)
  assert.deepEqual(h.calls.filter(c => c.action === 'sample').map(c => c.body.seq),[1,1,2])
})

// Token-checking backend fake: stop may only retire the exact current token.
test('race: delayed retire of old configured token cannot stop new start token', async t => {
  let serial = 0, server = state(false,'initial')
  const stops = []
  const h = clientFixture(t,(action,body) => {
    if (action === 'configure') server = state(false,`config-${++serial}`)
    if (action === 'start') {
      assert.equal(body.token,server.token); server = state(true,`start-${++serial}`)
    }
    if (action === 'stop') {
      const d = deferred()
      stops.push(() => {
        const matches = body.token === server.token
        if (matches) server = state(false,`stopped-${++serial}`)
        d.resolve(response({ok:matches,state:{...server}}))
      })
      return d.promise
    }
    if (action === 'sample') assert.equal(body.token,server.token)
    return Promise.resolve(response({ok:true,state:{...server}}))
  })
  await h.client.configure(payload)
  const old = server.token
  await h.client.configure(payload); await h.client.start('camera-a')
  const started = server.token
  assert.notEqual(old,started); assert.equal(stops.length,1)
  stops.shift()(); await Promise.resolve()
  assert.equal(server.active,true); assert.equal(server.token,started)
  await h.client.sample(frame()); assert.equal(h.client.getState().active,true)
})
test('race: later old start success retires only old token, not newer running token', async t => {
  const oldReply = deferred(); let serial = 0, starts = 0, server = state()
  const h = clientFixture(t,(action,body) => {
    if (action === 'configure') server = state(false,`config-${++serial}`)
    if (action === 'start') {
      assert.equal(body.token,server.token); server = state(true,`start-${++serial}`)
      if (++starts === 1) { oldReply.server = {...server}; return oldReply.promise }
    }
    if (action === 'stop') {
      const matches = body.token === server.token
      if (matches) server = state(false,`stopped-${++serial}`)
      return Promise.resolve(response({ok:matches,state:{...server}}))
    }
    if (action === 'sample') assert.equal(body.token,server.token)
    return Promise.resolve(response({ok:true,state:{...server}}))
  })
  await h.client.configure(payload); const first = h.client.start('camera-a')
  h.client.stop(); await h.client.configure(payload); await h.client.start('camera-a')
  const current = server.token
  oldReply.resolve(response({ok:true,state:oldReply.server})); await first
  assert.equal(server.token,current); assert.equal(server.active,true)
  await h.client.sample(frame()); assert.equal(h.client.getState().active,true)
})
test('watchdog: matching-session changed token revokes locally; stop sends OLD owned token only', async t => {
  const h = panelFixture(t); await h.arm()
  h.world.snap.hand_follow = state(true,'running')
  h.world.snap.hand_follow = {...state(false,'watchdog-rotated'),actual:[90,80,70,0,0,0]}
  assert.equal(h.panel.local.value.active,false)
  assert.deepEqual([...h.panel.telemetry.value.actual],[90,80,70,0,0,0])
  const stops = h.requests.filter(c => c.action === 'stop')
  assert.equal(stops.at(-1).body.token,'running')
  assert.equal(stops.some(c => c.body.token === 'watchdog-rotated'),false)
})
test('watchdog: active=false without token change also revokes locally', async t => {
  const h = panelFixture(t); await h.arm()
  h.world.snap.hand_follow = state(false,'running')
  assert.equal(h.panel.local.value.active,false)
})
test('watchdog: baseline pre-start snapshot does not revoke new HTTP start', async t => {
  const h = panelFixture(t); h.publish(); await h.panel.configure()
  h.world.snap.hand_follow = state(false,'configured')
  await h.begin(); h.panel.validate()
  assert.equal(h.panel.local.value.active,true)
  h.world.snap.hand_follow = state(true,'running'); h.panel.validate()
  assert.equal(h.panel.local.value.active,true)
  h.world.snap.hand_follow = state(false,'new-stop-token')
  assert.equal(h.panel.local.value.active,false)
})
test('race: queued old configured WS snapshots with NEW references cannot revoke HTTP start', async t => {
  const h = panelFixture(t); h.publish(); await h.panel.configure()
  h.world.snap.hand_follow = state(false,'configured')
  const beforeStart = h.world.snap.hand_follow
  await h.begin()
  h.world.snap.hand_follow = {...state(false,'configured')}
  assert.notEqual(h.world.snap.hand_follow,beforeStart)
  assert.equal(h.panel.local.value.active,true)
  h.world.snap.hand_follow = {...state(false,'configured')}
  assert.equal(h.panel.local.value.active,true)
  assert.equal(h.panel.local.value.server.token,'running')
  assert.equal(h.requests.some(c => c.action === 'stop' && c.body.token==='running'),false)
  h.world.snap.hand_follow = state(true,'running')
  h.world.snap.hand_follow = state(false,'watchdog-new')
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.filter(c => c.action === 'stop').at(-1).body.token,'running')
})
test('race: before first owned active snapshot, foreign tokens never arm or replace local token', async t => {
  const h = panelFixture(t); await h.arm()
  h.world.snap.hand_follow = state(true,'foreign-old-token')
  assert.equal(h.panel.local.value.active,true)
  assert.equal(h.panel.local.value.server.token,'running')
  h.world.snap.hand_follow = state(false,'running')
  assert.equal(h.panel.local.value.active,false)
  h.world.snap.hand_follow = state(true,'running')
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.filter(c => c.action === 'start').length,1)
})
test('race: an owned active WS snapshot present before HTTP response counts as confirmation', async t => {
  const h = panelFixture(t); h.publish(); await h.panel.configure()
  h.world.snap.hand_follow = state(true,'running')
  await h.begin()
  assert.equal(h.panel.local.value.active,true)
  h.world.snap.hand_follow = state(false,'watchdog-after-early-confirmation')
  assert.equal(h.panel.local.value.active,false)
})
test('mapping: corners/center exclude both horizontal and vertical letterboxing; mirror once', () => {
  for (const [vw,vh,iw,ih,expected] of [
    [320,180,640,480,{left:40,top:0,width:240,height:180}],
    [320,240,1280,720,{left:0,top:30,width:320,height:180}],
  ]) {
    const b = imageBoundsInView(vw,vh,iw,ih); assert.deepEqual(b,expected)
    for (const [raw,visible] of [[{x:1,y:0},[b.left,b.top]],
      [{x:0,y:0},[b.left+b.width,b.top]], [{x:0,y:1},[b.left+b.width,b.top+b.height]],
      [{x:1,y:1},[b.left,b.top+b.height]]]) {
      const tip = fingertipInView(raw,vw,vh,iw,ih,true)
      assert.deepEqual([tip.x,tip.y],visible)
    }
    const center = fingertipInView({x:.5,y:.5},vw,vh,iw,ih,true)
    assert.deepEqual([center.x,center.y],[b.left+b.width/2,b.top+b.height/2])
  }
})
test('components: actual panel/camera scripts, templates and scoped CSS compile; Rz is explicit', () => {
  for (const path of ['panels/HandFollowPanel.vue','components/CameraFrame.vue','components/CameraWidget.vue']) {
    const {descriptor,errors} = parse(read(path),{filename:path}); assert.deepEqual(errors,[])
    const script = compileScript(descriptor,{id:path})
    const template = compileTemplate({source:descriptor.template.content,filename:path,id:path,
      compilerOptions:{bindingMetadata:script.bindings}})
    assert.deepEqual(template.errors,[])
    for (const style of descriptor.styles) assert.deepEqual(compileStyle({source:style.content,id:path,scoped:style.scoped}).errors,[])
  }
  assert.match(read('panels/HandFollowPanel.vue'),/工具旋转（Rz）/)
  assert.ok(read('components/CameraFrame.vue').includes('handPipelineObservation'))
  assert.doesNotMatch(read('components/CameraFrame.vue'),/new Worker|createImageBitmap|postMessage/)
  assert.ok(read('components/CameraWidget.vue').includes(':mapping="!!mapping"'))
})

const REAL_ENDPOINT = '/api/real/hand-follow'
const readyReal = () => ({real:{stream_up:true,cmd_up:true,quality:'valid',override:.05},
  motion_granted:true,real_speed_pct:5,real_hand_follow:{available:true,why:'',
    control_policy:'real-palm-position-j6-zero-v1',phase:'idle',j6_deg:0}})

test('real client forces fixed orientation and gesture depth; start requires explicit boolean confirmation', async t => {
  const h=clientFixture(t,null,{endpoint:REAL_ENDPOINT})
  await h.client.configure({...payload,rotation:true,depth_mode:'scale'})
  const configured=h.calls[0].body
  assert.equal(configured.rotation,false);assert.equal(configured.depth_mode,'gesture')
  for (const confirmation of [undefined,true,{confirm_real:false},{confirm_real:1},{confirm_real:'true'}]) {
    assert.equal(await h.client.start('camera-a',confirmation),false)
  }
  assert.equal(h.calls.some(c=>c.action==='start'),false)
  assert.equal(await h.client.start('camera-a',{confirm_real:true}),true)
  assert.deepEqual(h.calls.find(c=>c.action==='start').body,{session:'camera-a',token:'configured',confirm_real:true})
  h.client.stop()
  assert.ok(h.calls.every(c=>c.path.startsWith(REAL_ENDPOINT+'/')))
  assert.equal(h.calls.at(-1).body.token,'running')
  assert.equal(h.calls.at(-1).options.keepalive,true)
})

test('sim default keeps the original configure and start payloads', async t => {
  const h=clientFixture(t);await h.arm()
  assert.deepEqual(h.calls[0].body,payload)
  assert.deepEqual(h.calls[1].body,{session:'camera-a',token:'configured'})
  assert.throws(()=>follow.createHandFollowClient({endpoint:()=>REAL_ENDPOINT}),/接口无效/)
})

test('snapshot selection is mode-specific and bound to each client endpoint', t => {
  const snap={hand_follow:{token:'sim-only'},real_hand_follow:{token:'real-only'}}
  const sim=clientFixture(t),real=clientFixture(t,null,{endpoint:REAL_ENDPOINT})
  assert.equal(follow.selectHandFollowSnapshot(snap),snap.hand_follow)
  assert.equal(sim.client.selectSnapshot(snap),snap.hand_follow)
  assert.equal(real.client.selectSnapshot(snap),snap.real_hand_follow)
  assert.equal(real.client.selectSnapshot({hand_follow:snap.hand_follow}),undefined)
})

test('real palm/camera loss stops the owned token and fresh input cannot restart it', async t => {
  for (const lost of [null,frame(1000,{point:null}),frame(1000,{fresh:false}),
    frame(1000,{session:'camera-b'}),frame(600),frame(1000,{trackingState:'lost'})]) {
    const h=clientFixture(t,null,{endpoint:REAL_ENDPOINT});await h.arm()
    await h.client.sample(frame());h.setTime(1100)
    await h.client.sample(lost)
    assert.equal(h.client.getState().active,false)
    assert.equal(h.calls.filter(c=>c.action==='sample').length,1)
    assert.equal(h.calls.at(-1).action,'stop');assert.equal(h.calls.at(-1).body.token,'running')
    h.setTime(1100);await h.client.sample(frame(1100))
    assert.equal(h.calls.filter(c=>c.action==='start').length,1)
    assert.equal(h.calls.filter(c=>c.action==='sample').length,1)
  }
})

test('real starts armed without a palm and waits without centering until the first valid palm', async t => {
  const h=clientFixture(t,null,{endpoint:REAL_ENDPOINT});await h.arm()
  assert.equal(h.client.getState().why,'等待手掌；不会自动居中')
  for (const time of [1000,1100]) {
    h.setTime(time);await h.client.sample(frame(time,{point:null}))
    assert.equal(h.client.getState().active,true)
    assert.equal(h.client.getState().handSeen,false)
    assert.equal(h.client.getState().waiting,true)
    assert.equal(h.client.getState().why,'等待手掌；不会自动居中')
    assert.equal(h.calls.at(-1).body.hand_present,false)
  }
  assert.equal(h.calls.some(c=>c.action==='stop'),false)
  h.setTime(1200);await h.client.sample(frame(1200))
  assert.equal(h.client.getState().active,true);assert.equal(h.client.getState().handSeen,true)
  assert.equal(h.calls.at(-1).body.hand_present,true)
  h.setTime(1300);await h.client.sample(frame(1300,{point:null}))
  assert.equal(h.client.getState().active,false)
  assert.equal(h.calls.at(-1).action,'stop')
})

test('a first palm observed while the initial absent sample is pending still latches loss', async t => {
  const pending=deferred()
  const h=clientFixture(t,a=>a==='sample' ? pending.promise : null,{endpoint:REAL_ENDPOINT})
  await h.arm();const first=h.client.sample(frame(1000,{point:null}))
  h.setTime(1100);await h.client.sample(frame(1100))
  assert.equal(h.client.getState().handSeen,true)
  h.setTime(1200);await h.client.sample(null)
  assert.equal(h.client.getState().active,false)
  assert.equal(h.calls.at(-1).action,'stop')
  pending.resolve(response({ok:true,state:state(true)}));await first
  assert.equal(h.client.getState().active,false)
})

test('real rejected/inactive samples revoke locally, display backend reason and never retire a foreign token', async t => {
  for (const server of [state(false,'running'),state(true,'running'),state(false,'foreign')]) {
    const h=clientFixture(t,a=>a==='sample' ? response({ok:false,why:'后台：无工具确认缺失',state:server}) : null,
      {endpoint:REAL_ENDPOINT})
    await h.arm();await h.client.sample(frame())
    assert.equal(h.client.getState().active,false)
    assert.equal(h.client.getState().why,'后台：无工具确认缺失')
    assert.equal(h.calls.at(-1).body.token,'running')
    assert.equal(h.calls.some(c=>c.action==='stop' && c.body.token==='foreign'),false)
    h.setTime(1100);await h.client.sample(frame(1100))
    assert.equal(h.calls.filter(c=>c.action==='sample').length,1)
  }
})

test('real active:false acknowledgement revokes even when the sample response says ok:true', async t => {
  const h=clientFixture(t,a=>a==='sample' ? response({ok:true,state:{...state(false,'running'),why:'后台已撤销'}}) : null,
    {endpoint:REAL_ENDPOINT})
  await h.arm();await h.client.sample(frame())
  assert.equal(h.client.getState().active,false)
  assert.equal(h.client.getState().why,'后台已撤销')
  assert.equal(h.calls.at(-1).body.token,'running')
})

test('real valid samples keep latest camera coordinates and owned token without emitting joint commands', async t => {
  const h=clientFixture(t,null,{endpoint:REAL_ENDPOINT});await h.arm()
  await h.client.sample(frame(1000,{point:{...landmark,hand_open:0}}))
  const sent=h.calls.at(-1)
  assert.equal(sent.path,REAL_ENDPOINT+'/sample')
  assert.deepEqual([sent.body.u,sent.body.v,sent.body.hand_open,sent.body.token],[.2,.7,0,'running'])
  assert.equal(Object.hasOwn(sent.body,'q'),false)
  assert.equal(h.client.getState().active,true)
})

test('sim rejected active sample waits on the same token and recovers', async t => {
  let rejected=true
  const h=clientFixture(t,a=>a==='sample' && rejected ? response({ok:false,why:'暂存',state:state(true)}) : null)
  await h.arm();await h.client.sample(frame())
  assert.equal(h.client.getState().active,true);assert.equal(h.client.getState().waiting,true)
  assert.equal(h.calls.some(c=>c.action==='stop'),false)
  rejected=false;h.setTime(1100);await h.client.sample(frame(1100))
  assert.equal(h.client.getState().active,true);assert.equal(h.client.getState().waiting,false)
})

test('real sample transport failure stops rather than waiting for automatic recovery', async t => {
  const h=clientFixture(t,a=>a==='sample' ? Promise.reject(new Error('network lost')) : null,{endpoint:REAL_ENDPOINT})
  await h.arm();await h.client.sample(frame())
  assert.equal(h.client.getState().active,false)
  assert.equal(h.calls.at(-1).action,'stop')
  assert.equal(h.client.getState().why,'network lost')
})

test('real sample timeout aborts, stops and ignores a late active reply', async t => {
  const late=deferred()
  const h=clientFixture(t,a=>a==='sample' ? late.promise : null,{endpoint:REAL_ENDPOINT,timeoutMs:10})
  await h.arm();const sent=h.client.sample(frame())
  await new Promise(resolve=>setTimeout(resolve,25))
  assert.equal(h.client.getState().active,false)
  assert.equal(h.calls.find(c=>c.action==='sample').options.signal.aborted,true)
  assert.equal(h.calls.at(-1).action,'stop')
  late.resolve(response({ok:true,state:state(true)}));await sent
  assert.equal(h.client.getState().active,false)
})

test('real client cannot resume saved ownership and detach sends stop instead of handing off', async t => {
  const h=clientFixture(t,null,{endpoint:REAL_ENDPOINT})
  const live={...state(true),seq:4},saved={active:true,configured:true,backendSession:'backend-a',server:live,seq:4}
  assert.equal(h.client.resumeOwned(saved,live,'backend-a'),false)
  assert.equal(h.calls.length,0)
  const sim=clientFixture(t)
  assert.equal(sim.client.resumeOwned({...saved,mode:'real'},live,'backend-a'),false)
  assert.equal(sim.client.resumeOwned({...saved,endpoint:REAL_ENDPOINT},live,'backend-a'),false)
  await h.arm();h.client.detach()
  assert.equal(h.client.getState().active,false)
  assert.equal(h.calls.at(-1).path,REAL_ENDPOINT+'/stop')
  assert.equal(await h.client.configure(payload),false)
})

test('real panel only confirms on explicit start; cancellation issues no configure or start', async t => {
  const h=panelFixture(t,{mode:'real',snap:readyReal(),confirm:()=>false})
  h.publish();assert.equal(h.requests.length,0);assert.equal(h.confirmations.length,0)
  await h.panel.start()
  assert.equal(h.requests.length,0);assert.equal(h.confirmations.length,1)
  assert.match(h.confirmations[0],/先受控前往工作区中心，J6 移动到 0°/)
  assert.match(h.confirmations[0],/实际到位后才开始手掌跟随，J6 保持 0°/)
  assert.match(h.confirmations[0],/丢掌或异常时停止，不自动恢复/)
  assert.match(h.confirmations[0],/无人、急停可用/)
  assert.match(h.confirmations[0],/不会自动连接、使能或标定/)
  await h.panel.configure()
  assert.equal(h.confirmations.length,1)
  assert.deepEqual(h.requests.map(c=>c.action),['configure'])
})

test('real panel start routes all commands to real and sends confirmation without enable/connect', async t => {
  const h=panelFixture(t,{mode:'real',snap:readyReal()});await h.arm()
  assert.equal(h.confirmations.length,1)
  assert.deepEqual(h.requests.map(c=>c.action),['configure','start'])
  assert.equal(h.requests[0].body.rotation,false);assert.equal(h.requests[0].body.depth_mode,'gesture')
  assert.equal(h.requests[1].body.confirm_real,true)
  assert.equal(h.requests[1].body.token,'real-configured')
  h.panel.stop()
  assert.equal(h.requests.at(-1).body.token,'real-running')
  assert.ok(h.requests.every(c=>c.path.startsWith(REAL_ENDPOINT+'/')))
})

test('real panel without first palm displays backend centering then waits at center', async t => {
  const h=panelFixture(t,{mode:'real',snap:readyReal()});h.publish(null)
  assert.equal(h.panel.detectionStatus.value,'等待手掌；先到中心再跟随')
  await h.panel.start()
  assert.equal(h.panel.local.value.active,true)
  h.world.snap.real_hand_follow={...readyReal().real_hand_follow,...h.panel.local.value.server,active:true,phase:'centering'}
  assert.equal(h.panel.statusText.value,'正在前往中心 · J6 归零')
  await h.clients.at(-1).sample(null)
  assert.equal(h.panel.local.value.active,true)
  h.world.snap.real_hand_follow.phase='waiting_hand'
  assert.equal(h.panel.statusText.value,'已到中心 · 等待手掌')
  assert.doesNotMatch(h.panel.statusText.value,/返回中心/)
  assert.match(h.confirmations[0],/实际到位后才开始手掌跟随/)
  h.publish(null,{detectorStatus:'手掌模型加载中…'})
  assert.match(h.panel.detectionStatus.value,/到达中心后等待手掌/)
})

test('real start guards retain link, feedback, permission, low speed and Elfin5 restrictions', async t => {
  for (const patch of [{real:{...readyReal().real,stream_up:false}},
    {real:{...readyReal().real,cmd_up:false}},{real:{...readyReal().real,quality:'stale'}},
    {motion_granted:false},{real_speed_pct:5.01},{real_speed_pct:null},
    {real:{...readyReal().real,override:.0501}},{real:{...readyReal().real,override:null}}]) {
    const h=panelFixture(t,{mode:'real',snap:{...readyReal(),...patch}});h.publish()
    assert.ok(h.panel.startBlocked.value)
    await h.panel.start();assert.equal(h.requests.length,0);assert.equal(h.confirmations.length,0)
  }
  const h=panelFixture(t,{mode:'real',snap:readyReal()});h.publish();h.props.visualModel='elfin3'
  await h.panel.start();assert.equal(h.requests.length,0);assert.match(h.panel.startBlocked.value,/Elfin5/)
})

test('real configure delegates denial to backend even without motion permission or device links', async t => {
  const h=panelFixture(t,{mode:'real',snap:{real_hand_follow:readyReal().real_hand_follow},handler:action=>action==='configure' ?
    response({ok:false,why:'后台：真实连接不存在，禁止配置'}) : null})
  h.publish();assert.ok(h.panel.activeGuard.value);assert.equal(h.panel.configureBlocked.value,'')
  await h.panel.configure()
  assert.deepEqual(h.requests.map(c=>c.action),['configure'])
  assert.equal(h.panel.statusText.value,'后台：真实连接不存在，禁止配置')
})

test('real availability and HTTP refusal messages show backend reasons verbatim', async t => {
  const h=panelFixture(t,{mode:'real',snap:{...readyReal(),real_hand_follow:{...readyReal().real_hand_follow,available:false,why:'后台只读设置禁止点动'}}})
  h.publish();assert.equal(h.panel.configureBlocked.value,'后台只读设置禁止点动')
  await h.panel.configure();assert.equal(h.requests.length,0)
  const denied=panelFixture(t,{mode:'real',snap:readyReal(),handler:action=>action==='configure' ?
    {ok:false,status:403,json:async()=>({why:'后台：请在现有工具入口确认无工具'})} : null})
  denied.publish();await denied.panel.configure()
  assert.equal(denied.panel.statusText.value,'后台：请在现有工具入口确认无工具')
})

test('real hidden, navigation, pagehide, unmount and offline retire the token; return never restarts', async t => {
  for (const event of ['hidden','navigation','pagehide','unmount','offline']) {
    const h=panelFixture(t,{mode:'real',snap:readyReal()});await h.arm()
    if(event==='hidden') {h.doc.hidden=true;h.events.visibilitychange()}
    if(event==='navigation') h.hooks.deactivate()
    if(event==='pagehide') h.events.pagehide()
    if(event==='unmount') h.dispose()
    if(event==='offline') h.events.offline()
    assert.equal(h.panel.local.value.active,false,event)
    assert.equal(h.requests.at(-1).path,REAL_ENDPOINT+'/stop')
    assert.equal(h.requests.at(-1).body.token,'real-running')
    assert.equal(h.tab.get('huayan.hand-follow.owner.v1'),null)
    if(event!=='unmount') {
      h.doc.hidden=false;h.events.visibilitychange();h.hooks.activate();h.publish()
      for(const tick of h.intervals)tick()
      assert.equal(h.requests.filter(c=>c.action==='start').length,1)
    }
  }
})

test('real refresh ignores saved real and legacy matching credentials and both active snapshots', t => {
  for (const mode of [undefined,'real']) {
    const live={...state(true,'saved-real'),seq:8},saved={mode,active:true,configured:true,
      backendSession:'backend-a',server:live,seq:8}
    const tab=new Map([['huayan.hand-follow.owner.v1',saved]])
    const h=panelFixture(t,{mode:'real',tab,snap:{...readyReal(),hand_follow:live,real_hand_follow:{...live,available:true}}})
    h.publish();h.panel.resumeRefreshOwner();for(const tick of h.intervals)tick()
    assert.equal(h.panel.local.value.active,false)
    assert.equal(h.panel.local.value.server,null)
    assert.equal(h.requests.length,0);assert.equal(h.confirmations.length,0)
    assert.equal(tab.get('huayan.hand-follow.owner.v1'),null)
    assert.match(h.panel.refreshNotice.value,/不会自动恢复/)
  }
})

test('mode switches dispose old ownership before new requests; stop tokens never cross endpoints', async t => {
  const h=panelFixture(t);await h.arm()
  h.world.mode='real'
  assert.equal(h.requests.at(-1).path,'/api/sim/hand-follow/stop')
  assert.equal(h.requests.at(-1).body.token,'running')
  Object.assign(h.world.snap,readyReal());await h.arm()
  h.world.mode='sim'
  assert.equal(h.requests.at(-1).path,REAL_ENDPOINT+'/stop')
  assert.equal(h.requests.at(-1).body.token,'real-running')
  h.world.snap.real=null;await h.arm()
  assert.equal(h.clients.length,3)
  assert.equal(await h.clients[0].configure(payload),false)
  assert.equal(await h.clients[1].start('camera-a',{confirm_real:true}),false)
  assert.ok(h.requests.filter(c=>c.path.includes('/sim/')).every(c=>!c.body.token?.startsWith('real-')))
  assert.ok(h.requests.filter(c=>c.path.includes('/real/')).every(c=>!['configured','running'].includes(c.body.token)))
})

test('mode switch during pending configure/start retires late tokens only at their original endpoint', async t => {
  for (const mode of ['sim','real']) for (const action of ['configure','start']) {
    const pending=deferred(),oldEndpoint=`/api/${mode}/hand-follow`
    const h=panelFixture(t,{mode,snap:mode==='real'?readyReal():{},handler:(a,_body,path)=>
      a===action && path.startsWith(oldEndpoint+'/') ? pending.promise : null})
    h.publish()
    const started=h.panel.start()
    for(let i=0;i<50 && !h.requests.some(c=>c.action===action);i++)await Promise.resolve()
    assert.ok(h.requests.some(c=>c.action===action))
    h.world.mode=mode==='real'?'sim':'real'
    const late=`${mode}-late-${action}`
    pending.resolve(response({ok:true,state:state(action==='start',late)}));await started
    assert.equal(h.panel.local.value.active,false)
    assert.ok(h.requests.some(c=>c.path===oldEndpoint+'/stop' && c.body.token===late))
    assert.ok(h.requests.every(c=>c.path.startsWith(oldEndpoint+'/')))
  }
})

test('real snapshot telemetry and revocation do not consume simulation state', async t => {
  const h=panelFixture(t,{mode:'real',snap:readyReal()});await h.arm()
  h.world.snap.hand_follow={...state(false,'sim-foreign'),actual:[999]}
  assert.equal(h.panel.local.value.active,true)
  h.world.snap.real_hand_follow={...state(true,'real-running'),available:true,actual:[1,2,3],target:[4,5,6],q:[7]}
  assert.deepEqual([...h.panel.telemetry.value.actual],[1,2,3])
  assert.deepEqual([...h.panel.telemetry.value.target],[4,5,6])
  assert.deepEqual([...h.panel.telemetry.value.q],[7])
  h.world.snap.real_hand_follow={...state(false,'real-watchdog'),available:true,why:'后台300ms看门狗已停止'}
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.at(-1).body.token,'real-running')
  assert.equal(h.panel.local.value.why,'后台300ms看门狗已停止')
})

test('real snapshot safety loss stops an active owner and cannot be rearmed by fresh snapshots', async t => {
  const h=panelFixture(t,{mode:'real',snap:readyReal()});await h.arm()
  h.world.snap.real.override=.06
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.at(-1).path,REAL_ENDPOINT+'/stop')
  h.world.snap.real.override=.05
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.filter(c=>c.action==='start').length,1)
})

test('real disconnected backend stops ownership and backend stop reasons outrank local permission hints', async t => {
  const h=panelFixture(t,{mode:'real',snap:readyReal()});await h.arm()
  h.world.snap.real_hand_follow={...state(true,'real-running'),available:true}
  h.world.snap={...h.world.snap,motion_granted:false,
    real_hand_follow:{...state(false,'real-watchdog'),why:'后台：控制器撤销权限'}}
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.panel.statusText.value,'停止待确认 · 后台：控制器撤销权限')
  const disconnected=panelFixture(t,{mode:'real',snap:readyReal()});await disconnected.arm()
  disconnected.world.connected=false
  assert.equal(disconnected.panel.local.value.active,false)
  assert.equal(disconnected.requests.at(-1).path,REAL_ENDPOINT+'/stop')
})

test('real panel exposes center-first J6-zero policy without browser commissioning unlock', () => {
  const source=read('panels/HandFollowPanel.vue')
  assert.match(source,/真机 · 手掌跟随/)
  assert.match(source,/先到中心 → J6＝0° → 到位后跟随/)
  assert.match(source,/实际工具、工具中心点、负载、路径与运动许可由后台检查，未通过不能启动/)
  assert.match(source,/liveSnapshot.value\?\.control_policy === 'real-palm-position-j6-zero-v1'/)
  assert.doesNotMatch(source,/commissioning\s*:\s*true|qualification\s*:\s*true|unlock\s*:\s*true/)
})


test('stop remains pending until explicit stop proof; acknowledgement is not stop proof', async t => {
  const delayed=deferred()
  const h=clientFixture(t,action=>action==='stop'?delayed.promise:null,{endpoint:REAL_ENDPOINT})
  await h.arm();h.client.stop()
  assert.equal(h.client.getState().active,false)
  assert.equal(h.client.getState().stopState,'pending')
  assert.equal(await h.client.configure(payload),false)
  delayed.resolve(response({ok:true,state:{active:false,stop_confirmed:false}}))
  await new Promise(r=>setTimeout(r,0))
  assert.equal(h.client.getState().stopState,'unknown')
  assert.equal(await h.client.configure(payload),false)
})
test('stop accepts explicit proof and keeps a failed transport unknown', async t => {
  for(const failed of [false,true]) {
    const delayed=deferred()
    const h=clientFixture(t,action=>action==='stop'?delayed.promise:null,{endpoint:REAL_ENDPOINT})
    await h.arm();h.client.stop()
    if(failed)delayed.reject(new Error('disconnected'))
    else delayed.resolve(response({ok:true,state:{active:false,stop_confirmed:true}}))
    await new Promise(r=>setTimeout(r,0))
    assert.equal(h.client.getState().stopState,failed?'unknown':'confirmed')
    assert.match(h.client.getState().why,failed?/未知/:/确认停止/)
  }
})


test('direction moves the entire range to each robot side and survives saved draft reopen',t=>{
  const storage=new Map(),h=panelFixture(t,{storage})
  h.panel.rangeCenter.value=[120,-350,680];h.panel.rangeSize.value=[700,200,400]
  h.panel.rangeDirection.value=270
  const centers=[[350,120,680],[-120,350,680],[-350,-120,680],[120,-350,680]]
  for(const [i,direction] of [0,90,180,270].entries()) {
    h.panel.changeDirection(direction)
    assert.deepEqual(h.panel.rangeCenter.value,centers[i])
    assert.deepEqual(h.panel.rangeSize.value,[700,200,400])
    assert.equal(h.panel.rangeDirection.value,direction)
  }
  h.dispose()
  const reopened=panelFixture(t,{storage})
  assert.deepEqual(reopened.panel.rangeCenter.value,[120,-350,680])
  assert.deepEqual(reopened.panel.rangeSize.value,[700,200,400])
  assert.equal(reopened.panel.rangeDirection.value,270)
  assert.equal(reopened.requests.length,0)
})

test('restore initial range is explicit and does not replace saved user default',async t=>{
  const storage=new Map(),h=panelFixture(t,{storage})
  h.panel.rangeCenter.value=[400,0,600];h.panel.rangeSize.value=[600,200,300]
  h.panel.changeDirection(0);await h.panel.saveRangeDefault()
  const saved=h.world.snap.settings.interaction_range_default
  await h.panel.resetRange()
  assert.deepEqual(h.panel.rangeCenter.value,[-500,0,700])
  assert.deepEqual(h.panel.rangeSize.value,[900,300,500])
  assert.equal(h.panel.rangeDirection.value,270)
  assert.deepEqual(h.world.snap.settings.interaction_range_default,saved)
  assert.equal(h.requests.length,0)
  assert.match(read('panels/HandFollowPanel.vue'),/>恢复初始范围<\/button>/)
  h.dispose()
  const reopened=panelFixture(t,{storage:new Map(),snap:{settings:{interaction_range_default:saved}}})
  assert.deepEqual(reopened.panel.rangeCenter.value,saved.center)
  assert.deepEqual(reopened.panel.rangeSize.value,saved.size)
  assert.equal(reopened.panel.rangeDirection.value,saved.direction)
})

test('cardinal direction moves the restored box to that side without configuring motion',async t=>{
  const h=panelFixture(t)
  await h.panel.resetRange()
  for(const [direction,center] of [[0,[0,-500,700]],[90,[500,0,700]],[180,[0,500,700]],[270,[-500,0,700]]]) {
    h.panel.changeDirection(direction)
    assert.deepEqual(h.panel.rangeCenter.value,center)
    assert.deepEqual(h.panel.rangeSize.value,[900,300,500])
  }
  const before=h.panel.rangeCenter.value.slice()
  h.panel.changeDirection(270);h.panel.changeDirection(NaN);h.panel.changeDirection(45)
  assert.deepEqual(h.panel.rangeCenter.value,before)
  assert.equal(h.panel.rangeDirection.value,270)
  assert.equal(h.requests.length,0)
})

test('whole-range direction movement stays within the current fence',t=>{
  const h=panelFixture(t)
  h.props.fence={x:[-500,500],y:[-500,500],z:[0,1000]}
  h.panel.rangeCenter.value=[350,0,600];h.panel.rangeSize.value=[800,200,400]
  h.panel.rangeDirection.value=270
  h.panel.changeDirection(0)
  assert.deepEqual(h.panel.rangeCenter.value,[0,350,600])
  assert.deepEqual(h.panel.rangeSize.value,[800,200,400])
  assert.equal(h.requests.length,0)
})

test('front-facing local dimensions do not spuriously move the shared center at the fence',t=>{
 const h=panelFixture(t)
 h.props.fence={x:[-900,900],y:[-900,900],z:[0,1400]}
 h.panel.rangeDirection.value=270;h.panel.rangeCenter.value=[-500,0,700];h.panel.rangeSize.value=[900,300,500]
 h.panel.changeRange()
 assert.deepEqual(h.panel.rangeCenter.value,[-500,0,700])
 assert.deepEqual(h.panel.rangeSize.value,[900,300,500]);assert.equal(h.requests.length,0)
})


test('shared backend defaults override legacy local ranges and failed saves retain the editable draft',async t=>{
 const persisted={center:[-500,0,700],size:[900,300,500],direction:270,depthRange:[.08,.4]}
 const storage=new Map([['huayan.hand-follow.range-default.v1',JSON.stringify({center:[100,0,600],size:[300,200,400],direction:0,depthRange:[.06,.3]})]])
 const h=panelFixture(t,{storage,snap:{settings:{interaction_range_default:persisted}},settingsFailure:true})
 assert.deepEqual(h.panel.rangeCenter.value,persisted.center);assert.deepEqual(h.panel.rangeSize.value,persisted.size);assert.deepEqual(h.panel.depthRange.value,persisted.depthRange)
 h.panel.rangeCenter.value=[-550,0,700];h.panel.changeRange();await h.panel.saveRangeDefault()
 assert.deepEqual(h.panel.rangeCenter.value,[-550,0,700]);assert.deepEqual(h.world.snap.settings.interaction_range_default,persisted);assert.equal(h.panel.rangeNotice.value,'保存失败');assert.equal(h.requests.length,0);assert.equal(h.settingsCalls.length,1)
})


test('stopped camera does not disable start and explicit click prepares capture before following',async t=>{
 const source=createFixtureCameraSource({ref,reactive});let h,prepares=0
 source.prepareHandFollow=async()=>{prepares++;h.world.cameras.active={state:'streaming',session:'clicked-camera'};h.publish(null,{session:'clicked-camera',hands:true});return true}
 h=panelFixture(t,{cameraSource:source});h.world.cameras.active=null;h.loseCamera()
 assert.equal(h.panel.startActionBlocked.value,'');assert.equal(prepares,0)
 await h.panel.start();assert.equal(prepares,1);assert.equal(h.panel.local.value.active,true)
})

test('browser preview enables the explicit start action but cannot configure until handoff yields a matching backend frame',async t=>{
 const source=createFixtureCameraSource({ref,reactive});source.browser.value=true;Object.assign(source.browserPreview.state,{status:'streaming',frameReady:true})
 let prepares=0,h
 source.prepareHandFollow=async()=>{prepares++;source.browser.value=false;source.browserPreview.state.status='idle';h.world.cameras.active={state:'streaming',session:'prepared-camera'};h.publish(null,{session:'prepared-camera',hands:true});return true}
 h=panelFixture(t,{cameraSource:source});h.world.cameras.active=null;h.loseCamera()
 assert.equal(h.panel.environmentIssue.value,'');assert.ok(h.panel.startBlocked.value);assert.equal(h.panel.startActionBlocked.value,'');assert.equal(prepares,0);assert.equal(h.requests.length,0)
 await h.panel.start();assert.equal(prepares,1);assert.equal(h.panel.local.value.active,true)
 assert.deepEqual(h.requests.filter(c=>['configure','start'].includes(c.action)).map(c=>c.body.session),['prepared-camera','prepared-camera'])
 assert.ok(h.deadlines.includes(10000),'backend-frame wait is capped at ten seconds')
})

test('a streaming workstation enables detection only on explicit start and waits for a fresh detector-owned frame',async t=>{
 const source=createFixtureCameraSource({ref,reactive});let prepares=0,h
 source.prepareHandFollow=async()=>{prepares++;h.publish(null,{hands:true});return true}
 h=panelFixture(t,{cameraSource:source});h.publish(null,{hands:false})
 assert.equal(prepares,0);assert.equal(h.panel.startActionBlocked.value,'')
 await h.panel.start();assert.equal(prepares,1);assert.equal(h.panel.local.value.active,true)
})

test('handoff failures, stale or foreign frames never configure/start and report the real preparation failure',async t=>{
 for(const kind of ['failure','stale','foreign','detection-off']){
 const source=createFixtureCameraSource({ref,reactive});let h
 source.prepareHandFollow=async()=>{if(kind==='failure'){source.message.value='工作站相机被占用';return false}h.publish(null,kind==='stale'?{fresh:false}:kind==='foreign'?{session:'old-camera'}:{hands:false});return true}
 h=panelFixture(t,{cameraSource:source});h.loseCamera();await h.panel.start()
 // The center move is issued first and must survive any camera failure.
 assert.equal(h.centerRequests.length,1,'camera failure cannot skip or cancel the center move')
 assert.equal(h.requests.some(c=>['configure','start'].includes(c.action)),false)
 assert.equal(h.panel.local.value.active,false);assert.match(h.panel.preparationError.value,kind==='failure'?/相机被占用/:/未收到有效/)
 h.dispose()
 }
})

test('cancelled, disconnected, hidden or replaced-mode preparation cannot start after its late success',async t=>{
 for(const kind of ['cancel','disconnect','hidden','session','mode']){
 const source=createFixtureCameraSource({ref,reactive}),waiting=deferred();source.prepareHandFollow=()=>waiting.promise
 const h=panelFixture(t,{cameraSource:source});h.publish(null)
 const start=h.panel.start();await Promise.resolve();assert.equal(h.panel.preparingCamera.value,true)
 if(kind==='cancel')h.panel.toggleStart()
 if(kind==='disconnect')h.world.connected=false
 if(kind==='hidden'){h.doc.hidden=true;h.events.visibilitychange()}
 if(kind==='session')h.world.snap.session_id='replacement-backend'
 if(kind==='mode')h.world.mode='real'
 waiting.resolve(true);await start
 assert.equal(h.requests.some(c=>['configure','start'].includes(c.action)),false);assert.equal(h.panel.local.value.active,false);assert.equal(h.panel.preparingCamera.value,false)
 h.dispose()
 }
})


test('a fresh palm is submitted on publication without waiting for the panel heartbeat',async t=>{
 const h=panelFixture(t);await h.arm()
 const before=h.requests.filter(row=>row.action==='sample').length
 h.publish({...landmark,x:.65},{frameSeq:99})
 assert.equal(h.requests.filter(row=>row.action==='sample').length,before+1)
 assert.equal(h.requests.at(-1).body.frame_seq,99)
 assert.equal(h.requests.at(-1).body.u,.65)
})


test('follow presents the robot front once without changing task range, motion or user orbit thereafter',t=>{
 const views=[],initial={theta:0,phi:.8,r:4,target:[0,.62,0],fov:32}
 const scene={getView:()=>initial,setView:value=>{views.push(value);return true}}
 const h=panelFixture(t,{scene})
 assert.equal(views.length,1);assert.equal(views[0].theta,Math.PI)
 assert.deepEqual({...views[0],theta:0},initial)
 h.hooks.activate();h.hooks.deactivate();h.hooks.activate()
 assert.equal(views.length,1);assert.deepEqual(h.requests,[])
})


test('simulation begins owned center motion before slow camera and model preparation resolves',async t=>{
 const camera=deferred(),center=deferred(),source=createFixtureCameraSource({ref,reactive})
 source.prepareHandFollow=()=>camera.promise
 const h=panelFixture(t,{cameraSource:source,centerCall:(path,body)=>path.endsWith('/center')?center.promise:{ok:true,startup_id:body.startup_id,phase:'cancelled'}})
 h.world.cameras.active=null;h.loseCamera()
 const pending=h.panel.start()
 assert.equal(h.centerRequests[0].path,'/api/sim/hand-follow/center')
 assert.equal('session' in h.centerRequests[0].body,false)
 assert.equal('frame_seq' in h.centerRequests[0].body,false)
 assert.equal(h.requests.length,0)
 center.resolve({ok:true,startup_id:h.centerRequests[0].body.startup_id,phase:'waiting'});for(let i=0;i<8;i++)await Promise.resolve()
 assert.equal(h.panel.centerPhase.value,'waiting');assert.equal(h.panel.preparingCamera.value,true)
 source.message.value='Camera unavailable';camera.resolve(false);await pending
 assert.equal(h.panel.centerPhase.value,'waiting');assert.equal(h.panel.local.value.active,false)
 assert.match(h.panel.preparationError.value,/Camera unavailable/)
 assert.equal(h.centerRequests.some(row=>row.path.endsWith('center-cancel')),false)
})

test('center-first startup hands its exact visible enclosure and owned ID to actual camera configuration',async t=>{
 const h=panelFixture(t);await h.arm()
 const center=h.centerRequests.find(row=>row.path.endsWith('/center'))
 const configure=h.requests.find(row=>row.action==='configure')
 assert.equal(configure.body.startup_id,center.body.startup_id)
 assert.deepEqual(configure.body.enclosure,center.body.enclosure)
 assert.equal(configure.body.session,'camera-a')
 assert.equal(configure.body.aspect,640/480)
})

test('a mismatched center response cannot authorize this startup or configure a camera',async t=>{
 const h=panelFixture(t,{centerCall:async(path,body)=>({ok:true,startup_id:path.endsWith('/center')?'old-startup':body.startup_id,phase:path.endsWith('/center')?'waiting':'cancelled'})})
 h.publish();await h.panel.start()
 assert.match(h.panel.preparationError.value,/会话已变化/)
 assert.equal(h.requests.some(row=>['configure','start'].includes(row.action)),false)
 assert.equal(h.centerRequests.at(-1).path,'/api/sim/hand-follow/center-cancel')
 assert.equal(h.centerRequests.at(-1).body.startup_id,h.centerRequests[0].body.startup_id)
})

test('a failed center task never starts following or replaces camera errors with success',async t=>{
 const h=panelFixture(t,{centerCall:async path=>path.endsWith('/center')?{ok:false,why:'Center blocked'}:{ok:true,phase:'cancelled'}})
 h.publish();await h.panel.start()
 assert.equal(h.requests.some(row=>row.action==='start'),false)
 assert.match(h.panel.preparationError.value,/Center blocked/)
})

test('stopping before a late center reply cancels only this startup ID and cannot resurrect following',async t=>{
 const late=deferred(),source=createFixtureCameraSource({ref,reactive})
 source.prepareHandFollow=async()=>true
 const h=panelFixture(t,{cameraSource:source,centerCall:async(path,body)=>path.endsWith('/center')?late.promise:{ok:true,startup_id:body.startup_id,phase:'cancelled'}})
 h.publish();const pending=h.panel.start();const id=h.centerRequests[0].body.startup_id
 h.panel.toggleStart();assert.equal(h.centerRequests.at(-1).path,'/api/sim/hand-follow/center-cancel')
 assert.equal(h.centerRequests.at(-1).body.startup_id,id)
 late.resolve({ok:true,phase:'waiting'});await pending
 assert.equal(h.panel.local.value.active,false);assert.equal(h.requests.some(row=>row.action==='start'),false)
 assert.ok(h.centerRequests.every(row=>row.body.startup_id===id))
 assert.ok(h.centerRequests.every(row=>!row.path.includes('/motion/cancel')))
})

test('real startup never calls simulation center endpoints',async t=>{
 const h=panelFixture(t,{mode:'real',snap:readyReal()});await h.arm()
 assert.deepEqual(h.centerRequests,[])
})


test('camera opening does not disable simulation center-first start or cause a second capture attempt',async t=>{
 const source=createFixtureCameraSource({ref,reactive});source.busy.value=true
 let prepares=0;source.prepareHandFollow=async()=>{prepares++;return true}
 const h=panelFixture(t,{cameraSource:source});h.publish()
 assert.equal(h.panel.startActionBlocked.value,'')
 const pending=h.panel.start()
 assert.equal(h.centerRequests[0].path,'/api/sim/hand-follow/center')
 assert.equal(prepares,0)
 source.busy.value=false
 await pending;assert.equal(prepares,1);assert.equal(h.panel.local.value.active,true)
})


test('an old failed center response cannot cancel a newer startup',async t=>{
 const first=deferred(),second=deferred();let starts=0
 const h=panelFixture(t,{centerCall:(path,body)=>path.endsWith('/center')?(++starts===1?first.promise:second.promise):Promise.resolve({ok:true,startup_id:body.startup_id,phase:'cancelled'})})
 h.publish();const old=h.panel.start();const oldId=h.centerRequests[0].body.startup_id
 h.panel.toggleStart();const newer=h.panel.start();const newId=h.centerRequests.findLast(row=>row.path.endsWith('/center')).body.startup_id
 first.reject(Error('old connection failed'));await old
 assert.equal(h.centerRequests.some(row=>row.path.endsWith('center-cancel')&&row.body.startup_id===newId),false)
 assert.notEqual(oldId,newId)
 second.resolve({ok:true,startup_id:newId,phase:'waiting'});await newer
 assert.equal(h.panel.local.value.active,true)
})

test('hiding and returning during model preparation cannot revive a cancelled startup',async t=>{
 const camera=deferred(),source=createFixtureCameraSource({ref,reactive});source.prepareHandFollow=()=>camera.promise
 const h=panelFixture(t,{cameraSource:source});h.publish();const pending=h.panel.start()
 for(let i=0;i<8;i++)await Promise.resolve()
 h.doc.hidden=true;h.events.visibilitychange();h.doc.hidden=false;h.events.visibilitychange()
 camera.resolve(true);await pending
 assert.equal(h.requests.some(row=>row.action==='start'),false)
 assert.ok(h.centerRequests.some(row=>row.path.endsWith('center-cancel')))
})
