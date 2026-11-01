<script setup>
import { computed, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { useWorld } from '../store'
import { parseResponse } from '../connectionState.js'
import { playAllowed } from '../functionPlay.js'

const w=useWorld(), state=ref(null), level=ref(1), pending=ref(''), message=ref(''), failed=ref(false)
const active=computed(()=>['checking','running'].includes(state.value?.phase))
const stoppable=computed(()=>active.value||['inspect','run'].includes(pending.value))
const allowed=computed(()=>playAllowed(w))
const phase=computed(()=>({idle:'未布置',ready:'待开始',checking:'准备中',checked:'可执行',running:'正在搬运',completed:'本关完成',cancelled:'已取消 / 部分完成',failed:'未完成'}[state.value?.phase] || '读取中'))
let controller=null,poll=null,timer=null,version=0,mutationEpoch=0,disposed=false,leaving=false
const prefix='/api/challenge/handling'
async function request(action,body,signal) {
  const timeout=action==='inspect'?105000:15000
  const combined=signal?AbortSignal.any([signal,AbortSignal.timeout(timeout)]):AbortSignal.timeout(timeout)
  const response=await fetch(prefix+(action?'/'+action:''),{method:action?'POST':'GET',cache:'no-store',signal:combined,
    ...(action?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body || {})}:{})})
  return parseResponse(response,prefix)
}
async function read() {
  if(disposed || leaving)return
  clearTimeout(timer);poll?.abort();const current=new AbortController();poll=current;const epoch=mutationEpoch
  try {
    const value=await request('',null,current.signal)
    if(!disposed && !leaving && epoch===mutationEpoch) {
      if(!failed.value && state.value?.phase!==value.phase)message.value=''
      state.value=value
    }
  }
  catch(error){if(!disposed && !leaving && epoch===mutationEpoch && error.name!=='AbortError'){failed.value=true;message.value=error.message}}
  finally {if(poll===current){poll=null;if(!disposed && !leaving)timer=setTimeout(read,active.value?500:2000)}}
}
async function act(action) {
  if(disposed || (pending.value && action!=='cancel'))return
  if(action==='run' && (!allowed.value || state.value?.phase!=='checked' || !state.value?.plan_id))return
  const previous=state.value
  const body=action==='setup'?{level:Number(level.value)}:action==='inspect'?{challenge_id:previous?.challenge_id}:
    action==='run'?{challenge_id:previous?.challenge_id,plan_id:previous?.plan_id}:{challenge_id:previous?.challenge_id}
  const ticket=++version;mutationEpoch++;controller?.abort();controller=new AbortController();pending.value=action;message.value='';failed.value=false
  // 一次 setup 最多自动回收一次跟随，避免停不掉时反复重试。
  const failedReclaim = action !== 'setup'
  try {
    const value=await request(action,body,controller.signal)
    if(ticket!==version || disposed)return
    mutationEpoch++;state.value=value;message.value=value.message || ''
  } catch(error) {
    // 仅仿真（绝不涉及真机）：残留的手掌跟随会话挡住了本页的自动布置。进页会自动
    // setup，此时停掉那次可见的空闲跟随，是操作者离开跟随页时期望的行为，也是安全的。
    // 其它占用（运动中的程序、队列、就绪任务、真机）仍照原样报冲突。
    if(action==='setup' && ticket===version && !disposed && !failedReclaim && /跟随/.test(error.message || '') &&
        w.mode!=='real' && !(w.snap?.real || {})?.connected) {
      failedReclaim=true
      try {
        const stop = await fetch('/api/sim/hand-follow/stop',{method:'POST',headers:{'Content-Type':'application/json'},
          body:'{}',signal:AbortSignal.timeout(10000)})
        if(!stop.ok)throw Error(String(stop.status))
        await new Promise(done=>setTimeout(done,1200))
        if(ticket===version && !disposed) {
          const value=await request('setup',body,controller.signal)
          if(ticket===version && !disposed){mutationEpoch++;state.value=value;failed.value=false
            message.value='已自动停止手掌跟随，'+(value.message || '搬运闯关已布置');return}
        }
      } catch(retryError) {
        if(ticket===version && !disposed)message.value='已请求停止手掌跟随但未生效，请到「有趣」页确认停止后重试'
      }
    }
    if(ticket===version && !disposed){failed.value=true;message.value=error.name==='AbortError'?'请求超时或取消，需回读当前状态':error.message}
  } finally {if(ticket===version)pending.value=''}
}
async function release() {
  if(pending.value)return
  pending.value='release';failed.value=false
  try {
    const response=await fetch('/api/play/program/release',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(15000)})
    const result=await parseResponse(response,'/api/play/program/release');message.value=result.note || '仿真持件已释放';await read()
  } catch(error){failed.value=true;message.value=error.message}
  finally{pending.value=''}
}
async function startStop(){
  if(active.value||['inspect','run'].includes(pending.value))return act('cancel')
  if(!state.value?.group||['idle','completed','cancelled','failed'].includes(state.value?.phase))await act('setup')
  if(state.value?.phase==='ready'&&!pending.value)await act('inspect')
  if(state.value?.phase==='checked'&&!pending.value)await act('run')
}
async function togglePause(){
  const action=w.snap?.exec?.state==='paused'?'resume':'pause'
  try{const response=await fetch('/api/motion/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(10000)});const value=await parseResponse(response,'/api/motion/'+action);message.value=value.message||''}
  catch(error){failed.value=true;message.value=error.message}
}
async function initialize(){await read();if(state.value&&!state.value.group&&!pending.value)await act('setup')}
function changeLevel(){if(!active.value&&!pending.value)void act('setup')}
function leave() {
  if(leaving)return
  leaving=true;version++;clearTimeout(timer);poll?.abort();controller?.abort()
  if((active.value || ['inspect','run'].includes(pending.value)) && state.value?.challenge_id)request('cancel',{challenge_id:state.value.challenge_id}).catch(error=>{w.error='搬运闯关离页取消未确认：'+error.message})
}
onMounted(initialize);onDeactivated(leave);onBeforeUnmount(()=>{leave();disposed=true})
</script>

<template>
  <section class="panel handling-challenge">
    <div class="panel-hd"><span class="panel-title">搬运闯关</span><span class="tag">仅仿真</span></div>
    <div class="panel-bd col">
      <div class="row"><select v-model.number="level" class="select grow" aria-label="搬运关卡" :disabled="!!pending || active" @change="changeLevel"><option v-for="item in state?.levels || []" :key="item.id" :value="item.id">第{{ item.id }}关 · {{ item.name }}</option></select></div>
      <p v-if="!allowed && !active" class="note">请保持未连接真机的静止仿真，并结束其他程序或跟随。</p>
      <div v-if="state?.group" class="challenge-stats" role="status"><strong>第{{state.level}}关 · {{phase}}</strong><span>已确认 {{state.completed}} / {{state.total}} 件</span><span>执行 {{state.elapsed_s.toFixed(2)}} 秒 · {{state.score}} 分</span></div>
      <div class="row"><button type="button" class="btn btn--primary grow" :disabled="pending==='cancel'||(!stoppable&&(!!pending||!allowed))" @click="startStop">{{stoppable?'停止':'开始'}}</button><button type="button" class="btn" :disabled="state?.phase!=='running'" @click="togglePause">{{w.snap?.exec?.state==='paused'?'继续':'暂停'}}</button></div>
      <p v-if="state?.phase==='checking'" class="dim tiny" role="status">{{state?.progress?.step || 0}} / {{state?.progress?.total || '—'}}</p>
      <button v-if="state?.held && state.phase!=='running'" type="button" class="btn btn--danger" :disabled="!!pending" @click="release">停止吸气并释放仿真持件</button>
      <p v-if="message || state?.message" class="note" :class="{bad:failed}" role="status">{{message || state.message}}</p>
      <p v-if="state?.phase==='cancelled' || state?.phase==='failed'" class="dim tiny">保留已确认完成数量；未全部完成不计满分。持件需人工释放整理后才能重新布置。</p>
      <details v-if="state?.results?.length"><summary>本次会话成绩</summary><div class="tscroll"><table><thead><tr><th>关卡</th><th>完成</th><th>执行秒数</th><th>分数</th></tr></thead><tbody><tr v-for="(item,index) in state.results.slice().reverse()" :key="index"><td>{{item.level}}</td><td>{{item.completed}} / {{item.total}}</td><td>{{item.elapsed_s.toFixed(2)}}</td><td>{{item.score}}</td></tr></tbody></table></div></details>
    </div>
  </section>
</template>

<style scoped>
.handling-challenge{min-width:0}.handling-challenge .select{min-width:0}.challenge-stats{display:grid;gap:5px;font-size:12px}.handling-challenge td,.handling-challenge th{font-size:12px;white-space:nowrap}.handling-challenge .note{overflow-wrap:anywhere}
</style>
