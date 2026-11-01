<script setup>
import {ref,onMounted,onActivated,onDeactivated,onBeforeUnmount} from 'vue'
import {parseResponse} from '../connectionState.js'
import {displayNumericText} from '../numberDisplay.js'
const runs=ref([]),busy=ref(false),message=ref('')
const phases={running:'执行中',waiting:'等待中',paused:'已暂停',finished:'已完成',aborted:'已中止',error:'执行失败',interrupted:'结束未确认',replaced:'已替换'}
const actions={sorting:'分拣',chess:'井字棋',handling:'搬运闯关',kit:'配套装盒',pattern:'图案拼摆'}
let generation=0,request=null
function cancel(){generation++;request?.abort();request=null;busy.value=false}
async function perform(work){
  if(busy.value)return
  const current=++generation,controller=new AbortController();request=controller;busy.value=true;message.value=''
  const timer=setTimeout(()=>controller.abort(),8000)
  try{await work(controller.signal,current)}catch(error){if(current===generation)message.value=error.name==='AbortError'?'读取已取消或超时':error.message}
  finally{clearTimeout(timer);if(current===generation){request=null;busy.value=false}}
}
function refresh(){return perform(async(signal,current)=>{
  const path='/api/runs',value=await parseResponse(await fetch(path,{cache:'no-store',signal}),path)
  if(!Array.isArray(value.runs) || value.analysis_only!==true)throw Error('运行记录格式无效')
  if(current===generation){runs.value=value.runs;message.value=value.error || ''}
})}
function exportRun(row){return perform(async(signal,current)=>{
  const path='/api/runs/'+encodeURIComponent(row.run_id)+'/export'
  const value=await parseResponse(await fetch(path,{cache:'no-store',signal}),path)
  if(value.analysis_only!==true || value.record?.run_id!==row.run_id)throw Error('导出记录身份不符')
  if(current!==generation)return
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}))
  const link=document.createElement('a');link.href=url;link.download='运行记录.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),0)
})}
const source=row=>`${row.context?.mode==='sim'?'仿真':row.context?.mode==='real'?'真机模式':'来源未知'} · ${actions[row.context?.action] || '程序'}`
onMounted(refresh);onActivated(refresh);onDeactivated(cancel);onBeforeUnmount(cancel)
</script>
<template>
  <section class="run-journal col" aria-label="运行记录">
    <div class="row"><strong class="grow">运行记录</strong><button class="btn btn--xs" :disabled="busy" @click="refresh">{{busy?'读取中…':'刷新'}}</button></div>
    <p v-if="message" class="note" role="status">{{message}}</p>
    <p v-if="!busy && !runs.length" class="tiny dim">暂无运行记录。</p>
    <div v-for="row in runs" :key="row.run_id" class="run-record col">
      <div class="row"><span class="grow">{{source(row)}} · {{phases[row.phase] || '状态未知'}}</span><button class="btn btn--xs" :disabled="busy" @click="exportRun(row)">导出</button></div>
      <span class="tiny dim">{{new Date(row.started_at*1000).toLocaleString()}} · {{row.confirmed_steps}} 步已确认<template v-if="row.last_confirmed"> · 最后确认{{row.last_confirmed.line?'第'+row.last_confirmed.line+'行':'第'+(row.last_confirmed.index+1)+'步'}}</template><template v-else> · 暂无确认步骤</template><template v-if="row.partial && !['running','waiting','paused'].includes(row.phase)"> · 部分完成</template></span>
      <p v-if="row.reason" class="tiny">{{displayNumericText(row.reason)}}</p>
    </div>
  </section>
</template>
<style scoped>
.run-journal{min-width:0}.run-record{padding:8px 0;border-bottom:1px solid var(--line)}.run-record p{margin:0;overflow-wrap:anywhere}
</style>
