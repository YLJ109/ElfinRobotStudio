// Opt-in isolated UI acceptance. No direct task-state/object mutation and no external model.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyUserFeedback({window,directory,report,wait}) {
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  assert.ok(process.argv.some(arg=>arg.startsWith('--smoke-output=')),'must use the existing isolated smoke profile')
  const result=report.userFeedback={completed:false,cleanupErrors:[]},ev=code=>window.webContents.executeJavaScript(code)
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms))
  async function until(check,label,ms=30000){const end=Date.now()+ms;while(Date.now()<end){if(await check())return;await pause(60)}throw Error('用户反馈验收超时：'+label)}
  const state=()=>ev(`fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(3000)}).then(async r=>{if(!r.ok)throw Error('state HTTP '+r.status);return r.json()})`)
  async function click(text,scope='document'){await until(()=>ev(`!![...${scope}.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}&&!b.disabled)`),'可点击 '+text);await ev(`(()=>{const b=[...${scope}.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}&&!b.disabled);b.scrollIntoView({block:'center'});b.click()})()`)}
  async function nav(title){await ev(`document.querySelector('nav button[title=${JSON.stringify(title)}]').click()`);await until(()=>ev(`document.querySelector('nav button[aria-current="page"]')?.title===${JSON.stringify(title)}`),'导航 '+title)}
  async function capture(name){const before=await ev('window.__elfinScene.dprInfo().submittedFrames');await until(()=>ev(`window.__elfinScene.dprInfo().submittedFrames>=${before+2}`),'两次真实提交帧');fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
  const execState=async()=>(await state()).exec?.state
  async function ask(text){await ev(`(()=>{const e=document.querySelector('textarea[aria-label="AI 指令"]');if(!e||e.disabled)throw Error('AI输入不可用');e.value=${JSON.stringify(text)};e.dispatchEvent(new Event('input',{bubbles:true}));e.closest('form').requestSubmit()})()`);await until(()=>ev(`!document.querySelector('textarea[aria-label="AI 指令"]').disabled`),'本地AI响应')}
  async function planWait(seconds){const previous=await ev(`document.querySelector('.message-plan:last-of-type')?.dataset.planId||''`);await ask('等待'+seconds+'秒');await until(()=>ev(`!![...document.querySelectorAll('.ai-run-controls button')].find(b=>b.textContent.trim()==='执行'&&!b.disabled)`),'有效WAIT计划');result['wait'+seconds+'Plan']=await ev(`[...document.querySelectorAll('.message-plan')].at(-1)?.textContent`);assert.match(result['wait'+seconds+'Plan'],/等待|WAIT/i)}
  let failure
  try {
    const initial=await state();assert.equal(initial.mode,'sim');assert.equal(initial.real,null);assert.ok(initial.motion_granted)
    await nav('有趣');result.funTabs=await ev(`[...document.querySelectorAll('[id^="functions-tab-"]')].map(e=>e.textContent.trim())`);assert.deepEqual(result.funTabs,['跟随','井字棋','绘制'])
    await nav('分拣');result.sortTabs=await ev(`[...document.querySelectorAll('[id^="functions-tab-"]')].map(e=>e.textContent.trim())`);assert.equal(result.sortTabs.length,6)
    await nav('程序');await ev(`document.querySelector('#program-tab-rules').click()`);await until(()=>ev(`document.querySelector('#program-tab-rules').getAttribute('aria-selected')==='true'`),'语法页');result.rules=await ev(`document.querySelector('#program-tab-rules').textContent.trim()`);assert.ok(result.rules);await capture('feedback-program-rules.png')
    await nav('AI');await until(()=>ev(`!!document.querySelector('.ai-run-controls')`),'AI控制');assert.equal(await ev(`document.querySelectorAll('.ai-run-controls button').length`),2)
    assert.equal(await ev(`!!document.querySelector('[data-ai-model-consent]:checked')`),false)
    await planWait(3);await click('执行',"document.querySelector('.ai-run-controls')");await until(async()=>['running','waiting'].includes(await execState()),'WAIT3真实启动');await click('暂停',"document.querySelector('.ai-run-controls')");await until(async()=>await execState()==='paused','真实暂停');result.paused=await state();await capture('feedback-ai-paused.png');await click('继续',"document.querySelector('.ai-run-controls')");await until(async()=>await execState()==='finished','WAIT3真实完成');result.finished=await state()
    await planWait(30);await click('执行',"document.querySelector('.ai-run-controls')");await until(async()=>['running','waiting'].includes(await execState()),'WAIT30真实启动');await click('停止',"document.querySelector('.ai-run-controls')");await until(async()=>!['running','waiting','paused'].includes(await execState()),'停止真实终态');result.stopped=await state();assert.notEqual(result.stopped.exec.state,'finished')
    await ask('按顺序移动到 P1、P2、P3');await until(()=>ev(`document.querySelector('.ai').textContent.includes('P1')||document.querySelector('.ai').textContent.includes('点位')`),'缺点位反馈');result.missingPoints=await ev(`[...document.querySelectorAll('.ai .note.bad,.ai .msg.assistant .bub')].at(-1)?.textContent.trim()`);assert.match(result.missingPoints||'',/P1|点位/);assert.ok(result.missingPoints.length<1000);await capture('feedback-ai-missing-points.png')
    await nav('点位');await ev(`document.querySelector('#point-tab-execute').click()`);await until(()=>ev(`!!document.querySelector('#point-mode-joint')`),'关节模式入口');await ev(`document.querySelector('#point-mode-joint').click()`)
    await until(()=>ev(`!!document.querySelector('input[aria-label="J6 目标角"]:not(:disabled)')`),'合法关节预览入口')
    const pointStart=await state(),startJ6=pointStart.joints.q[5]
    const goalJ6=await ev(`(()=>{const e=document.querySelector('input[aria-label="J6 目标角"]');const q=${startJ6};return q+2<=+e.max?q+2:q-2})()`)
    async function slideJ6(value){await ev(`(()=>{const e=document.querySelector('input[aria-label="J6 目标角"]');e.value=String(${value});e.dispatchEvent(new Event('input',{bubbles:true}))})()`)}
    await slideJ6(goalJ6)
    let pointPlan
    await until(async()=>{const s=await state();if(s.target_plan_id&&s.target_evidence?.plan_id===s.target_plan_id&&s.target_evidence.status==='preview'){pointPlan=s.target_plan_id;return true}return false},'当前实际预览票据')
    await click('执行目标',"document.querySelector('#point-pane-execute')")
    await until(async()=>{const s=await state(),proof=s.target_evidence;return s.target_plan_id===pointPlan&&proof?.plan_id===pointPlan&&proof.reached===true&&proof.status==='reached'&&proof.feedback_age_s>=0&&proof.feedback_age_s<=.15&&s.moving===false&&Math.abs(s.joints.q[5]-goalJ6)<.1},'当前计划新鲜真实到位')
    result.pointArrival=await state();assert.ok(Math.abs(result.pointArrival.joints.q[5]-startJ6)>1.8)
    await until(()=>ev(`document.querySelector('#point-pane-execute').textContent.includes('后端已确认目标到达并停稳')`),'UI显示本次实际到位')
    await capture('feedback-point-actual-arrival.png')
    await slideJ6(goalJ6+1)
    await until(async()=>{const s=await state();return s.target_plan_id&&s.target_plan_id!==pointPlan&&s.target_evidence?.plan_id===s.target_plan_id&&s.target_evidence.reached===false},'编辑后新预览使旧到位失效')
    await until(()=>ev(`!document.querySelector('#point-pane-execute').textContent.includes('后端已确认目标到达并停稳')&&!document.querySelector('#point-pane-execute').textContent.includes('后端六轴关节反馈已确认到达目标')`),'旧到位结论不残留')
    result.pointEdited={state:await state(),messages:await ev(`[...document.querySelectorAll('#point-pane-execute [role="status"]')].map(e=>e.textContent.trim())`)}
    assert.ok(result.pointEdited.messages.every(message=>message.length<180),'点位反馈保持简短')
    assert.ok(Math.abs(result.pointEdited.state.joints.q[5]-goalJ6)<.1,'仅编辑目标不得运动')
    await capture('feedback-point-new-target-no-old-arrival.png');await click('取消',"document.querySelector('#point-pane-execute')")
    await until(()=>ev(`!!document.querySelector('.suction-controls')`),'双吸盘');assert.equal(await ev(`document.querySelectorAll('.suction-side .row button').length`),4)
    result.suction=[]
    for(const side of ['左侧','右侧'])for(const [on,off,key] of [['吸气','停止吸气','vacuum'],['放气','停止放气','blow']]) {
      const id=side==='左侧'?'left':'right'
      async function press(label){await until(()=>ev(`!!document.querySelector('.suction-controls button[aria-label="${side}${label}"]:not(:disabled)')`),'吸盘 '+side+label);await ev(`document.querySelector('.suction-controls button[aria-label="${side}${label}"]').click()`)}
      const tools=()=>ev(`fetch('/api/tools',{cache:'no-store'}).then(r=>r.json())`)
      if((await tools()).mounted.suction[id][key]===true){await press(off);await until(async()=>(await tools()).mounted.suction[id][key]===false,'预先停气')}
      await press(on);await until(async()=>(await tools()).mounted.suction[id][key]===true,'有效开回读')
      const current=(await tools()).mounted.suction[id];assert.equal(current[key==='vacuum'?'blow':'vacuum'],false)
      await press(off);await until(async()=>(await tools()).mounted.suction[id][key]===false,'有效关回读');result.suction.push({side,key,on:true,off:true})
    }
    await capture('feedback-four-suction-controls.png')
    await nav('有趣');await ev(`document.querySelector('#functions-tab-interaction').click()`);await until(()=>ev(`!!document.querySelector('#input-tab-mouse')`),'鼠标输入');await ev(`document.querySelector('#input-tab-mouse').click();document.querySelector('.range-settings').open=true`)
    const readRange=()=>ev(`({center:[...document.querySelectorAll('.range-grid input[type=number]')].filter((_,i)=>i%2===0).map(e=>+e.value),size:[...document.querySelectorAll('.range-grid input[type=number]')].filter((_,i)=>i%2===1).map(e=>+e.value),direction:+document.querySelector('[aria-label="跟随朝向"]').value})`)
    await ev(`(()=>{const e=document.querySelector('[aria-label="跟随朝向"]');e.value='0';e.dispatchEvent(new Event('change',{bubbles:true}))})()`);await click('恢复初始范围');await until(async()=>{const r=await readRange();return r.direction===270&&JSON.stringify(r.center)==='[0,-400,700]'&&JSON.stringify(r.size)==='[800,300,500]'},'恢复真实初始范围');result.restoredRange=await readRange();await capture('feedback-handbox-restored.png')
    result.directionRanges=[]
    for(const [direction,center] of [[0,[400,0,700]],[90,[0,400,700]],[180,[-400,0,700]],[270,[0,-400,700]]]){
      await ev(`(()=>{const e=document.querySelector('[aria-label="跟随朝向"]');e.value='${direction}';e.dispatchEvent(new Event('change',{bubbles:true}))})()`)
      await until(async()=>{const r=await readRange();return r.direction===direction&&JSON.stringify(r.center)===JSON.stringify(center)},'整盒移到对应方向 '+direction)
      const actual=await readRange();assert.deepEqual(actual.size,[800,300,500]);result.directionRanges.push(actual)
      await capture('feedback-handbox-direction-'+direction+'.png')
    }
    await click('恢复初始范围')
    result.completed=true
  }catch(error){failure=error;result.failure=error.stack;try{await capture('feedback-failure.png')}catch{}}
  finally {
    try{const s=await state();if(['running','waiting','paused'].includes(s.exec?.state))await ev(`fetch('/api/motion/cancel',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}).then(r=>r.json())`)}catch(error){result.cleanupErrors.push(error.message)}
  }
  if(failure)throw failure
  assert.deepEqual(result.cleanupErrors,[])
}
module.exports={verifyUserFeedback}
