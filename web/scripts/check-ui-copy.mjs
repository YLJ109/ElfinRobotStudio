// Offline template checks: concise headers, meaningful status and shared tab semantics.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'

const source = name => readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8')
const excluded = new Set(['FencePanel.vue', 'PointsPanel.vue', 'ProgramPanel.vue'])
const panels = readdirSync(new URL('../src/panels/', import.meta.url)).filter(name => name.endsWith('.vue') && !excluded.has(name))
const files = [...panels.map(name => `panels/${name}`), 'components/TopBar.vue']

test('decorative duplicate subtitles are removed without hiding panel subtitles', () => {
  const decorative = /^(?:LINK\s*\/\s*MODE|VIEW|ADVANCED.*|SCENE FILES|SIM OBJECTS|FSM PUMP|PRE-FLIGHT INTERLOCK|JOINT HEALTH|POWER\s*\/\s*ETHERCAT|END\s*\/\s*BOX IO|ERROR EXPLAINER|DEVICE\s*\/\s*DIAGNOSTICS|TIMELINE PLAYBACK|ABOUT\s*\/\s*PROVENANCE|PRODUCT\s*\/\s*ARCHITECTURE|CAPABILITIES)$/
  for (const file of files) {
    const { descriptor } = parse(source(file))
    for (const match of descriptor.template.content.matchAll(/<span\b[^>]*class="[^"]*panel-sub[^"]*"[^>]*>([\s\S]*?)<\/span>/g)) assert.equal(decorative.test(match[1].trim()), false, `${file}: ${match[1]}`)
    for (const style of descriptor.styles) assert.doesNotMatch(style.content, /\.panel-sub[^{}]*\{[^}]*display\s*:\s*none/)
  }
})
test('dynamic subtitles, technical fields, model identity and safety context remain visible', () => {
  const keep = {
    'panels/PowerPanel.vue': ['{{ (real && real.host)', "{{ isReal ? '真机 Data Sheet' : '仿真状态机' }}"],
    'panels/SimPanel.vue': ['{{ caps }} 根碰撞包络', "{{ known ? '基座坐标系"],
    'panels/TelemetryPanel.vue': ['1Hz · 约 {{ trendSpan }} 分钟窗口', 'FTData', 'Script.cmdid', 'FK(q) ↔ CONTROLLER PCS', '关节电流 A', '位置偏差 mm'],
    'panels/HandFollowPanel.vue': ["{{isReal ? '真机 · 手掌'+actionLabel : '仅仿真'}}", '新流程尚未接入', '先到中心，J6 初始归零 → 正立平移跟随'],
    'panels/LegacyAiAssistant.vue': ['{{ h.content }}', 'h.result.warnings', 'h.result.code', '程序运行控制', "runnerActive||executing?'停止':'执行'", "runnerPaused?'继续':'暂停'", 'IO 接线和现场安全'],
    'panels/AboutPanel.vue': ['{{ cmds.length }} 条', 'fields.rows.length', 'huayan-robotics/elfin_robot', '本轮未复测', '不能外推', '不能替代物理急停按钮', '硬件待验证'],
    'panels/AiPanel.vue': ['<LegacyAiAssistant', 'ai-workspace'],
    'components/TopBar.vue': ['<div class="t1">玖枢</div>', '<div class="t2 mono">E05-1195_Pro</div>', '/elfin-logo-arm-v1.png', '{{ speedNote }}', '{{ speedText }}'],
  }
  for (const [file, snippets] of Object.entries(keep)) for (const snippet of snippets) assert.ok(source(file).includes(snippet), `${file} must retain ${snippet}`)
  assert.ok(source('panels/HandFollowPanel.vue').includes("tracking.value?'追踪':'跟随'"),'the real subtitle retains both distinct interaction roles')
  assert.doesNotMatch(source('panels/HandFollowPanel.vue'),/J6 保持 0°|J1～J5 协调跟随位置/,'runtime is full-pose six-axis translation, not a fixed J6 promise')
  assert.doesNotMatch(source('panels/AboutPanel.vue'), />不粉饰</)
  const assistant=source('panels/LegacyAiAssistant.vue')
  assert.match(assistant, /<label v-if="!simulation" class="confirm"/,'real-mode safety acknowledgement remains explicit without burdening simulation')
  assert.match(assistant, /!simulation\.value && confirmed\.value !== message\.message_id/,'only simulation bypasses manual acknowledgement; plan validation remains')
})
test('AI keeps one assistant and places its run controls in the input send row', () => {
  const ai = source('panels/AiPanel.vue')
  assert.doesNotMatch(ai,/tablist|workspace-tabs|状态诊断|草稿差异/)
  const assistant=source('panels/LegacyAiAssistant.vue'),controls=assistant.indexOf('class="crow" aria-label="程序运行控制"')
  assert.ok(controls>assistant.indexOf('<textarea'))
  assert.match(assistant.slice(controls),/@click="toggleRun"[\s\S]*@click="togglePause"[\s\S]*type="submit"/)

})
test('all scoped panel templates and TopBar compile without changing application entry points', () => {
  for (const file of files) {
    const { descriptor, errors } = parse(source(file))
    assert.deepEqual(errors, [], file)
    const script = compileScript(descriptor, { id: file })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: file, id: file, compilerOptions: { bindingMetadata: script.bindings } }).errors, [], file)
    for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: file, id: file, scoped: style.scoped }).errors, [], file)
  }
})
