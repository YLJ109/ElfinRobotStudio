// Explicit three-launch recovery exercise in --smoke-output's private data only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyRecovery({app,window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const marker=path.join(directory,'recovery-stage.json')
  const stage=fs.existsSync(marker)?JSON.parse(fs.readFileSync(marker,'utf8')):{phase:'create'}
  const evaluate=code=>window.webContents.executeJavaScript(code)
  const request=(route,body)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(route)},{cache:'no-store',...(${JSON.stringify(body)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(body)})})});const v=await r.json();if(!r.ok||v.ok===false)throw Error(v.why||v.detail||'HTTP '+r.status);return v})()`)
  const state=await request('/api/state',null);assert.equal(state.mode,'sim');assert.equal(state.real,null)
  async function panel(){
    await evaluate(`document.querySelector('nav button[title="系统"]').click()`)
    await wait(()=>evaluate(`!!document.querySelector('#system-tab-settings')`))
    await evaluate(`document.querySelector('#system-tab-settings').click()`)
    await wait(()=>evaluate(`!!document.querySelector('#settings-tab-profiles')`))
    await evaluate(`document.querySelector('#settings-tab-profiles').click()`)
    await wait(()=>evaluate(`!!document.querySelector('.backup-panel')`))
  }
  const click=label=>evaluate(`(()=>{const b=[...document.querySelectorAll('.backup-panel button')].find(b=>b.textContent.trim()===${JSON.stringify(label)});if(!b||b.disabled)throw Error('恢复按钮不可用：'+${JSON.stringify(label)});b.click()})()`)
  async function screenshot(name){await evaluate(`document.querySelector('.backup-panel')?.scrollIntoView({block:'start'})`);await evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
  report.recovery={phase:stage.phase,completed:false}
  if(stage.phase==='create'){
    await request('/api/programs',{name:'备份保留程序',src:'WAIT 1'})
    await panel()
    const download=path.join(directory,'portable-backup.json')
    let downloaded=false,downloadError=''
    window.webContents.session.once('will-download',(_event,item)=>{item.setSavePath(download);item.once('done',(_event,status)=>{downloaded=status==='completed';if(!downloaded)downloadError=status})})
    await click('导出数据备份')
    await wait(()=>{if(downloadError)throw Error(downloadError);return downloaded})
    assert.equal(JSON.parse(fs.readFileSync(download,'utf8')).format,'elfin-data-backup')
    await request('/api/programs',{name:'备份之后的新程序',src:'WAIT 2'})
    const payload=fs.readFileSync(download,'utf8')
    await evaluate(`(()=>{const input=document.querySelector('.backup-panel input[type=file]');const transfer=new DataTransfer();transfer.items.add(new File([${JSON.stringify(payload)}],'验收备份.json',{type:'application/json'}));input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}))})()`)
    await wait(()=>evaluate(`!!document.querySelector('.backup-diff')`))
    await evaluate(`document.querySelectorAll('.backup-panel details').forEach(el=>el.open=true)`)
    await screenshot('backup-difference.png')
    await click('确认创建恢复副本')
    await wait(()=>evaluate(`document.querySelector('.backup-panel').textContent.includes('恢复副本已创建')`))
    const base=path.join(app.getPath('userData'),'data','recovery')
    const copies=fs.readdirSync(base).filter(name=>/^[a-f0-9]{32}$/.test(name));assert.equal(copies.length,1)
    stage.phase='activated';stage.copy_id=copies[0]
    fs.writeFileSync(marker,JSON.stringify(stage))
    await screenshot('backup-created.png')
    await evaluate(`(()=>{window.confirm=()=>true})()`)
    await click('使用恢复副本并重启')
    report.recovery.relaunching=true
    return {relaunching:true}
  }
  const programs=(await request('/api/programs',null)).items.map(item=>item.name)
  assert.ok(programs.includes('备份保留程序'));assert.ok(!programs.includes('备份之后的新程序'))
  const pointer=JSON.parse(fs.readFileSync(path.join(app.getPath('userData'),'data-selection.json'),'utf8'))
  assert.equal(pointer.copy_id,stage.copy_id)
  if(stage.phase==='activated'){
    await request('/api/programs',{name:'恢复之后保存',src:'WAIT 3'})
    stage.phase='reopened';fs.writeFileSync(marker,JSON.stringify(stage))
    report.recovery.firstRestartVerified=true;report.recovery.relaunching=true
    // A normal second restart must accept legitimate saves in the selected domain.
    await request('/api/program/stop',{})
    app.relaunch();await evaluate(`window.elfinDesktop.close()`)
    return {relaunching:true}
  }
  assert.equal(stage.phase,'reopened');assert.ok(programs.includes('恢复之后保存'))
  await panel();await screenshot('backup-reopened.png')
  report.recovery={phase:'reopened',completed:true,copy_id:stage.copy_id,programs,
    firstRestartVerified:true,normalSaveAndSecondRestartVerified:true,simOnly:true}
  return {completed:true}
}
module.exports={verifyRecovery}
