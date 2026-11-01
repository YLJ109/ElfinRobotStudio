<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { trustedRemoteLink } from '../remoteCamera.ts'
const state=ref(null),code=ref(''),error=ref(''),busy=ref(false),expires=ref(0)
const detector=ref('hand')
let timer=null,abort=null,disposed=false,refreshing=false,version=0
async function call(path,body){
  const response=await fetch('/api/vision/remote/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:abort?AbortSignal.any([abort.signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000),cache:'no-store'})
  const value=await response.json();if(!response.ok || value.ok===false)throw Error(value.why || value.detail || '远程相机请求失败');return value
}
async function refresh(){if(refreshing)return;refreshing=true;const epoch=version;try{const value=await call('state');if(!disposed && epoch===version){state.value=value;if(value.phase!=='pairing')code.value=''}}catch(e){if(!disposed && epoch===version)error.value=e.message}finally{refreshing=false}}
async function act(action){if(busy.value)return;busy.value=true;version++;error.value='';try{const value=await call(action,action==='pair'?{detector:detector.value}:{});if(disposed)return;if(action==='pair'){code.value=value.code;expires.value=Date.now()+value.expires_in*1000}else{code.value='';expires.value=0}await refresh()}catch(e){if(!disposed)error.value=e.message}finally{if(!disposed)busy.value=false}}
function joinLink(){return trustedRemoteLink(state.value?.join_url,state.value?.public_origin || '')}
onMounted(()=>{abort=new AbortController();void refresh();timer=setInterval(()=>{if(code.value && Date.now()>=expires.value)code.value='';if(!document.hidden)void refresh()},5000)})
onBeforeUnmount(()=>{disposed=true;abort?.abort();clearInterval(timer)})
</script>
<template>
  <details><summary>手机或远程电脑相机</summary>
    <p v-if="!state?.available" class="tiny dim">{{state?.reason || state?.error || '尚未配置工作站 HTTPS 远程相机入口'}}</p>
    <template v-else>
      <p class="tiny dim">在远程设备打开相机页，再输入一次性配对码。视频仅上传工作站检测，不提供运动控制。</p>
      <a v-if="joinLink()" :href="joinLink()" target="_blank" rel="noopener noreferrer">打开远程相机页</a>
      <p v-else class="tiny dim">尚未配置可信 HTTPS 相机链接</p>
      <label class="row">检测内容<select v-model="detector" class="select grow" :disabled="busy"><option value="hand">手掌</option><option value="workpiece">工件颜色与轮廓</option></select></label>
      <div class="row"><button class="btn" :disabled="busy || !joinLink()" @click="act('pair')">生成配对码</button><button class="btn" :disabled="busy" @click="act('revoke')">撤销连接</button></div>
      <p v-if="code" class="mono">{{code}} · 两分钟内有效，仅可使用一次</p>
      <p class="tiny dim">{{({streaming:'远程检测已连接',pairing:'等待输入配对码',paired:'等待远程视频',connecting:'视频连接建立中',idle:'尚未连接'})[state.phase] || '尚未连接'}}</p>
      <p v-if="state.detector==='workpiece' && state.phase!=='idle'" class="tiny dim">工件结果在视觉页查看、标定，供找物与计数检查；远程画面不能用于机器人取放。重新配对会清除旧来源和标定。</p>
    </template>
    <p v-if="error" class="note" role="alert">{{error}}</p>
  </details>
</template>
