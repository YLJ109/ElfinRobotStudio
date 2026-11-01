// 仅显式--smoke-output启动时运行，在独立数据目录验收自己的桌面窗口。
const fs=require('node:fs')
const path=require('node:path')
const assert=require('node:assert/strict')
function install({app,ipcMain,BrowserWindow,directory,getOwnedBackendPid=()=>null}) {
  const began=performance.now(),report={stages:[],errors:[],ready:false,windowChecks:{}}
  let finished=false
  const journalOnly=process.argv.includes('--smoke-journal')
  const write=()=>fs.writeFileSync(path.join(directory,journalOnly?'journal-result.json':'result.json'),JSON.stringify(report,null,2))
  const wait=async check=>{
    const deadline=Date.now()+5000
    while(Date.now()<deadline){if(await check())return;await new Promise(resolve=>setTimeout(resolve,20))}
    throw new Error('桌面状态等待超时')
  }
  app.on('browser-window-created',(_event,window)=>{
    if(window.getSize()[0]<700) window.once('ready-to-show',async()=>{
      report.splashMs=performance.now()-began
      try{fs.writeFileSync(path.join(directory,journalOnly?'journal-splash.png':'splash.png'),(await window.webContents.capturePage()).toPNG())}catch{}
      if(process.argv.includes('--smoke-cancel-start')){
        report.cancelledDuringStartup=true;report.closeRequestedMs=performance.now()-began
        await window.webContents.executeJavaScript('window.elfinDesktop.close()')
      }
    })
    window.webContents.on('console-message',(_event,level,message)=>{if(level===3)report.errors.push(String(message).slice(0,600))})
  })
  ipcMain.on('desktop:boot',async(_event,value)=>{
    report.stages.push({...value,elapsedMs:performance.now()-began})
    if(value?.error){write();return}
    if(value?.phase!=='ready'||!value.ready||finished)return
    finished=true;report.ready=true;report.readyMs=performance.now()-began
    try{
      const window=BrowserWindow.getAllWindows().find(window=>window.getSize()[0]>700)
      assert.ok(window)
      const info=await window.webContents.executeJavaScript(`({title:document.title,controls:[...document.querySelectorAll('.window-controls button')].map(el=>el.getAttribute('aria-label')),node:typeof require,bridge:window.elfinDesktop?.version,scene:!!window.__elfinScene,frame:window.__elfinScene?.dprInfo()})`)
      assert.equal(info.controls.length,3);assert.equal(info.node,'undefined');assert.equal(info.bridge,1);assert.equal(info.scene,true)
      report.renderer=info
      report.build=await window.webContents.executeJavaScript(`fetch('/api/build',{cache:'no-store',signal:AbortSignal.timeout(5000)}).then(async r=>{if(!r.ok)throw Error('构建身份读取失败');return r.json()})`)
      assert.equal(report.build.verified,true,'实际桌面必须载入可核验生产构建')
      report.resourceTimings=await window.webContents.executeJavaScript(`performance.getEntriesByType('resource').filter(entry=>entry.name.includes('/api/')).map(entry=>({url:new URL(entry.name).pathname,start:entry.startTime,duration:entry.duration}))`)
      report.gpu=app.getGPUFeatureStatus()
      if(journalOnly){
        await require('./journalUiSmoke.cjs').verifyJournalUi({window,directory,report,wait})
        report.completed=true;write();report.closeRequestedMs=performance.now()-began
        await window.webContents.executeJavaScript('window.elfinDesktop.close()');return
      }
      if(process.argv.includes('--smoke-recovery')){
        const result=await require('./recoverySmoke.cjs').verifyRecovery({app,window,directory,report,wait})
        if(result.relaunching){write();return}
        report.completed=result.completed;write()
        report.closeRequestedMs=performance.now()-began
        await window.webContents.executeJavaScript(`window.elfinDesktop.close()`)
        return
      }
      await window.webContents.executeJavaScript(`document.querySelector('nav button[title="有趣"]').click()`)
      await wait(()=>window.webContents.executeJavaScript(`!!document.querySelector('#functions-tab-chess')`))
      await window.webContents.executeJavaScript(`document.querySelector('#functions-tab-chess').click()`)
      await wait(()=>window.webContents.executeJavaScript(`document.querySelectorAll('.chess-board button').length===9`))
      // A fresh workspace must not accept phantom moves without scene setup and
      // an explicitly started match. --smoke-renewal tests actual chess pick/place.
      assert.equal(await window.webContents.executeJavaScript(`[...document.querySelectorAll('.chess-board button')].every(el=>el.disabled)`),true)
      report.chessRequiresSetup=true
      await window.webContents.executeJavaScript(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`)
      assert.equal(await window.webContents.executeJavaScript(`document.querySelector('nav button[title="有趣"]')?.getAttribute('aria-current')`),'page')
      fs.writeFileSync(path.join(directory,'functions.png'),(await window.webContents.capturePage()).toPNG())
      await window.webContents.executeJavaScript(`document.querySelector('nav button[title="分拣"]').click()`)
      await wait(()=>window.webContents.executeJavaScript(`document.querySelector('#functions-panel')?.textContent.includes('机器人分拣')`))
      report.sortingVisible=true
      const created=await window.webContents.executeJavaScript(`(async()=>{
        const results=[];
        for(const [name,kind] of [['验收方块','box'],['验收球体','sphere']]){
          const response=await fetch('/api/objects',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name,kind,size:[30,30,40],xyz:[500,500,-100],collide:false})});
          const result=await response.json();if(!result.ok)throw new Error(result.why);
          results.push(result.object);
        }return results;
      })()`)
      assert.ok(created.every(object=>object.xyz[2]>0),'API创建也必须约束地面')
      assert.equal(await window.webContents.executeJavaScript(`!!document.querySelector('.sort-objects')`),false,'任务页不应保留直接搬动物体的整理旁路')
      report.noDirectObjectSorting=true;report.groundCreationConstrained=true
      for(const object of created)await window.webContents.executeJavaScript(`fetch('/api/objects/remove',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({id:${JSON.stringify(object.id)}})}).then(response=>response.json())`)
      if(process.argv.includes('--smoke-drawing')) await require('./drawingSmoke.cjs').verifyDrawing({window,directory,report,wait})
      if(process.argv.includes('--smoke-handling-feedback')) await require('./handlingFeedbackSmoke.cjs').verifyHandlingFeedback({window,directory,report,wait})
      if(process.argv.includes('--smoke-user-feedback')) await require('./userFeedbackSmoke.cjs').verifyUserFeedback({window,directory,report,wait})
      if(process.argv.includes('--smoke-camera-permission')) await require('./cameraPermissionSmoke.cjs').verifyCameraPermission({window,directory,report,wait})
      if(process.argv.includes('--smoke-vision')) await require('./visionSmoke.cjs').verifyVision({app,BrowserWindow,window,directory,report,wait,getOwnedBackendPid})
      if(process.argv.includes('--smoke-play')) await require('./playSmoke.cjs').verifyPlay({window,directory,report,wait})
      if(process.argv.includes('--smoke-robot-play')) await require('./robotPlaySmoke.cjs').verifyRobotPlay({window,directory,report,wait})
      if(process.argv.includes('--smoke-practical') || process.argv.includes('--smoke-letter-full')) await require('./practicalSmoke.cjs').verifyPractical({window,directory,report,wait})
      if(process.argv.includes('--smoke-review')) await require('./reviewSmoke.cjs').verifyReview({window,directory,report,wait})
      if(process.argv.includes('--smoke-renewal')) await require('./renewalSmoke.cjs').verifyRenewal({window,directory,report,wait})
      if(process.argv.includes('--smoke-soak')) {
        await window.webContents.executeJavaScript(`document.querySelector('nav button[title="有趣"]').click()`)
        await wait(()=>window.webContents.executeJavaScript(`!!document.querySelector('#functions-tab-chess')`))
        window.webContents.debugger.attach('1.3')
        const cycle=async()=>{
          for(const tab of ['chess','sorting','interaction']) {
            await window.webContents.executeJavaScript(`document.querySelector('nav button[title="${tab==='sorting'?'分拣':'有趣'}"]').click()`)
            await wait(()=>window.webContents.executeJavaScript(`!!document.querySelector('#functions-tab-${tab}')`))
            await window.webContents.executeJavaScript(`document.querySelector('#functions-tab-${tab}').click()`)
          }
        }
        const sample=async cycles=>{
          await window.webContents.executeJavaScript(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`)
          await window.webContents.debugger.sendCommand('HeapProfiler.collectGarbage')
          const heap=await window.webContents.debugger.sendCommand('Runtime.getHeapUsage')
          const resources=await window.webContents.executeJavaScript(`window.__elfinScene.resourceInfo()`)
          return {cycles,heapBytes:heap.usedSize,resources,mainHeapBytes:process.memoryUsage().heapUsed}
        }
        for(let i=0;i<5;i++)await cycle()
        await wait(()=>window.webContents.executeJavaScript(`!!document.querySelector('.hand-follow-panel')`))
        report.memorySamples=[await sample(5)]
        for(let i=0;i<25;i++)await cycle()
        report.memorySamples.push(await sample(30))
        for(let i=0;i<30;i++)await cycle()
        report.memorySamples.push(await sample(60))
        const [first,,last]=report.memorySamples
        report.retainedHeapGrowthBytes=last.heapBytes-first.heapBytes
        assert.ok(report.retainedHeapGrowthBytes<4*1024*1024,'60轮切页后的保留堆增长超过4MiB，需排查')
        assert.deepEqual(last.resources,first.resources,'切换功能页不应持续创建三维资源')
        window.webContents.debugger.detach()
      }
      await window.webContents.executeJavaScript(`document.querySelector('[aria-label="最大化窗口"]').click()`)
      await wait(()=>window.isMaximized());report.windowChecks.maximize=true
      await wait(()=>window.webContents.executeJavaScript(`!!document.querySelector('[aria-label="还原窗口"]')`))
      await window.webContents.executeJavaScript(`document.querySelector('[aria-label="还原窗口"]').click()`)
      await wait(()=>!window.isMaximized());report.windowChecks.restore=true
      // Windows还原动画期间连续发最小化会被窗口管理器合并；验收等待动画结束。
      await new Promise(resolve=>setTimeout(resolve,350))
      await window.webContents.executeJavaScript(`document.querySelector('[aria-label="最小化窗口"]').click()`)
      await wait(()=>window.isMinimized());report.windowChecks.minimize=true
      window.restore();window.setSize(1280,800)
      await wait(()=>Math.abs(window.getSize()[0]-1280)<=2);report.windowChecks.resize=true
      report.windowChecks.size=window.getSize()
      const resizedFrames=await window.webContents.executeJavaScript(`window.__elfinScene.dprInfo().submittedFrames`)
      await wait(()=>window.webContents.executeJavaScript(`window.__elfinScene.dprInfo().submittedFrames>=${resizedFrames+2}`))
      const image=await window.webContents.capturePage()
      fs.writeFileSync(path.join(directory,'main.png'),image.toPNG())
      report.screenshotSize=image.getSize();report.completed=true;write()
      report.closeRequestedMs=performance.now()-began
      await window.webContents.executeJavaScript(`document.querySelector('[aria-label="关闭窗口"]').click()`)
    }catch(error){
      report.failure=error?.stack || String(error)
      const window=BrowserWindow.getAllWindows().find(window=>window.getSize()[0]>700)
      if(window && !window.isDestroyed()){
        try{report.failureDiagnostic=await window.webContents.executeJavaScript(`(async()=>({text:document.body.innerText,state:await fetch('/api/state').then(r=>r.json()),play:await fetch('/api/play/program/state').then(r=>r.json())}))()`)}catch{}
        try{fs.writeFileSync(path.join(directory,journalOnly?'journal-failure.png':'failure.png'),(await window.webContents.capturePage()).toPNG())}catch{}
      }
      write();app.quit()
    }
  })
  app.on('will-quit',()=>{report.exitedCleanly=true;if(report.closeRequestedMs)report.closeMs=performance.now()-began-report.closeRequestedMs;write()})
}
module.exports={install}
