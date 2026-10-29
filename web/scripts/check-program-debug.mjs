import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { createProgramDebug, parseBreakpoints, canPollDebug } from '../src/programDebug.js'

test('debug polling is restricted to a visible authorised simulation tab',()=>{
  assert.equal(canPollDebug('debug',true,true),true)
  for(const args of [['editor',true,true],['debug',false,true],['debug',true,false],['debug',true,undefined]])assert.equal(canPollDebug(...args),false)
})

test('程序语法在本页打开，明确项目语言边界，不再跳转到无关概览', () => {
  const panel = readFileSync(new URL('../src/panels/ProgramPanel.vue', import.meta.url), 'utf8')
  const rules = readFileSync(new URL('../src/components/ProgramRules.vue', import.meta.url), 'utf8')
  assert.match(panel, /@click="programTab = 'rules'"/)
  assert.match(panel, /\['rules','语法'\]/)
  assert.doesNotMatch(panel, /@click="w.view = 'about'"/)
  assert.match(rules, /不是完整 Python 或厂商示教器格式/)
  assert.match(rules, /输出\(0,1\)/)
})

test('breakpoints are bounded positive source lines', () => {
  assert.deepEqual(parseBreakpoints('8, 3，8 12'), [3, 8, 12])
  assert.deepEqual(parseBreakpoints(''), [])
  for (const text of ['0', '-1', '1.5', '200001', 'NaN', Array(201).fill('2').join(',')]) {
    assert.throws(() => parseBreakpoints(text))
  }
})

test('construction sends nothing and all requests reject real context', async () => {
  let calls = 0
  const client = createProgramDebug({ request: () => { ++calls }, allowed: () => false })
  assert.equal(calls, 0)
  for (const operation of [() => client.status(), () => client.load('WAIT 0', []),
    () => client.control('step'), () => client.control('stop'), () => client.breakpoints([1])]) {
    await assert.rejects(operation, /仅限/)
  }
  assert.equal(calls, 0)
})

test('explicit load and step use debug routes and server session', async () => {
  const calls = []
  const client = createProgramDebug({ allowed: () => true, request: async (url, options) => {
    calls.push({ url, body: options.body && JSON.parse(options.body) })
    return { ok: true, json: async () => ({ ok: true, enabled: true, session: 'server-token', state: 'paused' }) }
  } })
  await client.load('SPEED 10', [1])
  assert.equal(calls.length, 1)
  await client.control('step')
  assert.equal(calls[1].url, '/api/program/debug/step')
  assert.equal(calls[1].body.session, 'server-token')
  assert.ok(calls.every(call => !/connect|camera|\/program\/start/u.test(call.url)))
})

test('missing integration and rejected operations surface real errors', async () => {
  const client = createProgramDebug({ allowed: () => true,
    request: async () => ({ status: 404 }) })
  await assert.rejects(() => client.status(), /路由尚未集成/)
  const denied = createProgramDebug({ allowed: () => true,
    request: async () => ({ ok: false, json: async () => ({ detail: '尚未完成' }) }) })
  await assert.rejects(() => denied.control('step'), /尚未完成/)
})

test('in-flight requests serialize and stale context responses are discarded', async () => {
  let resolve, allowed = true
  const client = createProgramDebug({ allowed: () => allowed,
    request: () => new Promise(done => { resolve = done }) })
  const pending = client.status()
  await assert.rejects(() => client.control('step'), /正在处理/)
  allowed = false
  resolve({ ok: true, json: async () => ({ ok: true, session: 'old' }) })
  await assert.rejects(() => pending, /响应已丢弃/)
})

test('program panel compiles and retains editor across categorized tabs', () => {
  const source = readFileSync(new URL('../src/panels/ProgramPanel.vue', import.meta.url), 'utf8')
  const { descriptor, errors } = parse(source)
  assert.deepEqual(errors, [])
  compileScript(descriptor, { id: 'program-debug-check' })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content,
    filename: 'ProgramPanel.vue', id: 'program-debug-check' }).errors, [])
  assert.match(source, /<ProgramEditor v-model="programSrc"/u)
  assert.match(source, /v-show="programTab==='editor'"/u)
  assert.match(source, /clearInterval\(debugTimer\)/u)
})
