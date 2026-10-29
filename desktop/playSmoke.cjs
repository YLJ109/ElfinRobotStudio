// 仅隔离smoke使用：移动场景道具，不发送任何机器人运动命令。
const assert=require('node:assert/strict')
const fs=require('node:fs')
const path=require('node:path')
async function verifyPlay({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const checks=report.play={completed:false},created=new Set()
  const evaluate=script=>window.webContents.executeJavaScript(script)
  const request=(route,body)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(route)},{cache:'no-store',signal:AbortSignal.timeout(10000),...( ${JSON.stringify(body)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(body)})})});const v=await r.json();if(!r.ok||v.ok===false)throw new Error(v.detail||v.why||'HTTP '+r.status);return v})()`)
  const remember=items=>{for(const item of items)if(item.task)created.add(item.id)}
  try{
    const state=await request('/api/state',null);assert.equal(state.mode,'sim');assert.equal(state.real,null)
    await evaluate(`document.querySelector('nav button[title="功能"]').click()`)
    await wait(()=>evaluate(`!!document.querySelector('#functions-tab-chess')`))
    await evaluate(`document.querySelector('#functions-tab-chess').click()`)
    await evaluate(`(()=>{const b=[...document.querySelectorAll('#functions-panel button')].find(el=>el.textContent.includes('布置棋局'));if(!b||b.disabled)throw new Error('布置棋局不可用');b.click()})()`)
    let objects
    await wait(async()=>{objects=(await request('/api/objects',null)).items;remember(objects);return objects.filter(item=>item.task?.mode==='chess').length===11})
    checks.chessPieces=objects.filter(item=>item.task?.role==='piece').length;assert.equal(checks.chessPieces,9)
    await wait(()=>evaluate(`!document.querySelector('.chess-board button').disabled`))
    await evaluate(`document.querySelector('.chess-board button').click()`)
    await wait(async()=>{objects=(await request('/api/objects',null)).items;return objects.filter(item=>item.task?.role==='piece'&&item.task.slot>=0).length===2})
    checks.chessMoved=2
    await evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`)
    fs.writeFileSync(path.join(directory,'play-chess.png'),(await window.webContents.capturePage()).toPNG())
    const before=objects.filter(item=>item.task).map(item=>item.id)
    const sorting=await request('/api/play/setup',{mode:'sorting',counts:{red:3,green:3,blue:3}});remember(sorting.objects)
    assert.ok(sorting.objects.every(item=>!before.includes(item.id)),'替换任务模板不能叠加旧棋子')
    await evaluate(`document.querySelector('#functions-tab-sorting').click()`)
    await wait(()=>evaluate(`(()=>{const b=[...document.querySelectorAll('#functions-panel button')].find(el=>el.textContent.includes('分拣到 B'));return b&&!b.disabled})()`))
    await evaluate(`([...document.querySelectorAll('#functions-panel button')].find(el=>el.textContent.includes('分拣到 B'))).click()`)
    await wait(async()=>{objects=(await request('/api/objects',null)).items;return objects.filter(item=>item.task?.role==='piece'&&item.task.slot>=0).length===9})
    await wait(()=>evaluate(`document.querySelector('#functions-panel').textContent.includes('B 区 9 个')`))
    await evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`)
    checks.sorted=9;assert.equal(new Set(objects.filter(item=>item.task?.role==='piece').map(item=>item.color)).size,3)
    fs.writeFileSync(path.join(directory,'play-sorting.png'),(await window.webContents.capturePage()).toPNG())
    await evaluate(`([...document.querySelectorAll('#functions-panel button')].find(el=>el.textContent.trim()==='回 A 原料区')).click()`)
    await wait(async()=>{objects=(await request('/api/objects',null)).items;return objects.filter(item=>item.task?.role==='piece'&&item.task.slot===-1).length===9})
    checks.reset=9;checks.completed=true
  }finally{
    const remaining=(await request('/api/objects',null)).items
    for(const item of remaining)if(created.has(item.id))await request('/api/objects/remove',{id:item.id})
  }
}
module.exports={verifyPlay}
