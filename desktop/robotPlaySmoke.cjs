// 明确禁用真机的独立桌面实例；真实仿真时间推进，不替换关节/物体更新。
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function waitForPrecheck(read,until){
  let status
  try{await until(async()=>{status=await read();return status.available},'正式预检按钮恢复可用',15)}
  catch(error){throw Error(error.message+'；按钮说明：'+(status?.title||'无')+'；当前状态：'+(status?.guide||'尚无界面状态'))}
  return status
}
async function verifyRobotPlay({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const result=report.robotPlay={completed:false,criterion:'color',cleanupErrors:[]},created=new Set(),evaluate=script=>window.webContents.executeJavaScript(script)
  const request=(url,payload)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(url)},{signal:AbortSignal.timeout(10000),cache:'no-store',...(${JSON.stringify(payload)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(payload)})})});const v=await r.json();if(!r.ok||v.ok===false)throw new Error(v.why||v.detail||'HTTP '+r.status);return v})()`)
  async function until(check,label,seconds=90){const end=Date.now()+seconds*1000;while(Date.now()<end){if(await check())return;await new Promise(resolve=>setTimeout(resolve,100))}throw new Error('取放验收超时：'+label)}
  let vision,failure
  try{
    const state=await request('/api/state',null);assert.equal(state.mode,'sim');assert.equal(state.real,null)
    await evaluate(`window.confirm=()=>true;document.querySelector('nav button[title="分拣"]').click()`)
    await wait(()=>evaluate(`!!document.querySelector('#functions-tab-sorting')`))
    await evaluate(`document.querySelector('#functions-tab-sorting').click()`)
    await wait(()=>evaluate(`!![...document.querySelectorAll('#functions-panel select')].find(el=>[...el.options].some(o=>o.value==='shape'))`))
    await evaluate(`(()=>{const select=[...document.querySelectorAll('#functions-panel select')].find(el=>[...el.options].some(o=>o.value==='shape'));select.value='color';select.dispatchEvent(new Event('change',{bubbles:true}))})()`)
    await wait(()=>evaluate(`document.querySelectorAll('.piece-counts input').length===3`))
    await evaluate(`(()=>{const fields=document.querySelectorAll('.piece-counts input');[1,1,1].forEach((value,i)=>{fields[i].value=String(value);fields[i].dispatchEvent(new Event('input',{bubbles:true}))});const button=[...document.querySelectorAll('#functions-panel button')].find(el=>el.textContent.trim()==='布置分拣料');if(!button||button.disabled)throw Error('正式布置分拣料入口不可用');button.click()})()`)
    let setup,pieces
    await until(async()=>{setup=await request('/api/state',null);pieces=setup.objects.filter(item=>item.task?.mode==='sorting'&&item.task.role==='piece');return pieces.length===3},'UI布置红绿蓝各一个工件',10)
    for(const item of setup.objects)if(item.task?.mode==='sorting')created.add(item.id)
    const initial=new Map(pieces.map(piece=>[piece.id,piece.xyz.slice()]))
    assert.equal(new Set(pieces.map(piece=>piece.color)).size,3)
    vision=await require('./sortingVisionSmoke.cjs').prepareSortingVision({window,request,until,directory,report,colors:['red','green','blue'],expectedShape:'rectangle'})
    await wait(()=>evaluate(`!!document.querySelector('.robot-play-program')`))
    await evaluate(`(()=>{const speed=document.querySelectorAll('.robot-play-program input[type="number"]')[1];speed.value='100';speed.dispatchEvent(new Event('input',{bubbles:true}))})()`)
    await waitForPrecheck(async()=>{
        result.precheckEntry=await evaluate(`(()=>{const button=[...document.querySelectorAll('.robot-play-program button')].find(el=>el.textContent.trim()==='预检生成');return {available:!!button&&!button.disabled,title:button?.title||'',guide:[...document.querySelectorAll('#functions-panel .note[role="status"]')].map(el=>el.textContent).join('；')}})()`)
        return result.precheckEntry
    },until)
    await evaluate(`(()=>{const button=[...document.querySelectorAll('.robot-play-program button')].find(el=>el.textContent.trim()==='预检生成');if(!button||button.disabled)throw Error('预检入口已变化：'+(button?.title||'')+'；'+[...document.querySelectorAll('#functions-panel .note[role="status"]')].map(el=>el.textContent).join('；'));button.click()})()`)
    await until(async()=>{
      const server=await request('/api/play/program/state',null)
      const ui=await evaluate(`({text:document.querySelector('.robot-play-program').textContent,source:document.querySelector('.robot-play-program textarea').value,ready:[...document.querySelectorAll('.robot-play-program button')].some(el=>el.textContent.trim()==='执行程序'&&!el.disabled),idle:[...document.querySelectorAll('.robot-play-program button')].some(el=>el.textContent.trim()==='预检生成'&&!el.disabled)})`)
      result.planning={progress:server.progress,message:server.message,ui:ui.text.slice(-1000)}
      if(!server.planning && !ui.ready && ui.idle)throw new Error('正式预检被拒绝：'+ui.text.slice(-1000))
      result.source=ui.source;return ui.ready
    },'正式UI完整预检',110)
    assert.match(result.source,/SIM_PICK /);assert.match(result.source,/TOOL BLOW_OFF/)
    result.visionBeforeRun=await vision.assertFresh()
    const before=await request('/api/state',null);result.startTcp=before.tcp
    await evaluate(`window.confirm=()=>true;[...document.querySelectorAll('.robot-play-program button')].find(el=>el.textContent.trim()==='执行程序').click()`)
    const carried=new Set(),changed=new Set();let last
    await until(async()=>{
      const service=await request('/api/play/program/state',null),state=await request('/api/state',null)
      if(['aborted','failed','error'].includes(state.exec.state))throw new Error('取放中止：'+state.exec.error)
      if(service.held && initial.has(service.held)){
        const object=state.objects.find(item=>item.id===service.held)
        assert.ok(object,'持件必须仍在正式场景');carried.add(object.id)
        if(Math.hypot(...object.xyz.map((value,index)=>value-initial.get(object.id)[index]))>20)changed.add(object.id)
      }
      if(changed.size && !result.carryScreenshot){fs.writeFileSync(path.join(directory,'robot-carry.png'),(await window.webContents.capturePage()).toPNG());result.carryScreenshot=true}
      last={service,state};result.carryProgress={held:[...carried],moved:[...changed]}
      if(service.sorting_score?.status==='failed')throw Error('真实放置未全部完成：'+service.sorting_score.reason)
      return carried.size===3 && changed.size===3 && state.exec.state==='finished' && service.sorting_score?.status==='completed'
    },'红绿蓝三件真实携带取放完成',300)
    assert.equal(carried.size,3,'每件均须实际持取');assert.equal(changed.size,3,'每件均须随机器人实际移动')
    const placed=last.state.objects.filter(item=>initial.has(item.id))
    assert.equal(placed.length,3);assert.equal(new Set(placed.map(item=>item.task.slot)).size,3)
    for(const item of placed){assert.ok(item.task.slot>=0);assert.ok(item.xyz[1]>200);assert.ok(carried.has(item.id)&&changed.has(item.id))}
    result.placements=placed.map(item=>({id:item.id,color:item.color,slot:item.task.slot,xyz:item.xyz}))
    assert.equal(last.service.held,null);assert.deepEqual(last.service.vacuum,{vacuum:false,blow:false})
    assert.ok(Math.hypot(...last.state.tcp.xyz_mm.map((value,index)=>value-[400,0,450][index]))<1)
    const score=last.service.sorting_score;assert.ok(score,'执行后必须有正式成绩');assert.equal(score.status,'completed');assert.equal(score.criterion,'color');assert.equal(score.total,3);assert.equal(score.completed,3);assert.equal(score.correct,3);assert.equal(score.accuracy_pct,100);assert.equal(score.score,100);assert.ok(score.elapsed_s>0)
    result.score=score;result.carried=true;result.returnedToWait=true;result.finished=true
    // The UI polls every 700ms; the smoke's 100ms API observation may see the
    // terminal score first. Wait for actual UI readback, never inject its state.
    await until(()=>evaluate(`(()=>{const score=[...document.querySelectorAll('.robot-play-program [role="status"]')].find(el=>el.textContent.includes('分拣完成'));return !!score && score.textContent.includes('100')})()`),'UI成绩正式读回并显示100分',5)
    result.uiScoreText=await evaluate(`(()=>{const score=[...document.querySelectorAll('.robot-play-program [role="status"]')].find(el=>el.textContent.includes('分拣完成'));score.scrollIntoView({block:'center'});return score.textContent})()`)
    fs.writeFileSync(path.join(directory,'robot-placed.png'),(await window.webContents.capturePage()).toPNG())
    result.completed=true
  }catch(error){failure=error;result.completed=false;result.error=error.stack || String(error);try{fs.writeFileSync(path.join(directory,'sorting-failure.png'),(await window.webContents.capturePage()).toPNG())}catch{}}
  finally{
    const cleanup=async(label,action)=>{try{await action()}catch(error){result.cleanupErrors.push(label+': '+error.message)}}
    await cleanup('停止程序',()=>request('/api/program/stop',{}))
    await cleanup('释放仿真持件',()=>request('/api/play/program/release',{}))
    if(vision)await cleanup('停止测试视觉',()=>vision.stop())
    await cleanup('删除本次任务物体',async()=>{const remaining=await request('/api/objects',null);for(const item of remaining.items)if(created.has(item.id))await request('/api/objects/remove',{id:item.id})})
  }
  if(failure)throw failure
  if(result.cleanupErrors.length)throw Error('分拣验收清理失败：'+result.cleanupErrors.join('；'))
}
module.exports={verifyRobotPlay,waitForPrecheck}
