<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
const props = defineProps({ session: String })
const host = ref(null), message = ref('等待真实深度点云…'), count = ref(0)
let renderer, scene, camera, controls, cloud, timer, animation, controller, observer, disposed = false, fitted = false
async function poll() {
  if (disposed) return
  controller = new AbortController()
  try {
    const data = await (await fetch('/api/cameras/pointcloud?session=' + encodeURIComponent(props.session), { signal: controller.signal, cache: 'no-store' })).json()
    if (disposed) return
    if (!data.ok) { message.value = data.why; cloud.visible = false; count.value = 0 }
    else {
      const geometry = new THREE.BufferGeometry()
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.xyz_m.flat(), 3))
      geometry.setAttribute('color', new THREE.Float32BufferAttribute((data.rgb || data.xyz_m.map(() => [0.3,0.8,0.9])).flat(), 3))
      cloud.geometry.dispose(); cloud.geometry = geometry; cloud.visible = true
      if (!fitted && data.xyz_m.length) {
        geometry.computeBoundingSphere()
        const sphere = geometry.boundingSphere
        controls.target.copy(sphere.center)
        camera.position.copy(sphere.center).add(new THREE.Vector3(0, -0.1, -Math.max(0.5, sphere.radius * 2.5)))
        controls.update(); fitted = true
      }
      message.value = ''; count.value = data.xyz_m.length
    }
  } catch(e) { if (!disposed && e.name !== 'AbortError') { message.value = e.message; cloud.visible = false; count.value = 0 } }
  finally { if (!disposed) timer = setTimeout(poll, 350) }
}
onMounted(() => {
  try {
    scene = new THREE.Scene(); scene.background = new THREE.Color('#070e16')
    camera = new THREE.PerspectiveCamera(55, 1, 0.01, 100); camera.up.set(0,-1,0)
    renderer = new THREE.WebGLRenderer({ antialias: true }); renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5)); host.value.appendChild(renderer.domElement)
    controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true
    cloud = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial({ size: 0.008, vertexColors: true })); scene.add(cloud)
    observer = new ResizeObserver(() => { const width = host.value.clientWidth; renderer.setSize(width, 210); camera.aspect = width / 210; camera.updateProjectionMatrix() }); observer.observe(host.value)
    const render = () => { if (disposed) return; controls.update(); renderer.render(scene, camera); animation = requestAnimationFrame(render) }; render(); poll()
  } catch(e) { message.value = '三维预览不可用：' + e.message }
})
onBeforeUnmount(() => { disposed = true; controller?.abort(); clearTimeout(timer); cancelAnimationFrame(animation); observer?.disconnect(); controls?.dispose(); cloud?.geometry.dispose(); cloud?.material.dispose(); renderer?.dispose(); renderer?.forceContextLoss() })
function exportPLY() {
  if (!cloud?.visible) return
  const p = cloud.geometry.getAttribute('position'), c = cloud.geometry.getAttribute('color')
  const lines = ['ply','format ascii 1.0', 'comment frame camera_optical units meters', `element vertex ${p.count}`, 'property float x','property float y','property float z','property uchar red','property uchar green','property uchar blue','end_header']
  for (let i=0; i<p.count; i++) lines.push(`${p.getX(i)} ${p.getY(i)} ${p.getZ(i)} ${Math.round(c.getX(i)*255)} ${Math.round(c.getY(i)*255)} ${Math.round(c.getZ(i)*255)}`)
  const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/plain' })), a = document.createElement('a'); a.href = url; a.download = 'camera-pointcloud.ply'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>
<template><div class="cloud"><div class="cloud-head"><b>实时深度点云</b><span>{{ count }} 点 · m</span><button class="btn btn--xs" :disabled="!count" @click="exportPLY">导出 PLY</button></div><div ref="host" class="cloud-stage"></div><small>{{ message || '拖动旋转 · 滚轮缩放 · 相机坐标系' }}</small></div></template>
<style scoped>.cloud { margin-top: 9px; padding: 6px; border: 1px solid var(--line-2); border-radius: 5px; }.cloud-head { display: flex; align-items: center; gap: 7px; font-size: 10px; }.cloud-head span { flex: 1; color: var(--tx-3); }.cloud-stage { height: 210px; overflow: hidden; margin-top: 5px; }small { color: var(--tx-3); font-size: 10px; }</style>
