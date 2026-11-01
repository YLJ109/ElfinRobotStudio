// Offline read-only checks. No server, controller, camera or external model.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { computed, ref } from 'vue'
import * as helpers from '../src/aiReadOnly.js'
import { readStudioSession, STUDIO_SESSION_KEY } from '../src/taskStudio.js'

const source = readFileSync(new URL('../src/panels/AiPanel.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor

test('diagnostic views allowlist status and never include credentials or device addresses', () => {
  const snap = { mode: 'real', safety: { level: 'red' }, joints: { known: true, q: Array(6).fill(1) }, secret: 'PRIVATE_TOKEN', real: { ip: 'PRIVATE_ADDRESS' } }
  const rows = helpers.diagnosticRows(true, snap)
  assert.equal(rows[1].value, '真机'); assert.equal(rows[2].tone, 'bad')
  assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_TOKEN|PRIVATE_ADDRESS/)
  const disconnected = helpers.diagnosticRows(false, snap)
  assert.equal(disconnected[1].value, '未知'); assert.equal(disconnected[2].value, '未知')
  assert.equal(helpers.diagnosticRows(true, null)[0].tone, 'warn')
  assert.equal(helpers.diagnosticRows(true, { ...snap, joints: { q: [NaN] } })[3].tone, 'warn')
})
test('line differences distinguish insertion, removal, repeated lines and CRLF', () => {
  const diff = helpers.diffDraftText('SPEED 10\r\nMOVEJ 1\r\nWAIT 1', 'SPEED 20\nMOVEJ 1\nWAIT 2')
  assert.equal(diff.added, 2); assert.equal(diff.removed, 2)
  assert.deepEqual(diff.rows.filter(r => r.kind === 'same').map(r => r.text), ['MOVEJ 1'])
  for (const [before, after] of [['', ''], ['', 'A'], ['A', ''], ['A\nB\nA', 'A\nA'], ['A\n', 'A']]) {
    const result = helpers.diffDraftText(before, after)
    assert.equal(result.rows.filter(r => r.kind !== 'added').map(r => r.text).join('\n'), before)
    assert.equal(result.rows.filter(r => r.kind !== 'removed').map(r => r.text).join('\n'), after)
  }
  assert.equal(helpers.diffDraftText('same', 'same').added, 0)
  assert.equal(helpers.diffDraftText('A\r\nB', 'A\nB').removed, 0)
  assert.throws(() => helpers.diffDraftText(Array(401).fill('x').join('\n'), ''), /400/)
  assert.throws(() => helpers.diffDraftText('x'.repeat(80001), ''), /80000/)
})
test('studio text uses validated current points and speed rather than stale program text', () => {
  assert.equal(helpers.studioDraftText({ speed: 27, poses: [[1, 2, 3, 4, 5, 6]], program: 'STALE' }), 'SPEED 27\nMOVEL 1,2,3,4,5,6')
  assert.equal(helpers.studioDraftText({ joints: [Array(6).fill(0)] }), 'SPEED 10\nMOVEJ 0,0,0,0,0,0')
  assert.throws(() => helpers.studioDraftText({ poses: [], speed: 10 }))
})
test('AI workspace compiles; original assistant is a retained independent tab and new tabs stay read-only', () => {
  const script = compileScript(descriptor, { id: 'ai-readonly' })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'AiPanel.vue', id: 'ai-readonly', compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: 'AiPanel.vue', id: 'ai-readonly', scoped: true }).errors, [])
  assert.doesNotMatch(source, /w\.(aiRun|aiSay|call|loadProgram|startProgram)|fetch\(|\/api\/|v-html/)
  assert.match(source, /<LegacyAiAssistant v-show="tab === 'assistant'" :active="tab === 'assistant'"/)
})
test('all panel interactions read local copies without network, credentials or store writes', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  let saved = null
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem(key) { assert.equal(key, STUDIO_SESSION_KEY); return saved },
    setItem() { throw new Error('read-only panel must not write session storage') },
  } })
  const state = { connected: true, snap: { mode: 'sim', safety: { level: 'green' }, joints: { q: Array(6).fill(0) } }, programSrc: 'SPEED 10', aiPlan: null }
  const world = new Proxy(state, {
    get(target, key) { assert.ok(Object.hasOwn(target, key), `unexpected world read: ${String(key)}`); return target[key] },
    set() { throw new Error('read-only panel must not write the store') },
  })
  const code = compileScript(descriptor, { id: 'ai-readonly' }).content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
  const deps = { ...helpers, computed, ref, readStudioSession, LegacyAiAssistant: {}, useWorld: () => world, fetch() { throw new Error('network forbidden') } }
  try {
    const panel = new Function(...Object.keys(deps), code)(...Object.values(deps)).setup({}, { expose() {} })
    assert.equal(panel.tab.value, 'assistant')
    panel.tab.value = 'diff'
    panel.captureBaseline(); assert.equal(panel.before.value, 'SPEED 10')
    panel.after.value = 'SPEED 20'; panel.compare()
    assert.equal(panel.compared.value.added, 1)
    assert.equal(state.programSrc, 'SPEED 10')
    panel.loadStudioDraft(); assert.match(panel.error.value, /还没有工艺草稿/)
    assert.equal(panel.after.value, 'SPEED 20')
    saved = JSON.stringify({ version: 1, tab: 'scan', form: {}, draft: { poses: [Array(6).fill(1)], speed: 35 } })
    panel.loadStudioDraft(); assert.equal(panel.after.value, 'SPEED 35\nMOVEL 1,1,1,1,1,1')
    assert.equal(panel.error.value, '')
    assert.equal(panel.compared.value, null)
    panel.compare(); assert.equal(panel.compared.value.added, 2)
    panel.edited(); assert.equal(panel.compared.value, null)
    assert.equal(panel.diagnostics.value[1].value, '仿真')
    panel.tab.value = 'assistant'; panel.tab.value = 'diff'
    assert.equal(panel.before.value, 'SPEED 10')
    assert.equal(panel.after.value, 'SPEED 35\nMOVEL 1,1,1,1,1,1')
    saved = '{invalid'
    panel.loadStudioDraft(); assert.ok(panel.error.value)
    assert.equal(state.programSrc, 'SPEED 10')
  } finally {
    if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous)
    else delete globalThis.sessionStorage
  }
})
