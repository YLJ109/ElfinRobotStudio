<script setup>
import { ref, computed, watch, defineAsyncComponent } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { PROGRAM_COMMANDS, TOOL_ACTIONS, PYTHON_WORDS, CONTAINER_METHODS } from '../programLanguage'
const ProgramEditor = defineAsyncComponent(() => import('../components/ProgramEditor.vue'))

const w = useWorld()
const { snap, programSrc, programs, points } = storeToRefs(w)
const selectedPoint = ref(null)
const coordinates = computed(() => selectedPoint.value ? [...(selectedPoint.value.tcp?.xyz_mm || []), ...(selectedPoint.value.tcp?.rpy_deg || [])] : [])
const pointAtCurrent = computed(() => snap.value?.joints.known !== false && selectedPoint.value?.joints?.every((v,i) => Math.abs(v - (snap.value?.joints.q[i] ?? Infinity)) < 0.01))
function previewPoint(point) { selectedPoint.value = point; w.setPreview(point.joints.slice()) }
const langMode = ref(localStorage.getItem('huayan.program.language') === 'en' ? 'en' : 'zh')
const languageEntries = [...PROGRAM_COMMANDS, ...TOOL_ACTIONS, ...PYTHON_WORDS, ...CONTAINER_METHODS]
  .filter((item, index, all) => all.findIndex(other => other.en === item.en) === index)
  .sort((a, b) => Math.max(a.zh.length, a.en.length) < Math.max(b.zh.length, b.en.length) ? 1 : -1)
function translateCode(source, from, to) {
  if (!source || from === to) return source
  const replacements = languageEntries.map(item => [from === 'zh' ? item.zh : item.en, to === 'zh' ? item.zh : item.en])
    .filter(([a, b]) => a && a !== b)
  return source.split(/\r?\n/).map(line => {
    const commentAt = line.indexOf('#')
    const code = commentAt < 0 ? line : line.slice(0, commentAt)
    const comment = commentAt < 0 ? '' : line.slice(commentAt)
    const strings = []
    const masked = code.replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, value => { strings.push(value); return `\u0000${strings.length - 1}\u0000` })
    let converted = masked
    for (const [a, b] of replacements) {
      const escaped = a.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      converted = converted.replace(new RegExp(`(^|[^\\w\\u3400-\\u9fff])${escaped}(?=$|[^\\w\\u3400-\\u9fff])`, 'g'), `$1${b}`)
    }
    converted = converted.replace(/\u0000(\d+)\u0000/g, (_, index) => strings[Number(index)])
    return converted + comment
  }).join('\n')
}
function switchLanguage(next) {
  if (next !== langMode.value) {
    programSrc.value = translateCode(programSrc.value, langMode.value, next)
    parsedSource.value = null
    steps.value = []
  }
  langMode.value = next
  localStorage.setItem('huayan.program.language', next)
}
const msg = ref(''), failed = ref(false), busy = ref(false), steps = ref([])
const libName = ref(''), selectedName = ref(''), parameters = ref('')
const parsedSource = ref(null), parsedParameters = ref(null)
const exec = computed(() => snap.value?.exec || {})
const granted = computed(() => !!snap.value?.motion_granted)
const parsed = computed(() => parsedSource.value === programSrc.value && parsedParameters.value === parameters.value && steps.value.length > 0)
const resume = computed(() => snap.value?.resume)
const commentedMotions = computed(() => {
  return programSrc.value.split(/\r?\n/).map((text, index) => ({ text, index }))
    .filter(({ text }) => /^\s*#\s*(?:关节移动|关节运动|直线移动|直线运动|MOVEJ|MOVEL)\s*\(/i.test(text))
})
const activeMotionTargets = computed(() => {
  const out = []
  for (const [index, raw] of programSrc.value.split(/\r?\n/).entries()) {
    const text = raw.replace(/#.*$/, '')
    const match = text.match(/(?:关节移动|关节运动|直线移动|直线运动|MOVEJ|MOVEL)\s*\(\s*["']?([\w\u3400-\u9fff.-]+)["']?\s*\)/i)
    if (match) {
      const point = points.value.find(p => p.code === match[1] || p.name === match[1])
      if (point) out.push({ point, line: index + 1 })
    }
  }
  return out
})
const noVisibleMove = computed(() => {
  const q = snap.value?.joints?.q
  const tcp = snap.value?.tcp
  if (!parsed.value || !activeMotionTargets.value.length) return false
  return activeMotionTargets.value.every(({ point }) => {
    const sameJoints = q?.length === 6 && point.joints?.every((v, i) => Math.abs(v - q[i]) < 0.05)
    const target = [...(point.tcp?.xyz_mm || []), ...(point.tcp?.rpy_deg || [])]
    const current = Array.isArray(tcp) ? tcp : [...(tcp?.xyz_mm || []), ...(tcp?.rpy_deg || [])]
    const sameTcp = target.length === 6 && current.length === 6 && target.every((v, i) => Math.abs(v - current[i]) < (i < 3 ? 0.5 : 0.2))
    return sameJoints || sameTcp
  })
})
const stateLabel = computed(() => ({ finished: '已完成', running: '执行中', paused: '已暂停', aborted: '已中止', waiting: '等待中' }[exec.value.state] || '空闲'))
watch([programSrc, parameters, points], () => { parsedSource.value = null; msg.value = ''; selectedPoint.value = points.value.find(p => p.id === selectedPoint.value?.id) || null }, { deep: true })
async function task(fn) {
  if (busy.value) return
  busy.value = true; failed.value = false
  try { await fn() } catch (e) { failed.value = true; msg.value = e.message }
  finally { busy.value = false }
}
async function load() {
  await task(async () => {
    const source = programSrc.value, rawParams = parameters.value
    const params = {}
    for (const row of rawParams.split(/\n|;/).map(s => s.trim()).filter(Boolean)) {
      const index = row.indexOf('=')
      if (index < 1 || !row.slice(index + 1).trim()) throw new Error('参数格式：名称=值')
      params[row.slice(0, index).trim()] = row.slice(index + 1).trim()
    }
    const r = await w.loadProgram(source, params)
    if (programSrc.value !== source || parameters.value !== rawParams) { msg.value = '代码已修改，请重新解析'; return }
    failed.value = !r.ok; steps.value = r.ok ? r.steps : []
    parsedSource.value = r.ok ? source : null; parsedParameters.value = r.ok ? rawParams : null
    msg.value = r.ok ? (steps.value[0]?.kind === 'python' ? '语法检查通过 · 步数由运行时控制流决定' : `解析成功 · ${steps.value.length} 步`) : (r.error || r.why || '解析失败')
  })
}
async function save() {
  await task(async () => {
    const name = libName.value.trim(), r = await w.saveProgram(name, programSrc.value)
    failed.value = !r.ok
    if (r.ok) { libName.value = name; selectedName.value = name }
    msg.value = r.ok ? '已保存：' + name : r.why || '保存失败'
  })
}
function newProgram() {
  programSrc.value = langMode.value === 'zh' ? '速度(15)\n' : 'SPEED(15)\n'
  libName.value = ''; selectedName.value = ''; steps.value = []; parsedSource.value = null
}
async function openProgram() {
  await task(async () => {
    const r = await w.openProgram(selectedName.value)
    failed.value = !r.ok
    if (r.ok) libName.value = selectedName.value
    msg.value = r.ok ? '已载入：' + selectedName.value : r.why || '载入失败'
  })
}
async function deleteProgram() {
  if (!selectedName.value || !window.confirm('删除程序「' + selectedName.value + '」？')) return
  await task(async () => {
    const r = await w.deleteProgram(selectedName.value)
    failed.value = !r.ok; msg.value = r.ok ? '程序已删除' : r.why || '删除失败'
    if (r.ok) selectedName.value = ''
  })
}
async function doResume() {
  const r = await w.call('/api/program/resume_last', {})
  failed.value = !r.ok; msg.value = r.ok ? `从第 ${r.from + 1}/${r.total} 步续跑` : r.why || '续跑失败'
}
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
        <button class="help-link" title="编程规则" @click="w.view = 'about'">规则</button>
        <span class="tiny dim">{{ stateLabel }}</span>
      </div>
      <div class="panel-bd fill">
        <ProgramEditor v-model="programSrc" :language="langMode" :points="points" @preview-point="previewPoint" @select-point="selectedPoint = $event" />
        <div v-if="selectedPoint" class="point-detail">
          <div><b>{{ selectedPoint.code }} · {{ selectedPoint.name }}</b><span>{{ selectedPoint.source === 'real' ? '真机保存' : '仿真保存' }}</span><button class="btn btn--xs" @click="previewPoint(selectedPoint)">影</button><button class="btn btn--xs" @click="w.cancelPreview(); selectedPoint = null">收起</button></div>
          <div class="point-values"><span v-for="(value, i) in selectedPoint.joints" :key="i">J{{ i + 1 }} {{ Number(value).toFixed(3) }}°</span></div>
          <div class="point-values"><span v-for="(value, i) in coordinates" :key="i">{{ ['X','Y','Z','A','B','C'][i] }} {{ Number(value).toFixed(3) }}{{ i < 3 ? 'mm' : '°' }}</span></div>
          <small>TCP：{{ selectedPoint.tcp_source === 'controller' ? '控制器测量' : '由保存关节角正解' }}</small>
          <small v-if="pointAtCurrent"> · 此点与当前姿态一致，残影重合</small>
        </div>
        <label class="parameters">参数<input class="input" v-model="parameters" placeholder="speed=12; point=P1" aria-label="任务参数" /></label>
        <div class="row run">
          <button class="btn" :disabled="busy" @click="load">解析</button>
          <button class="btn btn--primary" :disabled="busy || !granted || !parsed" @click="w.startProgram()">启动</button>
          <button class="btn" :disabled="exec.state !== 'paused'" @click="w.resume()">继续</button>
          <button class="btn btn--danger" @click="w.stopExec()">停止</button>
          <span class="mono tiny dim">{{ exec.dynamic ? '已执行 ' + (exec.completed_index || 0) + ' 步' : Math.max(0, exec.completed_index || 0) + '/' + (exec.total || 0) }}</span>
        </div>
        <div class="library-save">
          <input class="input" v-model="libName" placeholder="程序名称" aria-label="程序名称" @keyup.enter="save" />
          <button class="btn btn--xs" :disabled="busy || !libName.trim()" @click="save">保存</button>
          <button class="btn btn--xs" :disabled="busy" @click="newProgram">新建</button>
        </div>
        <div class="library-load">
          <select class="select" v-model="selectedName" :disabled="!programs.length" aria-label="已保存程序">
            <option value="">{{ programs.length ? '选择程序' : '暂无保存的程序' }}</option>
            <option v-for="p in programs" :key="p.name" :value="p.name">{{ p.name }}</option>
          </select>
          <button class="btn btn--xs" :disabled="busy || !selectedName" @click="openProgram">载入</button>
          <button class="btn btn--xs btn--danger" :disabled="busy || !selectedName" @click="deleteProgram">删除</button>
        </div>
        <div v-if="msg" class="note" :class="{ bad: failed }" role="status">{{ msg }}</div>
        <div v-if="exec.error" class="note bad" role="alert">{{ exec.error }}</div>
        <div v-if="commentedMotions.length" class="note warn">第 {{ commentedMotions.map(x => x.index + 1).join('、') }} 行的运动指令前有 #，当前不会执行；删除 # 后重新解析。</div>
        <div v-if="noVisibleMove" class="note warn">当前程序里的运动目标已经在当前位置，再次启动不会产生可见位移。请换一个点，或让程序先移动到另一个点再返回。</div>
        <div v-if="resume" class="note warn">
          上次进度 {{ resume.i }}/{{ resume.total }}
          <button class="btn btn--xs" :disabled="!granted || resume.dynamic" @click="doResume">续跑</button>
          <span v-if="resume.dynamic">含变量脚本请重新载入</span>
          <button v-if="resume.dynamic && resume.source" class="btn btn--xs" @click="programSrc = resume.source">取回代码</button>
          <button class="btn btn--xs" @click="w.call('/api/program/forget_resume', {})">丢弃</button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.prog, .program-panel { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.program-panel { clip-path: none; border-radius: 6px; }
.panel-hd.no-fold { cursor: default; }
.panel-hd.no-fold::after { display: none; }
.panel-bd.fill { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 6px; overflow: auto; }
.language-switch { display: flex; gap: 2px; border: 1px solid var(--line-2); border-radius: 5px; padding: 2px; }
.language-switch button { border: none; border-radius: 3px; padding: 2px 6px; background: transparent; color: var(--tx-3); font: inherit; font-size: 11px; cursor: pointer; }
.language-switch button.on { background: var(--acc); color: var(--bg-void); }
.help-link { color: var(--acc); border: none; background: transparent; font: inherit; font-size: 11px; cursor: pointer; }
.parameters { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 6px; font-size: 12px; }
.library-save, .library-load { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 4px; flex: none; }
.run { flex: none; gap: 5px; }
.point-detail { flex: none; padding: 7px; border: 1px solid var(--line-2); border-radius: 4px; font-size: 11px; }
.point-detail > div:first-child { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.point-detail b { color: var(--acc); overflow-wrap: anywhere; flex: 1; }
.point-detail small, .point-detail > div > span { color: var(--tx-3); }
.point-values { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px; padding-top: 6px; font: 10px var(--font-mono); }
</style>
