// Existing status, diagnostics and evidence UI; isolated data, no device actions.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyReview({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const result=report.review={completed:false},evaluate=code=>window.webContents.executeJavaScript(code)
  const request=(url,body)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(url)},{cache:'no-store',...(${JSON.stringify(body)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(body)})})});const v=await r.json();if(!r.ok||v.ok===false)throw Error(v.why||v.detail||'HTTP '+r.status);return v})()`)
  const pause=ms=>new Promise(r=>setTimeout(r,ms))
  async function capture(name){await evaluate(`new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
  async function button(label,selector='body'){await evaluate(`(()=>{const b=[...document.querySelectorAll(${JSON.stringify(selector+' button')})].find(b=>b.textContent.trim()===${JSON.stringify(label)});if(!b||b.disabled)throw Error('按钮不可用：'+${JSON.stringify(label)});b.click()})()`)}
  await button('启动条件','.statusbar');await wait(()=>evaluate(`!!document.querySelector('.readiness-popover')`))
  result.readiness=await evaluate(`(()=>{const el=document.querySelector('.readiness-popover'),r=el.getBoundingClientRect(),b=el.querySelector('button').getBoundingClientRect();return {text:el.textContent,rows:el.querySelectorAll('article').length,inViewport:r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,unobscured:el.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2))}})()`)
  await capture('readiness-navigation.png');assert.ok(result.readiness.rows>=6);assert.ok(result.readiness.inViewport);assert.ok(result.readiness.unobscured,'启动条件关闭按钮不能被相机或日志遮挡');assert.match(result.readiness.text,/下一步/);assert.match(result.readiness.text,/完成条件/)
  await evaluate(`document.querySelectorAll('.readiness-popover article')[2].querySelector('button').click()`)
  await wait(()=>evaluate(`document.querySelector('#system-tab-engineering')?.getAttribute('aria-selected')==='true'`))
  result.navigation=await evaluate(`location.hash`);assert.match(result.navigation,/engineering/)
  const loaded=await request('/api/program/load',{src:'WAIT 10\nWAIT 0.2'});assert.equal(loaded.steps.length,2)
  await request('/api/program/start',{})
  try{
    await wait(()=>evaluate(`document.querySelector('.statusbar').textContent.includes('程序任务')`))
    await evaluate(`document.querySelector('#system-tab-about').click()`)
    await wait(()=>evaluate(`!!document.querySelector('#about-tab-validation')`))
    result.crossPage=await evaluate(`document.querySelector('.statusbar').textContent`);assert.match(result.crossPage,/程序任务/);assert.match(result.crossPage,/已确认 0\/2/)
    await capture('task-owner-cross-page.png')
  }finally{result.stop=await request('/api/program/stop',{})}
  assert.equal(result.stop.ok,true)
  await evaluate(`document.querySelector('#about-tab-validation').click()`)
  await wait(()=>evaluate(`!!document.querySelector('.acceptance-version') && !![...document.querySelectorAll('#about-panel button')].find(b=>b.textContent.trim()==='刷新验收记录'&&!b.disabled)`))
  result.acceptance=await request('/api/acceptance',null)
  result.acceptanceUi=await evaluate(`(()=>{const section=document.querySelector('.acceptance-version').closest('section');section.scrollIntoView({block:'start'});return {text:section.textContent,features:section.querySelectorAll('.acceptance-feature').length}})()`)
  if(result.acceptance.matches_current){assert.match(result.acceptanceUi.text,/证据与当前构建一致/);assert.equal(result.acceptanceUi.features,result.acceptance.evidence.features.length)}else assert.match(result.acceptanceUi.text,/没有绑定版本|历史证据/)
  await capture('acceptance-version-bound.png')
  await evaluate(`document.querySelector('#system-tab-tele').click()`);await wait(()=>evaluate(`!!document.querySelector('#telemetry-tab-performance')`));await evaluate(`document.querySelector('#telemetry-tab-performance').click()`)
  await wait(()=>evaluate(`!![...document.querySelectorAll('#telemetry-panel button')].find(b=>b.textContent.trim()==='采集前样本')`))
  for(const label of ['前','后']){
    await button('采集'+label+'样本','#telemetry-panel');await pause(2300);await button('结束采样','#telemetry-panel')
    await wait(()=>evaluate(`document.querySelector('#telemetry-panel').textContent.includes('${label}样本已保存')`))
  }
  const exportPath=path.join(directory,'performance-ui-comparison.json');let downloaded=false
  window.webContents.session.once('will-download',(_event,item)=>{item.setSavePath(exportPath);item.once('done',(_event,status)=>{downloaded=status==='completed'})})
  await button('导出对比','#telemetry-panel');await wait(()=>downloaded)
  const exported=JSON.parse(fs.readFileSync(exportPath,'utf8'));assert.equal(exported.version,1);assert.equal(exported.before.identity,exported.after.identity);assert.ok(exported.before.samples.length>=2&&exported.after.samples.length>=2)
  const identity=JSON.parse(exported.before.identity)
  assert.equal(identity.build.backend_sha256,result.acceptance.build.backend_sha256);assert.equal(identity.build.frontend_sha256,result.acceptance.build.frontend_sha256)
  assert.ok(identity.session_id&&identity.tool&&identity.viewport.width>0&&identity.viewport.dpr>0,'导出必须绑定实际会话、工具与窗口')
  result.performanceExport={before:exported.before.samples.length,after:exported.after.samples.length,unmeasured:exported.unmeasured};await capture('performance-ui-comparison.png')
  for(const factor of [1.25,2]){window.webContents.setZoomFactor(factor);await pause(300);await button('启动条件','.statusbar');await wait(()=>evaluate(`!!document.querySelector('.readiness-popover')`));const bounds=await evaluate(`(()=>{const r=document.querySelector('.readiness-popover').getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,w:innerWidth,h:innerHeight}})()`);assert.ok(bounds.left>=0&&bounds.top>=0&&bounds.right<=bounds.w&&bounds.bottom<=bounds.h);await capture('readiness-zoom-'+factor+'.png');await button('关闭','.readiness-popover')}
  window.webContents.setZoomFactor(1)
  await require('./entryTruthSmoke.cjs').verifyEntryTruth({window,directory,report,wait})
  await require('./qualitySmoke.cjs').verifyQuality({window,directory,report,wait})
  await require('./aiClarificationSmoke.cjs').verifyAiClarification({window,directory,report,wait})
  result.completed=true
}
module.exports={verifyReview}
