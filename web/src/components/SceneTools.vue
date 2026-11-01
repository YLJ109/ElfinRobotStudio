<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { parseResponse } from '../connectionState.js'
import { alignedPosition, gridArrayPreview, readSceneStatus, sceneActionBody, sceneEditBlock, TEMPLATE_LABELS } from '../sceneTools.js'

const props = defineProps({ active: { type:Boolean, default:true } })
const w = useWorld()
const { snap, selObj } = storeToRefs(w)
const status = ref(null), busy = ref(false), message = ref(''), failed = ref(false)
const counts = ref([2,2,1]), spacing = ref([200,200,0]), template = ref('')
const step = ref(50), axes = ref([true,true,false]), alignMode = ref('floor')
const objects = computed(() => snap.value?.objects || [])
const source = computed(() => objects.value.find(o => o.id === selObj.value) || null)
const block = computed(() => sceneEditBlock(snap.value, w.connected))
const arrayPreview = computed(() => {
  try { return { value:gridArrayPreview(source.value, counts.value, spacing.value, objects.value.length), error:'' } }
  catch(e) { return { value:null, error:e.message } }
})
const alignment = computed(() => {
  try { return { xyz:alignedPosition(source.value, alignMode.value, step.value, axes.value), error:'' } }
  catch(e) { return { xyz:null, error:e.message } }
})
const alignChanged = computed(() => alignment.value.xyz && source.value?.xyz.some((v,i) => Math.abs(v - alignment.value.xyz[i]) > 1e-8))
let request = null, disposed = false
const prefix = '/api/workspace/scene'
async function readStatus() {
  const controller = new AbortController(); request = controller
  try {
    const value = readSceneStatus(await parseResponse(await fetch(prefix, {signal:controller.signal}), prefix))
    if (disposed || controller.signal.aborted) return false
    status.value = value
    if (!value.templates.includes(template.value)) template.value = value.templates[0] || ''
    return true
  } finally { if (request === controller) request = null }
}
async function refresh() {
  if (busy.value || !props.active || disposed) return
  busy.value = true; failed.value = false; message.value = ''
  try { if (await readStatus()) message.value = '已读取现场版本及批量操作历史。' }
  catch(e) { if (!disposed && e.name !== 'AbortError') { status.value = null; failed.value = true; message.value = `场景工具暂不可用：${e.message}` } }
  finally { busy.value = false }
}
watch(() => props.active, active => { if (active && !status.value) refresh(); else if (!active) request?.abort() }, {immediate:true})
onBeforeUnmount(() => { disposed = true; request?.abort() })
async function act(action) {
  if (busy.value || block.value || !props.active || disposed || !status.value) return
  busy.value = true; failed.value = false; message.value = ''
  try {
    const body = sceneActionBody(action, status.value, {template:template.value,id:selObj.value,counts:counts.value,spacing:spacing.value,
      mode:alignMode.value,step:step.value,axes:axes.value})
    const path = `${prefix}/${action}`
    // Never retry writes automatically: a lost response does not prove failure.
    const value = readSceneStatus(await parseResponse(await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),path))
    if (disposed) return
    status.value = value
    if (selObj.value && !value.objects.some(o => o.id === selObj.value)) w.selObj = null
    message.value = `${{template:'模板已追加',array:'数组已生成',align:'对齐已由服务端确认（位置变化时进入批量历史）',undo:'已撤销场景操作',redo:'已重做场景操作'}[action]}；现场未自动保存为文件。`
  } catch(e) {
    if (disposed) return
    failed.value = true; message.value = `${e.message}；未自动重试，请读取现态后核对。`
    status.value = null
    // Keep the failure visible; recovery is an explicit read, never another write.
  } finally { busy.value = false }
}
async function align() {
  if (!alignChanged.value || !source.value) return
  await act('align')
}
</script>

<template>
  <div class="scene-tools col" @keydown.space.stop>
    <div class="note tiny">摆放、模板、数组、对齐及物体编辑共用32步撤销/重做；服务重启后不保留。清空、载入现场或外部修改会使旧历史失效。</div>
    <div class="row"><button type="button" class="btn" :disabled="busy" @click="refresh">{{ busy ? '处理中…' : '读取现态 / 历史' }}</button><span class="tiny dim">无后台轮询；其他修改后需重新读取。</span></div>
    <div v-if="message" class="note" :class="failed ? 'bad' : ''" :role="failed ? 'alert' : 'status'">{{ message }}</div>
    <div v-if="block" class="note warn" role="status">{{ block }}。批量修改与对齐尚未开放，服务端仍会做最终检查。</div>
    <div v-if="!status" class="note">尚无可用的场景工具响应，模板、数组、对齐和历史操作未开放。</div>
    <template v-else>
      <section class="panel">
        <div class="panel-hd"><span class="panel-title">场景模板</span><span class="panel-sub">追加到当前现场</span></div>
        <div class="panel-bd"><label class="field">模板<select class="select" v-model="template" :disabled="busy"><option v-for="id in status.templates" :key="id" :value="id">{{ TEMPLATE_LABELS[id] || id }}</option></select></label>
          <p class="tiny dim">保留已有物体；工作台、托盘为仿真几何，不代表现场尺寸或安全验证。</p>
          <button v-if="status.templates.length" type="button" class="btn" :disabled="busy || !!block || status.objects.length >= 1000" @click="act('template')">追加所选模板</button>
          <span v-else class="note">服务端当前未提供模板。</span>
        </div>
      </section>
      <section class="panel">
        <div class="panel-hd"><span class="panel-title">网格数组</span><span class="panel-sub">数量含源物体</span></div>
        <div class="panel-bd">
          <label class="field">选中源物体<select class="select" :value="selObj || ''" :disabled="busy" @change="w.selObj = $event.target.value || null"><option value="">请选择</option><option v-for="o in objects" :key="o.id" :value="o.id">{{ o.name }} · {{ o.id }}</option></select></label>
          <div class="grid-params"><span>轴</span><span>数量</span><span>中心间距 mm</span><template v-for="(axis,i) in ['X','Y','Z']" :key="axis"><b>{{ axis }}</b><input class="input" type="number" min="1" max="12" step="1" v-model.number="counts[i]" :disabled="busy" :aria-label="`${axis}轴数组数量`" /><input class="input" type="number" step="10" v-model.number="spacing[i]" :disabled="busy" :aria-label="`${axis}轴数组间距毫米`" /></template></div>
          <div v-if="arrayPreview.error" class="note">{{ arrayPreview.error }}</div>
          <div v-else class="note tiny">新增 {{ arrayPreview.value.added }} 个，数组共 {{ arrayPreview.value.total }} 个（含原物体）。中心范围 {{ arrayPreview.value.min.join(', ') }} → {{ arrayPreview.value.max.join(', ') }} mm。参数预览不写入现场；间距是中心距，不保证无重叠或安全。</div>
          <button type="button" class="btn btn--primary" :disabled="busy || !!block || !!arrayPreview.error" @click="act('array')">生成数组</button>
        </div>
      </section>
      <section class="panel"><div class="panel-hd"><span class="panel-title">编辑历史</span></div><div class="panel-bd"><div class="row"><button type="button" class="btn" :disabled="busy || !!block || !status.can_undo" @click="act('undo')">撤销场景操作</button><button type="button" class="btn" :disabled="busy || !!block || !status.can_redo" @click="act('redo')">重做场景操作</button></div><span class="tiny dim">以最近一次读取的现场版本为准；版本冲突会拒绝操作并要求重新读取，不覆盖其他编辑。</span></div></section>
    </template>
    <section v-if="status" class="panel">
      <div class="panel-hd"><span class="panel-title">选中物体对齐</span><span class="panel-sub">原子修改 · 支持编辑历史</span></div>
      <div class="panel-bd">
        <span class="tiny">{{ source ? `当前选中：${source.name}` : '请在物体页或三维中选择物体' }}</span>
        <label class="field">方式<select class="select" v-model="alignMode" :disabled="busy"><option value="floor">几何底面对齐地面 Z=0</option><option value="grid">中心吸附基座网格</option></select></label>
        <div v-if="alignMode === 'grid'" class="row"><label class="field">网格步距 mm<input class="input" type="number" min="0.001" step="10" v-model.number="step" :disabled="busy" /></label><label v-for="(axis,i) in ['X','Y','Z']" :key="axis" class="axis-check"><input type="checkbox" v-model="axes[i]" :disabled="busy" />{{ axis }}</label></div>
        <div class="note tiny">{{ alignment.error || `位置 ${source.xyz.join(', ')} → ${alignment.xyz.join(', ')} mm` }}。仅改几何位置，不关闭重力、不代表碰撞检查通过。</div>
        <button type="button" class="btn" :disabled="busy || !!block || !!alignment.error || !alignChanged" @click="align">应用选中物体对齐</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.scene-tools { min-width:0; }
.field { display:flex; flex-direction:column; gap:4px; min-width:0; font-size:12px; }
.grid-params { display:grid; grid-template-columns:18px minmax(0,1fr) minmax(0,1.5fr); align-items:center; gap:6px; font-size:11px; }
.grid-params .input { width:100%; min-width:0; }
.axis-check { display:flex; align-items:center; gap:3px; font-size:12px; }
.note { overflow-wrap:anywhere; }
.scene-tools p { margin:0; }
</style>
