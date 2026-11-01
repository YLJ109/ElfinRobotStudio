const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyCameraPermission({window,directory,report}) {
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  assert.ok(process.argv.includes('--use-fake-device-for-media-stream'),'相机验收必须使用Chromium安全测试来源')
  assert.ok(!process.argv.includes('--use-fake-ui-for-media-stream'),'必须实际经过应用权限处理器')
  const result=report.cameraPermission={completed:false,source:'chromium-fake-device',physicalCameraUsed:false}
  const evaluate=s=>window.webContents.executeJavaScript(s)
  async function until(check,label){const end=Date.now()+20000;while(Date.now()<end){if(await check())return;await new Promise(r=>setTimeout(r,50))}throw Error(label)}
  try {
    await evaluate(`(()=>{window.__cameraTestOriginalFetch=window.fetch;window.__cameraTestUploads=[];window.fetch=(...args)=>{if(String(args[1]?.method||'GET').toUpperCase()==='POST'&&/vision.*(?:frame|virtual|offer)|remote.*offer/.test(String(args[0])))window.__cameraTestUploads.push(String(args[0]));return window.__cameraTestOriginalFetch(...args)}})()`)
    await evaluate(`(()=>{const button=document.querySelector('.camera-trigger');if(button)button.click()})()`)
    await until(()=>evaluate(`!!document.querySelector('#preview-camera-source')`),'相机面板未显示')
    await evaluate(`(()=>{const s=document.querySelector('#preview-camera-source');s.value='browser:default';s.dispatchEvent(new Event('change',{bubbles:true}))})()`)
    await until(()=>evaluate(`document.querySelector('#camera-panel .capture-button')?.textContent.trim()==='开启'`),'相机未处于关闭状态')
    await evaluate(`document.querySelector('#camera-panel .capture-button').click()`)
    await until(()=>evaluate(`(()=>{const v=document.querySelector('#camera-panel video');return v&&v.videoWidth>0&&v.videoHeight>0&&v.readyState>=2&&!v.paused&&v.currentTime>.2&&document.querySelector('#camera-panel .tag')?.textContent.trim()==='LIVE'})()`),'权限处理后未获得持续视频帧')
    result.preview=await evaluate(`(()=>{const v=document.querySelector('#camera-panel video');window.__cameraTestTracks=v.srcObject.getTracks();return {width:v.videoWidth,height:v.videoHeight,tracks:window.__cameraTestTracks.map(t=>({kind:t.kind,state:t.readyState}))}})()`)
    assert.ok(result.preview.tracks.every(t=>t.kind==='video'&&t.state==='live'))
    const rendered=await evaluate('window.__elfinScene.dprInfo().submittedFrames')
    await until(()=>evaluate(`window.__elfinScene.dprInfo().submittedFrames>=${rendered+2}`),'预览帧尚未提交')
    fs.writeFileSync(path.join(directory,'camera-permission-preview.png'),(await window.webContents.capturePage()).toPNG())
    await evaluate(`document.querySelector('#camera-panel .capture-button').click()`)
    await until(()=>evaluate(`window.__cameraTestTracks.every(t=>t.readyState==='ended')`),'关闭后视频轨道未释放')
    result.tracksReleased=true
    result.uploads=await evaluate(`window.__cameraTestUploads.slice()`);assert.deepEqual(result.uploads,[])
    const backend=await evaluate(`fetch('/api/vision/workspace/state').then(r=>r.json())`)
    assert.equal(backend.config.detection_enabled,false)
    result.backendDetectionStarted=false;result.completed=true
  } finally {
    await evaluate(`(()=>{for(const t of window.__cameraTestTracks||[])t.stop();if(window.__cameraTestOriginalFetch)window.fetch=window.__cameraTestOriginalFetch;delete window.__cameraTestOriginalFetch;delete window.__cameraTestTracks;delete window.__cameraTestUploads})()`)
  }
}
module.exports={verifyCameraPermission}
