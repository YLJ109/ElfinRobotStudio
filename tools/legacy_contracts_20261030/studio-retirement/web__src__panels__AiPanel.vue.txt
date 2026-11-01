<script setup>
import { computed, ref } from 'vue'
import { useWorld } from '../store'
import { diagnosticRows, diffDraftText, studioDraftText } from '../aiReadOnly.js'
import { readStudioSession } from '../taskStudio.js'
import LegacyAiAssistant from './LegacyAiAssistant.vue'

const w = useWorld()
const tab = ref('assistant'), before = ref(''), after = ref(''), message = ref(''), error = ref('')
const compared = ref(null), onlyChanges = ref(true)
const diagnostics = computed(() => diagnosticRows(w.connected, w.snap))
const visibleDiff = computed(() => compared.value?.rows.filter(row => !onlyChanges.value || row.kind !== 'same') || [])
function captureBaseline() { before.value = String(w.programSrc || ''); compared.value = null; message.value = '已复制当前编辑器文本作为比较基线。'; error.value = '' }
function loadStudioDraft() {
  error.value = ''; message.value = ''
  try {
    const session = readStudioSession(globalThis.sessionStorage)
    if (!session?.draft) throw new Error('本次会话还没有工艺草稿，请先在工艺工作区生成或打开轨迹。')
    after.value = studioDraftText(session.draft); compared.value = null
    message.value = '已读取工艺会话草稿，仅复制到本地比较区。'
  } catch (e) { error.value = e.message }
}
function compare() {
  error.value = ''; message.value = ''
  try { compared.value = diffDraftText(before.value, after.value) }
  catch (e) { error.value = e.message; compared.value = null }
}
function edited() { compared.value = null }
</script>

<template>
  <div class="ai-readonly">
    <nav class="row workspace-tabs" role="tablist" aria-label="AI 工作区分类">
      <button id="ai-tab-assistant" class="btn" role="tab" aria-controls="ai-panel-assistant" :aria-selected="tab === 'assistant'" :tabindex="tab === 'assistant' ? 0 : -1" :class="{ 'btn--primary': tab === 'assistant' }" :aria-pressed="tab === 'assistant'" @click="tab = 'assistant'">助手</button>
      <button id="ai-tab-diagnostics" class="btn" role="tab" aria-controls="ai-panel-diagnostics" :aria-selected="tab === 'diagnostics'" :tabindex="tab === 'diagnostics' ? 0 : -1" :class="{ 'btn--primary': tab === 'diagnostics' }" :aria-pressed="tab === 'diagnostics'" @click="tab = 'diagnostics'">状态诊断</button>
      <button id="ai-tab-diff" class="btn" role="tab" aria-controls="ai-panel-diff" :aria-selected="tab === 'diff'" :tabindex="tab === 'diff' ? 0 : -1" :class="{ 'btn--primary': tab === 'diff' }" :aria-pressed="tab === 'diff'" @click="tab = 'diff'">草稿差异</button>
    </nav>
    <LegacyAiAssistant v-show="tab === 'assistant'" :active="tab === 'assistant'" class="assistant-body" id="ai-panel-assistant" role="tabpanel" aria-labelledby="ai-tab-assistant" tabindex="0" />
    <p v-if="tab !== 'assistant'" class="dim tiny">本地只读：快照诊断与文本比较，不调用模型或设备。</p>
    <p v-if="tab !== 'assistant' && error" role="alert" class="note bad">{{ error }}</p><p v-if="tab !== 'assistant' && message" role="status" class="note">{{ message }}</p>
    <section v-if="tab === 'diagnostics'" class="col" id="ai-panel-diagnostics" role="tabpanel" aria-labelledby="ai-tab-diagnostics" tabindex="0">
      <div v-for="row in diagnostics" :key="row.label" class="panel panel-body col"><div class="row"><strong class="grow">{{ row.label }}</strong><span :class="row.tone">{{ row.value }}</span></div><p class="tiny dim">{{ row.detail }}</p></div>
      <details v-if="w.aiPlan" class="panel panel-body"><summary>已有 AI 草稿</summary><p class="tiny dim">未验证，仅供查看；执行请到助手页。</p><pre class="mono existing-draft">{{ JSON.stringify({ summary: w.aiPlan.summary, plan: w.aiPlan.plan, warnings: w.aiPlan.warnings }, null, 2) }}</pre></details>
      <p v-else class="dim tiny">暂无 AI 草稿。</p>
    </section>
    <section v-else-if="tab === 'diff'" class="col" id="ai-panel-diff" role="tabpanel" aria-labelledby="ai-tab-diff" tabindex="0">
      <div class="row"><button class="btn" @click="captureBaseline">读取编辑器作为基线</button><button class="btn" @click="loadStudioDraft">读取工艺会话草稿</button></div>
      <div class="comparison-inputs"><label class="col">比较基线<textarea v-model="before" class="ipt mono" rows="8" spellcheck="false" @input="edited" placeholder="读取编辑器，或粘贴原始文本" /></label><label class="col">候选草稿<textarea v-model="after" class="ipt mono" rows="8" spellcheck="false" @input="edited" placeholder="读取工艺草稿，或粘贴待比较文本" /></label></div>
      <div class="row"><button class="btn btn--primary" @click="compare">比较文本</button><label class="row"><input v-model="onlyChanges" type="checkbox" />仅显示变化</label><span v-if="compared" class="mono tiny">+{{ compared.added }} / −{{ compared.removed }}</span></div>
      <div v-if="compared" class="panel diff-output" aria-label="草稿差异结果"><p v-if="!compared.added && !compared.removed" class="panel-body dim" role="status">两侧文本相同。</p><div v-for="(row, i) in visibleDiff" :key="i" class="diff-line mono" :class="row.kind"><span class="line-number">{{ row.oldLine ?? '·' }}</span><span class="line-number">{{ row.newLine ?? '·' }}</span><span>{{ row.kind === 'added' ? '+' : row.kind === 'removed' ? '−' : ' ' }}</span><span class="line-text">{{ row.text || ' ' }}</span></div></div>
      <p class="dim tiny">文本比较不验证路径安全；工艺执行前须重新检查并确认。</p>
    </section>
  </div>
</template>

<style scoped>
.ai-readonly { display: flex; flex-direction: column; gap: var(--s5); height: 100%; min-height: 0; overflow: auto; }
.assistant-body { flex: 1; min-height: 240px; }
.panel-body { padding: var(--s5); }
h2 { font-size: 15px; }
p, pre { margin: 0; }
.comparison-inputs { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s5); }
textarea { width: 100%; min-height: 160px; resize: vertical; font-size: 12px; }
.diff-output, .existing-draft { max-height: 400px; overflow: auto; }
.existing-draft { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
.diff-line { display: grid; grid-template-columns: 32px 32px 12px minmax(0, 1fr); gap: var(--s3); font-size: 12px; padding: var(--s2); border-bottom: 1px solid var(--line); }
.diff-line.added { background: var(--acc-soft); color: var(--acc); }
.diff-line.removed { background: var(--danger-soft); color: var(--danger); }
.line-number { color: var(--tx-3); text-align: right; }
.line-text { white-space: pre-wrap; overflow-wrap: anywhere; }
summary { cursor: pointer; }
</style>
