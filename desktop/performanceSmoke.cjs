// Observations from the running renderer and owned simulation, never requested FPS.
const assert=require('node:assert/strict')
const {execFile}=require('node:child_process')
const {promisify}=require('node:util')
const os=require('node:os')
const exec=promisify(execFile)
async function backendResources(pid){
  if(!Number.isSafeInteger(pid) || pid<=0)throw Error('后端PID无效')
  if(process.platform!=='win32')throw Error('此采样器仅支持Windows Get-Process')
  const command=`$backendSample=Get-Process -Id ${pid} -ErrorAction Stop; @{pid=$backendSample.Id;cpu_seconds=$backendSample.TotalProcessorTime.TotalSeconds;working_set_bytes=$backendSample.WorkingSet64;private_bytes=$backendSample.PrivateMemorySize64}|ConvertTo-Json -Compress`
  const {stdout}=await exec('powershell.exe',['-NoProfile','-NonInteractive','-Command',command],{windowsHide:true,timeout:5000,maxBuffer:65536,encoding:'utf8'})
  const value=JSON.parse(stdout.trim().replace(/^\uFEFF/,''));assert.equal(value.pid,pid)
  return {...value,at:Date.now()}
}
async function measureLoad({app,window,report,seconds=60,getOwnedBackendPid=()=>null}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const evaluate=code=>window.webContents.executeJavaScript(code)
  const request=(route,payload)=>evaluate(`(async()=>{const response=await fetch(${JSON.stringify(route)},{cache:'no-store',signal:AbortSignal.timeout(10000),...(${JSON.stringify(payload)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(payload)})})});const value=await response.json();if(!response.ok||value.ok===false)throw Error(value.why||value.error||'HTTP '+response.status);return value})()`)
  const safety=await request('/api/state',null)
  assert.equal(safety.mode,'sim');assert.equal(safety.real,null)
  assert.ok(!['running','waiting','paused'].includes(safety.exec?.state),'不得覆盖其它运行任务')
  const backend=report.backendLoad={measured:false,reason:'尚未获取自有后端PID'}
  let backendBefore,pid
  try{
    pid=getOwnedBackendPid()
    if(!Number.isSafeInteger(pid) || pid<=0)throw Error('当前不是具有已认证PID的自有后端')
    backendBefore=await backendResources(pid);backend.pid=pid;backend.before=backendBefore
  }catch(error){backend.reason='未测：'+error.message}
  const before=app.getAppMetrics()
  let measurements,taskOwned=false,failure
  const task=report.loadTask={source:'LOOP 200\nSPEED 10\nWAIT 0.5\nEND',program_library_written:false}
  try{
  await request('/api/ready',{then_enable:true})
  const loaded=await request('/api/program/load',{src:task.source})
  taskOwned=true
  assert.equal(loaded.steps.length,400);assert.ok(loaded.steps.every((step,index)=>index%2===0?step.kind==='speed'&&step.pct===10:step.kind==='wait'&&step.sec===.5),'负载程序只能包含实际仿真速度设置与WAIT，不包含运动或IO')
  task.start=await request('/api/program/start',{})
  assert.ok(['running','waiting'].includes(task.start.state),'必须由正式Runner实际启动任务')
  measurements=await evaluate(`(async()=>{
    performance.setResourceTimingBufferSize(10000);
    const times=[],latency=[],taskLatency=[],ages=[],sequences=new Set(),errors=[],taskSamples=[],logRows=new Map();let iteration=0;
    const sceneBefore=window.__elfinScene.dprInfo();
    const started=performance.now(),until=started+${seconds*1000};
    let last=started,active=true;
    const frame=t=>{if(!active)return;times.push(t-last);last=t;requestAnimationFrame(frame)};
    requestAnimationFrame(frame);
    try{
      while(performance.now()<until){
        const began=performance.now();
        try{
          const r=await fetch('/api/vision/workspace/state',{cache:'no-store',signal:AbortSignal.timeout(2000)});
          if(!r.ok)throw Error('HTTP '+r.status);
          const v=await r.json();latency.push(performance.now()-began);
          if(v.result?.source==='virtual' && Number.isFinite(v.result.captured_at)){
            ages.push(Math.max(0,Date.now()-v.result.captured_at*1000));
            sequences.add(v.result.frame_seq);
          }
          if(iteration++%5===0){
            const taskBegan=performance.now();
            const taskResponse=await fetch('/api/program',{cache:'no-store',signal:AbortSignal.timeout(2000)});
            if(!taskResponse.ok)throw Error('任务回读HTTP '+taskResponse.status);
            const runner=(await taskResponse.json()).runner;taskLatency.push(performance.now()-taskBegan);
            taskSamples.push({state:runner.state,index:runner.index,completed_index:runner.completed_index,total:runner.total});
            if(!['running','waiting'].includes(runner.state))throw Error('负载期间WAIT任务不再执行：'+runner.state);
            const logsResponse=await fetch('/api/logs?n=5&kind=program',{cache:'no-store',signal:AbortSignal.timeout(2000)});
            if(!logsResponse.ok)throw Error('日志回读HTTP '+logsResponse.status);
            for(const row of (await logsResponse.json()).rows || [])logRows.set(row.seq,row);
          }
        }catch(e){errors.push(String(e))}
        await new Promise(resolve=>setTimeout(resolve,100));
      }
    }finally{active=false}
    const stats=a=>{if(!a.length)return {n:0,p50:null,p95:null,max:null};const s=[...a].sort((a,b)=>a-b);return {n:a.length,p50:s[Math.ceil(s.length*.5)-1],p95:s[Math.ceil(s.length*.95)-1],max:s.at(-1)}};
    const duration=performance.now()-started;
    const renderer=window.__elfinScene.dprInfo();
    return {duration_ms:duration,animation_callback_fps:times.length*1000/duration,animation_callback_interval_ms:stats(times),
      scene_submitted_fps:(renderer.submittedFrames-sceneBefore.submittedFrames)*1000/duration,
      request_ms:stats(latency),recognition_age_ms:stats(ages),observed_result_fps:sequences.size*1000/duration,
      task_request_ms:stats(taskLatency),task_samples:taskSamples,program_log_rows:[...logRows.values()],
      renderer,resources:window.__elfinScene.resourceInfo(),
      heap:performance.memory?{used:performance.memory.usedJSHeapSize,total:performance.memory.totalJSHeapSize}:null,errors};
  })()`)
  }catch(error){failure=error}
  finally{
    if(taskOwned){
      const stoppedAt=performance.now()
      try{
        task.stop_ack=await request('/api/program/stop',{})
        task.stop_request_ms=performance.now()-stoppedAt
        const end=Date.now()+5000
        do{task.stop_readback=(await request('/api/program',null)).runner;if(['aborted','idle'].includes(task.stop_readback.state))break;await new Promise(resolve=>setTimeout(resolve,50))}while(Date.now()<end)
        assert.ok(['aborted','idle'].includes(task.stop_readback.state),'停止请求后必须读回真实aborted/idle')
        task.stop_confirmed_ms=performance.now()-stoppedAt
      }catch(error){task.stop_error=error.message;failure=failure?new AggregateError([failure,error],'采样及任务停止失败'):error}
    }
    if(backendBefore)try{
      const after=await backendResources(pid),duration=(after.at-backendBefore.at)/1000,cpu=after.cpu_seconds-backendBefore.cpu_seconds
      assert.ok(duration>0 && cpu>=0)
      Object.assign(backend,{measured:true,reason:'',after,window_s:duration,cpu_seconds_delta:cpu,cpu_percent_one_core:cpu/duration*100,cpu_percent_machine:cpu/duration*100/os.availableParallelism(),logical_cpus:os.availableParallelism()})
    }catch(error){backend.measured=false;backend.reason='未测完整CPU区间：'+error.message}
  }
  if(failure)throw failure
  report.load={...measurements,processes_before:before,processes_after:app.getAppMetrics(),
    cpu_scope:'Electron进程见processes_before/after；自有后端CPU/工作集/私有内存见backendLoad，失败明确未测',
    gpu_features:app.getGPUFeatureStatus(),gpu_utilization_percent:null,
    gpu_utilization_note:'Electron未提供GPU使用率；功能状态和提交耗时不等于GPU使用率'}
  assert.ok(measurements.animation_callback_interval_ms.n>0 && measurements.scene_submitted_fps>0,'必须采到实际动画回调和场景提交')
  assert.ok(measurements.recognition_age_ms.n>0,'必须采到实际虚拟图像检测结果')
  assert.ok(measurements.task_samples.length>0 && measurements.task_samples.some(value=>value.state==='waiting'),'必须采到正式Runner的实际等待任务')
  assert.ok(measurements.program_log_rows.filter(row=>String(row.text).includes('SPEED 10')).length>=2,'负载期间必须采到正式执行器产生的多条速度步骤日志')
  assert.equal(measurements.errors.length,0,'负载采样不应掩盖API错误')
}
module.exports={measureLoad}
