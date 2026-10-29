// 仅显式--smoke-output启动时运行，在独立数据目录验收自己的桌面窗口。
const fs=require('node:fs')
const path=require('node:path')
const assert=require('node:assert/strict')
function install({app,ipcMain,BrowserWindow,directory}) {
  const began=performance.now(),report={stages:[],errors:[],ready:false,windowChecks:{}}
  let finished=false
  const write=()=>fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify(report,null,2))
  const wait=async check=>{
    const deadline=Date.now()+5000
    while(Date.now()<deadline){if(await check())return;await new Promise(resolve=>setTimeout(resolve,20))}
    throw new Error('桌面状态等待超时')
  }
  app.on('browser-window-created',(_event,window)=>{
    if(window.getSize()[0]<700) window.once('ready-to-show',async()=>{
      report.splashMs=performance.now()-began
      try{fs.writeFileSync(path.join(directory,'splash.png'),(await window.webContents.capturePage()).toPNG())}catch{}
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
      report.resourceTimings=await window.webContents.executeJavaScript(`performance.getEntriesByType('resource').filter(entry=>entry.name.includes('/api/')).map(entry=>({url:new URL(entry.name).pathname,start:entry.startTime,duration:entry.duration}))`)
      report.gpu=app.getGPUFeatureStatus()
      await window.webContents.executeJavaScript(`document.querySelector('nav button[title="功能"]').click()`)
      await wait(()=>window.webContents.executeJavaScript(`!!document.querySelector('#functions-tab-chess')`))
      await window.webContents.executeJavaScript(`document.querySelector('#functions-tab-chess').click()`)
      await wait(()=>window.webContents.executeJavaScript(`document.querySelectorAll('.chess-board button').length===9`))
      await window.webContents.executeJavaScript(`document.querySelector('.chess-board button').click()`)
      await wait(()=>window.webContents.executeJavaScript(`[...document.querySelectorAll('.chess-board button')].filter(el=>el.textContent.trim()==='O').length===1`))
      report.chessPlayed=true
      fs.writeFileSync(path.join(directory,'functions.png'),(await window.webContents.capturePage()).toPNG())
      await window.webContents.executeJavaScript(`document.querySelector('#functions-tab-sorting').click()`)
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
      await wait(()=>window.webContents.executeJavaScript(`document.querySelectorAll('.sort-objects input').length===2`))
      // 分别分发事件，允许Vue在两次真实用户点击之间同步checkbox模型。
      for(let index=0;index<2;index++)await window.webContents.executeJavaScript(`document.querySelectorAll('.sort-objects input')[${index}].click()`)
      await window.webContents.executeJavaScript(`(()=>{const details=document.querySelector('#functions-panel details');if(details)details.open=true;const button=[...document.querySelectorAll('#functions-panel button')].find(el=>el.textContent.trim().startsWith('整理场景（'));if(!button)throw new Error('缺少自选工件整理按钮');button.click()})()`)
      await wait(()=>window.webContents.executeJavaScript(`document.querySelector('#functions-panel').textContent.includes('已整理场景 2 / 2')`))
      report.sortingExecuted=true;report.groundCreationConstrained=true
      for(const object of created)await window.webContents.executeJavaScript(`fetch('/api/objects/remove',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({id:${JSON.stringify(object.id)}})}).then(response=>response.json())`)
      if(process.argv.includes('--smoke-vision')) await require('./visionSmoke.cjs').verifyVision({app,BrowserWindow,window,directory,report,wait})
      if(process.argv.includes('--smoke-play')) await require('./playSmoke.cjs').verifyPlay({window,directory,report,wait})
      if(process.argv.includes('--smoke-robot-play')) await require('./robotPlaySmoke.cjs').verifyRobotPlay({window,directory,report,wait})
      if(process.argv.includes('--smoke-soak')) {
        await window.webContents.executeJavaScript(`document.querySelector('nav button[title="功能"]').click()`)
        await wait(()=>window.webContents.executeJavaScript(`!!document.querySelector('#functions-tab-chess')`))
        window.webContents.debugger.attach('1.3')
        const cycle=async()=>{
          for(const tab of ['chess','sorting','interaction']) {
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
      const image=await window.webContents.capturePage()
      fs.writeFileSync(path.join(directory,'main.png'),image.toPNG())
      report.screenshotSize=image.getSize();report.completed=true;write()
      report.closeRequestedMs=performance.now()-began
      await window.webContents.executeJavaScript(`document.querySelector('[aria-label="关闭窗口"]').click()`)
    }catch(error){report.failure=error?.stack || String(error);write();app.quit()}
  })
  app.on('will-quit',()=>{report.exitedCleanly=true;if(report.closeRequestedMs)report.closeMs=performance.now()-began-report.closeRequestedMs;write()})
}
module.exports={install}
