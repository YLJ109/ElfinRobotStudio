<script setup>
import { ref,computed,onMounted,onBeforeUnmount,onActivated,onDeactivated } from 'vue'
import { useWorld } from '../store'
import { robotPlayFor } from '../robotPlayProgram.js'
const props=defineProps({group:String,action:String,slot:Number,criterion:{type:String,default:"color"},perception:{type:String,default:'scene'},blocked:Boolean})
const w=useWorld(),controller=robotPlayFor(w),s=controller.state
const message=ref('')
const locked=computed(()=>props.blocked || controller.busy.value)
let unsubscribe
function resume(){if(!unsubscribe)unsubscribe=controller.subscribe()}
function pause(){unsubscribe?.();unsubscribe=null}
onMounted(resume);onActivated(resume)
onDeactivated(pause);onBeforeUnmount(pause)
async function releaseHeld(){
  try{const r=await fetch('/api/play/program/release',{method:'POST',signal:AbortSignal.timeout(10000)});const value=await r.json();if(!r.ok || !value.ok)throw new Error(value.why || value.detail || '释放失败');message.value=value.note}catch(error){message.value=error.message}
}
const topSpeed=computed(()=>Math.max(1,Math.min(100,Number(w.snap?.sim_speed_pct??w.snap?.settings?.sim_speed_pct??w.snap?.speed_pct??100))))
const active=computed(()=>controller.running.value || controller.prechecking.value || s.submitting)
async function toggleRun(){
  if(controller.running.value)return controller.stop()
  if(controller.prechecking.value)return controller.cancelWaiting()
  if(locked.value)return false
  const group=props.group
  if(!group)return false
  const ok=await controller.generate({group,action:props.action,criterion:props.criterion,perception:props.perception,clearance:120,speed:topSpeed.value})
  if(ok)return controller.execute(()=>true)
}
const prechecking=computed(()=>controller.prechecking.value)
const phaseLabel=computed(()=>({queued:'排队中',starting:'准备中',planning:'规划路径',validating:'校验程序',checking:'检查路径',ik:'校验可达性',complete:'准备完成',completed:'准备完成',cancelled:'已取消',cancelling:'正在取消'}[s.progress?.phase] || '准备中'))
</script>
<template>
  <section class="panel robot-play-program">
    <div class="panel-hd"><span class="panel-title">分拣</span></div>
    <div class="panel-bd col">
      <div class="row"><slot name="placement" /><button class="btn btn--primary grow" :disabled="!active && (locked || !group)" @click="toggleRun">{{active?'停止':'开始'}}</button><button class="btn" :disabled="!controller.running.value || s.submitting || s.cancelled" @click="controller.togglePause">{{ (s.execution?.state || w.snap?.exec?.state)==='paused'?'继续':'暂停' }}</button></div>
      <div v-if="prechecking" class="col precheck-progress">
        <p class="tiny dim" role="status">{{phaseLabel}}<template v-if="s.progress?.total"> · {{s.progress.step}} / {{s.progress.total}}</template></p>
        <progress v-if="s.progress?.total" :value="Math.max(0, Math.min(s.progress.total, s.progress.step || 0))" :max="s.progress.total"></progress>
      </div>
      <p v-if="s.held" class="tiny" role="status">吸附：{{s.held}}</p>
      <button v-if="s.held && !s.active" class="btn btn--xs" @click="releaseHeld">释放工件</button>
      <div v-if="action==='sorting' && s.sorting_score?.group===group" class="note" role="status" aria-live="polite">
        {{ ({running:'分拣中',completed:'分拣完成',cancelled:'已取消',failed:'未全部完成'})[s.sorting_score.status] || '分拣结果' }} · 已放置 {{ s.sorting_score.completed }} / {{ s.sorting_score.total }} · 到位正确 {{ s.sorting_score.correct }}
        <br />到位正确率 {{ s.sorting_score.accuracy_pct==null?'未产生放料结果':s.sorting_score.accuracy_pct+'%' }} · 实际用时 {{ s.sorting_score.elapsed_s.toFixed(1) }} 秒 · {{ s.sorting_score.score }} 分
        <p v-if="s.sorting_score.reason" class="tiny">{{ s.sorting_score.reason }}</p>
      </div>
      <p v-if="s.message && (!prechecking || s.cancelPending || s.planningUncertain)" class="note" role="status">{{ s.message }}</p>
      <p v-if="message" class="note" role="status">{{message}}</p>
    </div>
  </section>
</template>
<style scoped>
.robot-play-program input{width:100%;min-width:0}.robot-play-program label{min-width:0;font-size:11px}.robot-play-program textarea{width:100%;resize:vertical;min-height:160px;border-radius:0}.robot-play-program td{overflow-wrap:anywhere}.robot-play-program .task-tabs{flex-wrap:wrap}
.precheck-progress{gap:6px;font-size:12px;overflow-wrap:anywhere}.precheck-progress progress{width:100%;accent-color:var(--acc)}
</style>
