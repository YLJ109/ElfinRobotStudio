// 诊断用：把页面加载后的 WS 状态、快照有无、控制台报错打出来。
// 跑法： node tools/cdp_diag.mjs [url]
import { spawn } from 'node:child_process'

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const url = process.argv[2] || 'http://127.0.0.1:8710/#/power'
const PORT = 9334

const chrome = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--enable-unsafe-swiftshader',
  '--use-angle=swiftshader', '--remote-debugging-port=' + PORT, 'about:blank',
], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let page = null
for (let i = 0; i < 60 && !page; i++) {
  await sleep(250)
  try {
    const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
    page = l.find((t) => t.type === 'page')
  } catch { /* 还没起来 */ }
}
if (!page) { chrome.kill(); throw new Error('CDP 没起来') }

const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((r) => { ws.onopen = r })
let id = 0
const pending = new Map()
const logs = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Runtime.consoleAPICalled') {
    logs.push(m.params.type + ': ' + m.params.args.map((a) => a.value ?? a.description ?? a.type).join(' '))
  }
  if (m.method === 'Runtime.exceptionThrown') {
    logs.push('EXCEPTION: ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text))
  }
  if (m.method === 'Log.entryAdded') logs.push('log-entry: ' + m.params.entry.text)
}
const send = (method, params = {}) => new Promise((res, rej) => {
  const mid = ++id
  pending.set(mid, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)))
  ws.send(JSON.stringify({ id: mid, method, params }))
})
const ev = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) return 'EVAL-ERR ' + JSON.stringify(r.exceptionDetails).slice(0, 200)
  return r.result.value
}

await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable')
await send('Page.navigate', { url })
await sleep(9000)

console.log('href          =', await ev('location.href'))
console.log('WS connected? =', await ev(`(() => {
  const s = document.querySelector('.link')
  return s ? s.textContent.trim() : 'no el'
})()`))
console.log('面板有无内容  =', await ev(`document.querySelectorAll('.side .panel').length`))
console.log('HTTP /api/state 从页面里 =', await ev(
  `(async () => { try { const r = await fetch('/api/state'); const j = await r.json()
      return r.status + ' mode=' + j.mode + ' fsm=' + ((j.real||{}).fsm) } catch(e) { return 'ERR ' + e.message } })()`))
console.log('store 里 snap =', await ev(`(() => {
  try { const app = document.querySelector('#app').__vue_app__
        return app ? 'vue app ok' : 'no __vue_app__' } catch(e) { return 'ERR ' + e.message } })()`))
console.log('\n--- 控制台 ---')
console.log(logs.slice(0, 25).join('\n') || '(无输出)')

chrome.kill(); ws.close()
