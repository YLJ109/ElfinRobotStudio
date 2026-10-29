<script setup>
import { computed, reactive, ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { createObjectTeaching, objectTeachingContext } from '../objectTeaching.js'
import { OBJECT_SURFACES, objectLabel } from '../objectCatalog.js'

const w = useWorld()
const { snap, selObj } = storeToRefs(w)
const obj = computed(() => ((snap.value && snap.value.objects) || []).find((o) => o.id === selObj.value) || null)
const hit = computed(() => ((snap.value && snap.value.obj_hits) || []).find((h) => h.id === selObj.value) || null)
const draft = reactive({ id: null, xyz: [], size: [] })
const moveEnabled = ref(true), sceneRevision = ref(0)
const move = reactive({})
const moveContext = computed(() => objectTeachingContext(w))
const editor = createObjectTeaching({ state: move, save: (id, patch) => w.patchObject(id, patch),
  allowed: (id, context, scene) => id === selObj.value && obj.value?.id === id && context === moveContext.value
    && w.view === 'objects' && moveEnabled.value && scene === window.__elfinScene })
const editing = computed(() => move.dragging || move.busy)
const xyzShown = computed(() => move.xyz || obj.value?.xyz || [0, 0, 0])
function syncMove() {
  const scene = window.__elfinScene
  if (obj.value && w.view === 'objects' && moveEnabled.value && moveContext.value) {
    editor.attach(scene, obj.value.id, moveContext.value)
  } else editor.detach()
}
function sceneReady() { sceneRevision.value++ }
function objectsReady() { if(!move.active && !editing.value)sceneReady() }
watch([() => obj.value?.id, () => w.view, moveContext, moveEnabled, sceneRevision], syncMove, { immediate: true, flush: 'post' })
watch(() => obj.value?.id, () => { draft.id = null; draft.xyz = []; draft.size = [] })
async function toggleMove() {
  if (w.view !== 'objects') { moveEnabled.value = true; w.view = 'objects'; await nextTick(); return }
  if(moveEnabled.value && !move.active) { sceneReady();return }
  moveEnabled.value = !moveEnabled.value
}
onMounted(() => { window.addEventListener('elfin-scene-ready', sceneReady);window.addEventListener('elfin-objects-ready',objectsReady) })
onBeforeUnmount(() => { editor.detach(); window.removeEventListener('elfin-scene-ready', sceneReady);window.removeEventListener('elfin-objects-ready',objectsReady) })
function begin(kind, i, value) {
  if (!obj.value) return
  draft.id = obj.value.id
  draft[kind] = obj.value[kind].slice()
  draft[kind][i] = value
}
function commit(kind, i, value) {
  if (!obj.value || editing.value || draft.id !== obj.value.id) return
  const values = (draft[kind].length ? draft[kind] : obj.value[kind].slice()).slice()
  values[i] = Number(value)
  if (!Number.isFinite(values[i])) return
  draft[kind] = []
  if (values[i] === obj.value[kind][i]) return
  kind === 'xyz' ? setAxis(i, values[i]) : setSize(i, values[i])
}

async function put(patch) {
  if (!obj.value || editing.value) return
  const id = obj.value.id
  try {
    const result = await w.patchObject(id, patch)
    if (result?.ok !== true) throw new Error(result?.why || '物体更新失败')
  } catch (error) { w.error = error.message }
}
function setAxis(i, v) {
  if (!obj.value) return
  const xyz = obj.value.xyz.slice()
  xyz[i] = Number(v)
  put({ xyz })
}
function setSize(i, v) {
  if (!obj.value) return
  const size = obj.value.size.slice()
  size[i] = Math.max(1, Number(v))
  put({ size })
}
async function del() {
  if (!obj.value || editing.value) return
  const id = obj.value.id
  try {
    const result = await w.removeObject(id)
    if (result?.ok !== true) throw new Error(result?.why || '物体删除失败')
    if (w.selObj === id) w.selObj = null
  } catch (error) { w.error = error.message }
}
const AX = ['X', 'Y', 'Z']
</script>

<template>
  <section v-if="obj" class="panel corner props">
    <div class="panel-hd">
      <span class="panel-title">物体属性</span>
      <span class="panel-sub ellip">{{ obj.name }} · {{ objectLabel(obj) }}</span>
      <span class="grow"></span>
      <button class="btn btn--xs no-fold" title="关闭" @click="w.selObj = null">×</button>
    </div>
    <div class="panel-bd">
      <div class="row nowrap">
        <button class="btn btn--xs" :class="{ 'btn--primary': move.active }" :aria-pressed="move.active"
                :disabled="!moveContext || editing" @click="toggleMove">{{ move.active ? '移动轴已开启' : '开启三维移动轴' }}</button>
        <button v-if="move.dragging" class="btn btn--xs" @click="editor.cancel()">取消移动</button>
        <span v-if="move.busy" class="dim tiny" role="status">保存中…</span>
      </div>
      <div v-if="move.active && !move.error" class="dim tiny">拖动三维坐标轴，松开保存一次。</div>
      <div v-else-if="!moveContext" class="dim tiny">三维移动仅在未连接真机的仿真会话开放。</div>
      <div v-else-if="moveEnabled && !move.active" class="dim tiny">等待三维物体就绪，可点“开启三维移动轴”重试。</div>
      <div v-if="move.error" class="note bad" role="alert">{{ move.error }}</div>
      <fieldset class="object-fields" :disabled="editing">
      <label class="row">名称<input class="input grow" maxlength="24" :value="obj.name" @change="put({name:$event.target.value})" /></label>
      <div class="lbl dim tiny">位置 mm（基座坐标，Z 朝上）</div>
      <div class="row nowrap" v-for="(a, i) in AX" :key="'p' + a">
        <span class="ax disp">{{ a }}</span>
        <input class="input mono grow" type="number" step="10" :aria-label="'物体 ' + a + ' 毫米'" :value="draft.xyz.length ? draft.xyz[i] : xyzShown[i].toFixed(0)"
               @focus="begin('xyz', i, $event.target.value)" @input="draft.xyz[i] = $event.target.value"
               @blur="commit('xyz', i, $event.target.value)" @keydown.enter.prevent="$event.target.blur()" />
      </div>
      <div class="lbl dim tiny">{{ obj.kind === 'sphere' ? '半径 mm' : obj.kind === 'cylinder' ? '半径 / 高度 mm' : 'X / Y / Z 尺寸 mm' }}</div>
      <div class="row nowrap">
        <span class="ax disp">{{ obj.kind==='box'?'S':'R' }}</span>
        <input class="input mono grow" type="number" min="1" step="10" :value="draft.size.length ? draft.size[0] : obj.size[0].toFixed(0)"
               @focus="begin('size', 0, $event.target.value)" @input="draft.size[0] = $event.target.value"
               @blur="commit('size', 0, $event.target.value)" @keydown.enter.prevent="$event.target.blur()" />
        <input v-if="obj.kind === 'box'" class="input mono grow" type="number" min="1" step="10"
               :value="draft.size.length ? draft.size[1] : obj.size[1].toFixed(0)"
               @focus="begin('size', 1, $event.target.value)" @input="draft.size[1] = $event.target.value"
               @blur="commit('size', 1, $event.target.value)" @keydown.enter.prevent="$event.target.blur()" />
        <input v-if="obj.kind !== 'sphere'" class="input mono grow" type="number" min="1" step="10"
               :value="draft.size.length ? draft.size[2] : obj.size[2].toFixed(0)"
               @focus="begin('size', 2, $event.target.value)" @input="draft.size[2] = $event.target.value"
               @blur="commit('size', 2, $event.target.value)" @keydown.enter.prevent="$event.target.blur()" />
      </div>

      <label class="row">材质<select class="select grow" :value="obj.surface || 'painted'" @change="put({surface:$event.target.value})"><option v-for="(surface,key) in OBJECT_SURFACES" :key="key" :value="key">{{ surface.label }}</option></select></label>
      <details class="tiny dim"><summary>碰撞代理</summary><p>{{ obj.kind }} 保守几何；工业外观的孔洞、滚筒间隙不参与精确网格碰撞。</p></details>

      <label class="sw"><input type="checkbox" :checked="obj.gravity" @change="put({ gravity: $event.target.checked })" />
        <span>重力<span class="dim tiny">9.81 m/s²</span></span></label>
      <div class="dim tiny">地面约束已开启，物体底部不会穿过 Z=0。</div>
      <label class="sw"><input type="checkbox" :checked="obj.floating" @change="put({ floating: $event.target.checked })" />
        <span>漂浮<span class="dim tiny">不受重力</span></span></label>
      <label class="sw"><input type="checkbox" :checked="obj.collide" @change="put({ collide: $event.target.checked })" />
        <span>与机器人碰撞检测</span></label>

      <div class="row nowrap">
        <span class="ax disp">色</span>
        <input class="swatch" type="color" :value="obj.color" @change="put({ color: $event.target.value })" />
        <span class="grow"></span>
        <span class="tag tag--mute mono">Vz {{ obj.vz === undefined ? '--' : obj.vz }}</span>
      </div>

      <div v-if="hit" class="note bad">
        与 <b class="mono">{{ hit.link }}</b> 的碰撞包络重叠 {{ Math.abs(hit.gap_mm).toFixed(1) }} mm。
      </div>
      
      <button class="btn btn--danger" @click="del">删除该物体</button>
      </fieldset>
    </div>
  </section>
</template>

<style scoped>
.props { max-height: calc(100% - 92px); overflow: auto; }
.object-fields { display:flex; flex-direction:column; gap:8px; border:0; margin:0; padding:0; min-width:0; }
.lbl { margin-top: 2px; }
.ax { width: 14px; font-size: 11.5px; color: var(--tx-3); }
.input { font-size: 12px; padding: 3px 6px; }
.sw { display: flex; align-items: center; gap: 6px; font-size: 12.5px; cursor: pointer; }
.sw .dim { margin-left: 4px; }
.swatch { width: 34px; height: 22px; padding: 0; border: 1px solid var(--line);
          border-radius: 0; background: none; }
</style>
