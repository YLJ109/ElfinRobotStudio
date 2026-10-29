<script setup>
import { ref,computed,onMounted,onBeforeUnmount,onActivated,onDeactivated } from 'vue'
import { useWorld } from '../store'
import { robotPlayFor,tcpWaitPose,playPointTab } from '../robotPlayProgram.js'
import TaskTabs from './TaskTabs.vue'
const props=defineProps({group:String,action:String,slot:Number,blocked:Boolean})
const w=useWorld(),controller=robotPlayFor(w),s=controller.state
const pointTab=ref('wait'),clearance=ref(120),speed=ref(15),wait=ref(null),message=ref('')
const locked=computed(()=>props.blocked || controller.busy.value)
const tabs=[{id:'wait',label:'等待'},{id:'a_pick',label:'A取料'},{id:'a_up',label:'A上位'},{id:'b_place',label:'B放料'},{id:'b_up',label:'B上位'},{id:'other',label:'其他'}]
const points=computed(()=>(s.plan?.points || []).filter(point=>playPointTab(point.role)===pointTab.value))
let unsubscribe
function resume(){if(!unsubscribe)unsubscribe=controller.subscribe()}
function pause(){unsubscribe?.();unsubscribe=null}
onMounted(resume);onActivated(resume)
onDeactivated(pause);onBeforeUnmount(pause)
function takeWait(){try{wait.value=tcpWaitPose(w.snap?.tcp);message.value='已读取当前 TCP 等待位'}catch(error){message.value=error.message}}
async function releaseHeld(){
  if(!globalThis.confirm('停止吸气并释放仿真持件？工件将保留当前位置，之后需要手动复位或整理。'))return
  try{const r=await fetch('/api/play/program/release',{method:'POST',signal:AbortSignal.timeout(10000)});const value=await r.json();if(!r.ok || !value.ok)throw new Error(value.why || value.detail || '释放失败');message.value=value.note}catch(error){message.value=error.message}
}
function generate(){
  if(locked.value)return
  if(!props.group){message.value='请先布置任务物体';return}
  if(s.source && s.source!==s.plan?.source && !globalThis.confirm('重新生成将覆盖手工修改的程序，继续？'))return
  return controller.generate({group:props.group,action:props.action,...(props.action==='chess'?{slot:props.slot}:{}),clearance:Number(clearance.value),speed:Number(speed.value),...(wait.value?{wait:wait.value.slice()}:{})})
}
const matching=computed(()=>s.plan?.group===props.group && s.plan?.action===props.action && (props.action!=='chess' || s.plan?.slot===props.slot))
const editableContext=computed(()=>s.parameters?.group===props.group && s.parameters?.action===props.action && (props.action!=='chess' || s.parameters?.slot===props.slot))
const prechecking=computed(()=>controller.prechecking.value)
const phaseLabel=computed(()=>({queued:'排队中',starting:'准备预检',planning:'规划路径',validating:'校验程序',checking:'检查路径',ik:'校验可达性',complete:'预检完成',completed:'预检完成',cancelled:'预检已取消',cancelling:'正在取消预检'}[s.progress?.phase] || '预检处理中'))
const progressValue=computed(()=>s.progress?.total>0 && s.progress?.step!=null?Math.min(s.progress.total,s.progress.step):undefined)
const fmt=pose=>Array.isArray(pose)?pose.map(value=>Number.isFinite(value)?value.toFixed(1):'—').join(', '):'—'
</script>
<template>
  <section class="panel robot-play-program">
    <div class="panel-hd"><span class="panel-title">机器人取放程序</span><span class="tag">仅仿真</span></div>
    <div class="panel-bd col">
      <p class="dim tiny">机器人仿真取放，真机 IO 需现场配置，目前不开真机自动作业。</p>
      <div class="row"><label class="grow">上位高度/mm<input v-model.number="clearance" type="number" class="input" min="20" max="500" :disabled="locked" /></label><label class="grow">速度/%<input v-model.number="speed" type="number" class="input" min="1" max="100" :disabled="locked" /></label></div>
      <div class="row"><button class="btn btn--xs" :disabled="locked" @click="takeWait">取当前 TCP 为等待位</button><button class="btn btn--xs" :disabled="locked" @click="wait=null">自动等待位</button></div>
      <p class="dim tiny mono">{{ wait?fmt(wait):'自动等待位：400, 0, 450 / 180, 0, 0' }}</p>
      <div class="row"><button class="btn btn--primary" :disabled="locked || !group || (action==='chess' && !Number.isInteger(slot))" @click="generate">{{ prechecking?'预检中…':'预检生成' }}</button><button class="btn" :disabled="locked || !editableContext || !s.source.trim()" @click="controller.recheck">预检修改</button><button v-if="prechecking" class="btn" :disabled="s.cancelling || s.can_cancel===false" @click="controller.cancelWaiting">{{ s.cancelling?'正在提交取消…':s.cancelPending?'重试取消预检':'取消预检' }}</button></div>
      <div v-if="prechecking" class="precheck-progress col" role="status" aria-live="polite">
        <span>{{ s.cancelPending?'取消预检待确认':phaseLabel }}<template v-if="s.progress?.step!=null && s.progress?.total>0"> · 步骤 {{ s.progress.step }} / {{ s.progress.total }}</template><template v-if="s.progress?.line"> · 第 {{ s.progress.line }} 行</template><template v-if="s.progress?.kind"> · {{ s.progress.kind }}</template></span>
        <progress :value="progressValue" :max="s.progress?.total || 1" aria-label="预检进度"></progress>
        <span class="tiny dim">{{ s.elapsed_s==null?'等待服务端报告耗时':`已耗时 ${s.elapsed_s.toFixed(1)} 秒` }}<template v-if="s.timeout_s!=null"> · 预检时限 {{ s.timeout_s.toFixed(0) }} 秒</template></span>
        <span v-if="s.serverMessage" class="tiny">{{ s.serverMessage }}</span>
        <span class="tiny dim">取消预检仅结束规划；运动中的程序请使用“取消执行”。</span>
      </div>
      <TaskTabs v-model="pointTab" :tabs="tabs" id="robot-play-points" label="取放点位分类" />
      <div id="robot-play-points-panel" class="tscroll" role="tabpanel" :aria-labelledby="`robot-play-points-tab-${pointTab}`"><table><thead><tr><th>点名</th><th>XYZ / ABC</th></tr></thead><tbody><tr v-for="(point,index) in points" :key="point.name+index"><td>{{ point.name }}<small v-if="point.object_id" class="dim"> {{ point.object_id }}</small></td><td class="mono tiny">{{ fmt(point.pose) }}</td></tr><tr v-if="!points.length"><td colspan="2" class="tiny dim">此分类暂无点位，请预检生成。</td></tr></tbody></table></div>
      <label class="col">程序代码<textarea v-model="s.source" class="input mono" rows="12" spellcheck="false" :disabled="locked || (!s.plan && !s.source)" aria-label="机器人取放程序代码" /></label>
      <p v-if="s.source && s.source!==s.plan?.source" class="note">程序已修改，需点击“预检修改”获得新的有效计划，之后才能执行。</p>
      <div class="row"><button class="btn btn--primary" :disabled="locked || !matching || !s.source.trim() || s.source!==s.plan?.source" @click="controller.execute()">执行程序</button><button class="btn" :disabled="!controller.running.value || s.submitting || s.cancelled" @click="controller.togglePause">{{ (s.execution?.state || w.snap?.exec?.state)==='paused'?'继续':'暂停' }}</button><button class="btn btn--danger" @click="controller.stop">取消执行</button></div>
      <p v-if="s.serverPlanning || s.active || s.held" class="tiny dim">{{ s.serverPlanning?'服务端预检中 · ':'' }}{{ s.active?'程序运行中 · ':'' }}吸附：{{ s.held || '无' }} · 吸气 {{ s.vacuum?.vacuum?'开':'关' }} / 破真空 {{ s.vacuum?.blow?'开':'关' }}</p>
      <button v-if="s.held && !s.active" class="btn btn--danger" @click="releaseHeld">手动停吸并释放仿真持件</button>
      <p v-if="!matching && s.plan" class="note">当前任务或棋盘格已变化，请重新预检。</p>
      <p v-if="s.message" class="note" role="status">{{ s.message }}</p>
      <p v-if="message" class="note" role="status">{{ message }}</p>
    </div>
  </section>
</template>
<style scoped>
.robot-play-program input{width:100%;min-width:0}.robot-play-program label{min-width:0;font-size:11px}.robot-play-program textarea{width:100%;resize:vertical;min-height:160px;border-radius:0}.robot-play-program td{overflow-wrap:anywhere}.robot-play-program .task-tabs{flex-wrap:wrap}
.precheck-progress{gap:6px;font-size:12px;overflow-wrap:anywhere}.precheck-progress progress{width:100%;accent-color:var(--acc)}
</style>
