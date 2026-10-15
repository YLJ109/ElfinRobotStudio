<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
const props = defineProps({ session: String, stream: String, measure: Boolean })
const url = ref(''), error = ref(''), measurement = ref(''), image = ref(null)
let timer, controller, revision = 0
function clear() { clearTimeout(timer); controller?.abort(); if (url.value) URL.revokeObjectURL(url.value); url.value = '' }
async function refresh(seq) {
  if (seq !== revision || !props.session) return
  controller = new AbortController()
  try {
    const response = await fetch(`/api/cameras/frame.jpg?session=${encodeURIComponent(props.session)}&stream=${props.stream}`, { cache: 'no-store', signal: controller.signal })
    if (!response.ok) throw new Error('等待有效图像帧…')
    const blob = await response.blob()
    if (seq !== revision) return
    const next = URL.createObjectURL(blob), old = url.value
    url.value = next; error.value = ''
    if (old) URL.revokeObjectURL(old)
  } catch (e) { if (seq === revision && e.name !== 'AbortError') { error.value = e.message; if (url.value) URL.revokeObjectURL(url.value); url.value = '' } }
  finally { if (seq === revision) timer = setTimeout(() => refresh(seq), 150) }
}
async function measureAt(event) {
  if (!props.measure || !image.value || !url.value) return
  const rect = image.value.getBoundingClientRect()
  const x = Math.min(image.value.naturalWidth - 1, Math.max(0, Math.floor((event.clientX - rect.left) / rect.width * image.value.naturalWidth)))
  const y = Math.min(image.value.naturalHeight - 1, Math.max(0, Math.floor((event.clientY - rect.top) / rect.height * image.value.naturalHeight)))
  const seq = revision
  try {
    const r = await (await fetch(`/api/cameras/measure?session=${encodeURIComponent(props.session)}&x=${x}&y=${y}`)).json()
    if (seq !== revision) return
    measurement.value = r.ok ? `(${x},${y}) 距离 ${r.distance_mm}mm · XYZ ${r.xyz_mm.join(', ')}mm` : r.why
  } catch (e) { if (seq === revision) measurement.value = e.message }
}
watch(() => [props.session, props.stream], () => { revision++; clear(); error.value = ''; measurement.value = ''; refresh(revision) }, { immediate: true })
onBeforeUnmount(() => { revision++; clear() })
</script>
<template><figure class="camera-frame"><figcaption>{{ ({color:'彩色',depth:'深度',infrared1:'左红外',infrared2:'右红外',infrared:'红外'})[stream] || stream }}<small v-if="measure">点击深度图测距</small></figcaption><img v-if="url" ref="image" :src="url" :alt="stream + ' 真实摄像头画面'" @click="measureAt" /><div v-else class="frame-wait">{{ error || '等待图像…' }}</div><small v-if="measurement">{{ measurement }}</small></figure></template>
<style scoped>
.camera-frame { margin: 9px 0 0; padding: 6px; border: 1px solid var(--line-2); border-radius: 5px; background: var(--bg-inset); }
figcaption { display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 5px; }
small { font-size: 10px; color: var(--tx-3); overflow-wrap: anywhere; }
img { display: block; width: 100%; height: auto; border-radius: 3px; }
.frame-wait { padding: 28px 6px; text-align: center; color: var(--tx-3); font-size: 11px; }
</style>
