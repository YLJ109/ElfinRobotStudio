import { handPipeline } from './handPipeline.js'
import { createBrowserCameraPreview } from './browserCameraPreview.ts'
import { revokeHandFollowInput, getHandFollowDetectionDemand } from './handFollowInput.js'
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { visionWorkspace, visionCall, applyVisionSnapshot, visionSnapshotEpoch, localVirtualPreview, virtualCaptureKey } from './visionWorkspace.js'

export const BROWSER_CAMERA='browser:default'
export const VIRTUAL_CAMERA='virtual:end-effector'
const controllers=new WeakMap()

// 正确：来源切换只关闭旧采集；选择真实设备不会擅自开启硬件。
export function createCameraSource(w,{state=visionWorkspace,request=visionCall,storage=globalThis.localStorage,pipeline=handPipeline,preview=null}={}){
  const busy=ref(false),message=ref(''),remembered=ref(storage?.getItem('huayan.camera.selected') || (state.config?.input==='virtual'?VIRTUAL_CAMERA:BROWSER_CAMERA))
  const browserPreview=preview || createBrowserCameraPreview({revoke:()=>revokeHandFollowInput()})
  const realMode=computed(()=>(w.snap?.mode || w.mode)==='real')
  const virtual=computed(()=>!['requesting','streaming'].includes(browserPreview.state.status) && !realMode.value && state.config?.input==='virtual')
  const devices=computed(()=>w.cameras?.devices || [])
  const visibleDevices=computed(()=>{
    const seen=new Set()
    return devices.value.filter(d=>{
      if(d.alias_of)return false
      const identity=d.physical_id || d.id
      if(seen.has(identity))return false
      seen.add(identity);return true
    })
  })
  const selected=computed(()=>{
    if(virtual.value)return VIRTUAL_CAMERA
    const id=w.cameras?.active?.id || remembered.value
    const device=devices.value.find(d=>d.id===id)
    const canonical=device?.alias_of || (device?.physical_id && visibleDevices.value.find(d=>d.physical_id===device.physical_id)?.id) || id
    return visibleDevices.value.some(d=>d.id===canonical)?canonical:visibleDevices.value.length===1?visibleDevices.value[0].id:''
  })
  const browser=computed(()=>selected.value===BROWSER_CAMERA)
  const running=computed(()=>browser.value?['requesting','streaming'].includes(browserPreview.state.status):virtual.value?!!state.config?.enabled:!!w.cameras?.active)
  const handDetection=computed(()=>pipeline.state.enabled && pipeline.state.source==='local' && pipeline.state.session===w.cameras?.active?.session)
  const detectionError=computed(()=>pipeline.state.error||'')
  const detectorState=computed(()=>pipeline.state.detectorState||'idle')
  const detectorError=computed(()=>pipeline.state.detectorError||'')
  // 只在后端真的报告检测器生命周期时才等待就绪：冷启动约 10 秒，期间 UI
  // 必须显示"启动中"，失败则显示后端给出的具体原因，绝不假装已开启。
  // 'idle' 是检测器根本没跑起来，绝不能当成"已开启"直接放行。
  const waitForDetectorReady=async()=>{
    const phase=pipeline.state?.detectorState
    if(phase==='ready')return{ok:true}
    if(phase==='failed')return{ok:false,error:pipeline.state?.detectorError||pipeline.state?.error||'检测器启动失败'}
    // 旧控制器没有检测器生命周期字段，无从判断，保持原有"不阻塞"行为。
    if(typeof pipeline.waitForDetector!=='function')return{ok:true}
    return pipeline.waitForDetector(25000)
  }
  // 唯一的检测开启方，带自己的身份标记：只有它能关掉自己开的检测，画面
  // 组件（CameraFrame）用另一个标记，卸载时不会误关这里刚拉起的推理。
  const DETECTION_OWNER='camera-source'
  const setHandDetection=async on=>{
      if(on && handDetection.value)return true
      if(on && (browser.value || virtual.value || !w.cameras?.active?.session)){message.value='请先开启工作站彩色相机';return false}
      const result=await pipeline.configure(!!on,'local',w.cameras?.active?.session || '',DETECTION_OWNER)
      if(!result){message.value=detectionError.value||'手掌检测启动失败';return false}
      if(on){
        const ready=await waitForDetectorReady()
        if(!ready.ok){
          // 后端已经给出可读原因（如"手掌检测器启动失败：No module named
          // 'mediapipe'"），别再套一层前缀变成"启动失败：…启动失败：…"。
          const reason=String(ready.error||'未知原因')
          message.value=reason.includes('启动失败')?('相机已开启，但'+reason):('相机已开启，但手掌检测启动失败：'+reason)
          return false
        }
        // 真正跑通了才抹掉上一次"启动失败"的提示，别让过期错误一直挂在界面上。
        message.value=''
        // 等待期间检测可能被别的组件关掉（预览窗口卸载、页面隐藏、切源）：
        // 必须复核真实开关状态，绝不把"已被关掉"当成"开启成功"返回。
        if(pipeline.state && pipeline.state.enabled===false){
          message.value='手掌检测已被关闭，请重新开启'
          return false
        }
      }
      return true
  }
  const options=computed(()=>[...visibleDevices.value.map((d,i)=>({id:d.id,name:`${i+1} · ${d.name || '摄像头'}`})),...realMode.value?[]:[{id:VIRTUAL_CAMERA,name:'仿真摄像头'}]])
  async function config(patch){
    if(patch.input==='virtual' && realMode.value)throw new Error('真机模式不提供仿真相机，请选择真实摄像头')
    const preview=localVirtualPreview(state)
    preview.suspended=true;preview.frame=null
    try{
      const epoch=visionSnapshotEpoch(state),response=await request('config',patch)
      if(!applyVisionSnapshot(state,response,epoch))throw new Error('摄像头来源已被更新，请确认当前来源后重试')
    }finally{preview.suspended=false}
  }
  function checked(result){if(!result?.ok)throw new Error(result?.why || '摄像头操作失败');return result}
  async function closeSession(session){
    if(typeof session!=='string' || !session)throw new Error('摄像头会话未知，请刷新状态后重试')
    checked(await w.closeCamera(session))
  }
  async function run(action){
    if(busy.value)return false
    busy.value=true;message.value=''
    try{await action();return true}catch(error){message.value=error.message;return false}finally{busy.value=false}
  }
  let discoverySession=null
  const refresh=()=>run(async()=>{
    const response=await w.refreshCameras()
    if(response?.ok===false)throw new Error(response.why || '扫描失败')
    discoverySession=w.snap?.session_id || null
    message.value=devices.value.length?`已重新加载，发现 ${devices.value.length} 个摄像头`:
      realMode.value?'未发现摄像头，请检查USB连接、驱动及其他程序占用后重新加载':'未发现真实摄像头，可选择末端仿真相机'
  })
  // A visible source picker needs current metadata after a backend restart.
  // Enumeration does not open capture; only an explicit toggle does that.
  const ensureDevices=async()=>{
    const session=w.snap?.session_id
    if(!session || discoverySession===session)return true
    if(busy.value)return false
    discoverySession=session
    return run(async()=>{
      const response=await w.refreshCameras()
      if(response?.ok===false)throw new Error(response.why || '扫描失败')
      if(!visibleDevices.value.length)message.value=response?.error || '未发现真实摄像头，请检查连接后重新加载'
    })
  }
  // Only the explicit follow button hands capture to the workstation.
  // startDetection=false 只做"把相机准备好"，检测留给调用方在合适时机再开：
  // 回中心是一段真实运动，开检测会让后端撤销正在进行的启动动作，两者必须
  // 错开。相机本身（打开设备、切视觉输入）与运动互不相干，可以并行。
  const prepareHandFollow=({cancelled=()=>false,startDetection=true}={})=>run(async()=>{
    let openedSession=null, detectionStarted=false
    const requireCurrent=()=>{if(cancelled())throw Error('已取消准备')}
    try{
      requireCurrent()
      if(!w.cameras?.active?.session){
        const label=browserPreview.state.stream?.getVideoTracks?.()[0]?.label?.trim().toLowerCase()
        // The visible picker has already enumerated devices. Reusing that list
        // avoids another blocking driver scan on every follow click.
        let known=visibleDevices.value
        if(!known.some(d=>(d.streams || ['color']).includes('color'))){
          const scanned=await w.refreshCameras()
          requireCurrent()
          if(scanned?.ok===false)throw Error(scanned.why || '摄像头扫描失败')
          if(!Array.isArray(scanned?.devices))throw Error('摄像头扫描结果无效，请重试')
          known=scanned.devices
        }
        const available=known.filter(d=>(d.streams || ['color']).includes('color')&&!d.alias_of)
        const matches=label?available.filter(d=>d.name?.trim().toLowerCase()===label):[]
        const device=available.find(d=>d.id===selected.value)|| (matches.length===1?matches[0]:available[0])
        if(!device)throw Error('未找到工作站彩色相机，请检查设备')
        browserPreview.stop('已切换工作站检测')
        revokeHandFollowInput()
        await config({input:'camera',enabled:false})
        requireCurrent()
        remembered.value=device.id;storage?.setItem('huayan.camera.selected',device.id)
        const opened=checked(await w.openCamera(device.id,['color'],30))
        openedSession=opened.active?.session
        if(!openedSession)throw Error('工作站相机会话未知，请重新开启')
      }else{
        // Reuse the one capture even if a stale source picker still says virtual.
        browserPreview.stop('已切换工作站检测')
      }
      requireCurrent()
      const active=w.cameras?.active
      if(!active?.session || (openedSession && active.session!==openedSession))throw Error('摄像头会话已变化，请重试')
      if(active.requested_streams && !active.requested_streams.includes('color'))throw Error('请选择工作站彩色画面')
      // 相机已经按彩色输入配好时不再重复提交：这次请求不改变任何状态，
      // 只是让"点开始跟随"多等一个来回。
      if(state.config?.input!=='camera' || state.config?.enabled!==true)await config({input:'camera',enabled:true})
      requireCurrent()
      if(w.cameras?.active?.session!==active.session)throw Error('摄像头会话已变化，请重试')
      if(startDetection && !handDetection.value){
        if(!await setHandDetection(true))throw Error(message.value || '手掌检测启动失败')
        detectionStarted=true
      }
      requireCurrent()
    }catch(error){
      // Roll back only resources acquired here; another window's session survives.
      if(openedSession && w.cameras?.active?.session===openedSession){
        if(detectionStarted && handDetection.value)await setHandDetection(false)
        await closeSession(openedSession)
      }
      throw error
    }
  })
  const select=id=>run(async()=>{
    if(id===VIRTUAL_CAMERA && realMode.value)throw new Error('真机模式不提供仿真相机，请选择真实摄像头')
    if(id!==VIRTUAL_CAMERA && id!==BROWSER_CAMERA && !devices.value.some(d=>d.id===id))throw new Error('设备不在当前列表，请刷新设备')
    if(id===selected.value && state.config?.input!=='image')return
    if(handDetection.value && !await setHandDetection(false))throw Error(message.value)
    browserPreview.stop('相机来源已变化，旧输入已撤销')
    revokeHandFollowInput()
    // 先确认旧来源已停，再启用新来源。失败时保留原选择，禁止双路采集。
    if(w.cameras?.active)await closeSession(w.cameras.active.session)
    if(id===VIRTUAL_CAMERA){await config({input:'virtual',enabled:true});remembered.value=id}
    else{
      await config({input:'camera',enabled:false})
      remembered.value=id
      storage?.setItem('huayan.camera.selected',id)
    }
    storage?.setItem('huayan.camera.source',id)
  })
  const toggle=()=>{
    if(browser.value && browserPreview.state.status==='requesting'){browserPreview.stop('已取消开启');return Promise.resolve(true)}
    return run(async()=>{
    if(browser.value){if(running.value)browserPreview.stop();else if(!await browserPreview.start() && browserPreview.state.status!=='idle')throw Error(browserPreview.state.error);return}
    if(virtual.value){
      if(w.cameras?.active)await closeSession(w.cameras.active.session)
      await config({input:'virtual',enabled:!state.config?.enabled});return
    }
    if(w.cameras?.active){if(handDetection.value && !await setHandDetection(false))throw Error(message.value);await closeSession(w.cameras.active.session);return}
    const device=devices.value.find(d=>d.id===selected.value)
    if(!device)throw new Error('请刷新并选择摄像头')
    await config({input:'camera',enabled:false})
    const streams=device.streams || ['color'],wanted=(storage?.getItem('huayan.camera.request-stream') || 'color').split('+')
    const valid=wanted.length<=2 && wanted.every(s=>['color','depth'].includes(s) && streams.includes(s))
    const fps=Number(storage?.getItem('huayan.camera.request-fps'))
    const opened=checked(await w.openCamera(device.id,valid?wanted:[streams.includes('color')?'color':streams[0]],Number.isInteger(fps)&&fps>=1&&fps<=60?fps:60))
    const openedSession=opened.active?.session
    try{await config({input:'camera',enabled:true})}catch(error){
      // 正确：只回滚本次开启的会话，绝不关闭另一个窗口后来开启的新会话。
      const session=openedSession
      if(typeof session==='string' && session){
        try{await closeSession(session)}catch(closeError){throw new Error(error.message+'；回滚未关闭会话：'+closeError.message)}
      }
      throw error
    }
    storage?.setItem('huayan.camera.selected',device.id)
    // 彩色画面开启后只在"跟随确实需要检测"时自动起推理。单纯看画面不再
    // 常驻 30Hz 手掌推理——那会把世界循环和预检一起拖慢。
    const colorWanted=valid?wanted.includes('color'):streams.includes('color')
    if(colorWanted && getHandFollowDetectionDemand() && !await setHandDetection(true) && !handDetection.value)
      throw Error('相机已开启，但手掌检测启动失败：'+(detectionError.value || message.value || '未知原因'))
  })
  }
  function syncPreference(event){if(event.key==='huayan.camera.selected')remembered.value=event.newValue || ''}
  return {busy,message,realMode,virtual,browser,browserPreview,handDetection,setHandDetection,prepareHandFollow,detectionError,detectorState,detectorError,devices,selected,running,options,refresh,ensureDevices,select,toggle,syncPreference}
}

export function useCameraSource(w){
  if(!controllers.has(w))controllers.set(w,createCameraSource(w))
  const controller=controllers.get(w)
  const listener=event=>controller.syncPreference(event)
  // This shared controller is inert on mount; the visible picker requests
  // device metadata. Capture still requires an explicit user action.
  let releasePreview=null
  onMounted(()=>{window.addEventListener('storage',listener);releasePreview=controller.browserPreview.lifecycle()})
  onBeforeUnmount(()=>{window.removeEventListener('storage',listener);releasePreview?.()})
  return controller
}

// 知识点：旧来源、过期帧和关闭后的残帧不能冒充当前实时仿真画面。
export function virtualPreview(state){
  const preview=localVirtualPreview(state),frame=preview.frame
  return state.config?.input==='virtual' && state.config.enabled && preview.visible && !preview.suspended &&
    frame?.canvas && frame.session===state.virtual_session && frame.key===virtualCaptureKey(state)?frame:null
}
