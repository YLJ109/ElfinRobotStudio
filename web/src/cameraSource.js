import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { visionWorkspace, visionCall, applyVisionSnapshot, visionSnapshotEpoch, localVirtualPreview, virtualCaptureKey } from './visionWorkspace.js'
import { headCameraSettings } from './virtualCameraMount.js'

export const VIRTUAL_CAMERA='virtual:end-effector'
const controllers=new WeakMap()

// 正确：来源切换只关闭旧采集；选择真实设备不会擅自开启硬件。
export function createCameraSource(w,{state=visionWorkspace,request=visionCall,storage=globalThis.localStorage}={}){
  const busy=ref(false),message=ref(''),remembered=ref(storage?.getItem('huayan.camera.selected') || '')
  const realMode=computed(()=>(w.snap?.mode || w.mode)==='real')
  const virtual=computed(()=>!realMode.value && state.config?.input==='virtual')
  const devices=computed(()=>w.cameras?.devices || [])
  const selected=computed(()=>virtual.value?VIRTUAL_CAMERA:(w.cameras?.active?.id || (devices.value.some(d=>d.id===remembered.value)?remembered.value:'')))
  const running=computed(()=>virtual.value?!!state.config?.enabled:!!w.cameras?.active)
  const options=computed(()=>[...devices.value.map(d=>({id:d.id,name:d.name || d.id})),...realMode.value?[]:[{id:VIRTUAL_CAMERA,name:'末端仿真相机 · 仿真'}]])
  const discoveredSessions=new Set()
  async function config(patch){
    if(patch.input==='virtual' && realMode.value)throw new Error('真机模式不提供仿真相机，请选择真实摄像头')
    const installation=headCameraSettings(state.config?.virtual)
    if(patch.input==='virtual' && installation!==state.config?.virtual)patch={...patch,virtual:installation}
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
  const refresh=()=>run(async()=>{
    const response=await w.refreshCameras()
    if(response?.ok===false)throw new Error(response.why || '扫描失败')
    message.value=devices.value.length?`已重新加载，发现 ${devices.value.length} 个摄像头`:
      realMode.value?'未发现摄像头，请检查USB连接、驱动及其他程序占用后重新加载':'未发现真实摄像头，可选择末端仿真相机'
  })
  async function ensureDiscovery(session){
    // Shared by every camera panel: one discovery per backend boot, not one
    // per component or state poll. Never open/select/close a capture here.
    if(typeof session!=='string' || !session || busy.value || discoveredSessions.has(session))return false
    discoveredSessions.add(session)
    if(discoveredSessions.size>16)discoveredSessions.delete(discoveredSessions.values().next().value)
    return refresh()
  }
  const select=id=>run(async()=>{
    if(id===VIRTUAL_CAMERA && realMode.value)throw new Error('真机模式不提供仿真相机，请选择真实摄像头')
    if(id!==VIRTUAL_CAMERA && !devices.value.some(d=>d.id===id))throw new Error('设备不在当前列表，请刷新设备')
    if(id===selected.value && state.config?.input!=='image')return
    // 先确认旧来源已停，再启用新来源。失败时保留原选择，禁止双路采集。
    if(w.cameras?.active)await closeSession(w.cameras.active.session)
    if(id===VIRTUAL_CAMERA)await config({input:'virtual',enabled:true})
    else{
      await config({input:'camera',enabled:false})
      remembered.value=id
      storage?.setItem('huayan.camera.selected',id)
    }
    storage?.setItem('huayan.camera.source',id)
  })
  const toggle=()=>run(async()=>{
    if(virtual.value){
      if(w.cameras?.active)await closeSession(w.cameras.active.session)
      await config({input:'virtual',enabled:!state.config?.enabled});return
    }
    if(w.cameras?.active){await closeSession(w.cameras.active.session);return}
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
  })
  function syncPreference(event){if(event.key==='huayan.camera.selected')remembered.value=event.newValue || ''}
  return {busy,message,realMode,virtual,devices,selected,running,options,refresh,ensureDiscovery,select,toggle,syncPreference}
}

export function useCameraSource(w){
  if(!controllers.has(w))controllers.set(w,createCameraSource(w))
  const controller=controllers.get(w)
  const listener=event=>controller.syncPreference(event)
  let stopDiscovery=null
  onMounted(()=>{
    window.addEventListener('storage',listener)
    stopDiscovery=watch(()=>[w.connected,w.snap?.session_id,controller.busy.value],()=>{
      if(w.connected && w.snap?.session_id)void controller.ensureDiscovery(w.snap.session_id)
    },{immediate:true})
  })
  onBeforeUnmount(()=>{stopDiscovery?.();window.removeEventListener('storage',listener)})
  return controller
}

// 知识点：旧来源、过期帧和关闭后的残帧不能冒充当前实时仿真画面。
export function virtualPreview(state){
  const preview=localVirtualPreview(state),frame=preview.frame
  return state.config?.input==='virtual' && state.config.enabled && preview.visible && !preview.suspended &&
    frame?.canvas && frame.session===state.virtual_session && frame.key===virtualCaptureKey(state)?frame:null
}
