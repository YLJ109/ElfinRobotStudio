import { reactive } from 'vue'
import { cleanHandLandmarks } from './handGeometry.js'
export const handPipelineState=reactive({enabled:false,source:null,session:null,epoch:-1,result:null,error:'',connected:false,detectorState:'idle',detectorError:'',detectorReady:false})
export function handPipelineObservation(packet, session, now=performance.now()) {
  const snapshot=packet?.snapshot,result=snapshot?.result
  const empty={landmarks:[],frameSeq:0,timestamp:0,controlValid:false,displayValid:false,error:snapshot?.error || ''}
  if(!snapshot?.enabled || snapshot.session!==session || !result || result.session!==session || result.epoch!==snapshot.epoch || result.source!==snapshot.source || result.coords!=='image_normalized')return empty
  if(!Number.isSafeInteger(result.seq) || result.seq<=0 || !Number.isFinite(snapshot.server_now) || !Number.isFinite(result.captured_at) || !Number.isFinite(packet.receivedAt) || !Number.isFinite(packet.requestStartedAt))return empty
  const roundTrip=Math.max(0,packet.receivedAt-packet.requestStartedAt)
  const capture=result.clock_domain==='server'?result.captured_at:result.received_at
  const age=(snapshot.server_now-capture)*1000+roundTrip+Math.max(0,now-packet.receivedAt)
  if(!Number.isFinite(age) || age<0)return empty
  const hand=result.observations?.find(item=>item.kind==='hand'),landmarks=cleanHandLandmarks(hand?.landmarks)
  const valid=!!result.valid && Array.isArray(hand?.landmarks) && hand.landmarks.length===21 && landmarks.length===21 && landmarks.every(Boolean)
  const remaining=(result.valid_until-snapshot.server_now)*1000-roundTrip-Math.max(0,now-packet.receivedAt)
  return {landmarks:valid?landmarks:[],frameSeq:result.seq,timestamp:now-age,
    displayValid:valid && age<1000,controlValid:valid && snapshot.source==='local' && result.clock_domain==='server' && result.control_valid===true && age<300 && remaining>0,
    error:result.error || snapshot.error || '',inferenceMs:(result.inference_finished_at-result.inference_started_at)*1000,
    model:result.model,roundTripMs:roundTrip}
}
// 后端快照里的检测器字段统一成前端命名，避免每个消费方各写一套映射。
function normalizeSnapshot(snapshot){
  const {detector_state,detector_error,detector_ready,...rest}=snapshot||{}
  return {...rest,detectorState:detector_state??'idle',detectorError:detector_error??'',detectorReady:!!detector_ready}
}
export function createHandPipelineClient({state=handPipelineState,fetcher=(...args)=>fetch(...args),now=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}) {
  let timer=null,abort=null,version=0,disposed=false,latest=null,configuration=Promise.resolve(),pendingConfiguration=null
  let pipelineOwner=null
  const subscribers=new Set()
  const emit=packet=>{latest=packet;for(const listener of subscribers)listener(packet)}
  async function request(path,body,signal){
    const response=await fetcher('/api/vision/pipeline'+path,{method:body===undefined?'GET':'POST',signal:signal || AbortSignal.timeout(3000),cache:'no-store',...(path==='/config' && body?.enabled===false?{keepalive:true}:{}),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})})
    const value=await response.json()
    if(!response.ok || value.ok===false)throw Error(value.why || value.detail || '手掌检测请求失败')
    return value
  }
  async function tick(){
    if(disposed || (!subscribers.size && !state.enabled))return
    const current=version,requestStartedAt=now(),controller=new AbortController();abort=controller
    let deadline=schedule(()=>controller.abort(),3000)
    try {
      const snapshot=await request('',undefined,controller.signal)
      if(current!==version || disposed)return
      // 结果未更新时不再整体写回响应式状态：33Hz 的无变化 Object.assign
      // 会让每个订阅组件重复渲染，正是光标跟随"一卡一卡"的一部分。
      // 但错误与检测器就绪状态必须参与比较：后端只改 error 时（检测器启动
      // 失败、相机被关闭）若被短路丢弃，前端会永远停在"检测中"的假状态。
      const unchanged=state.epoch===snapshot.epoch && state.enabled===snapshot.enabled
        && state.source===snapshot.source && state.session===snapshot.session
        && (state.result?.seq ?? -1)===(snapshot.result?.seq ?? -1)
        && (state.error ?? '')===(snapshot.error ?? '')
        && (state.detectorState ?? '')===(snapshot.detector_state ?? '')
        && (state.detectorError ?? '')===(snapshot.detector_error ?? '')
        && !!state.detectorReady===!!snapshot.detector_ready
      if(unchanged){state.connected=true;state.server_now=snapshot.server_now;return}
      Object.assign(state,normalizeSnapshot(snapshot),{connected:true})
      emit({snapshot,requestStartedAt,receivedAt:now()})
    } catch(error){if(current===version && !disposed){state.connected=false;state.result=null;state.error=error.message;emit(null)}}
    finally{cancel(deadline);if(abort===controller)abort=null;if(current===version && !disposed && (subscribers.size || state.enabled))timer=schedule(tick,Math.max(0,33-(now()-requestStartedAt)))}
  }
  function configure(enabled,source='local',session='',token='') {
    const key=JSON.stringify([enabled,source,session,token])
    if(pendingConfiguration?.key===key)return pendingConfiguration.result
    const current=++version
    cancel(timer);timer=null;abort?.abort();emit(null)
    state.result=null;state.error=''
    // 记录谁开的检测：后端 pipeline 是全局单例，卸载方只有在自己就是
    // 开启者时才允许关掉它，否则一个组件的卸载会掐掉别人的检测。
    // token 是调用方身份：只有 (来源, 会话) 无法区分"开启方"与"只负责
    // 渲染的画面组件"，两边都会填 local + 同一个 session，于是画面组件
    // 卸载时就能把别人的检测一起关掉 —— 跟随因此在切页时被取消。
    if(enabled)pipelineOwner={source,session,token}
    const perform=async()=>{
      if(current!==version || disposed)return false
      try {
        const snapshot=await request('/config',{enabled,source,session})
        Object.assign(state,normalizeSnapshot(snapshot),{connected:true})
        // 只有真的关掉了才交出所有权；失败时后端仍持有这份检测，必须保留
        // 归属，否则会留下一份"没人认领却还在跑"的孤儿检测。
        if(!enabled && pipelineOwner && pipelineOwner.source===source && pipelineOwner.session===session
            && (pipelineOwner.token||'')===(token||''))pipelineOwner=null
        if(subscribers.size || state.enabled)void tick()
        // 后端已经接受了这份配置就必须报成功。以前这里用 version 判断：
        // 同一时机若有别的组件也调了 configure（画面组件挂载/卸载、切流），
        // version 一变本次就返回 false，上层把"配置其实生效了"当成失败，
        // 界面随即显示"手掌检测启动失败"。版本号只用来决定要不要把过期
        // 快照写回状态，不该用来否定一次真正成功的请求。
        return true
      } catch(error){if(current===version){state.enabled=false;state.error=error.message;state.connected=false}return false}
    }
    const result=configuration.then(perform,perform)
    pendingConfiguration={key,result}
    const clear=()=>{if(pendingConfiguration?.result===result)pendingConfiguration=null}
    result.then(clear,clear)
    configuration=result.catch(()=>false)
    return result
  }
  // 只在"当前开启者就是本方"时关闭检测。owner 不匹配说明检测已被别人
  // 接管（例如另一个预览窗口或跟随面板），此时卸载不得越权关停。
  // token 必须一起比对：画面组件与相机面板的来源、会话完全相同，只比这两项
  // 会让画面组件在切换标签页时把面板刚开启的检测关掉。
  function release(source='local',session='',token=''){
    if(!pipelineOwner || pipelineOwner.source!==source || pipelineOwner.session!==session
        || (pipelineOwner.token||'')!==(token||''))return false
    const owner={...pipelineOwner}
    pipelineOwner=null
    const result=configure(false,source,session,token)
    // 关不掉就把所有权还回去：宁可继续由本方认领，也不要留下一份
    // "没人负责却还在跑"的全局检测。
    Promise.resolve(result).then(ok=>{if(ok===false)pipelineOwner=owner})
    return result
  }
  function owner(){return pipelineOwner ? {...pipelineOwner} : null}
  // 等待检测器真正可用：模型冷启动（首次约 10 秒）期间只能报"启动中"，
  // 启动失败必须带上后端给出的具体原因，绝不假装"检测已开启"。
  function waitForDetector(timeoutMs=25000){
    return new Promise(resolve=>{
      const deadline=Date.now()+timeoutMs
      let timer=null,done=false
      const finish=value=>{if(done)return;done=true;if(timer!=null)cancel(timer);resolve(value)}
      const check=()=>{
        if(disposed){finish({ok:false,error:'检测已停止'});return}
        const phase=state.detectorState
        if(phase==='ready'){finish({ok:true});return}
        if(phase==='failed'){finish({ok:false,error:state.detectorError||state.error||'检测器启动失败'});return}
        if(Date.now()>=deadline){finish({ok:false,error:'检测器启动超时'});return}
        timer=schedule(check,60)
      }
      check()
    })
  }
  function subscribe(listener){
    subscribers.add(listener);if(latest)listener(latest)
    if(subscribers.size===1 && !abort && !timer)void tick()
    return ()=>{subscribers.delete(listener);if(!subscribers.size && !state.enabled){cancel(timer);timer=null;abort?.abort();abort=null;latest=null}}
  }
  return {state,configure,release,owner,waitForDetector,subscribe,dispose(){disposed=true;version++;cancel(timer);abort?.abort();subscribers.clear();latest=null;pipelineOwner=null}}
}
export const handPipeline=createHandPipelineClient()
