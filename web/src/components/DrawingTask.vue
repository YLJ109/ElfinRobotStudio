<script setup>
import { displayNumericText } from '../numberDisplay.js'
import {computed,reactive,ref,watch,watchEffect,onMounted,onActivated,onDeactivated,onBeforeUnmount} from 'vue'
import {useWorld} from '../store'
import {INTERACTION_RANGE_FACTORY,readInteractionRangeDefault,saveInteractionRangeDefault,interactionRange,rotateInteractionCenter} from '../interactionRange.js'
import {createDrawingTask,drawingConfig,drawingShapes,drawingRange,drawingToolPose,drawingFeedbackIssue} from '../drawingTask.js'
import {localDrawingPreview,frontDrawingView,drawingToolFrame} from '../drawingPreview.js'
import {ensureSimTaskPen} from '../simTaskPen.js'
const w=useWorld(),scene=()=>globalThis.window?.__elfinScene
const controller=createDrawingTask(w)
const s=controller.state,real=computed(()=>w.snap?.mode==='real')
function rangeStorage(){try{return globalThis.localStorage}catch{return null}}
const initialRange=readInteractionRangeDefault(w,{storage:rangeStorage()})
const form=reactive({shape:'heart',center:real.value?Array(6).fill(null):[...initialRange.center,90,0,0],plane:'xz',width_mm:real.value?40:280,height_mm:real.value?30:240,angle_deg:5,speed_pct:real.value?5:100,repeats:1})
const rangeSize=ref(real.value?[50,50,50]:[...initialRange.size]),rangeDirection=ref(270)
const rangeEdited=ref(false),savingDefault=ref(false)
let applyingDefault=false
const draftConfig=computed(()=>({...form,center:drawingToolFrame(form.center,rangeDirection.value,real.value?'real':'sim'),speed_pct:Number(w.snap?.[real.value?'real_speed_pct':'sim_speed_pct']??w.snap?.settings?.[real.value?'real_speed_pct':'sim_speed_pct']??(real.value?5:100)),direction:real.value?0:rangeDirection.value,range_center_mm:form.center.slice(0,3),center_policy:real.value?'fixed':'range_lower',range_size_mm:[...rangeSize.value]}))
const draftBounds=computed(()=>{try{return drawingRange(form.center.slice(0,3),rangeSize.value,real.value?'real':'sim',real.value?0:rangeDirection.value)}catch{return null}})
const displayPlane=computed(()=>form.plane)
const rangeError=computed(()=>draftBounds.value?'':'请填写有效范围坐标和尺寸')
function changeDirection(value){
 const next=Number(value)
 if(real.value||active.value||s.pending||![0,90,180,270].includes(next)||next===rangeDirection.value||rangeError.value)return
 form.center=[...rotateInteractionCenter(form.center.slice(0,3),rangeDirection.value,next),...form.center.slice(3)]
 rangeDirection.value=next;rangeEdited.value=true;controller.edit()
}
function applyRange(){if(!rangeError.value){controller.edit();message.value='范围已应用'}}
async function saveRangeDefault(){
 if(rangeError.value||savingDefault.value||active.value)return
 savingDefault.value=true
 try{const existing=readInteractionRangeDefault(w,{storage:rangeStorage()});await saveInteractionRangeDefault(w,{center:form.center.slice(0,3),size:[...rangeSize.value],direction:rangeDirection.value,depthRange:existing.depthRange});rangeEdited.value=false;message.value='已设为默认'}catch(error){message.value=error.message||'默认范围保存失败'}finally{savingDefault.value=false}
}
function resetRange(){
 form.center=real.value?Array(6).fill(null):[...INTERACTION_RANGE_FACTORY.center,90,0,0]
 rangeSize.value=real.value?[50,50,50]:[...INTERACTION_RANGE_FACTORY.size];rangeDirection.value=INTERACTION_RANGE_FACTORY.direction;s.baseAccepted=false;message.value='已恢复初始范围'
}
watch(rangeSize,()=>{if(!applyingDefault)rangeEdited.value=true;message.value='';controller.edit()},{deep:true,flush:'sync'})
const message=ref(''),clock=ref(Date.now()),visible=ref(true)
const angular=computed(()=>['nod','shake'].includes(form.shape))
const active=computed(()=>controller.owned()&&(['checking','planning','running','paused'].includes(s.snapshot?.phase)||['run','pause','resume'].includes(s.pending)))
const running=computed(()=>controller.owned()&&['running','paused'].includes(s.snapshot?.phase))
const phase=computed(()=>{void clock.value;return controller.owned()&&s.snapshot?.phase==='ready'&&controller.expired()?'请重新开始':!controller.owned()&&['checking','planning','ready'].includes(s.snapshot?.phase)?'待开始':({idle:'待开始',checking:'准备中',planning:'准备中',ready:'可执行',running:'正在执行',paused:'已暂停',completed:'已完成',cancelled:'已取消',failed:'未完成',expired:'请重新开始',invalid:'目标已失效'}[s.snapshot?.phase]||'待开始')})
const inputError=computed(()=>{try{drawingConfig(draftConfig.value,real.value?'real':'sim');return ''}catch(error){return error.message}})
const localDraft=computed(()=>{try{return real.value?null:localDrawingPreview(draftConfig.value)}catch{return null}})
const changingTool=ref(false)
const hidePlanPreview=ref(false)
let startVersion=0
const why=computed(()=>{void clock.value;return controller.runReason()})
const progress=computed(()=>s.snapshot?.progress)
const maximum=computed(()=>real.value?50:500)
watch(form,()=>{if(!applyingDefault)rangeEdited.value=true;message.value='';hidePlanPreview.value=false;controller.edit()},{deep:true,flush:'sync'})
watch(()=>JSON.stringify(form.center),()=>{s.baseAccepted=false},{flush:'sync'})
watch(real,value=>{
 const defaults=readInteractionRangeDefault(w,{storage:rangeStorage()})
 form.center=value?Array(6).fill(null):[...defaults.center,90,0,0]
 rangeSize.value=value?[50,50,50]:[...defaults.size];rangeDirection.value=270;form.width_mm=value?40:280;form.height_mm=value?30:240;form.speed_pct=value?5:100;form.angle_deg=5
 s.baseAccepted=false
},{flush:'sync'})
watch(()=>w.snap?.settings?.interaction_range_default,value=>{
 const range=interactionRange(value)
 if(!range||real.value||rangeEdited.value||active.value||s.pending)return
 applyingDefault=true
 try{form.center=[...range.center,...form.center.slice(3)];rangeSize.value=[...range.size];rangeDirection.value=270}finally{applyingDefault=false}
},{deep:true})
const toolFeedbackIssue=computed(()=>s.readFailed?'工具状态读取失败，请重试':drawingFeedbackIssue(w,s.snapshot))
const currentToolPose=computed(()=>toolFeedbackIssue.value?null:drawingToolPose(w.snap,s.snapshot?.trace_tcp))
function takeBase(){
 const pose=currentToolPose.value
 if(!pose){message.value=toolFeedbackIssue.value||'工具作用点尚未就绪';return}
 form.center=[...pose];controller.adoptBase();message.value='已采用当前工具作用点'
}
function adoptBase(){if(!inputError.value){controller.adoptBase();message.value='已采用填写的基准'}}
async function preview(){if(inputError.value){message.value=inputError.value;return false}message.value='';hidePlanPreview.value=false;if(!real.value){focusDrawing();return true}return controller.preview(draftConfig.value)}
async function toggleRun(){
 if(running.value||s.runQueued)return controller.cancel()
 if(real.value){const ok=await controller.act('run');if(ok)hidePlanPreview.value=true;return ok}
 if(changingTool.value)return false
 hidePlanPreview.value=true
 const token=++startVersion
 changingTool.value=true;message.value=''
 try{await ensureSimTaskPen(w,'drawing');if(token!==startVersion||!visible.value||real.value)return false;return await controller.requestRun(draftConfig.value)}
 catch(error){message.value=error.message;return false}
 finally{changingTool.value=false}
}
function togglePause(){return controller.act(s.snapshot?.phase==='paused'?'resume':'pause')}
function focusDrawing(){
 if(real.value||!w.connected||w.stateFresh===false||rangeError.value)return
 const bounds=controller.visualSnapshot()?.bounds||draftBounds.value
 const value=frontDrawingView(bounds,displayPlane.value,scene()?.framingInfo?.())
 if(value)scene()?.setView(value)
}
watchEffect(()=>{
 const snapshot=controller.visualSnapshot()||(localDraft.value?{...localDraft.value,plan_id:'draft:'+JSON.stringify(draftConfig.value),session_id:w.snap?.session_id,phase:'preview'}:null)
 if(!visible.value||!w.connected||w.stateFresh===false||w.snap?.mode!=='sim'||w.snap?.real){scene()?.clearDrawing(controller.owner);return}
 if(!snapshot)scene()?.clearDrawing(controller.owner)
 if(!snapshot&&['width_mm','height_mm'].some(key=>typeof form[key]!=='number'||!Number.isFinite(form[key])))return
 try{if(snapshot?.bounds||draftBounds.value)scene()?.setDrawingBounds?.({owner:controller.owner,session:w.snap?.session_id,mode:'sim',bounds:snapshot?.bounds||draftBounds.value,plane:displayPlane.value})}catch{if(!snapshot)scene()?.clearDrawing(controller.owner)}
 if(!snapshot)return
 const tcp=drawingToolPose(w.snap,snapshot.trace_tcp)
 const retained=['completed','cancelled','failed','stopped'].includes(snapshot.phase)
 const valid=snapshot.phase!=='preview'&&(controller.owned()||retained)&&!s.readFailed&&(snapshot.phase!=='ready'||!!s.plan&&!controller.expired())&&!!tcp
 const current=w.snap?.exec?.current
 const traceActive=current?.drawing_plan_id===snapshot.plan_id&&current.drawing_trace===true
 scene()?.setDrawingState({owner:controller.owner,plan_id:snapshot.plan_id,session:snapshot.session_id,shape:snapshot.config?.shape||form.shape,path:snapshot.trace_path||snapshot.path,actual_samples:snapshot.actual_samples?.length?snapshot.actual_samples:undefined,bounds:snapshot.bounds||snapshot.config?.bounds,mode:'sim',phase:valid?snapshot.phase:snapshot.phase==='cancelled'?'cancelled':'invalid',progress:snapshot.progress,tcp,valid,trace_active:traceActive,preview_visible:!hidePlanPreview.value})
})
let timer,framed=false
watch(()=>form.shape,()=>{if(visible.value&&!real.value)focusDrawing()},{flush:'post'})
watchEffect(()=>{if(visible.value&&!real.value&&scene()&&!framed){focusDrawing();framed=true}})
function start(){const entering=!visible.value;visible.value=true;controller.start();if(entering)focusDrawing();if(!timer)timer=setInterval(()=>{clock.value=Date.now();controller.refreshValidity()},250)}
function leave(){++startVersion;visible.value=false;scene()?.clearDrawing(controller.owner);clearInterval(timer);timer=null;controller.leave()}
onMounted(start);onActivated(start);onDeactivated(leave)
onBeforeUnmount(()=>{++startVersion;visible.value=false;scene()?.clearDrawing(controller.owner);clearInterval(timer);controller.dispose()})
</script>
<template>
 <section class="panel drawing-task">
  <div class="panel-hd"><span class="panel-title">绘制</span><span class="tag">{{real?'真机':'仿真'}}</span></div>
  <div class="panel-bd col">
   <div class="row drawing-config"><label>图案<select v-model="form.shape" class="select" :disabled="running"><option v-for="[id,label] in drawingShapes" :key="id" :value="id">{{label}}</option></select></label></div>
   <details class="range-settings" open><summary>绘制范围 / mm</summary><fieldset :disabled="active||!!s.pending" class="drawing-range">
    <div class="range-grid"><span>轴</span><span>中心坐标</span><span>范围尺寸</span>
     <template v-for="(axis,i) in ['X · 宽','Y · 深','Z · 高']" :key="axis"><span>{{axis}}</span>
      <div class="range-field"><input v-model.number="form.center[i]" type="range" :min="i===2?0:-2000" max="2000" step="1" :aria-label="axis+'中心滑块'" /><input v-model.number="form.center[i]" type="number" step="1" :aria-label="axis+'中心坐标'" /></div>
      <div class="range-field"><input v-model.number="rangeSize[i]" type="range" :min="5" :max="real?50:5000" step="1" :aria-label="axis+'尺寸滑块'" /><input v-model.number="rangeSize[i]" type="number" :min="5" :max="real?50:5000" step="1" :aria-label="axis+'范围尺寸'" /></div>
     </template>
    </div>
   </fieldset></details>
   <div v-if="real" class="row"><button type="button" class="btn btn--xs" :disabled="running||!currentToolPose" @click="takeBase">读取当前TCP</button><button type="button" class="btn btn--xs" :disabled="running||!!inputError" @click="adoptBase">采用基准</button></div>
   <div class="drawing-config">
    <label v-if="!angular">{{form.shape==='circle'?'直径':'宽度'}} / mm<input v-model.number="form.width_mm" class="input" type="number" min="1" :max="maximum" :disabled="running" /></label>
    <label v-if="!angular&&!['square','circle'].includes(form.shape)">高度 / mm<input v-model.number="form.height_mm" class="input" type="number" min="1" :max="maximum" :disabled="running" /></label>
    <label v-if="angular">幅度 / °<input v-model.number="form.angle_deg" class="input" type="number" :min="real?-5:-15" :max="real?5:15" :disabled="running" /></label>
   </div>
   <div class="row"><button class="btn grow" :disabled="changingTool||!!s.pending||active||s.stopping||!!inputError||!!controller.blocked()||(real&&!s.baseAccepted)" @click="preview">预览</button><button class="btn btn--primary grow" :disabled="changingTool||s.stopping||(!(running||s.runQueued)&&(!!s.pending||!!inputError||!!controller.blocked()||(real&&!!why)))" @click="toggleRun">{{running||s.runQueued?'停止':'执行'}}</button></div>
   <p class="tiny" role="status">{{s.error||message||(real&&(toolFeedbackIssue||inputError))||phase}}<template v-if="progress?.total"> · {{progress.completed??0}} / {{progress.total}}</template></p>
   <!-- 面板内进度条：以前只有底栏那条全局进度，绘制页里看不到本任务的推进，
        会以为卡住了。total 由后端按轨迹采样点上报。 -->
   <progress v-if="progress?.total" :value="Math.min(progress.total,progress.completed??0)" :max="progress.total"></progress>
   <label v-if="real&&s.snapshot?.phase==='ready'&&controller.owned()" class="row tiny"><input v-model="s.confirmedReal" type="checkbox" />确认按当前基准执行真机运动</label>
   <p v-if="s.snapshot?.why" class="note tiny" role="alert">{{displayNumericText(s.snapshot.why)}}</p>
  </div>
 </section>
</template>
<style scoped>
.drawing-range{border:0;padding:8px 0 0;min-width:0;margin:0}.range-settings summary{font-size:12px;cursor:pointer}.range-grid{display:grid;grid-template-columns:48px minmax(0,1fr) minmax(0,1fr);gap:7px;align-items:center;font-size:11px}.range-grid input{width:100%;min-width:0;box-sizing:border-box}.range-field{display:flex;flex-direction:column;gap:3px;min-width:0}.range-field input[type=range]{padding:0;height:18px;accent-color:var(--acc)}.drawing-range>.row{margin-top:8px;flex-wrap:wrap}.drawing-task{min-width:0}.drawing-task p{margin:0}.drawing-task .col{gap:9px}.drawing-config{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.drawing-base{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.drawing-task label{min-width:0;font-size:12px}.drawing-task label:not(.row){display:grid;gap:3px}.drawing-task .input,.drawing-task .select{width:100%;min-width:0}.drawing-task pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto}.drawing-task progress{width:100%}.drawing-actions .btn{min-width:74px}
</style>
