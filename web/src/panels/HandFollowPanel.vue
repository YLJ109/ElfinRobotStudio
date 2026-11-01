<script setup>
import { frontTaskView } from '../taskFrontView.js'
import { computed, ref, onMounted, onBeforeUnmount, onActivated, onDeactivated, watch } from 'vue'
import { useWorld } from '../store'
import { useCameraSource } from '../cameraSource.js'
import { INTERACTION_RANGE_FACTORY, readInteractionRangeDefault, saveInteractionRangeDefault, interactionRange, rotateInteractionCenter } from '../interactionRange.js'
import { readTabState, saveTabState } from '../workspaceState.js'
import { createMouseFollowInput } from '../mouseFollowInput.js'
import { createHandFollowClient, getHandFollowInput, subscribeHandFollowInput,
  handFollowInputIssue, handFollowFrameIssue, requestHandFollowVision, setHandFollowDepthRange,
  setHandFollowDetectionDemand, selectHandFollowSnapshot } from '../handFollowInput.js'

const props = defineProps({ visualModel: { type:String, default:'' }, direction:{type:Number,default:270}, fence:{type:Object,default:null} })
const emit = defineEmits(['direction','box-preview'])
const w = useWorld()
const cameraSource=useCameraSource(w),preparingCamera=ref(false),preparationError=ref(''),prepareEpoch=ref(0),centerPhase=ref('')
let centerStartup=null
const browserReady=computed(()=>cameraSource.browser.value&&cameraSource.browserPreview.state.status==='streaming'&&cameraSource.browserPreview.state.frameReady===true)
const isReal = computed(() => w.mode === 'real')
const savedInputMode = readTabState('huayan.follow.input')
const inputMode = ref(!isReal.value && savedInputMode === 'mouse' ? 'mouse' : 'camera')
// 只有手掌与鼠标两种输入源。合成输入只可能是鼠标，且只用于仿真。
const mouseMode = computed(() => !isReal.value && inputMode.value === 'mouse')
const syntheticMode = mouseMode
const inputTabs = [{id:'camera',label:'手掌'},{id:'mouse',label:'鼠标'}]
const savedMouseSession = readTabState('huayan.follow.mouse-session')
const mouseSession = typeof savedMouseSession === 'string' && /^mouse-[a-zA-Z0-9-]{6,120}$/.test(savedMouseSession)
  ? savedMouseSession : `mouse-${globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}`
saveTabState('huayan.follow.mouse-session',mouseSession)
const mouse = createMouseFollowInput(mouseSession,readTabState('huayan.follow.mouse-position'))
const mousePosition = ref(mouse.position())
const mousePresent = ref(false)
watch(mousePosition,position=>saveTabState('huayan.follow.mouse-position',position),{flush:'sync'})
function moveMouse(event) {
  if (event.pointerType === 'touch') event.preventDefault()
  if (mouse.move(event.clientX,event.clientY,event.currentTarget.getBoundingClientRect())) {
    mousePresent.value=true;mousePosition.value=mouse.position()
  }
}
function mouseDepth(event) { mousePresent.value=true;mouse.depth(Number(event.target.value)); mousePosition.value=mouse.position() }
function centerMouse() { mouse.center(); mousePosition.value=mouse.position() }
function leaveMouse() {
  mousePresent.value=false;centerMouse()
  if (mouseMode.value && local.value.active) void client.sample(null)
}
function mouseFocusOut(event) { if (!event.currentTarget.contains(event.relatedTarget)) leaveMouse() }
function currentInput(forConfiguration=false) {
  return mouseMode.value ? (forConfiguration || mousePresent.value ? mouse.frame(performance.now()) : null) : cameraInput.value
}
const liveSnapshot = computed(() => selectHandFollowSnapshot(w.snap, w.mode))
const realFlowSupported = computed(() => isReal.value &&
  liveSnapshot.value?.control_policy === 'real-palm-position-j6-zero-v1')
const realFlowIssue = computed(() => isReal.value && !realFlowSupported.value
  ? '真机新流程尚未接入，当前后端不支持先到中心、J6 归零；禁止新启动，仍可停止已有跟随。' : '')
const depth = ref(true), rotation = ref(false), visible = ref(!document.hidden)
const DEFAULT_RANGE_CENTER = INTERACTION_RANGE_FACTORY.center, DEFAULT_RANGE_SIZE = INTERACTION_RANGE_FACTORY.size
const DRAFT_KEY = 'huayan.hand-follow.draft.v1'
const DIRECTIONS = [0,90,180,270]
const rangeDefault = readInteractionRangeDefault(w,{storage:localStorage})
function readDraft() {
  try {
    const value=JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null')
    if (value?.version !== 1 || !Array.isArray(value.center) || value.center.length !== 3 ||
      !Array.isArray(value.size) || value.size.length !== 3 || ![...value.center,...value.size].every(Number.isFinite) ||
      value.center.some(v=>Math.abs(v)>10000) || value.size.some(v=>v<=0 || v>5000) || !DIRECTIONS.includes(value.direction) ||
      !Array.isArray(value.depthRange) || value.depthRange.length !== 2 ||
      value.depthRange.some(v=>!Number.isFinite(v) || v<.005 || v>2) || value.depthRange[1]-value.depthRange[0]<.01) return null
    return value
  } catch { return null }
}
const draft = readDraft()
if (typeof draft?.depth === 'boolean') depth.value=draft.depth
if (typeof draft?.rotation === 'boolean') rotation.value=draft.rotation
if (isReal.value) rotation.value=false
const settingsOpen=ref(draft?.settingsOpen === true), depthSettingsOpen=ref(draft?.depthSettingsOpen === true)
const rangeCenter = ref([...rangeDefault.center]), rangeSize = ref([...rangeDefault.size]), rangeCustom = ref(true)
// Keep the requested direction locally until the parent prop update arrives.
// The center is always world XYZ; size remains local width/depth/height.
const rangeDirection = ref(rangeDefault.direction)
let initialDirectionSync=props.direction!==rangeDirection.value?rangeDirection.value:null
const depthRange = ref(rangeDefault.depthRange.slice())
const rangeEdited=ref(false),savingRangeDefault=ref(false)
watch([rangeCenter,rangeSize,rangeDirection,depthRange,depth,rotation,settingsOpen,depthSettingsOpen], () => {
  const value={version:1,center:rangeCenter.value.slice(),size:rangeSize.value.slice(),direction:rangeDirection.value,
    depthRange:depthRange.value.slice(),depth:depth.value,rotation:rotation.value,
    settingsOpen:settingsOpen.value,depthSettingsOpen:depthSettingsOpen.value}
  try { localStorage.setItem(DRAFT_KEY,JSON.stringify(value)) } catch { /* UI remains usable without storage. */ }
}, {deep:true,flush:'sync'})
watch(depthRange, value=>setHandFollowDepthRange(value), {deep:true,immediate:true})
const rangeNotice = ref('')
const rangeError = computed(() => {
  if (!rangeCustom.value) return ''
  return [...rangeCenter.value,...rangeSize.value].every(v=>typeof v==='number' && Number.isFinite(v)) &&
    rangeSize.value.every(v=>v>0 && v<=5000) && rangeCenter.value.every(v=>Math.abs(v)<=10000) ? '' : '请填写有效坐标和尺寸（mm）'
})
const mounted = ref(true), cameraInput = ref(getHandFollowInput()), clock = ref(performance.now())
const local = ref({ active:false, configured:false, busy:false, why:'就绪，点击开始跟随', server:null })
let timer = null, unsubscribe = null, client = null, ownedSnapshotSeen = false
const OWNER_KEY='huayan.hand-follow.owner.v1'
let refreshOwner=readTabState(OWNER_KEY), handingOff=false
function rememberOwner(state=local.value) {
  if(!isReal.value && state.active && state.server?.token && w.snap?.session_id) {
    refreshOwner={...state,mode:'sim',endpoint:syntheticMode.value?'/api/sim/mouse-follow':'/api/sim/hand-follow',backendSession:w.snap.session_id,seq:state.seq ?? Math.max(0,state.server.seq || 0)}
    saveTabState(OWNER_KEY,refreshOwner)
  } else {refreshOwner=null;saveTabState(OWNER_KEY,null)}
}
function resumeRefreshOwner() {
  if(isReal.value || refreshOwner?.mode === 'real') {refreshOwner=null;saveTabState(OWNER_KEY,null);return}
  if(!refreshOwner || !w.connected || !w.snap || local.value.active || local.value.busy)return
  const saved=refreshOwner
  if ((saved.endpoint || '/api/sim/hand-follow') !== (syntheticMode.value?'/api/sim/mouse-follow':'/api/sim/hand-follow')) return
  if(saved.backendSession!==w.snap.session_id || !w.snap.hand_follow?.active ||
      saved.server?.token!==w.snap.hand_follow.token) {refreshOwner=null;saveTabState(OWNER_KEY,null);return}
  if(!activeGuard.value && client?.resumeOwned(saved,w.snap.hand_follow,w.snap.session_id))ownedSnapshotSeen=true
}
const camera = computed(() => w.cameras?.active)
const modeGuard = computed(() => {
  if (props.visualModel !== 'elfin5') return '请在模型 / 工具中切换为 Elfin5'
  if (!['sim','real'].includes(w.mode)) return '请选择仿真或真机模式'
  if (!isReal.value && (w.snap?.real?.stream_up || w.snap?.real?.cmd_up || w.snap?.real?.connected)) return '请先断开真机连接'
  return ''
})
// These are frontend prerequisites, not a replacement for controller/tool safety.
// Configure still asks the backend for its own refusal when motion is unavailable.
const activeGuard = computed(() => {
  if (modeGuard.value) return modeGuard.value
  if (!isReal.value) return ''
  if (!mounted.value || !visible.value) return '跟随页面不可见'
  if (!w.connected || w.stateFresh === false || !w.snap) return '等待有效后端状态'
  if (liveSnapshot.value?.available === false) return liveSnapshot.value.why || '真机跟随尚未就绪，请查看启动条件'
  const real = w.snap.real
  if (!real?.stream_up || !real?.cmd_up) return '真机取流或命令连接未就绪'
  if (real.quality !== 'valid') return '真机反馈已失效'
  if (w.snap.motion_granted !== true) return '控制器尚未授予运动权限'
  const speed = w.snap.real_speed_pct, override = real.override
  if (!Number.isFinite(speed) || speed < 0 || speed > 5) return '真机目标速度须不高于 5%'
  if (!Number.isFinite(override) || override < 0 || override > .05) return '控制器反馈速度须不高于 5%'
  return ''
})
const environmentIssue = computed(() => {
  if (!mounted.value || !visible.value) return '跟随页面不可见'
  if (modeGuard.value) return modeGuard.value
  if (isReal.value && liveSnapshot.value?.available === false) return liveSnapshot.value.why || '真机跟随尚未就绪，请查看启动条件'
  if (!w.connected || w.stateFresh === false || !w.snap) return '等待有效后端状态'
  if (syntheticMode.value) return ''
  if(browserReady.value)return ''
  if (camera.value?.state !== 'streaming') return '请在右上角启动摄像头'
  if (!cameraInput.value || cameraInput.value.session !== camera.value.session) return '等待右上角彩色画面'
  return ''
})
const inputIssue = computed(() => handFollowInputIssue(cameraInput.value, clock.value))
const configureBlocked = computed(() => realFlowIssue.value || environmentIssue.value || (syntheticMode.value ? '' : handFollowFrameIssue(cameraInput.value, clock.value)))
const startBlocked = computed(() => configureBlocked.value || (isReal.value ? activeGuard.value : ''))
const startActionBlocked=computed(()=>{
  if(preparingCamera.value)return ''
  if(cameraSource.busy.value&&isReal.value)return '相机操作中'
  if(syntheticMode.value)return startBlocked.value
  const base=realFlowIssue.value||modeGuard.value||(!mounted.value||!visible.value?'跟随页面不可见':'')||(!w.connected||w.stateFresh===false||!w.snap?'等待有效后端状态':'')||(isReal.value?activeGuard.value:'')
  return base
})
const cameraStatus = computed(() => browserReady.value?'浏览器画面已连接':camera.value?.state === 'streaming'
  ? (cameraInput.value?.fresh ? '彩色画面已连接' : '等待有效彩色画面')
  : camera.value?.state === 'starting' ? '相机连接中' : '请在右上角启动摄像头')
const frameRates = computed(() => {
  const capture = camera.value?.actual_fps_by_stream?.color ?? camera.value?.actual_fps
  const detect = cameraInput.value?.detectionFps
  const format = value => Number.isFinite(value) && value >= 0 ? value.toFixed(1) : '—'
  return `采集 ${format(capture)} / 检测 ${cameraInput.value?.hands ? format(detect) : '—'} fps`
})
const detectionStatus = computed(() => {
  if (camera.value?.state !== 'streaming') return '等待相机'
  if (cameraInput.value?.detectorError) return cameraInput.value.detectorError
  if (!cameraInput.value?.hands) return '手掌检测准备中'
  if (cameraInput.value?.detectorStatus === '手掌模型加载中…') return isReal.value ?
    (realFlowSupported.value ? '手掌模型加载中；到达中心后等待手掌' : '手掌模型加载中；新跟随流程尚未接入') : '手掌模型加载中，可直接开始'
  if (cameraInput.value?.trackingState === 'holding') return '短暂漏检，保留标记'
  if (inputIssue.value) return isReal.value ? (!realFlowSupported.value ? '等待手掌；新跟随流程尚未接入' : local.value.handSeen ? '手掌丢失，需手动重新开始' : '等待手掌；先到中心再跟随') :
    local.value.active ? (telemetry.value?.returning ? '手掌已丢失1秒，返回中心' : '短暂漏检，保持位置') : '未检测到手掌，可直接开始'
  return '已定位掌心'
})
const telemetry = computed(() => {
  const live = liveSnapshot.value, reply = local.value.server
  // Snapshot data is display-only; it must never re-arm this mount.
  return live && (!reply || live.session === reply.session) ? live : reply
})
// Display-only server state. Never turns readiness, credentials or motion on.
const realPhaseLabels = {
  idle:'未启动', centering:'正在前往中心 · J6 归零', waiting_hand:'已到中心 · 等待手掌',
  starting:'正在启动连续跟随', following:'正在跟随 · J6 保持 0°',
  stopping:'正在停止 · 等待反馈确认', stopped:'已停止', fault:'故障 · 禁止继续运动',
}
const realPhase = computed(() => !isReal.value ? '' : !realFlowSupported.value
  ? '新流程尚未接入' : realPhaseLabels[telemetry.value?.phase] || '状态待确认')
const realBlockerLayers = {
  hardware:'硬件', controller:'控制器', firmware:'固件', tool:'工具', tcp:'工具中心点',
  payload:'负载', transport:'通信', qualification:'现场验证', commissioning:'现场验证',
  safety:'安全检查', motion:'运动', camera:'相机', vision:'手掌检测', profile:'控制参数',
}
const realBlockers = computed(() => {
  if (!isReal.value || !Array.isArray(telemetry.value?.blockers)) return []
  return telemetry.value.blockers.filter(item => item && typeof item === 'object').map(item => ({
    layer:realBlockerLayers[item.layer] || '启动条件',
    // Details come from the backend; interpolation escapes them, never v-html.
    detail:typeof item.detail === 'string' && item.detail.trim() ? item.detail.trim() : '未通过检查，请核对后台配置',
  }))
})
const refreshNotice = computed(() => !local.value.active && !local.value.busy && liveSnapshot.value?.active
  ? '后台存在跟随会话，当前页面未接管；刷新不会自动恢复运动，请确认后手动开始。' : '')
const statusText = computed(() => (preparingCamera.value&&(centerPhase.value==='centering'?'正在前往中心，准备手掌检测':centerPhase.value==='waiting'?'已到中心，等待手掌检测':'正在准备手掌检测')) || preparationError.value || (local.value.stopState === 'pending' && ('停止待确认 · ' + local.value.why)) || (local.value.stopState === 'unknown' && isReal.value && local.value.why) || (local.value.busy && local.value.why) ||
  (local.value.rejected && local.value.why) ||
  (local.value.active && telemetry.value?.blocked_reason) ||
  (local.value.active && local.value.waiting && (isReal.value ? realPhase.value : local.value.why)) || rangeError.value || (!local.value.active && startActionBlocked.value) ||
  (local.value.active ? environmentIssue.value : '') ||
  (local.value.active && isReal.value && realFlowSupported.value && telemetry.value?.phase && realPhase.value) || (local.value.active &&
  (telemetry.value?.why || telemetry.value?.status)) || local.value.why || '等待建立工作区')
const solverLimited = computed(() => !isReal.value && local.value.active &&
  ['保持位置', '局部跟随'].includes(telemetry.value?.status) && !telemetry.value?.blocked_reason)
const displayStatus = computed(() => solverLimited.value
  ? telemetry.value.status === '局部跟随' ? '正在小步跟随' : '当前方向求解受限，可向内或反向移动'
  : statusText.value)
function makeClient(mode) {
  return createHandFollowClient({ endpoint:mode === 'real' ? '/api/real/hand-follow' : syntheticMode.value ? '/api/sim/mouse-follow' : '/api/sim/hand-follow',
  guard:action => w.mode !== mode ? '模式已切换，请重新建立工作区' :
    action==='sample' ? activeGuard.value :
    (action==='start' && isReal.value ? activeGuard.value : '') || environmentIssue.value ||
    (syntheticMode.value ? '' : handFollowFrameIssue(cameraInput.value, performance.now())),
  onChange:state => {
    if (state.active && !local.value.active) {
      const live = selectHandFollowSnapshot(w.snap, mode)
      // WS may confirm before HTTP start completes. Compare against the token
      // acquired from HTTP; never adopt a token or active state from snapshots.
      ownedSnapshotSeen = !!live && live.session === state.server?.session &&
        live.token === state.server?.token && live.active === true
    } else if (!state.active) ownedSnapshotSeen = false
    local.value = state
    rememberOwner(state)
  } })
}
client = makeClient(w.mode)
if (isReal.value) {refreshOwner=null;saveTabState(OWNER_KEY,null)}

function stop(why = '已停止') { prepareEpoch.value++;preparingCamera.value=false;cancelCenterStartup();setHandFollowDetectionDemand(false);client.stop(why) }
// 只有"真的不能再继续"的原因才允许打断跟随。相机短暂掉帧、页面瞬时不可见
// 这类软条件只用于提示，交给后端保持位置/回中，不再直接把人踢停。
const hardStopReason = computed(() => modeGuard.value || (isReal.value ? activeGuard.value : ''))
let validateTimer = null, validateAt = 0
// 一帧画面会经接收、解码、巡检多条路径触发校验；合并到 8Hz 足够跟手。
// 只用于画面到达路径：后台快照与守卫变化的撤销必须按到达顺序立即判定，
// 合并会把"先看到自有令牌、再看到令牌被换掉"这一对快照压成一次，丢失撤销。
function validateSoon() {
  if (validateTimer != null) return
  const wait = Math.max(0, 120 - (performance.now() - validateAt))
  validateTimer = setTimeout(() => {
    validateTimer = null; validateAt = performance.now(); validate()
  }, wait)
}
function validate() {
  if(handingOff||preparingCamera.value)return
  resumeRefreshOwner()
  clock.value = performance.now()
  const live = liveSnapshot.value, owned = local.value.server
  if (local.value.active && live?.session === owned?.session) {
    // HTTP/WS have independent queues: any number of old configured snapshots
    // (including new object references) may arrive after the start reply.
    // Wait for the first owned active token before trusting foreign-token
    // revocation. Owned-token inactive is authoritative even before that point.
    if (live.token === owned.token && live.active === true) ownedSnapshotSeen = true
    else if (live.token === owned.token || ownedSnapshotSeen) {
      const why = live.blocked_reason || live.why
      stop(why || '后台跟随已停止，请重新建立工作区')
      if (why) local.value.rejected=true
      return
    }
  }
  const reason = hardStopReason.value
  if (reason && (local.value.active || local.value.busy || local.value.configured)) stop(reason)
}
// 建立工作区的前置条件：只挡"确实建不了工作区"的原因——模式/模型不符、
// 后端尚未接入新流程、后端明确说真机不可用、后端状态还没有。
// 真机取流、权限与反馈速度由后台裁决：前端不替后台拒绝，后台会给出自己的原因。
const configureGate = computed(() => modeGuard.value || realFlowIssue.value ||
  (isReal.value && liveSnapshot.value?.available === false ? (liveSnapshot.value.why || '真机跟随尚未就绪，请查看启动条件') : '') ||
  (!w.connected || w.stateFresh === false || !w.snap ? '等待有效后端状态' : ''))
async function configure(startupId=null) {
  validate()
  const source = currentInput(true)
  // 相机瞬时掉帧不再让建立工作区直接失败（等一秒就有了）。
  if (configureGate.value || rangeError.value || local.value.active || local.value.busy) return false
  if (!source?.session || !(source.width > 0) || !(source.height > 0)) return false
  const owner = client
  const enclosure = rangeCustom.value || startupId ? {
    min:rangeCenter.value.map((v,i)=>v-rangeSize.value[i]/2),
    max:rangeCenter.value.map((v,i)=>v+rangeSize.value[i]/2),
  } : null
  const ok = await owner.configure({ session:source.session, aspect:source.width / source.height,
    direction:rangeDirection.value, depth:depth.value, rotation:isReal.value || syntheticMode.value ? false : rotation.value, depth_mode:'gesture',
    ...(enclosure ? {enclosure} : {}),...(startupId?{startup_id:startupId}:{}) })
  if (owner !== client) return false
  if (ok && !rangeCustom.value) syncRange(owner.getState().server?.outer_bounds)
  if (ok) emit('box-preview',null)
  return ok
}
function cancelCenterStartup() {
  const current=centerStartup
  if(!current)return
  current.cancelled=true;centerStartup=null;centerPhase.value=''
  void w.call('/api/sim/hand-follow/center-cancel',{startup_id:current.id}).catch(error=>{
    preparationError.value='中心动作取消未确认：'+(error.message||'请求失败')
  })
}
async function moveToCenter(epoch,owner,mode,session) {
  cancelCenterStartup()
  const current={id:globalThis.crypto?.randomUUID?.() || `startup-${Date.now()}-${Math.random().toString(36).slice(2)}`,cancelled:false}
  centerStartup=current;centerPhase.value='centering'
  const frame=cameraInput.value
  const aspect=Number.isFinite(frame?.width)&&Number.isFinite(frame?.height)&&frame.height>0?frame.width/frame.height:4/3
  const payload={startup_id:current.id,aspect,depth:depth.value,rotation:rotation.value,direction:rangeDirection.value,depth_mode:'gesture',
    enclosure:{min:rangeCenter.value.map((v,i)=>v-rangeSize.value[i]/2),max:rangeCenter.value.map((v,i)=>v+rangeSize.value[i]/2)}}
  try {
  let result=await w.call('/api/sim/hand-follow/center',payload)
  const started=performance.now()
  while(true){
    if(current.cancelled || !preparationCurrent(epoch,owner,mode,session)){
      if(centerStartup===current)cancelCenterStartup()
      else void w.call('/api/sim/hand-follow/center-cancel',{startup_id:current.id}).catch(()=>{})
      return null
    }
    if(!result?.ok || ['failed','cancelled'].includes(result.phase))throw Error(result?.why || '前往中心失败')
    if(result.startup_id!==current.id)throw Error('中心动作会话已变化，请重试')
    if(result.phase==='waiting'){centerPhase.value='waiting';return current.id}
    if(result.phase!=='centering')throw Error('中心动作状态无效')
    if(performance.now()-started>120000){cancelCenterStartup();throw Error('前往中心超时，请重试')}
    await new Promise(resolve=>setTimeout(resolve,150))
    result=await w.call('/api/sim/hand-follow/center-status',{startup_id:current.id})
  }
  }catch(error){if(centerStartup===current)cancelCenterStartup();throw error}
}
function waitCameraIdle(epoch,owner,mode,session){
 if(!cameraSource.busy.value)return Promise.resolve(true)
 return new Promise(resolve=>{
  let done=false,timer=null,unwatch=()=>{}
  const finish=value=>{if(done)return;done=true;clearTimeout(timer);unwatch();resolve(value)}
  const check=()=>{if(!preparationCurrent(epoch,owner,mode,session))finish(false);else if(!cameraSource.busy.value)finish(true)}
  unwatch=watch(()=>[cameraSource.busy.value,prepareEpoch.value,w.mode,w.snap?.session_id,w.connected,w.stateFresh,mounted.value,visible.value],check,{flush:'sync'})
  timer=setTimeout(()=>finish(false),15000);check()
 })
}
function preparationCurrent(epoch,owner,mode,session){
 return epoch===prepareEpoch.value&&owner===client&&w.mode===mode&&w.snap?.session_id===session&&mounted.value&&visible.value&&w.connected&&w.stateFresh!==false&&!syntheticMode.value&&!modeGuard.value&&!(isReal.value&&activeGuard.value)
}
function waitCameraFrame(epoch,owner,mode,session,timeout=10000){
 const expectedCamera=camera.value?.session
 return new Promise(resolve=>{
  let done=false,timer=null,unwatch=()=>{}
  const finish=value=>{if(done)return;done=true;clearTimeout(timer);unwatch();resolve(value)}
  const check=()=>{
   if(!preparationCurrent(epoch,owner,mode,session)){finish(false);return}
   const frame=cameraInput.value,active=camera.value
   if(!expectedCamera||active?.session!==expectedCamera){finish(false);return}
   if(mode==='real'&&frame?.session===expectedCamera&&frame?.detectorError){preparationError.value=String(frame.detectorError);finish(false);return}
   // 仍然要求"检测到手"才放行：画面过期、别的会话、检测关闭都必须拒绝启动，
   // 否则会带着一份不可用的输入进跟随。原来的问题是**等太久**（10 秒 + 30 秒
   // 两轮），不是判据本身 —— 判据放宽会让上面那些情况混进来。
   if(active?.state==='streaming'&&frame?.session===active.session&&frame.hands===true&&!handFollowFrameIssue(frame,performance.now()))finish(true)
  }
  unwatch=watch(()=>[prepareEpoch.value,w.mode,w.snap?.session_id,w.connected,w.stateFresh,mounted.value,visible.value,camera.value?.state,camera.value?.session,cameraInput.value],check,{flush:'sync',deep:true})
  timer=setTimeout(()=>finish(false),timeout);check()
 })
}
function toggleStart(){
  // 仿真里"停止结果未知"不该吃掉第一次点击：后端令牌早已换新，直接开始即可。
  if(preparingCamera.value||local.value.active||local.value.busy||(isReal.value&&local.value.stopState==='unknown'))
    stop(preparingCamera.value?'已取消准备':'已停止')
  else void start()
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function start() {
  validate()
  if (startActionBlocked.value || preparingCamera.value || local.value.active || local.value.busy || rangeError.value) return
  const owner = client, real = isReal.value,mode=w.mode,session=w.snap?.session_id,epoch=++prepareEpoch.value
  preparationError.value=''
  if (real && !window.confirm('开始真机手掌跟随？\n机器人将先受控前往工作区中心，J6 移动到 0°；实际到位后才开始手掌跟随，J6 保持 0°。\n丢掌或异常时停止，不自动恢复。请确认运动范围内无人、急停可用。\n本操作不会自动连接、使能或标定；控制器、工具参数、路径和现场验证仍须通过后台检查。')) return
  let startupId=null
  if(!syntheticMode.value){
   // 从现在起才需要手掌推理；页面挂载和切页都不再自动开启。
   setHandFollowDetectionDemand(true)
   requestHandFollowVision()
   preparingCamera.value=true
   let centerFailed=false
   try{
    // Center motion owns its own startup ID, never a fabricated camera frame.
    // It is issued first and keeps running whatever the camera does.
    const center=real?Promise.resolve(null):moveToCenter(epoch,owner,mode,session)
    // 相机准备排在回中心之后：开设备会改变 cameras 状态，回中心期间一旦这些
    // 状态变化，准备流程就判定"已过期"并主动发出 center-cancel，用户看到的
    // 就是"摄像头开了，跟随却被取消"。检测更要在回中心之后 —— 开检测会让
    // 后端撤销正在进行的启动动作。
    const deadline=performance.now()+90000
    let prepared=false,prepareFailure=''
    const tryPrepare=async(startDetection=true)=>{
      if(!await waitCameraIdle(epoch,owner,mode,session))return false
      if(!preparationCurrent(epoch,owner,mode,session))return false
      try{
        // 相机来源只在真的失败时改写原因栏；"相机正忙"时原因栏不变，
        // 那种情况只是"还没准备好"，继续等就行。
        const before=String(cameraSource.message?.value||'')
        const ok=await cameraSource.prepareHandFollow({startDetection,cancelled:()=>centerFailed||!preparationCurrent(epoch,owner,mode,session)})
        if(ok)return true
        // 相机来源给出了失败原因（被占用、检测器起不来、找不到设备等）：
        // 那是终局结论，原样上报，不能被"还在等待"盖掉。
        const reason=String(cameraSource.message?.value||'').trim()
        if(reason&&reason!==before)prepareFailure=reason
        return false
      }
      catch(error){prepareFailure=error.message||'手掌检测准备失败';return false}
    }
    // 相机已经就绪时，只差把推理拉起来这一步，不必再走一遍准备相机的流程。
    const startDetection=async()=>{
      const before=String(cameraSource.message?.value||'')
      try{
        if(await cameraSource.setHandDetection(true))return true
        const reason=String(cameraSource.message?.value||'').trim()
        if(reason&&reason!==before)prepareFailure=reason
        return false
      }catch(error){prepareFailure=error.message||'手掌检测启动失败';return false}
    }
    // 回中心先走完，再准备相机：开设备会改变 cameras 状态，回中心期间一旦这些
    // 状态变化，准备流程就判定"已过期"并主动发出 center-cancel —— 用户看到的
    // 就是"摄像头开了，跟随却被取消"。两者必须串行。
    startupId=await center
    if(!real&&!startupId)return
    prepared=await tryPrepare(false)
    // 相机已经在跑时只差把推理拉起来这一步，不必再走一遍准备相机的流程。
    if(prepared&&!cameraSource.handDetection.value)prepared=await startDetection()
    while(!prepared && !prepareFailure && preparationCurrent(epoch,owner,mode,session) && performance.now()<deadline){
      if(!preparingCamera.value)return
      if(!centerPhase.value)centerPhase.value='waiting'
      preparationError.value='已到中心，等待摄像头就绪后自动开始…'
      await sleep(1200)
      if(epoch!==prepareEpoch.value)return
      prepared=await tryPrepare(true)
    }
    if(!prepared){preparationError.value=prepareFailure||'已到中心，但摄像头仍未就绪；开启画面后会继续，也可再次点击';return}
    if(!preparationCurrent(epoch,owner,mode,session))return
    preparationError.value=''
    // 超时保持 10 秒上限（既有契约：等一份"检测到手"的画面，最长十秒）。
    // 真正缩短等待靠启动预热 —— 模型在开程序时就加载好，举手后一两帧就过。
    if(!await waitCameraFrame(epoch,owner,mode,session,10000)){
      // 真机检测器的报错是终局结论；其它情况继续等画面晚到，不再要求再点一次。
      if(preparationError.value)return
      if(!await waitCameraFrame(epoch,owner,mode,session,30000)){
        preparationError.value='已到中心，但未收到有效彩色画面；请开启右上角彩色画面后重试'
        return
      }
    }
   }catch(error){centerFailed=true;if(epoch===prepareEpoch.value)preparationError.value=error.message||'手掌检测准备失败';return}
   finally{if(epoch===prepareEpoch.value)preparingCamera.value=false}
  }
  if (owner !== client || epoch!==prepareEpoch.value || startActionBlocked.value) return
  if ((startupId || real || !local.value.configured || (!syntheticMode.value&&local.value.server?.session!==cameraInput.value?.session)) && !await configure(startupId)) return
  if(startupId && centerStartup?.id===startupId){centerStartup=null;centerPhase.value=''}
  validate()
  const source=currentInput(true)
  if (owner === client && epoch===prepareEpoch.value && !startActionBlocked.value && local.value.configured && source?.session)
    await owner.start(source.session, real ? {confirm_real:true} : undefined)
}function syncRange(box) {
  if (!box?.min || !box?.max || [...box.min,...box.max].some(v=>!Number.isFinite(v)) || box.min.some((v,i)=>v>=box.max[i])) return
  rangeCenter.value=box.min.map((v,i)=>+( (v+box.max[i])/2 ).toFixed(1))
  rangeSize.value=box.min.map((v,i)=>+(box.max[i]-v).toFixed(1))
}
function clampRangeToFence() {
  const f=props.fence
  if (!f || !['x','y','z'].every(k=>Array.isArray(f[k]) && f[k].length===2 && f[k].every(Number.isFinite) && f[k][0]<f[k][1])) return
  const center=rangeCenter.value.slice(), size=rangeSize.value.slice()
  const low=[f.x[0],f.y[0],Math.max(0,f.z[0])], high=[f.x[1],f.y[1],f.z[1]]
  if (low.some((value,i)=>value>=high[i])) return
  const side=[90,270].includes(rangeDirection.value)
  size[0]=Math.max(1,Math.min(size[0],high[side?1:0]-low[side?1:0]))
  size[1]=Math.max(1,Math.min(size[1],high[side?0:1]-low[side?0:1]))
  size[2]=Math.max(1,Math.min(size[2],high[2]-low[2]))
  const half=(side?[size[1],size[0],size[2]]:size).map(v=>v/2)
  for (let i=0;i<3;i++) center[i]=Math.max(low[i]+half[i],Math.min(high[i]-half[i],center[i]))
  rangeCenter.value=center; rangeSize.value=size
}
function changeRange() {
  rangeEdited.value=true
  rangeNotice.value = ''
  clampRangeToFence()
  rangeCustom.value=true;stop('框预览，尚未应用')
  if (!rangeError.value) emit('box-preview',{
    min:rangeCenter.value.map((v,i)=>v-rangeSize.value[i]/2),
    max:rangeCenter.value.map((v,i)=>v+rangeSize.value[i]/2),
  })
}
function recordDepth(index) {
  const value=cameraInput.value?.point?.palm_scale
  if (local.value.active || local.value.busy || inputIssue.value || cameraInput.value?.trackingState==='holding' || !Number.isFinite(value)) return
  depthRange.value[index]=+value.toFixed(4)
  changeRange()
  rangeNotice.value=index===0 ? '已记录最远掌圈（最小）；再记录最近掌圈' : '已记录最近掌圈（最大），应用后生效'
}
function previewCurrentRange() {
  clampRangeToFence()
  if (rangeError.value) return
  emit('box-preview',{
    min:rangeCenter.value.map((v,i)=>v-rangeSize.value[i]/2),
    max:rangeCenter.value.map((v,i)=>v+rangeSize.value[i]/2),
  })
}
function changeDirection(direction, notifyParent = true) {
  const next = Number(direction)
  if (!DIRECTIONS.includes(next) || next === rangeDirection.value) return
  // Move the whole range around the robot base to the selected side. Keep
  // its height, local dimensions and user-adjusted radial/lateral offset.
  rangeCenter.value=rotateInteractionCenter(rangeCenter.value,rangeDirection.value,next)
  rangeDirection.value=next
  if (notifyParent) emit('direction',next)
  changeRange()
}
async function resetRange() {
  if (local.value.active || local.value.busy) return
  rangeCenter.value=[...DEFAULT_RANGE_CENTER]
  rangeSize.value=[...DEFAULT_RANGE_SIZE]
  rangeDirection.value=270
  depthRange.value=[.06,.30]
  emit('direction',rangeDirection.value)
  changeRange()
}
async function saveRangeDefault() {
  if (local.value.active || local.value.busy || rangeError.value || savingRangeDefault.value) return
  const value={center:rangeCenter.value.slice(),size:rangeSize.value.slice(),direction:rangeDirection.value,depthRange:depthRange.value.slice()}
  savingRangeDefault.value=true
  try {
    const saved=await saveInteractionRangeDefault(w,value)
    Object.assign(rangeDefault,saved);rangeEdited.value=false
    rangeNotice.value='已设为默认'
  } catch(error) { rangeNotice.value=error.message || '默认范围保存失败' }
  finally {savingRangeDefault.value=false}
}
watch(()=>w.snap?.settings?.interaction_range_default,value=>{
  const range=interactionRange(value)
  if(!range||rangeEdited.value||local.value.active||local.value.busy)return
  rangeCenter.value=[...range.center];rangeSize.value=[...range.size];rangeDirection.value=range.direction;depthRange.value=[...range.depthRange]
  emit('direction',range.direction);previewCurrentRange()
},{deep:true})
watch(() => telemetry.value?.outer_bounds, box=>{ if (!rangeCustom.value) { syncRange(box); clampRangeToFence() } })
watch(() => [props.fence?.x?.[0],props.fence?.x?.[1],props.fence?.y?.[0],props.fence?.y?.[1],props.fence?.z?.[0],props.fence?.z?.[1]], () => {
  clampRangeToFence()
  previewCurrentRange()
})
watch(() => props.direction, direction => changeDirection(direction,false))
function visibilityChanged() { visible.value = !document.hidden; if(!visible.value){if(preparingCamera.value){prepareEpoch.value++;preparingCamera.value=false}cancelCenterStartup();leaveMouse()}validate() }
function pageLeaving() {
  cancelCenterStartup()
  if (isReal.value) {stop('页面已离开，已请求停止');return}
  // A refresh transfers only the credential already acquired by this tab.
  // No configure/start request, foreign WS adoption or stale palm replay.
  if(local.value.active) {rememberOwner(client.getState());handingOff=true;client.detach()}
  else stop('页面已离开')
}
function connectionLost() {
  if(preparingCamera.value){prepareEpoch.value++;preparingCamera.value=false}
  cancelCenterStartup()
  leaveMouse()
  if (isReal.value) stop('网络离线，已请求停止')
  else if (local.value.active) void client.sample(null)
}
function pageShown(event) { if(event.persisted && handingOff)window.location.reload() }
function beginWatching() {
  if (timer) return
  // 不再在挂载/切页时拉起手掌检测：检测只在点击"开始跟随"后按需启动，
  // 否则 30Hz 推理会一直占着 CPU，把三维和预检一起拖慢。
  unsubscribe = subscribeHandFollowInput(value => {
    cameraInput.value = value; validateSoon()
    // Deliver a newly measured palm immediately; the 30 Hz timer remains a
    // freshness/loss heartbeat, while the client keeps only one latest sample.
    if (!syntheticMode.value && local.value.active)
      void client.sample(mounted.value && visible.value && w.connected ? currentInput() : null)
  })
  // 20Hz 只做采样心跳，不再顺带跑完整状态机（那是每秒几十次无谓重算）。
  timer = setInterval(() => {
    if (local.value.active) void client.sample(mounted.value && visible.value && w.connected ? currentInput() : null)
  }, 1000 / 20)
}
function endWatching() {
  clearInterval(timer); timer = null; unsubscribe?.(); unsubscribe = null
  if(!handingOff)stop('已离开跟随页面')
}
watch(() => [w.mode,inputMode.value], ([mode]) => {
  // Real mode accepts palm input only; a saved/simulated mouse owner retires here.
  if (mode === 'real' && inputMode.value !== 'camera') { inputMode.value = 'camera'; return }
  // Dispose before construction: old and late tokens always retire at the old endpoint.
  cancelCenterStartup();prepareEpoch.value++;preparingCamera.value=false;preparationError.value=''
  client.dispose()
  mousePresent.value=false
  refreshOwner=null;saveTabState(OWNER_KEY,null);handingOff=false
  local.value={active:false,configured:false,busy:false,why:'模式已切换，请重新建立工作区',server:null}
  if (mode === 'real') rotation.value=false
  saveTabState('huayan.follow.input',inputMode.value)
  client=makeClient(mode)
  // 切页/切模式不再自动拉起手掌检测，等用户点"开始跟随"。
}, {flush:'sync'})
// 快照与守卫的变化必须按到达顺序同步判定：合并会把"先看到自有令牌、
// 再看到令牌被换掉"这一对快照压成一次，丢失撤销。
watch(environmentIssue, validate, {flush:'sync'})
watch(activeGuard, validate, {flush:'sync'})
watch(() => [liveSnapshot.value, liveSnapshot.value?.token, liveSnapshot.value?.active], validate, {flush:'sync'})
watch(() => [props.visualModel, depth.value, rotation.value, props.direction], (next, previous) => {
  if (previous && next.every((v,i) => v === previous[i])) return
  if(previous && initialDirectionSync!==null && next[3]===initialDirectionSync && next.slice(0,3).every((v,i)=>v===previous[i])) {
    initialDirectionSync=null;return
  }
  if (local.value.active || local.value.configured || local.value.busy) stop('设置已变化，请重新建立工作区')
}, {flush:'sync'})
let frontFramed = false
function frameFrontOnce() {
  const scene = window.__elfinScene
  if (frontFramed || !scene?.getView || !scene?.setView) return
  frontFramed = scene.setView(frontTaskView(scene.getView())) !== false
}
onMounted(() => {
  frameFrontOnce()
  emit('direction',rangeDirection.value)
  previewCurrentRange()
  document.addEventListener('visibilitychange', visibilityChanged)
  window.addEventListener('pagehide', pageLeaving)
  window.addEventListener('pageshow', pageShown)
  window.addEventListener('offline', connectionLost)
  window.addEventListener('blur', leaveMouse)
  beginWatching()
})
onActivated(() => { frameFrontOnce(); mounted.value = true; visible.value = !document.hidden; previewCurrentRange(); beginWatching() })
// Simulation retains its off-page owner; real navigation retires it.
// 离开跟随页必须真正停止：否则 hand_follow.active 会一直挂着，
// 写字/井字棋/绘制的前置条件（要静止、要没有跟随）就永远是 false，
// 表现就是"开了摄像头以后什么都跑不了"。
onDeactivated(() => { cancelCenterStartup();prepareEpoch.value++;preparingCamera.value=false;mounted.value = false; leaveMouse();setHandFollowDetectionDemand(false);if (local.value.active || local.value.busy || isReal.value) stop('已离开跟随页面，已请求停止') })
onBeforeUnmount(() => {
  emit('box-preview',null)
  cancelCenterStartup();prepareEpoch.value++;preparingCamera.value=false;mounted.value = false; endWatching(); if(handingOff)client.detach();else client.dispose()
  document.removeEventListener('visibilitychange', visibilityChanged)
  window.removeEventListener('pagehide', pageLeaving)
  window.removeEventListener('pageshow', pageShown)
  window.removeEventListener('offline', connectionLost)
  window.removeEventListener('blur', leaveMouse)
})
</script>

<template>
  <div class="col hand-follow-panel">
    <section class="panel corner">
      <header class="panel-hd"><span class="panel-title">交互</span><span class="panel-sub">{{isReal ? '真机 · 手掌跟随' : '仅仿真'}}</span></header>
      <div class="panel-bd">
        <div class="input-tabs" role="tablist" aria-label="交互输入方式">
          <button v-for="tab in inputTabs" :id="`input-tab-${tab.id}`" :key="tab.id" type="button" class="btn" role="tab" :aria-selected="inputMode===tab.id" :tabindex="inputMode===tab.id?0:-1" aria-controls="interaction-input-panel" :disabled="isReal && tab.id!=='camera'" @click="inputMode=tab.id">{{tab.label}}</button>
        </div>
        <div id="interaction-input-panel" role="tabpanel" :aria-labelledby="`input-tab-${inputMode}`" class="input-content">
        <div v-if="mouseMode" class="mouse-controls" @pointerenter="mousePresent=true" @pointerleave="leaveMouse" @pointercancel="leaveMouse" @focusout="mouseFocusOut">
          <div class="mouse-pad" tabindex="0" role="group" aria-label="鼠标控制板：移动鼠标控制左右上下" @pointermove="moveMouse" @pointerdown="moveMouse">
            <span class="mouse-pad-label">移动鼠标 · 左右 / 上下</span>
            <span class="mouse-dot" :style="{left:`${mousePosition.x*100}%`,top:`${mousePosition.y*100}%`}"></span>
          </div>
          <label class="mouse-depth">后退<input type="range" min="0" max="1" step="0.01" :value="mousePosition.depth" :disabled="!depth" aria-label="鼠标前后位置" @input="mouseDepth" />前进</label>
          <button class="btn btn--xs" @click="centerMouse">目标回中心</button>
          <p class="hint">移入后跟随，移出控制区平滑回中心等待。仅仿真。</p>
        </div>
        </div>
        <div class="actions"><button class="btn" :class="preparingCamera || local.active || local.busy || local.stopState === 'unknown' ? 'btn--danger' : 'btn--primary'" :disabled="local.stopState === 'pending' || (!local.active && !local.busy && !(isReal && local.stopState === 'unknown') && (!!startActionBlocked || !!rangeError))" @click="toggleStart">{{local.stopState === 'pending' ? '停止待确认' : (isReal && local.stopState === 'unknown') ? '重试停止' : preparingCamera ? '取消准备' : local.active ? '停止跟随' : local.busy ? '正在准备…' : '开始跟随'}}</button></div>
        <p class="status" :class="{live:local.active, limited:solverLimited}" role="status" aria-live="polite">{{displayStatus}}</p>
        <div v-if="isReal" class="real-follow-state">
          <p class="hint" role="status" aria-live="polite">阶段：{{realPhase}}</p>
          <p v-if="realFlowSupported" class="hint">先到中心 → J6＝0° → 到位后跟随</p>
          <p v-else class="hint" role="note">{{realFlowIssue}}</p>
          <div v-if="realBlockers.length" class="real-blockers" role="note" aria-label="真机跟随启动条件">
            <p class="hint">尚未满足：</p>
            <ul><li v-for="(item,index) in realBlockers" :key="index">{{item.layer}}：{{item.detail}}</li></ul>
          </div>
        </div>
        <details v-if="solverLimited && telemetry?.why" class="hint"><summary>求解详情</summary>{{telemetry.why}}</details>
        <p v-if="isReal && realFlowSupported" class="hint">J1～J5 协调跟随位置，J6 保持 0°。丢掌或异常停止后，需手动重新开始。</p>
        <details class="range-settings" :open="settingsOpen" @toggle.self="settingsOpen=$event.target.open">
          <summary>范围设置</summary>
          <fieldset :disabled="preparingCamera || local.active || local.busy">
            <label class="option">朝向<select aria-label="跟随朝向" :value="rangeDirection" @change="changeDirection($event.target.value)"><option :value="270">正面</option><option :value="0">右侧</option><option :value="90">背面</option><option :value="180">左侧</option></select></label>
            <label class="option"><input v-model="depth" type="checkbox" />{{syntheticMode ? '启用前后控制' : '手势前后跟随'}}</label>
            <p v-if="isReal" class="hint">目标与反馈速度均须 ≤5%。实际工具、工具中心点、负载、路径与运动许可由后台检查，未通过不能启动。</p>
            <label v-if="!syntheticMode" class="option"><input v-model="rotation" type="checkbox" :disabled="isReal" />工具旋转（Rz）<span v-if="isReal">真机固定关闭</span></label>
            <div class="range-grid"><span>轴 / mm</span><span>中心坐标</span><span>范围尺寸</span>
              <template v-for="(axis,i) in ['X · 宽','Y · 深','Z · 高']" :key="axis"><span>{{axis}}</span>
                <div class="range-field"><input v-model.number="rangeCenter[i]" type="range" :min="i===2?0:-2000" max="2000" step="1" :aria-label="axis+'中心滑块'" @input="changeRange" /><input v-model.number="rangeCenter[i]" type="number" step="1" :aria-label="axis+'中心坐标'" @input="changeRange" /></div>
                <div class="range-field"><input v-model.number="rangeSize[i]" type="range" :min="i===1?20:100" :max="i===1?1000:3000" step="1" :aria-label="axis+'尺寸滑块'" @input="changeRange" /><input v-model.number="rangeSize[i]" type="number" min="1" max="5000" step="1" :aria-label="axis+'范围尺寸'" @input="changeRange" /></div>
              </template>
            </div>
            <div class="actions"><button class="btn btn--xs" :disabled="!!configureBlocked || !!rangeError" @click="configure">应用范围</button><button class="btn btn--xs" :disabled="!!rangeError||savingRangeDefault" @click="saveRangeDefault">设为默认</button><button class="btn btn--xs" @click="resetRange">恢复初始范围</button></div>
            <p v-if="rangeNotice" class="hint" role="status">{{rangeNotice}}</p>
          </fieldset>
        </details>
      </div>
    </section>
  </div>
</template>

<style scoped>
.hand-follow-panel { min-width:0; }
.input-content,.input-help { display:grid; gap:var(--s3); min-width:0; font-size:12px; line-height:1.7; overflow-wrap:anywhere; }
.mouse-controls { display:grid; gap:var(--s3); }
.mouse-pad { position:relative; aspect-ratio:4/3; border:1px solid var(--line); background:var(--bg-inset); cursor:crosshair; touch-action:none; }
.mouse-pad:focus-visible { outline:2px solid var(--acc); outline-offset:2px; }
.mouse-pad-label { position:absolute; inset:8px 0 auto; text-align:center; color:var(--tx-3); font-size:11px; pointer-events:none; }
.mouse-dot { position:absolute; width:12px; height:12px; transform:translate(-50%,-50%); border:2px solid var(--acc); border-radius:50%; background:var(--bg-inset); pointer-events:none; }
.mouse-depth { display:flex; align-items:center; gap:var(--s3); font-size:11px; }
.mouse-depth input { flex:1; min-width:0; accent-color:var(--acc); }
.panel-bd { display:flex; flex-direction:column; gap:var(--s4); }
p { margin:0; }
.hint { color:var(--tx-3); font-size:11px; line-height:1.6; overflow-wrap:anywhere; }
.option { display:flex; align-items:center; flex-wrap:wrap; gap:var(--s2); font-size:12px; }
.option span { font-size:10px; color:var(--tx-3); }
.actions { display:flex; flex-wrap:wrap; gap:var(--s3); }
.actions .btn { flex:1 1 auto; }
.status { color:var(--tx-2); font-size:12px; overflow-wrap:anywhere; }
.status.live { color:var(--ok); }
.status.limited { color:var(--state-warn); }
.real-follow-state { display:grid; gap:var(--s2); }
.real-blockers { color:var(--state-warn); font-size:11px; line-height:1.6; overflow-wrap:anywhere; }
.real-blockers ul { margin:var(--s2) 0 0; padding-left:18px; }
.vision-status { display:grid; gap:var(--s2); margin:0; font-size:11px; }
.vision-status div { display:grid; grid-template-columns:58px minmax(0,1fr); gap:var(--s3); }
.vision-status dt { color:var(--tx-3); }
.vision-status dd { margin:0; color:var(--tx-2); overflow-wrap:anywhere; }
.range-settings { border-top:1px solid var(--line); padding-top:var(--s3); }
.range-settings summary { font-size:11px; color:var(--tx-2); cursor:pointer; }
fieldset { display:flex; flex-direction:column; gap:var(--s4); border:0; margin:0; padding:var(--s4) 0 0; min-width:0; }
.range-grid { display:grid; grid-template-columns:62px minmax(0,1fr) minmax(0,1fr); gap:var(--s3); align-items:center; font-size:11px; color:var(--tx-2); }
.range-grid input { min-width:0; width:100%; box-sizing:border-box; }
.range-field { display:flex; flex-direction:column; gap:3px; min-width:0; }
.range-field input[type=range] { padding:0; height:18px; accent-color:var(--acc); cursor:ew-resize; }
</style>
