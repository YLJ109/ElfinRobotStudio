<script setup>
import { computed, onMounted, onBeforeUnmount, onActivated, onDeactivated, ref, watch } from 'vue'
import { useWorld } from '../store'
import { STUDIO_TABS, STUDIO_SESSION_KEY, buildGeneratePayload, buildRevisionPayload, filterRecordings, filterTrajectory, simplifyTrajectory, inspectionPayload, canRunStudio, isRevisionConflict, readStudioSession, recordingStatus } from '../taskStudio.js'

const props = defineProps({ initialTab: { type: String, default: 'recordings' } })
const w = useWorld()
const tab = ref(STUDIO_TABS.some(t => t.id === props.initialTab) ? props.initialTab : 'recordings')
const catalog = ref({ recordings: [], recording: { active: false, count: 0 } })
const busy = ref(false), error = ref(''), message = ref(''), loading = ref(false)
const name = ref('工艺任务'), search = ref(''), pointSearch = ref(''), onlySelected = ref(false)
const draft = ref(null), recordingId = ref(null), selected = ref([]), stride = ref(2), page = ref(0)
const dirty = ref(false), pendingDelete = ref(false)
const revisionConflict = ref(false), latestRecording = ref(null), localRecovery = ref(null)
const storageError = ref('')
const inspection = ref(null), confirmed = ref(false), scanKind = ref('raster'), handlingKind = ref('pick-place')
const form = ref({ center: '350, 0, 300', rpy: '180, 0, 0', width: 100, height: 100, spacing: 10, radius: 50, segments: 32,
  pick: '300, -100, 300, 180, 0, 0', place: '300, 100, 300, 180, 0, 0', lift: 50, speed: 10,
  rows: 2, cols: 2, layers: 1, step_x: 40, step_y: 40, step_z: 30 })
let disposed = false, suspended = false, controller = null, pollController = null, timer = null, generation = 0
try {
  const saved = readStudioSession(globalThis.sessionStorage)
  if (saved) {
    tab.value = saved.tab; draft.value = saved.draft || null; name.value = saved.name || '工艺任务'
    recordingId.value = saved.recordingId ?? null; dirty.value = !!saved.dirty
    revisionConflict.value = !!saved.revisionConflict; localRecovery.value = saved.localRecovery || null
    form.value = { ...form.value, ...saved.form }
    scanKind.value = saved.scanKind === 'circle' ? 'circle' : 'raster'
    handlingKind.value = saved.handlingKind === 'pallet' ? 'pallet' : 'pick-place'
    message.value = '已恢复本次会话草稿；执行前需重新检查。'
  }
} catch (e) { storageError.value = '会话草稿恢复失败：' + e.message }
function persistSession() {
  try {
    globalThis.sessionStorage?.setItem(STUDIO_SESSION_KEY, JSON.stringify({ version: 1, tab: tab.value,
      draft: draft.value, name: name.value, recordingId: recordingId.value, dirty: dirty.value,
      revisionConflict: revisionConflict.value, localRecovery: localRecovery.value, form: form.value,
      scanKind: scanKind.value, handlingKind: handlingKind.value }))
  } catch (e) { storageError.value = '会话草稿保存失败，请导出 JSON 留档：' + e.message }
}
const rows = computed(() => filterRecordings(catalog.value.recordings, search.value))
const recordingState = computed(() => recordingStatus(catalog.value.recording))
const points = computed(() => draft.value?.joints?.length ? draft.value.joints : draft.value?.poses || [])
const filteredPoints = computed(() => filterTrajectory(points.value, pointSearch.value, selected.value, onlySelected.value))
const visiblePoints = computed(() => filteredPoints.value.slice(page.value * 80, (page.value + 1) * 80))
const runnable = computed(() => canRunStudio({ connected: w.connected, mode: w.snap?.mode, inspection: inspection.value, confirmed: confirmed.value, busy: busy.value }))
const fieldLabels = { width: '宽度 mm · 1–3000', height: '高度 mm · 1–3000', spacing: '路径间距 mm · 1–3000', radius: '半径 mm · 1–1500', segments: '圆周分段 · 8–128', lift: '抬升 mm · 1–1000', speed: '速度 % · 1–100', rows: '行数', cols: '列数', layers: '层数 · 1–32', step_x: 'X 间距 mm · 1–2000', step_y: 'Y 间距 mm · 1–2000', step_z: '层高 mm · 1–2000' }
const numericFields = computed(() => tab.value === 'scan' ? [...(scanKind.value === 'raster' ? ['width', 'height', 'spacing'] : ['radius', 'segments']), 'speed'] : ['lift', 'speed', ...(handlingKind.value === 'pallet' ? ['rows', 'cols', 'layers', 'step_x', 'step_y', 'step_z'] : [])])

async function request(path = '', body, method = body === undefined ? 'GET' : 'POST', signal = controller?.signal) {
  const response = await fetch('/api/studio' + path, { method, signal, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) })
  const result = await response.json()
  if (!response.ok || result.ok === false) {
    const failure = new Error(typeof result.detail === 'string' ? result.detail : result.why || result.error || `请求失败 (${response.status})`)
    failure.status = response.status
    throw failure
  }
  return result
}
function invalidate() { inspection.value = null; confirmed.value = false }
function preview(path = draft.value?.poses) { window.__elfinScene?.setPlanPath(path || []) }
function cancel() {
  generation++; controller?.abort(); controller = null; busy.value = false; loading.value = false
  invalidate(); message.value = '已取消等待；已提交的操作可能已被服务端接受，请刷新核对。'
}
async function task(action) {
  if (busy.value || disposed || suspended) return
  clearTimeout(timer); pollController?.abort()
  const version = ++generation
  controller = new AbortController(); const signal = controller.signal
  busy.value = true; error.value = ''; message.value = ''
  const current = () => !disposed && !suspended && version === generation && !signal.aborted
  try { await action(current, signal) }
  catch (e) { if (current() && e.name !== 'AbortError') error.value = e.message }
  finally { if (current()) { busy.value = false; loading.value = false; controller = null; schedulePoll() } }
}
function schedulePoll() {
  clearTimeout(timer)
  if (disposed || suspended || busy.value || tab.value !== 'recordings' || !catalog.value.recording?.active) return
  timer = setTimeout(async () => {
    pollController = new AbortController()
    try { const result = await request('', undefined, 'GET', pollController.signal); if (!disposed && !pollController.signal.aborted) catalog.value = result }
    catch (e) { if (!disposed && e.name !== 'AbortError') { error.value = e.message; return } }
    schedulePoll()
  }, 2000)
}
function refresh() { return task(async current => { loading.value = true; const result = await request(); if (current()) catalog.value = result }) }
function record(active) {
  task(async current => {
    if (active && !name.value.trim()) throw new Error('请填写录制名称')
    await request(active ? '/record/start' : '/record/stop', active ? { name: name.value.trim() } : {})
    if (!current()) return
    const result = await request(); if (current()) { catalog.value = result; message.value = active ? '正在采样当前轨迹；录制不会启动运动。' : '录制已停止。' }
  })
}
function adopt(value, id = null) {
  // Changes replace the entire draft so checked plan IDs cannot survive an edit.
  invalidate(); draft.value = value; recordingId.value = id; selected.value = []; page.value = 0; dirty.value = false; pendingDelete.value = false; preview()
  revisionConflict.value = false; latestRecording.value = null
}
function openRecording(item) {
  task(async current => {
    const result = await request('/recordings/' + encodeURIComponent(item.id))
    if (!current()) return
    const value = result.recording || result.item || result
    if ((value.joints?.length || value.poses?.length)) buildRevisionPayload(value, value.name)
    adopt(JSON.parse(JSON.stringify(value)), item.id); name.value = value.name || item.name || '轨迹录制'
  })
}
function generate() {
  task(async current => {
    const payload = buildGeneratePayload(tab.value === 'scan' ? scanKind.value : handlingKind.value, name.value, form.value)
    invalidate()
    const result = await request('/generate', payload)
    if (current()) { const generated = { ...result.draft, speed: result.draft.speed ?? payload.options.speed }; inspectionPayload(generated); adopt(generated); message.value = '草稿已生成，仅显示预览。请到任务验收检查。' }
  })
}
function editPoints(next) {
  const original = points.value, key = draft.value.joints?.length ? 'joints' : 'poses'
  const indices = next.map(point => original.indexOf(point))
  const edited = { ...draft.value, [key]: next, program: '', count: next.length, points: [], duration: null }
  // Preserve matching spatial previews, but edited geometry no longer has original sample timing.
  for (const field of ['joints', 'poses']) {
    if (field !== key && Array.isArray(draft.value[field]) && draft.value[field].length === original.length) edited[field] = indices.map(i => draft.value[field][i])
  }
  invalidate(); draft.value = edited; selected.value = []; page.value = 0; preview()
  dirty.value = true
}
function removeSelected() { const ids = new Set(selected.value); editPoints(points.value.filter((_, i) => !ids.has(i))) }
function simplify() { try { editPoints(simplifyTrajectory(points.value, Number(stride.value))) } catch (e) { error.value = e.message } }
function saveRevision() {
  if (recordingId.value == null || revisionConflict.value) return
  return task(async current => {
    const payload = buildRevisionPayload(draft.value, name.value)
    let result
    try { result = await request('/recordings/' + encodeURIComponent(recordingId.value), payload, 'PUT') }
    catch (e) {
      if (current() && isRevisionConflict(e)) {
        invalidate(); revisionConflict.value = true; latestRecording.value = null
        throw new Error('revision 冲突：服务端录制已更新。本地草稿已保留，请读取最新版本后选择恢复方式。')
      }
      throw e
    }
    if (!current()) return
    invalidate(); message.value = '编辑已保存，修订号已递增；服务端仅保留最新录制。'
    const saved = result.recording || result.item
    if (saved) adopt(saved, saved.id ?? recordingId.value)
    else { invalidate(); revisionConflict.value = true; throw new Error('保存已响应但未返回新 revision。本地草稿已保留，请读取最新版本核对。') }
    const list = await request(); if (current()) catalog.value = list
  })
}
function loadLatestRevision() {
  if (recordingId.value == null) return
  return task(async current => {
    const result = await request('/recordings/' + encodeURIComponent(recordingId.value))
    if (!current()) return
    const value = result.item || result.recording || result
    buildRevisionPayload(value, value.name)
    latestRecording.value = JSON.parse(JSON.stringify(value))
    message.value = '已读取服务端最新版本，本地草稿保持原样。请选择恢复方式。'
  })
}
function keepLocalRevision() {
  if (busy.value || !latestRecording.value) return
  // This explicit choice changes only the expected revision; saving remains a separate action.
  draft.value = { ...draft.value, revision: latestRecording.value.revision }
  invalidate(); dirty.value = true; revisionConflict.value = false; latestRecording.value = null
  message.value = '已采用最新修订号并保留本地点位。再次保存将以本地整条轨迹替换服务端轨迹。'
}
function useLatestRevision() {
  if (busy.value || !latestRecording.value) return
  const backup = { draft: JSON.parse(JSON.stringify(draft.value)), name: name.value, id: recordingId.value }
  const latest = latestRecording.value
  adopt(latest, recordingId.value); name.value = latest.name
  localRecovery.value = backup
  message.value = '已使用服务端版本，本地草稿已留作备份，可恢复或导出。'
}
function restoreLocalDraft() {
  if (busy.value || !localRecovery.value) return
  const backup = localRecovery.value
  adopt(backup.draft, backup.id); name.value = backup.name; dirty.value = true
  revisionConflict.value = true; localRecovery.value = null
  message.value = '已恢复本地草稿及其原始 revision，请重新读取最新版本核对。'
}
function deleteRecording() {
  if (!pendingDelete.value || recordingId.value == null) return
  task(async current => {
    await request('/recordings/' + encodeURIComponent(recordingId.value), undefined, 'DELETE')
    if (!current()) return
    adopt(null); message.value = '录制已删除，请使用之前导出的 JSON 留档。'
    const list = await request(); if (current()) catalog.value = list
  })
}
function inspect() {
  if (busy.value) return
  tab.value = 'acceptance'
  task(async current => {
    invalidate()
    // Edited recordings are inspected from local points, never from an outdated saved revision.
    const payload = inspectionPayload(draft.value)
    // Always inspect the local draft with its explicit speed; saved-recording endpoint may omit it.
    const result = await request('/inspect', payload)
    if (current()) { inspection.value = result; preview(result.report?.tcp_path); message.value = '检查完成。报告不等于现场安全认证。' }
  })
}
function run() {
  if (!runnable.value) return
  const plan_id = inspection.value.plan_id
  task(async current => {
    // Consume confirmation before the request; double-clicks and retries require another inspection.
    invalidate()
    const result = await request('/run', { plan_id })
    if (current()) message.value = result.warn || '仿真执行已提交；完成情况以实时状态为准。'
  })
}
async function stop() {
  invalidate()
  const result = await w.call('/api/program/stop', {})
  if (!disposed) { if (result?.ok === false) error.value = result.why || '停止请求失败'; else message.value = '已提交停止请求，请核对实时状态。' }
}
function exportDraft() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(draft.value, null, 2)], { type: 'application/json;charset=utf-8' }))
  const a = document.createElement('a'); a.href = url; a.download = `${name.value.replace(/[\\/:*?"<>|]/g, '_') || 'recording'}.json`; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
watch([pointSearch, onlySelected], () => { page.value = 0 })
watch(() => [w.snap?.mode, w.connected], invalidate)
watch(tab, () => { cancel(); pollController?.abort(); clearTimeout(timer); message.value = ''; if (tab.value === 'recordings') refresh() }, { flush: 'sync' })
watch([draft, name, recordingId, dirty, revisionConflict, localRecovery, form, tab, scanKind, handlingKind], persistSession, { deep: true })
onMounted(refresh)
function suspend() {
  suspended = true; cancel(); pollController?.abort(); clearTimeout(timer); preview([]); persistSession()
}
onDeactivated(suspend)
onActivated(() => { if (suspended) { suspended = false; preview(); refresh() } })
onBeforeUnmount(() => { suspend(); disposed = true })
</script>

<template>
  <div class="task-studio">
    <header class="panel studio-heading">
      <div><h2 class="disp">工艺工作区</h2></div>
      <span class="tag tag--acc">仅仿真执行</span>
      <button class="btn btn--danger" @click="stop">停止程序</button>
    </header>
    <nav class="studio-tabs workspace-tabs" role="tablist" aria-label="工艺分类">
      <button v-for="t in STUDIO_TABS" :key="t.id" :id="`studio-tab-${t.id}`" class="btn" role="tab" :aria-controls="`studio-panel-${t.id}`" :aria-selected="tab === t.id" :tabindex="tab === t.id ? 0 : -1" :class="{ 'btn--primary': tab === t.id }" :aria-pressed="tab === t.id" @click="tab = t.id">{{ t.label }}</button>
    </nav>
    <div class="row tiny dim"><span>真机执行未开放。生成、录制和预览均不会自动启动运动。</span><span class="grow" /><button v-if="busy" class="btn btn--xs" @click="cancel">取消等待</button></div>
    <p v-if="error" class="note bad" role="alert">{{ error }} <button class="btn btn--xs" :disabled="busy" @click="refresh">重新连接工作区</button></p>
    <p v-if="message" class="note" role="status">{{ message }}</p>
    <p v-if="storageError" class="note warn" role="alert">{{ storageError }}</p>
    <p v-if="loading" class="dim" role="status">正在加载工作区…</p>

    <section v-if="tab === 'recordings'" class="studio-grid" id="studio-panel-recordings" role="tabpanel" aria-labelledby="studio-tab-recordings" tabindex="0">
      <div class="panel studio-body col">
        <div class="row"><h3 class="grow">录制库</h3><button class="btn btn--xs" :disabled="busy" @click="refresh">刷新</button></div>
        <label>录制名称<input v-model="name" class="ipt" maxlength="80" :disabled="busy" /></label>
        <div class="row"><button class="btn btn--primary" :disabled="busy || catalog.recording?.active" @click="record(true)">开始录制</button><button class="btn" :disabled="busy || !catalog.recording?.active" @click="record(false)">停止录制</button></div>
        <span class="tiny" :class="recordingState.warning ? 'warn' : catalog.recording?.active ? 'acc' : 'dim'" role="status">{{ recordingState.text }}</span>
        <p v-if="catalog.recording?.active && (catalog.recording.interrupted || catalog.recording.full)" class="note warn">{{ catalog.recording.interrupted || '容量已满，不再新增采样点。' }} 请停止录制以保存已有采样。</p>
        <input v-model="search" class="ipt" aria-label="筛选录制" placeholder="搜索名称 / ID" />
        <p v-if="!rows.length && !loading" class="dim">{{ search ? '没有匹配的录制。' : '还没有录制。开始采样后，手动完成你的示教轨迹。' }}</p>
        <button v-for="item in rows" :key="item.id" class="btn recording-row" :disabled="busy" @click="openRecording(item)"><span>{{ item.name || item.id }}</span><span class="dim tiny">{{ item.count ?? item.point_count ?? '—' }} 点 · r{{ item.revision ?? 1 }}</span></button>
      </div>
      <div class="panel studio-body col">
        <div class="row"><h3 class="grow">轨迹编辑</h3><span class="tag">{{ points.length }} 点<span v-if="draft?.revision != null"> · r{{ draft.revision }}</span></span></div>
        <div v-if="localRecovery" class="row note"><span>保留了一份本地编辑备份：{{ localRecovery.name }} · r{{ localRecovery.draft.revision }}</span><button class="btn" :disabled="busy" @click="restoreLocalDraft">恢复本地草稿</button></div>
        <div v-if="revisionConflict" class="col note warn" role="alert">
          <span>revision 冲突或修订号待核对。本地草稿未丢失，请先导出留档，再选择恢复方式。</span>
          <button class="btn" :disabled="busy" @click="loadLatestRevision">读取服务端最新版本</button>
          <template v-if="latestRecording"><span>服务端 r{{ latestRecording.revision }} · {{ latestRecording.name }} · {{ latestRecording.count ?? latestRecording.joints?.length ?? latestRecording.poses?.length }} 点；本地 r{{ draft?.revision }} · {{ points.length }} 点。保留本地编辑后再次保存会替换整条服务端轨迹。</span><div class="row"><button class="btn" :disabled="busy" @click="keepLocalRevision">采用最新修订号，保留本地编辑</button><button class="btn" :disabled="busy" @click="useLatestRevision">使用服务端版本，备份本地草稿</button></div></template>
        </div>
        <p class="tiny warn">编辑会替换录制，请先导出原始 JSON。服务端仅保留最新版本，修订号用于防止覆盖冲突。</p>
        <p v-if="!draft" class="dim">从左侧打开录制，选择轨迹点后可删除或按步长抽稀。</p>
        <template v-else>
          <div class="row"><input v-model="pointSearch" class="ipt grow" aria-label="筛选轨迹点" placeholder="筛选点号 / 数值" /><label class="row"><input v-model="onlySelected" type="checkbox" />仅已选</label></div>
          <div class="row"><button class="btn btn--xs" :disabled="busy" @click="selected = filteredPoints.map(p => p.index)">选中筛选结果</button><button class="btn btn--xs" :disabled="busy" @click="selected = []">清除选择</button><button class="btn btn--xs" :disabled="busy || !selected.length" @click="removeSelected">删除 {{ selected.length }} 点</button></div>
          <div class="point-list"><label v-for="row in visiblePoints" :key="row.index" class="point-row mono"><input v-model="selected" type="checkbox" :value="row.index" :disabled="busy" /><span>#{{ row.index + 1 }}</span><span>{{ row.point.map(v => Number(v).toFixed(2)).join(' · ') }}</span></label><p v-if="!filteredPoints.length" class="dim">没有匹配的轨迹点。</p></div>
          <div class="row tiny"><button class="btn btn--xs" :disabled="page === 0" @click="page--">上一页</button><span>{{ page + 1 }} / {{ Math.max(1, Math.ceil(filteredPoints.length / 80)) }}</span><button class="btn btn--xs" :disabled="(page + 1) * 80 >= filteredPoints.length" @click="page++">下一页</button></div>
          <div class="row"><label class="row">每 N 点保留<input v-model.number="stride" type="number" min="2" class="ipt short" :disabled="busy" /></label><button class="btn" :disabled="busy || points.length < 3" @click="simplify">抽稀 · 保留首尾</button></div>
          <div class="row"><button class="btn" :disabled="busy || revisionConflict || recordingId == null || !points.length" @click="saveRevision">保存新 revision</button><button class="btn" :disabled="busy" @click="exportDraft">导出 JSON</button><button class="btn btn--primary" :disabled="busy || !points.length" @click="inspect">检查编辑轨迹</button><button class="btn btn--xs" :disabled="busy || recordingId == null" @click="pendingDelete = !pendingDelete">删除录制…</button></div>
          <div v-if="pendingDelete" class="row note warn"><span>将删除此录制，请先导出留档。</span><button class="btn btn--danger" :disabled="busy" @click="deleteRecording">确认删除</button><button class="btn" :disabled="busy" @click="pendingDelete = false">取消</button></div>
        </template>
      </div>
    </section>

    <section v-else-if="tab === 'scan' || tab === 'handling'" class="studio-grid" :id="`studio-panel-${tab}`" role="tabpanel" :aria-labelledby="`studio-tab-${tab}`" tabindex="0">
      <form class="panel studio-body col" @submit.prevent="generate">
        <h3>{{ tab === 'scan' ? '擦拭 / 扫描路径' : '搬运 / 码垛路径' }}</h3>
        <fieldset :disabled="busy" class="col">
          <label>任务名称<input v-model="name" class="ipt" maxlength="80" required /></label>
          <label v-if="tab === 'scan'">路径类型<select v-model="scanKind" class="ipt"><option value="raster">往复栅格</option><option value="circle">圆周扫描</option></select></label>
          <label v-else>作业类型<select v-model="handlingKind" class="ipt"><option value="pick-place">取放搬运</option><option value="pallet">多层码垛</option></select></label>
          <template v-if="tab === 'scan'"><label>中心 X, Y, Z（mm）<input v-model="form.center" class="ipt mono" required /></label><label>姿态 Rx, Ry, Rz（°）<input v-model="form.rpy" class="ipt mono" required /></label></template>
          <template v-else><label>取料 X, Y, Z, Rx, Ry, Rz<input v-model="form.pick" class="ipt mono" required /></label><label>放料 X, Y, Z, Rx, Ry, Rz<input v-model="form.place" class="ipt mono" required /></label></template>
          <div class="grid2"><label v-for="key in numericFields" :key="key">{{ fieldLabels[key] }}<input v-model="form[key]" type="number" step="any" class="ipt" required /></label></div>
          <button class="btn btn--primary" type="submit">{{ busy ? '生成中…' : '生成草稿与预览' }}</button>
        </fieldset>
      </form>
      <div class="panel studio-body col"><h3>草稿预览</h3><p v-if="!draft" class="dim">填写参数后生成草稿。</p><template v-else><div class="row"><strong>{{ draft.name }}</strong><span class="tag">{{ draft.kind || '录制' }} · {{ points.length }} 点</span></div><p class="dim tiny">路径显示在主场景；关节录制需先检查。</p><pre class="program mono">{{ draft.program || JSON.stringify(points.slice(0, 12), null, 2) }}</pre><div class="row"><button class="btn" :disabled="busy" @click="preview()">显示路径</button><button class="btn" @click="exportDraft">导出草稿</button><button class="btn btn--primary" :disabled="busy || !points.length" @click="inspect">检查此草稿</button></div></template></div>
    </section>

    <section v-else class="panel studio-body col" id="studio-panel-acceptance" role="tabpanel" aria-labelledby="studio-tab-acceptance" tabindex="0">
      <div class="row"><h3 class="grow">任务验收</h3><span v-if="draft" class="tag">{{ draft.name }} · {{ points.length }} 点</span></div>
      <p v-if="!draft" class="dim">先打开录制或生成工艺草稿，再提交检查。</p>
      <label v-if="draft" class="row">检查 / 执行速度 %（1–100）<input class="ipt short" type="number" min="1" max="100" :value="draft.speed ?? 10" :disabled="busy" @change="draft = { ...draft, speed: Number($event.target.value), program: '' }; invalidate()" /></label>
      <button class="btn" :disabled="busy || !points.length" @click="inspect">{{ busy ? '处理中…' : '重新检查当前轨迹' }}</button>
      <template v-if="inspection">
        <p :class="inspection.report?.risk_detected === false ? 'ok' : 'bad'">{{ inspection.report?.risk_detected === false ? '本次检查未检出风险' : '存在风险或报告不完整，禁止执行' }}</p>
        <pre class="report mono">{{ JSON.stringify(inspection.report, null, 2) }}</pre>
        <label class="row"><input v-model="confirmed" type="checkbox" :disabled="busy || inspection.report?.risk_detected !== false || w.snap?.mode !== 'sim'" />我已核对当前轨迹，明确执行这一次仿真</label>
        <button class="btn btn--primary" :disabled="!runnable" @click="run">确认执行 · 仅仿真</button>
      </template>
      <p v-else class="dim">检查结果会绑定当前草稿；编辑、切换模式、离页或取消后需重新检查。</p>
      <p v-if="w.snap?.mode !== 'sim'" class="note warn">当前非已知仿真状态，执行已禁用。真机执行未开放。</p>
      <p class="tiny dim">软件检查不能替代现场安全验收；停止请求的返回不代表机器人已停稳。</p>
    </section>
  </div>
</template>

<style scoped>
.task-studio { display: flex; flex-direction: column; gap: var(--s5); min-width: 0; height: 100%; overflow: auto; padding: var(--s5); }
.studio-heading { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s4); padding: var(--s5); }
.studio-heading > div { flex: 1; min-width: 180px; }
.studio-heading h2 { font-size: 18px; }
.studio-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s5); min-height: 0; }
.studio-body { padding: var(--s5); min-width: 0; }
.studio-body h3 { font-size: 14px; }
label:not(.row):not(.point-row) { display: flex; flex-direction: column; gap: var(--s2); color: var(--tx-2); }
fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
.ipt { width: 100%; min-width: 0; }
.short { width: 65px; }
.recording-row { display: flex; justify-content: space-between; gap: var(--s4); text-align: left; white-space: normal; overflow-wrap: anywhere; }
.point-list { max-height: 350px; overflow: auto; border-block: 1px solid var(--line); }
.point-row { display: flex; gap: var(--s4); padding-block: var(--s3); align-items: start; font-size: 11px; border-bottom: 1px solid var(--line); }
.point-row span:last-child { overflow-wrap: anywhere; min-width: 0; }
.program, .report { margin: 0; padding: var(--s4); background: var(--bg-inset); border: 1px solid var(--line); border-radius: var(--r-md); overflow: auto; max-height: 340px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
p { margin: 0; }
</style>
