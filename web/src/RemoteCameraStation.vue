<script setup>
import { ref, onBeforeUnmount } from 'vue'
import BrowserCameraPreview from './components/BrowserCameraPreview.vue'
import { createRemoteCamera } from './remoteCamera'
const remote=createRemoteCamera(),code=ref(''),secure=location.protocol==='https:'
const release=remote.lifecycle()
onBeforeUnmount(release)
async function preview(){if(['streaming','requesting'].includes(remote.preview.state.status))remote.stop();else await remote.preview.start()}
</script>
<template>
  <main class="remote-station">
    <h1>远程相机</h1>
    <div class="preview"><BrowserCameraPreview :controller="remote.preview" /></div>
    <button @click="preview">{{remote.preview.state.status==='requesting'?'取消开启':remote.preview.state.status==='streaming'?'关闭相机':'开启本地预览'}}</button>
    <p>本地预览不上传。上传检测后由工作站处理；结果仅供查看。</p>
    <label>工作站配对码<input v-model="code" autocomplete="off" spellcheck="false" maxlength="32" :disabled="!['idle','error'].includes(remote.state.phase)" /></label>
    <button :disabled="!secure || remote.preview.state.status!=='streaming' || !['idle','error'].includes(remote.state.phase)" @click="remote.startDetection(code).then(ok=>{if(ok)code=''})">上传检测</button>
    <button v-if="!['idle','error'].includes(remote.state.phase)" @click="remote.stop()">停止上传并关闭相机</button>
    <p v-if="!secure">上传检测需要工作站同源 HTTPS。</p>
    <p role="status">{{({idle:'未上传',joining:'正在配对',connecting:'等待检测画面',detecting:'工作站检测中',error:'已停止'})[remote.state.phase]}}</p>
    <p v-if="!['idle','error'].includes(remote.state.phase)">{{remote.state.detector==='workpiece'?'工件颜色与轮廓检测 · 仅供查看':'手掌检测 · 仅供查看'}}</p>
    <p v-if="remote.state.error || remote.preview.state.error" role="alert">{{remote.state.error || remote.preview.state.error}}</p>
    <p v-if="remote.state.result?.valid">识别到 {{remote.state.result.observations?.length || 0}} 项目标</p>
  </main>
</template>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#15191f;color:#e4e9ef}body{margin:0}.remote-station{max-width:640px;margin:auto;padding:20px;box-sizing:border-box}h1{font-size:22px}.preview{aspect-ratio:4/3;background:#080b0f;border-radius:8px;overflow:hidden}button,input{font:inherit;padding:10px;margin:8px 6px 8px 0;border:1px solid #536174;border-radius:5px;background:#242e3b;color:inherit}button:disabled{opacity:.5}p{font-size:14px;line-height:1.6}label{display:block}input{max-width:100%;box-sizing:border-box}
</style>
