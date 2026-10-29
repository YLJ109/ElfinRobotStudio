<script setup>
// Restored from this session's original AiPanel, retaining the existing store/API path.
import { ref, computed, watch, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const props = defineProps({ active: { type: Boolean, default: true } })
const w = useWorld()
const { aiHistory, aiPlan, snap, meta } = storeToRefs(w)
const text = ref('')
const busy = ref(false)
const executing = ref(false)
const err = ref('')
const notice = ref('')
const confirmed = ref('')
const granted = computed(() => !!(snap.value && snap.value.motion_granted))
const stream = ref(null)

const DESC = {
  speed: (o) => `当前模式速度 → ${o.pct}%`,
  movej: (o) => (o.point ? `运动到点位 ${o.point}` : `运动到关节角 [${o.joints.map((v) => v.toFixed(1)).join(', ')}]`),
  dout: (o) => `末端 DO${o.bit + 1} → 电平 ${o.val}（协议 bit=${o.bit}，不代表吸取/释放）`,
  wait: (o) => `等待 ${o.sec}s`,
  tool: (o) => `工具动作 → ${{ vacuum_on: '吸附', vacuum_off: '释放', open: '张开', close: '夹紧', attach: '连接', detach: '脱离' }[o.action] || o.action}`,
  require_tool: (o) => `核对当前工具 → ${o.tool_id}`,
}

watch(() => aiHistory.value.length, async () => {
  await nextTick()
  if (stream.value) stream.value.scrollTop = stream.value.scrollHeight
})
watch(() => aiPlan.value?.plan_id, () => { confirmed.value = '' })

const SUGGEST = [
  '速度 8%，按 P1、P2、P3 的顺序走一遍',
  '把速度调到 5%，然后回到零位',
  '把 DO1 断掉',
]
function ask(t) { text.value = t; say() }
async function say() {
  if (!props.active || !text.value.trim() || busy.value) return
  busy.value = true
  err.value = ''
  const t = text.value
  text.value = ''
  try {
    const r = await w.aiSay(t)
    if (!r.ok) err.value = r.error || '调用失败'
  } catch (e) {
    err.value = e.message || '请求失败，请重试'
  } finally { busy.value = false }
}
function reason(message) { return w.aiReason(message) }
async function run(message) {
  if (!props.active || busy.value || executing.value || !granted.value
    || !message || confirmed.value !== message.message_id || reason(message)) return
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
  const r = await w.aiDiscard('已手动丢弃计划。')
  if (r?.ok === false) err.value = r.error || '撤销请求失败'
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
</script>

<template>
  <div class="ai">
    <header class="ai-header panel no-fold">
      <span class="ai-title">AI 助手</span>
      <span class="mono tag model-tag" :title="meta?.ai?.model || ''" :class="meta?.ai?.key_present ? 'tag--ok' : 'tag--mute'">{{ meta?.ai?.model || '--' }}</span>
      <span class="bar-state"><span v-if="busy" class="tag tag--info">思考中</span><span v-if="aiHistory.length" class="dim tiny mono">{{ aiHistory.length }} 条</span><button class="btn btn--xs no-fold" :disabled="!aiHistory.length || busy || executing" @click="clearHistory">清空</button></span>
    </header>
    <section ref="stream" class="stream panel no-fold" aria-label="AI 对话">
      <div v-if="!aiHistory.length" class="hello"><div class="sug"><button v-for="s in SUGGEST" :key="s" class="chip" :disabled="busy" @click="ask(s)">{{ s }}</button></div></div>
      <div v-for="(h, i) in aiHistory" :key="i" class="msg" :class="h.role"><div class="bub">
        <div>{{ h.content }}</div>
        <ul v-if="h.result?.questions?.length"><li v-for="(q, n) in h.result.questions" :key="n">{{ q }}</li></ul>
        <div v-if="h.role === 'assistant' && h.result?.kind === 'plan'" class="message-plan">
          <div class="row"><span class="panel-title">待确认计划 · {{ h.result.plan.length }} 步</span><span class="grow"></span><button v-if="aiPlan?.message_id === h.result.message_id" class="btn btn--xs" :disabled="executing" @click="dropPlan">丢弃</button></div>
          <ol class="plist"><li v-for="(o, n) in h.result.plan" :key="n">{{ DESC[o.op](o) }}</li></ol>
          <div v-for="(wn, n) in h.result.warnings" :key="n" class="note warn">{{ wn }}</div>
          <pre aria-label="已验证计划代码"><code>{{ h.result.code }}</code></pre>
          <div class="row"><button class="btn btn--xs" :disabled="!active" @click="copyCode(h.result)">复制代码</button><button class="btn btn--xs" :disabled="!active" @click="loadCode(h.result)">载入编辑器</button></div>
          <div v-if="h.result.status === 'ready' && !reason(h.result)">
            <label class="confirm"><input type="checkbox" :checked="confirmed === h.result.message_id" @change="confirmed = $event.target.checked ? h.result.message_id : ''">我已核对本条步骤、IO 接线和现场安全</label>
            <button class="btn btn--primary" :disabled="!active || busy || executing || confirmed !== h.result.message_id" @click="run(h.result)">确认执行 · {{ h.result.plan.length }} 步</button>
          </div>
          <div v-else class="note" role="status">{{ h.result.reason || reason(h.result) }}</div>
        </div>
      </div></div>
      <div v-if="err" class="note bad" role="alert">{{ err }}</div>
      <div v-if="notice" class="note" role="status">{{ notice }}</div>
    </section>
    <form class="composer panel corner no-fold" @submit.prevent="say">
      <textarea class="textarea ipt" rows="2" v-model="text" :disabled="busy" aria-label="AI 指令" placeholder="输入指令，例如：按顺序移动到 P1、P2" @keydown.enter="sendOnEnter"></textarea>
      <div class="crow"><span class="dim tiny">Enter 发送 · Shift+Enter 换行</span><span class="grow"></span><button type="submit" class="btn btn--primary" :disabled="!active || busy || !text.trim()">{{ busy ? '思考中' : '发送' }}</button></div>
    </form>
  </div>
</template>

<style scoped>
.ai { display: flex; flex-direction: column; gap: 6px; flex: 1 1 auto; height: 100%; min-height: 0; overflow: hidden; }
.ai-header { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; min-height: 38px; padding: 6px 10px; }
.model-tag { min-width: 0; max-width: 48%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-title { flex: 0 1 auto; min-width: 0; color: var(--tx-1); font-family: var(--font-display); font-size: 13px; font-weight: 600; white-space: nowrap; }
.bar-state { display: flex; align-items: center; justify-content: flex-end; gap: 6px; min-width: 0; margin-left: auto; }
.bar-state .tag { white-space: nowrap; }
.stream { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 8px; padding: 8px; background: var(--bg-inset); scrollbar-gutter: stable; }
.hello { display: flex; flex-direction: column; gap: 6px; }
.sug { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.chip { max-width: 100%; font: inherit; font-size: 12px; color: var(--tx-2); padding: 6px 8px; text-align: left; background: var(--surface-2); border: 1px solid var(--line-2); border-radius: 0; clip-path: var(--clip-br); cursor: pointer; transition: color .16s, border-color .16s, background .16s; }
.chip:hover:not(:disabled) { color: var(--tx-1); border-color: var(--line-3); background: var(--surface-3); }
.chip:disabled { opacity: .45; cursor: not-allowed; }
.chip:focus-visible { outline-offset: -3px; }
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.bub { max-width: 88%; padding: 6px 9px; font-size: 12.5px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: 0; clip-path: var(--clip-br); }
.msg.user .bub { background: var(--acc-soft); border: 1px solid var(--line-3); }
.msg.assistant .bub { background: var(--surface-2); border: 1px solid var(--line); color: var(--tx-1); }
.message-plan { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; white-space: normal; }
.message-plan pre { overflow: auto; max-width: 100%; padding: 8px; background: var(--bg-inset); white-space: pre; }
.confirm { display: flex; align-items: flex-start; gap: 5px; margin-bottom: 6px; }
.plist { display: flex; flex-direction: column; gap: 3px; font-size: 12.5px; padding-left: 18px; }
.op { display: inline-block; min-width: 52px; color: var(--acc); }
.composer { flex: 0 0 auto; display: flex; flex-direction: column; gap: 4px; padding: 6px; border-color: var(--line-2); }
.ipt { min-height: 56px; max-height: 150px; resize: vertical; font-family: var(--font-body); background: var(--bg-inset); border-radius: 0; }
.crow { display: flex; align-items: center; gap: 6px; }
@media (max-width: 520px) {
  .ai { gap: 5px; }
  .ai-header { min-height: 35px; padding: 4px 8px; }
  .model-tag { max-width: 32%; }
  .ai-title { font-size: 12px; }
  .bar-state { gap: 4px; }
  .bar-state .tiny { display: none; }
  .stream { padding: 7px; gap: 7px; }
  .bub { max-width: 94%; }
  .composer { padding: 7px; }
  .ipt { min-height: 48px; }
  .crow { min-height: 28px; }
  .crow > .tiny { font-size: 10px; }
}
</style>
