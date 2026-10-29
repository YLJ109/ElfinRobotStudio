// 明确禁用真机的独立桌面实例；真实仿真时间推进，不替换关节/物体更新。
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyRobotPlay({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const result=report.robotPlay={completed:false},created=new Set(),evaluate=script=>window.webContents.executeJavaScript(script)
  const request=(url,payload)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(url)},{signal:AbortSignal.timeout(10000),cache:'no-store',...(${JSON.stringify(payload)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(payload)})})});const v=await r.json();if(!r.ok||v.ok===false)throw new Error(v.why||v.detail||'HTTP '+r.status);return v})()`)
  async function until(check,label,seconds=90){const end=Date.now()+seconds*1000;while(Date.now()<end){if(await check())return;await new Promise(resolve=>setTimeout(resolve,100))}throw new Error('取放验收超时：'+label)}
  try{
    const state=await request('/api/state',null);assert.equal(state.mode,'sim');assert.equal(state.real,null)
    const setup=await request('/api/play/setup',{mode:'sorting',counts:{red:1,green:0,blue:0}})
    for(const item of setup.objects)if(item.task)created.add(item.id)
    const piece=setup.objects.find(item=>item.task?.role==='piece'),initial=piece.xyz
    await evaluate(`document.querySelector('nav button[title="功能"]').click()`)
    await wait(()=>evaluate(`!!document.querySelector('#functions-tab-sorting')`))
    await evaluate(`document.querySelector('#functions-tab-sorting').click()`)
    await wait(()=>evaluate(`!!document.querySelector('.robot-play-program')`))
    await evaluate(`(()=>{const speed=document.querySelectorAll('.robot-play-program input[type="number"]')[1];speed.value='100';speed.dispatchEvent(new Event('input',{bubbles:true}));const b=[...document.querySelectorAll('.robot-play-program button')].find(el=>el.textContent.trim()==='预检生成');if(b.disabled)throw new Error('预检按钮不可用');b.click()})()`)
    await until(async()=>{
      const server=await request('/api/play/program/state',null)
      const ui=await evaluate(`({text:document.querySelector('.robot-play-program').textContent,source:document.querySelector('.robot-play-program textarea').value,ready:[...document.querySelectorAll('.robot-play-program button')].some(el=>el.textContent.trim()==='执行程序'&&!el.disabled)})`)
      if(!server.planning && !ui.ready && !ui.text.includes('预检中') && ui.text.includes('失败'))throw new Error(ui.text.slice(-600))
      result.source=ui.source;return ui.ready
    },'完整预检')
    assert.match(result.source,/SIM_PICK /);assert.match(result.source,/TOOL BLOW_OFF/)
    const before=await request('/api/state',null);result.startTcp=before.tcp
    await evaluate(`window.confirm=()=>true;[...document.querySelectorAll('.robot-play-program button')].find(el=>el.textContent.trim()==='执行程序').click()`)
    let carried=false,changed=false,last
    await until(async()=>{
      const service=await request('/api/play/program/state',null),state=await request('/api/state',null)
      if(['aborted','failed'].includes(state.exec.state))throw new Error('取放中止：'+state.exec.error)
      const object=state.objects.find(item=>item.id===piece.id)
      if(service.held){carried=true;if(Math.hypot(...object.xyz.map((value,index)=>value-initial[index]))>20)changed=true}
      if(carried && changed && !result.carryScreenshot){fs.writeFileSync(path.join(directory,'robot-carry.png'),(await window.webContents.capturePage()).toPNG());result.carryScreenshot=true}
      last={service,state,object};return state.exec.state==='finished'
    },'真实时间的机器人取放',70)
    assert.ok(carried && changed,'必须看到工件随真实仿真关节运动，而非瞬移')
    assert.equal(last.object.task.slot,0);assert.ok(last.object.xyz[1]>200)
    assert.equal(last.service.held,null);assert.deepEqual(last.service.vacuum,{vacuum:false,blow:false})
    assert.ok(Math.hypot(...last.state.tcp.xyz_mm.map((value,index)=>value-[400,0,450][index]))<1)
    result.carried=true;result.returnedToWait=true;result.finished=true;result.completed=true
    fs.writeFileSync(path.join(directory,'robot-placed.png'),(await window.webContents.capturePage()).toPNG())
  }finally{
    await request('/api/program/stop',{})
    await request('/api/play/program/release',{})
    const remaining=await request('/api/objects',null)
    for(const item of remaining.items)if(created.has(item.id))await request('/api/objects/remove',{id:item.id})
  }
}
module.exports={verifyRobotPlay}
