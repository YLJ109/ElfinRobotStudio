// Read/export actual persisted runs only. Never creates a run or resumes motion.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyJournalUi({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const result=report.journalUi={completed:false},evaluate=script=>window.webContents.executeJavaScript(script)
  const state=await evaluate(`fetch('/api/state',{cache:'no-store'}).then(r=>r.json())`)
  assert.equal(state.mode,'sim');assert.equal(state.real,null);assert.notEqual(state.exec?.state,'running')
  const records=await evaluate(`fetch('/api/runs',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('运行记录HTTP '+r.status);return r.json()})`)
  assert.equal(records.analysis_only,true);assert.ok(Array.isArray(records.runs))
  result.run_ids=records.runs.map(row=>row.run_id)
  await evaluate(`document.querySelector('nav button[title="系统"]').click()`)
  await wait(()=>evaluate(`!!document.querySelector('#system-tab-engineering')`))
  await evaluate(`document.querySelector('#system-tab-engineering').click()`)
  await wait(()=>evaluate(`!!document.querySelector('#tools-tab-diagnostics')`))
  await evaluate(`document.querySelector('#tools-tab-diagnostics').click()`)
  await wait(()=>evaluate(`!!document.querySelector('.run-journal')`))
  if(!records.runs.length){
    await wait(()=>evaluate(`document.querySelector('.run-journal').textContent.includes('暂无运行记录')`))
    result.emptyStateVisible=true;throw Error('实际持久运行记录为空，不能声称棋局/分拣记录已验收')
  }
  assert.ok(records.runs.some(row=>row.context?.action==='chess' && row.confirmed_steps>0),'缺少真实棋局确认步骤')
  assert.ok(records.runs.some(row=>row.context?.action==='sorting' && row.confirmed_steps>0),'缺少真实分拣确认步骤')
  await wait(()=>evaluate(`document.querySelectorAll('.run-journal .run-record').length===${records.runs.length} && !document.querySelector('.run-journal button').disabled`))
  result.visibleRows=await evaluate(`[...document.querySelectorAll('.run-journal .run-record')].map(row=>row.textContent)`)
  records.runs.forEach((row,index)=>assert.ok(result.visibleRows[index].includes(row.confirmed_steps+' 步已确认'),'UI确认步骤与正式记录不符'))
  assert.ok(result.visibleRows.some(text=>text.includes('井字棋')));assert.ok(result.visibleRows.some(text=>text.includes('分拣')))
  for(const [kind,label] of [['kit','配套装盒'],['pattern','图案拼摆']])if(records.runs.some(row=>row.context?.action===kind))assert.ok(result.visibleRows.some(text=>text.includes(label)),'运行记录必须显示真实任务名称：'+label)
  const target=path.join(directory,'journal-ui-export.json'),session=window.webContents.session
  let handler,timer
  const downloaded=new Promise((resolve,reject)=>{
    timer=setTimeout(()=>reject(Error('运行记录UI导出等待超时')),15000)
    handler=(_event,item,webContents)=>{
      if(webContents && webContents.id!==window.webContents.id)return
      try{assert.equal(item.getFilename(),'运行记录.json');item.setSavePath(target)}catch(error){item.cancel();reject(error);return}
      item.once('done',(_event,state)=>state==='completed'?resolve():reject(Error('运行记录下载未完成：'+state)))
    }
    session.on('will-download',handler)
  })
  try{
    await evaluate(`(()=>{const first=document.querySelector('.run-journal .run-record');first.scrollIntoView({block:'center'});const button=[...first.querySelectorAll('button')].find(el=>el.textContent.trim()==='导出');if(!button||button.disabled)throw Error('首条运行记录导出不可用');button.click()})()`)
    await downloaded
  }finally{clearTimeout(timer);session.removeListener('will-download',handler)}
  const exported=JSON.parse(fs.readFileSync(target,'utf8'))
  assert.equal(exported.analysis_only,true);assert.equal(exported.record.run_id,records.runs[0].run_id)
  result.exportedRunId=exported.record.run_id;result.analysisOnly=true;result.exportFile=target
  fs.writeFileSync(path.join(directory,'journal-ui.png'),(await window.webContents.capturePage()).toPNG())
  result.completed=true
}
module.exports={verifyJournalUi}
