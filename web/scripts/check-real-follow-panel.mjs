import {createFixtureCameraSource} from './fixture-camera-source.mjs'
import * as interactionRanges from '../src/interactionRange.js'
// Offline compiled Vue setup with fake HTTP/lifecycle only. No server or device.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, ref, reactive, watch, effectScope } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as follow from '../src/handFollowInput.js'
import { createMouseFollowInput } from '../src/mouseFollowInput.js'
import { createInteractionInput, interactionModeIssue } from '../src/interactionInput.js'

const source = readFileSync(new URL('../src/panels/HandFollowPanel.vue', import.meta.url), 'utf8')
const { descriptor, errors } = parse(source)
assert.deepEqual(errors, [])
const compiled = compileScript(descriptor, { id:'real-follow-panel-offline' })
const script = compiled.content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
const response = body => ({ ok:true, status:200, json:async () => body })

function fixture(t, { mode='real', available=true, confirm=true } = {}) {
  const hooks={}, requests=[], confirmations=[], intervals=new Set(), storage=new Map()
  const world=reactive({ mode, connected:true,
    // Simulation start owns a separate center-first task; the real flow centers
    // inside the backend follow session, so this endpoint must stay unused there.
    async call(path,body){requests.push({path,body,action:path.split('/').at(-1),center:true});return {ok:true,startup_id:body.startup_id,phase:'waiting'}},
    cameras:{active:{state:'streaming',session:'camera-a'}},
    snap:{session_id:'backend-a',motion_granted:true,real_speed_pct:5,
      real:{stream_up:true,cmd_up:true,quality:'valid',override:.05},
      real_hand_follow:{available,phase:'idle',active:false,configured:false,blockers:[],
        why:available?'等待开始':'尚未完成现场验证',driver:'StartServo/PushServoJ',
        control_policy:'real-palm-position-j6-zero-v1',j6_deg:0},hand_follow:null} })
  const browser={addEventListener(){},removeEventListener(){}}
  const deps={useCameraSource:()=>createFixtureCameraSource({ref,reactive}),setTimeout:fn=>setTimeout(fn,0),clearTimeout,...interactionRanges,computed,ref,watch,useWorld:()=>world,document:{...browser,hidden:false},
    window:{...browser,confirm:message=>{confirmations.push(message);return confirm}},
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
    readTabState:()=>null,saveTabState(){},
    onMounted:fn=>{hooks.mount=fn},onBeforeUnmount:fn=>{hooks.unmount=fn},
    onActivated:fn=>{hooks.activate=fn},onDeactivated:fn=>{hooks.deactivate=fn},
    setInterval:fn=>{intervals.add(fn);return fn},clearInterval:fn=>intervals.delete(fn),
    ...follow,createMouseFollowInput,createInteractionInput,interactionModeIssue,
    createHandFollowClient:options=>follow.createHandFollowClient({...options,
      fetcher:async(path,options)=>{
        assert.match(path,/^\/api\/(real|sim)\/hand-follow\/(configure|start|sample|stop)$/)
        const action=path.split('/').at(-1),body=JSON.parse(options.body)
        requests.push({path,action,body})
        return response({ok:true,state:{active:action==='start'||action==='sample',configured:true,
          session:'camera-a',token:'owned',phase:action==='start'?'centering':'idle'}})
      }})}
  const component=new Function(...Object.keys(deps),script)(...Object.values(deps))
  const scope=effectScope(),props=reactive({visualModel:'elfin5',direction:270,fence:null})
  const panel=scope.run(()=>component.setup(props,{expose(){},emit(){}}))
  const producer=Symbol('offline-camera')
  hooks.mount()
  const publish=point=>{
    const now=performance.now()
    follow.publishHandFollowInput({session:'camera-a',stream:'color',fresh:true,hands:true,
      width:640,height:480,frameSeq:Math.max(1,Math.round(now*1000)),frameUpdatedAt:now,timestamp:now,
      point:point===undefined?{x:.5,y:.5,palm_scale:.12}:point},producer)
  }
  publish()
  t.after(()=>{hooks.unmount();scope.stop();follow.publishHandFollowInput(null,producer)})
  return {panel,world,requests,confirmations,publish,hooks,intervals}
}

test('real panel script, template and scoped CSS compile',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'HandFollowPanel.vue',
    id:'real-follow-panel-offline',compilerOptions:{bindingMetadata:compiled.bindings}}).errors,[])
  for(const style of descriptor.styles) assert.deepEqual(compileStyle({source:style.content,id:'real-follow-panel-offline',scoped:true}).errors,[])
})

test('mount and arbitrary server phases never arm or acquire credentials',t=>{
  const h=fixture(t)
  const labels={idle:'未启动',centering:'正在前往中心 · J6 归零',waiting_hand:'已到中心 · 等待手掌',
    starting:'正在启动连续跟随',following:'正在跟随 · J6 保持 0°',stopping:'正在停止 · 等待反馈确认',
    stopped:'已停止',fault:'故障 · 禁止继续运动'}
  for(const [phase,label] of Object.entries(labels)){
    h.world.snap.real_hand_follow.phase=phase
    assert.equal(h.panel.realPhase.value,label)
    assert.equal(h.panel.local.value.active,false)
    assert.equal(h.panel.local.value.server,null)
  }
  h.world.snap.real_hand_follow.phase='future-unknown'
  assert.equal(h.panel.realPhase.value,'状态待确认')
  assert.equal(h.requests.length,0)
})

test('missing qualification remains blocked with concise per-layer details',async t=>{
  const h=fixture(t,{available:false})
  h.world.snap.real_hand_follow.blockers=[
    {layer:'tool',code:'tcp',path:'tcp',detail:'请核对实际工具中心点'},
    {layer:'qualification',code:'missing',path:'records',detail:'尚无现场验证记录'},
    {layer:'future',detail:' 新条件 '},null,{layer:'payload',detail:''}]
  assert.deepEqual(h.panel.realBlockers.value,[
    {layer:'工具',detail:'请核对实际工具中心点'},
    {layer:'现场验证',detail:'尚无现场验证记录'},
    {layer:'启动条件',detail:'新条件'},
    {layer:'负载',detail:'未通过检查，请核对后台配置'}])
  await h.panel.start()
  assert.equal(h.requests.length,0)
  assert.equal(h.confirmations.length,0)
  assert.match(h.panel.startBlocked.value,/现场验证/)
  h.world.snap.real_hand_follow.blockers={bad:true}
  assert.deepEqual(h.panel.realBlockers.value,[])
  assert.ok(h.panel.startBlocked.value)
})

test('explicit confirmation describes center-first J6 zero; cancellation sends nothing',async t=>{
  const h=fixture(t,{confirm:false})
  await h.panel.start()
  assert.equal(h.confirmations.length,1)
  const message=h.confirmations[0]
  assert.match(message,/先受控前往工作区中心，J6 移动到 0°/)
  assert.match(message,/实际到位后才开始手掌跟随/)
  assert.match(message,/不会自动连接、使能或标定/)
  assert.doesNotMatch(message,/无工具确认|不自动居中|0\.25°|兼容跟随/)
  assert.equal(h.requests.length,0)
})

test('confirmed start only uses existing configure/start API; no commissioning unlock',async t=>{
  const h=fixture(t)
  await h.panel.start()
  assert.deepEqual(h.requests.map(item=>item.action),['configure','start'])
  assert.equal(h.requests[0].body.rotation,false)
  assert.equal(h.requests[1].body.confirm_real,true)
  for(const item of h.requests){
    assert.doesNotMatch(JSON.stringify(item.body),/qualification|commissioning|verified|unlock|enable|MoveJ|StartServo/)
  }
})

test('motor permission and actual speed remain mandatory',async t=>{
  const h=fixture(t)
  h.world.snap.motion_granted=false
  await h.panel.start()
  assert.match(h.panel.startBlocked.value,/运动权限/)
  h.world.snap.motion_granted=true
  h.world.snap.real.override=.1
  await h.panel.start()
  assert.match(h.panel.startBlocked.value,/反馈速度/)
  assert.equal(h.requests.length,0)
})

test('waiting client text cannot overwrite the server center-first phase',t=>{
  const h=fixture(t)
  h.panel.local.value={active:true,configured:true,busy:false,waiting:true,
    why:'等待手掌；不会自动居中',server:{session:'camera-a',token:'owned'}}
  h.world.snap.real_hand_follow={available:true,phase:'centering',session:'camera-a',token:'owned',active:true,
    control_policy:'real-palm-position-j6-zero-v1'}
  assert.equal(h.panel.statusText.value,'正在前往中心 · J6 归零')
  h.world.snap.real_hand_follow.phase='waiting_hand'
  assert.equal(h.panel.statusText.value,'已到中心 · 等待手掌')
  h.panel.local.value.waiting=false
  assert.equal(h.panel.statusText.value,'已到中心 · 等待手掌')
  h.world.snap.real_hand_follow.blocked_reason='路径检查未通过'
  assert.equal(h.panel.statusText.value,'路径检查未通过')
})

test('missing or legacy policy blocks configure/start even when backend says available',async t=>{
  const h=fixture(t)
  for(const policy of [undefined,null,'finite_relj_compat','real-palm-position-j6-zero-v2']){
    h.world.snap.real_hand_follow.control_policy=policy
    // Even a cached HTTP reply cannot establish current backend support.
    h.panel.local.value.server={control_policy:'real-palm-position-j6-zero-v1'}
    assert.equal(h.panel.realFlowSupported.value,false)
    assert.match(h.panel.configureBlocked.value,/新流程尚未接入/)
    assert.match(h.panel.startBlocked.value,/禁止新启动/)
    assert.equal(h.panel.realPhase.value,'新流程尚未接入')
    h.publish(null)
    assert.doesNotMatch(h.panel.detectionStatus.value,/先到中心|到达中心/)
    await h.panel.configure()
    await h.panel.start()
  }
  assert.equal(h.requests.length,0)
  assert.equal(h.confirmations.length,0)
})

test('legacy gate never disables explicit stop for an already owned session',async t=>{
  const h=fixture(t)
  await h.panel.start()
  assert.equal(h.panel.local.value.active,true)
  h.world.snap.real_hand_follow.control_policy='finite_relj_compat'
  assert.equal(h.panel.local.value.active,true)
  assert.match(h.panel.startBlocked.value,/禁止新启动/)
  h.panel.stop()
  assert.equal(h.panel.local.value.active,false)
  assert.equal(h.requests.at(-1).action,'stop')
  assert.equal(h.requests.at(-1).body.token,'owned')
})

test('simulation keeps its own endpoint, no real phase/blockers or confirmation',async t=>{
  const h=fixture(t,{mode:'sim'})
  h.world.snap.real=null
  h.publish(null)
  await h.panel.start()
  assert.equal(h.panel.realPhase.value,'')
  assert.deepEqual(h.panel.realBlockers.value,[])
  assert.equal(h.confirmations.length,0)
  // Center-first runs before any workspace/start call, and only against sim.
  assert.deepEqual(h.requests.map(item=>item.path),['/api/sim/hand-follow/center','/api/sim/hand-follow/configure','/api/sim/hand-follow/start'])
})

test('template uses escaped blockers and removes retired compatibility claims',()=>{
  assert.match(descriptor.template.content,/阶段：\{\{realPhase\}\}/)
  assert.match(descriptor.template.content,/\{\{item.layer\}\}：\{\{item.detail\}\}/)
  assert.doesNotMatch(descriptor.template.content,/v-html/)
  assert.doesNotMatch(source,/系统工具入口确认无工具|单步上限 0\.25|启动不自动居中/)
  assert.match(descriptor.template.content,/<p v-if="realFlowSupported"[^>]*>先到中心/)
  assert.match(descriptor.template.content,/<p v-if="isReal && realFlowSupported"/)
  const disabled = descriptor.template.content.match(/:disabled="([^"\n]*local\.stopState === 'pending'[^"\n]*)"/)[1]
  // The template now gates on the real-mode retry state and the merged start
  // blocker, so the harness must supply the same bindings the panel exposes.
  const isDisabled = new Function('local','startActionBlocked','rangeError','isReal',`return ${disabled}`)
  for (const state of [{active:true,busy:false,stopState:'idle'},{active:false,busy:true,stopState:'idle'},{active:false,busy:false,stopState:'unknown'}]) {
    assert.equal(isDisabled(state,'permission denied','invalid range',true),false,'stop/retry remains reachable despite start blockers')
  }
  assert.equal(isDisabled({active:false,busy:false,stopState:'pending'},'','',true),true,'duplicate pending stop is prevented')
  // Simulation never renders a retry-stop button: an unknown stop result there
  // means "start again", so start blockers must keep that button disabled.
  assert.equal(isDisabled({active:false,busy:false,stopState:'unknown'},'permission denied','invalid range',false),true,'simulated start stays blocked while start blockers exist')
})
