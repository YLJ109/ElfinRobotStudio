<script setup>
import { ref, computed, defineAsyncComponent, watch, onActivated, onDeactivated, onBeforeUnmount } from 'vue'
import { useWorld } from '../store'
import TaskTabs from '../components/TaskTabs.vue'
import RobotPlayProgram from '../components/RobotPlayProgram.vue'
import HandlingChallenge from '../components/HandlingChallenge.vue'
import PracticalTask from '../components/PracticalTask.vue'
import { robotPlayFor } from '../robotPlayProgram.js'
import { scenePlay, playAllowed, playFor, sortingCounts } from '../functionPlay.js'
import { createChessMatch } from '../chessMatch.js'
import { visionWorkspace, ensureVirtualVision } from '../visionWorkspace.js'
const HandFollowPanel = defineAsyncComponent(() => import('./HandFollowPanel.vue'))
const TrackPanel = defineAsyncComponent(() => import('../components/TrackPanel.vue'))
const DrawingTask = defineAsyncComponent(() => import('../components/DrawingTask.vue'))
const TextWritingTask = defineAsyncComponent(() => import('../components/TextWritingTask.vue'))
const props = defineProps({visualModel:String,direction:Number,fence:Object,category:{type:String,default:'fun'}})
const emit = defineEmits(['direction','box-preview','interaction-visible'])
const w = useWorld(), tab = ref(props.category==='sorting'?'sorting':'interaction')
const panelActive=ref(true)
watch([tab,panelActive],([current,active])=>emit('interaction-visible',active && current==='interaction'),{immediate:true,flush:'sync'})
onActivated(()=>{panelActive.value=true})
const allTabs = [{id:'interaction',label:'跟随'},{id:'track',label:'追踪'},{id:'chess',label:'井字棋'},{id:'drawing',label:'绘制'},{id:'writing',label:'写字'},{id:'sorting',label:'分拣'},{id:'handling',label:'搬运闯关'},{id:'kit',label:'配套装盒'},{id:'pattern',label:'图案拼摆'}]
const funTabs = ['interaction','track','chess','drawing','writing']
const tabs = computed(()=>allTabs.filter(item=>props.category==='sorting'?!funTabs.includes(item.id):funTabs.includes(item.id)))
// 页内任务都只在仿真执行；真机模式要说明原因，不能只留一个禁用按钮。
const realMode=computed(()=>w.snap?.mode==='real')
watch(()=>props.category,()=>{if(!tabs.value.some(item=>item.id===tab.value))tab.value=tabs.value[0].id},{flush:'sync'})
// 跟随、井字棋、分拣都要"看得见"：进入这些页时自动把仿真摄像头打开，省掉
// 先去视觉面板手动开启这一步。开启动作本身收口在视觉模块里（幂等、不抢真实
// 相机），功能页不自己管采集。
// 绘制和写字不在此列：它们只算轨迹，不看画面；摄像头的采集与推流会跟路径
// 预检抢事件循环，开着反而让进度卡在 0。
const needsCamera = computed(() => ['interaction','chess','sorting'].includes(tab.value))
function ensureSimCamera(){
  if(!panelActive.value || realMode.value || !needsCamera.value)return
  if(visionWorkspace.config?.enabled && visionWorkspace.config?.input)return
  void ensureVirtualVision()
}
watch([tab,panelActive,realMode],ensureSimCamera,{immediate:true,flush:'post'})
const objects=computed(()=>w.snap?.objects || [])
const chess=computed(()=>scenePlay(objects.value,'chess'))
const sorting=computed(()=>scenePlay(objects.value,'sorting'))
const task=playFor(w),taskState=task.state
const robotProgram=robotPlayFor(w)
const counts=ref({red:3,green:3,blue:3}),sortingCriterion=ref('color'),shapeCounts=ref({rectangle:3,circle:3})
const sortingPerception=ref('scene')
const countError=computed(()=>{try{if(sortingCriterion.value==='shape'){const values=Object.values(shapeCounts.value);if(values.some(v=>!Number.isInteger(v) || v<0 || v>9) || values.reduce((a,b)=>a+b,0)<1)throw Error('矩形和圆形分别需要0～9个，总数至少1个')}else sortingCounts(counts.value);return ''}catch(error){return error.message}})
const total=computed(()=>Object.values(sortingCriterion.value==='shape'?shapeCounts.value:counts.value).reduce((sum,value)=>sum+(Number(value)||0),0))
function resetGame() {
  if(busy.value || (!!chess.value.group && !allowed.value))return
  match.cancel('棋局已结束。棋子回到 A 区后可重新开始。')
  if(match.busy())return
  robotProgram.invalidate()
  if(chess.value.group)return task.run('reset',{group:chess.value.group})
}
async function setupTask(mode) {
  if(busy.value || !allowed.value)return
  if(mode==='sorting' && countError.value)return
  match.cancel('重新布置，本局结束；场景就绪后重新开始。')
  if(match.busy())return
  robotProgram.invalidate()
  return task.run('setup',{mode,...(mode==='sorting'?{criterion:sortingCriterion.value,...(sortingCriterion.value==='shape'?{shape_counts:shapeCounts.value}:{counts:counts.value})}:{})})
}
function play(index) {
  // SIM03：点了棋盘格就等于开始，无论后面是换吸盘还是预检都要马上有真实状态。
  if (match.canPlay(index)) w.markMotionStart?.('chess')
  return match.play(index)
}
const externalBusy=computed(()=>taskState.busy || robotProgram.busy.value)
const match=createChessMatch(w,{blocked:()=>externalBusy.value})
const matchState=match.state
const board=computed(()=>{const value=matchState.board.slice(),pending=matchState.pending;if(pending&&value[pending.slot]==null)value[pending.slot]=pending.symbol;return value})
const changingChessTool=ref(false)
const busy=computed(()=>externalBusy.value || match.busy() || changingChessTool.value)
const chessActive=computed(()=>['checking','planning','submitting','moving','human'].includes(matchState.phase))
watch(()=>[w.connected,w.snap?.session_id,w.snap?.mode,!!w.snap?.real,!!w.snap?.hand_follow?.active,chess.value.group,w.snap?.exec?.execution?.epoch],()=>match.observe(),{flush:'sync'})
watch(tab,(value,old)=>{
  if(old==='chess')match.cancel('棋局已停止')
  if(value==='chess'&&!chess.value.group&&!busy.value&&allowed.value)void setupTask('chess')
},{flush:'sync'})
const starting=ref(false)
// 排程等待期间显示真实进度（后端按"第几步/共几步"上报），避免只看到一句"正在排程…"。
const preflightPct=computed(()=>{
  const play=w.snap?.robot_play,p=play?.progress
  if(!play?.planning||!Number.isInteger(p?.step)||!Number.isInteger(p?.total)||p.total<=0)return null
  return Math.round(Math.max(0,Math.min(p.total,p.step))/p.total*100)
})
const startLatency=computed(()=>w.startLatency?.label==='chess'?w.startLatency:null)
async function startGame(){
  if(match.busy()||['human','planning','moving','submitting','checking'].includes(matchState.phase)){match.cancel('棋局已停止');return}
  if(busy.value || !allowed.value || !chess.value.group)return
  w.markMotionStart?.('chess')
  starting.value=true
  try{
    if(w.snap?.tools?.visual?.kind!=='vacuum-reference'){
      changingChessTool.value=true
      const currentTab=tab.value,currentGroup=chess.value.group
      try{
        const changed=await w.call('/api/sim/task-tool',{task:'chess'})
        if(!changed?.ok){matchState.message=changed?.why||'双吸盘切换失败';return}
        if(!panelActive.value || tab.value!==currentTab || chess.value.group!==currentGroup)return
      }catch(error){matchState.message=error.message;return}
      finally{changingChessTool.value=false}
    }
    robotProgram.invalidate()
    if(['finished','cancelled','error'].includes(matchState.phase)&&!matchState.recoveryRequired){const reset=await resetGame();if(!reset)return}
    return match.start()
  }finally{starting.value=false}
}
const allowed=computed(()=>playAllowed(w))
function deactivate(){panelActive.value=false;match.cancel('功能页已离开，本局取消；检查场景后重新布置。')}
onDeactivated(deactivate);onBeforeUnmount(deactivate)
</script>
<template>
  <div class="col functions-panel">
    <TaskTabs v-model="tab" :tabs="tabs" id="functions" :label="category==='sorting'?'分拣分类':'有趣分类'" />
    <div v-if="taskState.busy || taskState.message" class="note" role="status" aria-live="polite">
      {{ taskState.busy?'处理中':taskState.message }}
    </div>
    <div id="functions-panel" class="col" role="tabpanel" :aria-labelledby="`functions-tab-${tab}`">
      <KeepAlive><HandFollowPanel v-if="tab==='interaction'" v-bind="props" @direction="emit('direction',$event)" @box-preview="emit('box-preview',$event)" /></KeepAlive>
      <TrackPanel v-if="tab==='track'" />
      <DrawingTask v-if="tab==='drawing'" />
      <TextWritingTask v-if="tab==='writing'" />
      <section v-if="tab==='chess'" class="panel chess-panel">
        <div class="panel-hd"><span class="panel-title">井字棋</span></div>
        <div class="panel-bd">
          <button class="btn btn--primary" :disabled="changingChessTool || !allowed || !chess.group || !!chess.error || (externalBusy&&!chessActive) || matchState.recoveryRequired" @click="startGame">{{chessActive?'停止':'开始'}}</button>
          <p v-if="chess.error" class="note" role="alert">{{ chess.error }}</p>
          <div class="chess-board" role="group" aria-label="井字棋棋盘">
            <button v-for="(cell,index) in board" :key="index" class="cell" :class="{computer:cell===matchState.ai}" :aria-label="`第${Math.floor(index/3)+1}行第${index%3+1}列：${cell || '空'}`" :disabled="busy || !match.canPlay(index)" @click="play(index)"><svg v-if="cell" class="chess-piece" viewBox="0 0 64 64" aria-hidden="true"><circle v-if="cell==='O'" cx="32" cy="32" r="21" /><path v-else d="M16 16L48 48M48 16L16 48" /></svg></button>
          </div>
          <p role="status" aria-live="polite">{{ matchState.message }}</p>
          <p v-if="starting && !chessActive" class="tiny dim" role="status" aria-live="polite">正在排程…<template v-if="preflightPct!==null"> {{ preflightPct }}%</template></p>
          <p v-else-if="startLatency" class="tiny dim" role="status">本次点击到首动实测 {{ (startLatency.ms/1000).toFixed(1) }} 秒</p>
          <p v-if="matchState.phase==='planning' && matchState.planningReadError" class="note" role="alert">{{ matchState.planningReadError }}</p>
          <p v-if="matchState.recoveryRequired" class="note">{{matchState.message}} <button class="btn btn--xs" :disabled="matchState.stopping || matchState.verifying" @click="match.verifyIdle()">检查状态</button></p>
        </div>
      </section>
      <section v-if="tab==='sorting'" class="panel">
        <div class="panel-hd"><span class="panel-title">机器人分拣</span><span class="tag">{{ realMode ? '真机不可用' : '仅仿真' }}</span></div>
        <div class="panel-bd">
          <div v-if="sortingCriterion==='color'" class="piece-counts" title="每色≤3 且合计≤9 用 72mm，其余 38mm；A 区随机乱序"><label v-for="(label,color) in {red:'红色',green:'绿色',blue:'蓝色'}" :key="color">{{ label }}<input v-model.number="counts[color]" class="input" type="number" min="0" max="9" step="1" :disabled="busy" /></label></div>
          <p class="dim tiny">共 {{ total }} 件</p>
          <p v-if="countError" class="note" role="alert">{{ countError }}</p>
          <label class="row">分拣依据<select v-model="sortingCriterion" class="select grow" :disabled="busy" @change="robotProgram.invalidate()"><option value="color">颜色</option><option value="shape">形状 · 矩形/圆形</option></select></label>
          <label class="row">物料来源<select v-model="sortingPerception" class="select grow" :disabled="busy" @change="robotProgram.invalidate()"><option value="scene">场景物料</option><option value="camera">相机识别</option></select></label>
          <div v-if="sortingCriterion==='shape'" class="piece-counts"><label>矩形<input v-model.number="shapeCounts.rectangle" type="number" min="0" max="9" :disabled="busy" /></label><label>圆形<input v-model.number="shapeCounts.circle" type="number" min="0" max="9" :disabled="busy" /></label></div>
        </div>
      </section>
      <RobotPlayProgram v-if="tab==='sorting'" :group="sorting.group" action="sorting" :criterion="sortingCriterion" :perception="sortingPerception" :blocked="taskState.busy">
        <template #placement><button class="btn" :disabled="busy || !allowed || !!countError" @click="setupTask('sorting')">摆放</button></template>
      </RobotPlayProgram>
      <PracticalTask v-if="['kit','pattern'].includes(tab)" :key="tab" :kind="tab" />
      <HandlingChallenge v-if="tab==='handling'" />
    </div>
  </div>
</template>
<style scoped>
.chess-panel .panel-bd{gap:6px}.chess-panel p{margin:0;line-height:1.45}.chess-help{font-size:11px}.chess-help p{margin-top:6px}.chess-board{width:min(100%,240px,calc(100dvh - 380px));min-width:132px;align-self:center;flex:none;display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line-2);border:1px solid var(--line-2)}
.cell{aspect-ratio:1;border:0;border-radius:0;background:var(--bg-inset);color:var(--acc);font:600 34px var(--font-mono,monospace);cursor:pointer}
.chess-piece{display:block;width:80%;height:80%;margin:auto;fill:none;stroke:currentColor;stroke-width:7;stroke-linecap:round;stroke-linejoin:round;filter:drop-shadow(0 0 4px currentColor)}
.cell.computer{color:var(--warn,#e0a33b)}.cell:disabled{cursor:default;opacity:1}.cell:focus-visible{outline:2px solid var(--acc);outline-offset:-3px}
.functions-panel{min-width:0}.functions-panel :deep(.task-tabs){flex-wrap:nowrap}.functions-panel :deep(.task-tabs>button){flex:1 1 0;min-width:0;white-space:nowrap;padding-left:4px;padding-right:4px}
.piece-counts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.piece-counts label{display:grid;gap:4px;font-size:12px}.piece-counts input{width:100%;min-width:0}
</style>
