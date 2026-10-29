<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import TeachingPanel from './TeachingPanel.vue'
import { teachingContext } from '../teachingDraft.js'
import { targetFor } from '../targetState.js'

const w = useWorld()
const target = targetFor(w), targetState = target.state, targetOwner = Symbol('points-target')
const { points, snap, tele } = storeToRefs(w)
const finitePose = values => Array.isArray(values) && values.length === 6 && values.every(Number.isFinite)
const name = ref('')
const storing = ref(false)
const storageMessage = ref('')
const renameId = ref(null), renameText = ref('')
const pointTab = ref(['storage','teaching'].includes(localStorage.getItem('huayan.pointTab')) ? localStorage.getItem('huayan.pointTab') : 'execute')
// 保留旧偏好及交接状态；示教在界面上归属「直接执行」的内层模式。
const executionPage = computed(() => pointTab.value !== 'storage')
const teachingPanel = ref(null)
const teachingEnvironment = computed(() => teachingContext(w))
watch(pointTab, (v) => localStorage.setItem('huayan.pointTab', v))
const previewPointId = ref(null)
async function renamePoint(p) {
  const r = await w.renamePoint(p.id, renameText.value.trim())
  if (r.ok) renameId.value = null
}
const granted = computed(() => !!(snap.value && snap.value.motion_granted))
const isReal = computed(() => snap.value && snap.value.mode === 'real')
const paused = computed(() => !!(snap.value && snap.value.paused))
const q = computed(() => finitePose(snap.value?.joints?.q) ? snap.value.joints.q : [])
const lo = computed(() => snap.value?.joints?.lower || [])
const hi = computed(() => snap.value?.joints?.upper || [])
/* 残影是否在场：读**后端**登记的 preview，不读前端本地变量。
   这样"真机已经站到假手上、后端自动收掉"能立刻反映到界面，不会出现两边不一致。 */
const preview = computed(() => (snap.value ? snap.value.preview : null))
const previewSafety = computed(() => snap.value?.preview_safety || null)
// level is the backend's execution decision. In simulation joint_level may
// remain red/alarm as a proximity diagnostic while level is green/yellow.
const previewBlocked = computed(() => !['green', 'yellow'].includes(previewSafety.value?.level)
  || !!previewSafety.value?.collisions?.length)

/* 滑块是「预设目标角」。没有残影、且用户没在拖的时候，它跟着机器人实际角实时更新 ——
   进页面第一眼看到的就是真机现在的姿态，而不是六个 0。 */
const tgt = ref(finitePose(preview.value) ? preview.value.slice() : null)
const editing = ref(0)          // 最近一次触摸滑块的时间戳，拖拽期间不被实际值盖掉
function adopt(a) { if (!tgt.value) tgt.value = a.slice(); else tgt.value = a.slice() }
const tv = computed(() => tgt.value || [0, 0, 0, 0, 0, 0])
watch(preview, (value) => { if (!tgt.value && finitePose(value)) adopt(value) })
watch(() => targetState.joints, value => { if (target.usable() && finitePose(value)) adopt(value) })

function slide(i, ev) {
  stopCartWork(); stopJointHold()
  if (!finitePose(q.value) || !Number.isFinite(Number(ev.target.value))) return
  if (!tgt.value) tgt.value = q.value.slice()
  editing.value = Date.now()
  const next = tgt.value.slice()
  next[i] = clamp(i, Number(ev.target.value))
  tgt.value = next
  // 点位目标统一先显示残影，最终只通过「执行目标」下发。
  w.setPreview(next, { source: 'joint', owner: targetOwner })
}
function fillStyle(i) {
  const l = lo.value[i] ?? -180, h = hi.value[i] ?? 180
  return { '--fill': (((tv.value[i] ?? 0) - l) / Math.max(1e-6, h - l)) * 100 + '%' }
}
const cls = (i) => {
  const m = Math.max(Math.abs((tv.value[i] ?? 0)>=0 ? (hi.value[i] ?? 180) : (lo.value[i] ?? -180)),1e-6)
  const r = Math.abs(tv.value[i] ?? 0) / m
  return r*100 >= (snap.value?.settings?.safety_red_pct ?? 95) ? 'bad' : r*100 >= (snap.value?.settings?.safety_yellow_pct ?? 90) ? 'warn' : 'acc'
}
const delta = (i) => ((tv.value[i] ?? 0) - (q.value[i] ?? 0))

/* ---- 目标预览：轻点一步，按住连续调整残影；此处不调用运动/停止接口 ---- */
const STEPS = [0.1, 0.5, 1, 5, 10]
const step = ref(1)
let holdTimer = null
const holding = ref(null)
const clamp = (i, v) => Math.max(Number.isFinite(lo.value[i]) ? lo.value[i] : -180,
  Math.min(Number.isFinite(hi.value[i]) ? hi.value[i] : 180, v))
const jointEditable = computed(() => granted.value && finitePose(q.value) && !cartMode.value && pointTab.value === 'execute')
function stepJoint(i, dir) {
  if (disposed || !jointEditable.value) return
  stopCartWork()
  editing.value = Date.now()
  // Use the latest local edit, not a delayed backend preview acknowledgement.
  const base = (finitePose(tgt.value) ? tgt.value : finitePose(preview.value) ? preview.value : q.value).slice()
  base[i] = clamp(i, base[i] + (dir ? step.value : -step.value))
  tgt.value = base
  w.setPreview(base, { source: 'joint', owner: targetOwner })
}
function repeatJoint() {
  if (!holding.value || !jointEditable.value) { stopJointHold(); return }
  stepJoint(holding.value.axis, holding.value.direction)
  holdTimer = setTimeout(repeatJoint, 80)
}
function pressJoint(axis, direction, event) {
  if (disposed || (event.button !== undefined && event.button !== 0) || event.isPrimary === false || !jointEditable.value) return
  stopJointHold(); stopCartWork()
  holding.value = { axis, direction, pointerId: event.pointerId, key: event.key }
  try { event.currentTarget?.setPointerCapture?.(event.pointerId) } catch {}
  stepJoint(axis, direction)
  holdTimer = setTimeout(repeatJoint, 320)
}
function releaseJoint(event) {
  if (event?.pointerId !== undefined && event.pointerId !== holding.value?.pointerId) return
  stopJointHold()
}
function jointKeyDown(axis, direction, event) {
  if (![' ', 'Enter'].includes(event.key)) return
  event.preventDefault()
  if (!event.repeat) pressJoint(axis, direction, { key: event.key })
}
function jointKeyUp(event) { if (event.key === holding.value?.key) stopJointHold() }
function jointClick(axis, direction, event) {
  // Pointer/key presses already stepped on down; detail=0 also supports AT clicks.
  if (event.detail === 0 && !holding.value) stepJoint(axis, direction)
}
onBeforeUnmount(() => {
  target.invalidate('点位面板已关闭，目标已失效；请重新预览。', targetOwner)
  stopCartWork(); stopJointHold(); clearTimeout(fkTimer); fkSeq++; disposed = true
  if (typeof window !== 'undefined') {
    window.removeEventListener('pointerup', releaseInputs, true)
    window.removeEventListener('pointercancel', releaseInputs, true)
    window.removeEventListener('keyup', inputKeyUp, true)
    window.removeEventListener('blur', suspendInputs)
    document.removeEventListener('visibilitychange', onVisibility)
  }
  clearInterval(holdBeat)
  if (freeOn.value) w.free(false)
})

/* 执行目标：下发之后**不能**马上清残影 —— 残影要留在原地等真机开上来，
   由后端判"六轴都进 ±0.3° 且站住 0.5s"才收。这是用户明确要的口径。 */
async function runTarget() {
  if (disposed || pointTab.value !== 'execute' || executionBlockReason.value) return
  await w.moveJ(targetState.joints.slice())
}
async function cancel() {
  const stopping=target.cancelExecution()
  resetCart()
  if (q.value.length === 6) adopt(q.value)
  await stopping
}
function toHome() {
  stopCartWork(); stopJointHold()
  const z = [0, 0, 0, 0, 0, 0]
  tgt.value = z.slice()
  w.setPreview(z, { source: 'joint', owner: targetOwner })
  if (cartMode.value) syncCartFromJoints(z)
}
function applyPoint(p) {
  if (!Array.isArray(p.joints) || p.joints.length !== 6 || !p.joints.every(v => typeof v === 'number' && Number.isFinite(v))) {
    w.error = '点位角度无效，不能预览'
    return false
  }
  stopCartWork(); stopJointHold()
  previewPointId.value = p.id
  editing.value = Date.now()
  tgt.value = p.joints.slice()
  w.setPreview(tgt.value, { source: 'point', owner: targetOwner, pose: p.tcp ? poseValues(p.tcp) : null })
  if (cartMode.value) syncCartFromJoints(tgt.value)
  return true
}
function goPoint(p) {
  if (!applyPoint(p)) return
  pointTab.value = 'execute'
}
function previewPoint(p) {
  // 仅登记三维残影；留在存储页，不调用任何运动入口。
  applyPoint(p)
}

/* ---- 笛卡尔目标 → 逆解 ---- */
const ikRes = ref(null)
const cartMode = ref(localStorage.getItem('huayan.cartMode') === '1')
watch(cartMode, (v) => localStorage.setItem('huayan.cartMode', v ? '1' : '0'))
const cartNames = ['X', 'Y', 'Z', 'Rx', 'Ry', 'Rz']
const linearStep = ref(1), angularStep = ref(1)
const cartTarget = ref([0, 0, 1195, 0, 0, 0])
// 手输保留原始草稿，提交前不让 IK 或实时反馈改写输入。
const cartDraft = ref(null)
const tcpNow = computed(() => (snap.value ? snap.value.tcp : null))
let cartTimer = null
let fkTimer = null
let fkSeq = 0
let cartRevision = 0
let cartWorker = null
const cartQueued = ref(false)
let disposed = false
const cartSolving = ref(false)
const cartLoading = ref(false)
const cartBusy = computed(() => cartSolving.value || cartLoading.value)
const cartDirty = ref(false)
const cartError = ref('')
const cartHold = ref(null)
let cartHoldTimer = null
let lastCartPose = null
let lastCartJoints = null
let cartToken = null
watch(jointEditable, (on) => { if (!on) stopJointHold() })
const poseValues = (p) => [...p.xyz_mm, ...(p.rpy_deg || [0, 0, 0])].slice(0, 6).map(Number)
watch(q, (a) => {
  // Keep the claimed teaching target while waiting for a possibly delayed
  // preview acknowledgement; actual feedback must not silently replace it.
  if (target.usable() || targetState.executing || cartDraft.value) return
  if (!finitePose(a) || finitePose(preview.value) || holding.value || cartBusy.value || cartQueued.value) return
  if (Date.now() - editing.value < 1200) return
  adopt(a)
  if (cartMode.value) cartDirty.value = false
}, { immediate: true })
watch(tcpNow, (p) => {
  if (p && !preview.value && !cartBusy.value && !cartDirty.value && Date.now() - editing.value > 1200 && Array.isArray(p.xyz_mm)) {
    cartTarget.value = poseValues(p)
    lastCartPose = cartTarget.value.slice()
    lastCartJoints = q.value.slice()
  }
}, { immediate: true })
watch(tv, (a) => {
  if (cartMode.value || !a || a.length !== 6) return
  clearTimeout(fkTimer)
  const seq = ++fkSeq
  fkTimer = setTimeout(async () => {
    try {
      const r = await w.fk(a.slice())
      if (disposed || seq !== fkSeq || cartMode.value || !r.ok) return
      cartTarget.value = poseValues(r)
      lastCartPose = cartTarget.value.slice()
      lastCartJoints = a.slice()
    } catch (e) { if (!disposed && seq === fkSeq) w.error = e.message }
  }, 80)
})
async function syncCartFromJoints(joints) {
  stopCartWork()
  clearTimeout(fkTimer)
  const seq = ++fkSeq
  if (joints.length !== 6) return
  cartLoading.value = true
  try {
    const r = await w.fk(joints)
    if (disposed || seq !== fkSeq || !cartMode.value) return
    if (r.ok) {
      cartTarget.value = poseValues(r)
      lastCartPose = cartTarget.value.slice()
      lastCartJoints = joints
      ikRes.value = null
      cartDirty.value = !!preview.value
    }
  } catch (e) { if (!disposed && seq === fkSeq) cartError.value = e.message }
  finally { if (seq === fkSeq) cartLoading.value = false }
}
watch(cartMode, (on) => {
  stopCartWork(); stopJointHold()
  // 切出示教时由交接事件提供坐标，不能再用延迟 FK 覆盖该目标。
  if (on && pointTab.value !== 'teaching') syncCartFromJoints((tgt.value || preview.value || q.value).slice())
}, { immediate: true, flush: 'sync' })
function selectPreviewMode(mode) {
  if (mode === 'teaching') { pointTab.value = 'teaching'; return }
  cartMode.value = mode === 'cartesian'
  pointTab.value = 'execute'
}
function acceptTeachingTarget(offer) {
  if (disposed || pointTab.value !== 'execute' || w.connected === false || !['sim','real'].includes(snap.value?.mode)
      || !snap.value?.session_id || offer?.context !== teachingEnvironment.value
      || !finitePose(offer?.joints) || !finitePose(offer?.pose) || typeof offer.claim !== 'function') return
  if (!offer.claim()) return
  stopCartWork(); stopJointHold()
  const joints = offer.joints.slice()
  tgt.value = joints.slice(); editing.value = Date.now(); previewPointId.value = null
  cartTarget.value = offer.pose.slice(); lastCartPose = offer.pose.slice(); lastCartJoints = joints.slice()
  cartDirty.value = true; cartError.value = ''; ikRes.value = null
  // Re-register the same preview; adoption alone is not backend acknowledgement.
  w.setPreview(joints, { source: 'teaching', pose: offer.pose, owner: targetOwner })
}
watch(pointTab, (tab, previous) => {
  if (tab !== 'execute') { stopCartWork(); stopJointHold() }
  if (tab === 'teaching' || previous === 'teaching') {
    if (tab === 'teaching') target.invalidate('示教目标尚未求解，请调整目标或求解预览。')
    else if (tab === 'execute' && !teachingPanel.value?.handoffToExecution?.()) target.invalidate('示教目标尚未有效交接，请重新求解或调整目标后预览。')
  }
}, { flush: 'sync' })
// A model/TCP change invalidates any solution requested with the old tool frame.
watch(teachingEnvironment, () => {
  stopCartWork(); stopJointHold()
  cartDirty.value = false
  if (cartMode.value && pointTab.value === 'execute' && q.value.length === 6) syncCartFromJoints((tgt.value || q.value).slice())
}, { flush: 'sync' })
function previewCart() {
  clearTimeout(cartTimer)
  if (cartWorker) return cartWorker
  if (cartLoading.value || disposed || !cartQueued.value) return Promise.resolve()
  cartWorker = (async () => {
    while (cartQueued.value && cartMode.value && !disposed) {
      cartQueued.value = false
      cartSolving.value = true
      const revision = cartRevision
      const solveToken = cartToken
      const goal = cartTarget.value.slice()
      const seed = (lastCartJoints || tgt.value || q.value).slice()
      let r
      try { r = await w.ik(goal, { seed, allow_partial: true }) }
      catch (error) { r = { ok: false, why: error.message } }
      if (disposed || revision !== cartRevision || !cartMode.value || !target.current(solveToken)) continue
      if (!r || typeof r !== 'object') r = { ok: false, why: '预览响应无效，请重试。' }
      ikRes.value = r
      if (r.ok && finitePose(r.joints) && finitePose(r.achieved)) {
        cartError.value = r.clipped ? '已到达当前方向的可用边界。' : ''
        editing.value = Date.now()
        tgt.value = r.joints.slice()
        lastCartJoints = r.joints.slice()
        cartTarget.value = r.achieved.slice()
        lastCartPose = cartTarget.value.slice()
        w.setPreview(tgt.value, { source: 'cartesian', owner: targetOwner, pose: r.achieved, from: solveToken })
      } else if (lastCartPose) {
        // Keep controls and ghost at the same last valid pose. A failed target
        // must never leave an enabled execution button pointing at a stale ghost.
        cartTarget.value = lastCartPose.slice()
        cartError.value = r.why || '求解未成功，请调整目标或手动重试。'
        target.fail(cartError.value, solveToken)
      } else {
        cartError.value = r.why || '求解未成功，请调整目标或手动重试。'
        target.fail(cartError.value, solveToken)
      }
    }
  })().finally(() => { cartWorker = null; cartSolving.value = false; if (cartQueued.value && !disposed) previewCart() })
  return cartWorker
}
function queueCart() {
  cartToken = target.beginSolve({ source: 'cartesian', owner: targetOwner, pose: cartTarget.value })
  cartDirty.value = true; cartError.value = ''; ikRes.value = null
  editing.value = Date.now(); cartRevision++; cartQueued.value = true
  clearTimeout(cartTimer)
  cartTimer = setTimeout(previewCart, 40)
}
// R = Rz * Ry * Rx, matching the shared model. A base-axis increment is
// pre-multiplied, as in Huayan's official ElfinTeleopAPI::PoseStampedRotation.
function rotateBaseAxis(values, axis, degrees) {
  const [rx, ry, rz] = values.slice(3).map(v => v * Math.PI / 180)
  const [cx, sx, cy, sy, cz, sz] = [Math.cos(rx), Math.sin(rx), Math.cos(ry), Math.sin(ry), Math.cos(rz), Math.sin(rz)]
  const r = [[cz*cy, cz*sy*sx-sz*cx, cz*sy*cx+sz*sx], [sz*cy, sz*sy*sx+cz*cx, sz*sy*cx-cz*sx], [-sy, cy*sx, cy*cx]]
  const angle = degrees * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle)
  const change = axis === 0 ? [[1,0,0],[0,c,-s],[0,s,c]] : axis === 1 ? [[c,0,s],[0,1,0],[-s,0,c]] : [[c,-s,0],[s,c,0],[0,0,1]]
  const m = change.map(row => [0,1,2].map(j => row.reduce((sum, v, k) => sum + v*r[k][j], 0)))
  const pitch = Math.asin(Math.max(-1, Math.min(1, -m[2][0])))
  const regular = Math.abs(Math.cos(pitch)) > 1e-9
  const roll = regular ? Math.atan2(m[2][1], m[2][2]) : Math.atan2(-m[1][2], m[1][1])
  const yaw = regular ? Math.atan2(m[1][0], m[0][0]) : 0
  return [...values.slice(0, 3), ...[roll, pitch, yaw].map(v => Math.abs(v) < 1e-12 ? 0 : v * 180 / Math.PI)]
}
function stepCart(i, direction) {
  if (cartLoading.value || !cartMode.value || disposed || pointTab.value !== 'execute') return
  if (cartDraft.value && !applyCartDraft()) return
  const amount = direction * (i < 3 ? linearStep.value : angularStep.value)
  if (i < 3) { const next = cartTarget.value.slice(); next[i] += amount; cartTarget.value = next }
  else cartTarget.value = rotateBaseAxis(cartTarget.value, i - 3, amount)
  queueCart()
}
function setCartValue(i, raw) {
  if (cartLoading.value || !cartMode.value || disposed || pointTab.value !== 'execute') return
  const draft = (cartDraft.value || cartTarget.value.map(String)).slice()
  draft[i] = raw
  // 包括清空、负号等中间输入，都立即取消旧求解并作废旧执行目标。
  stopCartWork()
  cartDraft.value = draft; cartDirty.value = true; ikRes.value = null
  cartError.value = ''; editing.value = Date.now()
  target.invalidate('笛卡尔目标正在编辑，请输入有效数值并提交预览。')
}
function applyCartDraft() {
  const draft = cartDraft.value
  if (!draft || draft.some(value => String(value).trim() === '') || !finitePose(draft.map(Number))) {
    cartError.value = '请输入六个有效数值后提交预览。'
    return false
  }
  cartTarget.value = draft.map(Number); cartDraft.value = null
  return true
}
function commitCart() {
  if (!cartDraft.value || cartLoading.value || !cartMode.value || disposed || pointTab.value !== 'execute') return
  if (!applyCartDraft()) return
  queueCart()
}
function repeatCart() {
  if (!cartHold.value || disposed) return
  // Do not accumulate an unbounded unreachable target while IK is working.
  if (!cartBusy.value && !cartQueued.value) stepCart(cartHold.value.axis, cartHold.value.direction)
  cartHoldTimer = setTimeout(repeatCart, 80)
}
function pressCart(axis, direction, event) {
  if ((event.button !== undefined && event.button !== 0) || event.isPrimary === false || cartLoading.value || !cartMode.value || disposed || pointTab.value !== 'execute') return
  releaseCart()
  cartHold.value = { axis, direction, pointerId: event.pointerId, key: event.key }
  try { event.currentTarget?.setPointerCapture?.(event.pointerId) } catch {}
  stepCart(axis, direction)
  cartHoldTimer = setTimeout(repeatCart, 320)
}
function releaseCart(event) {
  if (event?.pointerId !== undefined && event.pointerId !== cartHold.value?.pointerId) return
  clearTimeout(cartHoldTimer); cartHoldTimer = null; cartHold.value = null
}
function cartKeyDown(axis, direction, event) {
  if (![' ', 'Enter'].includes(event.key)) return
  event.preventDefault()
  if (event.repeat) return
  pressCart(axis, direction, { key: event.key })
}
function cartKeyUp(event) { if (event.key === cartHold.value?.key) releaseCart() }
function cartClick(axis, direction, event) { if (event.detail === 0) stepCart(axis, direction) }
function stopCartWork() {
  if (cartToken && target.current(cartToken) && targetState.stage === 'solving') target.invalidate('目标求解已取消，请重新预览。', targetOwner)
  releaseCart(); clearTimeout(cartTimer); clearTimeout(fkTimer)
  ++cartRevision; ++fkSeq; cartQueued.value = false; cartLoading.value = false; cartSolving.value = false
  cartDraft.value = null
  if (lastCartPose) cartTarget.value = lastCartPose.slice()
}
function stopJointHold() {
  clearTimeout(holdTimer); holdTimer = null; holding.value = null
}
function releaseInputs(event) { releaseJoint(event); releaseCart(event) }
function inputKeyUp(event) { jointKeyUp(event); cartKeyUp(event) }
function suspendInputs() { stopCartWork(); stopJointHold(); freeUp() }
function onVisibility() { if (document.hidden) suspendInputs() }
onMounted(() => {
  window.addEventListener('pointerup', releaseInputs, true)
  window.addEventListener('pointercancel', releaseInputs, true)
  window.addEventListener('keyup', inputKeyUp, true)
  window.addEventListener('blur', suspendInputs)
  document.addEventListener('visibilitychange', onVisibility)
})
function syncActual() {
  stopCartWork(); stopJointHold()
  cartDirty.value = false; cartError.value = ''; ikRes.value = null
  if (q.value.length === 6) adopt(q.value)
  if (tcpNow.value) { cartTarget.value = poseValues(tcpNow.value); lastCartPose = cartTarget.value.slice(); lastCartJoints = q.value.slice() }
}
function resetCart() {
  syncActual(); stopJointHold()
  w.cancelPreview()
}
async function teach() {
  const r = await w.teach(name.value || `P${points.value.length + 1}`)
  if (r.ok) name.value = ''
}
async function addPreset() {
  if (storing.value || disposed) return
  if (!target.usable()) { w.error = '目标已失效，请重新预览后再存储。'; return }
  const token = { revision: targetState.revision, context: targetState.context }, submittedName = name.value
  storing.value = true; storageMessage.value = ''
  try {
    const result = await w.addPoint(name.value || `P${points.value.length + 1}`, targetState.joints.slice())
    if (disposed) return
    if (result?.ok && target.current(token)) {
      if (name.value === submittedName) name.value = ''
      storageMessage.value = '当前目标点位已保存，未执行运动。'
    } else {
      storageMessage.value = result?.why || '目标已变化或点位存储失败，名称已保留，请核对点位列表。'
      w.error = storageMessage.value
    }
  } catch (error) { if (!disposed) { storageMessage.value = error.message || '点位存储失败，名称与目标已保留。'; w.error = storageMessage.value } }
  finally { if (!disposed) storing.value = false }
}
/* ---- 手拖示教（Basic Free Drive）：按住才生效，松手即退出 ---- */
const free = computed(() => (snap.value ? snap.value.free : { on: false }))
const freeOn = computed(() => !!(free.value && free.value.on))
const ctrlFsm = computed(() => (snap.value || {}).live_fsm)
const freeReadback = computed(() => {
  const r = (snap.value || {}).real || {}
  return r.free_drive
})
let holdBeat = null
async function freeDown() {
  if (!isReal.value || !granted.value) return
  await w.free(true, true)
  clearInterval(holdBeat)
  // 按住期间每 0.5s 续一次：浏览器丢 pointerup（Alt+Tab、弹窗抢焦点）时不至于一直松着闸
  holdBeat = setInterval(() => { if (freeOn.value) w.free(true, true) }, 500)
}
function freeUp() {
  clearInterval(holdBeat); holdBeat = null
  if (freeOn.value) w.free(false)
}

// One explanation drives both the disabled button and the visible status.
// Keep acknowledgement and safety separate so a diagnostic never hides why.
const executionBlockReason = computed(() => {
  if (holding.value || cartHold.value || cartBusy.value || cartQueued.value) return '正在调整或求解目标，请等待预览完成。'
  const reason = target.blockReason()
  if (reason) return reason
  if (!finitePose(tv.value)) return '目标关节值无效，请重新预览目标。'
  if (!tv.value.every((v, i) => Math.abs(v - preview.value[i]) < .001)) return '等待后端确认最新目标预览；当前目标与预览不一致。'
  return ''
})
let motionTimer=null
async function pollMotion(){clearTimeout(motionTimer);if(disposed || !targetState.executing)return;await target.refreshMotionState();if(!disposed && targetState.executing)motionTimer=setTimeout(pollMotion,700)}
watch(()=>targetState.executing,on=>{if(on)void pollMotion();else clearTimeout(motionTimer)})
onBeforeUnmount(()=>clearTimeout(motionTimer))

/* ---- 末端 IO：吸盘/气阀是作业动作，所以放在作业页 ---- */
const endDo = computed(() => (((tele.value || {}).end_do) || ((snap.value || {}).real || {}).end_do || []))
const endDi = computed(() => (((snap.value || {}).real || {}).end_di) || null)
async function setDo(bit, val) { await w.call('/api/io/enddo', { bit, val }) }
</script>

<template>
  <div class="col points-workspace" :class="{ 'storage-view': pointTab === 'storage' }">
    <div class="point-tabs" role="tablist" aria-label="点位工作区">
      <button id="point-tab-execute" class="point-tab" :class="{ on: executionPage }" role="tab" :aria-selected="executionPage" aria-controls="point-pane-execute" :tabindex="executionPage ? 0 : -1" @click="pointTab === 'storage' && (pointTab = 'execute')">直接执行</button>
      <button id="point-tab-storage" class="point-tab" :class="{ on: pointTab === 'storage' }" role="tab" :aria-selected="pointTab === 'storage'" aria-controls="point-pane-storage" :tabindex="pointTab === 'storage' ? 0 : -1" @click="pointTab = 'storage'">存储</button>
    </div>
    <section v-if="executionPage" id="point-pane-execute" role="tabpanel" aria-labelledby="point-tab-execute" class="panel corner">
      <div class="panel-hd motion-head">
        <span class="panel-title">目标预览</span>
        <span class="tag tag--mute motion-state" role="status">{{ target.label() }}</span>
        <div class="mode-bar no-fold">
          <div class="mode-group" role="group" aria-label="目标预览模式">
            <span class="mode-label">目标</span>
            <div class="seg" role="tablist" aria-label="目标类型">
              <button id="point-mode-joint" role="tab" :class="{ on: pointTab === 'execute' && !cartMode }" :aria-selected="pointTab === 'execute' && !cartMode" :aria-pressed="pointTab === 'execute' && !cartMode" aria-controls="point-mode-content" @click.stop="selectPreviewMode('joint')">关节</button>
              <button id="point-mode-cartesian" role="tab" :class="{ on: pointTab === 'execute' && cartMode }" :aria-selected="pointTab === 'execute' && cartMode" :aria-pressed="pointTab === 'execute' && cartMode" :disabled="q.length !== 6" aria-controls="point-mode-content" @click.stop="selectPreviewMode('cartesian')">笛卡尔</button>
              <button id="point-mode-teaching" role="tab" :class="{ on: pointTab === 'teaching' }" :aria-selected="pointTab === 'teaching'" :aria-pressed="pointTab === 'teaching'" aria-controls="point-pane-teaching" @click.stop="selectPreviewMode('teaching')">三维示教</button>
            </div>
          </div>
        </div>
      </div>
      <TeachingPanel v-if="pointTab === 'teaching'" ref="teachingPanel" id="point-pane-teaching" role="tabpanel" aria-labelledby="point-mode-teaching" @execution-target="acceptTeachingTarget" />
      <div v-else id="point-mode-content" role="tabpanel" :aria-labelledby="cartMode ? 'point-mode-cartesian' : 'point-mode-joint'" class="panel-bd">
        <div v-if="!cartMode" class="row nowrap">
          <span class="dim tiny">步距</span>
          <button v-for="s in STEPS" :key="s" class="btn btn--xs" :class="{ 'btn--on': step === s }"
                  @click="step = s">{{ s }}°</button>
          <span class="grow"></span>
          <button class="btn btn--xs" :disabled="!granted || q.length !== 6" title="只预览零位，不移动机器人" @click="toHome">预览零位</button>
        </div>

        <div v-if="!cartMode" v-for="i in 6" :key="i" class="ax">
          <span class="axn disp">J{{ i }}</span>
          <span class="real mono dim" title="实际角（控制器上报）">{{ q[i - 1] !== undefined ? q[i - 1].toFixed(1) : '--' }}</span>
          <button class="btn btn--xs btn--sq joint-preview" :disabled="!jointEditable"
                  :aria-label="'J' + i + '− 预览'" title="轻点一步，长按连续调整目标残影"
                  :aria-pressed="holding?.axis === i - 1 && holding?.direction === 0"
                  @pointerdown.prevent="pressJoint(i - 1, 0, $event)" @pointerup="releaseJoint" @pointercancel="releaseJoint" @lostpointercapture="releaseJoint" @pointerleave="releaseJoint" @blur="releaseJoint"
                  @keydown="jointKeyDown(i - 1, 0, $event)" @keyup="jointKeyUp" @click.prevent="jointClick(i - 1, 0, $event)" @contextmenu.prevent>−</button>
          <input type="range" :style="fillStyle(i - 1)"
                 :min="lo[i - 1] ?? -180" :max="hi[i - 1] ?? 180" step="0.05"
                 :value="tv[i - 1]" :disabled="q.length !== 6" @input="slide(i - 1, $event)"
                 :aria-label="'J' + i + ' 目标角'" />
          <button class="btn btn--xs btn--sq joint-preview" :disabled="!jointEditable"
                  :aria-label="'J' + i + '+ 预览'" title="轻点一步，长按连续调整目标残影"
                  :aria-pressed="holding?.axis === i - 1 && holding?.direction === 1"
                  @pointerdown.prevent="pressJoint(i - 1, 1, $event)" @pointerup="releaseJoint" @pointercancel="releaseJoint" @lostpointercapture="releaseJoint" @pointerleave="releaseJoint" @blur="releaseJoint"
                  @keydown="jointKeyDown(i - 1, 1, $event)" @keyup="jointKeyUp" @click.prevent="jointClick(i - 1, 1, $event)" @contextmenu.prevent>+</button>
          <input class="tgtv mono" :class="cls(i - 1)" type="text" readonly :aria-label="'J' + i + ' 目标角（只读）'" title="目标角只读，请使用左侧滑块或 ± 按钮调整"
                 :value="tv[i - 1] !== undefined ? tv[i - 1].toFixed(2) : ''"
                 tabindex="-1" />
          <span class="dl mono tiny" :class="Math.abs(delta(i - 1)) > 0.05 ? 'acc' : 'dim'"
                title="目标 − 实际">{{ (delta(i - 1) >= 0 ? '+' : '') + delta(i - 1).toFixed(1) }}</span>
        </div>
        <div v-else class="cart-buttons">
          <div class="row nowrap cart-actions">
            <span class="dim tiny">目标 TCP</span>
            <button class="btn btn--xs" @click="resetCart">回到实际</button>
          </div>
          <section v-for="(group, gi) in [cartNames.slice(0, 3), cartNames.slice(3)]" :key="gi" class="cart-group">
            <div class="row"><b class="tiny">{{ gi ? '旋转 · 固定轴 Rx / Ry / Rz' : '平移 · 基座坐标' }}</b><span class="grow"></span>
              <label class="tiny">步距 <select v-if="gi === 0" v-model.number="linearStep" aria-label="平移步距"><option v-for="s in STEPS" :key="s" :value="s">{{ s }} mm</option></select>
                <select v-else v-model.number="angularStep" aria-label="旋转步距"><option v-for="s in STEPS" :key="s" :value="s">{{ s }}°</option></select></label>
            </div>
            <div v-for="(lab, j) in group" :key="lab" class="cart-row">
              <button class="btn cart-jog" :disabled="cartLoading" :aria-label="lab + '− 预览'"
                      :aria-pressed="cartHold?.axis === gi * 3 + j && cartHold?.direction === -1"
                      @pointerdown.prevent="pressCart(gi * 3 + j, -1, $event)" @pointerup="releaseCart" @pointercancel="releaseCart" @lostpointercapture="releaseCart" @pointerleave="releaseCart" @blur="releaseCart"
                      @keydown="cartKeyDown(gi * 3 + j, -1, $event)" @keyup="cartKeyUp" @click.prevent="cartClick(gi * 3 + j, -1, $event)" @contextmenu.prevent>{{ lab }}−</button>
              <label class="cart-value"><input type="number" class="input mono" :value="cartDraft ? cartDraft[gi * 3 + j] : Number(cartTarget[gi * 3 + j].toFixed(2))"
                      :step="gi ? angularStep : linearStep" :disabled="cartLoading" :aria-label="lab + ' 目标'"
                      @input="setCartValue(gi * 3 + j, $event.target.value)" @change="commitCart" @keydown.enter.prevent="commitCart" /><span>{{ gi ? '°' : 'mm' }}</span></label>
              <button class="btn cart-jog" :disabled="cartLoading" :aria-label="lab + '+ 预览'"
                      :aria-pressed="cartHold?.axis === gi * 3 + j && cartHold?.direction === 1"
                      @pointerdown.prevent="pressCart(gi * 3 + j, 1, $event)" @pointerup="releaseCart" @pointercancel="releaseCart" @lostpointercapture="releaseCart" @pointerleave="releaseCart" @blur="releaseCart"
                      @keydown="cartKeyDown(gi * 3 + j, 1, $event)" @keyup="cartKeyUp" @click.prevent="cartClick(gi * 3 + j, 1, $event)" @contextmenu.prevent>{{ lab }}+</button>
            </div>
          </section>
          <div v-if="cartError" class="note warn tiny" role="status">{{ cartError }}</div>
          
        </div>

        <p class="dim tiny">轻点 ± 预览一步，长按连续调整残影，松开停止预览调整。只有点击“执行目标”才移动机器人，按关节轨迹到点。</p>
        <div class="row">
          <button class="btn btn--primary grow" :disabled="!!executionBlockReason" :aria-describedby="executionBlockReason ? 'point-execution-status' : undefined"
                  :title="executionBlockReason || '按关节轨迹到达目标，不保证末端沿直线移动'" @click="runTarget">
            执行目标
          </button>
          <button class="btn" :disabled="!!target.pauseReason()" :title="target.pauseReason()" @click="target.pauseOrResume">{{ target.isPaused()?'继续':'暂停' }}</button>
          <button class="btn" :disabled="targetState.controlPending==='cancel' || (!targetState.executing && !preview && !holding && !cartDirty && !cartBusy && !cartQueued && !cartHold)" @click="cancel">取消</button>
        </div>
        <div v-if="preview && previewSafety?.level === 'yellow'" class="note warn tiny">黄色预警：接近围栏或地面，仍可执行。</div>
        <div v-if="executionBlockReason" id="point-execution-status" class="note tiny" :class="preview && previewBlocked ? 'bad' : 'warn'" role="status">{{executionBlockReason}}</div>
        <p v-if="targetState.executionMessage" class="tiny" role="status">{{ targetState.executionMessage }}</p>
        <p v-if="targetState.controlMessage" class="note tiny" role="status">{{ targetState.controlMessage }}</p>
        <div v-if="paused" class="note bad">
          后端报告暂停，请核对控制器状态；仅当前有效执行目标可通过上方“继续”恢复。
        </div>
        
        <div v-if="!granted" class="note warn">
          当前只读，先在顶部完成就绪。
        </div>
      </div>
    </section>

    <section v-if="isReal && pointTab === 'execute'" class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">手拖示教</span>
        <span class="grow"></span>
        <span class="tag" :class="freeOn ? 'tag--warn' : 'tag--mute'">{{ freeOn ? '松闸中' : '已抱闸' }}</span>
      </div>
      <div class="panel-bd">
        <button class="btn free no-fold" :class="freeOn ? 'btn--on' : 'btn--arm'"
                :disabled="!granted && !freeOn"
                @pointerdown.prevent="freeDown" @pointerup.prevent="freeUp"
                @pointerleave="freeUp" @contextmenu.prevent>
          <span class="fl1">{{ freeOn ? '松开即抱闸' : '按住松开抱闸' }}</span>
        </button>
        <div class="note warn tiny">松闸无重力补偿，请托住机械臂。</div>
        
        <div v-if="free && free.why" class="note bad tiny">{{ free.why }}</div>
        <div class="row">
          <button class="btn grow" :disabled="!granted" @click="teach">示教保存当前点</button>
          <button class="btn" :disabled="!preview" @click="cancel">收残影</button>
        </div>
      </div>
    </section>

    <section v-if="pointTab === 'execute'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">末端 IO</span>
      </div>
      <div class="panel-bd">
        <div class="row nowrap">
          <span class="dim tiny">可写 DO1–DO3</span>
          <button v-for="b in 3" :key="b" class="btn btn--xs"
                  :class="{ 'btn--on': Number(endDo[b - 1]) === 1 }"
                  :disabled="!granted" @click="setDo(b - 1, Number(endDo[b - 1]) === 1 ? 0 : 1)">
            DO{{ b }} {{ Number(endDo[b - 1]) === 1 ? '亮' : '灭' }}
          </button>
        </div>
        <div class="row nowrap" v-if="endDi">
          <span class="dim tiny">回读 DI1–DI4</span>
          <span v-for="(v, i) in endDi" :key="i" class="tag" :class="Number(v) ? 'tag--ok' : 'tag--mute'">
            DI{{ i + 1 }}={{ v }}</span>
        </div>
        
      </div>
    </section>

    <section v-if="pointTab === 'storage'" id="point-pane-storage" role="tabpanel" aria-labelledby="point-tab-storage" class="panel point-create">
      <div class="panel-hd">
        <span class="panel-title">点位</span>
        <span class="panel-sub">{{ points.length }} 个点位</span>
      </div>
      <div class="panel-bd">
        <div class="row">
          <input class="grow" v-model="name" aria-label="目标点位名称" placeholder="点位名，留空自动编号" />
          <button class="btn" :disabled="storing || !target.usable()" @click="addPreset">{{ storing ? '保存中…' : '存目标值' }}</button>
        </div>
        <p class="tiny" role="status">目标状态：{{ target.label() }} · {{ target.usable() ? '存储当前目标关节值，不执行运动。' : target.blockReason() }}</p>
        <p v-if="storageMessage" class="tiny" role="status">{{ storageMessage }}</p>
        
      </div>
    </section>

    <section v-if="pointTab === 'storage'" class="panel point-library">
      <div class="panel-hd">
        <span class="panel-title">点位表</span>
        <span class="panel-sub">{{ points.length }} 个点位</span>
        <button v-if="preview" class="btn btn--xs" @click="cancel">收残影</button>
      </div>
      <div class="panel-bd flush point-library-body">
        <div class="point-list" role="list" aria-label="已保存点位">
          <article v-for="p in points" :key="p.id" class="point-card" :class="{'previewing': preview && previewPointId === p.id}" role="listitem">
            <div class="point-card-head">
              <div class="disp point-identity">
                  <form v-if="renameId === p.id" @submit.prevent="renamePoint(p)"><input v-model="renameText" aria-label="点位名称" maxlength="80" /><button class="btn btn--xs" type="submit">存</button><button class="btn btn--xs" type="button" @click="renameId = null">取消</button></form>
                  <button v-else class="point-name" title="修改点位名称" @click="renameId = p.id; renameText = p.name">
                    <b>{{ p.name || p.code }}</b>
                    <span v-if="p.name && p.name !== p.code" class="point-code mono">{{ p.code }}</span>
                    <span class="edit-mark" aria-hidden="true">✎</span>
                  </button>
              </div>
              <span class="tag source-tag" :class="p.source === 'real' ? 'tag--ok' : 'tag--mute'">
                {{ p.source === 'real' ? '真机' : p.source === 'sim' ? '仿真' : p.source }}</span>
              <div class="ops">
                  <button class="btn btn--xs" :aria-label="'预览残影 ' + (p.name || p.code)" :aria-pressed="!!preview && previewPointId === p.id" @click="previewPoint(p)">预览残影</button>
                  <button class="btn btn--xs go-btn" title="载入目标残影并转到直接执行" @click="goPoint(p)">到点</button>
                  <button class="btn btn--xs btn--sq btn--danger" @click="w.removePoint(p.id)">删</button>
              </div>
            </div>
            <div class="joint-strip mono" aria-label="六轴关节角">
              <span v-for="(v, i) in p.joints" :key="i"><small>J{{ i + 1 }}</small><b>{{ Number(v).toFixed(3) }}°</b></span>
            </div>
            <details v-if="p.tcp" class="tcp-detail">
              <summary>TCP 坐标</summary>
              <div class="tcp-strip mono">
                <span v-for="(v, i) in [...(p.tcp.xyz_mm || []), ...(p.tcp.rpy_deg || [])]" :key="i"><small>{{ ['X','Y','Z','A','B','C'][i] }}</small><b>{{ Number(v).toFixed(3) }}{{ i < 3 ? 'mm' : '°' }}</b></span>
              </div>
            </details>
          </article>
          <div v-if="!points.length" class="dim tiny empty">
            {{ isReal ? '暂无点位，可示教保存当前姿态或存储目标值。' : '暂无点位，调整目标姿态后点击「存目标值」。' }}
          </div>
        </div>
      </div>
    </section>

  </div>
</template>
<style scoped>
.motion-head { display:grid; grid-template-columns:auto minmax(0,1fr) auto 7px;
  grid-template-areas:'title space state arrow' 'modes modes modes arrow'; gap:7px 8px; align-items:center; }
.motion-head::after { grid-area:arrow; margin-left:0; justify-self:end; }
.motion-head > .panel-title { grid-area:title; }
.motion-state { grid-area:state; justify-self:end; white-space:nowrap; }
.mode-bar { grid-area:modes; display:grid; grid-template-columns:minmax(0,1fr); gap:8px; min-width:0; }
.mode-group { display:grid; grid-template-columns:auto minmax(0,1fr); align-items:center; gap:5px; min-width:0; }
.mode-label { color:var(--tx-3); font-size:10px; letter-spacing:.04em; white-space:nowrap; }
.seg { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:2px; min-width:0; padding:2px;
  border:1px solid var(--line-2); background:var(--bg-inset); }
.seg button { min-width:0; height:24px; padding:0 5px; border:0; color:var(--tx-2); background:transparent;
  font:inherit; font-size:11px; white-space:nowrap; cursor:pointer; transition:background .16s,color .16s; }
.seg button:hover:not(:disabled) { color:var(--tx-1); background:rgba(122,162,220,.09); }
.seg button:focus-visible { outline:2px solid var(--acc); outline-offset:1px; }
.seg button:disabled { cursor:not-allowed; opacity:.42; }
.point-name { border: 0; background: none; padding: 3px 0; color: var(--tx-1); font: inherit; text-align: left; cursor: pointer; overflow-wrap: anywhere; }
.point-name { display:flex; align-items:center; flex-wrap:wrap; gap:4px; width:100%; }
.point-name b { color:var(--acc); font-family:var(--font-mono); font-size:12px; }
.point-code { padding:1px 4px; border:1px solid var(--line-2); border-radius:4px; color:var(--tx-3); font-size:9.5px; }
.edit-mark { color:var(--tx-3); font-size:10px; opacity:.75; }
.point-name:hover .edit-mark { color:var(--acc); opacity:1; }
td.disp form { display: flex; flex-wrap: wrap; gap: 3px; }
td.disp form input { width: 100%; min-width: 70px; }
</style>

<style scoped>
.ax { display: grid; grid-template-columns: 20px 40px 22px minmax(0, 1fr) 22px 46px 34px;
      align-items: center; gap: 3px; padding: 2px 0; }
.axn { font-size: 12px; font-weight: 700; color: var(--tx-2); }
.real { font-size: 10.5px; text-align: right; }
.dl { text-align: right; font-size: 10px; }
.btn--sq { width: 22px; padding: 0; font-size: 12px; }
.ops { display: flex; gap: 2px; }
.points-workspace.storage-view { flex:1; min-height:0; height:100%; overflow:hidden; }
.point-create { flex:0 0 auto; }
.point-library { flex:1 1 auto; min-height:0; display:flex; flex-direction:column; overflow:hidden; }
.point-library > .panel-hd { flex:0 0 auto; }
.point-library-body { flex:1 1 auto; min-height:0; overflow:hidden; }
.point-list { flex:1 1 auto; min-height:0; overflow:auto; display:flex; flex-direction:column; gap:5px; padding:5px; }
.point-card { padding:7px; border:1px solid var(--line); border-radius:6px; background:rgba(255,255,255,.018); }
.point-card:hover { border-color:var(--line-2); background:rgba(122,162,220,.04); }
.point-card.previewing { border-color:var(--acc); background:rgba(47,214,240,.06); }
.point-card-head { display:grid; grid-template-columns:minmax(0,1fr) auto auto; align-items:center; gap:6px; }
.point-identity { min-width:0; }
.source-tag { padding:1px 5px; font-size:10px; }
.joint-strip,.tcp-strip { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:3px; margin-top:6px; }
.joint-strip > span,.tcp-strip > span { min-width:0; padding:4px 2px; border-radius:4px; text-align:center; background:var(--bg-inset); }
.joint-strip small,.tcp-strip small { display:block; color:var(--tx-3); font-size:9px; line-height:1.1; }
.joint-strip b,.tcp-strip b { display:block; margin-top:2px; overflow:hidden; color:var(--tx-1); font-size:10px; line-height:1.2; text-overflow:ellipsis; white-space:nowrap; }
.tcp-detail { margin-top:4px; color:var(--tx-3); font-size:10px; }
.tcp-detail summary { width:max-content; cursor:pointer; color:var(--acc); }
.go-btn { min-width:38px; padding:0 6px; color:var(--ok); border-color:rgba(46,230,168,.3); }
.go-btn:hover:not(:disabled) { border-color:var(--ok); background:var(--ok-soft); }
.unit { width: 24px; text-align: right; }
.empty { padding: 10px; }
/* 目标角为只读回显：统一通过滑块或 ± 按钮修改，避免出现输入值与预设状态不同步。 */
.tgtv { width: 46px; min-width: 0; padding: 0 3px; height: 22px; font-size: 12px; text-align: right;
        background: var(--bg-inset); border: 1px solid var(--line-2); border-radius: 5px;
        color: var(--acc); font-family: var(--font-mono); }
.tgtv.warn { color: var(--warn); }
.tgtv.bad { color: var(--danger); }
.tgtv:focus { outline: none; border-color: var(--acc); }
.tgtv[readonly] { cursor: default; opacity: .9; }
.cart-buttons { display:flex; flex-direction:column; gap:10px; }
.cart-group { display:flex; flex-direction:column; gap:6px; border:1px solid var(--line); padding:8px; border-radius:6px; }
.cart-row { display:grid; grid-template-columns:minmax(60px,1fr) minmax(70px,1.4fr) minmax(60px,1fr); align-items:center; gap:8px; }
.cart-jog,.joint-preview { touch-action:none; user-select:none; }
.cart-jog[aria-pressed="true"],.joint-preview[aria-pressed="true"] { color:var(--acc); border-color:var(--acc); background:var(--acc-soft); }
.cart-value { display:flex; align-items:center; gap:4px; min-width:0; }
.cart-value input { width:100%; min-width:0; text-align:right; color:var(--acc); font-size:12px; }
.cart-value span { flex:none; color:var(--tx-3); font-size:10px; }
.free { flex-direction: column; gap: 0; height: 46px; }
.fl1 { font-family: var(--font-display); font-size: 13px; font-weight: 700; letter-spacing: .05em; }
.fl2 { font-size: 9px; letter-spacing: .14em; opacity: .7; }
@media (max-width: 350px) {
  .mode-bar { grid-template-columns:1fr; }
  .point-card-head { grid-template-columns:minmax(0,1fr) auto; }
  .source-tag { display:none; }
  .joint-strip,.tcp-strip { grid-template-columns:repeat(3,minmax(0,1fr)); }
}
</style>
