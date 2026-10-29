<script setup>
import { computed, ref } from 'vue'
import { useWorld } from '../store'
import { useCameraSource } from '../cameraSource.js'
defineProps({compact:Boolean})
const w=useWorld()
const {busy,message,selected,realMode,virtual,devices,options,running,refresh:scan,select,toggle}=useCameraSource(w)
const fps=ref(Number(localStorage.getItem('huayan.camera.request-fps')) || 60),stream=ref(localStorage.getItem('huayan.camera.request-stream') || 'color')
const active=computed(()=>w.cameras?.active)
const device=computed(()=>devices.value.find(value=>value.id===selected.value))
function capture(){localStorage.setItem('huayan.camera.request-fps',String(fps.value));localStorage.setItem('huayan.camera.request-stream',stream.value);return toggle()}
</script>
<template>
  <section class="panel"><div class="panel-hd"><span class="panel-title">摄像头来源</span><span class="tag">{{ virtual?(running?'仿真采集中':'仿真已关闭'):active?.state || '未开启' }}</span></div>
    <div class="panel-bd">
      <label class="col">摄像头来源<select :value="selected" class="select" :disabled="busy" @change="select($event.target.value)"><option value="" disabled>{{realMode?'请选择真实摄像头':'请选择设备或末端仿真相机'}}</option><option v-for="item in options" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <div class="row"><button class="btn" :disabled="busy" title="重新枚举摄像头，更新新插入设备；不自动开启采集" @click="scan">{{busy?'加载中…':'重新加载'}}</button><button class="btn" :class="running?'btn--danger':'btn--primary'" :disabled="busy || (!virtual && !running && !device)" @click="capture">{{ busy?'处理中…':running?'关闭':'开启' }}</button></div>
      <p v-if="!devices.length" class="tiny dim">{{realMode?'尚无真实设备，插入摄像头后点击重新加载。':'尚无真实设备，请点击重新加载；仿真相机无需摄像头。'}}</p>
      <p v-if="virtual" class="tiny dim">末端仿真相机 · 画面来自三维场景，与真实摄像头互斥。</p>
      <template v-if="!virtual && !compact">
      <label class="row">图像流<select v-model="stream" class="select grow" :disabled="!!active || busy"><option value="color">彩色</option><option v-if="device?.streams?.includes('depth')" value="color+depth">彩色＋深度</option><option v-if="device?.streams?.includes('depth')" value="depth">深度</option></select></label>
      <label class="row">请求帧率<input v-model.number="fps" class="input grow" type="number" min="1" max="60" :disabled="!!active || busy" /></label>
      <div class="tiny dim">设备实际帧率以回读及实测为准；彩色流开启后自动运行当前工件识别方案。</div>
      <dl v-if="active" class="camera-properties"><dt>采集实测</dt><dd>{{ active.actual_fps ?? '—' }} fps</dd><dt>分辨率</dt><dd>{{ active.images?.color?.width || '—' }} × {{ active.images?.color?.height || '—' }}</dd><dt>设备类型</dt><dd>{{ active.kind }}</dd><dt>会话</dt><dd class="mono">{{ active.session }}</dd></dl>
      <p class="dim tiny">曝光、增益等写入接口尚未接入，不显示虚假的可调控件。更换设备前先关闭当前采集。</p>
      </template>
      <p v-if="message || (!virtual && active?.error)" class="note" role="alert">{{ message || active?.error }}</p>
    </div>
  </section>
</template>
<style scoped>.camera-properties{display:grid;grid-template-columns:75px 1fr;gap:6px;font-size:11px}.camera-properties dt{color:var(--tx-2)}.camera-properties dd{margin:0;overflow-wrap:anywhere}</style>
