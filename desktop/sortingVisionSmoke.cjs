// Opt-in isolated smoke only: actual Three pixels -> production HSV -> calibration.
// The six reference points are a fixed test plane, never object-derived detections.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
function sortingCameraInstallation(m){
  const columns=[[-m[4],-m[5],-m[6]],[m[0],m[1],m[2]],[m[8],m[9],m[10]]]
  const delta=[.45-m[12],.45-m[13],.35-m[14]]
  return {offset_mm:columns.map(axis=>axis.reduce((sum,v,i)=>sum+v*delta[i],0)*1000),direction:columns.map(axis=>-axis[1]*1000),width:640,fov:55,fps:15}
}
function sortingCalibrationPoints(m,width,height,fov){
  const tan=Math.tan(fov*Math.PI/360),aspect=width/height
  return [[280,-520],[620,-520],[620,-180],[280,-180],[390,-410],[510,-290]].map(([x,y])=>{
    const delta=[x/1000-m[12],.004-m[13],-y/1000-m[14]]
    const local=[0,4,8].map(offset=>delta.reduce((sum,v,i)=>sum+v*m[offset+i],0))
    if(local[2]>=0)throw Error('标定参考平面不在镜头前方')
    const px=(local[0]/(-local[2]*tan*aspect)+1)*width/2,py=(1-local[1]/(-local[2]*tan))*height/2
    if(px<0||px>=width||py<0||py>=height)throw Error('标定参考点超出真实视野')
    return [px,py,x,y]
  })
}
async function waitForFreshVision(read,ready){
  const deadline=performance.now()+1500,message='必须持续采集新帧，而非重复静态旧结果'
  for(;;){
    const remaining=deadline-performance.now();assert.ok(remaining>0,message)
    let timer,current
    try{current=await Promise.race([read(),new Promise(resolve=>{timer=setTimeout(()=>resolve(null),remaining)})])}
    finally{clearTimeout(timer)}
    assert.ok(current&&performance.now()<=deadline,message)
    assert.ok(!current.error,current.error || '实际视觉检测失败')
    assert.equal(current.stale,false);assert.equal(current.result?.source,'virtual');assert.equal(current.result?.calibrated,true)
    assert.equal(current.virtual_session,ready.virtual_session)
    assert.equal(current.result.source_key,ready.result.source_key)
    const age=Date.now()/1000-current.result.captured_at
    assert.ok(Number.isFinite(age)&&age>=0&&age<1.5,'实际采集帧已过期或时间无效')
    assert.ok(Number.isSafeInteger(current.result.frame_seq),'实际帧序号无效')
    if(current.result.frame_seq>ready.result.frame_seq){
      assert.ok(current.result.frame_seq>ready.result.frame_seq,message)
      return current
    }
    await new Promise(resolve=>setTimeout(resolve,Math.min(20,Math.max(0,deadline-performance.now()))))
  }
}
async function prepareSortingVision({window,request,until,directory,report,colors=['red'],expectedShape='circle'}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const evaluate=script=>window.webContents.executeJavaScript(script)
  const state=await request('/api/state',null);assert.equal(state.mode,'sim');assert.equal(state.real,null)
  const previous=(await request('/api/vision/workspace/state',null)).config
  const diagnostic=process.argv.includes('--smoke-diagnostic')
  if(diagnostic)await evaluate(`(()=>{
    window.__sortingDiagnostic={frames:[],states:[]};window.__sortingOriginalFetch=window.fetch;
    window.fetch=async(...args)=>{
      let frame=null;
      if(String(args[0]).endsWith('/api/vision/workspace/virtual')){
        const body=JSON.parse(args[1].body);frame={sentAt:Date.now(),session:body.session,key:body.view_key,captured_at:body.captured_at};
        window.__sortingDiagnostic.frames.push(frame);window.__sortingDiagnostic.lastUpload=body.image_base64;
      }
      const response=await window.__sortingOriginalFetch(...args);
      if(frame){frame.status=response.status;response.clone().json().then(value=>{frame.reply=value;frame.receivedAt=Date.now()}).catch(()=>{})}
      return response;
    };
  })()`)
  let lastSource
  try{
  const config=await evaluate(`(()=>{
    const scene=window.__elfinScene;if(!scene)throw Error('实际Three场景未就绪');
    scene.configureVirtualCamera({enabled:false,offset_mm:[0,0,0],direction:[0,0,-1],width:640,fov:55});
    return (${sortingCameraInstallation.toString()})(scene.virtualCameraPose());
  })()`)
  const configured=await request('/api/vision/workspace/config',{input:'virtual',enabled:true,detection_enabled:true,mapping:false,height_mm:24,min_area:100,roi:[0,0,1,1],colors,virtual:config})
  const startup=report.sortingVisionStartup={expected_session:configured.virtual_session,transient_states:[]}
  let observed
  await until(async()=>{
    const value=await request('/api/vision/workspace/state',null);lastSource=value
    if(diagnostic)await evaluate(`window.__sortingDiagnostic.states.push(${JSON.stringify({at:Date.now(),error:value.error,session:value.virtual_session,result:value.result?{seq:value.result.frame_seq,key:value.result.source_key,captured_at:value.result.captured_at,objects:value.result.objects}:null})})`)
    // Switching to the renderer is asynchronous. The explicit no-frame waiting
    // state may precede its first frame; it is never accepted as a detection.
    if(value.virtual_session!==configured.virtual_session || value.config?.input!=='virtual' || !value.config?.enabled || !value.config?.detection_enabled)throw Error('测试相机配置或会话已变化')
    // A discarded old/cold-start frame is not a successful inference. Only
    // these explicit empty-result startup states may wait within the fixed
    // first-frame deadline; session changes and all model/decode errors fail.
    const startingWithoutFrame=!value.result && ['等待已开启的彩色相机或导入图片','仿真相机视角或帧已失效'].includes(value.error)
    if(startingWithoutFrame && startup.transient_states.length<20)startup.transient_states.push({at:Date.now(),error:value.error})
    if(value.error && !startingWithoutFrame)throw Error('实际虚拟相机识别失败：'+value.error)
    const result=value.result
    if(!value.error && value.stale===false && result?.source==='virtual' && result.image?.base64 && result.objects?.some(object=>object.status==='observed' && object.shape===expectedShape)){observed=value;return true}
    return false
  },'实际Three帧经HSV识别圆形',20)
  const size=observed.result.image
  const points=await evaluate(`(${sortingCalibrationPoints.toString()})(window.__elfinScene.virtualCameraPose(),${size.width},${size.height},${config.fov})`)
  const calibrated=await request('/api/vision/workspace/calibrate',{points,plane_z:4,max_error_mm:2})
  assert.equal(calibrated.calibration.check_errors_mm.length,2)
  assert.ok(calibrated.calibration.check_errors_mm.every(error=>error<=2))
  let ready
  await until(async()=>{
    const value=await request('/api/vision/workspace/state',null),result=value.result
    if(value.error)throw Error(value.error)
    if(value.stale===false && result?.calibrated && result.source==='virtual' && result.objects.some(object=>object.status==='observed' && object.shape===expectedShape && object.mapped?.coordinate_space==='base_xy_planar_estimate')){ready=value;return true}
    return false
  },'当前相机会话的独立标定与新鲜形状观测',15)
  report.sortingVision={source:'actual-three-render',frame:ready.result.frame_seq,captured_at:ready.result.captured_at,
    shape:ready.result.objects.map(object=>({id:object.id,color:object.color,shape:object.shape,mapped:object.mapped})),
    calibration:calibrated.calibration,virtual:config,readObjectPositionsForDetection:false}
  const evidenceFile='sorting-hsv-evidence.'+(ready.result.image.mime==='image/png'?'png':'jpg')
  report.sortingVision.image=evidenceFile
  fs.writeFileSync(path.join(directory,evidenceFile),Buffer.from(ready.result.image.base64,'base64'))
  return {async assertFresh(){
    return waitForFreshVision(()=>request('/api/vision/workspace/state',null),ready)
  },async stop(){
    let busy=0
    await until(async()=>{
      try{await request('/api/vision/workspace/config',{...previous,enabled:false,detection_enabled:false,mapping:false});return true}
      catch(error){if(error.message!=='视觉配置任务繁忙')throw error;busy++;return false}
    },'等待当前有界帧提交退出并关闭检测',5)
    const stopped=await request('/api/vision/workspace/state',null)
    assert.equal(stopped.config.enabled,false);assert.equal(stopped.config.detection_enabled,false)
    report.sortingVisionStop={disabledConfirmed:true,rejectedBusyAttempts:busy}
  }}
  }catch(error){if(lastSource?.result){report.sortingVisionLast={error:lastSource.error,stale:lastSource.stale,session:lastSource.virtual_session,result:{...lastSource.result,image:{...lastSource.result.image,base64:undefined}}};if(lastSource.result.image?.base64)fs.writeFileSync(path.join(directory,'sorting-last-inference.jpg'),Buffer.from(lastSource.result.image.base64,'base64'))}try{await request('/api/vision/workspace/config',{...previous,enabled:false,detection_enabled:false,mapping:false})}catch(cleanup){error.message+='；关闭测试视觉失败：'+cleanup.message}throw error}
  finally{if(diagnostic){report.sortingVisionDiagnostic=await evaluate(`(()=>{const value=window.__sortingDiagnostic;window.fetch=window.__sortingOriginalFetch;delete window.__sortingOriginalFetch;delete window.__sortingDiagnostic;return value})()`);if(report.sortingVisionDiagnostic.lastUpload){fs.writeFileSync(path.join(directory,'sorting-last-upload.jpg'),Buffer.from(report.sortingVisionDiagnostic.lastUpload,'base64'));delete report.sortingVisionDiagnostic.lastUpload}}}
}
module.exports={prepareSortingVision,sortingCameraInstallation,sortingCalibrationPoints,waitForFreshVision}
