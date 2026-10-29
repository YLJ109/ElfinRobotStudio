<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { ROBOT_MODELS, DEFAULT_VACUUM, BUILTIN_TOOLS, vacuumDimensions, loadToolCatalog } from '../visualAssets.js'

// selectedToolId is the authoritative active tool: catalog id, vacuum-reference,
// or none. A draft selection never claims to be mounted until the parent updates
// this prop after the common tool-switch operation has succeeded.
const props = defineProps({ busy: Boolean, toolLocked: Boolean, selected: String, toolName: String, skin: String,
  selectedToolId: { type: String, default: 'vacuum-reference' }, selectedToolRotation: Array })
const emit = defineEmits(['robot', 'tool', 'skin', 'rotation'])
const open = ref(localStorage.getItem('huayan.models.open') === '1'), error = ref('')
const catalog = ref([]), catalogLoading = ref(false), catalogError = ref('')
const choice = ref(props.selectedToolId), alive = { value: true }
const options = computed(() => [...BUILTIN_TOOLS, ...catalog.value])
const selectedEntry = computed(() => catalog.value.find((entry) => entry.id === choice.value))
const currentName = computed(() => props.toolName || options.value.find((entry) => entry.id === props.selectedToolId)?.name || '未识别工具')
const dimensions = ref({ ...DEFAULT_VACUUM })
const rotation = ref([0, 0, 90])
try {
  const saved = JSON.parse(localStorage.getItem('huayan.visualTool') || 'null')
  if (saved?.orientationVersion === 2 && Array.isArray(saved.rotation)) rotation.value = saved.rotation.slice()
  if (saved?.kind === 'vacuum-reference') dimensions.value = vacuumDimensions(saved.dimensions)
} catch { /* ignore corrupt display settings */ }
function validRotation() {
  if (rotation.value.length !== 3 || !rotation.value.every((v) => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 360)) {
    throw new Error('安装角必须是 -360°～360° 的有效数字')
  }
  return rotation.value.slice()
}
function loadPhotoDimensions() {
  if (props.busy || props.toolLocked) return
  dimensions.value = { ...DEFAULT_VACUUM }
}
function rotationFor(id) {
  return catalog.value.find((entry) => entry.id === id)?.default_rotation_deg.slice() ||
    (id === 'vacuum-reference' ? [0, 0, 90] : [0, 0, 0])
}
watch(choice, (id) => { rotation.value = id === props.selectedToolId && props.selectedToolRotation?.length === 3
  ? props.selectedToolRotation.slice() : rotationFor(id); error.value = '' })
watch(() => props.selectedToolId, (id) => { choice.value = id; rotation.value = props.selectedToolRotation?.slice() || rotationFor(id) })
watch(() => props.selectedToolRotation, (value) => {
  if (choice.value === props.selectedToolId && value?.length === 3) rotation.value = value.slice()
}, { deep: true })
async function refreshCatalog() {
  if (catalogLoading.value) return
  catalogLoading.value = true; catalogError.value = ''
  try {
    const result = await loadToolCatalog()
    if (alive.value) {
      catalog.value = result.tools
      if (choice.value === props.selectedToolId) rotation.value = props.selectedToolRotation?.slice() || rotationFor(choice.value)
    }
  } catch (e) { if (alive.value) catalogError.value = e.message || '工具目录加载失败' }
  finally { if (alive.value) catalogLoading.value = false }
}
watch(open, (v) => {
  localStorage.setItem('huayan.models.open', v ? '1' : '0')
  if (v && !catalog.value.length) void refreshCatalog()
}, { immediate: true })
onBeforeUnmount(() => { alive.value = false })
function switchTool() {
  if (props.busy || props.toolLocked) return
  try {
    error.value = ''
    if (choice.value === 'none') { emit('tool', null); return }
    const nextRotation = validRotation()
    if (choice.value === 'vacuum-reference') {
      emit('tool', { kind: 'vacuum-reference', dimensions: vacuumDimensions(dimensions.value),
        rotation: nextRotation, orientationVersion: 2, name: '双吸盘参考模型 · 尺寸待核' })
      return
    }
    const entry = selectedEntry.value
    if (!entry) throw new Error('请先加载并选择已安装的末端工具')
    // Canonical TCP/bounds come from the trusted catalog, not from this event.
    emit('tool', { kind: 'library', id: entry.id, rotation: nextRotation })
  } catch (e) { error.value = e.message }
}
function applyRotation() {
  if (props.busy || props.toolLocked || choice.value !== props.selectedToolId || choice.value === 'none') return
  try { emit('rotation', validRotation()); error.value = '' } catch (e) { error.value = e.message }
}
</script>

<template>
  <div class="model-library no-fold">
    <button class="btn btn--xs" :aria-expanded="open" @click="open = !open">模型 / 工具</button>
    <section v-if="open" class="library" aria-label="模型与末端工具库">
      <div class="row"><b>模型与末端工具</b><span class="grow"></span><button class="btn btn--xs" aria-label="关闭模型库" @click="open = false">×</button></div>
      <label>机器人显示模型<select aria-label="机器人显示模型" :value="selected" :disabled="busy" @change="emit('robot', $event.target.value)"><option v-for="m in ROBOT_MODELS" :key="m.id" :value="m.id">{{ m.name }}</option></select></label>
      <label>机器人皮肤<select aria-label="机器人皮肤" :value="skin" :disabled="busy" @change="emit('skin', $event.target.value)"><option value="cyber">科技金属</option><option value="factory">原厂金属</option></select></label>
      <p v-if="selected !== 'elfin5'" class="warn">仅切换三维外观，不是当前真机数字孪生；运动学、限位和碰撞模型仍为 Elfin 5。</p>
      <a href="https://github.com/huayan-robotics/elfin_robot" target="_blank" rel="noopener noreferrer">华沿官方 URDF 模型来源 · BSD-3-Clause</a>
      <hr />
      <p v-if="toolLocked" class="warn">请先停止当前运动，再切换或调整工具。</p>
      <fieldset class="tool-fields" :disabled="busy || toolLocked">
      <b>已安装末端工具</b>
      <label>选择工具<select v-model="choice" aria-label="选择末端工具">
        <option v-for="entry in options" :key="entry.id" :value="entry.id">{{ entry.name }}{{ entry.id === selectedToolId ? ' · 当前' : '' }}</option>
      </select></label>
      <p v-if="catalogLoading" role="status">正在读取本地工具目录…</p>
      <div v-if="catalogError" class="catalog-error"><p class="bad" role="alert">{{ catalogError }}</p><button class="btn btn--xs" @click="refreshCatalog">重试目录</button></div>
      <template v-if="selectedEntry">
        <img class="tool-preview" :src="selectedEntry.preview" :alt="selectedEntry.name + ' 开源模型预览'" />
        <p>{{ selectedEntry.name }} · 银白／黑色金属与橡胶 · {{selectedEntry.pose==='articulated-simulation'?'仿真开合动画':'固定参考姿态'}}</p>
        <p>参考 TCP / mm：{{ selectedEntry.tcp_mm.map(v => v.toFixed(1)).join(' / ') }}。来自模型装配，非实物标定。</p>
        <p><a :href="selectedEntry.source" target="_blank" rel="noopener noreferrer">模型来源</a> · <a :href="selectedEntry.license_file" target="_blank" rel="noopener noreferrer">{{ selectedEntry.license }}</a></p>
      </template>
      <template v-if="choice === 'vacuum-reference'">
      <b>双吸盘参考尺寸</b>
      <p>实拍参考：下方连接段 40 mm，两吸盘总长各 30 mm；上方约 50 mm 腕部由机器人模型保留，不重复加长。气动接头、气管与吸盘直径为外观参考，非厂家 CAD。</p>
      <button class="btn btn--xs" @click="loadPhotoDimensions">载入实拍尺寸 · 连接段40 / 吸盘30 mm</button>
      <div class="dimensions">
        <label v-for="[key, text] in [['length','板长'],['width','板宽'],['thickness','板厚'],['edge','吸盘中心距端'],['cupHeight','吸盘总长'],['cupDiameter','吸盘直径（待测）'],['mountHeight','下方连接段高度']]" :key="key">{{ text }} / mm<input v-model.number="dimensions[key]" type="number" min="0" step="1" /></label>
      </div>
      </template>
      <template v-if="choice !== 'none'">
      <b>工具安装角</b>
      <div class="dimensions"><label v-for="(axis, i) in ['Rx','Ry','Rz']" :key="axis">{{ axis }} / °<input v-model.number="rotation[i]" type="number" min="-360" max="360" step="1" /></label></div>
      <div class="row"><button v-for="(axis, i) in ['Rx','Ry','Rz']" :key="axis" class="btn btn--xs" :disabled="busy" @click="rotation[i] = rotation[i] === 180 ? 0 : 180; applyRotation()">{{ axis }} 翻转180°</button></div>
      <button v-if="choice === selectedToolId" class="btn btn--xs" :disabled="busy" @click="applyRotation">应用当前工具角度</button>
      </template>
      <button class="btn primary" :disabled="busy || (choice !== 'none' && choice !== 'vacuum-reference' && !selectedEntry)" @click="switchTool">{{ choice === 'vacuum-reference' ? '应用末端尺寸' : '切换所选工具' }}</button>
      <p class="dim" role="status">当前工具：{{ currentName }}</p>
      <p class="warn">仿真由统一工具流程同步模型与参考 TCP。真机仅显示，不写入控制器 TCP、负载或 IO；实物安装与标定须单独确认。</p>
      </fieldset>
      <p v-if="error" class="bad" role="alert">{{ error }}</p>
    </section>
  </div>
</template>

<style scoped>
.tool-fields{border:0;margin:0;padding:0;min-width:0;display:flex;flex-direction:column;gap:9px}
.tool-preview{width:100%;height:150px;object-fit:contain;border:1px solid var(--line);border-radius:4px;background:var(--bg)}.catalog-error{display:flex;align-items:center;gap:8px}
.model-library{position:relative}.library{position:absolute;bottom:34px;left:0;width:340px;max-width:calc(100vw - 36px);max-height:70vh;overflow:auto;display:flex;flex-direction:column;gap:9px;padding:13px;background:var(--bg-panel,#0c1420);border:1px solid var(--line-2);border-radius:8px;box-shadow:0 12px 45px #0008;text-shadow:none;z-index:20}.library b{color:var(--tx-1)}.library label{display:flex;flex-direction:column;gap:4px;font-size:11px}.library p,.library a{font-size:11px;line-height:1.6;margin:0;overflow-wrap:anywhere}.library a{color:var(--acc)}.library input,.library select{width:100%;min-width:0;box-sizing:border-box}.dimensions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.library hr{width:100%;border:0;border-top:1px solid var(--line);margin:2px 0}
</style>
