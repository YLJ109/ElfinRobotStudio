// Display only: the original Drawing service owns all motion and sampled ink.
import {drawingToolPose} from './drawingTask.js'
export function projectAiDrawing(w,id,{read=async path=>{
 const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(5000)})
 if(!response.ok)throw Error('绘制状态读取失败');return response.json()
},scene=()=>globalThis.window?.__elfinScene,schedule=setTimeout,unschedule=clearTimeout}={}){
 const owner='ai-drawing:'+id,session=w.snap?.session_id
 let closed=false,timer,failures=0
 function stop(clear=false){closed=true;unschedule(timer);if(clear)scene()?.clearDrawing(owner)}
 async function poll(){
  if(closed)return
  if(!w.connected||w.snap?.mode!=='sim'||w.snap?.session_id!==session){stop(true);return}
  try{
   const value=await read('/api/drawing/state')
   if(closed)return
   if(value.plan_id!==id||value.session_id!==session||value.mode!=='sim'){stop();return}
   failures=0
   const tcp=drawingToolPose(w.snap,value.trace_tcp)
   scene()?.setDrawingState({owner,plan_id:id,session,shape:value.config?.shape,
    path:value.trace_path||value.path,trace_paths:value.trace_paths,actual_samples:value.actual_samples,
    bounds:value.bounds,plane:value.config?.plane,mode:'sim',phase:value.phase,progress:value.progress,tcp,
    valid:!!tcp&&w.stateFresh!==false,trace_active:value.trace_active,preview_visible:false})
   if(['completed','failed','cancelled','stopped','expired'].includes(value.phase)){stop();return}
  }catch(error){if(closed)return;if(++failures>=3){w.error=error.message||'绘制显示状态未确认';stop();return}}
  timer=schedule(poll,150);timer?.unref?.()
 }
 void poll();return stop
}
