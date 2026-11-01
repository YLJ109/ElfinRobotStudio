import { shallowReactive, markRaw } from 'vue'

type PreviewStatus = 'idle' | 'requesting' | 'streaming' | 'error'
export function cameraPermissionMessage(error: unknown): string {
  const e = error as { name?: string; message?: string }
  const messages: Record<string,string> = {
    NotAllowedError:typeof window!=='undefined' && 'elfinDesktop' in window ? '相机访问被拒绝，请在系统相机权限中允许此应用后重试' : '相机权限被拒绝，请在浏览器中允许后重试',
    NotFoundError:'未找到相机，请检查连接', NotReadableError:'相机无法读取，可能被其他程序占用',
    OverconstrainedError:'相机不支持当前选择，请重新选择设备', SecurityError:'相机需要 HTTPS 或本机安全地址',
    AbortError:'相机开启已取消', TimeoutError:'相机授权等待超时，请确认权限后重试',
  }
  return messages[e?.name || ''] || e?.message || '相机开启失败'
}
export function createBrowserCameraPreview({
  mediaDevices = globalThis.navigator?.mediaDevices,
  secure = () => globalThis.isSecureContext !== false,
  now = () => performance.now(),
  schedule = setTimeout, cancel = clearTimeout,
  timeoutMs = 15000, revoke = (_reason: string) => {},
} = {}) {
  const state = shallowReactive({ status: 'idle' as PreviewStatus, stream: null as MediaStream|null,
    session:'', error:'', deviceId:'', startedAt:0, frameReady:false, frameAt:0, frameSeq:0 })
  let pendingCancel:(()=>void)|null=null
  let generation=0, deadline:ReturnType<typeof setTimeout>|null=null
  let frameDeadline:ReturnType<typeof setTimeout>|null=null, viewers=0
  let trackListeners:Array<[MediaStreamTrack,()=>void]>=[]
  const stopTracks = (stream: MediaStream|null) => { for(const track of stream?.getTracks?.() || [])track.stop() }
  function stop(reason='预览已关闭') {
    generation++;pendingCancel?.();pendingCancel=null
    if(deadline!==null)cancel(deadline)
    deadline=null
    if(frameDeadline!==null)cancel(frameDeadline);frameDeadline=null
    state.frameReady=false;state.frameAt=0;state.frameSeq=0
    for(const [track,listener] of trackListeners)track.removeEventListener?.('ended',listener)
    trackListeners=[]
    const stream=state.stream;state.stream=null;state.session='';state.status='idle';state.startedAt=0
    stopTracks(stream);revoke(reason)
  }
  async function start(deviceId='') {
    if(state.status==='requesting')return false
    stop('相机来源已更新，请重新启动控制')
    const current=++generation
    state.error='';state.deviceId=deviceId
    if(!secure() || !mediaDevices?.getUserMedia) {
      state.status='error';state.error='相机需要 HTTPS 或本机安全地址，并允许浏览器访问相机';return false
    }
    state.status='requesting'
    try {
      const request=mediaDevices.getUserMedia({audio:false,video:{...(deviceId?{deviceId:{exact:deviceId}}:{facingMode:'environment'}),width:{ideal:1280},height:{ideal:720},frameRate:{ideal:30,max:30}}})
      // Permission prompts cannot be cancelled. Every retired late grant must
      // immediately stop its tracks, including a transport ignoring timeout.
      const guarded=request.then(stream=>{if(current!==generation){stopTracks(stream);return null}return stream})
      const timeout=new Promise<never>((_,reject)=>{
        deadline=schedule(()=>{if(current===generation){generation++;reject(Object.assign(Error('相机授权等待超时'),{name:'TimeoutError'}))}},timeoutMs)
      })
      let cancelRequest:()=>void
      const cancelled=new Promise<null>(resolve=>{cancelRequest=()=>resolve(null);pendingCancel=cancelRequest})
      const stream=await Promise.race([guarded,timeout,cancelled])
      if(pendingCancel===cancelRequest!)pendingCancel=null
      if(current!==generation || !stream)return false
      if(deadline!==null)cancel(deadline);deadline=null
      const tracks=stream.getVideoTracks()
      if(!tracks.length){stopTracks(stream);throw Error('相机没有提供视频轨道')}
      state.stream=markRaw(stream);state.status='streaming';state.startedAt=now()
      state.session=globalThis.crypto?.randomUUID?.() || `browser-${Date.now()}-${current}`
      const ended=()=>{stop('相机已断开，旧控制输入已撤销');state.status='error';state.error='相机已断开，请检查设备后重新开启'}
      trackListeners=tracks.map(track=>{track.addEventListener?.('ended',ended);return [track,ended]})
      return true
    } catch(error) {
      if(current===generation || (state.status==='requesting' && current+1===generation)) {
        if(deadline!==null)cancel(deadline);deadline=null
        state.status='error';state.error=cameraPermissionMessage(error)
        revoke('相机不可用，旧控制输入已撤销')
      }
      return false
    }
  }
  function presentFrame() {
    if(state.status!=='streaming')return
    state.frameReady=true;state.frameAt=now();state.frameSeq++
    if(frameDeadline!==null)cancel(frameDeadline)
    frameDeadline=schedule(()=>{state.frameReady=false;revoke('相机断流，旧控制输入已撤销')},1500)
  }
  function retainPreview() { viewers++;return ()=>{viewers=Math.max(0,viewers-1);if(viewers===0)stop('预览已关闭')} }
  function lifecycle(win=globalThis.window,doc=globalThis.document) {
    const hidden=()=>{if(doc?.hidden)stop('页面已隐藏，预览与旧控制输入已关闭')}
    const leave=()=>stop('页面已离开，预览与旧控制输入已关闭')
    win?.addEventListener?.('pagehide',leave);doc?.addEventListener?.('visibilitychange',hidden)
    return ()=>{win?.removeEventListener?.('pagehide',leave);doc?.removeEventListener?.('visibilitychange',hidden);stop('预览已离开')}
  }
  return {state,start,stop,lifecycle,presentFrame,retainPreview}
}
