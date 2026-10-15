// Run against an isolated backend with HUYAN_NO_REAL=1, never the operator port.
import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'

const base = process.argv[2] || 'http://127.0.0.1:8711'
assert.equal(new URL(base).hostname, '127.0.0.1')
assert.notEqual(new URL(base).port, '8710', 'Use an isolated backend, not the operator session')
const state = await (await fetch(base+'/api/state')).json()
assert.equal(state.mode,'sim')
const setupPost=async(path,body)=>(await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()
await setupPost('/api/speed',{pct:100})
await setupPost('/api/preview',{joints:null})
assert.ok((await setupPost('/api/movej',{joints:[0,0,0,0,0,0]})).ok)
for(let i=0;i<400;i++) {
  const s=await(await fetch(base+'/api/state')).json()
  if(s.joints.q.every(v=>Math.abs(v)<.001)){await new Promise(r=>setTimeout(r,100));break}
  await new Promise(r=>setTimeout(r,25))
  assert.ok(i<399,'Isolated simulation must reach initial pose')
}
const port=9347
const chrome=spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',[
  '--headless=new','--no-sandbox','--disable-gpu','--enable-unsafe-swiftshader',
  '--use-angle=swiftshader','--window-size=1560,900',`--remote-debugging-port=${port}`,
  `--user-data-dir=${process.env.TEMP}/hy-cartesian-browser-${process.pid}`,'about:blank'],{stdio:'ignore'})
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
let ws
try {
  let page
  for(let i=0;i<60&&!page;i++) {
    await sleep(150)
    try { page=(await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(p=>p.type==='page') } catch {}
  }
  assert.ok(page,'Browser must launch')
  ws=new WebSocket(page.webSocketDebuggerUrl)
  await new Promise(r=>ws.onopen=r)
  let id=0
  const pending=new Map()
  ws.onmessage=e=>{ const m=JSON.parse(e.data);if(m.id) {pending.get(m.id)?.(m);pending.delete(m.id)} }
  const send=(method,params={})=>new Promise((resolve,reject)=>{
    const i=++id
    pending.set(i,m=>m.error?reject(new Error(JSON.stringify(m.error))):resolve(m.result))
    ws.send(JSON.stringify({id:i,method,params}))
  })
  const ev=async expression=>{
    const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true})
    assert.ok(!r.exceptionDetails,JSON.stringify(r.exceptionDetails))
    return r.result?.value
  }
  const wait=async(expression,ms=12000)=>{
    const end=Date.now()+ms
    while(Date.now()<end){if(await ev(expression))return;await sleep(100)}
    throw new Error('Timed out: '+expression)
  }
  const slide=async(axis,value)=>ev(`(()=>{const e=document.querySelectorAll('.cart-row input[type=range]')[${axis}];e.value=${value};e.dispatchEvent(new Event('input',{bubbles:true}));return e.value})()`)
  await send('Page.enable');await send('Runtime.enable')
  await send('Page.navigate',{url:base+'/#/work/points'})
  await wait("!!document.querySelector('.ax input[type=range]')")
  await wait("![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='笛卡尔').disabled")
  await ev(`window.__cartErrors=[];window.addEventListener('error',e=>window.__cartErrors.push(e.message))`)
  await ev(`(()=>{[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='笛卡尔').click()})()`)
  await wait("document.querySelectorAll('.cart-row input[type=range]').length===6 && !document.querySelector('.cart-row input[type=range]').disabled")
  await slide(2,1180)
  await wait("document.querySelector('.cart-actions').textContent.includes('逆解已收敛')")
  const solved=await ev(`(async()=>{const s=await(await fetch('/api/state')).json();return {q:s.joints.q,preview:s.preview,values:[...document.querySelectorAll('.cart-row .tgtv')].map(e=>+e.value),ghost:window.__elfinScene?.ghostInfo()}})()`)
  assert.ok(solved.preview,'Valid Cartesian target must load a ghost')
  assert.ok(Math.abs(solved.values[2]-1180)<.1)
  assert.deepEqual(solved.q,[0,0,0,0,0,0],'Preview must not move the simulated actual robot')
  assert.ok(solved.ghost?.visible,'3D ghost must be visible')
  console.log('PASS Cartesian retract solves, ghost visible, actual joints unchanged')

  // Delay the first response, then change the target before it arrives.
  await ev(`(()=>{const f=window.fetch.bind(window);let first=true;window.__ikActive=0;window.__ikMax=0;window.__ikGoals=[];window.fetch=async(...a)=>{if(a[0]!='/api/ik')return f(...a);window.__ikActive++;window.__ikMax=Math.max(window.__ikMax,window.__ikActive);window.__ikGoals.push(JSON.parse(a[1].body).target);const r=await f(...a);if(first){first=false;await new Promise(r=>setTimeout(r,500))}window.__ikActive--;return r}})()`)
  await slide(2,1170)
  await wait('window.__ikActive===1')
  await slide(2,1150)
  await wait("document.querySelector('.cart-actions').textContent.includes('逆解已收敛') && window.__ikActive===0 && window.__ikGoals.length>=2")
  const rapid=await ev(`({max:window.__ikMax,z:+document.querySelectorAll('.cart-row .tgtv')[2].value})`)
  assert.equal(rapid.max,1,'IK requests must not run concurrently')
  assert.ok(Math.abs(rapid.z-1150)<.1,'Newest drag must win')
  console.log('PASS rapid drag serialized; stale response discarded; latest target wins')

  await slide(0,1000)
  await wait("!document.querySelector('.cart-actions').textContent.includes('求解中')")
  const boundary=await ev(`(async()=>{const s=await(await fetch('/api/state')).json();const r=await(await fetch('/api/fk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({joints:s.preview})})).json();return {values:[...document.querySelectorAll('.cart-row .tgtv')].map(e=>+e.value),pose:r.xyz_mm.concat(r.rpy_deg),text:document.querySelector('.cart-sliders').textContent}})()`)
  assert.ok(boundary.values[0]<1000,'Invalid target must not remain in the controls')
  for(let i=0;i<6;i++)assert.ok(Math.abs(boundary.values[i]-boundary.pose[i])<=.6)
  assert.ok(boundary.text.includes('有效目标'))
  console.log('PASS boundary shows solved target; controls match ghost FK')

  // Execute a larger Cartesian retract at 100% so the UI test finishes quickly.
  await ev(`(async()=>{await fetch('/api/speed',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pct:100})});[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='回到实际').click()})()`)
  await slide(2,1040)
  await wait("document.querySelector('.cart-actions').textContent.includes('逆解已收敛')")
  await wait("![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='执行目标').disabled")
  const target=(await(await fetch(base+'/api/state')).json()).preview
  await ev(`(()=>{[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='执行目标').click()})()`)
  const samples=[]
  for(let i=0;i<120;i++) {
    const s=await(await fetch(base+'/api/state')).json()
    samples.push({q:s.joints.q,t:Date.now()})
    if(samples.length>5 && s.joints.q.some(v=>Math.abs(v)>1) && !s.preview)break
    await sleep(25)
  }
  const motion=samples.filter((s,i)=>i>0&&s.q.some((v,j)=>Math.abs(v-samples[i-1].q[j])>.002))
  assert.ok(motion.length>5,'Motion must provide a continuous sequence of poses')
  const live=motion.find(s=>s.q.filter(v=>Math.abs(v)>.1).length>=2)
  assert.ok(live,'Multiple joints must move together')
  const dominant=target.reduce((a,v,i)=>Math.abs(v)>Math.abs(target[a])?i:a,0)
  const middle=samples.filter(s=>Math.abs(s.q[dominant]/target[dominant])>.1&&Math.abs(s.q[dominant]/target[dominant])<.9)
  let stationary=0,maxStationary=0
  for(let i=1;i<middle.length;i++) {
    stationary=middle[i].q.every((v,j)=>Math.abs(v-middle[i-1].q[j])<.001)?stationary+1:0
    maxStationary=Math.max(maxStationary,stationary)
  }
  assert.ok(maxStationary<4,'No internal stop lasting 100ms or more')
  const errors=await ev('window.__cartErrors')
  assert.deepEqual(errors,[])
  console.log('PASS simulated execution moves several joints together; no page errors')
  await mkdir('output/cartesian-fix',{recursive:true})
  await writeFile('output/cartesian-fix/ui-verification.json',JSON.stringify({checks:4,maxConcurrentIK:rapid.max,motionSamples:motion.length,maxStationarySamples:maxStationary,errors},null,2))
  const shot=await send('Page.captureScreenshot',{format:'png'})
  await writeFile('output/cartesian-fix/verified.png',Buffer.from(shot.data,'base64'))
} finally {ws?.close();chrome.kill()}
