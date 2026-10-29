<script setup>
import { ref, computed, defineAsyncComponent, watch, onActivated, onDeactivated, onBeforeUnmount } from 'vue'
import { useWorld } from '../store'
import TaskTabs from '../components/TaskTabs.vue'
import RobotPlayProgram from '../components/RobotPlayProgram.vue'
import { robotPlayFor } from '../robotPlayProgram.js'
import { sortingPlan, scenePlay, playAllowed, playFor, sortingCounts } from '../functionPlay.js'
import { createChessMatch } from '../chessMatch.js'
import { visionWorkspace } from '../visionWorkspace.js'
const HandFollowPanel = defineAsyncComponent(() => import('./HandFollowPanel.vue'))
const props = defineProps({visualModel:String,direction:Number,fence:Object})
const emit = defineEmits(['direction','box-preview','interaction-visible'])
const w = useWorld(), tab = ref('interaction')
const panelActive=ref(true)
watch([tab,panelActive],([current,active])=>emit('interaction-visible',active && current==='interaction'),{immediate:true,flush:'sync'})
onActivated(()=>{panelActive.value=true})
const tabs = [{id:'interaction',label:'交互'},{id:'chess',label:'井字棋'},{id:'sorting',label:'分拣'}]
const objects=computed(()=>w.snap?.objects || [])
const chess=computed(()=>scenePlay(objects.value,'chess'))
const sorting=computed(()=>scenePlay(objects.value,'sorting'))
const task=playFor(w),taskState=task.state
const robotProgram=robotPlayFor(w)
const counts=ref({red:3,green:3,blue:3})
const countError=computed(()=>{try{sortingCounts(counts.value);return ''}catch(error){return error.message}})
const total=computed(()=>Object.values(counts.value).reduce((sum,value)=>sum+(Number(value)||0),0))
const sortingVisionGuide=computed(()=>{
  const v=visionWorkspace
  if(!v.connected || !v.config)return '尚未获取有效的共享视觉状态；请到视觉页检查 A 区画面、标定和识别结果。'
  if(!v.config.enabled)return '视觉识别尚未启用；请到视觉页手动配置并采集 A 区，完成标定和识别。'
  if(v.config.input!=='camera')return '当前不是实际相机输入；真实相机分拣需到视觉页选择相机、采集 A 区并完成标定和识别。'
  if(!v.calibration)return '尚未完成标定；请先采集 A 区画面，完成平面标定与工件识别，再预检分拣。'
  if(v.stale)return '视觉结果已过期；请到视觉页重新采集 A 区，检查标定并更新识别结果。'
  if(!v.result || v.result.source!=='camera' || !v.result.calibrated)return '尚无有效的相机标定识别结果；请到视觉页采集 A 区并检查标定与识别。'
  return '已有相机标定识别结果；请核对 A 区工件覆盖、颜色和坐标，再预检取放路径。视觉结果不代表运动预检已通过。'
})
function openVision(){w.view='vision'}
function resetGame() {
  if(busy.value || (!!chess.value.group && !allowed.value))return
  match.cancel('棋局已结束，请等待棋子回 A 区后重新启动仿真自动取放。')
  if(match.busy())return
  robotProgram.invalidate()
  if(chess.value.group)return task.run('reset',{group:chess.value.group})
}
async function setupTask(mode) {
  if(busy.value || !allowed.value)return
  if(mode==='sorting' && countError.value)return
  const different=objects.value.some(object=>object.task?.group && object.task.mode!==mode)
  if(different && !globalThis.confirm('切换模板将替换现有任务物体（包括 A/B 区域），保留普通场景物体。继续？'))return
  match.cancel('任务重新布置，本局结束；场景就绪后请重新启动。')
  if(match.busy())return
  robotProgram.invalidate()
  return task.run('setup',{mode,...(mode==='sorting'?{counts:counts.value}:{})})
}
function play(index) { return match.play(index) }
const criterion=ref('color'),selected=ref([]),sortingBusy=ref(false),message=ref('')
const externalBusy=computed(()=>sortingBusy.value || taskState.busy || robotProgram.busy.value)
const match=createChessMatch(w,{blocked:()=>externalBusy.value})
const matchState=match.state
const board=computed(()=>matchState.board)
const busy=computed(()=>externalBusy.value || match.busy())
watch(()=>[w.connected,w.snap?.session_id,w.snap?.mode,!!w.snap?.real,!!w.snap?.hand_follow?.active,chess.value.group,w.snap?.exec?.execution?.epoch],()=>match.observe(),{flush:'sync'})
watch(tab,(_value,old)=>{if(old==='chess')match.cancel('已离开井字棋，本局取消；请检查三维场景，不自动续局。')},{flush:'sync'})
function startGame(){
  if(busy.value || !allowed.value || !chess.value.group)return
  if(!globalThis.confirm('启动仿真自动取放棋局？随机分配你和 AI 的 O/X，O 固定先行。点击空格后，机器人将自动规划并执行双方的单枚取放，无需逐回合确认；仅限仿真。'))return
  robotProgram.invalidate()
  return match.start()
}
const ordinary=computed(()=>objects.value.filter(object=>!object.task))
const chosen=computed(()=>ordinary.value.filter(object=>selected.value.includes(object.id)))
const sortPreview=computed(()=>{try{return {steps:sortingPlan(chosen.value,criterion.value),error:''}}catch(error){return {steps:[],error:error.message}}})
const plan=computed(()=>sortPreview.value.steps)
const allowed=computed(()=>playAllowed(w))
function sortTask() {
  if(busy.value || !allowed.value || !sorting.value.group || !sorting.value.pieces.length)return
  return robotProgram.generate({group:sorting.value.group,action:'sorting',clearance:120,speed:15})
}
function resetSorting(){if(busy.value || !allowed.value || !sorting.value.group)return;robotProgram.invalidate();return task.run('reset',{group:sorting.value.group})}
let cancelled=false
watch(tab,(_value,old)=>{if(old==='sorting')cancelled=true})
async function sort() {
  if(busy.value || !allowed.value || !chosen.value.length)return
  let steps
  try{steps=sortingPlan(chosen.value,criterion.value)}catch(error){message.value=error.message;return}
  sortingBusy.value=true;cancelled=false;message.value=''
  const session=w.snap.session_id
  let done=0
  try{
    for(const step of steps){
      if(cancelled || !allowed.value || w.snap.session_id!==session)break
      const response=await w.patchObject(step.id,{xyz:step.xyz,gravity:false,floating:true})
      if(!response?.ok)throw new Error(response?.why || '分拣保存失败')
      done++
    }
    message.value=`已整理场景 ${done} / ${steps.length} 个物体${cancelled?'，已取消后续步骤':''}`
  }catch(error){message.value=`已完成 ${done} 个：${error.message}`}
  finally{sortingBusy.value=false}
}
function deactivate(){panelActive.value=false;cancelled=true;match.cancel('功能页已离开，本局取消；请检查三维场景后重新布置。')}
onDeactivated(deactivate);onBeforeUnmount(deactivate)
</script>
<template>
  <div class="col functions-panel">
    <TaskTabs v-model="tab" :tabs="tabs" id="functions" label="功能分类" />
    <div v-if="taskState.busy || taskState.message" class="note" role="status" aria-live="polite">
      {{ taskState.busy?'共享场景任务处理中，切页后仍会保留进度…':taskState.message }}
      <button v-if="taskState.busy" class="btn btn--xs" @click="task.cancel()">停止等待</button>
    </div>
    <div id="functions-panel" class="col" role="tabpanel" :aria-labelledby="`functions-tab-${tab}`">
      <KeepAlive><HandFollowPanel v-if="tab==='interaction'" v-bind="props" @direction="emit('direction',$event)" @box-preview="emit('box-preview',$event)" /></KeepAlive>
      <section v-if="tab==='chess'" class="panel">
        <div class="panel-hd"><span class="panel-title">井字棋</span><button class="btn btn--xs" :disabled="busy || (!!chess.group && !allowed)" @click="resetGame">{{ chess.group?'棋子回 A 原料区':'重新开始' }}</button></div>
        <div class="panel-bd">
          <button class="btn btn--primary" :disabled="busy || !allowed" @click="setupTask('chess')">布置棋局（5圈4叉）</button>
          <p class="dim tiny">A 原料区放置 5 个 O、4 个 X，B 放料区为三维棋盘。布置会替换现有任务物体，保留普通场景物体。</p>
          <p class="dim tiny">随机决定你或 AI 执 O，O 固定先行。机器人为双方逐枚取放；执行完成且三维槽位更新后才记录棋步，AI 完成后才轮到你。</p>
          <p v-if="!chess.group" class="note">请先布置三维棋局；此棋盘仅用于机器人仿真对局。</p>
          <div class="row"><button class="btn btn--primary" :disabled="busy || !allowed || !chess.group || !!chess.error || matchState.phase==='human'" @click="startGame">启动一局 · 仿真自动取放</button><button v-if="match.busy() || ['human','error'].includes(matchState.phase)" class="btn btn--danger" @click="match.cancel()">取消本局及取放</button></div>
          <p v-if="matchState.human" class="dim tiny">你执 {{ matchState.human }} · AI 执 {{ matchState.ai }} · O 先行</p>
          <p v-if="chess.group" class="dim tiny">机器人仿真取放，真机 IO 需现场配置，目前不开真机自动作业。</p>
          <p v-if="chess.group" class="dim tiny">A 区剩余 {{ chess.atA }} 枚 · B 区 {{ chess.atB }} 枚；格位与三维场景同步。</p>
          <p v-if="!allowed" class="note">布置和场景落子需要静止的仿真会话，并停止程序与交互跟随。</p>
          <p v-if="chess.error" class="note" role="alert">{{ chess.error }}</p>
          <div class="chess-board" role="group" aria-label="井字棋棋盘">
            <button v-for="(cell,index) in board" :key="index" class="cell" :class="{computer:cell===matchState.ai}" :aria-label="`第${Math.floor(index/3)+1}行第${index%3+1}列：${cell || '空'}`" :disabled="busy || !match.canPlay(index)" @click="play(index)">{{ cell }}</button>
          </div>
          <p role="status" aria-live="polite">{{ matchState.message }}</p>
          <p v-if="matchState.recoveryRequired" class="note">上次规划或执行结果尚未确认，已锁定新操作；不会自动重试或续局。<button class="btn" :disabled="matchState.stopping || matchState.verifying" @click="match.verifyIdle()">{{ matchState.verifying?'正在确认状态…':'检查仿真静止状态并解除锁定' }}</button></p>
          <p v-if="['cancelled','error'].includes(matchState.phase)" class="note">棋盘保留已确认棋步；取消或失败时，三维场景可能已有部分动作，请检查并回 A 区或重新布置。</p>
        </div>
      </section>
      <section v-if="tab==='sorting'" class="panel">
        <div class="panel-hd"><span class="panel-title">机器人分拣</span><span class="tag">仅仿真</span></div>
        <div class="panel-bd">
          <p class="dim tiny">A 原料区 → B 放料区；生成机器人取放程序，预检后人工确认执行。机器人仿真取放，真机 IO 需现场配置，目前不开真机自动作业。</p>
          <div class="piece-counts"><label v-for="(label,color) in {red:'红色',green:'绿色',blue:'蓝色'}" :key="color">{{ label }}<input v-model.number="counts[color]" class="input" type="number" min="0" max="9" step="1" :disabled="busy" /></label></div>
          <p class="dim tiny">默认红、绿、蓝各 3 个，72mm 大工件在 A 区随机乱序。当前合计 {{ total }} / 27 个（至少 1 个）；布置会替换现有任务物体和 A/B 区域，保留普通场景物体。</p>
          <p v-if="countError" class="note" role="alert">{{ countError }}</p>
          <button class="btn" :disabled="busy || !allowed || !!countError" @click="setupTask('sorting')">布置分拣料</button>
          <p class="dim tiny">机器人分拣依据相机识别的工件颜色与标定坐标，不以模板颜色代替识别。请先采集 A 区、完成标定与识别，再生成并预检取放程序。</p>
          <p class="note" role="status">{{ sortingVisionGuide }}</p>
          <button class="btn" @click="openVision">前往视觉页 · 采集 A 区 / 标定 / 识别</button>
          <p class="dim tiny">此入口仅切换页面，不会自动扫描或开关相机。下方自选普通物体可按形状或颜色整理。</p>
          <p v-if="sorting.group" class="dim tiny">任务工件 {{ sorting.pieces.length }} 个 · A 区 {{ sorting.atA }} 个 · B 区 {{ sorting.atB }} 个</p>
          <div class="row"><button class="btn btn--primary" :disabled="busy || !allowed || !sorting.group || !sorting.pieces.length" @click="sortTask">预检分拣程序（{{ sorting.pieces.length }}）</button><button class="btn" :disabled="busy || !allowed || !sorting.group" @click="resetSorting">重置物料回 A 区</button></div>
          <p v-if="!allowed" class="note">需要静止的仿真会话，并停止程序与交互跟随。</p>
          <details><summary>整理场景（普通物体，不驱动机器人）</summary><p class="dim tiny">只整理普通场景物体位置，不是机器人分拣；不包含任务棋子和 A/B 区域。</p>
            <label class="row">整理规则<select v-model="criterion" class="select grow" :disabled="busy"><option value="kind">形状</option><option value="color">颜色</option></select></label>
            <div class="sort-objects"><label v-for="object in ordinary" :key="object.id" class="sw"><input v-model="selected" type="checkbox" :value="object.id" :disabled="busy" />{{ object.name }}</label></div>
            <p v-if="!ordinary.length" class="dim tiny">先到「场景」添加小工件。</p>
            <p v-if="sortPreview.error" class="note" role="alert">{{ sortPreview.error }}</p>
            <div class="row"><button class="btn" :disabled="busy || !allowed || !plan.length" @click="sort">整理场景（{{ chosen.length }}）</button><button v-if="sortingBusy" class="btn" @click="cancelled=true">取消后续整理</button></div>
          </details>
          <p v-if="message" role="status">{{ message }}</p>
        </div>
      </section>
      <RobotPlayProgram v-if="tab==='sorting' && sorting.group" :group="sorting.group" action="sorting" :blocked="taskState.busy || sortingBusy" />
    </div>
  </div>
</template>
<style scoped>
.chess-board{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line-2);border:1px solid var(--line-2)}
.cell{aspect-ratio:1;border:0;border-radius:0;background:var(--bg-inset);color:var(--acc);font:600 34px var(--font-mono,monospace);cursor:pointer}
.cell.computer{color:var(--warn,#e0a33b)}.cell:disabled{cursor:default;opacity:1}.cell:focus-visible{outline:2px solid var(--acc);outline-offset:-3px}
.sort-objects{display:grid;gap:8px;max-height:260px;overflow:auto}.functions-panel{min-width:0}
.piece-counts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.piece-counts label{display:grid;gap:4px;font-size:12px}.piece-counts input{width:100%;min-width:0}
</style>
