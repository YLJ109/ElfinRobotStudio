<script setup>
import { computed, ref, onMounted, onBeforeUnmount, onActivated, onDeactivated, watch } from 'vue'
import { useWorld } from '../store'
import { readTabState, saveTabState } from '../workspaceState.js'
import { createMouseFollowInput } from '../mouseFollowInput.js'
import { createInteractionInput, interactionModeIssue } from '../interactionInput.js'
import { createHandFollowClient, getHandFollowInput, subscribeHandFollowInput,
  handFollowInputIssue, handFollowFrameIssue, requestHandFollowVision, setHandFollowDepthRange,
  selectHandFollowSnapshot } from '../handFollowInput.js'

const props = defineProps({ visualModel: { type:String, default:'' }, direction:{type:Number,default:270}, fence:{type:Object,default:null} })
const emit = defineEmits(['direction','box-preview'])
const w = useWorld()
const isReal = computed(() => w.mode === 'real')
const savedInputMode = readTabState('huayan.follow.input')
const inputMode = ref(!isReal.value && ['mouse','keyboard','gamepad'].includes(savedInputMode) ? savedInputMode : 'camera')
const mouseMode = computed(() => !isReal.value && inputMode.value === 'mouse')
const syntheticMode = computed(() => inputMode.value !== 'camera')
const inputTabs = [{id:'camera',label:'手掌'},{id:'mouse',label:'鼠标'},{id:'keyboard',label:'键盘'},{id:'gamepad',label:'手柄'}]
const interactionState = ref({x:.5,y:.5,depth:.5,present:false,gamepad:null})
const savedInteractionSession = readTabState('huayan.follow.interaction-session')
const interactionSession = typeof savedInteractionSession === 'string' && /^mouse-[a-zA-Z0-9-]{6,120}$/.test(savedInteractionSession)
  ? savedInteractionSession : `mouse-interaction-${globalThis.crypto?.randomUUID?.() || Date.now()}`
saveTabState('huayan.follow.interaction-session',interactionSession)
const interaction = createInteractionInput({ session:interactionSession,
  enabled:() => mounted.value && visible.value && w.connected && !isReal.value && local.value.active,
  onChange:state => { interactionState.value=state } })
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
  return mouseMode.value ? (forConfiguration || mousePresent.value ? mouse.frame(performance.now()) : null) :
    syntheticMode.value ? interaction.frame(performance.now(),forConfiguration) : cameraInput.value
}
const liveSnapshot = computed(() => selectHandFollowSnapshot(w.snap, w.mode))
const realFlowSupported = computed(() => isReal.value &&
  liveSnapshot.value?.control_policy === 'real-palm-position-j6-zero-v1')
const realFlowIssue = computed(() => isReal.value && !realFlowSupported.value
  ? '真机新流程尚未接入，当前后端不支持先到中心、J6 归零；禁止新启动，仍可停止已有跟随。' : '')
const depth = ref(true), rotation = ref(false), visible = ref(!document.hidden)
const DEFAULT_RANGE_CENTER = [0,-400,700], DEFAULT_RANGE_SIZE = [800,300,500]
const RANGE_DEFAULT_KEY = 'huayan.hand-follow.range-default.v1'
const DRAFT_KEY = 'huayan.hand-follow.draft.v1'
const DIRECTIONS = [0,90,180,270]
function readRangeDefault() {
  try {
    const value=JSON.parse(localStorage.getItem(RANGE_DEFAULT_KEY) || 'null')
    if (Array.isArray(value?.center) && Array.isArray(value?.size) &&
        value.center.length===3 && value.size.length===3 &&
        [...value.center,...value.size].every(Number.isFinite) &&
        value.size.every(v=>v>0 && v<=5000) && value.center.every(v=>Math.abs(v)<=10000) &&
        (value.direction == null || DIRECTIONS.includes(value.direction)))
      return {center:value.center.slice(),size:value.size.slice(),direction:value.direction ?? 270,
        depthRange:Array.isArray(value.depthRange) && value.depthRange.length===2 &&
          value.depthRange.every(v=>Number.isFinite(v) && v>=.005 && v<=2) && value.depthRange[1]-value.depthRange[0]>=.01
          ? value.depthRange.slice() : [.06,.30]}
  } catch { /* ignore invalid or unavailable browser storage */ }
  return {center:DEFAULT_RANGE_CENTER,size:DEFAULT_RANGE_SIZE,direction:270,depthRange:[.06,.30]}
}
const rangeDefault = readRangeDefault()
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
const rangeCenter = ref([...(draft?.center || rangeDefault.center)]), rangeSize = ref([...(draft?.size || rangeDefault.size)]), rangeCustom = ref(true)
// Keep the requested direction locally until the parent prop update arrives.
// The center is always world XYZ; size remains local width/depth/height.
function rememberedDirection() {
  if (draft) return draft.direction
  try {
    const saved=localStorage.getItem('huayan.hand.direction')
    if (saved !== null && DIRECTIONS.includes(Number(saved))) return Number(saved)
  } catch { /* retain the saved range default */ }
  return rangeDefault.direction
}
const rangeDirection = ref(rememberedDirection())
let initialDirectionSync=props.direction!==rangeDirection.value?rangeDirection.value:null
const depthRange = ref((draft?.depthRange || rangeDefault.depthRange).slice())
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
  if (interactionModeIssue(w.mode,inputMode.value)) return interactionModeIssue(w.mode,inputMode.value)
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
  if (!w.connected || !w.snap) return '后端未连接'
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
  if (!w.connected || !w.snap) return '后端未连接'
  if (syntheticMode.value) return ''
  if (camera.value?.state !== 'streaming') return '请在右上角启动摄像头'
  if (!cameraInput.value || cameraInput.value.session !== camera.value.session) return '等待右上角彩色画面'
  return ''
})
const inputIssue = computed(() => handFollowInputIssue(cameraInput.value, clock.value))
const configureBlocked = computed(() => realFlowIssue.value || environmentIssue.value || (syntheticMode.value ? '' : handFollowFrameIssue(cameraInput.value, clock.value)))
const startBlocked = computed(() => configureBlocked.value || (isReal.value ? activeGuard.value : ''))
const cameraStatus = computed(() => camera.value?.state === 'streaming'
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
const statusText = computed(() => (local.value.busy && local.value.why) ||
  (local.value.rejected && local.value.why) ||
  (local.value.active && telemetry.value?.blocked_reason) ||
  (local.value.active && local.value.waiting && (isReal.value ? realPhase.value : local.value.why)) || rangeError.value || (!local.value.active && startBlocked.value) ||
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

function stop(why = '已停止') { interaction.reset(); client.stop(why) }
function validate() {
  if(handingOff)return
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
  const reason = local.value.active ? activeGuard.value : local.value.operation === 'start' ?
    startBlocked.value : configureBlocked.value
  if ((local.value.active || local.value.busy || local.value.configured) && reason) stop(reason)
}
async function configure() {
  validate()
  if (configureBlocked.value || rangeError.value || local.value.active || local.value.busy) return false
  const source = currentInput(true)
  const owner = client
  const enclosure = rangeCustom.value ? {
    min:rangeCenter.value.map((v,i)=>v-rangeSize.value[i]/2),
    max:rangeCenter.value.map((v,i)=>v+rangeSize.value[i]/2),
  } : null
  const ok = await owner.configure({ session:source.session, aspect:source.width / source.height,
    direction:rangeDirection.value, depth:depth.value, rotation:isReal.value || syntheticMode.value ? false : rotation.value, depth_mode:'gesture',
    ...(enclosure ? {enclosure} : {}) })
  if (owner !== client) return false
  if (ok && !rangeCustom.value) syncRange(owner.getState().server?.outer_bounds)
  if (ok) emit('box-preview',null)
  return ok
}
async function start() {
  validate()
  if (startBlocked.value || local.value.active || local.value.busy || rangeError.value) return
  const owner = client, real = isReal.value
  if (real && !window.confirm('开始真机手掌跟随？\n机器人将先受控前往工作区中心，J6 移动到 0°；实际到位后才开始手掌跟随，J6 保持 0°。\n丢掌或异常时停止，不自动恢复。请确认运动范围内无人、急停可用。\n本操作不会自动连接、使能或标定；控制器、工具参数、路径和现场验证仍须通过后台检查。')) return
  if (owner !== client || startBlocked.value) return
  // The backend owns real center planning, arrival confirmation and Servo entry.
  if ((real || !local.value.configured) && !await configure()) return
  validate()
  const source=currentInput(true)
  if (owner === client && !startBlocked.value && local.value.configured && source?.session)
    await owner.start(source.session, real ? {confirm_real:true} : undefined)
}
function syncRange(box) {
  if (!box?.min || !box?.max || [...box.min,...box.max].some(v=>!Number.isFinite(v)) || box.min.some((v,i)=>v>=box.max[i])) return
  rangeCenter.value=box.min.map((v,i)=>+( (v+box.max[i])/2 ).toFixed(1))
  rangeSize.value=box.min.map((v,i)=>+(box.max[i]-v).toFixed(1))
}
function clampRangeToFence() {
  const f=props.fence
  if (!f || !['x','y','z'].every(k=>Array.isArray(f[k]) && f[k].length===2 && f[k].every(Number.isFinite) && f[k][0]<f[k][1])) return
  const center=rangeCenter.value.slice(), size=rangeSize.value.slice()
  const side=rangeDirection.value===90 || rangeDirection.value===270
  const low=[f.x[0],f.y[0],Math.max(0,f.z[0])], high=[f.x[1],f.y[1],f.z[1]]
  if (low.some((value,i)=>value>=high[i])) return
  size[0]=Math.max(1,Math.min(size[0],side?high[1]-low[1]:high[0]-low[0]))
  size[1]=Math.max(1,Math.min(size[1],side?high[0]-low[0]:high[1]-low[1]))
  size[2]=Math.max(1,Math.min(size[2],high[2]-low[2]))
  const half=[(side?size[1]:size[0])/2,(side?size[0]:size[1])/2,size[2]/2]
  for (let i=0;i<3;i++) center[i]=Math.max(low[i]+half[i],Math.min(high[i]-half[i],center[i]))
  rangeCenter.value=center; rangeSize.value=size
}
function changeRange() {
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
  let [x,y,z] = rangeCenter.value
  const turns = (next-rangeDirection.value+360)%360/90
  // Exact quarter turns avoid accumulating floating-point drift when cycling.
  for (let i=0;i<turns;i++) [x,y]=[-y,x]
  rangeCenter.value=[x || 0,y || 0,z]
  rangeDirection.value=next
  if (notifyParent) emit('direction',next)
  changeRange()
}
async function resetRange() {
  if (local.value.active || local.value.busy) return
  rangeCenter.value=[...rangeDefault.center]
  rangeSize.value=[...rangeDefault.size]
  rangeDirection.value=rangeDefault.direction
  depthRange.value=rangeDefault.depthRange.slice()
  emit('direction',rangeDirection.value)
  changeRange()
}
function saveRangeDefault() {
  if (local.value.active || local.value.busy || rangeError.value) return
  const value={center:rangeCenter.value.slice(),size:rangeSize.value.slice(),direction:rangeDirection.value,depthRange:depthRange.value.slice()}
  try {
    localStorage.setItem(RANGE_DEFAULT_KEY,JSON.stringify(value))
    rangeDefault.center=value.center
    rangeDefault.size=value.size
    rangeDefault.direction=value.direction
    rangeDefault.depthRange=value.depthRange
    rangeNotice.value='已设为默认'
  } catch { rangeNotice.value='默认值保存失败，浏览器存储不可用' }
}
watch(() => telemetry.value?.outer_bounds, box=>{ if (!rangeCustom.value) { syncRange(box); clampRangeToFence() } })
watch(() => [props.fence?.x?.[0],props.fence?.x?.[1],props.fence?.y?.[0],props.fence?.y?.[1],props.fence?.z?.[0],props.fence?.z?.[1]], () => {
  clampRangeToFence()
  previewCurrentRange()
})
watch(() => props.direction, direction => changeDirection(direction,false))
function visibilityChanged() { visible.value = !document.hidden; if(!visible.value)leaveMouse();validate() }
function pageLeaving() {
  interaction.dispose()
  if (isReal.value) {stop('页面已离开，已请求停止');return}
  // A refresh transfers only the credential already acquired by this tab.
  // No configure/start request, foreign WS adoption or stale palm replay.
  if(local.value.active) {rememberOwner(client.getState());handingOff=true;client.detach()}
  else stop('页面已离开')
}
function connectionLost() {
  interaction.reset()
  leaveMouse()
  if (isReal.value) stop('网络离线，已请求停止')
  else if (local.value.active) void client.sample(null)
}
function pageShown(event) { if(event.persisted && handingOff)window.location.reload() }
function beginWatching() {
  if (timer) return
  // Enables the existing color preview/detector, never opens camera hardware.
  if (!syntheticMode.value) requestHandFollowVision()
  unsubscribe = subscribeHandFollowInput(value => { cameraInput.value = value; validate() })
  timer = setInterval(() => {
    validate()
    if (local.value.active) void client.sample(mounted.value && visible.value && w.connected ? currentInput() : null)
  }, 1000 / 30)
}
function endWatching() {
  clearInterval(timer); timer = null; unsubscribe?.(); unsubscribe = null
  if(!handingOff)stop('已离开跟随页面')
}
watch(() => [w.mode,inputMode.value], ([mode]) => {
  // Dispose before construction: old and late tokens always retire at the old endpoint.
  client.dispose()
  interaction.dispose()
  mousePresent.value=false
  refreshOwner=null;saveTabState(OWNER_KEY,null);handingOff=false
  local.value={active:false,configured:false,busy:false,why:'模式已切换，请重新建立工作区',server:null}
  if (mode === 'real') rotation.value=false
  saveTabState('huayan.follow.input',inputMode.value)
  client=makeClient(mode)
  if (!syntheticMode.value) requestHandFollowVision()
  if (mounted.value) interaction.select(inputMode.value,mode)
}, {flush:'sync'})
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
onMounted(() => {
  emit('direction',rangeDirection.value)
  previewCurrentRange()
  document.addEventListener('visibilitychange', visibilityChanged)
  window.addEventListener('pagehide', pageLeaving)
  window.addEventListener('pageshow', pageShown)
  window.addEventListener('offline', connectionLost)
  window.addEventListener('blur', leaveMouse)
  beginWatching()
  interaction.select(inputMode.value,w.mode)
})
onActivated(() => { mounted.value = true; visible.value = !document.hidden; previewCurrentRange(); beginWatching(); interaction.select(inputMode.value,w.mode) })
// Simulation retains its off-page owner; real navigation retires it.
onDeactivated(() => { mounted.value = false; interaction.dispose(); leaveMouse();if (isReal.value) stop('已离开跟随页面，已请求停止') })
onBeforeUnmount(() => {
  emit('box-preview',null)
  mounted.value = false; endWatching(); if(handingOff)client.detach();else client.dispose()
  interaction.dispose()
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
        <p v-if="!isReal && !syntheticMode && camera?.state !== 'streaming'" class="hint">没有摄像头？<button class="btn btn--xs" @click="inputMode='mouse'">使用鼠标模拟</button></p>
        <div v-if="mouseMode" class="mouse-controls" @pointerenter="mousePresent=true" @pointerleave="leaveMouse" @pointercancel="leaveMouse" @focusout="mouseFocusOut">
          <div class="mouse-pad" tabindex="0" role="group" aria-label="鼠标控制板：移动鼠标控制左右上下" @pointermove="moveMouse" @pointerdown="moveMouse">
            <span class="mouse-pad-label">移动鼠标 · 左右 / 上下</span>
            <span class="mouse-dot" :style="{left:`${mousePosition.x*100}%`,top:`${mousePosition.y*100}%`}"></span>
          </div>
          <label class="mouse-depth">后退<input type="range" min="0" max="1" step="0.01" :value="mousePosition.depth" :disabled="!depth" aria-label="鼠标模拟前后位置" @input="mouseDepth" />前进</label>
          <button class="btn btn--xs" @click="centerMouse">目标回中心</button>
          <p class="hint">移入后跟随，移出控制区平滑回中心等待。仅仿真。</p>
        </div>
        <div v-if="inputMode==='keyboard'" class="input-help">
          <p>WASD / 方向键：左右上下<br>Q 后退 · E 前进 · 空格回中心</p>
          <p class="hint">开始跟随后按住移动，松开全部按键平滑回中心等待。输入框内不响应；失焦或隐藏后清空按键，重新按下才移动。</p>
        </div>
        <div v-if="inputMode==='gamepad'" class="input-help">
          <p role="status">{{interactionState.gamepad ? `已连接 #${interactionState.gamepad.index+1} · ${interactionState.gamepad.id}` : '未检测到手柄，请连接后按任意手柄按钮'}}</p>
          <p class="hint">左摇杆左右上下，右摇杆纵轴前后；18% 死区。连接或恢复后先松开摇杆，回中后才能输入。松杆、拔出、失焦或隐藏时平滑回中心等待。</p>
        </div>
        <dl v-if="!syntheticMode" class="vision-status"><div><dt>相机</dt><dd>{{cameraStatus}}</dd></div><div><dt>手掌检测</dt><dd>{{detectionStatus}}</dd></div><div><dt>实测帧率</dt><dd>{{frameRates}}</dd></div></dl>
        </div>
        <div class="actions"><button class="btn" :class="local.active || local.busy ? 'btn--danger' : 'btn--primary'" :disabled="!local.active && !local.busy && (!!startBlocked || !!rangeError)" @click="local.active || local.busy ? stop() : start()">{{local.active ? '停止跟随' : local.busy ? '正在准备…' : '开始跟随'}}</button></div>
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
        <p v-if="refreshNotice" class="hint" role="status">{{refreshNotice}}</p>
        <p v-if="isReal && realFlowSupported" class="hint">J1～J5 协调跟随位置，J6 保持 0°。丢掌或异常停止后，需手动重新开始。</p>
        <details class="range-settings" :open="settingsOpen" @toggle.self="settingsOpen=$event.target.open">
          <summary>范围设置</summary>
          <fieldset :disabled="local.active || local.busy">
            <label class="option">朝向<select aria-label="跟随朝向" :value="rangeDirection" @change="changeDirection($event.target.value)"><option :value="270">正面</option><option :value="0">右侧</option><option :value="90">背面</option><option :value="180">左侧</option></select></label>
            <label class="option"><input v-model="depth" type="checkbox" />{{syntheticMode ? '启用前后控制' : '手势前后跟随'}}</label>
            <p v-if="!syntheticMode" class="hint">掌心控制左右上下<span v-if="depth">，握拳后退、张开前进</span>；{{isReal ? '丢掌停止，不自动恢复。' : '丢掌回中心。'}}</p>
            <p v-if="isReal" class="hint">目标与反馈速度均须 ≤5%。实际工具、工具中心点、负载、路径与运动许可由后台检查，未通过不能启动。</p>
            <label v-if="!syntheticMode" class="option"><input v-model="rotation" type="checkbox" :disabled="isReal" />工具旋转（Rz）<span v-if="isReal">真机固定关闭</span></label>
            <div class="range-grid"><span>轴 / mm</span><span>中心坐标</span><span>范围尺寸</span>
              <template v-for="(axis,i) in ['X · 宽','Y · 深','Z · 高']" :key="axis"><span>{{axis}}</span>
                <div class="range-field"><input v-model.number="rangeCenter[i]" type="range" :min="i===2?0:-2000" max="2000" step="1" :aria-label="axis+'中心滑块'" @input="changeRange" /><input v-model.number="rangeCenter[i]" type="number" step="1" :aria-label="axis+'中心坐标'" @input="changeRange" /></div>
                <div class="range-field"><input v-model.number="rangeSize[i]" type="range" :min="i===1?20:100" :max="i===1?1000:3000" step="1" :aria-label="axis+'尺寸滑块'" @input="changeRange" /><input v-model.number="rangeSize[i]" type="number" min="1" max="5000" step="1" :aria-label="axis+'范围尺寸'" @input="changeRange" /></div>
              </template>
            </div>
            <div class="actions"><button class="btn btn--xs" :disabled="!!configureBlocked || !!rangeError" @click="configure">应用范围</button><button class="btn btn--xs" :disabled="!!rangeError" @click="saveRangeDefault">设为默认</button><button class="btn btn--xs" @click="resetRange">恢复默认</button></div>
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
