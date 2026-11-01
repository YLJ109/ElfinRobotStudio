// Restore regression: mocked store only; never uses a robot or external AI service.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { ref, computed, watch, nextTick, reactive, toRefs, effectScope } from 'vue'
// 组件新增了 aiProviderCatalog.js 依赖（配置弹窗负责厂商/型号/API Key 的读写）。
// 这里直接接真实模块：readAiConfig / isConfigured / maskKey 的语义由被测源码决定，
// 而不是测试里另写一份同名假实现。
import { AI_CONFIG_KEY, readAiConfig, isConfigured, maskKey, pushAiConfig, clearAiConfig } from '../src/aiProviderCatalog.js'

const source = readFileSync(new URL('../src/panels/LegacyAiAssistant.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor
function harness(overrides = {}, options = {}) {
  const calls = [], props = reactive({ active: true }), scope = effectScope()
  const mounted = [], requests = [], storage = new Map()
  const world = reactive({ aiHistory: [{ role: 'assistant', content: '已有历史' }],
    aiPlan: { message_id: 'current-message', plan_id: 'checked', summary: '已有提案', plan: [{ op: 'wait', sec: 1 }], warnings: ['需核对'], code: 'WAIT 1', status: 'ready' },
    snap: { motion_granted: true },
    async aiSay(text) { calls.push(['chat', text]); return { ok: true } },
    async aiRun() { calls.push(['run']); return { ok: true } }, ...overrides })
  world.aiReason = message => message === world.aiPlan ? '' : '历史消息不能执行'
  world.aiDiscard = () => { world.aiPlan = null }
  // 浏览器侧依赖替身：本机 localStorage、后端 fetch、确认框。测试期间不碰真实后端。
  const previous = { localStorage: globalThis.localStorage, fetch: globalThis.fetch, window: globalThis.window }
  globalThis.localStorage = {
    getItem: key => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => { storage.set(key, String(value)) },
    removeItem: key => { storage.delete(key) },
    clear: () => storage.clear(),
  }
  globalThis.fetch = async (path, init) => { requests.push({ path, body: init?.body ? JSON.parse(init.body) : null }); return { ok: true, json: async () => ({ ok: true }) } }
  globalThis.window = { confirm: () => options.confirm !== false }
  if (options.config) storage.set(AI_CONFIG_KEY, JSON.stringify(options.config))
  const code = compileScript(descriptor, { id: 'legacy-ai' }).content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
  const deps = { ref, computed, watch, nextTick, onMounted: fn => { mounted.push(fn) }, storeToRefs: toRefs, useWorld: () => world,
    readAiConfig, isConfigured, maskKey, pushAiConfig, clearAiConfig, AiModelConfig: { name: 'AiModelConfig' } }
  const component = new Function(...Object.keys(deps), code)(...Object.values(deps))
  const api = scope.run(() => component.setup(props, { expose() {} }))
  return { api, world, props, calls, mounted, requests, storage, dispose() { scope.stop(); globalThis.localStorage = previous.localStorage; globalThis.fetch = previous.fetch; globalThis.window = previous.window } }
}

test('real assistant explicitly selects a pending revision and sends a supplement without execution',async()=>{
 const message={kind:'clarify',request_id:'turn',clarification:{id:'pending',revision:2}}
 let sent,runs=0
 const h=harness({aiClarificationReason:m=>m.clarification?.id==='pending'?'':'旧澄清',aiSay:async(text,selected)=>{sent={text,selected};return {ok:true}},aiRun:async()=>runs++})
 try {
  h.api.supplement(message);assert.equal(h.api.clarificationMessage.value.clarification.revision,2)
  h.api.text.value='等待0.2秒';await h.api.say()
  assert.equal(sent.text,'等待0.2秒');assert.equal(sent.selected.clarification.id,'pending');assert.equal(runs,0);assert.equal(h.api.clarificationMessage.value,null)
  h.api.supplement({kind:'clarify'});assert.match(h.api.err.value,/旧澄清/)
 }finally{h.dispose()}
})

test('restored assistant compiles with history, proposals, warnings, explicit run and composer', () => {
  const script = compileScript(descriptor, { id: 'legacy-ai' })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'LegacyAiAssistant.vue', id: 'legacy-ai', compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: 'LegacyAiAssistant.vue', id: 'legacy-ai', scoped: true }).errors, [])
  for (const text of ['aiHistory', '待确认计划', 'h.result.warnings', '程序运行控制', '@submit.prevent="say"']) assert.ok(source.includes(text))
  assert.doesNotMatch(source, /v-html|<section v-if="aiPlan"|吸\/吹/)
  assert.match(source, /v-for="\(h, i\) in aiHistory"[\s\S]*?h.result.code[\s\S]*?我已核对/)
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

// 改造说明：AI 助手从「内联模型开关」改成「配置弹窗」，组件不再暴露 modelStatus /
// allowModel，标题栏改为展示已保存的型号 + 已配置/未配置标签。这里改断言新行为：
// 标题栏显示型号、状态标签如实反映本机是否配置完整、Key 只以掩码出现、来源标签明确。
test('header shows the configured model with an honest configured/unconfigured tag and masked key',()=>{
 const h=harness({}, {config:{providerId:'zhipu',providerName:'智谱 Zhipu',model:'glm-5.3',baseUrl:'https://open.bigmodel.cn/api/paas/v4',apiKey:'sk-secret-1234'}})
 try{
  assert.equal(h.api.configured.value,true)
  assert.equal(h.api.modelLabel.value,'智谱 Zhipu · glm-5.3')
  assert.match(h.api.modelTitle.value,/Key ••••1234$/u)
  assert.doesNotMatch(h.api.modelTitle.value,/sk-secret-1234/u)
  assert.equal(h.api.sourceLabel({source_kind:'local_rules',kind:'plan',status:'ready'}),'本地规则 · 待确认草稿')
  assert.equal(h.api.sourceLabel({source_kind:'local_rules',kind:'plan',status:'submitted'}),'本地规则')
  assert.equal(h.api.sourceLabel({source_kind:'model'}),'模型回答')
  assert.equal(h.api.sourceLabel({source_kind:'not_generated'}),'未生成回答')
 }finally{h.dispose()}
 const bare=harness()
 try{
  assert.equal(bare.api.configured.value,false)
  assert.equal(bare.api.modelLabel.value,'未选择模型')
  assert.match(bare.api.modelTitle.value,/尚未配置模型/u)
 }finally{bare.dispose()}
})

// 改造说明：原先内联的「允许模型」开关已被用户要求删除，型号改由配置弹窗选定。
// 保留「只有显式发送才调用 AI」这一语义，并追加弹窗/清空两条真实行为断言。
test('model mode is authorized through the config dialog, only explicit sending calls AI, and the retired suggestion chips stay gone',async()=>{
 const sent=[];const h=harness({aiSay:async(...args)=>{sent.push(args);return {ok:true}}})
 try{
  assert.equal(sent.length,0)
  h.api.text.value='解释变量';await h.api.say();assert.equal(sent[0][2],true)
  assert.equal(sent.length,1)
  assert.doesNotMatch(source,/:aria-pressed="allowModel"/u);assert.doesNotMatch(source,/速度 8%|SUGGEST/u)
 }finally{h.dispose()}
})

test('the config button opens the model dialog and the clear button wipes the stored configuration',async()=>{
 const h=harness({}, {config:{providerId:'zhipu',providerName:'智谱 Zhipu',model:'glm-5.3',baseUrl:'https://open.bigmodel.cn/api/paas/v4',apiKey:'sk-secret-1234'}})
 try{
  assert.equal(h.api.configOpen.value,false)
  assert.match(source,/@click="configOpen = true"/u)
  assert.match(source,/<AiModelConfig v-if="configOpen" @close="configOpen = false" @change="onConfigChange"/u)
  h.api.configOpen.value=true
  assert.equal(h.api.configOpen.value,true)
  // 启动时把本机保存的配置交给后端进程（Key 只在请求体里，不落项目文件）。
  h.mounted.forEach(fn=>fn())
  assert.deepEqual(h.requests.at(-1),{path:'/api/ai/config',body:{provider:'智谱 Zhipu',model:'glm-5.3',base_url:'https://open.bigmodel.cn/api/paas/v4',api_key:'sk-secret-1234'}})
  // 弹窗保存新配置后，标题栏的型号与状态随之更新。
  h.api.onConfigChange({providerId:'deepseek',providerName:'深度求索 DeepSeek',model:'deepseek-v4-pro',baseUrl:'https://api.deepseek.com',apiKey:'sk-new-5678'})
  assert.equal(h.api.configured.value,true)
  assert.equal(h.api.modelLabel.value,'深度求索 DeepSeek · deepseek-v4-pro')
  await h.api.dropConfig()
  assert.equal(h.api.configured.value,false)
  assert.equal(h.api.modelLabel.value,'未选择模型')
  assert.equal(h.storage.has(AI_CONFIG_KEY),false)
  assert.deepEqual(h.requests.at(-1),{path:'/api/ai/config',body:{clear:true}})
 }finally{h.dispose()}
})


test('shared Runner controls stop without a valid AI plan, pause/resume authoritative state and preserve rejection', async () => {
 const calls=[]; const h=harness({call:async(path)=>{calls.push(path);return {ok:true}}})
 try {
  h.world.snap.exec={state:'waiting'};h.world.snap.motion_granted=false;h.world.aiPlan=null
  await h.api.togglePause();assert.equal(calls.at(-1),'/api/program/pause')
  h.world.snap.exec.state='paused';await h.api.togglePause();assert.equal(calls.length,1)
  h.world.snap.motion_granted=true;await h.api.togglePause();assert.equal(calls.at(-1),'/api/program/resume')
  await h.api.toggleRun();assert.equal(calls.at(-1),'/api/motion/cancel')
  h.world.snap.exec.state='idle';await h.api.toggleRun();assert.equal(calls.length,3)
  h.world.snap.exec.state='running';h.world.call=async()=>({ok:false,why:'暂停被拒绝'})
  await h.api.togglePause();assert.equal(h.api.err.value,'暂停被拒绝');assert.equal(h.world.snap.exec.state,'running')
 }finally{h.dispose()}
})


test('missing point or failed generation never enables AI execution and stop preempts a pending pause',async()=>{
 let release;const paths=[];const h=harness({call:async(path)=>{paths.push(path);if(path.endsWith('/pause'))return new Promise(r=>release=r);return {ok:true}}})
 try {
  h.world.aiReason=()=> '缺少点位 P1';h.api.confirmed.value=h.world.aiPlan.message_id
  await h.api.toggleRun();assert.equal(h.calls.length,0);assert.equal(h.api.executionWhy.value,'缺少点位 P1')
  h.world.aiPlan=null;await h.api.toggleRun();assert.equal(h.calls.length,0)
  h.world.snap.exec={state:'running'};const pending=h.api.togglePause()
  await h.api.toggleRun();assert.deepEqual(paths,['/api/program/pause','/api/motion/cancel'])
  release({ok:true});await pending
 }finally{h.dispose()}
})


test('simulation executes a ready bound plan directly while real mode still requires explicit safety confirmation',async()=>{
 const h=harness()
 try {
  h.world.snap.mode='sim';h.world.snap.real=null
  await h.api.run(h.world.aiPlan);assert.deepEqual(h.calls,[['run']])
  h.world.snap.mode='real';await h.api.run(h.world.aiPlan);assert.equal(h.calls.length,1)
  h.api.confirmed.value=h.world.aiPlan.message_id;await h.api.run(h.world.aiPlan);assert.equal(h.calls.length,2)
  h.world.snap.mode='sim';h.world.snap.motion_granted=false;await h.api.run(h.world.aiPlan);assert.equal(h.calls.length,2)
 }finally{h.dispose()}
})


test('new question clears old cancellation notice and only generic clarification help is collapsed',async()=>{
 const h=harness()
 try {
  h.api.notice.value='已手动丢弃计划';h.api.text.value='按P1、P2、P3运行';await h.api.say();assert.equal(h.api.notice.value,'')
  const help='可明确补充待定速度、点位或等待；其余情况请以“完整要求：”重述本次全部动作。'
  const specific='请保存这些点位，或提供正确的点位编号。'
  assert.deepEqual(h.api.visibleQuestions({kind:'clarify',questions:[specific,help]}),[specific])
  assert.deepEqual(h.api.visibleQuestions({kind:'failed',questions:['模型连接失败',help]}),['模型连接失败',help])
 }finally{h.dispose()}
})


test('simulation AI run uses the existing bound plan immediately without confirmation or a second preview',async()=>{
 const h=harness({snap:{mode:'sim',real:null,motion_granted:true}})
 try{
  const message=h.world.aiPlan
  assert.equal(h.api.confirmed.value,'');assert.equal(h.api.executionWhy.value,'')
  assert.equal(h.api.sourceLabel({source_kind:'local_rules',kind:'plan',status:'ready'}),'本地规则 · 可执行程序')
  await h.api.toggleRun();assert.deepEqual(h.calls,[['run']])
  assert.equal(h.api.err.value,'')
  h.world.aiReason=()=> '计划参数无效';await h.api.toggleRun()
  assert.deepEqual(h.calls,[['run']]);assert.equal(h.api.executionWhy.value,'计划参数无效')
 }finally{h.dispose()}
})
