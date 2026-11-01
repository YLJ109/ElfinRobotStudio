<script setup>
import { displayNumericText } from '../numberDisplay.js'
import {computed,reactive,ref,watch,watchEffect,onMounted,onActivated,onBeforeUnmount,onDeactivated} from 'vue'
import {useWorld} from '../store'
import {createDrawingTask,drawingToolPose,drawingFeedbackIssue} from '../drawingTask.js'
import {writingText,writingFonts} from '../textContours.js'
import {textWritingView} from '../textWritingView.js'
import {writingDefaults,writingDefaultInk,writingGroundPaths,writingInk} from '../writingConfig.js'
import {ensureSimTaskPen} from '../simTaskPen.js'
const w=useWorld(),controller=createDrawingTask(w),s=controller.state
const real=computed(()=>w.snap?.mode==='real')
const form=reactive({...writingDefaults,...(real.value?{x:null,y:null,z:null,size:5}:{})}),inkStyle=ref(writingDefaultInk),message=ref(''),preparing=ref(false)
const sim=computed(()=>w.snap?.mode==='sim'&&!w.snap?.real)
const owned=computed(()=>controller.owned())
const active=computed(()=>owned.value&&['checking','planning','running','paused'].includes(s.snapshot?.phase))
const running=computed(()=>owned.value&&['running','paused'].includes(s.snapshot?.phase))
const toolFeedbackIssue=computed(()=>s.readFailed?'工具状态读取失败，请重试':drawingFeedbackIssue(w,s.snapshot))
const realWritingIssue=computed(()=>{
 if(!real.value)return ''
 if(w.snap?.tools?.tool?.type!=='pen')return '请安装并验证真实笔工具档案'
 const speed=Number(w.snap?.real_speed_pct??w.snap?.settings?.real_speed_pct)
 if(!Number.isFinite(speed)||speed<1||speed>5)return '真机书写速度需要1～5%，请调整顶部速度'
 return toolFeedbackIssue.value
})
const currentToolPose=computed(()=>toolFeedbackIssue.value?null:drawingToolPose(w.snap,s.snapshot?.trace_tcp))
function takeBase(){
 const pose=currentToolPose.value
 if(!pose){message.value=toolFeedbackIssue.value||'工具作用点尚未就绪';return}
 form.x=pose[0];form.y=pose[1];form.z=pose[2]-15
 controller.adoptBase();message.value='已采用当前笔尖TCP'
}
function adoptBase(){if(!inputError.value){controller.adoptBase();message.value='已采用填写的基准'}}
const draft=ref(null)
const hidePlanPreview=ref(false)
const count=computed(()=>Array.from(form.text).length)
const inputError=computed(()=>{try{writingText(form.text);if(![form.x,form.y,form.z,form.size].every(v=>typeof v==='number'&&Number.isFinite(v)))throw Error('请填写有效位置和字号');if(form.z<1||form.z>800)throw Error('书写高度需要1～800毫米');if(form.size<(real.value?2:20)||form.size>(real.value?40:180))throw Error(real.value?'字号需要2～40毫米':'字号需要20～180毫米');return ''}catch(error){return error.message}})
let visible=true,disposed=false,worker=null,workerReject=null,previousTool,toolSession='',clockTimer,lifecycle=0,framed=false,draftTimer,glyphVersion=0
function cancelGlyph(){worker?.terminate();worker=null;workerReject?.(Error('字形已更新'));workerReject=null}
function scheduleDraft(){clearTimeout(draftTimer);if(visible&&!disposed&&!active.value&&sim.value)draftTimer=setTimeout(()=>{void preview(false)},120)}
// 表单一变，正在生成的字形就作废了（glyphVersion 前进），preview() 的 finally
// 因为 token 不匹配不会复位 preparing —— 面板会永久停在"生成字形…"，
// 预览/执行按钮全部失效。这里显式解锁，让草稿重新排版。
watch(form,()=>{message.value='';hidePlanPreview.value=false;draft.value=null;++glyphVersion;cancelGlyph();preparing.value=false;controller.edit();scheduleDraft()},{deep:true,flush:'sync'})
watch(()=>[form.x,form.y,form.z],()=>{s.baseAccepted=false},{flush:'sync'})
function glyphs(settings){return new Promise((resolve,reject)=>{
  const instance=new Worker(new URL('../textWorker.js',import.meta.url),{type:'module'});worker=instance;workerReject=reject
  instance.onmessage=({data})=>{instance.terminate();if(worker===instance){worker=null;workerReject=null}data.ok?resolve(data):reject(Error(data.why))}
  instance.onerror=()=>{instance.terminate();if(worker===instance){worker=null;workerReject=null}reject(Error('字形生成失败，请重试'))}
  instance.postMessage({id:1,text:settings.text,fontStyle:settings.fontStyle})
})}
async function preview(execute=false){
  if(preparing.value||active.value||(!sim.value&&!real.value)||inputError.value)return
  if(real.value&&realWritingIssue.value){message.value=realWritingIssue.value;return false}
  if(real.value&&!s.baseAccepted){message.value='请先采用当前笔尖基准';return false}
  hidePlanPreview.value=execute
  clearTimeout(draftTimer);++glyphVersion;cancelGlyph()
  const token=glyphVersion,settings={...form}
  preparing.value=execute;message.value=''
  try{
    const glyph=await glyphs(settings);if(disposed||token!==glyphVersion)return
    const width=Math.max(5,glyph.em_height*form.size),height=Math.max(5,glyph.em_width*form.size)
    if(width>500||height>500||(real.value&&(width+24>50||height+24>50)))throw Error(real.value?'文字超出50毫米范围，请减小字号':'文字太宽，请减小字号')
    const fence=w.snap?.settings?.fence||w.snap?.fence||w.settings?.fence
    if(fence?.enabled!==false&&fence?.x&&fence?.y&&(form.x-width/2<fence.x[0]+20||form.x+width/2>fence.x[1]-20||form.y-height/2<fence.y[0]+20||form.y+height/2>fence.y[1]-20))throw Error('文字超出围栏，请调整位置或字号')
    const paths=writingGroundPaths(glyph.strokes,form)
    draft.value={plan_id:'draft:'+Date.now(),session_id:w.snap?.session_id,trace_paths:paths,path:paths.flat(),bounds:{min:[form.x-width/2-12,form.y-height/2-12,form.z-3],max:[form.x+width/2+12,form.y+height/2+12,form.z+83]},phase:'checking'}
    if(!execute&&!real.value)return true
    if(sim.value&&w.snap?.tools?.visual?.kind!=='pen'){
      previousTool=JSON.parse(JSON.stringify(w.snap?.tools?.visual??null));toolSession=w.snap?.session_id
      await ensureSimTaskPen(w,'writing')
    }
    if(disposed||!visible||token!==glyphVersion)return
    const payload={shape:'text',text:glyph.text,strokes:glyph.strokes,font_size_mm:form.size,writing_z_mm:form.z,
      center:[form.x,form.y,form.z+15,180,0,0],range_center_mm:[form.x,form.y,form.z+(real.value?15:40)],range_size_mm:[width+24,height+24,real.value?50:86],
      plane:'xy',direction:0,center_policy:'fixed',width_mm:width,height_mm:height,angle_deg:0,
      speed_pct:Number(w.snap?.[real.value?'real_speed_pct':'sim_speed_pct']??w.snap?.settings?.[real.value?'real_speed_pct':'sim_speed_pct']??(real.value?5:100)),repeats:1}
    return await (real.value?controller.preview(payload):(execute?controller.requestRun(payload):controller.preview(payload)))
  }catch(error){if(!disposed&&token===glyphVersion)message.value=error.message}
  finally{if(token===glyphVersion)preparing.value=false}
}
function execute(){
  hidePlanPreview.value=true
  if(running.value||s.runQueued||preparing.value){draft.value=null;return controller.cancel()}
  if(real.value)return controller.act('run').then(ok=>{if(!ok)hidePlanPreview.value=false;return ok})
  if(owned.value&&['checking','planning','ready'].includes(s.snapshot?.phase))return controller.requestRun()
  return preview(true)
}
const label=computed(()=>({checking:'正在准备笔画',planning:'正在准备笔画',ready:'可执行',running:'正在书写',paused:'已暂停',completed:'书写完成',cancelled:'已停止',failed:'书写未完成',expired:'请重新预览'}[s.snapshot?.phase]||''))
watchEffect(()=>{
  const scene=globalThis.window?.__elfinScene,snapshot=controller.visualSnapshot()||draft.value
  if(visible&&sim.value&&scene&&!framed){scene.setView(textWritingView());framed=true}
  if(!visible||!sim.value||!w.connected||w.stateFresh===false||!snapshot){scene?.clearDrawing(controller.owner);return}
  const tcp=drawingToolPose(w.snap,snapshot.trace_tcp)
  const retained=['completed','cancelled','failed','stopped'].includes(snapshot.phase)
  scene?.setDrawingState({owner:controller.owner,plan_id:snapshot.plan_id,session:snapshot.session_id,shape:'text',
    path:snapshot.trace_path||snapshot.path,trace_paths:snapshot.trace_paths,actual_samples:snapshot.actual_samples,ink_style:inkStyle.value,
    bounds:snapshot.bounds,plane:'xy',mode:'sim',phase:snapshot.phase,progress:snapshot.progress,tcp,
    valid:(owned.value||retained)&&!s.readFailed&&!!tcp,trace_active:snapshot.trace_active,preview_visible:!hidePlanPreview.value})
})
async function cleanup(final=false){
  if(disposed)return
  const token=++lifecycle
  visible=false;draft.value=null;clearTimeout(draftTimer);++glyphVersion;clearInterval(clockTimer);clockTimer=null;cancelGlyph();preparing.value=false
  globalThis.window?.__elfinScene?.clearDrawing(controller.owner)
  const stopped=controller.cancel()
  if(final){disposed=true;controller.dispose()}
  await stopped
  if(token!==lifecycle||visible)return
  if(!final)controller.leave()
  if(previousTool!==undefined&&toolSession===w.snap?.session_id&&sim.value&&w.snap?.tools?.visual?.kind==='pen'){
    try{
      const response=await fetch('/api/tools',{cache:'no-store'}),current=await response.json()
      if(!response.ok)throw Error('工具状态读取失败')
      if(current.visual?.kind==='pen'){
        const result=await w.call('/api/sim/tool',{tool:previousTool,expected_revision:current.mounted?.revision})
        if(!result.ok&&result.code!=='tool_changed')w.error=result.why||'工具恢复失败'
      }
    }catch(error){w.error=error.message}
  }
  previousTool=undefined
}
watch(real,value=>{form.x=value?null:writingDefaults.x;form.y=value?null:writingDefaults.y;form.z=value?null:writingDefaults.z;form.size=value?5:writingDefaults.size;draft.value=null;s.baseAccepted=false},{flush:'sync'})
function start(){if(disposed)return;const entering=!visible;visible=true;++lifecycle;controller.start();if(entering&&sim.value)resetWritingView();scheduleDraft();if(!clockTimer)clockTimer=setInterval(()=>controller.refreshValidity(),250)}
function resetWritingView(){if(!sim.value)return;globalThis.window?.__elfinScene?.setView(textWritingView())}
onMounted(start);onActivated(start)
onBeforeUnmount(()=>cleanup(true));onDeactivated(()=>cleanup(false))
</script>
<template>
 <section class="panel writing-task">
  <div class="panel-hd"><span class="panel-title">地面写字</span><span class="tag">{{real?'真机':'仿真'}}</span></div>
  <div class="panel-bd col">
   <fieldset :disabled="preparing||active||(!sim&&!real)">
    <label class="text-label">文字 <small>{{count}} / 5</small><input v-model="form.text" class="input" aria-label="书写文字" placeholder="中英文、数字、符号" autocomplete="off" /></label>
    <div class="writing-fields"><label>X / mm<input v-model.number="form.x" class="input" type="number" step="10" aria-label="书写中心 X" /></label><label>Y / mm<input v-model.number="form.y" class="input" type="number" step="10" aria-label="书写中心 Y" /></label></div>
    <label>Z / mm<input v-model.number="form.z" class="input" type="number" min="1" max="800" step="5" aria-label="书写高度 Z" /></label>
    <label>字号 / mm<input v-model.number="form.size" class="input" type="number" :min="real?2:20" :max="real?40:180" :step="real?1:5" aria-label="书写字号" /></label>
    <div class="writing-fields"><label>字体<select v-model="form.fontStyle" class="select" aria-label="书写字体"><option v-for="(font,key) in writingFonts" :key="key" :value="key">{{font.label}}</option></select></label><label>笔迹<select v-model="inkStyle" class="select" aria-label="笔迹风格"><option v-for="(ink,key) in writingInk" :key="key" :value="key">{{ink.label}}</option></select></label></div>
   </fieldset>
   <div v-if="real" class="row"><button class="btn btn--xs" :disabled="active||!currentToolPose" @click="takeBase">读取当前笔尖TCP</button><button class="btn btn--xs" :disabled="active||!!inputError" @click="adoptBase">采用基准</button></div>
   <div class="row"><button class="btn grow" :disabled="preparing||active||!!s.pending||!!inputError||(!sim&&!real)||!w.connected||(real&&(!s.baseAccepted||!!realWritingIssue))" @click="preview(false)">{{preparing?'生成字形…':'预览'}}</button><button class="btn btn--primary grow" :disabled="preparing||s.stopping||!!inputError||(!sim&&!real)||!w.connected||(real&&!running&&(!!controller.runReason()||!!realWritingIssue))" @click="execute">{{running||s.runQueued?'停止':'执行'}}</button><button v-if="running" class="btn" :disabled="!!s.pending" @click="controller.act(s.snapshot?.phase==='paused'?'resume':'pause')">{{s.snapshot?.phase==='paused'?'继续':'暂停'}}</button></div>
   <button class="btn btn--xs" :disabled="!sim" @click="resetWritingView">正面俯视</button>
   <label v-if="real&&owned&&s.snapshot?.phase==='ready'" class="row tiny"><input v-model="s.confirmedReal" type="checkbox" />确认按当前基准执行真机书写</label>
   <p v-if="inputError||message||s.error||label||(real&&toolFeedbackIssue)" class="tiny" role="status">{{s.error||message||(real&&realWritingIssue)||inputError||label}}<template v-if="s.snapshot?.progress?.total"> · {{s.snapshot.progress.completed}} / {{s.snapshot.progress.total}}</template></p>
   <!-- 面板内进度条：只有底栏那条全局进度时，写字页里看不出本任务推进到哪。 -->
   <progress v-if="s.snapshot?.progress?.total" :value="Math.min(s.snapshot.progress.total,s.snapshot.progress.completed??0)" :max="s.snapshot.progress.total"></progress>
   <p v-if="s.snapshot?.phase==='failed'&&s.snapshot.why" class="tiny" role="alert">{{displayNumericText(s.snapshot.why)}}</p>
  </div>
 </section>
</template>
<style scoped>
.writing-task fieldset{border:0;padding:0;margin:0;display:grid;gap:10px;min-width:0}.writing-task label{display:grid;gap:5px;font-size:12px;min-width:0}.writing-task .input{width:100%;min-width:0}.writing-fields{display:grid;grid-template-columns:1fr 1fr;gap:8px}.text-label{position:relative}.text-label small{position:absolute;right:0;top:0;color:var(--tx-3)}.writing-task p{margin:0;overflow-wrap:anywhere}.writing-task .col{gap:12px}
</style>
