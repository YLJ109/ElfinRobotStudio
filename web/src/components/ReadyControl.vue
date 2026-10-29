<script setup>
import { computed, ref, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
const w=useWorld(),{snap}=storeToRefs(w)
const ctrl=computed(()=>snap.value?.ctrl || {})
const primary=computed(()=>ctrl.value.primary || {key:'link',label:'连接中…',enabled:false})
const running=computed(()=>!!ctrl.value.ready_running)
const cancelling=computed(()=>running.value || primary.value.key==='cancel_ready')
const label=computed(()=>cancelling.value?'取消就绪':primary.value.key==='ready'?'一键就绪':primary.value.label)
const busy=ref(false),denied=ref('')
let disposed=false
onBeforeUnmount(()=>{disposed=true})
async function press(){
  if(disposed || busy.value || !w.connected || (!cancelling.value && !primary.value.enabled))return
  const key=primary.value.key,cancel=cancelling.value
  const session=snap.value?.session_id,mode=snap.value?.mode
  const unchanged=()=>!disposed && w.connected && snap.value?.session_id===session &&
    snap.value?.mode===mode && primary.value.key===key && primary.value.enabled
  busy.value=true;denied.value=''
  try{
    if(cancel){
      const result=await w.call('/api/ready/cancel',{})
      if(!disposed && !result?.ok)denied.value=result?.why || '取消就绪未确认'
      return
    }
    let challenge
    if(ctrl.value.need_ack){
      // The visible inline declaration is accepted by this explicit click,
      // never on mount, polling, reconnect or a background timer.
      const ack=await w.ackMotion({nobody:true,estop:true})
      if(!unchanged())return
      if(!ack?.ok || !ack.challenge){denied.value=ack?.why || '就绪请求未获确认';return}
      challenge=ack.challenge
    }
    if(!unchanged())return
    const result=await w.control(key,challenge)
    if(!disposed && !result?.ok)denied.value=result?.why || '操作未完成'
  }catch(error){if(!disposed)denied.value=error.message || '操作失败'}
  finally{busy.value=false}
}
</script>
<template>
  <div class="ready-control">
    <button class="btn ready-primary" :class="'btn--'+(cancelling?'warn':primary.tone || 'acc')"
      :disabled="!w.connected || busy || (!cancelling && !primary.enabled)" :title="primary.why" @click="press">
      {{ busy?'请求中…':label }}
    </button>
    <p v-if="snap?.mode==='real' && primary.key==='ready' && !cancelling" class="tiny dim">
      点击就绪表示已确认工作区无人、无障碍，并能触及物理急停。就绪不执行点位运动。
    </p>
    <div v-if="denied" class="note bad" role="alert">{{ denied }}</div>
  </div>
</template>
<style scoped>
.ready-control{display:flex;flex-direction:column;gap:6px}
.ready-primary{width:100%;min-height:38px}
.ready-control p{margin:0;line-height:1.5}
</style>
