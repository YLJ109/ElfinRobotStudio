<script setup>
// Restored from this session's original AiPanel, retaining the existing store/API path.
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import AiModelConfig from '../components/AiModelConfig.vue'
import { clearAiConfig, isConfigured, maskKey, pushAiConfig, readAiConfig } from '../aiProviderCatalog.js'

const props = defineProps({ active: { type: Boolean, default: true } })
const w = useWorld()
const { aiHistory, aiPlan, snap } = storeToRefs(w)
const text = ref('')
const busy = ref(false)
const executing = ref(false)
const err = ref('')
const notice = ref('')
const confirmed = ref('')
const clarificationMessage=ref(null)
const clarificationHelp='可明确补充待定速度、点位或等待；其余情况请以“完整要求：”重述本次全部动作。'
// 模型由「配置」弹窗选定并存本机；标题栏只负责显示当前型号与是否已配置。
const aiConfig = ref(readAiConfig())
const configOpen = ref(false)
const configured = computed(() => isConfigured(aiConfig.value))
const modelLabel = computed(() => configured.value
  ? `${aiConfig.value.providerName || aiConfig.value.providerId} · ${aiConfig.value.model}` : '未选择模型')
const modelTitle = computed(() => configured.value
  ? `${aiConfig.value.providerName || aiConfig.value.providerId} · ${aiConfig.value.model} · ${aiConfig.value.baseUrl} · Key ${maskKey(aiConfig.value.apiKey)}`
  : '尚未配置模型，点「配置」选择厂商与型号')
// 配置状态由按钮自己说清楚，不再另挂一个「已配置/未配置」标签。
const configLabel = computed(() => configured.value ? '已配置' : '配置')
const configTitle = computed(() => configured.value
  ? '修改厂商、模型与 API Key' : '选择厂商、模型并填写 API Key')
function onConfigChange(config) { aiConfig.value = config }
async function dropConfig() {
  if (typeof window !== 'undefined' && window.confirm
      && !window.confirm('清空已保存的 AI 模型配置（厂商 / 模型 / API Key / Base URL）？')) return
  clearAiConfig()
  await pushAiConfig(null)
  aiConfig.value = null
}
// 启动时把本机配置交给后端；没存过配置就不打招呼，保留后端 .env 里的旧 Key 仍能生效。
onMounted(() => { if (configured.value) void pushAiConfig(aiConfig.value) })
function visibleQuestions(message) {
  const questions=Array.isArray(message?.questions)?message.questions:[]
  return message?.kind==='clarify'?questions.filter(question=>question!==clarificationHelp):questions
}
function sourceLabel(result) {
  if(result?.source_kind==='local_rules')return result.kind==='plan'&&result.status==='ready'?(simulation.value?'本地规则 · 可执行程序':'本地规则 · 待确认草稿'):'本地规则'
  return result?.source_kind==='model'?'模型回答':'未生成回答'
}
const simulation = computed(() => snap.value?.mode === 'sim' && !snap.value?.real)
const granted = computed(() => !!(snap.value && snap.value.motion_granted))
const runnerState = computed(() => snap.value?.exec?.state || 'idle')
const runnerActive = computed(() => ['running', 'waiting', 'paused'].includes(runnerState.value))
const runnerPaused = computed(() => runnerState.value === 'paused')
const controlBusy = ref(false), stopBusy = ref(false)
const executionWhy = computed(() => !aiPlan.value ? '尚无可执行计划' : reason(aiPlan.value) || (!granted.value ? '尚未取得运动许可' : !simulation.value && confirmed.value !== aiPlan.value.message_id ? '请先核对计划' : ''))
async function control(action) {
  const pending = action === 'stop' ? stopBusy : controlBusy
  if (pending.value) return
  pending.value = true; err.value = ''
  try {
    const path = action === 'stop' ? '/api/motion/cancel' : '/api/program/' + action
    const result = await w.call(path, {})
    if (!result?.ok) err.value = result?.why || result?.error || '操作被拒绝'
  } catch (e) { err.value = e.message || '操作失败' }
  finally { pending.value = false }
}
async function toggleRun() {
  if (runnerActive.value) return control('stop')
  if (executing.value){const r=await w.aiDiscard('已停止本次准备。');if(r?.ok===false)err.value=r.error||'停止未确认';return}
  return run(aiPlan.value)
}
async function togglePause() {
  if (!runnerActive.value || (runnerPaused.value && !granted.value)) return
  return control(runnerPaused.value ? 'resume' : 'pause')
}
const stream = ref(null)

const DESC = {
  speed: (o) => `当前模式速度 → ${o.pct}%`,
  movej: (o) => (o.point ? `运动到点位 ${o.point}` : `运动到关节角 [${o.joints.map((v) => v.toFixed(1)).join(', ')}]`),
  dout: (o) => `末端 DO${o.bit + 1} → 电平 ${o.val}（协议 bit=${o.bit}，不代表吸取/释放）`,
  wait: (o) => `等待 ${o.sec}s`,
  tool: (o) => `工具动作 → ${{ vacuum_on: '吸附', vacuum_off: '释放', open: '张开', close: '夹紧', attach: '连接', detach: '脱离' }[o.action] || o.action}`,
  require_tool: (o) => `核对当前工具 → ${o.tool_id}`,
  drawing: (o) => `用笔绘制${({heart:'爱心',circle:'圆圈',square:'正方形',rectangle:'长方形'})[o.shape]||o.shape}`,
}

watch(() => aiHistory.value.length, async () => {
  await nextTick()
  if (stream.value) stream.value.scrollTop = stream.value.scrollHeight
})
watch(() => aiPlan.value?.plan_id, () => { confirmed.value = '' })

async function say() {
  if (!props.active || !text.value.trim() || busy.value) return
  busy.value = true
  err.value = ''; notice.value = ''
  const t = text.value
  text.value = ''
  try {
    const r = await w.aiSay(t,clarificationMessage.value,true)
    clarificationMessage.value=null
    // 原因已写进本轮对话气泡时，不再在下方重复同一句。
    if (!r.ok) {
      const e = r.error || '调用失败', last = aiHistory.value.at(-1)
      const shown = last?.role === 'assistant' && ['failed', 'invalid'].includes(last.result?.status) && last.content === e
      err.value = shown ? '' : e
    }
  } catch (e) {
    err.value = e.message || '请求失败，请重试'
  } finally { busy.value = false }
}
function reason(message) { return w.aiReason(message) }
// 空计划不摆标题和步骤；同一条原因不在气泡与计划框里各说一遍。
function planNote(result, content) {
  if (result?.kind !== 'plan') return ''
  const t = result.reason || (aiPlan.value?.message_id === result.message_id ? reason(result) : '')
  return t && t !== content ? t : ''
}
async function run(message) {
  if (!props.active || busy.value || executing.value || !granted.value
    || runnerActive.value || !message || (!simulation.value && confirmed.value !== message.message_id) || reason(message)) return
  executing.value = true
  err.value = ''
  try {
    const r = await w.aiRun(message)
    if (!r.ok) err.value = r.why || '执行被拒'
  } catch (e) {
    err.value = e.message || '执行请求失败'
  } finally { executing.value = false }
}
async function dropPlan() {
  confirmed.value = ''
  clarificationMessage.value=null
  const r = await w.aiDiscard('已手动丢弃计划。')
  if (r?.ok === false) err.value = r.error || '撤销请求失败'
  else notice.value='本次计划或澄清已取消。'
}
async function clearHistory() { err.value = ''; await dropPlan(); w.aiHistory = []; notice.value = '' }
async function copyCode(message) {
  if (!props.active || !message?.code) return
  try { await navigator.clipboard.writeText(message.code); notice.value = '代码已复制；未执行。' }
  catch { err.value = '复制失败，请直接选择代码复制。' }
}
function loadCode(message) {
  if (!props.active || !message?.code) return
  w.programSrc = message.code
  notice.value = '代码已载入程序编辑器；未加载运行任务，也未执行。'
}
function sendOnEnter(event) {
  if (event.isComposing || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return
  event.preventDefault()
  say()
}
function supplement(message) {
  const why=w.aiClarificationReason(message)
  if(why){err.value=why;return}
  clarificationMessage.value=message;text.value=''
  notice.value=''
}
</script>

<template>
  <div class="ai">
    <header class="ai-header panel no-fold">
      <span class="ai-title">AI 助手</span>
      <span class="ai-tags">
        <span v-if="busy" class="tag tag--info">思考中</span>
      </span>
      <span class="bar-state">
        <button class="btn no-fold" :title="modelTitle + ' · ' + configTitle" @click="configOpen = true">{{ configLabel }}</button>
        <button class="btn btn--danger no-fold" title="删除已保存的厂商/模型/API Key/Base URL" :disabled="!configured" @click="dropConfig">清空</button>
      </span>
    </header>
    <AiModelConfig v-if="configOpen" @close="configOpen = false" @change="onConfigChange" />
    <section ref="stream" class="stream panel no-fold" aria-label="AI 对话">
      <div v-for="(h, i) in aiHistory" :key="i" class="msg" :class="h.role"><div class="bub">
        <span v-if="h.role==='assistant' && h.result?.status!=='thinking'" class="tiny dim ai-source" :data-source-kind="h.result?.source_kind || 'not_generated'">{{sourceLabel(h.result)}}</span>
        <div>{{ h.content }}</div>
        <ul v-if="visibleQuestions(h.result).length"><li v-for="(q, n) in visibleQuestions(h.result)" :key="n">{{ q }}</li></ul>
        <div v-if="h.result?.clarification" class="row clarification-ticket" :data-clarification-id="h.result.clarification.id" :data-revision="h.result.clarification.revision" :data-request-id="h.result.request_id" :data-expires-at="h.result.expires_at">
          <span class="tiny" :title="w.aiClarificationReason(h.result)">{{ w.aiClarificationReason(h.result) ? '已失效' : '待补充' }}</span>
          <button v-if="!w.aiClarificationReason(h.result)" class="btn btn--xs" :title="clarificationHelp" :disabled="busy || executing || !active" @click="supplement(h.result)">补充本次要求</button>
          <button v-if="!w.aiClarificationReason(h.result)" class="btn btn--xs" :disabled="busy || executing" @click="dropPlan">取消澄清</button>
        </div>
        <div v-if="h.role === 'assistant' && h.result?.kind === 'plan' && (h.result.plan?.length || planNote(h.result, h.content))" class="message-plan" :data-plan-id="h.result.plan_id" :data-request-id="h.result.request_id">
          <div v-if="h.result.plan?.length" class="row"><span class="panel-title">{{ simulation ? '程序' : '待确认计划' }} · {{ h.result.plan.length }} 步</span><span class="grow"></span><button v-if="aiPlan?.message_id === h.result.message_id" class="btn btn--xs" :disabled="executing" @click="dropPlan">丢弃</button></div>
          <ol v-if="h.result.plan?.length" class="plist"><li v-for="(o, n) in h.result.plan" :key="n">{{ DESC[o.op](o) }}</li></ol>
          <div v-for="(wn, n) in h.result.warnings" :key="n" class="note warn">{{ wn }}</div>
          <pre v-if="h.result.code" aria-label="已验证计划代码"><code>{{ h.result.code }}</code></pre>
          <div v-if="h.result.code" class="row"><button class="btn btn--xs" :disabled="!active" @click="copyCode(h.result)">复制代码</button><button class="btn btn--xs" :disabled="!active" @click="loadCode(h.result)">载入编辑器</button></div>
          <div v-if="h.result.status === 'ready' && !reason(h.result)">
            <label v-if="!simulation" class="confirm"><input type="checkbox" :checked="confirmed === h.result.message_id" @change="confirmed = $event.target.checked ? h.result.message_id : ''">我已核对本条步骤、IO 接线和现场安全</label>
          </div>
          <div v-else-if="planNote(h.result, h.content)" class="note" role="status">{{ planNote(h.result, h.content) }}</div>
        </div>
      </div></div>
      <div v-if="err" class="note bad" role="alert">{{ err }}</div>
      <div v-if="notice" class="note" role="status">{{ notice }}</div>
    </section>
    <form class="composer panel corner no-fold" @submit.prevent="say">
      <p v-if="clarificationMessage" class="tiny" role="status">补充本次要求<button type="button" class="btn btn--xs" @click="clarificationMessage=null">改为新要求</button></p>
      <textarea class="textarea ipt" rows="2" v-model="text" :disabled="busy" aria-label="AI 指令" placeholder="输入指令，例如：按顺序移动到 P1、P2" @keydown.enter="sendOnEnter"></textarea>
      <div class="crow" aria-label="程序运行控制"><button type="button" class="btn btn--primary" :title="runnerActive||executing?'停止当前任务':executionWhy" :disabled="runnerActive||executing?stopBusy:(!active||busy||controlBusy||!!executionWhy)" @click="toggleRun">{{runnerActive||executing?'停止':'执行'}}</button><button type="button" class="btn" :disabled="controlBusy||!runnerActive||(runnerPaused&&!granted)" @click="togglePause">{{runnerPaused?'继续':'暂停'}}</button><span class="grow"></span><button type="submit" class="btn btn--primary" :disabled="!active||busy||!text.trim()">{{busy?'思考中':'发送'}}</button></div>
    </form>
  </div>
</template>

<style scoped>
.ai { display: flex; flex-direction: column; gap: 6px; flex: 1 1 auto; height: 100%; min-height: 0; overflow: hidden; }
/* 标题栏：标题固定左侧不压缩，模型/状态标签成组，按钮组靠右。
   窄侧栏下整行允许换行——宁可换行也不压字重叠。 */
.ai-header { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s3) var(--s4); flex: 0 0 auto; min-height: 40px; padding: 6px 10px; }
.ai-title { flex: 0 0 auto; color: var(--tx-1); font-family: var(--font-display); font-size: 13px; font-weight: 600; white-space: nowrap; }
.ai-tags { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s3); min-width: 0; }
.model-tag { flex: 0 1 auto; min-width: 0; max-width: 148px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-state { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: var(--s3); min-width: 0; margin-left: auto; }
.bar-state .tag { white-space: nowrap; }
.bar-state .btn { flex: 0 0 auto; height: var(--btn-h); padding: 0 var(--btn-px); font-size: var(--btn-fs); }
.stream { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 8px; padding: 8px; background: var(--bg-inset); scrollbar-gutter: stable; }
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.bub { max-width: 88%; padding: 6px 9px; font-size: 12.5px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: 0; clip-path: var(--clip-br); }
.msg.user .bub { background: var(--acc-soft); border: 1px solid var(--line-3); }
.msg.assistant .bub { background: var(--surface-2); border: 1px solid var(--line); color: var(--tx-1); }
.message-plan { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; white-space: normal; }
.message-plan pre { overflow: auto; max-width: 100%; padding: 8px; background: var(--bg-inset); white-space: pre; }
.confirm { display: flex; align-items: flex-start; gap: 5px; margin-bottom: 6px; }
.plist { display: flex; flex-direction: column; gap: 3px; font-size: 12.5px; padding-left: 18px; }
.composer { flex: 0 0 auto; display: flex; flex-direction: column; gap: 4px; padding: 6px; border-color: var(--line-2); }
.ipt { min-height: 56px; max-height: 150px; resize: vertical; font-family: var(--font-body); background: var(--bg-inset); border-radius: 0; }
.crow { display: flex; align-items: center; gap: 6px; }
@media (max-width: 520px) {
  .ai { gap: 5px; }
  .ai-header { min-height: 40px; padding: 4px 8px; }
  .model-tag { max-width: 112px; }
  .ai-title { font-size: 12px; }
  .bar-state .tiny { display: none; }
  .stream { padding: 7px; gap: 7px; }
  .bub { max-width: 94%; }
  .composer { padding: 7px; }
  .ipt { min-height: 48px; }
  .crow { min-height: 28px; }
  .crow > .tiny { font-size: 10px; }
}
</style>
