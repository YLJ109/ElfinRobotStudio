<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, defineAsyncComponent } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { PROGRAM_COMMANDS, TOOL_ACTIONS, PYTHON_WORDS, CONTAINER_METHODS } from '../programLanguage'
import { parseResponse } from '../connectionState'
const ProgramEditor = defineAsyncComponent(() => import('../components/ProgramEditor.vue'))
const ProgramRules = defineAsyncComponent(() => import('../components/ProgramRules.vue'))

const w = useWorld()
const { snap, programSrc, programs, points } = storeToRefs(w)
const selectedPoint = ref(null)
const programTab = ref('editor')
const coordinates = computed(() => selectedPoint.value ? [...(selectedPoint.value.tcp?.xyz_mm || []), ...(selectedPoint.value.tcp?.rpy_deg || [])] : [])
function previewPoint(point) { selectedPoint.value = point; w.setPreview(point.joints.slice()) }
const langMode = ref(localStorage.getItem('huayan.program.language') === 'en' ? 'en' : 'zh')
const languageEntries = [...PROGRAM_COMMANDS, ...TOOL_ACTIONS, ...PYTHON_WORDS, ...CONTAINER_METHODS]
  .filter((item, index, all) => all.findIndex(other => other.en === item.en) === index)
  .sort((a, b) => Math.max(a.zh.length, a.en.length) < Math.max(b.zh.length, b.en.length) ? 1 : -1)
function translateCode(source, from, to) {
  if (!source || from === to) return source
  const replacements = new Map(languageEntries.map(item => [from === 'zh' ? item.zh : item.en, to === 'zh' ? item.zh : item.en]))
  // Strings and comments are opaque, including '#' inside strings and multiline literals.
  return source.replace(/"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|#[^\n]*|[\p{L}_][\p{L}\p{N}_]*/gu, (token, offset) => {
    if (/^["'#]/.test(token)) return token
    const method = CONTAINER_METHODS.find(item => token === (from === 'zh' ? item.zh : item.en))
    if (method && !/\.\s*$/.test(source.slice(0, offset))) return token
    if (from === 'zh' && token === '循环') {
      const rest = source.slice(offset + token.length).split('\n', 1)[0]
      return /^\s+\d+\s*(?:#.*)?$/.test(rest) ? 'LOOP' : 'for'
    }
    return replacements.get(token) || token
  })
}
function switchLanguage(next) {
  if (next !== langMode.value) {
    programSrc.value = translateCode(programSrc.value, langMode.value, next)
  }
  langMode.value = next
  localStorage.setItem('huayan.program.language', next)
}
const busy = ref(''), executionBusy = ref('')
const libName = ref(''), selectedName = ref('')
const savedProgram = ref({name:'',source:''}), fileError = ref('')
const dirty = computed(() => !!programSrc.value.trim() && (programSrc.value!==savedProgram.value.source || libName.value.trim()!==savedProgram.value.name))
function confirmDiscard(){return !dirty.value || window.confirm('当前程序尚未保存，放弃修改？')}
function protectDraft(event){if(dirty.value){event.preventDefault();event.returnValue=''}}
onMounted(()=>window.addEventListener('beforeunload',protectDraft))
const librarySource = ref(''), libraryError = ref(''), libraryReading = ref(false)
watch([selectedName, programTab, () => programs.value.find(p => p.name === selectedName.value)?.updated], async ([name, tab], _, cleanup) => {
  librarySource.value = ''; libraryError.value = ''; libraryReading.value = false
  if (!name || tab !== 'library') return
  const controller = new AbortController()
  cleanup(() => controller.abort())
  libraryReading.value = true
  try {
    const response = await fetch('/api/programs/' + encodeURIComponent(name), { signal: controller.signal })
    const result = await parseResponse(response, '/api/programs/' + name)
    if (controller.signal.aborted) return
    if (!result.ok || typeof result.src !== 'string') throw new Error(result.why || '程序读取失败')
    librarySource.value = result.src
  } catch (error) {
    if (!controller.signal.aborted) libraryError.value = error.message
  } finally {
    if (!controller.signal.aborted) libraryReading.value = false
  }
})
const diagnostics = ref([]), validation = ref('checking'), checkedSource = ref(null), executable = ref(false)
const exec = computed(() => snap.value?.exec || {})
const startLatency = computed(() => (w.startLatency?.label === 'program' ? w.startLatency : null))
const granted = computed(() => !!snap.value?.motion_granted)
const reportedState = ref(null)
const executionState = computed(() => reportedState.value || exec.value.state || 'idle')
const active = computed(() => ['running', 'waiting', 'paused'].includes(executionState.value))
const paused = computed(() => executionState.value === 'paused')
const simulation = computed(() => snap.value?.mode === 'sim' && !snap.value?.real)
// SIM03 只放宽仿真：仿真一次点击就排程，语法/可执行检查在 toggleRun 里同步串在
// 下发前面跑完。真机保留「先校验通过才可执行」的硬件门槛，不在这里取消。
const canRun = computed(() => !busy.value && !executionBusy.value && granted.value && (simulation.value ? !!programSrc.value.trim() : validation.value === 'valid' && checkedSource.value === programSrc.value && executable.value))
const stateLabel = computed(() => ({ finished: '已完成', running: '执行中', paused: '已暂停', aborted: '已中止', waiting: '等待中', error: '执行失败' }[executionState.value] || '空闲'))
let validationTimer, validationRequest, revision = 0, executionRevision = 0, logId = 0, disposed = false, lastValidationLog = ''
function record(text) {
  // UI-only operations share the existing execution log without writing user data.
  w.logs = [...w.logs, { seq: `program-ui-${Date.now()}-${++logId}`, t: Date.now() / 1000, kind: 'program', text }].slice(-2000)
}
async function validateSource() {
  clearTimeout(validationTimer)
  validationRequest?.abort()
  const request = new AbortController(), version = ++revision, source = programSrc.value
  validationRequest = request
  validation.value = 'checking'; checkedSource.value = null; executable.value = false
  try {
    const response = await fetch('/api/program/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ src: source }), signal: request.signal })
    const result = await parseResponse(response, '/api/program/validate')
    if (disposed || version !== revision || source !== programSrc.value) return false
    if (!Array.isArray(result.diagnostics)) throw new Error(result.why || result.error || '校验响应不完整')
    diagnostics.value = result.diagnostics
    validation.value = result.ok && !result.diagnostics.length ? 'valid' : 'invalid'
    checkedSource.value = source; executable.value = !!result.executable
    const detail = result.diagnostics.map(item => `第 ${item.line} 行：${item.message}`).join('；')
    if (detail && detail !== lastValidationLog) record('语法检查：' + detail)
    lastValidationLog = detail
    return validation.value === 'valid'
  } catch (error) {
    if (disposed || version !== revision || request.signal.aborted) return false
    validation.value = 'unavailable'; diagnostics.value = []
    const detail = '语法检查失败：' + error.message
    if (detail !== lastValidationLog) record(detail)
    lastValidationLog = detail
    return false
  } finally {
    if (validationRequest === request) validationRequest = null
  }
}
watch([programSrc, points], () => {
  clearTimeout(validationTimer); validationRequest?.abort(); ++revision
  validation.value = 'checking'; checkedSource.value = null; executable.value = false; diagnostics.value = []
  selectedPoint.value = points.value.find(point => point.id === selectedPoint.value?.id) || null
  validationTimer = setTimeout(validateSource, 300)
}, { deep: true, immediate: true, flush: 'sync' })
watch(() => snap.value?.exec?.state, () => { ++executionRevision; reportedState.value = null }, { flush: 'sync' })
watch(() => exec.value.error, (error, previous) => { if (error && error !== previous) record('执行结果：' + error) })
async function task(label, fn, pending = busy) {
  if (pending.value) return
  pending.value = label
  try { await fn() } catch (error) { record(`${label}失败：${error.message}`) }
  finally { pending.value = '' }
}
function requireOk(result, fallback) {
  if (!result?.ok) throw new Error(result?.why || result?.error || fallback)
  return result
}
async function programAction(action) {
  const path = action==='stop'?'/api/motion/cancel':'/api/program/' + action
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  return parseResponse(response, path)
}
async function toggleRun() {
  await task(active.value ? '中止' : '排程', async () => {
    if (active.value) {
      const version = executionRevision
      requireOk(await programAction('stop'), '中止被拒绝')
      if (executionRevision === version) reportedState.value = 'aborted'
      return
    }
    const source = programSrc.value, mode = snap.value?.mode, session = snap.value?.session_id
    const direct = simulation.value
    if (!source.trim() || !granted.value) return
    w.markMotionStart?.('program')
    // 检查在同一次点击里跑完：不通过就不排程，原因直接抛给 task() 显示。
    if (!direct && (checkedSource.value !== source || validation.value !== 'valid') && !await validateSource())
      throw new Error('语法检查未通过，未开始排程')
    if (!direct && !executable.value) throw new Error('程序没有可执行的运动指令，未开始排程')
    if (source !== programSrc.value || mode !== snap.value?.mode || !granted.value) throw new Error('程序或运行环境已变化，本次运行已取消')
    requireOk(await w.loadProgram(source), '程序加载失败')
    if (source !== programSrc.value || mode !== snap.value?.mode || session !== snap.value?.session_id || !granted.value || (!direct && checkedSource.value !== source)) { record('程序或运行环境已变化，本次运行已取消'); return }
    const version = executionRevision
    const result = requireOk(await w.startProgram(), '程序启动被拒绝')
    if (executionRevision === version) reportedState.value = result.state || 'running'
    record('程序开始执行')
  }, executionBusy)
}
async function togglePause() {
  if (!active.value) return
  await task(paused.value ? '继续' : '暂停', async () => {
    const wasPaused = paused.value
    const version = executionRevision
    const result = requireOk(await programAction(wasPaused ? 'resume' : 'pause'), '操作被拒绝')
    if (executionRevision === version) reportedState.value = result.state || (wasPaused ? 'running' : 'paused')
    record(wasPaused ? '程序继续执行' : '程序已暂停')
  }, executionBusy)
}
async function save() {
  if(busy.value)return
  fileError.value=''
  let name=libName.value.trim()
  if(!name){
    const entered=window.prompt('程序名称','')
    if(entered===null)return
    name=entered.trim()
  }
  if(!name){fileError.value='请输入程序名称';return}
  if(Array.from(name).length>40){fileError.value='程序名称最多40字';return}
  if(programs.value.some(item=>item.name===name) && name!==savedProgram.value.name && !window.confirm('覆盖程序「'+name+'」？'))return
  await task('保存', async () => {
    const source=programSrc.value
    try{
      requireOk(await w.saveProgram(name,source),'保存失败')
      savedProgram.value={name,source};libName.value=name;selectedName.value=name
      record('已保存程序 '+name)
    }catch(error){fileError.value=error.message;throw error}
  })
}
function newProgram() {
  if(busy.value || !confirmDiscard())return false
  const speed = (snap.value?.mode==='real'?snap.value?.real_speed_pct:snap.value?.sim_speed_pct) ?? snap.value?.settings?.[snap.value?.mode === 'real' ? 'real_speed_pct' : 'sim_speed_pct'] ?? 100
  programSrc.value = langMode.value === 'zh' ? `速度(${speed})\n` : `SPEED(${speed})\n`
  libName.value = ''; selectedName.value = ''; selectedPoint.value = null
  savedProgram.value={name:'',source:programSrc.value};fileError.value='';programTab.value='editor'
  return true
}
async function openProgram() {
  if(busy.value || !selectedName.value || !confirmDiscard())return
  fileError.value=''
  await task('载入', async () => {
    const name = selectedName.value
    try{requireOk(await w.openProgram(name,{expectedSource:programSrc.value}), '载入失败')}
    catch(error){fileError.value=error.message;throw error}
    libName.value = name;savedProgram.value={name,source:programSrc.value}
    record('已载入程序 ' + name)
    programTab.value = 'editor'
  })
}
async function deleteProgram() {
  if (!selectedName.value || !window.confirm('删除程序「' + selectedName.value + '」？')) return
  await task('删除', async () => {
    const name = selectedName.value
    requireOk(await w.deleteProgram(name), '删除失败')
    selectedName.value = ''
    record('已删除程序 ' + name)
  })
}
onBeforeUnmount(() => { window.removeEventListener('beforeunload',protectDraft); disposed = true; clearTimeout(validationTimer); validationRequest?.abort(); ++revision })
</script>

<template>
  <div class="prog">
    <section class="panel program-panel">
      <div class="panel-hd no-fold">
        <span class="panel-title">程序</span>
        <div class="language-switch" role="group" aria-label="补全语言">
          <button :class="{ on: langMode === 'zh' }" :aria-pressed="langMode === 'zh'" @mousedown.prevent @click="switchLanguage('zh')">中文</button>
          <button :class="{ on: langMode === 'en' }" :aria-pressed="langMode === 'en'" @mousedown.prevent @click="switchLanguage('en')">EN</button>
        </div>
        <span class="grow"></span>
        <span class="tiny dim">{{ stateLabel }}</span>
      </div>
      <div class="panel-bd fill">
        <div class="program-tabs" role="tablist" aria-label="程序分类">
          <button v-for="tab in [['editor','编程'],['library','程序库'],['rules','语法']]" :id="'program-tab-'+tab[0]" :key="tab[0]" type="button" role="tab" :aria-selected="programTab===tab[0]" :aria-controls="'program-pane-'+tab[0]" :tabindex="programTab===tab[0]?0:-1" :class="{on:programTab===tab[0]}" @click="programTab=tab[0]">{{ tab[1] }}</button>
        </div>
        <p v-if="fileError" class="note tiny" role="alert">{{fileError}}</p>
        <div id="program-pane-editor" v-show="programTab==='editor'" class="program-pane" role="tabpanel" aria-labelledby="program-tab-editor">
        <div class="row editor-file-actions"><span class="grow editor-file-name" :title="libName || '未命名'">{{libName || '未命名'}}{{dirty?' · 未保存':''}}</span></div>
        <ProgramEditor v-model="programSrc" :language="langMode" :points="points" :diagnostics="diagnostics" @preview-point="previewPoint" @select-point="selectedPoint = $event" />
        <div v-if="selectedPoint" class="point-detail">
          <div><b>{{ selectedPoint.code }} · {{ selectedPoint.name }}</b><span>{{ selectedPoint.source === 'real' ? '真机保存' : '仿真保存' }}</span><button class="btn btn--xs" @click="previewPoint(selectedPoint)">影</button><button class="btn btn--xs" @click="w.cancelPreview(); selectedPoint = null">收起</button></div>
          <div class="point-values"><span v-for="(value, i) in selectedPoint.joints" :key="i">J{{ i + 1 }} {{ Number(value).toFixed(3) }}°</span></div>
          <div class="point-values"><span v-for="(value, i) in coordinates" :key="i">{{ ['X','Y','Z','A','B','C'][i] }} {{ Number(value).toFixed(3) }}{{ i < 3 ? 'mm' : '°' }}</span></div>
          <small>TCP：{{ selectedPoint.tcp_source === 'controller' ? '控制器测量' : '由保存关节角正解' }}</small>
        </div>
        <div class="row run">
          <button class="btn" :disabled="!!busy" @click="newProgram">新建</button>
          <button class="btn btn--primary" :disabled="!!busy" @click="save">保存</button>
          <button class="btn" :class="active ? 'btn--danger' : 'btn--primary'" :disabled="active ? !!executionBusy : !canRun" @click="toggleRun">{{ active ? '中止' : '运行' }}</button>
          <button class="btn" :disabled="!!executionBusy || !active || (paused && !granted)" @click="togglePause">{{ paused ? '继续' : '暂停' }}</button>
          <span class="mono tiny dim progress">{{ exec.dynamic ? '已执行 ' + (exec.completed_index || 0) + ' 步' : Math.max(0, exec.completed_index || 0) + '/' + (exec.total || 0) }}</span>
        </div>
        <p v-if="validation==='invalid' && diagnostics.length" class="note tiny" role="alert">第 {{diagnostics[0].line}} 行：{{diagnostics[0].message==='invalid syntax'?'语法错误，请检查指令和参数':diagnostics[0].message}}</p>
        <p v-else-if="validation==='unavailable'" class="note tiny" role="alert">暂时无法校验程序，请重试。</p>
        <p v-if="executionBusy" class="tiny dim" role="status" aria-live="polite">正在{{ executionBusy }}…</p>
        <p v-else-if="startLatency" class="tiny dim" role="status">本次点击到首动实测 {{ (startLatency.ms / 1000).toFixed(1) }} 秒</p>
        </div>
        <div id="program-pane-library" v-show="programTab==='library'" class="program-pane" role="tabpanel" aria-labelledby="program-tab-library">
        <div class="library-save">
          <input class="input" v-model="libName" placeholder="程序名称" aria-label="程序名称" @keyup.enter="save" />
          <button class="btn btn--xs" :disabled="busy || !libName.trim()" @click="save">保存</button>
          <button class="btn btn--xs" :disabled="busy" @click="newProgram">新建</button>
        </div>
        <div class="library-load">
          <span class="library-selected" :title="selectedName">{{ selectedName || '选择程序' }}</span>
          <button class="btn btn--xs" :disabled="busy || !selectedName" @click="openProgram">载入</button>
          <button class="btn btn--xs btn--danger" :disabled="busy || !selectedName" @click="deleteProgram">删除</button>
        </div>
        <div class="library-list" role="group" aria-label="已保存程序">
          <button v-for="p in programs" :key="p.name" class="library-item" :class="{on:selectedName===p.name}"
                  :aria-pressed="selectedName===p.name" :disabled="!!busy" @click="selectedName=p.name">
            <span>{{ p.name }}</span><small>{{ p.lines }} 行</small>
          </button>
          <p v-if="!programs.length" class="tiny dim">暂无保存的程序</p>
        </div>
        <div v-if="selectedName" class="library-preview" aria-label="程序预览" :aria-busy="libraryReading">
          <p v-if="libraryReading" class="tiny dim">正在读取…</p>
          <p v-else-if="libraryError" role="alert" class="tiny">{{ libraryError }}</p>
          <pre v-else>{{ librarySource || '空程序' }}</pre>
        </div>
        </div>
        <div v-if="programTab==='rules'" id="program-pane-rules" class="program-pane rules-pane" role="tabpanel" aria-labelledby="program-tab-rules">
          <ProgramRules />
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.prog, .program-panel { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.program-pane { display:flex; flex-direction:column; gap:6px; flex:1; min-height:0; }
.rules-pane { overflow:auto; }
.program-panel { clip-path: none; }
.panel-hd.no-fold { cursor: default; }
.panel-hd.no-fold::after { display: none; }
.panel-bd.fill { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 6px; overflow: auto; }
.language-switch { display: flex; gap: 2px; border: 1px solid var(--line-2); border-radius: 5px; padding: 2px; }
.language-switch button { border: none; border-radius: 3px; padding: 2px 6px; background: transparent; color: var(--tx-3); font: inherit; font-size: 11px; cursor: pointer; }
.language-switch button.on { background: var(--acc); color: var(--bg-void); }
.library-save, .library-load { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 4px; flex: none; }
.library-selected { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; align-self:center; font-size:12px; color:var(--tx-3); }
.library-list { flex:1; min-height:100px; overflow:auto; display:flex; flex-direction:column; gap:4px; }
.library-item { display:flex; align-items:center; justify-content:space-between; gap:8px; flex:none; padding:10px; border:1px solid var(--line-2); background:var(--bg-void); color:var(--tx-2); font:inherit; font-size:12px; text-align:left; cursor:pointer; }
.library-item span { min-width:0; overflow-wrap:anywhere; }
.library-item small { flex:none; color:var(--tx-3); font-size:10px; }
.library-item:hover, .library-item.on { border-color:var(--acc); color:var(--acc); background:var(--bg-panel); }
.library-item:focus-visible { outline:2px solid var(--acc); outline-offset:-2px; }
.library-preview { flex:none; max-height:180px; overflow:auto; border:1px solid var(--line-2); padding:8px; }
.library-preview pre { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; font:11px/1.6 var(--font-mono); }
.editor-file-actions { flex:none;gap:6px;min-width:0; }.editor-file-name{font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
/* 同一行的四个操作按钮统一高度/内边距/字号；主操作只靠颜色区分，不靠加宽。 */
.run { flex: none; display: flex; align-items: center; gap: var(--s3); padding-top: 2px; }
.run .btn { flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center; min-width: 64px; height: var(--btn-h); padding: 0 var(--btn-px); font-size: var(--btn-fs); }
.progress { margin-left: auto; text-align: right; }
.point-detail { flex: none; padding: 7px; border: 1px solid var(--line-2); border-radius: 4px; font-size: 11px; }
.point-detail > div:first-child { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.point-detail b { color: var(--acc); overflow-wrap: anywhere; flex: 1; }
.point-detail small, .point-detail > div > span { color: var(--tx-3); }
.point-values { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px; padding-top: 6px; font: 10px var(--font-mono); }
</style>
