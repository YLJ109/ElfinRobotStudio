// Explicit isolated UI acceptance: preference edits never reload or stop a task.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyQuality({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const result=report.quality={completed:false},evaluate=code=>window.webContents.executeJavaScript(code)
  result.navigation=[]
  async function navigation(expected){
    await wait(()=>evaluate(`document.querySelector('nav button[aria-current="page"]')?.title===${JSON.stringify(expected)}`))
    const before=await evaluate(`window.__elfinScene.dprInfo().submittedFrames`)
    await wait(()=>evaluate(`window.__elfinScene.dprInfo().submittedFrames>${before}`))
    const value=await evaluate(`({hash:location.hash,navcurrent:document.querySelector('nav button[aria-current="page"]')?.title,submittedFrames:window.__elfinScene.dprInfo().submittedFrames})`)
    assert.equal(value.navcurrent,expected);assert.ok(value.submittedFrames>before);result.navigation.push(value)
  }
  const request=(url,body)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(url)},{cache:'no-store',signal:AbortSignal.timeout(10000),...(${JSON.stringify(body)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(body)})})});const value=await r.json();if(!r.ok||value.ok===false)throw Error(value.why||'HTTP '+r.status);return value})()`)
  const click=label=>evaluate(`(()=>{const button=[...document.querySelectorAll('#tools-panel button')].find(el=>el.textContent.trim()===${JSON.stringify(label)});if(!button||button.disabled)throw Error('画质按钮不可用：'+${JSON.stringify(label)});button.click()})()`)
  const capture=async name=>{await evaluate(`new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
  async function qualityPage(){await evaluate(`document.querySelector('nav button[title="系统"]').click()`);await wait(()=>evaluate(`!!document.querySelector('#system-tab-engineering')`));await evaluate(`document.querySelector('#system-tab-engineering').click()`);await wait(()=>evaluate(`!!document.querySelector('#tools-tab-display')`));await evaluate(`document.querySelector('#tools-tab-display').click()`);await wait(()=>evaluate(`!!document.querySelector('#tools-panel select')`));await navigation('系统')}
  async function choose(value){await evaluate(`(()=>{const select=document.querySelector('#tools-panel select');select.value=${JSON.stringify(value)};select.dispatchEvent(new Event('change',{bubbles:true}))})()`);await wait(()=>evaluate(`document.querySelector('#tools-panel select').value===${JSON.stringify(value)}`))}
  let taskOwned=false,original,failure
  try{
    result.stage='check-isolated-session'
    const safety=await request('/api/state',null);assert.equal(safety.mode,'sim');assert.equal(safety.real,null);assert.ok(!['running','waiting','paused'].includes(safety.exec?.state))
    result.timeOrigin=await evaluate('performance.timeOrigin')
    result.stage='edit-unsaved-program-draft'
    await evaluate(`document.querySelector('nav button[title="程序"]').click()`);await wait(()=>evaluate(`!!document.querySelector('.cm-content[contenteditable="true"]')`))
    await evaluate(`document.querySelector('.cm-content').focus()`)
    window.webContents.selectAll()
    await wait(()=>evaluate(`(()=>{const editor=document.querySelector('.cm-content'),selection=getSelection();return !!editor&&!!selection?.rangeCount&&editor.contains(selection.anchorNode)&&editor.contains(selection.focusNode)&&selection.toString().length>0&&selection.toString().replace(/\\s/g,'')===editor.innerText.replace(/\\s/g,'')})()`))
    await window.webContents.insertText('# quality-draft-preserved\nWAIT 0.25')
    await wait(()=>evaluate(`[...document.querySelectorAll('.cm-content .cm-line')].map(line=>line.textContent).join('\\n')==='# quality-draft-preserved\\nWAIT 0.25'`))
    result.draftBefore=await evaluate(`[...document.querySelectorAll('.cm-content .cm-line')].map(line=>line.textContent).join('\\n')`)
    result.stage='start-official-wait-task'
    await request('/api/program/load',{src:'WAIT 60'});taskOwned=true;await request('/api/program/start',{})
    await qualityPage()
    result.stage='cancel-quality-draft'
    original=await evaluate(`localStorage.getItem('huayan.modelQuality')==='low'?'low':'full'`)
    const other=original==='full'?'low':'full';await choose(other);await click('取消修改')
    assert.equal(await evaluate(`document.querySelector('#tools-panel select').value`),original)
    assert.equal(await evaluate(`localStorage.getItem('huayan.modelQuality')==='low'?'low':'full'`),original)
    result.stage='save-next-startup-quality'
    await choose(other);await click('保存下次启动画质')
    assert.equal(await evaluate(`localStorage.getItem('huayan.modelQuality')`),other)
    result.persistedNextStartup=other;result.runnerAfterSave=(await request('/api/program',null)).runner
    assert.ok(['running','waiting'].includes(result.runnerAfterSave.state),'保存画质不能停止或重载WAIT任务')
    assert.equal(await evaluate('performance.timeOrigin'),result.timeOrigin,'保存画质不能整页刷新')
    await navigation('系统');await capture('quality-next-start.png')
    await evaluate(`document.querySelector('nav button[title="程序"]').click()`);await wait(()=>evaluate(`!!document.querySelector('.cm-content')`))
    result.stage='verify-preserved-program-draft';await navigation('程序')
    result.draftAfter=await evaluate(`[...document.querySelectorAll('.cm-content .cm-line')].map(line=>line.textContent).join('\\n')`);assert.equal(result.draftAfter,result.draftBefore,'画质保存不能丢失未保存程序草稿')
    assert.equal(await evaluate('performance.timeOrigin'),result.timeOrigin)
    await qualityPage();await choose(original);await click('保存下次启动画质');original=null
  }catch(error){failure=error;result.error=error.stack||String(error)}
  finally{
    if(original)try{await qualityPage();await choose(original);await click('保存下次启动画质')}catch(error){result.restoreError=error.message;failure||=error}
    if(taskOwned)try{result.stop=await request('/api/program/stop',{});result.stoppedRunner=(await request('/api/program',null)).runner;assert.ok(['aborted','idle'].includes(result.stoppedRunner.state))}catch(error){result.stopError=error.message;failure||=error}
  }
  if(failure)throw failure
  result.modelBounds=[]
  try{
    for(const zoom of [1.25,2]){
      result.stage='model-library-zoom-'+zoom
      window.webContents.setZoomFactor(zoom);await new Promise(resolve=>setTimeout(resolve,300))
      await evaluate(`(()=>{const button=document.querySelector('.model-library>button');if(button.getAttribute('aria-expanded')!=='true')button.click()})()`)
      await wait(()=>evaluate(`!!document.querySelector('.model-library .library')`))
      const bounds=await evaluate(`(()=>{const library=document.querySelector('.model-library .library'),r=library.getBoundingClientRect(),stage=document.querySelector('.stage').getBoundingClientRect(),close=library.querySelector('button[aria-label="关闭模型库"]'),b=close.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,stage:{left:stage.left,top:stage.top,right:stage.right,bottom:stage.bottom},viewport:{w:innerWidth,h:innerHeight},closeVisible:close.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)),options:library.querySelector('select[aria-label="选择末端显示模型"]')?.options.length}})()`)
      result.modelBounds.push({zoom,...bounds});await capture('model-library-zoom-'+zoom+'.png')
      assert.ok(bounds.left>=bounds.stage.left&&bounds.top>=bounds.stage.top&&bounds.right<=bounds.stage.right&&bounds.bottom<=bounds.stage.bottom,'模型库须完整落在stage内')
      assert.ok(bounds.left>=0&&bounds.top>=0&&bounds.right<=bounds.viewport.w&&bounds.bottom<=bounds.viewport.h);assert.ok(bounds.closeVisible,'关闭按钮需实际可点击');assert.ok(bounds.options>=2)
      await evaluate(`(()=>{const select=document.querySelector('.model-library select[aria-label="选择末端显示模型"]');select.focus();select.dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true}))})()`)
      await wait(()=>evaluate(`!!document.querySelector('#theme-select-listbox')`))
      const menu=await evaluate(`(()=>{const el=document.querySelector('#theme-select-listbox'),r=el.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,w:innerWidth,h:innerHeight,options:el.querySelectorAll('[role=option]').length}})()`)
      result.modelBounds.at(-1).menu=menu;await capture('model-library-options-zoom-'+zoom+'.png')
      assert.ok(menu.left>=0&&menu.top>=0&&menu.right<=menu.w&&menu.bottom<=menu.h);assert.ok(menu.options>=2)
      await evaluate(`document.querySelector('.model-library select[aria-label="选择末端显示模型"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`)
      await wait(()=>evaluate(`!document.querySelector('#theme-select-listbox')`))
      await evaluate(`document.querySelector('.model-library button[aria-label="关闭模型库"]').click()`);await wait(()=>evaluate(`!document.querySelector('.model-library .library')`))
    }
  }finally{window.webContents.setZoomFactor(1)}
  result.completed=true
}
module.exports={verifyQuality}
