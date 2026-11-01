import {watch} from 'vue'
export async function ensureSimTaskPen(w,task){
 if(w.snap?.mode!=='sim'||w.snap?.real)throw Error('自动换笔仅用于仿真')
 if(w.snap?.tools?.visual?.kind==='pen')return true
 const session=w.snap.session_id,result=await w.call('/api/sim/task-tool',{task})
 if(!result?.ok)throw Error(result?.why||'笔工具切换失败')
 if(w.snap?.session_id!==session||w.snap?.mode!=='sim')throw Error('连接已变化，请重试')
 if(w.snap?.tools?.visual?.kind==='pen')return true
 return new Promise((resolve,reject)=>{
  let stop=()=>{},timer
  const check=()=>{
   if(w.snap?.session_id!==session||w.snap?.mode!=='sim'){clearTimeout(timer);stop();reject(Error('连接已变化，请重试'));return}
   if(w.snap?.tools?.visual?.kind==='pen'){clearTimeout(timer);stop();resolve(true)}
  }
  stop=watch(()=>[w.snap?.tools?.visual?.kind,w.snap?.session_id,w.snap?.mode],check)
  timer=setTimeout(()=>{stop();reject(Error('笔工具切换未确认，请重试'))},5000);check()
 })
}
