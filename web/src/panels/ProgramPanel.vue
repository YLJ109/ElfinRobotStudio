<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, defineAsyncComponent } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { PROGRAM_COMMANDS, TOOL_ACTIONS, PYTHON_WORDS, CONTAINER_METHODS } from '../programLanguage'
import { parseResponse } from '../connectionState'
import { createProgramDebug, parseBreakpoints, canPollDebug } from '../programDebug'
const ProgramEditor = defineAsyncComponent(() => import('../components/ProgramEditor.vue'))
const ProgramRules = defineAsyncComponent(() => import('../components/ProgramRules.vue'))

const w = useWorld()
const { snap, programSrc, programs, points } = storeToRefs(w)
const selectedPoint = ref(null)
const programTab = ref('editor'), breakpointText = ref(''), debugState = ref(null), debugError = ref('')
const debugSource = ref(null)
const debugAllowed = computed(() => snap.value?.mode === 'sim' && !snap.value?.real)
const debuggerClient = createProgramDebug({ allowed: () => debugAllowed.value })
let debugTimer, debugReading = false
const debugVisible = ref(!document.hidden)
const pollDebug = computed(() => canPollDebug(programTab.value,debugVisible.value,debugAllowed.value))
const debugVisibility = () => { debugVisible.value=!document.hidden }
onMounted(() => document.addEventListener('visibilitychange',debugVisibility))
async function refreshDebug() {
  if (disposed || !pollDebug.value || debugReading) return
  debugReading = true
  try {
    const result = await debuggerClient.status()
    if (disposed || !pollDebug.value) return
    if (result.session !== debugState.value?.session) debugSource.value = null
    debugState.value = result; debugError.value = ''
  }
  catch (error) { if (!disposed) debugError.value = error.message }
  finally { debugReading = false }
}
async function debugAction(action) {
  if (executionBusy.value || debugReading) return
  executionBusy.value = '调试'
  try {
    debugError.value = ''
    if (action === 'load') {
      const source = programSrc.value
      if (!await validateSource() || source !== programSrc.value || !executable.value) return
      debugState.value = await debuggerClient.load(source, parseBreakpoints(breakpointText.value))
      debugSource.value = source
    } else if (action === 'breakpoints') {
      debugState.value = await debuggerClient.breakpoints(parseBreakpoints(breakpointText.value))
    } else debugState.value = await debuggerClient.control(action)
  } catch (error) { debugError.value = error.message }
  finally { executionBusy.value = '' }
}
watch([programTab, debugAllowed, debugVisible], () => {
  clearInterval(debugTimer)
  if (!debugAllowed.value) { debugState.value = null; debugSource.value = null }
  if (pollDebug.value) {
    void refreshDebug()
    debugTimer = setInterval(() => { if (!executionBusy.value) void refreshDebug() }, 500)
  }
})
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
const diagnostics = ref([]), validation = ref('checking'), checkedSource = ref(null), executable = ref(false)
const exec = computed(() => snap.value?.exec || {})
const granted = computed(() => !!snap.value?.motion_granted)
const reportedState = ref(null)
const executionState = computed(() => reportedState.value || exec.value.state || 'idle')
const active = computed(() => ['running', 'waiting', 'paused'].includes(executionState.value))
const paused = computed(() => executionState.value === 'paused')
const canRun = computed(() => !busy.value && !executionBusy.value && granted.value && validation.value === 'valid' && checkedSource.value === programSrc.value && executable.value)
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
  await task(active.value ? '中止' : '运行', async () => {
    if (active.value) {
      const version = executionRevision
      requireOk(await programAction('stop'), '中止被拒绝')
      if (executionRevision === version) reportedState.value = 'aborted'
      return
    }
    const source = programSrc.value
    if (!await validateSource() || !executable.value || source !== programSrc.value || !granted.value) return
    requireOk(await w.loadProgram(source), '程序加载失败')
    if (source !== programSrc.value || checkedSource.value !== source) { record('代码已修改，本次运行已取消'); return }
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
  await task('保存', async () => {
    const name = libName.value.trim(), r = await w.saveProgram(name, programSrc.value)
    requireOk(r, '保存失败')
    libName.value = name; selectedName.value = name
  })
}
function newProgram() {
  programSrc.value = langMode.value === 'zh' ? '速度(15)\n' : 'SPEED(15)\n'
  libName.value = ''; selectedName.value = ''; selectedPoint.value = null
}
async function openProgram() {
  await task('载入', async () => {
    const name = selectedName.value
    requireOk(await w.openProgram(name), '载入失败')
    libName.value = name
    record('已载入程序 ' + name)
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
onBeforeUnmount(() => { disposed = true; document.removeEventListener('visibilitychange',debugVisibility); clearInterval(debugTimer); clearTimeout(validationTimer); validationRequest?.abort(); ++revision })
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
        <button class="help-link" title="ElfinScript 编程规则" @click="programTab = 'rules'">语法</button>
        <span class="tiny dim">{{ stateLabel }}</span>
      </div>
      <div class="panel-bd fill">
        <div class="program-tabs" role="tablist" aria-label="程序分类">
          <button v-for="tab in [['editor','编程'],['debug','仿真调试'],['library','程序库'],['rules','语法']]" :id="'program-tab-'+tab[0]" :key="tab[0]" type="button" role="tab" :aria-selected="programTab===tab[0]" :aria-controls="'program-pane-'+tab[0]" :tabindex="programTab===tab[0]?0:-1" :class="{on:programTab===tab[0]}" @click="programTab=tab[0]">{{ tab[1] }}</button>
        </div>
        <div id="program-pane-editor" v-show="programTab==='editor'" class="program-pane" role="tabpanel" aria-labelledby="program-tab-editor">
        <ProgramEditor v-model="programSrc" :language="langMode" :points="points" :diagnostics="diagnostics" @preview-point="previewPoint" @select-point="selectedPoint = $event" />
        <div v-if="selectedPoint" class="point-detail">
          <div><b>{{ selectedPoint.code }} · {{ selectedPoint.name }}</b><span>{{ selectedPoint.source === 'real' ? '真机保存' : '仿真保存' }}</span><button class="btn btn--xs" @click="previewPoint(selectedPoint)">影</button><button class="btn btn--xs" @click="w.cancelPreview(); selectedPoint = null">收起</button></div>
          <div class="point-values"><span v-for="(value, i) in selectedPoint.joints" :key="i">J{{ i + 1 }} {{ Number(value).toFixed(3) }}°</span></div>
          <div class="point-values"><span v-for="(value, i) in coordinates" :key="i">{{ ['X','Y','Z','A','B','C'][i] }} {{ Number(value).toFixed(3) }}{{ i < 3 ? 'mm' : '°' }}</span></div>
          <small>TCP：{{ selectedPoint.tcp_source === 'controller' ? '控制器测量' : '由保存关节角正解' }}</small>
        </div>
        <div class="row run">
          <button class="btn" :class="active ? 'btn--danger' : 'btn--primary'" :disabled="active ? !!executionBusy : !canRun" @click="toggleRun">{{ active ? '中止' : '运行' }}</button>
          <button class="btn" :disabled="!!executionBusy || !active || (debugState?.enabled && ['running','waiting','paused'].includes(debugState?.state)) || (paused && !granted)" @click="togglePause">{{ paused ? '继续' : '暂停' }}</button>
          <span class="mono tiny dim progress">{{ exec.dynamic ? '已执行 ' + (exec.completed_index || 0) + ' 步' : Math.max(0, exec.completed_index || 0) + '/' + (exec.total || 0) }}</span>
        </div>
        </div>
        <div id="program-pane-debug" v-show="programTab==='debug'" class="program-pane debug-pane" role="tabpanel" aria-labelledby="program-tab-debug">
          <p v-if="!debugAllowed" class="tiny">仅限仿真且未连接真机。请手动切换模式并断开连接。</p>
          <template v-else>
            <p class="tiny dim">显式载入后停在入口。单步进入语句；运动与 WAIT 完成后暂停，下一条尚未下发。断点在语句执行前生效；复合语句会进入其内部语句。</p>
            <div class="row run">
              <button class="btn" :disabled="!!executionBusy || debugReading || active || !granted || validation!=='valid'" @click="debugAction('load')">载入调试</button>
              <button class="btn" :disabled="!!executionBusy || debugReading" @click="refreshDebug">刷新</button>
            </div>
            <label class="tiny">断点行号（逗号分隔）<input class="input" v-model="breakpointText" placeholder="3, 8, 12" /></label>
            <button class="btn btn--xs" :disabled="!!executionBusy || debugReading || !debugState?.enabled" @click="debugAction('breakpoints')">应用断点</button>
            <div class="row run">
              <button class="btn btn--primary" :disabled="!!executionBusy || debugReading || debugState?.state!=='paused' || debugSource!==programSrc" @click="debugAction('step')">单步</button>
              <button class="btn" :disabled="!!executionBusy || debugReading || debugState?.state!=='paused' || debugSource!==programSrc" @click="debugAction('continue')">继续</button>
              <button class="btn" :disabled="!!executionBusy || debugReading || !['running','waiting'].includes(debugState?.state)" @click="debugAction('pause')">完成本步后暂停</button>
              <button class="btn btn--danger" :disabled="!!executionBusy || debugReading || !debugState?.enabled || !['running','waiting','paused'].includes(debugState?.state)" @click="debugAction('stop')">中止</button>
            </div>
            <p v-if="debugSource && debugSource!==programSrc" class="tiny">编辑内容已变化，请中止后重新载入调试。</p>
            <p v-if="debugState?.enabled && debugSource===null" class="tiny">当前会话与本页编辑内容尚未匹配，请中止后重新载入调试。</p>
            <p class="tiny" aria-live="polite">{{ debugState?.state || '未载入' }} · 行 {{ debugState?.line || '—' }} · 已完成 {{ debugState?.completed_index || 0 }} 条指令 · {{ debugState?.reason || '' }}</p>
            <p v-if="debugError" role="alert" class="tiny">{{ debugError }}</p>
            <p class="tiny dim">{{ debugState?.variable_note || '变量仅在载入调试后可观察。刷新页面后，请中止旧会话并重新载入以匹配编辑内容。' }}</p>
            <table v-if="debugState?.variables?.length" class="debug-vars"><thead><tr><th>变量</th><th>值</th><th>作用域</th></tr></thead><tbody><tr v-for="item in debugState.variables" :key="item.name"><td>{{ item.name }}</td><td><pre>{{ JSON.stringify(item.value) }}</pre></td><td>{{ item.scope }}</td></tr></tbody></table>
            <p v-else class="tiny dim">当前无可观察变量。</p>
          </template>
        </div>
        <div id="program-pane-library" v-show="programTab==='library'" class="program-pane" role="tabpanel" aria-labelledby="program-tab-library">
        <div class="library-save">
          <input class="input" v-model="libName" placeholder="程序名称" aria-label="程序名称" @keyup.enter="save" />
          <button class="btn btn--xs" :disabled="busy || !libName.trim()" @click="save">保存</button>
          <button class="btn btn--xs" :disabled="busy" @click="newProgram">新建</button>
        </div>
        <div v-if="programTab==='rules'" id="program-pane-rules" class="program-pane rules-pane" role="tabpanel" aria-labelledby="program-tab-rules">
          <p class="tiny dim">ElfinScript：本项目机器人脚本，支持 Python 风格与中文别名，不是完整 Python 或厂商示教器语言。</p>
          <ProgramRules />
        </div>
        <div class="library-load">
          <select class="select" v-model="selectedName" :disabled="!programs.length" aria-label="已保存程序">
            <option value="">{{ programs.length ? '选择程序' : '暂无保存的程序' }}</option>
            <option v-for="p in programs" :key="p.name" :value="p.name">{{ p.name }}</option>
          </select>
          <button class="btn btn--xs" :disabled="busy || !selectedName" @click="openProgram">载入</button>
          <button class="btn btn--xs btn--danger" :disabled="busy || !selectedName" @click="deleteProgram">删除</button>
        </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.prog, .program-panel { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.program-pane { display:flex; flex-direction:column; gap:6px; flex:1; min-height:0; }
.debug-pane { overflow:auto; }
.rules-pane { overflow:auto; }
.debug-vars { width:100%; font-size:11px; text-align:left; border-collapse:collapse; }
.debug-vars td, .debug-vars th { border-bottom:1px solid var(--line-2); padding:5px; vertical-align:top; }
.debug-vars pre { white-space:pre-wrap; overflow-wrap:anywhere; margin:0; max-width:260px; }
.program-panel { clip-path: none; }
.panel-hd.no-fold { cursor: default; }
.panel-hd.no-fold::after { display: none; }
.panel-bd.fill { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 6px; overflow: auto; }
.language-switch { display: flex; gap: 2px; border: 1px solid var(--line-2); border-radius: 5px; padding: 2px; }
.language-switch button { border: none; border-radius: 3px; padding: 2px 6px; background: transparent; color: var(--tx-3); font: inherit; font-size: 11px; cursor: pointer; }
.language-switch button.on { background: var(--acc); color: var(--bg-void); }
.help-link { color: var(--acc); border: none; background: transparent; font: inherit; font-size: 11px; cursor: pointer; }
.library-save, .library-load { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 4px; flex: none; }
.run { flex: none; display: flex; align-items: center; gap: 6px; padding-top: 2px; }
.run .btn { min-width: 64px; }
.progress { margin-left: auto; text-align: right; }
.point-detail { flex: none; padding: 7px; border: 1px solid var(--line-2); border-radius: 4px; font-size: 11px; }
.point-detail > div:first-child { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.point-detail b { color: var(--acc); overflow-wrap: anywhere; flex: 1; }
.point-detail small, .point-detail > div > span { color: var(--tx-3); }
.point-values { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px; padding-top: 6px; font: 10px var(--font-mono); }
</style>
