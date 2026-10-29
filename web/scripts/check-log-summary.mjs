import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import { summarizeLog, logKindLabel } from '../src/logSummary.js'

test('arrival is simplified exactly; unknown suffix errors remain verbatim',()=>{
  const original='仿真已到达残影目标（六轴残差≤0.3°），残影自动收掉'
  assert.deepEqual(summarizeLog(original),{text:'仿真已到位，已收起预览',original,changed:true})
  const error=original+'；但发生 TimeoutError，code=29999'
  assert.equal(summarizeLog(error).text,error)
})

test('categories use Chinese display labels without replacing keys or command contents',()=>{
  assert.equal(logKindLabel('event'),'事件');assert.equal(logKindLabel('execution'),'执行')
  for(const key of ['cmd','event','alarm','ai','program','point','link','sys','interlock','perm','jog','ready','speed','power','free','execution'])
    assert.match(logKindLabel(key),/[\u4e00-\u9fff]/)
  for(const unknown of ['new-event','toString','__proto__'])assert.equal(logKindLabel(unknown),unknown)
  const raw='MOVEJ [0,10,20,30,40,50] failed code=20904 FSM=14\nTimeoutError: socket closed'
  assert.equal(summarizeLog(raw).text,raw)
})

test('both log UIs ellipsize collapsed lines and keep original details and metadata',()=>{
  for(const path of ['../src/components/LogDock.vue','../src/panels/LogsPanel.vue']) {
    const source=readFileSync(new URL(path,import.meta.url),'utf8')
    assert.match(source,/white-space:\s*nowrap/);assert.match(source,/text-overflow:\s*ellipsis/)
    assert.match(source,/logKindLabel\(r.kind\)/)
    assert.match(source,/:title="r.summary.original"/)
    assert.match(source,/JSON.stringify\([^\n]+extra, null, 2\)/)
    assert.match(source,/white-space:\s*pre-wrap/)
  }
})

test('model summary removes source boilerplate, not model identity', () => {
  assert.equal(summarizeLog('模型 elfin5 载入，限位与几何来自官方 URDF').text, '模型 elfin5 已载入')
})
test('protection stop keeps the stop and reset requirement', () => {
  assert.equal(summarizeLog('触发 100%/碰撞级：已自动停止执行，需人工复位后继续').text,
    '已停止：触发保护，需复位')
})
test('object collision shows names, and mixed hazards/counts are retained', () => {
  assert.equal(summarizeLog('安全等级 -> alarm：检测到越界/碰撞：object（1 处）；摆放物「box-a」压到 elfin_link1').text,
    '碰撞：box-a / elfin_link1')
  const mixed = summarizeLog('检测到越界/碰撞：fence/object（3 处）；摆放物「box-a」压到 elfin_link1').text
  for (const token of ['fence/object', '3处', 'box-a', 'elfin_link1']) assert.ok(mixed.includes(token))
})
test('boundary-only alarm does not invent an object or lose hazard types', () => {
  assert.equal(summarizeLog('安全等级 -> alarm：检测到越界/碰撞：fence/ground（2 处）').text,
    '越界/碰撞：fence/ground（2处）')
})
test('unknown FSM and enabled value remain visible', () => {
  assert.equal(summarizeLog('未验证的控制器状态 FSM=99，流程已中止').text, '已中止：未知 FSM=99')
  assert.equal(summarizeLog('当前状态不允许 enable：FSM=7、enabled=0').text,
    'enable 被拒绝：FSM=7 / enabled=0')
})
test('command failure retains command, error code, and known reason', () => {
  assert.equal(summarizeLog('GrpReset,0,; -> GrpReset,Fail,20904,0,Emergency stop reset timeout (2656ms)').text,
    'GrpReset 失败 [20904]：急停复位超时')
  assert.equal(summarizeLog('GrpEnable,0,; -> GrpEnable,Fail,20018,This command is prohibited from being executed in the current robot state (0ms)').text,
    'GrpEnable 失败 [20018]：当前状态禁止执行')
})
test('unknown errors keep all detail, additional codes and important exceptions', () => {
  const text = summarizeLog('Foo,0,; -> Foo,Fail,29999,secondary=28888 TimeoutError FSM=14 (10ms)').text
  for (const token of ['Foo', '29999', '28888', 'TimeoutError', 'FSM=14']) assert.ok(text.includes(token))
})
test('unknown messages and unrecognized extra warnings fall back verbatim', () => {
  for (const text of ['新设备自检消息，错误码 27123，请检查接线',
    '模型 elfin5 载入，限位与几何来自官方 URDF；但校验失败 TimeoutError',
    '触发保护但尚未确认停止，FSM=33', '手动 MOVEJ failed: 控制器状态已失效或连接已变化']) {
    const result = summarizeLog(text)
    assert.equal(result.text, text)
    assert.equal(result.changed, false)
  }
})
test('raw record is retained exactly and never mutated', () => {
  const row = Object.freeze({seq:12,kind:'sys',text:'模型 elfin5 载入，限位与几何来自官方 URDF'})
  const result = summarizeLog(row)
  assert.equal(result.original, row.text)
  assert.equal(result.changed, true)
  assert.equal(row.seq, 12)
})
test('component exposes exact original in both hover title and expandable details', () => {
  const component = readFileSync(new URL('../src/components/LogDock.vue', import.meta.url), 'utf8')
  assert.match(component, /<details class="message"/)
  assert.match(component, /<summary :title="r\.summary\.original"/)
  assert.match(component, /class="original"[\s\S]*?\{\{ r\.summary\.original \}\}/)
  assert.doesNotMatch(component, /\.dock\s*\{[^}]*\bwidth\s*:/)
})

test('title bar cannot collapse; only its dedicated button closes the card', () => {
  const component = readFileSync(new URL('../src/components/LogDock.vue', import.meta.url), 'utf8')
  const header = component.match(/<div class="panel-hd"[^>]*>/)?.[0]
  assert.ok(header)
  assert.doesNotMatch(header, /@click|v-on:click/)
  assert.match(component, /aria-label="收起运行日志"[\s\S]*?@click\.stop="open = false"/)
  assert.doesNotMatch(component, /open = !open/)
})

test('collapsed state replaces the whole panel with a compact restore button', () => {
  const component = readFileSync(new URL('../src/components/LogDock.vue', import.meta.url), 'utf8')
  assert.match(component, /<button v-if="!open" class="dock log-trigger btn btn--xs"[\s\S]*?@click="open = true">运行日志<\/button>\s*<section v-else/)
  assert.match(component, /\.dock\.log-trigger\s*\{[^}]*width:max-content !important/)
  assert.match(component, /\.dock\.log-trigger\s*\{[^}]*max-width:calc\(100% - 20px\) !important/)
  assert.doesNotMatch(component, /folded|<section[^>]*v-show/)
  assert.match(component, /localStorage\.setItem\('huayan\.log\.open'/)
})
