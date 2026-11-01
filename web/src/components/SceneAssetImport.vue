<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { useWorld } from '../store'
import { parseResponse } from '../connectionState.js'
import { sceneEditBlock } from '../sceneTools.js'
import { parseSceneAsset } from '../sceneAssetVisual.js'
import { disposeAsset } from '../toolVisual.js'

const props = defineProps({ active: { type: Boolean, default: true } })
const w = useWorld()
const open = ref(false), input = ref(null), viewport = ref(null), file = ref(null)
const name = ref(''), units = ref(''), source = ref(''), license = ref(''), role = ref('obstacle')
const size = ref([100,100,100]), adapt = ref(false), inspected = ref(null), ready = ref(false), busy = ref(false), message = ref(''), failed = ref(false)
const block = computed(() => sceneEditBlock(w.snap, w.connected))
let encoded = '', buffer = null, request = null, generation = 0, disposed = false, preview = null, objectId = '', importing = false, sizeTimer = null
const metadata = () => ({ name:name.value.trim(), units:units.value, source:source.value.trim(), license:license.value.trim(), role:role.value, ...(inspected.value || adapt.value ? {size_mm:size.value.map(Number)} : {}) })
function clearPreview() {
  ready.value = false
  if (!preview) return
  preview.observer.disconnect(); preview.controls.dispose(); disposeAsset(preview.root)
  preview.renderer.dispose(); preview.renderer.domElement.remove(); preview = null
}
function cancel() {
  if(importing)message.value='请求已取消，请检查场景中是否已添加'
  generation++; request?.abort(); request = null; busy.value = false
  clearTimeout(sizeTimer); sizeTimer = null
  clearPreview(); inspected.value = null;file.value=null;encoded='';buffer=null;open.value=false
}
async function choose(event) {
  cancel(); message.value = ''; failed.value = false
  const chosen = event.target.files?.[0]; event.target.value = ''
  if (!chosen) return
  if (!/\.glb$/i.test(chosen.name) || chosen.size > 1024*1024) { failed.value=true; message.value='请选择1MiB以内的静态GLB'; return }
  const ticket = generation
  file.value = chosen; name.value = chosen.name.replace(/\.glb$/i,'').slice(0,24); open.value = true
  buffer = await chosen.arrayBuffer()
  if (disposed || ticket !== generation) return
  const bytes = new Uint8Array(buffer); let binary = ''
  for (let offset=0; offset<bytes.length; offset+=32768) binary += String.fromCharCode(...bytes.subarray(offset,offset+32768))
  encoded = btoa(binary); objectId = crypto.randomUUID().replaceAll('-','').slice(0,12)
}
async function draw(ticket) {
  clearPreview(); await nextTick()
  const root = await parseSceneAsset(buffer, size.value)
  if (disposed || ticket !== generation || !viewport.value) { disposeAsset(root); return }
  try {
    const renderer = new THREE.WebGLRenderer({antialias:true,alpha:true}); renderer.setPixelRatio(Math.min(window.devicePixelRatio,1.5))
    const scene = new THREE.Scene(); scene.add(root)
    scene.add(new THREE.HemisphereLight(0xffffff,0x50606c,2))
    const light = new THREE.DirectionalLight(0xffffff,2); light.position.set(1,2,3); scene.add(light)
    const camera = new THREE.PerspectiveCamera(38,1,.001,20)
    const distance = Math.max(...size.value)/1000*2.3; camera.position.set(distance,distance*.7,distance)
    const controls = new OrbitControls(camera,renderer.domElement); controls.enableDamping=false
    const render = () => { if (!disposed) renderer.render(scene,camera) }
    const resize = () => { if(!viewport.value)return;const width=Math.max(180,viewport.value.clientWidth);renderer.setSize(width,190);camera.aspect=width/190;camera.updateProjectionMatrix();render() }
    controls.addEventListener('change',render)
    const observer = new ResizeObserver(resize)
    preview={renderer,root,controls,observer};observer.observe(viewport.value)
    viewport.value.appendChild(renderer.domElement); resize(); ready.value=true
  } catch(error) { if(preview)clearPreview();else disposeAsset(root); throw error }
}
async function inspect() {
  if (!encoded || busy.value) return
  const ticket=++generation; request?.abort(); const controller=new AbortController();request=controller;const timeout=setTimeout(()=>controller.abort(),15000); busy.value=true; message.value=''; failed.value=false; inspected.value=null
  clearPreview()
  try {
    const value=await parseResponse(await fetch('/api/scene-assets/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({glb:encoded,metadata:metadata()}),signal:request.signal}),'/api/scene-assets/inspect')
    if(disposed || ticket!==generation)return
    if(value?.ok!==true)throw Error(typeof value?.why==='string' && value.why.trim()?value.why:'资源检查失败，请重试')
    if(!Array.isArray(value.asset?.size_mm) || value.asset.size_mm.length!==3
      || !value.asset.size_mm.every(v=>Number.isFinite(v) && v>=1 && v<=500)
      || typeof value.revision!=='string' || !value.revision.trim())throw Error('检查响应不完整，未开放添加')
    inspected.value=value;size.value=value.asset.size_mm.slice();await draw(ticket)
  } catch(error) { if(ticket===generation){failed.value=true;message.value=error.name==='AbortError'?'检查超时，请简化后重试':error.message;inspected.value=null;clearPreview()} }
  finally {clearTimeout(timeout); if(ticket===generation)busy.value=false }
}
async function place() {
  if(!ready.value || busy.value || block.value)return
  const ticket=++generation;const controller=new AbortController();request=controller;const timeout=setTimeout(()=>controller.abort(),15000);busy.value=true;failed.value=false;message.value='';importing=true
  try {
    const value=await parseResponse(await fetch('/api/scene-assets/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({glb:encoded,metadata:metadata(),object_id:objectId,revision:inspected.value.revision}),signal:request.signal}),'/api/scene-assets/import')
    if(disposed || ticket!==generation)return
    if(value?.ok!==true)throw Error(typeof value?.why==='string' && value.why.trim()?value.why:'资源添加失败，请重试')
    if(typeof value.object?.id!=='string' || !value.object.id
      || typeof value.object.name!=='string' || !value.object.name)throw Error('添加响应不完整，结果尚未确认')
    w.selObj=value.object.id;message.value=`已添加：${value.object.name}`;clearPreview();inspected.value=null;file.value=null
  } catch(error) { if(ticket===generation){failed.value=true;message.value=(error.name==='AbortError'?'导入请求超时':error.message)+'；请检查场景后重试';inspected.value=null;clearPreview()} }
  finally {clearTimeout(timeout); importing=false;if(ticket===generation)busy.value=false }
}
watch([name,units,source,license,role],()=>{if(inspected.value){inspected.value=null;clearPreview()}})
// 尺寸每敲一个字符就重建一次 WebGL 上下文、重新解析一次 GLB：太贵。
// 合并成一次延迟重绘——先作废在途绘制，等用户停手 250ms 后只重建一次。
watch(size,()=>{
  if(!inspected.value || busy.value)return
  const ticket=++generation
  clearTimeout(sizeTimer)
  sizeTimer=setTimeout(()=>{draw(ticket).catch(error=>{failed.value=true;message.value=error.message})},250)
},{deep:true})
watch(()=>props.active,active=>{if(!active)cancel()})
function loadError(event) { failed.value=true;message.value=event.detail?.why || '场景资源显示失败，保留碰撞代理' }
window.addEventListener('elfin-scene-asset-error',loadError)
onBeforeUnmount(()=>{disposed=true;cancel();window.removeEventListener('elfin-scene-asset-error',loadError)})
</script>

<template>
  <div class="asset-import">
    <button class="btn btn--xs" type="button" @click="input?.click()">导入三维资源</button>
    <input ref="input" type="file" accept=".glb" class="asset-file" @change="choose" />
    <div v-if="open && file" class="asset-form">
      <p class="dim tiny">静态GLB · 1MiB内 · 无贴图、外链和动画。外观导入不具备抓取资格。</p>
      <label>名称<input v-model="name" class="input" maxlength="24" :disabled="busy" /></label>
      <label>原始单位<select v-model="units" class="select" :disabled="busy"><option value="" disabled>请选择</option><option value="m">米</option><option value="cm">厘米</option><option value="mm">毫米</option></select></label>
      <label>来源<input v-model="source" class="input" maxlength="300" placeholder="作者或来源说明" :disabled="busy" /></label>
      <label>许可<input v-model="license" class="input" maxlength="160" placeholder="填写已确认的许可或使用授权" :disabled="busy" /></label>
      <label>用途<select v-model="role" class="select" :disabled="busy"><option value="obstacle">障碍物（参与碰撞）</option><option value="decoration">装饰物（不参与碰撞）</option></select></label>
      <label class="asset-adapt"><input v-model="adapt" type="checkbox" :disabled="busy" />指定适配尺寸</label>
      <div v-if="inspected || adapt" class="asset-size"><span>实际尺寸 X / Y / Z（mm）</span><div class="row"><input v-for="(_,index) in size" :key="index" v-model.number="size[index]" type="number" min="1" max="500" class="input" :aria-label="`资源尺寸${['X','Y','Z'][index]}`" :disabled="busy" /></div></div>
      <div ref="viewport" class="asset-viewport" v-show="inspected" aria-label="导入资源预览"></div>
      <p v-if="block" class="dim tiny">{{ block }}</p>
      <div class="row"><button type="button" class="btn btn--xs" :disabled="busy || !units || !source.trim() || !license.trim() || !name.trim()" @click="inspect">检查并预览</button><button type="button" class="btn btn--primary btn--xs" :disabled="busy || !ready || !!block" @click="place">确认添加</button><button type="button" class="btn btn--xs" @click="cancel">取消</button></div>
      <p v-if="busy" class="dim tiny" role="status">处理中…</p>
    </div>
    <p v-if="message" class="tiny asset-message" :class="{failed}" role="status">{{ message }}</p>
  </div>
</template>

<style scoped>
.asset-import{margin-top:10px}.asset-file{display:none}.asset-form{display:flex;flex-direction:column;gap:8px;margin-top:8px}.asset-form label{display:grid;grid-template-columns:58px minmax(0,1fr);align-items:center;gap:6px;font-size:12px}.asset-form .input,.asset-form .select{width:100%;min-width:0}.asset-size .row .input{width:0;flex:1}.asset-viewport{width:100%;min-height:190px;border:1px solid var(--line);border-radius:6px;overflow:hidden}.asset-message{overflow-wrap:anywhere}.failed{color:var(--red)}
</style>
