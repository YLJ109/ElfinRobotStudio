// 残影验收（假手模型）：拖滑块只摆假手，真机那台必须原地不动；
// 点执行后真机开过去，站到假手位置上之后假手自动收掉。
// 跑法： node tools/cdp_probe.mjs
import { spawn } from 'node:child_process'
import { writeFile, mkdir } from 'node:fs/promises'

/* 前置闸：这个探针会拖 J6 走 35°，是给**仿真**写的。
   2026-10-12 真机就绪时被它误拖走 17°（我随后分步退回原位）。
   所以 mode=real 时默认直接退出（连 Chrome 都不必起），除非显式 --allow-real。 */
const URL0 = process.argv[2] || 'http://127.0.0.1:8710'
if (!process.argv.includes('--allow-real')) {
  const st = await (await fetch(URL0 + '/api/state')).json()
  if (st.mode === 'real') {
    console.log('拒绝执行：后端现在是真机模式，这个探针会真的拖走 J6 35°。'
      + '先断开回仿真（驱动页里点断开），或明确加 --allow-real。')
    process.exit(2)
  }
}

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const PORT = 9333
const OUT = (process.env.TEMP || 'C:/temp').replace(/\\/g, '/') + '/hyshots'
const chrome = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--enable-unsafe-swiftshader',
  '--use-angle=swiftshader', '--hide-scrollbars', '--window-size=1560,900',
  `--remote-debugging-port=${PORT}`, 'about:blank',
], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

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
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) return 'EVAL-ERR ' + JSON.stringify(r.exceptionDetails).slice(0, 200)
  return r.result ? r.result.value : undefined
}
const shot = async (name) => {
  const r = await send('Page.captureScreenshot', { format: 'png' })
  await mkdir(OUT, { recursive: true })
  await writeFile(`${OUT}/${name}.png`, Buffer.from(r.data, 'base64'))
}
// 本体 J6 关节节点的世界旋转 vs 假手 J6 —— 用来证明"真机没动、假手动了"
const poseOf = () => ev(`(() => {
  const s = window.__elfinScene
  const g = s.ghostInfo()
  const q = window.__elfinScene.lastRealQ || null
  return JSON.stringify({ ghostVisible: g.visible, meshes: g.meshes, realMeshes: g.realMeshes, q })
})()`)
const backendQ = () => ev(`(async () => { const s = await (await fetch('/api/state')).json()
  return s.joints.q.map(v => +v.toFixed(2)).join(',') })()`)
const dragJ6 = (to, steps = 20) => ev(`(() => {
  const el = [...document.querySelectorAll('.ax input[type=range]')][5]
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  for (let i = 1; i <= ${steps}; i++) { set.call(el, (${to} * i / ${steps}).toFixed(2)); el.dispatchEvent(new Event('input', { bubbles: true })) }
  return el.value
})()`)

await send('Page.enable'); await send('Runtime.enable')

await send('Page.navigate', { url: 'http://127.0.0.1:8710/#/work/points' })
for (let i = 0; i < 60; i++) {
  await sleep(250)
  if (await ev(`!!(document.querySelector('.link')||{}).textContent?.includes('后端在线')`)) break
}
await sleep(1000)
// 走正门：确认框换一次性凭据 → 拿凭据升运动档。直接调 /api/perm 已经升不上去了。
const grant = await ev(`(async () => {
  const j = async (p, b) => (await fetch(p, {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)})).json()
  // 新模型里没有"调档"这回事：按主按钮把状态推到 就绪+使能，许可自动就是运动。
  const ack = await j('/api/perm/ack', {acks:{nobody:true, estop:true}})
  const r = await j('/api/control', {key:'ready', challenge:ack.challenge})
  let g = null
  for (let i = 0; i < 24; i++) {
    await new Promise(res => setTimeout(res, 250))
    g = await j('/api/state')
    if (g.motion_granted) break
  }
  return JSON.stringify({ack:!!ack.challenge, ok:r.ok, granted:!!(g && g.motion_granted),
                         fsm:g && g.sim_state.fsm, why:r.why})
})()`)
console.log('开运动许可 =', grant)
await sleep(500)

let fails = 0
const say = (n, ok, d) => { if (!ok) fails++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}  ${d}`) }

const q0 = await backendQ()
say('起始：假手不可见', (await JSON.parse(await poseOf())).ghostVisible === false, q0)

// 1) 拖滑块 → 假手出现，真机（后端上报）角度一点不变
const tgt = await dragJ6(35)
await sleep(700)
const p1 = JSON.parse(await poseOf())
const q1 = await backendQ()
say('拖滑块后假手现身', p1.ghostVisible === true, `假手网格 ${p1.meshes} 个 / 本体 ${p1.realMeshes} 个`)
say('真机原地不动（后端角度未变）', q1 === q0, `目标 J6=${tgt}  后端实际=${q1}`)
const hud = await ev(`[...document.querySelectorAll('.hud .tag')].map(e=>e.textContent.trim()).join(' | ')`)
say('HUD 标注"真机未动"', hud.includes('残影就位'), hud)
await shot('g1_ghost_up')

// 2) 点执行 → 真机开过去
await ev(`(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='执行目标'); b.click(); return 1 })()`)
await sleep(500)
const mid = await backendQ()
say('执行后真机开始动', mid !== q0, `0.5s 后=${mid}`)
await shot('g2_moving')

// 3) 到位后假手自动收掉
let gone = false
for (let i = 0; i < 40; i++) {
  await sleep(300)
  const st = JSON.parse(await poseOf())
  const qq = (await backendQ()).split(',').map(Number)
  if (!st.ghostVisible && Math.abs(qq[5] - Number(tgt)) < 0.3) { gone = true; break }
}
const fin = await backendQ()
say('真机站到假手位置上后假手自动消失', gone, `末态=${fin}，目标 J6=${tgt}`)
await shot('g3_arrived')

// 4) 取消：立刻收假手 + 滑块回真值
await dragJ6(-25, 12)
await sleep(500)
const before = JSON.parse(await poseOf())
await ev(`(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='取消'); b.click(); return 1 })()`)
await sleep(600)
const after = JSON.parse(await poseOf())
const sliders = await ev(`[...document.querySelectorAll('.ax input[type=range]')].map(e=>Number(e.value).toFixed(1)).join(',')`)
say('取消立刻收假手', before.ghostVisible && !after.ghostVisible, `${before.ghostVisible} → ${after.ghostVisible}`)
say('取消后滑块回到真实数据', sliders === fin.split(',').map(v => Number(v).toFixed(1)).join(','),
    `滑块=${sliders} 实际=${fin}`)

// 5) 实时模式：拖滑块真机直接跟着动，不出假手
await ev(`(async () => { await fetch('/api/settings', {method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify({ghost_mode:'live'})}); return 1 })()`)
await sleep(400)
const qLive0 = await backendQ()
await dragJ6(10, 10)
// 实时模式是"松手才下发"（避免每帧都往控制器灌 MOVEJ），所以要补一个 change
await ev(`(() => { const el=[...document.querySelectorAll('.ax input[type=range]')][5]
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  set.call(el, '10'); el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true })); return 1 })()`)
let followed = false
for (let i = 0; i < 30; i++) {
  await sleep(300)
  const qq = (await backendQ()).split(',').map(Number)
  if (Math.abs(qq[5] - 10) < 0.5) { followed = true; break }
}
const gLive = JSON.parse(await poseOf()).ghostVisible
say('实时模式：真机跟着滑块走且不出假手', followed && !gLive, `末态=${await backendQ()} 假手=${gLive}（起点 ${qLive0}）`)

// 收尾：回仿真零位、降回只读、恢复残影模式
await ev(`(async () => {
  const j = async (p, b) => (await fetch(p, {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)})).json()
  await j('/api/movej', {joints:[0,0,0,0,0,0]})
  await j('/api/settings', {ghost_mode:'ghost'})
  await j('/api/control', {key:'cancel_ready'})
  return 1 })()`)
await sleep(2500)
console.log('收尾后 =', await backendQ(), 'perm=', await ev(`(async()=>((await (await fetch('/api/state')).json()).perm))()`))
console.log(fails ? `\n${fails} 项未达标` : '\n全部通过')
chrome.kill(); ws.close()
process.exit(fails ? 1 : 0)
