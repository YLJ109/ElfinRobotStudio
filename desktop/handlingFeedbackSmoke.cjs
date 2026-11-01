// Explicit isolated UI acceptance: real Runner and held-object motion; no scene patches.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyHandlingFeedback({window,directory,report,wait}) {
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  assert.ok(process.argv.some(arg=>arg.startsWith('--smoke-output=')),'isolated smoke profile required')
  const result=report.handlingFeedback={completed:false,cleanupErrors:[]},ev=code=>window.webContents.executeJavaScript(code)
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms)),began=Date.now(),deadline=began+330000
  async function until(check,label,ms=30000){const end=Math.min(deadline,Date.now()+ms);while(Date.now()<end){if(await check())return;await pause(80)}throw Error('搬运实际验收超时：'+label)}
  async function get(route){return ev(`fetch(${JSON.stringify(route)},{cache:'no-store',signal:AbortSignal.timeout(5000)}).then(async r=>{const v=await r.json();if(!r.ok||v.ok===false)throw Error(v.why||v.detail||'HTTP '+r.status);return v})`)}
  const challenge=()=>get('/api/challenge/handling'),state=()=>get('/api/state'),play=()=>get('/api/play/program/state')
  async function click(text){await until(()=>ev(`!![...document.querySelectorAll('.handling-challenge button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}&&!b.disabled)`),'可用按钮 '+text);await ev(`(()=>{const b=[...document.querySelectorAll('.handling-challenge button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}&&!b.disabled);b.scrollIntoView({block:'center'});b.click()})()`)}
  async function capture(name){const frames=await ev('window.__elfinScene.dprInfo().submittedFrames');await until(()=>ev(`window.__elfinScene.dprInfo().submittedFrames>=${frames+2}`),'实际提交帧',10000);fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
  let failure,ownsRun=false
  try {
    const initial=await state();assert.equal(initial.mode,'sim');assert.equal(initial.real,null);assert.ok(initial.motion_granted);assert.equal(initial.joints.known,true);assert.equal(initial.joints.q.length,6);assert.ok(initial.joints.q.every(Number.isFinite));assert.ok(!['running','waiting','paused'].includes(initial.exec?.state))
    await ev(`document.querySelector('nav button[title="分拣"]').click()`);await until(()=>ev(`!!document.querySelector('#functions-tab-handling')`),'搬运闯关分类');await ev(`document.querySelector('#functions-tab-handling').click()`);await until(()=>ev(`!!document.querySelector('.handling-challenge select[aria-label="搬运关卡"] option[value="1"]')`),'第一关加载')
    await ev(`(()=>{window.__handlingPreviousConfirm=window.confirm;window.confirm=message=>String(message).startsWith('布置本关会替换');const e=document.querySelector('[aria-label="搬运关卡"]');e.value='1';e.dispatchEvent(new Event('change',{bubbles:true}))})()`)
    await click('布置关卡');await until(async()=>{const s=await challenge();return s.level===1&&s.phase==='ready'&&s.total===1},'真实布置第一关')
    result.setup=await challenge();result.before=await state();assert.equal(Object.keys(result.setup.goals).length,1);await capture('handling-feedback-ready.png')
    await click('预检整关路径');await until(async()=>{const s=await challenge();if(['failed','cancelled'].includes(s.phase))throw Error(s.message);return s.phase==='checked'&&!!s.plan_id},'真实完整预检',115000)
    result.checked=await challenge();assert.equal(result.checked.completed,0);assert.equal(result.checked.elapsed_s,0)
    await click('执行本关');ownsRun=true;await until(async()=>{const s=await challenge();return s.phase==='running'&&s.run_id===result.checked.plan_id},'Runner接受本次计划')
    let jointMoved=false,carried=false,carryMoved=false,carryScreenshot=false,maxJointDelta=0
    const objectsBefore=new Map(result.before.objects.map(o=>[o.id,o]))
    await until(async()=>{
      const c=await challenge(),s=await state(),p=await play();result.latest={challenge:c,runner:p.execution}
      assert.equal(s.mode,'sim');assert.equal(s.real,null)
      maxJointDelta=Math.max(maxJointDelta,...s.joints.q.map((v,i)=>Math.abs(v-result.before.joints.q[i])));jointMoved=maxJointDelta>.2
      if(c.held){carried=true;const object=s.objects.find(o=>o.id===c.held),old=objectsBefore.get(c.held);if(object&&old&&Math.hypot(...object.xyz.map((v,i)=>v-old.xyz[i]))>5){carryMoved=true;if(!carryScreenshot){await capture('handling-feedback-actual-carry.png');carryScreenshot=true}}}
      if(['failed','cancelled'].includes(c.phase))throw Error(c.message+'; Runner '+JSON.stringify(p.execution))
      return c.phase==='completed'
    },'实际取放完整完成',200000)
    const terminal=await challenge(),s=await state(),p=await play();result.terminal=terminal;result.execution=p.execution;result.maxJointDelta=maxJointDelta;result.carryObserved=carried;result.carriedActualDisplacement=carryMoved
    assert.ok(jointMoved,'必须观察到真实关节变化');assert.ok(carried&&carryMoved,'必须观察到实际持件及其位移');assert.equal(terminal.completed,1);assert.equal(terminal.total,1);assert.equal(terminal.score,100);assert.equal(terminal.held,null);assert.equal(p.held,null);assert.equal(p.execution.state,'finished');assert.ok(terminal.elapsed_s>1);assert.equal(terminal.run_id,result.checked.plan_id)
    result.positions=[]
    for(const [id,goal] of Object.entries(terminal.goals)){const object=s.objects.find(o=>o.id===id);assert.ok(object);const error=Math.hypot(...object.xyz.map((v,i)=>v-goal.xyz[i]));assert.ok(error<=.5);assert.equal(object.task.slot,goal.slot);result.positions.push({id,goal:goal.xyz,actual:object.xyz,error})}
    await until(()=>ev(`document.querySelector('.handling-challenge').textContent.includes('本关完成')&&document.querySelector('.handling-challenge').textContent.includes('100 分')`),'实际UI完整成绩');await capture('handling-feedback-completed-100.png');result.completed=true
  }catch(error){failure=error;result.failure=error.stack;try{result.failureState={challenge:await challenge(),state:await state(),play:await play()};await capture('handling-feedback-failure.png')}catch(e){result.contextError=e.message}}
  finally {
    try{await ev(`if(window.__handlingPreviousConfirm){window.confirm=window.__handlingPreviousConfirm;delete window.__handlingPreviousConfirm}`)}catch(e){result.cleanupErrors.push(e.message)}
    if(ownsRun&&!result.completed)try{await ev(`fetch('/api/challenge/handling/cancel',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}).then(async r=>{if(!r.ok)throw Error('cancel HTTP '+r.status);return r.json()})`)}catch(e){result.cleanupErrors.push(e.message)}
  }
  if(failure)throw failure
  assert.deepEqual(result.cleanupErrors,[])
}
module.exports={verifyHandlingFeedback}
