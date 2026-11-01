<script setup>
import { computed, reactive, ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { createObjectTeaching, objectTeachingContext } from '../objectTeaching.js'
import { OBJECT_SURFACES, objectLabel } from '../objectCatalog.js'
import { createTextDraft } from '../textDraft.js'

const w = useWorld()
const { snap, selObj } = storeToRefs(w)
const obj = computed(() => ((snap.value && snap.value.objects) || []).find((o) => o.id === selObj.value) || null)
const hit = computed(() => ((snap.value && snap.value.obj_hits) || []).find((h) => h.id === selObj.value) || null)
const draft = reactive({ id: null, xyz: [], size: [], rotation_deg: [] })
const scaleDraft = reactive({id:null,value:''})
const nameState = reactive({})
const nameEditor = createTextDraft({state:nameState,save:(id,name)=>w.editObject(id,{name}),maxLength:24})
const nameDraft = computed(()=>nameEditor.entry())
watch(()=>[obj.value?.id,obj.value?.name],()=>nameEditor.sync(obj.value?.id ?? null,obj.value?.name || ''),{immediate:true})
const moveEnabled = ref(true), sceneRevision = ref(0)
const propertySaving = ref(false)
const move = reactive({})
const moveContext = computed(() => objectTeachingContext(w))
const editor = createObjectTeaching({ state: move, save: (id, patch) => w.editObject(id, patch),
  allowed: (id, context, scene) => id === selObj.value && obj.value?.id === id && context === moveContext.value
    && !obj.value?.locked && !propertySaving.value && !nameDraft.value?.saving && w.view === 'objects' && moveEnabled.value && scene === window.__elfinScene })
const editing = computed(() => move.dragging || move.busy || propertySaving.value || nameDraft.value?.saving)
const waitingForObject = computed(() => w.view === 'objects' && !obj.value?.locked && moveEnabled.value && !move.active)
const xyzShown = computed(() => move.xyz || obj.value?.xyz || [0, 0, 0])
function syncMove() {
  const scene = window.__elfinScene
  if (obj.value && !obj.value?.locked && !propertySaving.value && !nameDraft.value?.saving && w.view === 'objects' && moveEnabled.value && moveContext.value) {
    editor.attach(scene, obj.value.id, moveContext.value)
  } else editor.detach()
}
function sceneReady() { sceneRevision.value++ }
function objectsReady() { if(!move.active && !editing.value)sceneReady() }
watch([() => obj.value?.id, () => obj.value?.locked, () => w.view, moveContext, moveEnabled, sceneRevision], syncMove, { immediate: true, flush: 'post' })
watch(() => obj.value?.id, () => { draft.id = null; draft.xyz = []; draft.size = []; draft.rotation_deg = []; scaleDraft.id=null;scaleDraft.value='' })
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
  draft[kind] = (obj.value[kind] || [0,0,0]).slice()
  draft[kind][i] = value
}
async function commit(kind, i, value) {
  if (!obj.value || editing.value || draft.id !== obj.value.id) return
  const id=obj.value.id
  const values = (draft[kind].length ? draft[kind] : (obj.value[kind] || [0,0,0]).slice()).slice()
  if(String(value).trim()===''){w.error='数值不能为空';return}
  values[i] = Number(value)
  if (!Number.isFinite(values[i]) || (kind==='size' && values[i]<1)){w.error='请输入有效数值，尺寸至少1毫米';return}
  if (values[i] === (obj.value[kind] || [0,0,0])[i]) {draft[kind]=[];return}
  propertySaving.value=true
  try{
    const result=await w.editObject(id,{[kind]:values})
    if(result?.ok!==true)throw new Error(result?.why||'物体更新失败')
    if(draft.id===id)draft[kind]=[]
  }catch(error){if(!error.requestHandled)w.error=error.message}
  finally{propertySaving.value=false}
}

async function put(patch) {
  if (!obj.value || editing.value) return
  const id = obj.value.id
  propertySaving.value=true
  try {
    const result = await w.editObject(id, patch)
    if (result?.ok !== true) throw new Error(result?.why || '物体更新失败')
    return result
  } catch (error) { if(!error.requestHandled)w.error = error.message }
  finally { propertySaving.value=false }
}
async function commitScale(value) {
  if(!obj.value || scaleDraft.id!==obj.value.id || editing.value)return
  if(String(value).trim()==='' || !Number.isFinite(Number(value)) || Number(value)<.01 || Number(value)>100){w.error='等比缩放请输入0.01～100';return}
  const id=obj.value.id,result=await put({scale:Number(value)})
  if(result?.ok && scaleDraft.id===id){scaleDraft.id=null;scaleDraft.value=''}
}
async function duplicate() {
  if(!obj.value || obj.value.locked || obj.value.task || editing.value)return
  const id=obj.value.id;propertySaving.value=true
  try {
    const result=await w.duplicateSceneObject(id)
    if(!result?.ok || !result.object?.id)throw Error(result?.why || '复制未确认')
    if(w.selObj===id)w.selObj=result.object.id
  }catch(error){w.error=error.message}
  finally{propertySaving.value=false}
}
async function del() {
  if (!obj.value || obj.value.locked || editing.value) return
  const id = obj.value.id;propertySaving.value=true
  try {
    const result = await w.removeSceneObject(id)
    if (result?.ok !== true) throw new Error(result?.why || '物体删除失败')
    if (w.selObj === id) w.selObj = null
  } catch (error) { w.error = error.message }
  finally{propertySaving.value=false}
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
                :disabled="!moveContext || editing || obj.locked" @click="toggleMove">{{ move.active ? '移动轴已开启' : '开启三维移动轴' }}</button>
        <button v-if="move.dragging" class="btn btn--xs" @click="editor.cancel()">取消移动</button>
        <span v-if="move.busy" class="dim tiny" role="status">保存中…</span>
      </div>
      <div v-if="move.active && !move.error" class="dim tiny">拖动三维坐标轴，松开保存一次。</div>
      <div v-else-if="!moveContext" class="dim tiny">三维移动仅在未连接真机的仿真会话开放。</div>
      <div v-else-if="waitingForObject" class="dim tiny">等待三维物体就绪，可点“开启三维移动轴”重试。</div>
      <div v-if="move.error" class="note bad" role="alert">{{ move.error }}</div>
      <button class="btn btn--xs" :disabled="editing" @click="put({locked:!obj.locked})">{{obj.locked?'解锁物体':'锁定物体'}}</button>
      <p v-if="obj.locked" class="dim tiny">物体已锁定，编辑和删除前请先解锁。</p>
      <fieldset class="object-fields" :disabled="editing || obj.locked">
      <label class="row">名称<input class="input grow" maxlength="24" aria-label="物体名称" :value="nameDraft?.text"
        :disabled="nameDraft?.saving" @focus="nameDraft.focused=true" @input="nameDraft.text=$event.target.value"
        @blur="nameEditor.commit(obj.name)" @keydown.enter.prevent="$event.target.blur()"
        @keydown.esc.prevent="nameEditor.cancel(obj.name)" /></label>
      <div v-if="nameDraft?.error" class="note bad" role="alert">{{nameDraft.error}}<button class="btn btn--xs" @click="nameEditor.cancel(obj.name)">取消改名</button></div>
      <span v-if="nameDraft?.saving" class="dim tiny" role="status">保存名称…</span>
      <span v-else-if="nameDraft?.awaiting && !nameDraft.error" class="dim tiny" role="status">等待名称回读…</span>
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

      <fieldset class="object-fields" :disabled="!!obj.task && obj.task.role!=='obstacle'">
      <div class="lbl dim tiny">旋转 °（基座 X / Y / Z）</div>
      <div class="row nowrap" v-for="(a,i) in AX" :key="'r'+a"><span class="ax disp">{{a}}</span><input class="input mono grow" type="number" min="-3600" max="3600" step="5" :aria-label="'物体绕'+a+'轴旋转角度'" :value="draft.rotation_deg.length?draft.rotation_deg[i]:(obj.rotation_deg || [0,0,0])[i]" @focus="begin('rotation_deg',i,$event.target.value)" @input="draft.rotation_deg[i]=$event.target.value" @blur="commit('rotation_deg',i,$event.target.value)" @keydown.enter.prevent="$event.target.blur()" /></div>
      <label class="row">等比缩放<input class="input mono grow" type="number" min="0.01" max="100" step="0.1" aria-label="物体等比缩放" :value="scaleDraft.id===obj.id?scaleDraft.value:(obj.scale ?? 1)" @focus="scaleDraft.id=obj.id;scaleDraft.value=String(obj.scale ?? 1)" @input="scaleDraft.value=$event.target.value" @blur="commitScale($event.target.value)" @keydown.enter.prevent="$event.target.blur()" /></label>

      </fieldset>
      <p v-if="obj.task && obj.task.role!=='obstacle'" class="dim tiny">任务工件与区域请重新布置，不支持独立旋转或缩放。</p>
      <label class="row">材质<select class="select grow" :value="obj.surface || 'painted'" @change="put({surface:$event.target.value})"><option v-for="(surface,key) in OBJECT_SURFACES" :key="key" :value="key">{{ surface.label }}</option></select></label>
      <details class="tiny dim"><summary>碰撞代理</summary><p>{{ obj.kind }} 保守几何；工业外观的孔洞、滚筒间隙不参与精确网格碰撞。</p></details>

      <label class="sw"><input type="checkbox" :checked="obj.gravity" @change="put({ gravity: $event.target.checked })" />
        <span>重力<span class="dim tiny">9.81 m/s²</span></span></label>
      <div class="dim tiny">地面约束已开启，物体底部不会穿过 Z=0。</div>
      <label class="sw"><input type="checkbox" :checked="obj.floating" @change="put({ floating: $event.target.checked })" />
        <span>漂浮<span class="dim tiny">不受重力</span></span></label>

      <div class="row nowrap">
        <span class="ax disp">色</span>
        <input class="swatch" type="color" :value="obj.color" @change="put({ color: $event.target.value })" />
        <span class="grow"></span>
        <span class="tag tag--mute mono">Vz {{ obj.vz === undefined ? '--' : obj.vz }}</span>
      </div>

      <div v-if="hit" class="note bad">
        与 <b class="mono">{{ hit.link }}</b> 包络相交<template v-if="Number.isFinite(hit.gap_mm)"> · {{ Math.abs(hit.gap_mm).toFixed(1) }} mm</template>。
      </div>
      
      <div class="row"><button class="btn" :disabled="!!obj.task" :title="obj.task?'任务物体请使用任务布置':''" @click="duplicate">复制物体</button><button class="btn btn--danger" @click="del">删除物体</button></div>
      <span class="dim tiny">属性、复制和删除可在场景工具撤销。</span>
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
