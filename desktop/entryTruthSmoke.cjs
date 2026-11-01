// Real entry/label acceptance, isolated profile, no device connections or motion.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyEntryTruth({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const result=report.entryTruth={completed:false,cleanupErrors:[]},evaluate=script=>window.webContents.executeJavaScript(script)
  const request=(url,method='GET')=>evaluate(`(async()=>{const response=await fetch(${JSON.stringify(url)},{method:${JSON.stringify(method)},cache:'no-store',signal:AbortSignal.timeout(10000)});const value=await response.json();if(!response.ok||value.ok===false)throw Error(value.why||'HTTP '+response.status);return value})()`)
  async function click(label,scope='body'){await evaluate(`(()=>{const button=[...document.querySelectorAll(${JSON.stringify(scope+' button')})].find(el=>el.textContent.trim()===${JSON.stringify(label)});if(!button||button.disabled)throw Error('入口不可用：'+${JSON.stringify(label)});button.click()})()`)}
  async function nav(title){await evaluate(`document.querySelector('nav button[title=${JSON.stringify(title)}]').click()`);await wait(()=>evaluate(`document.querySelector('nav button[aria-current="page"]')?.title===${JSON.stringify(title)}`))}
  async function capture(name){const before=await evaluate('window.__elfinScene.dprInfo().submittedFrames');await wait(()=>evaluate(`window.__elfinScene.dprInfo().submittedFrames>${before}`));fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
  async function system(tab){await nav('系统');await wait(()=>evaluate(`!!document.querySelector('#system-tab-${tab}')`));await evaluate(`document.querySelector('#system-tab-${tab}').click()`)}
  let createdPoint=null,failure
  try{
    const initial=await request('/api/state');assert.equal(initial.mode,'sim');assert.equal(initial.real,null);assert.ok(!['running','waiting','paused'].includes(initial.exec?.state))
    result.stage='protocol-reference'
    await system('about');await wait(()=>evaluate(`!!document.querySelector('#about-tab-diagnostics')`));await evaluate(`document.querySelector('#about-tab-diagnostics').click()`)
    await wait(()=>evaluate(`document.querySelector('#about-panel').textContent.includes('协议参考目录')`))
    result.protocol=await evaluate(`({titles:[...document.querySelectorAll('#about-panel .panel-title')].map(el=>el.textContent),text:document.querySelector('#about-panel').textContent})`)
    assert.ok(result.protocol.titles.includes('协议参考目录'));assert.ok(!result.protocol.titles.includes('已接入指令'));assert.match(result.protocol.text,/不代表当前支持/)
    await capture('entry-protocol-reference.png')
    result.stage='unique-connection-entry'
    await system('settings');await wait(()=>evaluate(`!!document.querySelector('#settings-tab-connection')`));await evaluate(`document.querySelector('#settings-tab-connection').click()`)
    result.settings=await evaluate(`({buttons:[...document.querySelectorAll('#settings-panel button')].map(el=>el.textContent.trim()),autoEditors:[...document.querySelectorAll('#settings-panel label')].filter(el=>el.textContent.includes('开机自动连接真机')&&el.querySelector('input[type=checkbox]')).length})`)
    assert.equal(result.settings.autoEditors,1);assert.ok(!result.settings.buttons.some(label=>['连接真机','断开回仿真','真机','仿真'].includes(label)))
    await capture('entry-settings-connection-summary.png');await click('前往控制','#settings-panel')
    await wait(()=>evaluate(`document.querySelector('nav button[aria-current="page"]')?.title==='控制'`));await evaluate(`document.querySelector('#control-tab-connection').click()`)
    result.driver=await evaluate(`({buttons:[...document.querySelectorAll('#drive-panel button')].map(el=>el.textContent.trim()),autoEditors:[...document.querySelectorAll('#drive-panel label')].filter(el=>el.textContent.includes('自动连接')&&el.querySelector('input[type=checkbox]')).length,text:document.querySelector('#drive-panel').textContent})`)
    assert.equal(result.driver.buttons.filter(label=>['连接真机','断开回仿真'].includes(label)).length,1);assert.equal(result.driver.autoEditors,0);assert.ok(!result.driver.buttons.includes('真机')&&!result.driver.buttons.includes('仿真'));assert.match(result.driver.text,/已保存的开机自动连接/)
    await capture('entry-driver-single-connection.png');await click('前往设置','#drive-panel');await wait(()=>evaluate(`document.querySelector('#system-tab-settings')?.getAttribute('aria-selected')==='true'`))
    result.stage='vision-controls-and-readonly-pairing'
    await nav('视觉');await wait(()=>evaluate(`!!document.querySelector('#industrial-vision-tab-camera')`))
    result.visionTabs=await evaluate(`[...document.querySelectorAll('[id^="industrial-vision-tab-"]')].map(el=>el.id)`)
    assert.deepEqual(result.visionTabs,['industrial-vision-tab-camera','industrial-vision-tab-objects','industrial-vision-tab-position','industrial-vision-tab-calibration'])
    await evaluate(`document.querySelector('#industrial-vision-tab-objects').click()`);await wait(()=>evaluate(`!![...document.querySelectorAll('.vision-workspace button')].find(el=>el.textContent.trim()==='暂停识别与映射')`));await capture('entry-vision-recognition-controls.png')
    await evaluate(`document.querySelector('#industrial-vision-tab-camera').click()`)
    await wait(()=>evaluate(`!![...document.querySelectorAll('.vision-workspace details')].find(el=>el.querySelector('summary')?.textContent==='手机或远程电脑相机')`))
    await evaluate(`(()=>{const details=[...document.querySelectorAll('.vision-workspace details')].find(el=>el.querySelector('summary')?.textContent==='手机或远程电脑相机');details.open=true;details.scrollIntoView({block:'center'})})()`)
    const remote=await request('/api/vision/remote/state')
    if(remote.available){await wait(()=>evaluate(`!![...document.querySelectorAll('.vision-workspace select option')].find(el=>el.value==='workpiece')`));result.remote=await evaluate(`({options:[...document.querySelectorAll('.vision-workspace select option')].filter(el=>['hand','workpiece'].includes(el.value)).map(el=>({value:el.value,label:el.textContent})),text:[...document.querySelectorAll('.vision-workspace details')].find(el=>el.querySelector('summary')?.textContent==='手机或远程电脑相机').textContent})`);assert.ok(result.remote.options.some(option=>option.value==='workpiece'&&option.label.includes('工件')))}
    else result.remote={unverified:remote.reason||remote.error||'远程接收依赖不可用'}
    const afterRemote=await request('/api/vision/remote/state');assert.equal(afterRemote.phase,remote.phase);assert.equal(afterRemote.session,remote.session)
    result.remote.paired=false;result.remote.deviceOpened=false;await capture('entry-remote-workpiece-selector.png')
    result.stage='point-load-is-preview-only'
    await nav('点位');await wait(()=>evaluate(`!!document.querySelector('#point-tab-execute')`));await evaluate(`document.querySelector('#point-tab-execute').click();document.querySelector('#point-mode-joint').click()`)
    await wait(()=>evaluate(`!!document.querySelector('input[aria-label="J1 目标角"]')`))
    const beforePoint=await request('/api/state'),name='入口验收预览-'+Date.now()
    const next=beforePoint.joints.q[0]+(beforePoint.joints.q[0]<100?1:-1)
    await evaluate(`(()=>{const slider=document.querySelector('input[aria-label="J1 目标角"]');if(slider.disabled)throw Error('关节预览不可用');slider.value=${next};slider.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#point-tab-storage').click()})()`)
    await wait(()=>evaluate(`!![...document.querySelectorAll('#point-pane-storage button')].find(el=>el.textContent.trim()==='存目标值'&&!el.disabled)`))
    await evaluate(`(()=>{const input=document.querySelector('input[aria-label="目标点位名称"]');input.value=${JSON.stringify(name)};input.dispatchEvent(new Event('input',{bubbles:true}))})()`);await click('存目标值','#point-pane-storage')
    await wait(async()=>{const point=(await request('/api/points')).items.find(item=>item.name===name);if(point)createdPoint=point;return !!point})
    await wait(()=>evaluate(`[...document.querySelectorAll('.point-card')].some(el=>el.textContent.includes(${JSON.stringify(name)}))`))
    await evaluate(`(()=>{const card=[...document.querySelectorAll('.point-card')].find(el=>el.textContent.includes(${JSON.stringify(name)}));const button=[...card.querySelectorAll('button')].find(el=>el.textContent.trim()==='载入预览');if(!button)throw Error('缺少真实载入预览入口');button.click()})()`)
    await wait(()=>evaluate(`document.querySelector('#point-tab-execute').getAttribute('aria-selected')==='true'`))
    result.point={id:createdPoint.id,name,previewJ1:await evaluate(`Number(document.querySelector('input[aria-label="J1 目标角（只读）"]').value)`),before:beforePoint.joints.q,after:(await request('/api/state')).joints.q}
    assert.ok(Math.abs(result.point.previewJ1-next)<.01);assert.deepEqual(result.point.after,result.point.before,'载入预览不得移动仿真关节');await capture('entry-point-load-preview-only.png')
    const final=await request('/api/state');assert.equal(final.mode,'sim');assert.equal(final.real,null)
    result.chessScope='本轮未启动棋局；活跃离页提示由既有棋局烟测断言与离线回归覆盖'
  }catch(error){failure=error;result.error=error.stack||String(error);try{await capture('entry-truth-failure.png')}catch{}}
  finally{
    if(createdPoint)try{
      const removedViaUi=await evaluate(`(()=>{const storage=document.querySelector('#point-tab-storage');if(!storage)return false;storage.click();return true})()`)
      if(removedViaUi){await wait(()=>evaluate(`[...document.querySelectorAll('.point-card')].some(el=>el.textContent.includes(${JSON.stringify(createdPoint.name)}))`));await evaluate(`(()=>{const card=[...document.querySelectorAll('.point-card')].find(el=>el.textContent.includes(${JSON.stringify(createdPoint.name)}));[...card.querySelectorAll('button')].find(el=>el.textContent.trim()==='删').click()})()`);await wait(async()=>!(await request('/api/points')).items.some(point=>point.id===createdPoint.id))}
      else await request('/api/points/'+encodeURIComponent(createdPoint.id),'DELETE')
    }catch(error){result.cleanupErrors.push(error.message);try{await request('/api/points/'+encodeURIComponent(createdPoint.id),'DELETE')}catch(fallback){result.cleanupErrors.push(fallback.message)}}
    try{await evaluate(`(()=>{const button=[...document.querySelectorAll('.stage button')].find(el=>el.textContent.includes('收掉残影'));if(button)button.click()})()`)}catch(error){result.cleanupErrors.push(error.message)}
  }
  if(failure)throw failure
  assert.deepEqual(result.cleanupErrors,[]);result.completed=true
}
module.exports={verifyEntryTruth}
