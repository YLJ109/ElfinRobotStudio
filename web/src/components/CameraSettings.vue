<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { useWorld } from '../store'
import { useCameraSource } from '../cameraSource.js'
import BrowserCameraPreview from './BrowserCameraPreview.vue'
import RemoteCameraPairing from './RemoteCameraPairing.vue'
import { visionWorkspace } from '../visionWorkspace.js'
defineProps({compact:Boolean})
const w=useWorld()
const {busy,message,selected,realMode,virtual,browser,browserPreview,handDetection,setHandDetection,devices,options,running,refresh:scan,ensureDevices,select,toggle}=useCameraSource(w)
watch(()=>[w.connected,w.snap?.session_id,busy.value],([connected])=>{
  if(connected && !document.hidden && !busy.value)void ensureDevices()
},{immediate:true})
const fps=ref(Number(localStorage.getItem('huayan.camera.request-fps')) || 60),stream=ref(localStorage.getItem('huayan.camera.request-stream') || 'color')
onBeforeUnmount(()=>{if(browserPreview.state.status==='requesting')browserPreview.stop('页面已离开')})
const active=computed(()=>w.cameras?.active)
const device=computed(()=>devices.value.find(value=>value.id===selected.value))
function capture(){localStorage.setItem('huayan.camera.request-fps',String(fps.value));localStorage.setItem('huayan.camera.request-stream',stream.value);return toggle()}
</script>
<template>
  <section class="panel"><div class="panel-hd"><span class="panel-title">摄像头来源</span><span class="tag">{{browser?(browserPreview.state.frameReady?'本地预览':running?'等待画面':'未开启'):virtual?(running?'仿真采集中':'仿真已关闭'):active?.state || '未开启'}}</span></div>
    <div class="panel-bd">
      <label class="col">摄像头来源<select :value="selected" class="select" :disabled="busy" @change="select($event.target.value)"><option value="" disabled>{{realMode?'请选择真实摄像头':'请选择设备或末端仿真相机'}}</option><option v-for="item in options" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <div class="row"><button class="btn" :disabled="busy" title="重新枚举摄像头，更新新插入设备；不自动开启采集" @click="scan">{{busy?'加载中…':'重新加载'}}</button><button class="btn" :class="running?'btn--danger':'btn--primary'" :disabled="(busy && browserPreview.state.status!=='requesting') || (!browser && !virtual && !running && !device)" @click="capture">{{browserPreview.state.status==='requesting'?'取消开启':busy?'处理中…':running?'关闭':'开启'}}</button></div>
      <p v-if="!browser && !devices.length" class="tiny dim">{{realMode?'尚无真实设备，插入摄像头后点击重新加载。':'尚无真实设备，请点击重新加载；仿真相机无需摄像头。'}}</p>
      <p v-if="virtual" class="tiny dim">末端仿真相机 · 画面来自三维场景，与真实摄像头互斥。</p>
      <div v-if="browser && running" class="local-preview"><BrowserCameraPreview :controller="browserPreview" /></div>
      <p v-if="browser" class="tiny dim">仅本机预览，不上传视频，不运行检测；需要 HTTPS 或本机安全地址。</p>
      <p v-if="browser && visionWorkspace.config?.enabled && visionWorkspace.config?.input==='virtual'" class="tiny dim">工作站来源：末端仿真相机 · {{visionWorkspace.config.detection_enabled?'工件检测已开启':'仅预览'}}。上方为本页本地预览。</p>
      <template v-if="!browser && !virtual && !compact">
      <label class="row">图像流<select v-model="stream" class="select grow" :disabled="!!active || busy"><option value="color">彩色</option><option v-if="device?.streams?.includes('depth')" value="color+depth">彩色＋深度</option><option v-if="device?.streams?.includes('depth')" value="depth">深度</option></select></label>
      <label class="row">请求帧率<input v-model.number="fps" class="input grow" type="number" min="1" max="60" :disabled="!!active || busy" /></label>
      <dl v-if="active" class="camera-properties"><dt>采集实测</dt><dd>{{ active.actual_fps ?? '—' }} fps</dd><dt>分辨率</dt><dd>{{ active.images?.color?.width || '—' }} × {{ active.images?.color?.height || '—' }}</dd><dt>设备类型</dt><dd>{{ active.kind }}</dd><dt>会话</dt><dd class="mono">{{ active.session }}</dd>      </dl>
      </template>
      <p v-if="message || (browser && browserPreview.state.error) || (!virtual && active?.error)" class="note" role="alert">{{ message || browserPreview.state.error || active?.error }}</p>
      <RemoteCameraPairing v-if="!compact" />
    </div>
  </section>
</template>
<style scoped>.local-preview{aspect-ratio:4/3;min-height:120px;overflow:hidden}.camera-properties{display:grid;grid-template-columns:75px 1fr;gap:6px;font-size:11px}.camera-properties dt{color:var(--tx-2)}.camera-properties dd{margin:0;overflow-wrap:anywhere}</style>
