<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useWorld } from '../store'
import { TEACHING_DRAFT_KEY, DEFAULT_TEACHING_POSE, MAX_TEACHING_ROWS, validTeachingVector,
  validTeachingRows, copyTeachingRows, teachingDraftValue, readTeachingDraft, teachingContext, teachingFlight } from '../teachingDraft.js'
import { targetFor } from '../targetState.js'

const emit = defineEmits(['execution-target'])
const w = useWorld(), flight = teachingFlight(w)
const target = targetFor(w), targetOwner = Symbol('teaching-target')
const solutionDetails = ref(null)
const pose = ref([...DEFAULT_TEACHING_POSE]), label = ref('示教点'), rows = ref([])
const positionOnly = ref(false)
const solved = ref(null), message = ref('调整目标或取当前 TCP 后求解；不会执行运动。')
const storageError = ref(''), saveUnconfirmed = ref(false), past = ref([]), future = ref([])
const pending = ref(null), scheduled = ref(false)
const enabled = computed(() => w.connected !== false && ['sim','real'].includes(w.snap?.mode)
  && typeof w.snap?.session_id === 'string' && !!w.snap.session_id)
const simulationStorage=computed(()=>w.snap?.mode==='sim' && !w.snap?.real)
const context = computed(() => teachingContext(w))
const saving = computed(() => flight.kind.value === 'save')
const busy = computed(() => !!flight.kind.value || scheduled.value || !!pending.value)
const canAdd = computed(() => enabled.value && !busy.value && validTeachingVector(solved.value)
  && solvedRevision === revision && solvedContext === context.value && target.usable() && rows.value.length < MAX_TEACHING_ROWS)
const canSave = computed(() => enabled.value && simulationStorage.value && !busy.value && rows.value.length > 0 && validTeachingRows(rows.value, true))
const executionReason=computed(()=>busy.value?'正在求解，请等待预览完成。':target.blockReason())
let motionTimer=null
async function pollMotion(){clearTimeout(motionTimer);if(disposed || !target.state.executing)return;await target.refreshMotionState();if(!disposed && target.state.executing)motionTimer=setTimeout(pollMotion,700)}
watch(()=>target.state.executing,on=>{if(on)void pollMotion();else clearTimeout(motionTimer)})
async function executeTarget(){if(disposed || executionReason.value || !validTeachingVector(solved.value))return;await w.moveJ([...solved.value])}
async function cancelTarget(){invalidate();await target.cancelExecution();w.cancelPreview?.();message.value='目标已取消，请重新求解后再执行。'}
let disposed = false, timer = null, revision = 0, solvedRevision = -1, solvedContext = '', ownPreview = null
let branchSeed = null, branchContext = ''
let acceptingSolution = false
let storage = null
try {
  storage = window.localStorage
  const draft = readTeachingDraft(storage)
  if (draft) {
    pose.value = draft.pose; label.value = draft.label; rows.value = draft.rows; saveUnconfirmed.value = draft.saveUnconfirmed
    message.value = '已恢复本地草稿；逆解已失效，请重新求解。'
  }
} catch (error) { storageError.value = `无法恢复：${error.message}` }

function persist(overrides = {}) {
  try {
    if (!storage) throw new Error('本地存储不可用')
    storage.setItem(TEACHING_DRAFT_KEY, JSON.stringify(teachingDraftValue({ pose: pose.value, label: label.value,
      rows: rows.value, saveUnconfirmed: saveUnconfirmed.value, ...overrides })))
    storageError.value = ''
    return true
  } catch (error) { storageError.value = `草稿尚未可靠持久化：${error.message}`; return false }
}

function invalidate() {
  target.invalidate('示教目标已失效，请重新求解。', targetOwner)
  solutionDetails.value = null
  revision++; solved.value = null; solvedRevision = -1; solvedContext = ''
  clearTimeout(timer); timer = null; scheduled.value = false; pending.value = null
  // Clear only this panel's local display, never write into a changed mode.
  if (ownPreview && JSON.stringify(w.preview) === JSON.stringify(ownPreview)) w.preview = null
  ownPreview = null
}

function syncHandles() {
  if (disposed) return
  const visible = enabled.value && !saving.value && validTeachingVector(pose.value)
  const bindingRevision = revision, bindingContext = context.value
  window.__elfinScene?.setTeachTarget(visible ? [...pose.value] : null, visible ? xyz => {
    if (disposed || !enabled.value || saving.value || bindingRevision !== revision || bindingContext !== context.value
        || !Array.isArray(xyz) || xyz.length !== 3 || !xyz.every(Number.isFinite)) return
    if (xyz.every((value, i) => value === pose.value[i])) return
    pose.value = [...xyz, ...pose.value.slice(3)]
  } : null)
}

function queueSolve() {
  if (disposed || !enabled.value || saving.value || !validTeachingVector(pose.value)) return
  scheduled.value = true
  const token = target.beginSolve({ source: 'teaching', owner: targetOwner, pose: pose.value })
  timer = setTimeout(() => {
    timer = null; scheduled.value = false
    pending.value = { kind: 'ik', revision, context: context.value, target: [...pose.value], token }
    pump()
  }, 160)
}

function live(request) {
  return !disposed && enabled.value && request.revision === revision && request.context === context.value && target.current(request.token)
}

// Called synchronously by the parent before this tab unmounts. The event's
// one-shot claim transfers only preview ownership, never execution permission.
function handoffToExecution() {
  const transferable = () => !disposed && enabled.value && !busy.value && validTeachingVector(solved.value)
    && solvedRevision === revision && solvedContext === context.value
    && target.usable() && target.isOwner(targetOwner)
    && ownPreview && JSON.stringify(w.preview) === JSON.stringify(ownPreview)
  if (!transferable()) return false
  const sourceRevision = revision, sourceContext = context.value, joints = [...solved.value]
  let claimed = false
  emit('execution-target', { joints, pose: [...pose.value], context: sourceContext, claim() {
    if (claimed || !transferable() || revision !== sourceRevision || context.value !== sourceContext) return false
    claimed = true
    ownPreview = null  // invalidate()/unmount must not clear the parent's preview.
    return true
  } })
  return claimed
}
defineExpose({ handoffToExecution })

async function pump() {
  if (disposed || !enabled.value || saving.value || !pending.value) return
  const release = flight.acquire(pending.value.kind)
  if (!release) return
  const request = pending.value
  pending.value = null
  try {
    if (!live(request)) return
    if (request.kind === 'fk') {
      const result = await w.fk([...request.joints])
      if (!live(request) || JSON.stringify(request.joints) !== JSON.stringify(w.snap?.joints?.q)) return
      const target = result?.ok === true && Array.isArray(result.xyz_mm) && result.xyz_mm.length === 3
        && Array.isArray(result.rpy_deg) && result.rpy_deg.length === 3
        ? [...result.xyz_mm, ...result.rpy_deg] : null
      if (!validTeachingVector(target)) throw new Error(result?.why || '正解未返回有效 xyz_mm / rpy_deg')
      pose.value = target
      if (!scheduled.value) { invalidate(); queueSolve() }
    } else {
      // 正确：同一环境中的连续示教从最近有效分支继续；失败结果不能充当种子。
      const seed = branchContext === context.value && validTeachingVector(branchSeed) ? [...branchSeed]
        : validTeachingVector(w.snap?.joints?.q) ? [...w.snap.joints.q] : undefined
      const result = await w.ik([...request.target], { seed, ...(positionOnly.value ? { position_only: true } : {}) })
      if (!live(request)) return
      solutionDetails.value = result && typeof result === 'object' ? result : null
      if (result?.ok !== true || !validTeachingVector(result.joints)) throw new Error(result?.why || '逆解未返回有效 joints')
      if (positionOnly.value) {
        if (result.orientation_constrained !== false || !validTeachingVector(result.achieved)) {
          throw new Error('自动姿态响应不完整，未载入')
        }
        // 正确：明确显示实际求得的 A/B/C，不把姿态自由的解伪装成固定姿态解。
        acceptingSolution = true
        try { pose.value = [...request.target.slice(0, 3), ...result.achieved.slice(3)] }
        finally { acceptingSolution = false }
      }
      branchSeed = [...result.joints]; branchContext = context.value
      w.setPreview([...result.joints], { source: 'teaching', owner: targetOwner, pose: pose.value, from: request.token })
      ownPreview = [...result.joints]; solved.value = [...result.joints]
      solvedRevision = revision; solvedContext = context.value
      message.value = positionOnly.value ? '预览已更新，姿态自动，未执行运动' : '预览已更新，未执行运动'
    }
  } catch (error) {
    if (live(request)) {
      message.value = error.message || '求解失败'
      target.fail(message.value, request.token)
    }
  }
  finally { release() }
}

function current() {
  if (disposed || !enabled.value || busy.value || !validTeachingVector(w.snap?.joints?.q)) return
  invalidate()
  branchSeed = null; branchContext = ''
  pending.value = { kind: 'fk', revision, context: context.value, joints: [...w.snap.joints.q],
    token: target.beginSolve({ source: 'teaching', owner: targetOwner, pose: pose.value }) }
  pump()
}

function solveNow() {
  if (disposed || !enabled.value || busy.value || !validTeachingVector(pose.value)) return
  invalidate()
  pending.value = { kind: 'ik', revision, context: context.value, target: [...pose.value],
    token: target.beginSolve({ source: 'teaching', owner: targetOwner, pose: pose.value }) }
  pump()
}

function remember() {
  past.value.push(copyTeachingRows(rows.value))
  if (past.value.length > 30) past.value.shift()
  future.value = []
}

function restore(from, to) {
  if (disposed || saving.value || !from.length) return
  to.push(copyTeachingRows(rows.value)); if (to.length > 30) to.shift()
  rows.value = copyTeachingRows(from.pop())
}

function add() {
  if (disposed || !canAdd.value) return
  remember()
  rows.value = [...rows.value, { name: `${label.value.trim() || '示教点'} ${rows.value.length + 1}`, joints: [...solved.value] }]
}

function remove(index) {
  if (disposed || saving.value || !Number.isInteger(index) || index < 0 || index >= rows.value.length) return
  remember(); rows.value = rows.value.filter((_, i) => i !== index)
}

function rename(index, value) {
  if (disposed || saving.value || !rows.value[index] || typeof value !== 'string') return
  const name = value.slice(0, 80)
  if (rows.value[index].name === name) return
  remember()
  const next = copyTeachingRows(rows.value); next[index].name = name; rows.value = next
}

async function save() {
  if (disposed || !canSave.value) return
  const release = flight.acquire('save')
  if (!release) return
  const requestContext = context.value, requestRevision = revision, batch = copyTeachingRows(rows.value)
  saveUnconfirmed.value = true
  if (!persist()) { saveUnconfirmed.value = false; release(); return }
  let attempted = false
  try {
    if (disposed || !enabled.value || context.value !== requestContext) return
    attempted = true
    const response = await fetch('/api/teaching/batch', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ points: batch }) })
    const result = await response.json()
    if (!response.ok || result?.ok !== true) throw new Error(result?.why || `保存失败 (${response.status})`)
    if (!Array.isArray(result.items) || result.items.length !== batch.length || !Array.isArray(result.points)) {
      throw new Error('保存响应不完整，请核对点位列表后再重试')
    }
    if (disposed) return
    if (!enabled.value || context.value !== requestContext || revision !== requestRevision
        || JSON.stringify(rows.value) !== JSON.stringify(batch)) {
      message.value = '环境或草稿已变化，保存结果未采纳；草稿已保留，请核对点位列表。'
      return
    }
    // Durably clear first; storage failure after server success retains all drafts.
    if (!persist({ rows: [], saveUnconfirmed: false })) {
      message.value = '服务端已保存，但本地草稿清理失败；请核对点位列表，勿重复保存。'
      return
    }
    w.points = result.points
    rows.value = []; past.value = []; future.value = []; saveUnconfirmed.value = false
    message.value = `已保存 ${result.items.length} 个仿真点位`
  } catch (error) {
    if (!disposed) message.value = `${error.message || '保存失败'}；草稿已保留。请核对点位列表后再手动重试。`
  } finally {
    if (!disposed && !attempted) { saveUnconfirmed.value = false; persist() }
    release()
  }
}

watch(pose, () => {
  if (acceptingSolution) { persist(); syncHandles(); return }
  invalidate(); persist(); syncHandles()
  if (!validTeachingVector(pose.value)) message.value = '目标需为六维有限数值'
  else queueSolve()
}, { deep: true, flush: 'sync' })
watch(positionOnly, () => { invalidate(); syncHandles(); queueSolve() }, { flush: 'sync' })
watch([rows, label, saveUnconfirmed], () => persist(), { deep: true, flush: 'sync' })
watch(context, () => {
  branchSeed = null; branchContext = ''
  invalidate(); syncHandles()
  message.value = '模式、工具、连接或会话已变化，旧逆解失效；请重新求解。'
}, { flush: 'sync' })
watch(flight.kind, () => { syncHandles(); pump() })
onMounted(() => { syncHandles(); window.addEventListener('elfin-scene-ready', syncHandles) })
onBeforeUnmount(() => {
  clearTimeout(motionTimer)
  disposed = true; invalidate()
  window.removeEventListener('elfin-scene-ready', syncHandles)
  window.__elfinScene?.setTeachTarget(null)
})
</script>
<template>
  <section class="panel corner" :aria-busy="busy"><div class="panel-hd"><span class="panel-title">三维示教</span><span class="tag tag--mute">{{ w.snap?.mode==='real'?'真机目标预览':'仿真目标预览' }}</span></div>
    <div class="panel-bd col">
      <p v-if="!enabled" class="dim">请连接服务并选择有效的仿真或真机会话。</p>
      <p class="dim tiny">拖动场景坐标轴修改 TCP；只生成残影，不直接执行。草稿自动保存于本机，恢复后需重新求解。</p>
      <p v-if="storageError" class="note bad tiny" role="alert">{{ storageError }}</p>
      <p v-if="saveUnconfirmed" class="note tiny" role="status">保存结果尚未确认；草稿已保留。请核对点位列表，避免重复保存。</p>
      <div class="row" role="group" aria-label="示教姿态约束"><button class="btn" :class="{ 'btn--primary': !positionOnly }" :aria-pressed="!positionOnly" :disabled="!enabled || saving" @click="positionOnly = false">固定姿态</button><button class="btn" :class="{ 'btn--primary': positionOnly }" :aria-pressed="positionOnly" :disabled="!enabled || saving" @click="positionOnly = true">自动姿态</button></div>
      <div class="pose-grid"><label v-for="(axis,i) in ['X / mm','Y / mm','Z / mm','A / °','B / °','C / °']" :key="axis">{{axis}}<input v-model.number="pose[i]" class="input mono" type="number" step="1" :aria-label="axis" :disabled="!enabled || saving || (positionOnly && i >= 3)"></label></div>
      <div class="row"><button class="btn" :disabled="!enabled || busy" @click="current">取当前 TCP</button><button class="btn" :disabled="!enabled || busy || !validTeachingVector(pose)" @click="solveNow">求解预览</button></div>
      <p role="status" class="tiny">{{ saving ? '正在批量保存…' : busy ? '求解中…' : target.label() + ' · ' + (target.state.stage === 'failed' ? target.state.reason : message) }}</p>
      <div class="row"><button class="btn btn--primary grow" :disabled="!!executionReason || !validTeachingVector(solved)" :title="executionReason" @click="executeTarget">执行目标</button><button class="btn" :disabled="!!target.pauseReason()" :title="target.pauseReason()" @click="target.pauseOrResume">{{ target.isPaused()?'继续':'暂停' }}</button><button class="btn" :disabled="target.state.controlPending==='cancel'" @click="cancelTarget">取消</button></div>
      <p class="tiny dim">拖动与求解不移动机器人；仅显式执行目标通过预检后按关节轨迹运动。</p>
      <p v-if="target.state.controlMessage || target.state.executionMessage" class="note tiny" role="status">{{ target.state.controlMessage || target.state.executionMessage }}</p>
      <details v-if="solutionDetails">
        <summary>求解详情</summary>
        <p class="tiny mono">
        {{ solutionDetails.reason_code || (solutionDetails.ok ? 'solved' : '搜索未成功') }}
        <template v-if="Number.isFinite(solutionDetails.solve_ms)"> · 求解 {{ solutionDetails.solve_ms.toFixed(1) }} ms</template>
        · 位置最大轴残差 {{ Number.isFinite(solutionDetails.pos_err_mm) ? solutionDetails.pos_err_mm.toFixed(3) + ' mm' : '未计算' }}
        <template v-if="!positionOnly"> · 姿态最大轴残差 {{ Number.isFinite(solutionDetails.rot_err_deg) ? solutionDetails.rot_err_deg.toFixed(3) + ' °' : '未计算' }}</template>
        </p>
        <p v-if="solutionDetails.proof" class="tiny">几何证明：{{ solutionDetails.proof }}
        <template v-if="solutionDetails.bound_reason"> · 几何判据：{{ solutionDetails.bound_reason }}</template>
        <template v-if="Number.isFinite(solutionDetails.gap_mm)"> · 间隙 {{ solutionDetails.gap_mm.toFixed(3) }} mm</template>
        <template v-if="Number.isFinite(solutionDetails.upper_bound_mm)"> · 上界 {{ solutionDetails.upper_bound_mm.toFixed(3) }} mm</template>
        <template v-if="Number.isFinite(solutionDetails.tolerance_mm)"> · 容差 {{ solutionDetails.tolerance_mm.toFixed(3) }} mm</template>
        </p>
        <p v-if="solutionDetails.ok && validTeachingVector(solutionDetails.achieved)" class="tiny mono">实际求得 XYZ(mm) / A B C(°)：{{ solutionDetails.achieved.map(v => v.toFixed(3)).join(' / ') }}</p>
      </details>
      <input v-model="label" class="input" maxlength="60" aria-label="点位名称前缀" placeholder="点位名称前缀" :disabled="saving">
      <button class="btn btn--primary" :disabled="!canAdd" @click="add">加入点位草稿</button>
      <div class="row"><button class="btn btn--xs" :disabled="!past.length || saving" @click="restore(past,future)">撤销</button><button class="btn btn--xs" :disabled="!future.length || saving" @click="restore(future,past)">重做</button><span class="dim">{{rows.length}} / {{ MAX_TEACHING_ROWS }}</span></div>
      <div v-if="!rows.length" class="dim tiny">尚无草稿。调整目标后加入，可批量保存。</div>
      <div v-for="(row,index) in rows" :key="index" class="row"><input :value="row.name" class="input grow" maxlength="80" :aria-label="`草稿点位${index+1}名称`" :disabled="saving" @input="rename(index, $event.target.value)"><button class="btn btn--xs" :disabled="saving" @click="remove(index)">移除</button></div>
      <button class="btn btn--primary" :disabled="!canSave" @click="save">批量保存仿真点位</button>
      <p v-if="!simulationStorage" class="dim tiny">当前后端批量保存仅支持仿真；真机可预览、求解并显式执行，批量保存暂不可用。</p>
    </div>
  </section>
</template>
<style scoped>.pose-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.pose-grid label{min-width:0;font-size:11px;color:var(--tx-2)}.pose-grid input{width:100%}</style>
