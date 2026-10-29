// Restore regression: mocked store only; never uses a robot or external AI service.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { ref, computed, watch, nextTick, reactive, toRefs, effectScope } from 'vue'

const source = readFileSync(new URL('../src/panels/LegacyAiAssistant.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor
function harness(overrides = {}) {
  const calls = [], props = reactive({ active: true }), scope = effectScope()
  const world = reactive({ aiHistory: [{ role: 'assistant', content: '已有历史' }],
    aiPlan: { message_id: 'current-message', plan_id: 'checked', summary: '已有提案', plan: [{ op: 'wait', sec: 1 }], warnings: ['需核对'], code: 'WAIT 1', status: 'ready' },
    snap: { motion_granted: true }, meta: { ai: { model: 'configured-model', key_present: true } },
    async aiSay(text) { calls.push(['chat', text]); return { ok: true } },
    async aiRun() { calls.push(['run']); return { ok: true } }, ...overrides })
  world.aiReason = message => message === world.aiPlan ? '' : '历史消息不能执行'
  world.aiDiscard = () => { world.aiPlan = null }
  const code = compileScript(descriptor, { id: 'legacy-ai' }).content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
  const deps = { ref, computed, watch, nextTick, storeToRefs: toRefs, useWorld: () => world }
  const component = new Function(...Object.keys(deps), code)(...Object.values(deps))
  const api = scope.run(() => component.setup(props, { expose() {} }))
  return { api, world, props, calls, dispose() { scope.stop() } }
}

test('restored assistant compiles with history, proposals, warnings, explicit run and composer', () => {
  const script = compileScript(descriptor, { id: 'legacy-ai' })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'LegacyAiAssistant.vue', id: 'legacy-ai', compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: 'LegacyAiAssistant.vue', id: 'legacy-ai', scoped: true }).errors, [])
  for (const text of ['aiHistory', '待确认计划', 'h.result.warnings', '确认执行', '@submit.prevent="say"']) assert.ok(source.includes(text))
  assert.doesNotMatch(source, /v-html|<section v-if="aiPlan"|吸\/吹/)
  assert.match(source, /v-for="\(h, i\) in aiHistory"[\s\S]*?h.result.code[\s\S]*?run\(h.result\)/)
})
test('mount and switching tabs preserve chat input, history and proposal without any action', async () => {
  const h = harness()
  try {
    assert.deepEqual(h.calls, [])
    h.api.text.value = '尚未发送'
    h.props.active = false; await nextTick()
    await h.api.say(); await h.api.run()
    assert.deepEqual(h.calls, [])
    h.props.active = true; await nextTick()
    assert.equal(h.api.text.value, '尚未发送')
    assert.equal(h.api.aiHistory.value[0].content, '已有历史')
    assert.equal(h.api.aiPlan.value.plan_id, 'checked')
    assert.deepEqual(h.calls, [])
  } finally { h.dispose() }
})
test('chat is explicit, duplicate sends are blocked and a returned proposal never runs automatically', async () => {
  let complete, sends = 0, runs = 0
  const h = harness({ aiSay: async () => { sends++; return new Promise(resolve => { complete = resolve }) }, aiRun: async () => { runs++; return { ok: true } } })
  try {
    h.api.text.value = '生成一个提案'
    const first = h.api.say()
    h.api.text.value = '重复发送'; await h.api.say()
    assert.equal(sends, 1); assert.equal(runs, 0)
    complete({ ok: true }); await first
    assert.equal(h.api.busy.value, false); assert.equal(runs, 0)
  } finally { h.dispose() }
})
test('execution preserves motion permission and duplicate-submit guards; rejection is visible', async () => {
  let complete, runs = 0
  const h = harness({ aiRun: async () => { runs++; return new Promise(resolve => { complete = resolve }) } })
  try {
    h.world.snap.motion_granted = false; await h.api.run(); assert.equal(runs, 0)
    h.world.snap.motion_granted = true
    const message = h.world.aiPlan
    await h.api.run(message); assert.equal(runs, 0) // checkbox is mandatory
    h.api.confirmed.value = message.message_id
    const first = h.api.run(message); await h.api.run(message)
    assert.equal(runs, 1)
    complete({ ok: false, why: '计划已过期' }); await first
    assert.equal(h.api.executing.value, false); assert.equal(h.api.err.value, '计划已过期')
    assert.equal(h.api.aiPlan.value.plan_id, 'checked')
  } finally { h.dispose() }
})

test('code load only edits local source; historical confirmation cannot run a different plan', async () => {
  const h = harness()
  try {
    const message = h.world.aiPlan
    h.api.loadCode(message)
    assert.equal(h.world.programSrc, 'WAIT 1'); assert.deepEqual(h.calls, [])
    assert.match(h.api.notice.value, /未执行/)
    h.api.confirmed.value = 'old-message'
    await h.api.run({ ...message, message_id: 'old-message' })
    assert.deepEqual(h.calls, [])
    await h.api.clearHistory()
    assert.equal(h.world.aiPlan, null); assert.deepEqual(h.world.aiHistory, [])
  } finally { h.dispose() }
})
test('chat errors recover and IME or modified Enter cannot submit', async () => {
  let sends = 0
  const h = harness({ aiSay: async () => { sends++; throw new Error('请求失败') } })
  try {
    h.api.text.value = '测试'
    for (const modifier of ['isComposing', 'shiftKey', 'ctrlKey', 'altKey', 'metaKey']) h.api.sendOnEnter({ [modifier]: true, preventDefault() { throw new Error('should not intercept') } })
    assert.equal(sends, 0)
    await h.api.say()
    assert.equal(h.api.err.value, '请求失败'); assert.equal(h.api.busy.value, false)
    h.api.dropPlan(); assert.equal(h.world.aiPlan, null)
    assert.equal(h.world.aiHistory.length, 1)
  } finally { h.dispose() }
})
