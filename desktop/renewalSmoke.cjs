// Explicit isolated desktop acceptance, using real UI and simulation execution.
const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
function manualArrived(current,accepted,planId,target){
  const motion=current.exec?.execution,binding=accepted.execution
  assert.ok(binding&&Number.isFinite(binding.started),'手动运动必须返回本次执行身份')
  assert.ok(motion&&motion.epoch===binding.epoch&&motion.started===binding.started,'本次手动运动被替换或撤销')
  if(['error','failed','aborted','cancelled'].includes(motion.state))throw Error(motion.error||'非零起点运动失败')
  const matches=current.joints?.q?.every((v,i)=>Number.isFinite(v)&&Math.abs(v-target[i])<.1)
  return matches&&!current.moving&&motion.state==='completed'&&!motion.busy&&current.target_evidence?.plan_id===planId&&current.target_evidence.reached===true
}
async function verifyRenewal({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const result=report.renewal={completed:false}
  const evaluate=script=>window.webContents.executeJavaScript(script)
  const request=(route,body=null)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(route)},{cache:'no-store',...(${JSON.stringify(body)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(body)})})});const value=await r.json();if(!r.ok||value.ok===false)throw Error(value.why||value.detail||'HTTP '+r.status);return value})()`)
  async function until(check,label,seconds=180){
    const deadline=Date.now()+seconds*1000
    while(Date.now()<deadline){if(await check())return;await new Promise(resolve=>setTimeout(resolve,100))}
    throw new Error('完整界面流程超时：'+label)
  }
  const click=async label=>{
    await until(()=>evaluate(`(()=>{const b=[...document.querySelectorAll('#functions-panel button')].find(b=>b.textContent.trim().startsWith(${JSON.stringify(label)}));return !!b && !b.disabled && b.getClientRects().length>0})()`),'按钮可用：'+label,15)
    return evaluate(`(()=>{const b=[...document.querySelectorAll('#functions-panel button')].find(b=>b.textContent.trim().startsWith(${JSON.stringify(label)}));if(!b||b.disabled)throw new Error('按钮不可用：'+${JSON.stringify(label)});b.click()})()`)
  }
  const occupied=()=>evaluate(`[...document.querySelectorAll('.chess-board button')].filter(b=>b.textContent.trim()).length`)
  const state=await request('/api/state');assert.equal(state.mode,'sim');assert.equal(state.real,null)
  // Keep the real simulation clock and path checks. Select the ordinary UI
  // speed control explicitly, so this long workflow does not inherit 10%.
  await evaluate(`(()=>{const speed=document.querySelector('.top input[type="range"]');if(!speed||speed.disabled)throw Error('仿真倍率不可用');speed.value='100';speed.dispatchEvent(new Event('input',{bubbles:true}));speed.dispatchEvent(new Event('change',{bubbles:true}))})()`)
  await until(async()=>{const s=await request('/api/state');return s.settings?.sim_speed_pct===100 || s.speed?.actual_pct===100},'仿真倍率回读',10)
  const initialQ=[101.629,-10.347,-127.27,-155.56,-29.154,-21.647]
  // Reproduce a previous task's stopped Runner before the independent manual
  // motion. Only this MOVEJ's current execution and arrival proof may decide it.
  await request('/api/program/stop',{})
  const limits=(await request('/api/state')).joints
  assert.ok(limits && Array.isArray(limits.lower)&&Array.isArray(limits.upper),'必须有正式六轴限位')
  assert.ok(initialQ.every((v,i)=>Number.isFinite(limits.lower[i])&&Number.isFinite(limits.upper[i])&&v>=limits.lower[i]&&v<=limits.upper[i]),'非零起点必须在真实模型限位内')
  const preview=await request('/api/preview',{joints:initialQ})
  assert.ok(preview.plan_id,'非零起点必须经正式预览产生计划票据')
  assert.notEqual(preview.executable,false,'非零起点不得绕过不可执行预览')
  const accepted=await request('/api/movej',{joints:initialQ,plan_id:preview.plan_id})
  assert.notEqual(accepted.accepted,false,'非零起点运动必须被正式接受')
  await until(async()=>{
    const current=await request('/api/state');assert.equal(current.mode,'sim');assert.equal(current.real,null)
    const evidence=current.target_evidence
    if(manualArrived(current,accepted,preview.plan_id,initialQ)){result.initialPosture={requested:initialQ,actual:current.joints.q,plan_id:preview.plan_id,evidence};return true}
    return false
  },'正式运动到用户非零手动姿态并确认到达',120)
  await evaluate(`window.__smokeChessRandom=Math.random;Math.random=()=>0;document.querySelector('nav button[title="有趣"]').click()`)
  try { await wait(()=>evaluate(`!!document.querySelector('#functions-tab-chess')`)) }
  finally { await evaluate(`Math.random=window.__smokeChessRandom;delete window.__smokeChessRandom`) }
  await evaluate(`(()=>{document.querySelector('#functions-tab-chess').click();window.confirm=()=>true})()`)
  await click('布置棋局')
  await until(async()=>{const value=await request('/api/objects');return value.items.filter(o=>o.task?.mode==='chess').length===11},'三维棋局布置',15)
  const originals=new Map((await request('/api/objects')).items.filter(o=>o.task?.mode==='chess'&&o.task.role==='piece').map(o=>[o.id,o.xyz.slice()]))
  result.chessSetup=true
  await click('启动一局')
  await until(async()=>{
    const text=await evaluate(`document.querySelector('#functions-panel').textContent`)
    if(text.includes('已锁定新操作'))throw new Error(text.slice(-900))
    return evaluate(`[...document.querySelectorAll('.chess-board button')].some(b=>!b.disabled)`)
  },'随机先手与首个可下回合')
  const before=await occupied();assert.equal(before,0,'固定人类O先行时机器人不得提前落子')
  assert.ok(await evaluate(`document.querySelector('#functions-panel').textContent.includes('轮到你（O）')`),'必须实际人类O回合')
  assert.ok(await evaluate(`document.querySelector('#functions-panel').textContent.includes('离开本页会取消本局，已放棋子保留。')`),'活跃棋局必须事前显示离页取消和保留棋子的后果')
  await evaluate(`(()=>{const b=document.querySelectorAll('.chess-board button')[4];if(!b||b.disabled)throw Error('第5格当前不可下');b.click()})()`)
  const carried=new Set(),moved=new Set()
  await until(async()=>{
    const status=await request('/api/play/program/state')
    if(status.held){const current=(await request('/api/objects')).items.find(o=>o.id===status.held);assert.ok(current&&originals.has(current.id),'持件必须属于正式棋局');carried.add(current.id);if(Math.hypot(...current.xyz.map((v,i)=>v-originals.get(current.id)[i]))>20)moved.add(current.id)}
    const text=await evaluate(`document.querySelector('#functions-panel').textContent`)
    if(text.includes('已锁定新操作'))throw new Error(text.slice(-900))
    return (await occupied())>=before+2 && !status.active && !status.planning && !status.held &&
      await evaluate(`[...document.querySelectorAll('.chess-board button')].some(b=>!b.disabled)`)
  },'人类和AI逐枚真实仿真取放',480)
  const objects=(await request('/api/objects')).items
  const placed=objects.filter(o=>o.task?.mode==='chess'&&o.task?.role==='piece'&&o.task.slot>=0)
  assert.equal(placed.length,await occupied());assert.equal(placed.length,2)
  assert.ok(placed.some(o=>o.task.symbol==='O'&&o.task.slot===4),'人类O必须实际放第5格')
  assert.ok(placed.some(o=>o.task.symbol==='X'),'AI必须实际放X')
  for(const piece of placed){assert.ok(carried.has(piece.id),'每枚棋子必须观察正式持件');assert.ok(moved.has(piece.id),'每枚棋子必须观察实际携带移动')}
  result.chessCarried=[...carried];result.chessMoved=[...moved]
  result.chessPlaced=placed.length
  await until(()=>evaluate(`document.querySelector('#functions-panel').textContent.includes('轮到你（O）')`),'双方棋子已投影且恢复人类回合',10)
  await evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`)
  fs.writeFileSync(path.join(directory,'chess-real-simulation.png'),(await window.webContents.capturePage()).toPNG())
  await click('取消本局及取放')
  await until(()=>evaluate(`[...document.querySelectorAll('.chess-board button')].every(b=>b.disabled)`),'取消后棋盘闭锁',15)
  const afterCancel=await occupied();assert.equal(afterCancel,placed.length)
  result.chessCancelledPreservingConfirmedMoves=true
  await click('棋子回 A 原料区')
  await until(async()=>{const value=await request('/api/objects');return value.items.filter(o=>o.task?.mode==='chess'&&o.task?.role==='piece'&&o.task.slot===-1).length===9},'棋局复位',15)
  result.chessReset=true
  await require('./robotPlaySmoke.cjs').verifyRobotPlay({window,directory,report,wait})
  result.completed=true
}
module.exports={verifyRenewal,manualArrived}
