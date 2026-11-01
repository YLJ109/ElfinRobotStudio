<script setup>
import {computed,ref,onMounted,onBeforeUnmount} from 'vue'
import {useWorld} from '../store'
import {scenePlay,playAllowed} from '../functionPlay.js'
import {createPracticalTask,assemblyPayload,assemblyRecipe,patternCells,taskKinds} from '../practicalTasks.js'
import {visionWorkspace} from '../visionWorkspace.js'
const props=defineProps({kind:{type:String,required:true}})
const w=useWorld(),assembly=computed(()=>['kit','pattern'].includes(props.kind))
const controller=createPracticalTask({prefix:assembly.value?'/api/assembly':'/api/vision/tasks',requireConfirmation:false,onLeaveError:message=>{w.error=message}})
const state=controller.state,snapshot=computed(()=>state.snapshot),task=computed(()=>assembly.value?snapshot.value:snapshot.value?.task)
const columns=ref(3),rows=ref(2),cells=ref(['red','green','blue',null,null,null]),symbol=ref('A'),color=ref('red')
const name=ref(''),selectedColor=ref('red'),shape=ref(''),expected=ref(3),roi=ref([0,0,1,1]),tolerance=ref(5),slotsText=ref('')
const now=ref(Date.now()),group=computed(()=>scenePlay(w.snap?.objects||[],'sorting').group)
const groupReady=computed(()=>!!group.value&&(!snapshot.value?.group||snapshot.value.group===group.value))
const active=computed(()=>['running','checking','planning'].includes(task.value?.phase)||!!snapshot.value?.planning)
const stoppable=computed(()=>active.value||['inspect','run'].includes(state.pending))
const currentMode=computed(()=>!task.value?.mode || task.value.mode===(props.kind==='count'?'inspection':props.kind))
const analyzing=ref(false),busy=computed(()=>analyzing.value||!!state.pending||active.value)
const expired=computed(()=>Number.isFinite(task.value?.result?.expires_at)&&now.value>=task.value.result.expires_at*1000)
const usable=computed(()=>!state.readFailed&&(assembly.value?playAllowed(w):snapshot.value?.available===true))
const labels={idle:'尚未开始',preview:'待开始',checking:'准备中',checked:'可执行',running:'正在执行',completed:'已完成',cancelled:'已取消',failed:'未完成'}
const phase=computed(()=>!snapshot.value?'等待读取':!task.value||!currentMode.value?'尚未操作':labels[task.value?.phase]||'尚未操作')
const result=computed(()=>expired.value||!currentMode.value?null:task.value?.result)
const pointer=computed(()=>{const value=result.value,image=visionWorkspace.result;if(state.readFailed||!value?.valid||!value.selected||!value.image||!visionWorkspace.connected||visionWorkspace.stale||image?.source_key!==value.frame?.source_key||snapshot.value?.context_id!==value.context_id)return null;return value.selected})
const pointerImage=computed(()=>pointer.value&&result.value?.image)
const inputError=ref('')
function resize(){if(!Number.isInteger(columns.value)||!Number.isInteger(rows.value)||columns.value<1||columns.value>5||rows.value<1||rows.value>5){inputError.value='行列数需要1～5';return}cells.value=Array.from({length:columns.value*rows.value},(_,i)=>cells.value[i]??null);invalidate()}
function glyph(){columns.value=3;rows.value=5;cells.value=patternCells(symbol.value,color.value);invalidate()}
function invalidate(){state.confirmed=false;if(task.value?.preview_id)controller.act('cancel',{preview_id:task.value.preview_id})}
function payload(){
  if(assembly.value)return {...assemblyPayload(props.kind,snapshot.value?.group||group.value,columns.value,rows.value,cells.value),perception:'scene'}
  const selector={...(name.value.trim()?{name:name.value.trim()}:{}),...(selectedColor.value?{color:selectedColor.value}:{}),...(shape.value?{shape:shape.value}:{})}
  if(!Object.keys(selector).length)throw Error('请选择目标名称、颜色或形状')
  if(roi.value.some(value=>!Number.isFinite(value))||roi.value[0]<0||roi.value[1]<0||roi.value[2]<=0||roi.value[3]<=0||roi.value[0]+roi.value[2]>1||roi.value[1]+roi.value[3]>1)throw Error('检查区域需在画面范围内')
  const base={mode:props.kind==='count'?'inspection':'find',context_id:snapshot.value?.context_id,roi:[...roi.value],selector}
  if(props.kind==='count'){
    if(!Number.isInteger(expected.value)||expected.value<0||expected.value>50)throw Error('预期数量需要0～50')
    base.expected_count=expected.value
    if(slotsText.value.trim()){
      base.slots=slotsText.value.trim().split('\n').map((line,index)=>{const xy=line.split(',').map(value=>Number(value.trim()));if(xy.length!==2||line.split(',').some(value=>!value.trim())||xy.some(value=>!Number.isFinite(value)))throw Error('每行格位请输入 X,Y 毫米坐标');return {id:String(index+1),xy_mm:xy}})
      if(!Number.isFinite(tolerance.value)||tolerance.value<.1||tolerance.value>100)throw Error('格位容差需要0.1～100毫米')
      base.tolerance_mm=tolerance.value
    }
  }
  return base
}
async function act(action){
  inputError.value=''
  try{
    if(action==='preview'){if(!usable.value)return false;if(assembly.value&&!groupReady.value&&!snapshot.value?.group)throw Error('物料场景正在同步，请重试');return await controller.act(action,payload())}
    const current=task.value
    if(['inspect','run'].includes(action)&&!currentMode.value)return
    if(action==='run'&&(!usable.value||expired.value || (assembly.value&&(current?.phase!=='checked'||!current?.plan_id))))return
    return await controller.act(action,action==='run'?(assembly.value?{preview_id:current?.preview_id,plan_id:current?.plan_id}:{plan_id:current?.plan_id}):{preview_id:current?.preview_id})
  }catch(error){inputError.value=error.message;return false}
}
async function setupMaterials(){
  if(busy.value||!usable.value)return
  inputError.value=''
  try{
    const recipe=assemblyRecipe(props.kind,columns.value,rows.value,cells.value)
    return await controller.act('setup',recipe)
  }catch(error){inputError.value=error.message}
}
async function analyze(){
  if(busy.value||!usable.value)return
  inputError.value='';analyzing.value=true
  try{
    if(!await controller.act('preview',payload()))return
    if(!await controller.act('inspect',{preview_id:task.value?.preview_id}))return
    await controller.act('run',{plan_id:task.value?.plan_id})
  }catch(error){inputError.value=error.message}
  finally{analyzing.value=false}
}
async function startAssembly(){
  if(stoppable.value)return act('cancel')
  if(busy.value||!usable.value)return false
  if(task.value?.phase==='checked'&&task.value.plan_id)return act('run')
  if(task.value?.phase==='preview'&&task.value.preview_id){if(await act('inspect')&&task.value?.phase==='checked')return act('run');return}
  if(!await setupMaterials())return
  if(!await act('preview'))return
  if(!await act('inspect'))return
  if(task.value?.phase==='checked')return act('run')
}
async function togglePause(){
  const action=w.snap?.exec?.state==='paused'?'resume':'pause'
  try{const response=await fetch('/api/motion/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(10000)});const value=await response.json();if(!response.ok||!value.ok)throw Error(value.why||value.detail||'操作失败');inputError.value=''}
  catch(error){inputError.value=error.message}
}
let timer
onMounted(()=>{controller.read();timer=setInterval(()=>{now.value=Date.now();controller.read()},750)})
onBeforeUnmount(()=>{clearInterval(timer);controller.leave()})
</script>
<template>
  <section class="panel practical-task" :data-task="kind">
    <div class="panel-hd"><span class="panel-title">{{taskKinds[kind].label}}</span><span class="tag">{{assembly?'仅仿真':'只读识别'}}</span></div>
    <div class="panel-bd col">
      <template v-if="assembly">
        <div v-if="kind==='pattern'" class="row"><select v-model="symbol" class="select" :disabled="busy"><option v-for="value in ['0','1','2','3','A','H']" :key="value">{{value}}</option></select><select v-model="color" class="select" :disabled="busy"><option value="red">红色</option><option value="green">绿色</option><option value="blue">蓝色</option></select><button class="btn" :disabled="busy" @click="glyph">填入图案</button></div>
        <div class="row"><label>列<input v-model.number="columns" type="number" min="1" max="5" :disabled="busy" @change="resize" /></label><label>行<input v-model.number="rows" type="number" min="1" max="5" :disabled="busy" @change="resize" /></label></div>
        <div class="recipe-grid" :style="{gridTemplateColumns:`repeat(${columns},minmax(0,1fr))`}"><select v-for="(_,index) in cells" :key="index" v-model="cells[index]" class="select" :aria-label="`格位${index+1}`" :disabled="busy" @change="invalidate"><option :value="null">留空</option><option value="red">红</option><option value="green">绿</option><option value="blue">蓝</option></select></div>
      </template>
      <template v-else>
        <p class="dim tiny">使用当前画面中的颜色与平面形状；未知类别或抓取条件不执行机器人动作。</p>
        <label>目标名称<input v-model="name" class="input" placeholder="已观测名称，可留空" :disabled="busy" @change="invalidate" /></label>
        <div class="row"><select v-model="selectedColor" class="select grow" aria-label="目标颜色" :disabled="busy" @change="invalidate"><option value="">任意颜色</option><option v-for="value in ['red','green','blue']" :key="value" :value="value">{{({red:'红色',green:'绿色',blue:'蓝色'})[value]}}</option></select><select v-model="shape" class="select grow" aria-label="目标形状" :disabled="busy" @change="invalidate"><option value="">任意形状</option><option value="circle">圆形</option><option value="rectangle">矩形</option><option value="triangle">三角形</option><option value="irregular">不规则</option></select></div>
        <details><summary>检查区域</summary><div class="roi-fields"><label v-for="(label,index) in ['左','上','宽','高']" :key="label">{{label}}<input v-model.number="roi[index]" type="number" min="0" max="1" step=".05" :disabled="busy" @change="invalidate" /></label></div></details>
        <template v-if="kind==='count'"><label>预期数量<input v-model.number="expected" type="number" min="0" max="50" :disabled="busy" @change="invalidate" /></label><details><summary>格位与错位检查</summary><label>每行 X,Y（毫米）<textarea v-model="slotsText" class="input" :disabled="busy" @change="invalidate" /></label><label>容差/mm<input v-model.number="tolerance" type="number" min=".1" max="100" :disabled="busy" @change="invalidate" /></label></details></template>
      </template>
      <p v-if="!usable" class="note">{{assembly?'请保持静止的仿真会话，并停止其它程序与跟随。':snapshot?.why || '等待有效的共享视觉来源。'}}</p>
      <p v-if="!currentMode" class="note">当前是另一任务的预览或执行结果；请取消或重新预览本页任务。</p>
      <div class="row"><template v-if="assembly"><button class="btn btn--primary grow" :disabled="state.pending==='cancel'||(!stoppable&&(busy||!usable))" @click="startAssembly">{{stoppable?'停止':'开始'}}</button><button class="btn" :disabled="task?.phase!=='running'" @click="togglePause">{{w.snap?.exec?.state==='paused'?'继续':'暂停'}}</button></template><button v-else class="btn btn--primary" :disabled="busy||!usable" @click="analyze">{{kind==='find'?'查找目标':'检查托盘'}}</button><button v-if="!assembly&&(task?.preview_id||busy)" class="btn btn--danger" :disabled="state.pending==='cancel'" @click="act('cancel')">停止</button></div>
      <p role="status">{{phase}}<template v-if="currentMode&&task?.total!=null"> · 已确认 {{task.completed||0}} / {{task.total}}</template></p>
      <p v-if="task?.planning||task?.phase==='checking'" class="dim tiny">{{task?.progress?.step||0}} / {{task?.progress?.total||'—'}}</p>
      <p v-if="expired" class="note">识别结果已过期，请重新预览。</p>
      <p v-if="inputError||state.error||(currentMode&&(task?.error||task?.message||snapshot?.message))" class="note" role="alert">{{inputError||state.error||task?.error||task?.message||snapshot?.message}}</p>
      <template v-if="result"><p v-if="result.count!=null">识别 {{result.count}} 件 · 缺少 {{result.missing_count||0}} · 多出 {{result.extra_count||0}}</p><div v-if="result.slots?.length" class="tscroll"><table><tbody><tr v-for="slot in result.slots" :key="slot.id"><td>{{slot.id}}</td><td>{{({ok:'符合',missing:'缺件',misaligned:'错位',ambiguous:'无法唯一判断'})[slot.status]||slot.status}}</td><td>{{slot.error_mm==null?'—':`${slot.error_mm.toFixed(1)} mm`}}</td></tr></tbody></table></div></template>
      <p v-if="result?.status" role="status">{{({found:'已找到唯一目标',ambiguous:'有多个匹配目标，请缩小范围',not_found:'未找到目标',passed:'检查符合预期',attention:'检查发现差异，请核对缺件、多件或错位'})[result.status]||'检查结果待核对'}}</p>
      <svg v-if="pointerImage" class="target-image" :viewBox="`0 0 ${pointerImage.width} ${pointerImage.height}`" role="img" :aria-label="`已识别目标：${pointer.name}`"><image :href="`data:${pointerImage.mime};base64,${pointerImage.base64}`" :width="pointerImage.width" :height="pointerImage.height"/><polygon :points="pointer.corners.map(point=>point.join(',')).join(' ')" fill="none" stroke="var(--accent,#2fd6f0)" stroke-width="3"/><circle :cx="pointer.center_normalized[0]*pointerImage.width" :cy="pointer.center_normalized[1]*pointerImage.height" r="7" fill="none" stroke="white" stroke-width="2"/></svg>
      <div v-if="currentMode&&task?.placements?.length" class="tscroll"><table><thead><tr><th>格位</th><th>物料</th><th>目标/mm</th></tr></thead><tbody><tr v-for="item in task.placements" :key="item.slot"><td>{{item.slot+1}}</td><td>{{({red:'红',green:'绿',blue:'蓝'})[item.color]}}</td><td>{{item.xyz.map(value=>value.toFixed(1)).join(', ')}}</td></tr></tbody></table></div>
      <details v-if="currentMode&&task?.program"><summary>任务程序</summary><pre>{{task.program}}</pre></details>
      <p v-if="!assembly" class="dim tiny">仅显示识别与检查结果；姿态、高度和抓取条件未经确认，取放不可用。</p>
      <p v-if="task?.held" class="note">当前仍有仿真持件，请在末端工具页停止吸气并释放后整理现场。</p>
    </div>
  </section>
</template>
<style scoped>
.practical-task{min-width:0}.target-image{width:100%;max-height:250px}.practical-task p{margin:0}.practical-task .col{gap:9px}.practical-task label{font-size:12px;display:grid;gap:4px}.practical-task input[type=number]{width:70px}.recipe-grid,.roi-fields{display:grid;gap:5px}.roi-fields{grid-template-columns:repeat(4,minmax(0,1fr))}.practical-task .select{min-width:0}.practical-task td{font-size:12px;overflow-wrap:anywhere}.practical-task textarea{min-height:70px}.practical-task .note{overflow-wrap:anywhere}
</style>
