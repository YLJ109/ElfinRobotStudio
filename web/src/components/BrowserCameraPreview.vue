<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
const props=defineProps({controller:{type:Object,required:true}})
const emit=defineEmits(['resolution'])
const video=ref(null),playError=ref('')
const release=props.controller.retainPreview()
let frameHandle=null,disposed=false
function readFrame(){
  if(disposed || !video.value || !props.controller.state.stream)return
  props.controller.presentFrame()
  emit('resolution',{width:video.value.videoWidth,height:video.value.videoHeight})
  if(video.value.requestVideoFrameCallback)frameHandle=video.value.requestVideoFrameCallback(readFrame)
}
function loaded(){
  if(!video.value)return
  if(video.value.requestVideoFrameCallback){if(frameHandle===null)frameHandle=video.value.requestVideoFrameCallback(readFrame)}
  else if(video.value.readyState>=2)readFrame()
}
watch(()=>[video.value,props.controller.state.stream],async([element,stream])=>{
  if(!element)return
  if(frameHandle!==null)element.cancelVideoFrameCallback?.(frameHandle);frameHandle=null
  element.srcObject=stream || null;playError.value=''
  if(stream)try{await element.play()}catch(error){if(!disposed)playError.value='预览播放失败，请关闭后重新开启'}
},{flush:'post'})
onBeforeUnmount(()=>{disposed=true;if(frameHandle!==null)video.value?.cancelVideoFrameCallback?.(frameHandle);if(video.value)video.value.srcObject=null;release()})
</script>
<template>
  <div class="browser-preview">
    <video ref="video" autoplay muted playsinline aria-label="浏览器本机相机预览" @loadeddata="loaded" @timeupdate="!video?.requestVideoFrameCallback && loaded()"></video>
    <span v-if="!controller.state.frameReady || playError" class="preview-state" role="status">{{playError || (controller.state.status==='requesting'?'等待相机授权':'等待新画面')}}</span>
  </div>
</template>
<style scoped>.browser-preview{position:relative;width:100%;height:100%;min-height:100px;background:var(--bg-inset)}video{display:block;width:100%;height:100%;object-fit:contain}.preview-state{position:absolute;inset:0;display:grid;place-items:center;background:rgba(0,0,0,.5);font-size:12px;color:var(--tx-2)}</style>
