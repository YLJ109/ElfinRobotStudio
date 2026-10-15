// 前端开销 A/B：用 CDP 的 Performance.getMetrics 量 20 秒内的
// 脚本执行 / 样式重算 / 布局 累计耗时与堆占用。
// 跑法： node tools/fe_perf.mjs <标签> [url]
import { spawn } from 'node:child_process'
import { writeFile } from 'node:fs/promises'

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const TAG = process.argv[2] || 'now'
const PAGE_URL = process.argv[3] || 'http://127.0.0.1:8710/#/sim'
const PORT = 9339
const SECONDS = 20

const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu',
  '--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--window-size=1560,900',
  `--remote-debugging-port=${PORT}`, 'about:blank'], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let page = null
for (let i = 0; i < 80 && !page; i++) {
  await sleep(250)
  try {
    const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
    page = l.find((t) => t.type === 'page')
  } catch { /* 还没起来 */ }
}
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((r) => { ws.onopen = r })
let id = 0
const pend = new Map()
ws.onmessage = (e) => {
  const m = JSON.parse(e.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
}
const send = (method, params = {}) => new Promise((res, rej) => {
  const mid = ++id
  pend.set(mid, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)))
  ws.send(JSON.stringify({ id: mid, method, params }))
})
const metrics = async () => {
  const r = await send('Performance.getMetrics')
  const o = {}
  for (const m of r.metrics) o[m.name] = m.value
  return o
}

await send('Performance.enable')
await send('Page.enable')
await send('Page.navigate', { url: PAGE_URL })
for (let i = 0; i < 60; i++) {
  await sleep(250)
  const ready = await (async () => {
    const r = await send('Runtime.evaluate', {
      expression: `!!(document.querySelector('.link')||{}).textContent?.includes('后端在线')`,
      returnByValue: true })
    return r.result.value
  })()
  if (ready) break
}
await sleep(2000)

// 让机器人在动：静态页面量不出真实负载（空闲时后端本来就不重算）
await send('Runtime.evaluate', {
  expression: `(async () => {
    const j = async (p, b) => (await fetch(p, {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)})).json()
    const ack = await j('/api/perm/ack', {acks:{nobody:true, estop:true}})
    await j('/api/perm', {level:1, challenge:ack.challenge})
    await j('/api/movej', {joints:[20,-15,30,0,-20,40]})
    await j('/api/movej', {joints:[-20,15,-30,0,20,-40]})
    await j('/api/perm', {level:0})
    return 1 })()`, returnByValue: true, awaitPromise: true })

const a = await metrics()
const t0 = Date.now()
// 期间持续来回运动，保证前端一直在收帧、渲染
const mover = setInterval(() => {
  send('Runtime.evaluate', {
    expression: `(async () => {
      const j = async (p, b) => (await fetch(p, {method:'POST',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)})).json()
      const ack = await j('/api/perm/ack', {acks:{nobody:true, estop:true}})
      const g = await j('/api/perm', {level:1, challenge:ack.challenge})
      if (g.ok) { await j('/api/movej', {joints:[15,-10,20,0,-15,25]});
                  await j('/api/movej', {joints:[-15,10,-20,0,15,-25]});
                  await j('/api/perm', {level:0}) }
      return 1 })()`, returnByValue: true, awaitPromise: true }).catch(() => {})
}, 1500)
await sleep(SECONDS * 1000)
clearInterval(mover)
const b = await metrics()
const el = (Date.now() - t0) / 1000

const out = {
  tag: TAG, seconds: +el.toFixed(1),
  script_s: +((b.ScriptDuration - a.ScriptDuration)).toFixed(4),
  script_url_s: +((b.ScriptDuration - a.ScriptDuration) / el * 1000).toFixed(2),
  style_s: +((b.RecalcStyleDuration - a.RecalcStyleDuration)).toFixed(4),
  layout_s: +((b.LayoutDuration - a.LayoutDuration)).toFixed(4),
  task_s: +((b.TaskDuration - a.TaskDuration)).toFixed(4),
  heap_MB: +(b.JSHeapUsedSize / 1048576).toFixed(1),
  nodes: b.Nodes, listeners: b.JSEventListeners,
}
console.log(`[${TAG}] ${out.seconds}s 内：脚本 ${out.script_s}s (${out.script_url_s}ms/s) · `
  + `样式 ${out.style_s}s · 布局 ${out.layout_s}s · 任务 ${out.task_s}s · 堆 ${out.heap_MB}MB · `
  + `节点 ${out.nodes}`)
await writeFile(new URL(`./_fe_${TAG}.json`, import.meta.url), JSON.stringify(out, null, 2))
chrome.kill(); ws.close()
