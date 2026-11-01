import { shallowReactive, watch } from 'vue'
import { createBrowserCameraPreview } from './browserCameraPreview.ts'

const PREFIX='/api/vision/remote'
export function limitVideoSdp(sdp:string):string {
  if((sdp.match(/^m=video /gm)||[]).length!==1 || /^m=(?!video )/m.test(sdp))throw Error('仅支持一路视频')
  if((sdp.match(/^a=sendonly\r?$/gm)||[]).length!==1 || /^a=(?:sendrecv|recvonly|inactive)\r?$/m.test(sdp))throw Error('仅支持单向上传视频')
  const sections=sdp.split(/(?=^m=)/m)
  return sections.map(section=>section.startsWith('m=video ')?section.replace(/^b=(?:AS|TIAS):.*\r?\n/gm,'').replace(/^(m=video .*\r?\n)/,'$1b=AS:1500\r\n'):section).join('')
}
export function trustedRemoteLink(value:string, origin=globalThis.location?.origin):string {
  try {const url=new URL(value);return url.protocol==='https:' && url.origin===origin && url.pathname==='/remote-camera.html' && !url.username && !url.password && !url.search && !url.hash?url.href:''}catch{return ''}
}
export function createRemoteCamera({preview=createBrowserCameraPreview(),Peer=globalThis.RTCPeerConnection,
  request=globalThis.fetch,secure=()=>globalThis.location?.protocol==='https:',schedule=setTimeout,cancel=clearTimeout,now=()=>performance.now()}={}) {
  const state=shallowReactive({phase:'idle',error:'',result:null as any,session:'',clock:null as any,detector:'hand'})
  let peer:RTCPeerConnection|null=null,token='',generation=0,heartbeat:ReturnType<typeof setTimeout>|null=null
  let currentAbort:AbortController|null=null,stopping=false,clockAck:{nonce:string,received_ms:number}|null=null
  const leave=(secret:string)=>secret?request(PREFIX+'/leave',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+secret},body:'{}',keepalive:true}).catch(()=>{}):Promise.resolve()
  async function call(path:string,body:any,secret='',signal?:AbortSignal) {
    const response=await request(PREFIX+path,{method:'POST',headers:{'Content-Type':'application/json',...(secret?{Authorization:'Bearer '+secret}:{})},body:JSON.stringify(body),signal:signal?AbortSignal.any([signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000),cache:'no-store'})
    const value=await response.json()
    if(!response.ok || value.ok===false)throw Error(value.why || value.detail || '检测连接失败')
    return value
  }
  function step<T>(promise:Promise<T>,signal:AbortSignal):Promise<T> {
    return new Promise((resolve,reject)=>{
      const stopped=()=>{cleanup();reject(Error('连接已取消'))}
      const timer=schedule(()=>{cleanup();reject(Error('视频连接建立超时'))},10000)
      function cleanup(){cancel(timer);signal.removeEventListener('abort',stopped)}
      signal.addEventListener('abort',stopped,{once:true})
      promise.then(value=>{cleanup();resolve(value)},error=>{cleanup();reject(error)})
      if(signal.aborted)stopped()
    })
  }
  function stop(reason='',release=true) {
    if(stopping)return
    stopping=true;generation++;currentAbort?.abort();currentAbort=null
    if(heartbeat!==null)cancel(heartbeat);heartbeat=null
    const old=token;token='';peer?.close();peer=null;state.session='';state.result=null
    clockAck=null;state.clock=null;state.detector='hand'
    state.phase=reason?'error':'idle';state.error=reason
    void leave(old)
    if(release)preview.stop('远程检测已关闭')
    stopping=false
  }
  async function startDetection(code:string) {
    if(!['idle','error'].includes(state.phase))return false
    if(!secure()){state.error='上传检测需要工作站同源 HTTPS';state.phase='error';return false}
    if(!Peer){state.error='此浏览器不支持视频连接';state.phase='error';return false}
    if(preview.state.status!=='streaming' || !preview.state.stream){state.error='请先开启本地预览';return false}
    if(!code.trim()){state.error='请输入工作站配对码';return false}
    stop('',false)
    const epoch=++generation,stream=preview.state.stream
    const abort=new AbortController();currentAbort=abort
    state.phase='joining';state.error=''
    try {
      const joined=await call('/join',{code:code.trim()},'',abort.signal)
      if(epoch!==generation){void leave(joined.token);return false}
      if(typeof joined.token!=='string' || !joined.token)throw Error('配对未返回有效会话')
      token=joined.token;state.session=joined.session
      if(joined.detector!=null && !['hand','workpiece'].includes(joined.detector))throw Error('工作站检测内容无效')
      state.detector=joined.detector || 'hand'
      const track=stream.getVideoTracks()[0]
      await step(track.applyConstraints({width:{max:1280},height:{max:720},frameRate:{max:15}}),abort.signal)
      if(epoch!==generation)return false
      const settings=track.getSettings()
      if(!settings.width || !settings.height || settings.width>1280 || settings.height>720 || (settings.frameRate && settings.frameRate>15.1))throw Error('相机未满足远程视频尺寸或帧率限制')
      const connection=new Peer({iceServers:[]});peer=connection
      connection.ontrack=()=>{if(epoch===generation)stop('工作站返回了意外视频，已关闭连接')}
      connection.onconnectionstatechange=()=>{if(epoch===generation && ['failed','disconnected','closed'].includes(connection.connectionState))stop('视频连接已断开，请重新配对')}
      const sender=connection.addTransceiver(track,{direction:'sendonly',streams:[stream]}).sender
      const parameters=sender.getParameters();parameters.encodings=parameters.encodings?.length?parameters.encodings:[{}]
      for(const encoding of parameters.encodings){encoding.maxBitrate=1500000;encoding.maxFramerate=15}
      await step(sender.setParameters(parameters),abort.signal)
      if(epoch!==generation)return false
      const offer=await step(connection.createOffer(),abort.signal)
      if(epoch!==generation)return false
      await step(connection.setLocalDescription({type:'offer',sdp:limitVideoSdp(offer.sdp || '')}),abort.signal)
      if(epoch!==generation)return false
      await new Promise<void>((resolve,reject)=>{
        if(connection.iceGatheringState==='complete'){resolve();return}
        const done=()=>{if(connection.iceGatheringState==='complete'){cleanup();resolve()}}
        const aborted=()=>{cleanup();reject(Error('连接已取消'))}
        const timeout=schedule(()=>{cleanup();reject(Error('视频连接建立超时'))},5000)
        function cleanup(){cancel(timeout);connection.removeEventListener('icegatheringstatechange',done);abort.signal.removeEventListener('abort',aborted)}
        connection.addEventListener('icegatheringstatechange',done);abort.signal.addEventListener('abort',aborted,{once:true})
        if(abort.signal.aborted)aborted()
      })
      if(epoch!==generation)return false
      state.phase='connecting'
      const answer=await call('/offer',{type:'offer',sdp:limitVideoSdp(connection.localDescription?.sdp || '')},token,abort.signal)
      if(epoch!==generation)return false
      if(answer.type!=='answer' || answer.session!==state.session)throw Error('工作站响应会话不匹配')
      await step(connection.setRemoteDescription({type:'answer',sdp:answer.sdp}),abort.signal)
      if(epoch!==generation)return false
      const poll=async()=>{
        try {
          const sent=now()
          const value=await call('/heartbeat',{clock:{sent_ms:sent,...(clockAck?{previous:clockAck}:{})}},token,abort.signal)
          if(epoch!==generation)return
          const received=now(),probe=value.clock_probe
          clockAck=null
          if(probe!=null){
            if(typeof probe.nonce!=='string'||!probe.nonce||probe.client_sent_ms!==sent||!Number.isFinite(probe.server_received_ms)||!Number.isFinite(probe.server_sent_ms)||probe.server_sent_ms<probe.server_received_ms||received<sent)throw Error('工作站时钟回执无效')
            clockAck={nonce:probe.nonce,received_ms:received}
          }
          // This maps heartbeat clocks only. RTP frames still have no verified
          // sensor capture timestamp; an estimate can never grant motion.
          state.clock=value.state?.clock?{...value.state.clock,capture_age_available:false,control_qualified:false}:null
          if(value.state?.error)throw Error(value.state.error)
          if(!value.state?.session)throw Error('检测会话已关闭，请重新配对')
          if(value.state?.session && value.state.session!==state.session)throw Error('检测会话已失效')
          if(value.state?.detector && value.state.detector!==state.detector)throw Error('检测内容已切换，请重新配对')
          // Remote clock is untrusted: display only, never publish control input.
          const result=value.result
          if(result && (result.source!=='remote' || result.session!==state.session))throw Error('检测结果来源已变化')
          state.result=result?{...result,control_valid:false}:null
          if(value.result?.error)throw Error(value.result.error)
          if(value.result?.valid===false)throw Error('工作站检测未成功，请停止后重试')
          state.phase=value.result?.valid?'detecting':'connecting'
          heartbeat=schedule(()=>{void poll()},3000)
        }catch(error){if(epoch===generation)stop((error as Error).message)}
      }
      await poll();return epoch===generation
    }catch(error){if(epoch===generation)stop((error as Error).message);return false}
    finally{if(epoch===generation)currentAbort=abort}
  }
  const unwatch=watch(()=>[preview.state.stream,preview.state.status,preview.state.frameReady],()=>{
    if(!stopping && !['idle','error'].includes(state.phase) && (preview.state.status!=='streaming' || !preview.state.stream || (!preview.state.frameReady && preview.state.frameSeq>0)))stop('相机已停止或断流，请重新配对')
  })
  function lifecycle(win=globalThis.window,doc=globalThis.document) {
    const hide=()=>{if(doc.hidden)stop()},exit=()=>stop()
    win?.addEventListener('pagehide',exit);doc?.addEventListener('visibilitychange',hide)
    return ()=>{win?.removeEventListener('pagehide',exit);doc?.removeEventListener('visibilitychange',hide);unwatch();stop()}
  }
  return {state,preview,startDetection,stop,lifecycle}
}
