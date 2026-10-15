// 用 CDP 在真实时间里等页面出数据再截图（--virtual-time-budget 会在 WS 送达前就截，别用）。
// 跑法： node tools/shot_cdp.mjs <outDir> <name>=<url> [<name>=<url> ...]
import { spawn } from 'node:child_process'
import { writeFile, mkdir } from 'node:fs/promises'

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const out = process.argv[2] || '/tmp/hyshots'
const jobs = process.argv.slice(3).map((a) => {
  const i = a.indexOf('=')
  return { name: a.slice(0, i), url: a.slice(i + 1) }
})
if (!jobs.length) { console.error('用法: node tools/shot_cdp.mjs <outDir> name=url ...'); process.exit(1) }

const PORT = 9335
const chrome = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--enable-unsafe-swiftshader',
  '--use-angle=swiftshader', '--hide-scrollbars', '--window-size=1560,900',
  `--remote-debugging-port=${PORT}`, 'about:blank',
], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

await mkdir(out, { recursive: true })
let page = null
for (let i = 0; i < 80 && !page; i++) {
  await sleep(250)
  try {
    const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
    page = l.find((t) => t.type === 'page')
  } catch { /* 还没起来 */ }
}
if (!page) { chrome.kill(); throw new Error('CDP 端口没起来') }

const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((r) => { ws.onopen = r })
let id = 0
const pending = new Map()
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
}
const send = (method, params = {}) => new Promise((res, rej) => {
  const mid = ++id
  pending.set(mid, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)))
  ws.send(JSON.stringify({ id: mid, method, params }))
})
const ev = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  return r.result && r.result.value
}

await send('Page.enable')
await send('Runtime.enable')

for (const j of jobs) {
  await send('Page.navigate', { url: j.url })
  // 等到 store 真收到快照（顶栏出现"后端在线"且面板有卡片），最多 12s
  let ready = false
  for (let i = 0; i < 48; i++) {
    await sleep(250)
    ready = await ev(`(() => {
      const link = document.querySelector('.link')
      return !!(link && link.textContent.includes('后端在线')
                && document.querySelector('.side .panel'))
    })()`)
    if (ready) break
  }
  await sleep(1200)
  const r = await send('Page.captureScreenshot', { format: 'png' })
  await writeFile(`${out}/${j.name}.png`, Buffer.from(r.data, 'base64'))
  console.log((ready ? 'OK  ' : 'NODATA ') + j.name + '.png  ' + j.url)
}

chrome.kill(); ws.close()
