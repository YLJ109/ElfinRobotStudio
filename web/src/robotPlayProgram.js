import { reactive, computed } from 'vue'
import { playAllowed } from './functionPlay.js'

export function tcpWaitPose(tcp){
  const pose=[...(tcp?.xyz_mm || []),...(tcp?.rpy_deg || [])]
  if(pose.length!==6 || !pose.every(Number.isFinite))throw new Error('当前 TCP 不完整，不能作为等待位')
  return pose
}
export function playPointTab(role){
  const key=String(role || '').toLowerCase().replace(/[ -]/g,'_')
  if(['wait','waiting','home'].includes(key))return 'wait'
  if(['a_up','a_above','a_approach','pick_up','pick_above','above_pick','source_above','pick_approach','a_safe'].includes(key))return 'a_up'
  if(['b_up','b_above','b_approach','place_up','place_above','above_place','target_above','place_approach','b_safe'].includes(key))return 'b_up'
  if(['a_pick','pick','source','pickup'].includes(key))return 'a_pick'
  if(['b_place','place','target','drop'].includes(key))return 'b_place'
  return 'other'
}
const paths={generate:'/api/play/program',run:'/api/play/program/run',state:'/api/play/program/state',cancel:'/api/play/program/cancel',stop:'/api/motion/cancel',pause:'/api/motion/pause',resume:'/api/motion/resume'}
export async function robotPlayRequest(action,payload,{signal,fetcher=globalThis.fetch}={}){
  if(!Object.hasOwn(paths,action))throw new Error('玩法程序接口无效')
  const timeout=action==='generate'?105000:action==='state'?5000:action==='stop'?10000:15000
  const response=await fetcher(paths[action],{method:action==='state'?'GET':'POST',cache:'no-store',
    ...(action==='state'?{}:{headers:{'content-type':'application/json'},body:JSON.stringify(payload || {})}),
    signal:signal?AbortSignal.any([signal,AbortSignal.timeout(timeout)]):AbortSignal.timeout(timeout)})
  const value=await response.json()
  if(!response.ok || value?.ok!==true){
    const detail=value?.why || value?.detail || '玩法程序请求失败'
    const error=new Error(typeof detail==='string'?detail:JSON.stringify(detail))
    error.serverRejected=true;throw error
  }
  return value
}

export function createRobotPlayController(w,{request=robotPlayRequest,schedule=setTimeout,cancel=clearTimeout}={}){
  const state=reactive({planning:false,serverPlanning:false,planningUncertain:false,cancelling:false,cancelPending:false,progress:null,elapsed_s:null,timeout_s:null,can_cancel:null,submitting:false,active:false,uncertain:false,cancelled:false,held:null,vacuum:null,execution:null,serverMessage:'',plan:null,source:'',message:'',parameters:null})
  const running=computed(()=>state.active || state.uncertain || ['running','waiting','paused'].includes(w.snap?.exec?.state))
  const prechecking=computed(()=>state.planning || state.serverPlanning || state.planningUncertain || state.cancelPending || state.cancelling)
  const busy=computed(()=>prechecking.value || !!state.held || state.submitting || running.value)
  let planning=null,version=0,watchers=0,timer=null,pollAbort=null,pollVersion=0,activityRevision=0
  async function generate(parameters){
    if(busy.value || !playAllowed(w))return false
    if(!parameters?.group || !['sorting','chess'].includes(parameters.action)){state.message='请先布置任务物体';return false}
    if(parameters.action==='chess' && (!Number.isInteger(parameters.slot) || parameters.slot<0 || parameters.slot>8)){state.message='请先选择空棋盘格位';return false}
    const session=w.snap.session_id,token=++version,controller=new AbortController()
    activityRevision++
    planning=controller;state.planning=true;state.plan=null;state.progress=null;state.elapsed_s=null;state.timeout_s=null;state.can_cancel=null;state.serverMessage='';state.source=typeof parameters.source==='string'?parameters.source:'';state.message='正在预检生成，尚未执行机器人动作…'
    state.parameters=JSON.parse(JSON.stringify(parameters))
    let received=false
    try{
      const value=await request('generate',state.parameters,{signal:controller.signal})
      if(token!==version || controller.signal.aborted)return false
      received=true
      state.serverPlanning=false;state.planningUncertain=false
      if(w.snap.session_id!==session || !playAllowed(w))throw new Error('仿真会话或运行状态已变化，请重新预检')
      if(!value.plan_id || typeof value.source!=='string' || !value.source.trim() || !Array.isArray(value.points))throw new Error('服务端程序结果不完整')
      state.plan={...value,group:parameters.group,action:parameters.action,slot:parameters.slot,session};state.source=value.source
      state.message='预检生成完成，检查点位与程序后人工确认执行。';return true
    }catch(error){
      if(token===version){
        state.planningUncertain=!received && !error.serverRejected
        state.message=error.message+(state.planningUncertain?'；服务端预检状态待确认，请等待状态更新或取消预检。':'')
      }
      return false
    }
    finally{if(token===version){activityRevision++;state.planning=false;planning=null}}
  }
  async function cancelWaiting(){
    if(!prechecking.value || state.cancelling || state.can_cancel===false)return false
    version++;activityRevision++;planning?.abort();planning=null;state.planning=false;state.plan=null
    state.cancelPending=true;state.planningUncertain=true;state.cancelling=true
    state.message='正在取消预检，等待服务端确认；不会停止运动或执行程序。'
    try{
      await request('cancel',{})
      state.message='已提交取消预检，等待服务端确认。';return true
    }catch(error){state.message='取消预检请求失败：'+error.message+'；预检状态待确认，请等待状态更新或重试取消。';return false}
    finally{activityRevision++;state.cancelling=false}
  }
  function recheck(){if(!state.parameters || !state.source.trim())return false;return generate({...state.parameters,source:state.source})}
  async function execute(confirm=globalThis.confirm){
    if(busy.value || !playAllowed(w) || !state.plan || !state.source.trim())return false
    if(state.source!==state.plan.source){state.message='程序已修改，请先点击预检修改';return false}
    if(state.plan.session!==w.snap.session_id){state.message='会话已变化，请重新预检';return false}
    if(!(w.snap.objects || []).some(object=>object.task?.group===state.plan.group)){state.message='任务组已变化，请重新预检';return false}
    if(!confirm?.('确认执行机器人仿真取放程序？机器人将实际运动；修改的程序由服务端再次校验。'))return false
    if(!playAllowed(w) || state.plan.session!==w.snap.session_id){state.message='运行条件已变化，请重新预检';return false}
    activityRevision++
    state.submitting=true;state.cancelled=false;state.message='正在提交执行…'
    try{
      await request('run',{plan_id:state.plan.plan_id,source:state.source})
      state.active=true;state.message='已提交机器人仿真取放，请观察运行状态。';return true
    }catch(error){state.uncertain=true;state.message=error.message+'；执行状态待确认，请勿重复提交。';return false}
    finally{activityRevision++;state.submitting=false}
  }
  async function stop(){
    // 正确：停止是独立请求，取消规划等待不会代替停止程序。
    activityRevision++;state.plan=null;state.cancelled=true
    try{await request('stop',{});state.message='已提交取消，等待服务端确认；本次计划作废，不能继续，需重新预检。';return true}
    catch(error){state.message='停止失败：'+error.message;return false}
    finally{activityRevision++}
  }
  async function togglePause(){
    if(!running.value || state.submitting || state.cancelled)return false
    const paused=(state.execution?.state || w.snap?.exec?.state)==='paused'
    activityRevision++;state.submitting=true
    try{
      const result=await request(paused?'resume':'pause',{})
      state.execution={...(state.execution || {}),state:result.state || (paused?'running':'paused')}
      state.message=paused?'继续执行':'已暂停';return true
    }catch(error){state.message=error.message;return false}
    finally{state.submitting=false;activityRevision++}
  }
  async function poll(token){
    if(token!==pollVersion || !watchers)return
    const controller=new AbortController(),revision=activityRevision;pollAbort=controller
    try{
      const value=await request('state',undefined,{signal:controller.signal})
      if(token!==pollVersion || !watchers || state.submitting || state.cancelling || revision!==activityRevision)return
      state.active=!!value.active;state.held=value.held || null
      if(typeof value.planning==='boolean'){
        state.serverPlanning=value.planning
        if(!value.planning){
          state.planningUncertain=false
          if(state.cancelPending){state.cancelPending=false;state.message=value.message || '服务端已确认预检结束；未发送执行请求。'}
        }
      }
      state.progress=value.progress && typeof value.progress==='object'?{
        phase:typeof value.progress.phase==='string'?value.progress.phase:'',
        step:Number.isFinite(value.progress.step)?Math.max(0,value.progress.step):null,
        total:Number.isFinite(value.progress.total)?Math.max(0,value.progress.total):null,
        line:Number.isInteger(value.progress.line) && value.progress.line>0?value.progress.line:null,
        kind:typeof value.progress.kind==='string'?value.progress.kind:'',
      }:null
      state.elapsed_s=Number.isFinite(value.elapsed_s) && value.elapsed_s>=0?value.elapsed_s:null
      state.timeout_s=Number.isFinite(value.timeout_s) && value.timeout_s>0?value.timeout_s:null
      state.can_cancel=typeof value.can_cancel==='boolean'?value.can_cancel:null
      state.vacuum=value.vacuum || null;state.execution=value.execution || null;state.serverMessage=value.message || '';state.uncertain=false
    }catch(error){if(token===pollVersion && !controller.signal.aborted){if(state.active)state.uncertain=true;state.message='玩法状态读取失败：'+error.message}}
    finally{if(token===pollVersion){pollAbort=null;if(watchers)timer=schedule(()=>poll(token),700)}}
  }
  function subscribe(){
    watchers++;if(watchers===1)void poll(++pollVersion)
    let removed=false
    return ()=>{if(removed)return;removed=true;if(--watchers===0){pollVersion++;cancel(timer);pollAbort?.abort();pollAbort=null}}
  }
  function invalidate(){
    if(prechecking.value)void cancelWaiting()
    // Even when the backend temporarily disallows cancellation, a changed
    // context must never accept the old generation response as a usable plan.
    if(state.planning){version++;activityRevision++;planning?.abort();planning=null;state.planning=false;state.planningUncertain=true}
    state.plan=null;state.source=''
  }
  return {state,running,busy,prechecking,generate,recheck,execute,cancelWaiting,stop,togglePause,subscribe,invalidate}
}
const controllers=new WeakMap()
export function robotPlayFor(w){if(!controllers.has(w))controllers.set(w,createRobotPlayController(w));return controllers.get(w)}
