// Explicit isolated acceptance: rendered camera pixels, official task UI, real simulation time.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyPractical({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const letterFull=process.argv.includes('--smoke-letter-full'),began=Date.now(),overallDeadline=letterFull?began+900000:Infinity
  let verifying=true
  const result=report.practical={completed:false,cleanupErrors:[],letterFull},evaluate=code=>window.webContents.executeJavaScript(code)
  const request=(url,payload)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(url)},{signal:AbortSignal.timeout(${letterFull?Math.max(1,Math.min(verifying?110000:10000,verifying?overallDeadline-Date.now():10000)):110000}),cache:'no-store',...(${JSON.stringify(payload)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(payload)})})});const v=await r.json();if(!r.ok||v.ok===false)throw Error(v.why||v.detail||'HTTP '+r.status);return v})()`)
  async function until(check,label,seconds=100){const end=Math.min(Date.now()+seconds*1000,verifying?overallDeadline:Infinity);while(Date.now()<end){if(await check())return;await new Promise(resolve=>setTimeout(resolve,80))}throw Error('实用任务验收超时：'+label)}
  async function click(label){await until(()=>evaluate(`!![...document.querySelectorAll('.practical-task button')].find(b=>b.textContent.trim()===${JSON.stringify(label)}&&!b.disabled)`),'入口可用：'+label,10);await evaluate(`(()=>{const b=[...document.querySelectorAll('.practical-task button')].find(b=>b.textContent.trim()===${JSON.stringify(label)});if(!b||b.disabled)throw Error('入口不可用：'+${JSON.stringify(label)});b.click()})()`)}
  async function tab(kind){await evaluate(`document.querySelector('#functions-tab-${kind}').click()`);await wait(()=>evaluate(`!!document.querySelector('.practical-task[data-task="${kind}"]')`));await until(()=>evaluate(`!![...document.querySelectorAll('.practical-task button')].find(b=>['查找目标','检查托盘','预览格位'].includes(b.textContent.trim())&&!b.disabled)`),'正式入口就绪',10)}
  async function capture(name){await evaluate(`new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
  async function grid(columns,rows,cells){
    await evaluate(`(()=>{for(const [label,value] of [['列',${columns}],['行',${rows}]]){const e=[...document.querySelectorAll('.practical-task label')].find(e=>e.textContent.trim()===label).querySelector('input');e.value=String(value);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))}})()`)
    await wait(()=>evaluate(`document.querySelectorAll('.recipe-grid select').length===${columns*rows}`))
    for(let i=0;i<cells.length;i++)await evaluate(`(()=>{const e=document.querySelectorAll('.recipe-grid select')[${i}];e.selectedIndex=${cells[i]===null?0:['red','green','blue'].indexOf(cells[i])+1};e.dispatchEvent(new Event('change',{bubbles:true}))})()`)
    await until(()=>evaluate(`!![...document.querySelectorAll('.practical-task button')].find(b=>b.textContent.trim()==='预览格位'&&!b.disabled)`),'格位编辑及旧票撤销完成',10)
  }
  async function checked(readonly){await click(readonly?'预览识别':'预览格位');await until(()=>evaluate(`document.querySelector('.practical-task [role=status]')?.textContent.includes('等待预检')`),'预览',10);await click(readonly?'核对当前画面':'预检完整路径');await until(async()=>{const value=await evaluate(`({text:document.querySelector('.practical-task').textContent,check:[...document.querySelectorAll('.practical-task button')].some(b=>b.textContent.trim()==='执行取放'&&!b.disabled)})`);if(value.text.includes('请求未确认')||value.text.includes('预检未通过'))throw Error(value.text);return value.check},'完整预检',110)}
  let vision,group,failure
  try{
    const state=await request('/api/state',null);assert.equal(state.mode,'sim');assert.equal(state.real,null)
    if(!letterFull || process.argv.includes('--smoke-practical')){
    group=(await request('/api/play/setup',{mode:'sorting',criterion:'shape',shape_counts:{rectangle:0,circle:1}})).group
    await evaluate(`document.querySelector('nav button[title="分拣"]').click()`);await wait(()=>evaluate(`!!document.querySelector('#functions-tab-find')`))
    vision=await require('./sortingVisionSmoke.cjs').prepareSortingVision({window,request,until,directory,report})
    await tab('find');await click('查找目标')
    await until(()=>evaluate(`!!document.querySelector('.practical-task .target-image')`),'实际同帧目标指向',5)
    result.find=await request('/api/vision/tasks/state',null);await capture('task-find-pointer.png')
    await tab('count')
    await evaluate(`(()=>{const e=[...document.querySelectorAll('.practical-task label')].find(e=>e.textContent.startsWith('预期数量')).querySelector('input');e.value='2';e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`)
    await click('检查托盘')
    await until(()=>evaluate(`document.querySelector('.practical-task').textContent.includes('缺少 1')`),'真实识别缺件',5)
    result.count=await request('/api/vision/tasks/state',null);await capture('task-count-missing.png')
    await tab('kit');await grid(2,1,['red','red']);await click('预览格位')
    await until(()=>evaluate(`document.querySelector('.practical-task').textContent.includes('实际识别物料不足')`),'缺料明确拒绝',5)
    assert.equal((await request('/api/play/program/state',null)).active,false);await capture('task-insufficient-material.png')
    await grid(5,1,['red',null,null,null,null]);await click('预览格位')
    await until(()=>evaluate(`document.querySelector('.practical-task').textContent.includes('格位容量不足')`),'容量明确拒绝',5);await capture('task-capacity-rejected.png')
    for(const kind of ['kit','pattern']){
      await tab(kind)
      await grid(kind==='kit'?2:1,1,kind==='kit'?['red','green']:['red'])
      await evaluate(`(()=>{window.confirm=()=>true;return true})()`);await click('按配方布置物料')
      await until(async()=>{const current=await request('/api/state',null);const next=current.objects.find(o=>o.task?.role==='piece')?.task?.group;if(next&&next!==group){group=next;return true}return false},'按配方事务布置真实物料',10)
      await vision.stop();vision=await require('./sortingVisionSmoke.cjs').prepareSortingVision({window,request,until,directory,report,colors:['red','green'],expectedShape:'rectangle'})
      await checked(false);await vision.assertFresh()
      if(kind==='pattern'){
        await click('取消');await until(()=>evaluate(`document.querySelector('.practical-task [role=status]')?.textContent.includes('已取消')`),'预检后取消实际读回',5)
        assert.equal((await request('/api/play/program/state',null)).active,false);await capture('task-pattern-cancelled.png');await checked(false)
      }
      await click('执行取放')
      let held=false,moved=false
      await until(async()=>{const task=await request('/api/assembly/state',null),state=await request('/api/state',null);if(task.held){held=true;const piece=state.objects.find(v=>v.id===task.held);if(piece?.xyz[1]>-300)moved=true}if(['failed','cancelled'].includes(task.phase))throw Error(task.message+' '+state.exec.error);result[kind]=task;fs.writeFileSync(path.join(directory,'practical-progress.json'),JSON.stringify({kind,phase:task.phase,completed:task.completed,total:task.total,held:task.held,line:state.exec.current?.line}));return task.phase==='completed'},kind+'实际取放',kind==='kit'?180:110)
      assert.equal(result[kind].completed,kind==='kit'?2:1);assert.equal(result[kind].total,kind==='kit'?2:1);assert.equal(result[kind].held,null);assert.ok(held&&moved,'必须观察实际携带移动')
      await until(()=>evaluate(`document.querySelector('.practical-task [role=status]')?.textContent.includes('已完成')`),'终态实际UI读回',5);await capture('task-'+kind+'-completed.png')
    }
    }else{
      await evaluate(`document.querySelector('nav button[title="分拣"]').click()`)
      await wait(()=>evaluate(`!!document.querySelector('#functions-tab-pattern')`))
      await evaluate(`document.querySelector('#functions-tab-pattern').click()`)
      await wait(()=>evaluate(`!!document.querySelector('.practical-task[data-task="pattern"]')`))
      await evaluate(`(()=>{window.confirm=()=>true;return true})()`)
    }
    // The built-in letter must also have enough real material and fit its grid.
    await click('填入图案');await click('按配方布置物料')
    await until(async()=>{const s=await request('/api/state',null),pieces=s.objects.filter(o=>o.task?.role==='piece');if(pieces.length!==10)return false;assert.ok(pieces.every(p=>p.size[0]===38));result.letterRecipe={symbol:'A',pieces:pieces.length,width:38,runner:s.exec.state};return true},'字母A真实物料与小格容量',10)
    await vision?.stop();vision=await require('./sortingVisionSmoke.cjs').prepareSortingVision({window,request,until,directory,report,colors:['red'],expectedShape:'rectangle'})
    await click('预览格位');await until(async()=>{const s=await request('/api/assembly/state',null);result.letterPreview=s;return s.phase==='preview'&&s.total===10},'字母A真实观测格位预览',10);await capture('task-letter-A-preview.png')
    if(letterFull){
      const initial=await request('/api/state',null)
      assert.equal(initial.mode,'sim');assert.equal(initial.real,null)
      assert.ok(initial.motion_granted,'当前仿真没有运动资格，不能跳过就绪门禁')
      const originals=new Map(initial.objects.filter(o=>o.task?.role==='piece').map(o=>[o.id,o]))
      assert.equal(originals.size,10);assert.ok([...originals.values()].every(p=>p.size[0]===38&&p.size[1]===38&&p.size[2]===24))
      const observed=await vision.assertFresh();result.letterFreshFrame={seq:observed.result.frame_seq,captured_at:observed.result.captured_at,source:observed.result.source}
      await checked(false);await vision.assertFresh()
      const checkedState=await request('/api/assembly/state',null)
      assert.equal(checkedState.phase,'checked');assert.equal(checkedState.total,10);assert.ok(checkedState.plan_id)
      assert.equal(new Set(checkedState.placements.map(p=>p.object_id)).size,10)
      assert.equal(new Set(checkedState.placements.map(p=>p.slot)).size,10)
      await capture('task-letter-A-checked.png')
      await click('执行取放')
      const carried=new Set(),placed=new Set();let runningObserved=false
      await until(async()=>{
        const task=await request('/api/assembly/state',null),state=await request('/api/state',null)
        assert.equal(state.mode,'sim');assert.equal(state.real,null)
        if(task.phase==='running')runningObserved=true
        if(['failed','cancelled'].includes(task.phase))throw Error(task.message+' '+state.exec.error)
        if(task.held){
          const piece=state.objects.find(o=>o.id===task.held),original=originals.get(task.held)
          assert.ok(piece&&original,'持件必须来自本次10件真实物料')
          if(Math.hypot(...piece.xyz.map((v,i)=>v-original.xyz[i]))>5)carried.add(piece.id)
        }
        for(const placement of task.placements||[])if(placement.confirmed)placed.add(placement.object_id)
        result.letterExecution={...task,runningObserved,carried:[...carried],confirmed:[...placed],runner:state.exec.state,elapsedMs:Date.now()-began}
        fs.writeFileSync(path.join(directory,'letter-full-progress.json'),JSON.stringify(result.letterExecution,null,2))
        return task.phase==='completed'
      },'字母A完整10件真实执行',900)
      const final=await request('/api/state',null),task=await request('/api/assembly/state',null)
      assert.ok(runningObserved);assert.equal(task.phase,'completed');assert.equal(task.completed,10);assert.equal(task.total,10);assert.equal(task.held,null);assert.equal(final.exec.state,'finished')
      assert.equal(carried.size,10,'逐件必须观察真实持件移动，不能只有累计数')
      assert.equal(placed.size,10);assert.equal(task.placements.length,10)
      result.letterActualPlacements=task.placements.map(placement=>{
        const actual=final.objects.find(o=>o.id===placement.object_id);assert.ok(actual)
        assert.equal(actual.task.slot,placement.slot);assert.equal(placement.confirmed,true)
        const error=Math.hypot(...actual.xyz.map((v,i)=>v-placement.xyz[i]));assert.ok(error<=.5,'实际工件没有到达对应格位')
        return {...placement,actual_xyz:actual.xyz,error_mm:error}
      })
      await until(()=>evaluate(`document.querySelector('.practical-task [role=status]')?.textContent.includes('已完成')`),'10件终态UI读回',5)
      await capture('task-letter-A-completed.png')
      // Frame the actual finished B layout, away from the log/camera overlays.
      // Wait for submitted renders, not merely animation callbacks.
      const beforeFrames=await evaluate(`(()=>{const s=window.__elfinScene;const before=s.dprInfo().submittedFrames;if(!s.setView({theta:0,phi:.15,r:1.1,target:[.45,.02,-.35]}))throw Error('图案视角不可用');return before})()`)
      await until(()=>evaluate(`window.__elfinScene.dprInfo().submittedFrames>=${beforeFrames+2}`),'实际完成图案渲染',5)
      result.letterPatternView=await evaluate(`window.__elfinScene.getView()`)
      await capture('task-letter-A-completed-pattern.png');result.letterElapsedMs=Date.now()-began
    }
    result.completed=true
  }catch(error){failure=error;result.error=error.stack||String(error);try{await capture('task-failure.png')}catch{}}
  finally{
    verifying=false
    for(const [name,action] of [['停止程序',()=>request('/api/program/stop',{})],['释放持件',()=>request('/api/play/program/release',{})],['关闭测试视觉',()=>vision?.stop()]])try{await action()}catch(error){result.cleanupErrors.push(name+': '+error.message)}
  }
  if(failure)throw failure
  assert.deepEqual(result.cleanupErrors,[])
}
module.exports={verifyPractical}
