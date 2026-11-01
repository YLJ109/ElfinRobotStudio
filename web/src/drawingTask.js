import {reactive,ref,watch,effectScope} from 'vue'
import {parseResponse} from './connectionState.js'
export const drawingShapes=[['heart','爱心'],['square','正方形'],['rectangle','长方形'],['circle','圆圈'],['nod','点头'],['shake','摇头']]
const finite=value=>typeof value==='number'&&Number.isFinite(value)
export function drawingConfig(value,mode){
 if(!['sim','real'].includes(mode))throw Error('当前模式不可用')
 if(!value||(!drawingShapes.some(([key])=>key===value.shape)&&value.shape!=='text'))throw Error('请选择绘制动作')
 if(!Array.isArray(value.center)||value.center.length!==6||!value.center.every(finite))throw Error('请填写完整基准位姿')
 if(!['xy','xz','yz'].includes(value.plane))throw Error('请选择绘制平面')
 if(!['width_mm','height_mm','angle_deg','speed_pct','repeats'].every(key=>finite(value[key])))throw Error('参数需要有限数值')
 const real=mode==='real',angular=['nod','shake'].includes(value.shape),maximum=real?50:500,width=angular?20:value.width_mm,height=angular?20:['square','circle'].includes(value.shape)?width:value.height_mm
 if(!finite(width)||width<1||width>maximum||!finite(height)||height<1||height>maximum)throw Error(`宽高需要1～${maximum}毫米`)
 if(angular&&(!finite(value.angle_deg)||Math.abs(value.angle_deg)<.5||Math.abs(value.angle_deg)>(real?5:15)))throw Error(`角度范围为±${real?5:15}°`)
 if(!finite(value.speed_pct)||value.speed_pct<1||value.speed_pct>(real?5:100))throw Error(`速度需要1～${real?5:100}%`)
 if(!Number.isInteger(value.repeats)||value.repeats<1||value.repeats>3)throw Error('重复次数需要1～3')
 const range=value.range_size_mm??(real?[50,50,50]:[900,300,500]),rangeCenter=value.range_center_mm??value.center.slice(0,3)
 const direction=value.direction??0
 if(![0,90,180,270].includes(direction)||(real&&direction!==0))throw Error('绘制朝向无效')
 drawingRange(rangeCenter,range,mode,direction)
 const policy=value.center_policy??'fixed'
 if(!['fixed','range_lower'].includes(policy)||(real&&policy!=='fixed'))throw Error('当前模式不允许自动绘制落点')
 if(real&&rangeCenter.some((v,i)=>Math.abs(v-value.center[i])>.001))throw Error('真机范围必须采用当前有效TCP')
 if(value.range_size_mm!==undefined&&!angular){
  const axes=({xy:[0,1],xz:[0,2],yz:[1,2]})[value.plane]
  if(axes.some((axis,i)=>(i?height:width)>range[axis]-4))throw Error('图形超出绘制范围，请调整尺寸')
 }
 const text=value.shape==='text'
 const writingZ=value.writing_z_mm??90
 if(text&&(!finite(writingZ)||writingZ<1||writingZ>800))throw Error('书写高度需要1～800毫米')
 if(text&&(typeof value.text!=='string'||Array.from(value.text.trim()).length<1||Array.from(value.text.trim()).length>5||!Array.isArray(value.strokes)||!value.strokes.length||!finite(value.font_size_mm)))throw Error('文字书写需要1～5个字符及有效笔画')
 if(text&&(value.font_size_mm<(real?2:20)||value.font_size_mm>(real?40:180)))throw Error(`字号需要${real?'2～40':'20～180'}毫米`)
 if(text&&real&&(value.plane!=='xy'||value.repeats!==1))throw Error('真机文字仅支持水平书写一次')
 return {direction,range_center_mm:[...rangeCenter],center_policy:policy,range_size_mm:[...range],shape:value.shape,center:[...value.center],plane:angular?'xy':value.plane,width_mm:width,height_mm:height,angle_deg:angular?value.angle_deg:0,speed_pct:value.speed_pct,repeats:value.repeats,...(text?{text:value.text,strokes:value.strokes,font_size_mm:value.font_size_mm,writing_z_mm:writingZ}:{})}
}
// A form range is visual only; it has no plan id or execution authority.
export function drawingBounds(value,mode){
 const config=drawingConfig(value,mode)
 return drawingRange(config.range_center_mm,config.range_size_mm,mode,config.direction)
}
export function drawingRange(center,size,mode='sim',direction=0){
 if(!Array.isArray(center)||center.length!==3||!center.every(v=>finite(v)&&Math.abs(v)<=10000)||!Array.isArray(size)||size.length!==3||!size.every(v=>finite(v)&&v>=5&&v<=(mode==='real'?50:5000)))throw Error('请填写有效范围坐标和尺寸')
 if(![0,90,180,270].includes(direction))throw Error('绘制朝向无效')
 const span=direction===90||direction===270?[size[1],size[0],size[2]]:size
 return {min:center.map((v,i)=>v-span[i]/2),max:center.map((v,i)=>v+span[i]/2)}
}

// Transform the plan's immutable tool contact offset with this exact live TCP frame.
// This is geometry only: no polling result is substituted for current feedback.
export function drawingFeedbackIssue(w,feedback){
 if(!w.connected||w.stateFresh===false||!w.snap?.session_id)return '等待有效后端状态'
 if(w.snap?.joints?.known!==true)return '等待有效机器人关节反馈'
 if(w.snap.mode==='real'){
  const real=w.snap.real
  if(!real?.cmd_up||!real?.stream_up)return '真机命令连接或反馈连接未就绪'
  if(real.quality!=='valid')return '真机反馈无效，请等待有效读数'
 }
 if(feedback?.session_id!==w.snap.session_id||feedback?.mode!==w.snap.mode)return '等待当前工具作用点'
 if(feedback.trace_tcp?.valid!==true)return feedback.trace_tcp?.why||'工具作用点未验证，请核对实际笔工具与TCP'
 return ''
}

export function drawingToolPose(snapshot,traceTcp){
 if(traceTcp?.valid!==true)return null
 const xyz=snapshot?.tcp?.xyz_mm,rpy=snapshot?.tcp?.rpy_deg,offset=traceTcp?.offset_in_snapshot_tcp_mm
 if(![xyz,rpy,offset].every(row=>Array.isArray(row)&&row.length===3&&row.every(finite)))return null
 const [a,b,c]=rpy.map(v=>v*Math.PI/180),[x,y,z]=offset
 const ca=Math.cos(a),sa=Math.sin(a),cb=Math.cos(b),sb=Math.sin(b),cc=Math.cos(c),sc=Math.sin(c)
 const rx=cb*cc*x+(sa*sb*cc-ca*sc)*y+(ca*sb*cc+sa*sc)*z
 const ry=cb*sc*x+(sa*sb*sc+ca*cc)*y+(ca*sb*sc-sa*cc)*z
 const rz=-sb*x+sa*cb*y+ca*cb*z
 const pose=[xyz[0]+rx,xyz[1]+ry,xyz[2]+rz,...rpy]
 return pose.every(finite)?pose:null
}

export function drawingView(bounds,plane,framing={}){
 if(!bounds||![bounds.min,bounds.max].every(row=>Array.isArray(row)&&row.length===3&&row.every(finite))||bounds.min.some((v,i)=>v>=bounds.max[i]))return null
 const min=[Math.min(0,bounds.min[0]/1000),Math.min(0,bounds.min[2]/1000),Math.min(0,-bounds.max[1]/1000)]
 const max=[Math.max(0,bounds.max[0]/1000),Math.max(.3,bounds.max[2]/1000),Math.max(0,-bounds.min[1]/1000)]
 const target=min.map((v,i)=>(v+max[i])/2),radius=Math.hypot(...min.map((v,i)=>(max[i]-v)/2))
 const fov=finite(framing.fov)&&framing.fov>=10&&framing.fov<=110?framing.fov:22
 const aspect=framing.view?.fullWidth>0&&framing.view?.fullHeight>0?framing.view.fullWidth/framing.view.fullHeight:1
 const half=Math.min(fov*Math.PI/360,Math.atan(Math.tan(fov*Math.PI/360)*aspect))
 return {theta:plane==='xz'?Math.PI/2:0,phi:plane==='xy'?.2:1.35,r:Math.min(12,Math.max(1.1,radius/Math.sin(half)*1.15)),target,fov}
}

// Bind motion meaning, not the global preferences counter. In simulation the
// mounted revision is also that counter, so geometry/TCP identify its tool.
export function drawingContext(w){
 const s=w.snap,settings=s?.settings||{},real=s?.mode==='real'
 const tool=value=>value?{
  mounted:{tool_id:value.mounted?.tool_id,state:value.mounted?.state,revision:real?value.mounted?.revision:undefined},
  tool:value.tool?{id:value.tool.id,type:value.tool.type,revision:real?value.tool.revision:undefined,
   tcp:value.tool.tcp?{translation_mm:value.tool.tcp.translation_mm,rpy_deg:value.tool.tcp.rpy_deg,status:value.tool.tcp.status}:undefined,
   payload:value.tool.payload,geometry:value.tool.geometry,collision:value.tool.collision,size:value.tool.size}:undefined,
  visual:real?undefined:value.visual
 }:null
 return JSON.stringify({connected:w.connected,fresh:w.stateFresh!==false,session:s?.session_id,mode:s?.mode,hasReal:!!s?.real,
  connection:real?{epoch:s?.real?.connection_epoch,command_session:s?.real?.command_session,cmd_up:s?.real?.cmd_up,stream_up:s?.real?.stream_up,quality:s?.real?.quality}:undefined,
  ack:real?{ok:s?.speed_ack?.ok,target:s?.speed_ack?.target,req:s?.speed_ack?.req}:undefined,
  model:w.meta?.model?.variant,modelLimits:w.meta?.model?{lower:w.meta.model.lower,upper:w.meta.model.upper,vmax:w.meta.model.vmax_urdf}:undefined,
  settings:Object.fromEntries(['limit_source','custom_limits','fence','self_collision','ground_enabled','vel_source','real_move_cmd','safety_yellow_pct','safety_red_pct','safety_alarm_pct'].map(key=>[key,settings[key]])),
  tcp:s?.tcp_frame,joints:s?.joints?{known:s.joints.known,lower:s.joints.lower,upper:s.joints.upper,vmax:s.joints.vmax}:undefined,
  tools:tool(s?.tools || w.tools),
  objects:Array.isArray(s?.objects)?s.objects.map(object=>({id:object.id,kind:object.kind,size:object.size,xyz:object.xyz,rotation:object.rotation,scale:object.scale,gravity:object.gravity,floating:object.floating,collision:object.collision,metadata:object.metadata?.collision_proxy})):undefined})
}
function drawingSpeed(w){const s=w.snap,key=s?.mode==='real'?'real_speed_pct':'sim_speed_pct';return s?.[key]??s?.settings?.[key]??s?.speed_pct}
export async function drawingRequest(action,payload,{signal,fetcher=globalThis.fetch}={}){
 const url='/api/drawing/'+action,timeout=action==='preview'?105000:action==='state'?5000:10000
 const response=await fetcher(url,{method:action==='state'?'GET':'POST',cache:'no-store',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(timeout)]):AbortSignal.timeout(timeout),...(action==='state'?{}:{headers:{'content-type':'application/json'},body:JSON.stringify(payload||{})})})
 const value=await parseResponse(response,url)
 if(value?.ok!==true)throw Error(value?.why||value?.error||'绘制请求未完成')
 return value
}
export function createDrawingTask(w,{request=drawingRequest,now=Date.now,schedule=setTimeout,unschedule=clearTimeout,setPath=()=>{},clearPath=()=>{},onLeaveError=message=>{w.error=message},confirmReplace=()=>globalThis.confirm?.('载入将替换当前程序草稿，继续？')===true}={}){
 const send=async(...args)=>{const value=await request(...args);if(value?.ok!==true)throw Error(value?.why||value?.error||'绘制请求未完成');return value}
 const state=reactive({snapshot:null,plan:null,pending:'',error:'',confirmedReal:false,baseAccepted:false,readFailed:false,stopping:false,visualPlanId:null,visualContext:'',runQueued:false})
 const owner='drawing:'+ (globalThis.crypto?.randomUUID?.()||Math.random().toString(36).slice(2))
 let retiredPlan=null
 const own=ref(null)
 let revision=0,mutation=0,readSerial=0,deadline=0,abort=null,timer=null,active=false,disposed=false,reading=false
 const scope=effectScope()
 const owned=()=>!!own.value&&state.snapshot?.plan_id===own.value
 const activePhase=value=>['checking','planning','running','paused'].includes(value?.phase)
 function expired(){return own.value===retiredPlan&&!!own.value||!deadline||now()>=deadline}
 function clear(){state.plan=null;deadline=0;state.confirmedReal=false;clearPath(owner)}
 function scopeCancel(id,report=false){
  return send('cancel',{plan_id:id}).catch(error=>{if(report)onLeaveError('绘制停止未确认：'+error.message);throw error})
 }
 function invalidate(reason='参数已修改，请重新预检'){
  state.runQueued=false
  const shouldCancel=own.value&&['ready','checking','planning','running','paused'].includes(state.snapshot?.phase)
  revision++;mutation++;abort?.abort();abort=null;state.pending='';state.error=reason;clear()
  const previous=own.value;own.value=null
  if(shouldCancel)void scopeCancel(previous,true).catch(()=>{})
 }
 function accept(value,sentAt=now()){
  if(!value||value.ok!==true||!['idle','checking','planning','ready','running','paused','completed','cancelled','failed','expired','invalid'].includes(value.phase))throw Error('绘制状态无效')
  if(value?.mode && value.mode!==w.snap?.mode || value?.session_id && value.session_id!==w.snap?.session_id)throw Error('绘制状态不属于当前模式或会话')
  state.snapshot=value;state.readFailed=false
  if(!owned()){clear();return}
  if(value.phase==='ready'){
   const candidate=Number.isFinite(value.expires_in_s)?sentAt+Math.max(0,value.expires_in_s)*1000:0
   deadline=deadline&&candidate?Math.min(deadline,candidate):candidate
   if(!expired()){
    if(!Array.isArray(value.path)||value.path.length<2||value.path.length>(value.config?.shape==='text'?2048:1024)||value.path.some(point=>!Array.isArray(point)||point.length!==6||!point.every(finite)))throw Error('预检路径无效')
    if(typeof value.program!=='string'||!value.program.trim())throw Error('预检程序无效')
    state.plan=value;setPath(value.path,{owner})
   }else {retiredPlan=own.value;state.plan=null;state.confirmedReal=false;clearPath(owner);state.error='预检已过期，请重新预检'}
  }else if(['cancelled','failed','expired','invalid'].includes(value.phase)){state.runQueued=false;clear()}
  if(value.phase==='ready'&&state.runQueued)queueMicrotask(runQueued)
 }
 async function runQueued(){
  if(!state.runQueued||state.pending||!owned()||state.snapshot?.phase!=='ready')return false
  state.runQueued=false
  return act('run')
 }
 async function requestRun(config){
  if(w.snap?.mode!=='sim')return act('run')
  if(state.runQueued||state.stopping)return false
  if(owned()&&state.snapshot?.phase==='ready')return act('run')
  state.runQueued=true
  if(owned()&&['checking','planning'].includes(state.snapshot?.phase))return true
  const accepted=await preview(config)
  if(!accepted)state.runQueued=false
  else await runQueued()
  return accepted
 }
 function blocked(){
  if(!w.connected||w.stateFresh===false||!w.snap?.session_id)return '连接已失效'
  if(!['sim','real'].includes(w.snap.mode))return '当前模式不可用'
  if(state.readFailed)return '状态读取失败，请重试'
  if(state.snapshot?.plan_id&&state.snapshot.plan_id!==own.value&&activePhase(state.snapshot))return '另一绘制作业正在运行'
  return ''
 }
 function runReason(){
  const why=blocked();if(why)return why
  if(!owned()||!state.plan||state.snapshot?.phase!=='ready')return '请先预检路径'
  if(expired())return '预检已过期，请重新预检'
  if(!w.snap?.motion_granted)return '当前未就绪'
  if(w.snap.mode==='real'&&(!state.baseAccepted||!state.confirmedReal))return '请确认基准与真机执行'
  if(state.snapshot?.can_run===false)return state.snapshot.why||'当前条件不允许执行'
  return ''
 }
 async function read(){
  if(disposed||reading)return false
  reading=true;const version=revision,epoch=mutation,ticket=++readSerial,context=drawingContext(w)
  try{
   const sentAt=now(),value=await send('state')
   if(disposed||version!==revision||epoch!==mutation||ticket!==readSerial||context!==drawingContext(w))return false
   accept(value,sentAt);return true
  }catch(error){if(!disposed&&version===revision&&epoch===mutation){state.runQueued=false;state.readFailed=true;state.error=error.message;clear()}return false}
  finally{reading=false}
 }
 async function preview(config){
  if(disposed||state.pending||state.stopping)return false
  const why=blocked();if(why){state.error=why;return false}
  let payload
  try{payload=drawingConfig(config,w.snap.mode);if(w.snap.mode==='real'&&!state.baseAccepted)throw Error('请先采用当前基准')}catch(error){state.error=error.message;return false}
  if(owned()&&activePhase(state.snapshot)){state.error='请先停止当前绘制';return false}
  const previous=own.value&&state.snapshot?.phase==='ready'?own.value:null;own.value=null;state.visualPlanId=null;clear();const version=++revision;mutation++;const context=drawingContext(w)
  abort?.abort();const current=new AbortController();abort=current;state.pending='preview';state.error=''
  try{
   if(previous)await scopeCancel(previous)
   const sentAt=now(),value=await send('preview',payload,{signal:current.signal})
   if(disposed||version!==revision||context!==drawingContext(w)){if(value?.plan_id)void scopeCancel(value.plan_id,true).catch(()=>{});return false}
   if(!value?.plan_id)throw Error('预检未返回任务编号')
   own.value=value.plan_id;state.visualPlanId=own.value;state.visualContext=context;accept(value,sentAt);return true
  }catch(error){if(!disposed&&version===revision)state.error=error.message;return false}
  finally{if(version===revision){state.pending='';abort=null}}
 }
 async function act(action){
  if(action==='cancel')return cancel()
  if(disposed||state.pending||state.stopping||!owned())return false
  if(action==='run'){const why=runReason();if(why){state.error=why;return false}}
  else if(action==='pause'&&state.snapshot?.phase!=='running')return false
  else if(action==='resume'){
   if(state.snapshot?.phase!=='paused')return false
   const why=blocked()||(!w.snap?.motion_granted?'当前未就绪':'')
   if(why){state.error=why;return false}
  }
  else if(!['run','pause','resume'].includes(action))return false
  const id=own.value,version=revision,context=drawingContext(w);mutation++;state.pending=action;state.error=''
  try{
   const sentAt=now(),value=await send(action,{plan_id:id,...(action==='run'&&w.snap.mode==='real'?{confirm_real:true}:{})})
   if(disposed||version!==revision||id!==own.value||context!==drawingContext(w))return false
   accept(value,sentAt);return true
  }catch(error){if(!disposed&&version===revision)state.error=error.message;return false}
  finally{if(version===revision)state.pending=''}
 }
 async function cancel(){
  state.runQueued=false
  if(!own.value||state.stopping)return false
  const id=own.value,version=++revision;mutation++;abort?.abort();abort=null;state.pending='';state.stopping=true;clear()
  try{const value=await scopeCancel(id);if(version===revision&&!disposed){state.snapshot=value;own.value=null;state.error=''}return true}
  catch(error){if(!disposed)state.error='停止未确认：'+error.message;return false}
  finally{state.stopping=false}
 }
 function refreshValidity(){if(owned()&&state.snapshot?.phase==='ready'&&expired()){retiredPlan=own.value;state.plan=null;state.confirmedReal=false;clearPath(owner);state.error='预检已过期，请重新预检'}}
 function edit(){state.visualPlanId=null;state.visualContext='';invalidate()}
 function adoptBase(){state.baseAccepted=true;state.confirmedReal=false}
 function loadProgram(){
  if(!state.plan||!owned()||expired()||typeof state.plan.program!=='string'||!state.plan.program.trim()){state.error='请先预检有效程序';return false}
  if(String(w.programSrc||'').trim()&&w.programSrc!==state.plan.program&&!confirmReplace())return false
  w.programSrc=state.plan.program;w.workTab='program';w.view='work';return true
 }
 async function poll(){unschedule(timer);timer=null;if(!active||disposed)return;await read();if(active&&!disposed)timer=schedule(poll,activePhase(state.snapshot)?500:2000)}
 function visualSnapshot(){return state.visualPlanId&&state.snapshot?.plan_id===state.visualPlanId&&state.visualContext===drawingContext(w)?state.snapshot:null}
 function start(){if(active||disposed)return;active=true;void poll()}
 function leave(){state.visualPlanId=null;active=false;unschedule(timer);timer=null;invalidate('');state.baseAccepted=false}
 function dispose(){if(disposed)return;leave();disposed=true;scope.stop()}
 scope.run(()=>{
  watch(()=>drawingContext(w),()=>{
   const affected=!!own.value||state.pending==='preview'||!!state.plan
   state.visualPlanId=null;state.visualContext='';state.baseAccepted=false
   if(affected)invalidate('环境已变化，请重新预检')
   else {revision++;mutation++;state.confirmedReal=false}
  },{flush:'sync'})
  watch(()=>drawingSpeed(w),(value)=>{
   // Simulation's live toolbar override changes timing, not checked geometry.
   if(w.snap?.mode==='sim')return
   if(!own.value&&state.pending!=='preview')return
   const planned=state.snapshot?.config?.speed_pct
   const applyingOwnSpeed=w.snap?.mode==='sim'&&(state.pending==='run'||['running','paused'].includes(state.snapshot?.phase))
     &&finite(value)&&finite(planned)&&Math.abs(value-planned)<1e-6
   if(!applyingOwnSpeed){state.visualPlanId=null;state.baseAccepted=false;invalidate('倍率已变化，请重新预检')}
  },{flush:'sync'})
 })
 return {state,owner,owned,expired,blocked,runReason,read,preview,requestRun,act,cancel,edit,adoptBase,loadProgram,refreshValidity,visualSnapshot,start,leave,dispose}
}
