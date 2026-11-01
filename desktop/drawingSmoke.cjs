// Opt-in bounded isolated drawing acceptance; only official UI starts motion.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyDrawing({window,directory,report,wait}){
 assert.equal(process.env.HUYAN_NO_REAL,'1');assert.ok(process.argv.some(v=>v.startsWith('--smoke-output=')))
 const result=report.drawing={completed:false,shapes:[],cleanupErrors:[]},ev=code=>window.webContents.executeJavaScript(code),deadline=Date.now()+1200000
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));let shapeDeadline=deadline,owns=null,failure
 function persist(){fs.writeFileSync(path.join(directory,'drawing-progress.json'),JSON.stringify(result,null,2))}
 async function until(check,label,ms=30000){const end=Math.min(deadline,shapeDeadline,Date.now()+ms);while(Date.now()<end){if(await check())return;await sleep(50)}throw Error('绘制验收超时：'+label)}
 const get=url=>ev(`fetch(${JSON.stringify(url)},{cache:'no-store',signal:AbortSignal.timeout(4000)}).then(async r=>{const v=await r.json();if(!r.ok||v.ok===false)throw Error(v.why||v.detail||'HTTP '+r.status);return v})`)
 const state=()=>get('/api/state'),drawing=()=>get('/api/drawing/state'),visual=()=>ev('window.__elfinScene.drawingInfo()')
 async function click(text){await until(()=>ev(`!![...document.querySelectorAll('.drawing-task button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}&&!b.disabled)`),'正式按钮 '+text);await ev(`(()=>{const b=[...document.querySelectorAll('.drawing-task button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}&&!b.disabled);b.scrollIntoView({block:'center'});b.click()})()`)}
 async function capture(name){const before=await ev('window.__elfinScene.dprInfo().submittedFrames');await until(()=>ev(`window.__elfinScene.dprInfo().submittedFrames>=${before+2}`),'两次实际提交帧',10000);fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
 async function enter(){await ev(`document.querySelector('nav button[title="有趣"]').click()`);await until(()=>ev(`!!document.querySelector('#functions-tab-drawing')`),'绘制分类');await ev(`document.querySelector('#functions-tab-drawing').click()`);await until(()=>ev(`!!document.querySelector('.drawing-task')`),'绘制组件')}
 async function configure(shape){
  await ev(`(()=>{const root=document.querySelector('.drawing-task'),select=root.querySelector('.drawing-config select');select.value=${JSON.stringify(shape)};select.dispatchEvent(new Event('change',{bubbles:true}))})()`);await sleep(50)
  await ev(`(()=>{const root=document.querySelector('.drawing-task');for(const [label,value] of [['速度 / %',100],['宽度 / mm',280],['直径 / mm',280],['高度 / mm',240],['幅度 / °',5],['重复',1]]){const e=[...root.querySelectorAll('label')].find(l=>l.textContent.trim()===label)?.querySelector('input');if(e){e.value=String(value);e.dispatchEvent(new Event('input',{bubbles:true}))}}const selects=root.querySelectorAll('.drawing-config select');if(selects.length>1){selects[1].value='xz';selects[1].dispatchEvent(new Event('change',{bubbles:true}))}})()`)
  await click('预检路径');await until(async()=>{const d=await drawing();if(['failed','invalid','expired'].includes(d.phase))throw Error(d.why);return d.config?.shape===shape&&d.phase==='ready'},'完整正式预检 '+shape,100000)
  const d=await drawing();assert.equal(d.config.speed_pct,100);assert.equal(d.config.repeats,1);assert.equal(d.config.plane,['nod','shake'].includes(shape)?'xy':'xz');assert.deepEqual(d.config.range_size_mm,[900,300,500]);assert.deepEqual(d.config.range_center_mm,[-500,0,700]);assert.equal(d.config.center_policy,'range_lower');assert.deepEqual(d.bounds.min,[-950,-150,450]);assert.deepEqual(d.bounds.max,[-50,150,950]);assert.equal(d.trace_tcp.valid,true);assert.equal(d.trace_tcp.kind,'left_cup_tip');assert.ok(Math.hypot(...d.trace_tcp.offset_in_snapshot_tcp_mm)>50,'必须使用真实杯尖，而非安装板中心');if(!['nod','shake'].includes(shape))assert.equal(d.config.width_mm,280);owns=d.plan_id;await click('查看轨迹');return d
 }
 const rpyMatrix=p=>{const [a,b,c]=p.map(v=>v*Math.PI/180),ca=Math.cos(a),sa=Math.sin(a),cb=Math.cos(b),sb=Math.sin(b),cc=Math.cos(c),sc=Math.sin(c);return [cc*cb,cc*sb*sa-sc*ca,cc*sb*ca+sc*sa,sc*cb,sc*sb*sa+cc*ca,sc*sb*ca-cc*sa,-sb,cb*sa,cb*ca]}
 function rotationError(a,b){const x=rpyMatrix(a),y=rpyMatrix(b);return Math.acos(Math.max(-1,Math.min(1,(x.reduce((s,v,i)=>s+v*y[i],0)-1)/2)))*180/Math.PI}
 function tcp(s,definition){
  assert.equal(s.joints.known,true);assert.equal(definition?.valid,true)
  const p=[...s.tcp.xyz_mm,...s.tcp.rpy_deg],offset=definition.offset_in_snapshot_tcp_mm;assert.equal(p.length,6);assert.ok(p.every(Number.isFinite));assert.equal(offset.length,3);assert.ok(offset.every(Number.isFinite))
  const matrix=rpyMatrix(p.slice(3))
  return [...p.slice(0,3).map((v,i)=>v+offset.reduce((sum,d,j)=>sum+matrix[i*3+j]*d,0)),...p.slice(3)]
 }
 async function run(shape,{cancel=false,pause=false}={}){
  shapeDeadline=Math.min(deadline,Date.now()+180000)
  const entry={shape,cancel,completed:false,samples:[],sampleOverflow:0},start=Date.now();result.shapes.push(entry);persist()
  entry.checked=await configure(shape);entry.resourcesBefore=await ev('window.__elfinScene.resourceInfo()');entry.renderBefore=await ev('window.__elfinScene.dprInfo()');const before=await state(),initialQ=before.joints.q.slice()
  await capture('drawing-'+shape+(cancel?'-cancel':'')+'-outline.png');await click('执行')
  let moved=false,traceSeen=false,paused=false,strokeShot=false,maxAngle=0
  await until(async()=>{
   const d=await drawing(),s=await state(),v=await visual(),p=tcp(s,d.trace_tcp)
   assert.equal(d.plan_id,owns);assert.equal(s.mode,'sim');assert.equal(s.real,null);assert.ok(v.drawCalls<=5)
   assert.equal(d.trace_tcp.binding_id,entry.checked.trace_tcp.binding_id);assert.deepEqual(v.bounds,{min:d.bounds.min,max:d.bounds.max},'显示框必须等于预检实际消费的范围')
   if(['failed','expired','invalid'].includes(d.phase)||d.phase==='cancelled'&&!cancel)throw Error(d.why)
   moved=moved||s.joints.q.some((q,i)=>Math.abs(q-initialQ[i])>.15)
   maxAngle=Math.max(maxAngle,...p.slice(3).map((angle,i)=>Math.abs(((angle-d.config.center[i+3]+540)%360)-180)))
   const tracing=s.exec?.current?.drawing_plan_id===owns&&s.exec.current.drawing_trace===true
   if(tracing)assert.ok(p.slice(0,3).every((value,i)=>value>=d.bounds.min[i]-.01&&value<=d.bounds.max[i]+.01),'实际工具尖端必须在框内绘制')
   traceSeen=traceSeen||tracing
   if(entry.samples.length<4096)entry.samples.push({at_ms:Date.now()-start,phase:d.phase,q:s.joints.q,tcp:p,progress:d.progress,trace:tracing,visual:v.samples,segments:v.segments});else entry.sampleOverflow++
   if(v.completedEffect&&d.phase!=='completed'){const terminalNow=await drawing();assert.equal(terminalNow.plan_id,owns);assert.equal(terminalNow.phase,'completed','非明确完成终态不得播放完成特效');assert.equal(terminalNow.progress.completed,terminalNow.progress.total)}
   if(!strokeShot&&v.samples>3&&(v.segments>1||['nod','shake'].includes(shape))){await click('查看轨迹');await capture('drawing-'+shape+(cancel?'-cancel':'')+'-actual-stroke.png');strokeShot=true}
   if(pause&&!paused&&tracing&&v.samples>3&&d.progress.completed<d.progress.total-1){
    await click('暂停');await until(async()=>(await drawing()).phase==='paused','暂停已确认');await sleep(150);const n=(await visual()).samples;await sleep(450);assert.equal((await visual()).samples,n,'暂停期间不可追加真实轨迹');await capture('drawing-'+shape+'-paused.png');await click('继续');await until(async()=>['running','completed'].includes((await drawing()).phase),'恢复真实执行');paused=true
   }
   if(cancel&&tracing&&v.samples>4&&d.progress.completed<d.progress.total){await click('停止');await until(async()=>(await drawing()).phase==='cancelled','部分取消终态');return true}
   if(d.phase==='completed'){if(cancel)throw Error('取消场景未及时取消而完整完成');return true}
   persist();return false
  },'实际六轴执行 '+shape,180000)
  entry.terminal=await drawing();entry.state=await state();entry.visual=await visual();entry.maxAngleDeg=maxAngle;entry.nonzeroJointMotion=moved;entry.traceObserved=traceSeen;entry.resourcesAfter=await ev('window.__elfinScene.resourceInfo()');entry.renderAfter=await ev('window.__elfinScene.dprInfo()')
  assert.ok(moved&&traceSeen);assert.ok(entry.visual.samples>2);if(!['nod','shake'].includes(shape))assert.ok(entry.visual.segments>0);else assert.ok(maxAngle>2,'实际ABC摆幅必须超过2度')
  if(cancel){assert.equal(entry.terminal.phase,'cancelled');assert.ok(entry.terminal.progress.completed<entry.terminal.progress.total);assert.equal(entry.visual.completedEffect,false);await sleep(2900);assert.equal((await visual()).animating,false);await capture('drawing-circle-partial-cancel-no-celebration.png')}
  else{
   assert.equal(entry.terminal.phase,'completed');assert.equal(entry.state.exec.state,'finished');assert.equal(entry.terminal.progress.completed,entry.terminal.progress.total)
   const p=tcp(entry.state,entry.terminal.trace_tcp),c=entry.terminal.config.center;entry.centerErrorMm=Math.hypot(...p.slice(0,3).map((v,i)=>v-c[i]));entry.rotationErrorDeg=rotationError(p.slice(3),c.slice(3));assert.ok(entry.centerErrorMm<=2);assert.ok(entry.rotationErrorDeg<=.2)
   await until(async()=>(await visual()).completedEffect===true,'同计划实际终态特效',2000);await capture('drawing-'+shape+'-completed-starlight.png');await sleep(2900);const stopped=await visual();assert.equal(stopped.animating,false);await sleep(200);assert.equal((await visual()).animating,false,'重复完成轮询不得重播');if(pause)assert.ok(paused)
  }
  assert.ok(entry.renderAfter.submittedFrames>entry.renderBefore.submittedFrames);entry.elapsed_ms=Date.now()-start;entry.completed=true;persist()
 }
 try{
  const initial=await state();assert.equal(initial.mode,'sim');assert.equal(initial.real,null);assert.ok(initial.motion_granted);await enter()
  await until(async()=>{const v=await visual();return v.frameSegments>=12&&!!v.bounds&&!v.plan_id&&v.samples===0},'进入即显示无运动许可的绘制范围');result.draftRange=await visual();await capture('drawing-initial-range-no-motion.png');assert.deepEqual((await state()).joints.q,initial.joints.q)
  for(const shape of ['heart','square','rectangle','circle','nod','shake'])await run(shape,{pause:shape==='heart'})
  const memory=result.shapes.map(r=>r.resourcesAfter);result.resourceSamples=memory
  assert.ok(memory.at(-1).geometries<=Math.max(memory[0].geometries,memory[1].geometries)+2,'重复形状不应持续增加几何资源');assert.ok(memory.at(-1).textures<=Math.max(memory[0].textures,memory[1].textures)+2)
  await run('circle',{cancel:true})
  shapeDeadline=deadline;const ready=await configure('square'),before=await state();await ev(`void(window.__drawingPreviousConfirm=window.confirm,window.confirm=()=>true)`);await click('载入程序');await until(()=>ev(`document.querySelector('nav button[title="程序"]').getAttribute('aria-current')==='page'`),'仅载入编辑器');await sleep(400);const after=await state();assert.deepEqual(after.joints.q,before.joints.q);assert.equal(after.exec.state,before.exec.state);result.programLoad={program:ready.program,jointsUnchanged:true};await capture('drawing-load-program-no-motion.png')
  await until(async()=>(await visual()).owner===null,'离页释放当前绘制视觉');result.released=await visual();result.completed=true;persist()
 }catch(error){failure=error;result.failure=error.stack;try{result.context={drawing:await drawing(),state:await state(),visual:await visual(),ui:await ev(`({task:document.querySelector('.drawing-task')?.textContent,footer:document.querySelector('.statusbar')?.textContent})`)};shapeDeadline=Date.now()+10000;await capture('drawing-failure.png')}catch(e){result.contextError=e.message}persist()}
 finally{
  try{await ev(`if(window.__drawingPreviousConfirm){window.confirm=window.__drawingPreviousConfirm;delete window.__drawingPreviousConfirm}`)}catch(e){result.cleanupErrors.push(e.message)}
  if(failure&&owns)try{await ev(`fetch('/api/drawing/cancel',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({plan_id:${JSON.stringify(owns)}})}).then(async r=>{if(!r.ok)throw Error('cancel HTTP '+r.status);return r.json()})`)}catch(e){result.cleanupErrors.push(e.message)}
  persist()
 }
 if(failure)throw failure
 assert.deepEqual(result.cleanupErrors,[])
}
module.exports={verifyDrawing}
