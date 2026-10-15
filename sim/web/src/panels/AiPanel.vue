<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { aiHistory, aiPlan, snap, meta } = storeToRefs(w)
const text = ref('')
const busy = ref(false)
const err = ref('')
const granted = computed(() => !!(snap.value && snap.value.motion_granted))
const stream = ref(null)

const DESC = {
  speed: (o) => `当前模式速度 → ${o.pct}%`,
  movej: (o) => (o.point ? `运动到点位 ${o.point}` : `运动到关节角 [${o.joints.map((v) => v.toFixed(1)).join(', ')}]`),
  dout: (o) => `末端 DO${o.bit + 1} → ${o.val ? '通（吸/吹）' : '断（停）'}`,
  wait: (o) => `等待 ${o.sec}s`,
}

watch(() => aiHistory.value.length, async () => {
  await nextTick()
  if (stream.value) stream.value.scrollTop = stream.value.scrollHeight
})

const SUGGEST = [
  '按 P1、P2、P3 的顺序走一遍，速度 8%',
  '把速度调到 5%，然后回到零位',
  '把 DO1 断掉',
]
function ask(t) { text.value = t; say() }
async function say() {
  if (!text.value.trim() || busy.value) return
  busy.value = true
  err.value = ''
  const t = text.value
  text.value = ''
  const r = await w.aiSay(t)
  if (!r.ok) err.value = r.error || '调用失败'
  busy.value = false
}
async function run() {
  const r = await w.aiRun()
  if (!r.ok) err.value = r.why || '执行被拒'
}
function dropPlan() { w.aiPlan = null }
</script>

<template>
  <div class="ai">
    <div class="bar" role="status" aria-live="polite">
      <span class="ai-title">AI 助手</span>
      <span class="mono tag model-tag" :title="meta ? meta.ai.model : ''" :class="meta && meta.ai.key_present ? 'tag--ok' : 'tag--bad'">
        {{ meta ? meta.ai.model : '--' }}
      </span>
      <span class="bar-state">
        <span v-if="busy" class="tag tag--info">思考中</span>
        <span v-if="aiHistory.length" class="dim tiny mono">{{ aiHistory.length }} 条</span>
        <button class="btn btn--xs no-fold" :disabled="!aiHistory.length" @click="w.aiHistory = []">清空</button>
      </span>
    </div>

    <div ref="stream" class="stream">
      <div v-if="!aiHistory.length" class="hello">
        <div class="sug">
          <button v-for="s in SUGGEST" :key="s" class="chip" :disabled="busy" @click="ask(s)">{{ s }}</button>
        </div>
      </div>
      <div v-for="(h, i) in aiHistory" :key="i" class="msg" :class="h.role">
        <div class="bub">{{ h.content }}</div>
      </div>
      <div v-if="busy" class="msg assistant"><div class="bub dim">思考中…</div></div>
      <div v-if="err" class="note bad">{{ err }}</div>
    </div>

    <section v-if="aiPlan" class="panel plan">
      <div class="panel-hd">
        <span class="panel-title">待确认计划</span>
        <span class="panel-sub ellip">{{ aiPlan.summary || '（模型未给说明）' }}</span>
        <span class="grow"></span>
        <span class="tag tag--acc">{{ aiPlan.plan.length }} 步</span>
        <button class="btn btn--xs" @click="dropPlan">丢弃</button>
      </div>
      <div class="panel-bd">
        <ol class="plist">
          <li v-for="(o, i) in aiPlan.plan" :key="i">
            <span class="mono tiny op">{{ o.op }}</span>{{ (DESC[o.op] || (() => JSON.stringify(o)))(o) }}
          </li>
        </ol>
        <div v-for="(wn, i) in aiPlan.warnings" :key="'w' + i" class="note warn">{{ wn }}</div>
        <div class="row">
          <button class="btn btn--primary grow" :disabled="!granted || !aiPlan.plan.length" @click="run">
            人工确认，执行这 {{ aiPlan.plan.length }} 步
          </button>
        </div>
      </div>
    </section>

    <div class="composer">
      <textarea class="ipt" rows="2" v-model="text" :disabled="busy"
                aria-label="AI 指令" placeholder="输入指令，例如：按顺序移动到 P1、P2"
                @keydown.enter.exact.prevent="say"></textarea>
      <div class="crow">
        <span class="dim tiny">Enter 发送 · Shift+Enter 换行</span>
        <span class="grow"></span>
        <button class="btn btn--primary" :disabled="busy || !text.trim()" @click="say">
          {{ busy ? '思考中' : '发送' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai { display: flex; flex-direction: column; gap: 6px; flex: 1 1 auto; height: 100%; min-height: 0; overflow: hidden; }
.bar { display: flex; flex-wrap: nowrap; align-items: center; justify-content: space-between; gap: 8px; flex: 0 0 35px; min-height: 35px; padding: 4px 8px;
       overflow: hidden;
       background: var(--bg-inset); border: 1px solid var(--line); border-radius: 8px; }
.model-tag { min-width: 0; max-width: 58%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-title { flex: 0 1 auto; min-width: 0; color: var(--tx-1); font-family: var(--font-display); font-size: 13px; font-weight: 600; white-space: nowrap; }
.bar-state { display: flex; align-items: center; justify-content: flex-end; gap: 6px; min-width: 0; }
.bar-state .tag { white-space: nowrap; }
.cap { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.stream { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 8px;
          padding: 8px; background: var(--bg-panel); border: 1px solid var(--line); border-radius: var(--r-md); }
.hello { display: flex; flex-direction: column; gap: 6px; }
.hi2 { font-size: 11.5px; line-height: 1.6; }
.sug { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.chip { font: inherit; font-size: 12px; color: var(--tx-2); padding: 5px 9px; text-align: left;
        background: rgba(122, 162, 220, .07); border: 1px solid var(--line); border-radius: 12px;
        cursor: pointer; transition: all .16s; }
.chip:hover { color: var(--tx-1); border-color: var(--line-3); background: rgba(47, 214, 240, .1); }

.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.bub { max-width: 88%; padding: 6px 9px; font-size: 12.5px; line-height: 1.6;
       white-space: pre-wrap; word-break: break-word; border-radius: 10px; }
.msg.user .bub { background: rgba(47, 214, 240, .13); border: 1px solid rgba(47, 214, 240, .3);
                 border-bottom-right-radius: 3px; }
.msg.assistant .bub { background: rgba(122, 162, 220, .07); border: 1px solid var(--line);
                      color: var(--tx-2); border-bottom-left-radius: 3px; }

.plan { flex: 0 0 auto; max-height: 42%; overflow: auto; }
.plist { display: flex; flex-direction: column; gap: 3px; font-size: 12.5px; padding-left: 18px; }
.op { display: inline-block; min-width: 52px; color: var(--acc); }

.composer { flex: 0 0 auto; display: flex; flex-direction: column; gap: 4px; padding: 6px;
            background: var(--bg-panel); border: 1px solid var(--line-2); border-radius: var(--r-md); }
.ipt { width: 100%; resize: none; font: inherit; font-size: 12.5px; line-height: 1.6;
       background: rgba(2, 5, 9, .5); color: var(--tx-1);
       border: 1px solid var(--line); border-radius: 8px; padding: var(--s4); }
.ipt:focus { outline: none; border-color: var(--acc); }
.crow { display: flex; align-items: center; gap: 6px; }

@media (max-width: 520px) {
  .ai { gap: 5px; }
  .bar { min-height: 35px; padding: 4px 7px; }
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
