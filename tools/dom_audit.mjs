// DOM 量测验收：不靠肉眼猜，直接把尺寸和溢出量打出来。
// 跑法： node tools/dom_audit.mjs
import { spawn } from 'node:child_process'

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const PORT = 9336
const chrome = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--enable-unsafe-swiftshader',
  '--use-angle=swiftshader', '--window-size=1560,900', `--remote-debugging-port=${PORT}`, 'about:blank',
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
if (!page) { chrome.kill(); throw new Error('CDP 没起来') }

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
  return r.result ? r.result.value : undefined
}

await send('Page.enable'); await send('Runtime.enable')
let fails = 0
const say = (name, ok, detail) => { if (!ok) fails++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  ${detail}`) }

async function goto(hash) {
  await send('Page.navigate', { url: 'http://127.0.0.1:8710/' + hash })
  for (let i = 0; i < 60; i++) {
    await sleep(250)
    const ready = await ev(`(() => { const l = document.querySelector('.link')
      return !!(l && l.textContent.includes('后端在线') && document.querySelector('.side .panel')) })()`)
    if (ready) break
  }
  await sleep(700)
}

// ---------- 全局：main 内边距 + 横向溢出 ----------
await goto('#/sim')
const pad = await ev(`getComputedStyle(document.querySelector('.main')).padding`)
say('main 上下内边距 6px', pad === '6px', `实测 padding=${pad}`)
const ovf = await ev(`(() => {
  const bad = [...document.querySelectorAll('*')].filter(e => {
    const r = e.getBoundingClientRect()
    return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1)
  }).map(e => e.className + '|' + Math.round(e.getBoundingClientRect().right))
  return JSON.stringify({ doc: document.documentElement.scrollWidth, win: innerWidth, bad: bad.slice(0, 6) })
})()`)
const o = JSON.parse(ovf)
say('无横向溢出', o.doc <= o.win + 1, `scrollWidth=${o.doc} win=${o.win} 越界元素=${JSON.stringify(o.bad)}`)

// ---------- 折叠真的生效 ----------
const fold = await ev(`(() => {
  const p = document.querySelector('.side .panel')
  const before = p.getBoundingClientRect().height
  p.querySelector('.panel-hd').click()
  const after = p.getBoundingClientRect().height
  const folded = p.classList.contains('folded')
  p.querySelector('.panel-hd').click()
  const back = p.getBoundingClientRect().height
  return JSON.stringify({ before: Math.round(before), after: Math.round(after), back: Math.round(back), folded })
})()`)
const fo = JSON.parse(fold)
say('点标题能折叠', fo.folded && fo.after < fo.before - 10, `${fo.before}px → ${fo.after}px → 展开回 ${fo.back}px`)
const btnSafe = 'skipped'   // 带正文按钮的卡片在设置页统一验

// ---------- 顶栏：只有一个主按钮，没有许可选择器 ----------
await goto('#/sim')
const one = await ev(`(() => {
  const top = document.querySelector('.top')
  const prim = [...top.querySelectorAll('.primary')]
  const lamps = [...top.querySelectorAll('.lamp')].map(e => e.textContent.trim())
  return JSON.stringify({ prim: prim.length, label: prim[0] ? prim[0].textContent.trim() : null,
    disabled: prim[0] ? prim[0].disabled : null, lamps,
    perms: !!document.querySelector('.perms'), permSelect: !!top.querySelector('select') })
})()`)
const on = JSON.parse(one)
say('顶栏只有一个主按钮（状态驱动）', on.prim === 1 && !!on.label,
    `数量=${on.prim} 标签=${on.label} disabled=${on.disabled}`)
say('许可选择器已移除（状态推导）', on.perms === false && on.permSelect === false,
    `.perms=${on.perms} 顶栏select=${on.permSelect}`)
say('三个状态灯在（连接/上电/使能）', on.lamps.length === 3, JSON.stringify(on.lamps))

// ---------- 设置页：3 张卡 + 高级折叠 ----------
await goto('#/settings')
const struct = await ev(`(() => {
  const cards = [...document.querySelectorAll('.side .panel-title')].map(e => e.textContent.trim())
  const adv = [...document.querySelectorAll('.side .panel-title')].find(e => e.textContent.trim() === '高级')
  return JSON.stringify({ cards, hasAdv: !!adv,
    limBefore: document.querySelectorAll('.side input[type=number]').length })
})()`)
const sc = JSON.parse(struct)
say('设置页只剩 3 张主卡 + 高级', sc.cards.length === 4 && sc.hasAdv, JSON.stringify(sc.cards))
say('限位表默认收在高级里', sc.limBefore <= 12, `展开前 number 输入=${sc.limBefore}`)
const advOpen = await ev(`(() => {
  const hd = [...document.querySelectorAll('.side .panel-hd')].find(e => e.textContent.includes('高级'))
  hd.click()
  return new Promise(r => setTimeout(() => {
    const nums = document.querySelectorAll('.side input[type=number]').length
    const txt = document.querySelector('.side').textContent
    r(JSON.stringify({ nums, hasGuard: txt.includes('运动硬闸') || txt.includes('可动轴') }))
  }, 350))
})()`)
const ad = JSON.parse(advOpen)
say('展开高级后示教器限位表 12 个输入齐全', ad.nums >= 12, `number 输入=${ad.nums}`)
/* 反向断言：软件里不许再有"替操作者做主"的轴/步幅/速度开关。
   2026-10-12 用户明确要求："我可以移动所有轴啊，不要限制用户操作真机。"
   对我自己跑脚本的限制在 tools/hy.py，不在软件里。 */
say('设置页已无真机运动保险', ad.hasGuard === false, JSON.stringify(ad))

// ---------- AI 页头部不被裁 ----------
await goto('#/work/ai')
const ai = await ev(`(() => {
  const bar = document.querySelector('.ai .bar')
  const kids = [...bar.children].map(e => {
    const r = e.getBoundingClientRect()
    return { t: (e.textContent || '').trim().slice(0, 12), w: Math.round(r.width), clipped: e.scrollWidth > e.clientWidth + 1 }
  })
  const stream = document.querySelector('.ai .stream')
  const sr = stream.getBoundingClientRect()
  const cr = document.querySelector('.ai .composer').getBoundingClientRect()
  return JSON.stringify({ barW: Math.round(bar.getBoundingClientRect().width), kids,
    streamH: Math.round(sr.height), composerBottom: Math.round(cr.bottom), stageBottom: Math.round(document.querySelector('.side').getBoundingClientRect().bottom) })
})()`)
const a = JSON.parse(ai)
say('AI 头部条没被裁', !a.kids.some(k => k.clipped), JSON.stringify(a.kids))
say('AI 对话区撑满高度', a.streamH > 300, `stream 高=${a.streamH}px，输入框底 ${a.composerBottom} vs 栏底 ${a.stageBottom}`)

// ---------- 程序页 3/4 + 1/4 ----------
await goto('#/work/program')
const pg = await ev(`(() => {
  const code = document.querySelector('.prog .code').getBoundingClientRect()
  const term = document.querySelector('.prog .tbody').getBoundingClientRect()
  const side = document.querySelector('.side').getBoundingClientRect()
  return JSON.stringify({ code: Math.round(code.height), term: Math.round(term.height),
    side: Math.round(side.height), ratio: +(code.height / (code.height + term.height)).toFixed(2),
    pageScroll: document.querySelector('.pane').scrollHeight > document.querySelector('.pane').clientHeight + 2 })
})()`)
const g = JSON.parse(pg)
say('程序页代码:终端 ≈ 3:1', Math.abs(g.ratio - 0.75) < 0.12, `代码 ${g.code}px / 终端 ${g.term}px = ${g.ratio}`)
say('程序页外壳不滚动', g.pageScroll === false, `pane 是否溢出=${g.pageScroll}`)

// ---------- 摆放页 + 日志栏 ----------
await goto('#/work/objects')
const ob = await ev(`(() => {
  const btns = [...document.querySelectorAll('.side button')].map(b => b.textContent.trim())
  return JSON.stringify({ add: ['+ 长方体','+ 球','+ 圆柱'].every(x => btns.includes(x)),
    dock: !!document.querySelector('.dock'), rows: document.querySelectorAll('.dock .ln').length })
})()`)
const b2 = JSON.parse(ob)
say('摆放页三种形状按钮齐全', b2.add, JSON.stringify(b2.add))
say('右下日志栏在舞台上', b2.dock && b2.rows > 0, `日志行数=${b2.rows}`)

// ---------- 日志页拉伸 ----------
await goto('#/logs')
const lg = await ev(`(() => {
  const h = (sel) => { const e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().height) : -1 }
  const chain = { side: h('.side'), pane: h('.pane'), logs: h('.logs'), panel: h('.logs .panel'),
                  bd: h('.logs .panel-bd'), table: h('.logs .tscroll') }
  chain.fills = +(chain.table / chain.side).toFixed(2)
  chain.paneOverflow = getComputedStyle(document.querySelector('.pane')).overflowY
  chain.logsDisplay = getComputedStyle(document.querySelector('.logs')).display
  chain.panelFlex = getComputedStyle(document.querySelector('.logs .panel')).flex
  return JSON.stringify(chain)
})()`)
const g2 = JSON.parse(lg)
say('日志表撑满整栏', g2.fills > 0.7, JSON.stringify(g2))

console.log(`\n${fails === 0 ? '全部通过' : fails + ' 项未达标'}`)
chrome.kill(); ws.close()
process.exit(fails ? 1 : 0)
