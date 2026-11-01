import { reactive, shallowReactive, watch } from 'vue'
export const COLOR_NAMES={red:'红色',yellow:'黄色',green:'绿色',cyan:'青色',blue:'蓝色',purple:'紫色',white:'白色',black:'黑色'}
export const SHAPE_NAMES={circle:'圆形',rectangle:'矩形',triangle:'三角形',irregular:'不规则'}
export const visionWorkspace=reactive({instance_id:null,revision:-1,config:null,calibration:null,result:null,error:'',stale:false,connected:false})
const snapshotGates=new WeakMap()
function snapshotGate(state){
  if(!snapshotGates.has(state))snapshotGates.set(state,{epoch:0,retired:new Set()})
  return snapshotGates.get(state)
}
export function visionSnapshotEpoch(state){return snapshotGate(state).epoch}
// 正确：同一进程只向前应用；重启允许低版本，但迟到的旧进程不能再次接管。
export function applyVisionSnapshot(state,value,epoch=visionSnapshotEpoch(state)){
  if(!value || !Number.isSafeInteger(value.revision) || value.revision<0)return false
  const gate=snapshotGate(state),current=state.instance_id || null,incoming=value.instance_id || null
  if(current!==incoming){
    if(!incoming || gate.retired.has(incoming) || epoch!==gate.epoch)return false
    if(current){gate.retired.add(current);if(gate.retired.size>64)gate.retired.delete(gate.retired.values().next().value)}
    gate.epoch++
  }else if(value.revision<(state.revision ?? -1))return false
  Object.assign(state,value)
  return true
}
export async function visionCall(path,payload,signal) {
  if(!['state','config','image','calibrate','virtual'].includes(path))throw new Error('视觉接口无效')
  const response=await fetch('/api/vision/workspace/'+path,{method:payload===undefined?'GET':'POST',
    cache:'no-store',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000),...(payload===undefined?{}:{headers:{'content-type':'application/json'},body:JSON.stringify(payload)})})
  const value=await response.json()
  if(!response.ok || !value.ok){const error=new Error(value.why || '视觉请求未完成');error.status=response.status;error.why=value.why;throw error}
  return value
}
export function createVisionPolling({state=visionWorkspace,request=visionCall,apply=()=>{},visible=()=>!document.hidden,
  schedule=setTimeout,cancel=clearTimeout}={}) {
  let disposed=false,timer=null,controller=null,published=null
  async function tick(){
    if(disposed)return
    try{
      controller=new AbortController()
      const epoch=visionSnapshotEpoch(state)
      const value=await request('state',undefined,controller.signal)
      if(disposed)return
      const recovering=!state.connected
      // 正确：配置响应可先更新共享状态，但不能因此漏掉三维层的首次通知。
      const changed=recovering || !published || value.instance_id!==published.instance_id || value.revision!==published.revision || value.stale!==published.stale
      if(applyVisionSnapshot(state,value,epoch)){
        state.connected=true
        if(changed){apply(value);published={instance_id:value.instance_id,revision:value.revision,stale:value.stale}}
      }
    }catch(error){if(!disposed){state.connected=false;state.error='视觉连接中断：'+error.message;state.result=null;apply(null)}}
    finally{controller=null;if(!disposed)timer=schedule(tick,visible()?200:1000)}
  }
  void tick()
  return ()=>{disposed=true;cancel(timer);controller?.abort();state.connected=false;apply(null)}
}
export function mappedVisionObjects(state) {
  if(!state?.config?.mapping || !state.config.enabled || state.stale || !state.result?.calibrated)return []
  return state.result.objects.filter(object=>object.mapped && ['observed','holding'].includes(object.status)).slice(0,50)
}

// Local presentation is deliberately outside backend snapshots/results. A slow
// recognizer cannot replace the preview or impersonate a new measurement.
const virtualPreviews=new WeakMap()
export const VIRTUAL_PREVIEW_MAX_AGE_MS=750
export function localVirtualPreview(state=visionWorkspace){
  if(!virtualPreviews.has(state))virtualPreviews.set(state,shallowReactive({visible:false,suspended:false,frame:null,
    completed:0,uploaded:0,uploadSkipped:0,capture_ms:null,upload_ms:null}))
  return virtualPreviews.get(state)
}
export function virtualCaptureKey(state){
  const config=state.config,v=config?.virtual
  return JSON.stringify([state.instance_id,state.virtual_session,config?.input,!!config?.enabled,
    v?.offset_mm,v?.direction,v?.fov,v?.width,v?.fps])
}
export function virtualRecognitionFps(config){
  const fps=config?.virtual?.fps
  return Number.isFinite(fps) && fps>=1 && fps<=30?fps:30
}
export function createVirtualCapture({state=visionWorkspace,getScene,allowed,request=visionCall,
  schedule=setTimeout,cancel=clearTimeout,now=()=>performance.now(),doc=globalThis.document,
  visible=()=>localVirtualPreview(state).visible,previewFps=30,
  scheduleExpiry=setTimeout,cancelExpiry=clearTimeout}={}){
  const preview=localVirtualPreview(state)
  const targetFps=Number.isFinite(previewFps) && previewFps>=1 && previewFps<=30?previewFps:30
  let disposed=false,timer=null,controller=null,capturing=false,uploading=false,epoch=0,sequence=0,nextUploadAt=0
  let configuredScene=null,configuredKey=null,configuredActive=null
  let expiryTimer=null
  const enabled=()=>!disposed && !doc?.hidden && visible() && !preview.suspended && allowed() &&
    state.config?.enabled && state.config.input==='virtual' && !!state.virtual_session && !!getScene()
  function clearPreview(){cancelExpiry(expiryTimer);expiryTimer=null;preview.frame=null}
  function invalidate(){
    epoch++;nextUploadAt=0;clearPreview();controller?.abort()
    configuredScene=null;configuredKey=null;configuredActive=null
    getScene()?.configureVirtualCamera({enabled:false})
  }
  function wake(){
    invalidate();cancel(timer);timer=null
    if(!capturing && !disposed)void tick()
  }
  // Compare semantic capture identity, not the polling revision/result. In
  // particular, a 200ms recognition readback must not reset a 30fps preview.
  const unwatch=watch([()=>virtualCaptureKey(state),allowed,visible,()=>preview.suspended],wake,{flush:'sync'})
  const stopDetectionWatch=watch(()=>state.config?.detection_enabled,on=>{if(!on){controller?.abort();nextUploadAt=0}},{flush:'sync'})
  doc?.addEventListener?.('visibilitychange',wake)
  async function upload(frame,token,key,scene){
    if(!state.config?.detection_enabled)return
    if(uploading || now()+.001<nextUploadAt){preview.uploadSkipped++;return}
    uploading=true;const abort=new AbortController();controller=abort;const started=now(),generation=state.config_generation,config=JSON.stringify(state.config)
    nextUploadAt=started+1000/virtualRecognitionFps(state.config)
    const valid=()=>token===epoch && key===virtualCaptureKey(state) && generation===state.config_generation && config===JSON.stringify(state.config) && scene===getScene() && enabled() && state.config?.detection_enabled && !abort.signal.aborted
    try{
      const encoded=frame.encode?await frame.encode():frame
      if(!valid() || !encoded?.image_base64)return
      await request('virtual',{session:state.virtual_session,image_base64:encoded.image_base64,
        view_key:encoded.view_key,captured_at:encoded.captured_at},abort.signal)
      if(valid()){preview.uploaded++;preview.upload_ms=now()-started}
      // Only the existing state polling owns recognized objects/results.
    }catch(error){if(valid()){if(error.status===429 && error.why==='视觉配置任务繁忙')preview.uploadSkipped++;else state.error='仿真相机识别上传：'+error.message}}
    finally{if(controller===abort)controller=null;uploading=false}
  }
  async function tick(){
    if(disposed || capturing)return
    capturing=true;const started=now(),token=epoch,key=virtualCaptureKey(state)
    try{
      const scene=getScene(),active=enabled()
      if(scene && (scene!==configuredScene || key!==configuredKey || !!active!==configuredActive)){
        scene.configureVirtualCamera({...state.config?.virtual,enabled:!!active})
        configuredScene=scene;configuredKey=key;configuredActive=!!active
      }
      if(!active){clearPreview();return}
      const frame=await scene.captureVirtualFrame({encode:false})
      if(token!==epoch || key!==virtualCaptureKey(state) || scene!==getScene() || !enabled())return
      if(!frame?.canvas || now()-started>=VIRTUAL_PREVIEW_MAX_AGE_MS){clearPreview();return}
      // Publication counts a completed local readback/copy, never a requested
      // timer tick, uploaded response or a reused recognized frame.
      clearPreview()
      const published={...frame,session:state.virtual_session,key,sequence:++sequence}
      preview.frame=published
      // Independent of the capture loop: a hung GPU readback cannot keep the
      // previous bitmap LIVE. Clearing this reactive field invalidates the UI
      // computed value without releasing the in-flight readback or its buffers.
      expiryTimer=scheduleExpiry(()=>{
        if(!disposed && token===epoch && preview.frame===published){expiryTimer=null;preview.frame=null}
      },Math.max(0,VIRTUAL_PREVIEW_MAX_AGE_MS-(now()-started)))
      preview.completed++;preview.capture_ms=now()-started
      void upload(frame,token,key,scene)
    }catch(error){if(token===epoch && enabled()){clearPreview();state.error='仿真相机：'+error.message}}
    finally{
      capturing=false
      if(!disposed){
        const delay=enabled()?Math.max(0,1000/targetFps-(now()-started)):300
        timer=schedule(tick,delay)
      }
    }
  }
  void tick()
  return ()=>{
    if(disposed)return
    disposed=true;unwatch();stopDetectionWatch();doc?.removeEventListener?.('visibilitychange',wake)
    cancel(timer);invalidate();preview.visible=false
  }
}

// 分拣与有趣任务都要"看得见"，但开相机不该由各功能页各自实现一遍：那会绕开
// 这里统一的实例/版本闸门，变成第二路采集。统一收口为一个幂等入口：已经开着
// 就不动，仿真模式才切到末端仿真相机，正在用工作站真实相机时不抢。
let ensuringVirtual=null
export async function ensureVirtualVision(state=visionWorkspace,request=visionCall){
  const config=state.config
  if(config?.enabled && (config?.input==='virtual' || config?.input==='camera'))return true
  if(ensuringVirtual)return ensuringVirtual
  const epoch=visionSnapshotEpoch(state)
  ensuringVirtual=request('config',{input:'virtual',enabled:true})
    .then(value=>{applyVisionSnapshot(state,value,epoch);return true})
    .catch(()=>false)
    .finally(()=>{ensuringVirtual=null})
  return ensuringVirtual
}
