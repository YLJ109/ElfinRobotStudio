import { reactive } from 'vue'
import { parseResponse } from './connectionState.js'

export const taskKinds={find:{label:'视觉找物'},count:{label:'计数检查'},kit:{label:'配套装盒'},pattern:{label:'图案拼摆'}}
const glyphs={ '0':['111','101','101','101','111'],'1':['010','110','010','010','111'],'2':['111','001','111','100','111'],'3':['111','001','111','001','111'],'A':['010','101','111','101','101'],'H':['101','101','111','101','101'] }
export function patternCells(symbol,color='red'){
  if(!glyphs[symbol] || !['red','green','blue'].includes(color))throw Error('请选择已支持的图案和颜色')
  return glyphs[symbol].join('').split('').map(value=>value==='1'?color:null)
}
export function assemblyPayload(mode,group,columns,rows,cells){
  if(!group)throw Error('请先布置物料')
  return {...assemblyRecipe(mode,columns,rows,cells),group}
}
export function assemblyRecipe(mode,columns,rows,cells){
  if(!['kit','pattern'].includes(mode))throw Error('任务类型无效')
  if(!Number.isInteger(columns)||!Number.isInteger(rows)||columns<1||columns>5||rows<1||rows>5)throw Error('行列数需要1～5')
  if(cells.length!==columns*rows || cells.some(value=>value!==null&&!['red','green','blue'].includes(value)) || !cells.some(Boolean))throw Error('请配置至少一个有效格位')
  return {mode,columns,rows,cells:[...cells]}
}
export function createPracticalTask({prefix,fetcher=globalThis.fetch,onLeaveError=()=>{},requireConfirmation=true}){
  const state=reactive({snapshot:null,pending:'',error:'',confirmed:false,readFailed:false})
  let version=0,mutation=0,readVersion=0,disposed=false,controller=null
  async function request(action,payload,signal){
    const response=await fetcher(prefix+'/'+action,{method:action==='state'?'GET':'POST',cache:'no-store',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(action==='inspect'?105000:15000)]):AbortSignal.timeout(15000),...(action==='state'?{}:{headers:{'content-type':'application/json'},body:JSON.stringify(payload || {})})})
    const value=await parseResponse(response,prefix+'/'+action)
    if(value?.ok!==true)throw Error(value?.why||value?.error||'任务请求未完成')
    return value
  }
  async function read(){
    const epoch=mutation,ticket=++readVersion
    try{const value=await request('state');if(!disposed&&epoch===mutation&&ticket===readVersion){if(state.snapshot?.context_id!==value.context_id || state.snapshot?.preview_id!==value.preview_id || state.snapshot?.task?.preview_id!==value.task?.preview_id)state.confirmed=false;state.snapshot=value;state.readFailed=false}}
    catch(error){if(!disposed&&epoch===mutation&&ticket===readVersion){state.error=error.message;state.readFailed=true;state.confirmed=false}}
  }
  async function act(action,payload){
    if(disposed || (state.pending && action!=='cancel') || (action==='run'&&requireConfirmation&&!state.confirmed))return false
    const ticket=++version;mutation++;controller?.abort();controller=new AbortController();state.pending=action;state.error='';state.confirmed=false
    try{const value=await request(action,payload,controller.signal);if(disposed||ticket!==version)return false;mutation++;state.snapshot=value;return true}
    catch(error){if(!disposed&&ticket===version)state.error=error.name==='AbortError'?'请求未确认，请刷新状态后再操作':error.message;return false}
    finally{if(ticket===version)state.pending=''}
  }
  function leave(){
    if(disposed)return
    const snapshot=state.snapshot?.task||state.snapshot,needsCancel=state.pending || ['checking','running','planning'].includes(snapshot?.phase)
    disposed=true;version++;mutation++;controller?.abort()
    if(needsCancel&&snapshot?.preview_id)request('cancel',{preview_id:snapshot.preview_id}).catch(error=>onLeaveError('任务离页取消未确认：'+error.message))
  }
  return {state,read,act,leave}
}
