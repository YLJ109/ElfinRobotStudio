<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import TaskTabs from '../components/TaskTabs.vue'
import SceneAssetImport from '../components/SceneAssetImport.vue'
import { OBJECT_CATEGORIES, OBJECT_SURFACES, catalogEntry, catalogSpec, catalogThumbnail, filterObjectCatalog, objectLabel } from '../objectCatalog.js'

const w = useWorld()
const { snap, selObj, scenes, activeScene } = storeToRefs(w)
const objs = computed(() => (snap.value ? snap.value.objects || [] : []))
const hits = computed(() => (snap.value ? snap.value.obj_hits || [] : []))
const height = ref(300)
const sceneName = ref('')
const sceneMsg = ref('')
const fileInput = ref(null)
const tab = ref('objects')
const tabs = [{id:'objects',label:'物体库'},{id:'current',label:'场景物体'},{id:'scenes',label:'现场文件'}]

const catalogSearch=ref(''),category=ref('all'),selectedPreset=ref('box'),chosenColor=ref(''),chosenSurface=ref(''),adding=ref(false)
const resources=computed(()=>filterObjectCatalog(catalogSearch.value,category.value))
const selectedEntry=computed(()=>catalogEntry(selectedPreset.value))
watch(selectedPreset,()=>{chosenColor.value='';chosenSurface.value=''})
const placing=ref(false)
function cancelPlacement(){window.__elfinScene?.cancelPlacement();placing.value=false}
function add(kind=selectedPreset.value,event=null) {
  if(adding.value)return
  sceneMsg.value=''
  const item=catalogEntry(kind),scene=window.__elfinScene
  if(!item){sceneMsg.value='请选择有效类型';return}
  if(!scene?.beginPlacement){sceneMsg.value='三维场景尚未就绪';return}
  if(!w.connected || w.stateFresh===false || snap.value?.mode!=='sim' || snap.value?.real || snap.value?.running){sceneMsg.value='请在已连接的空闲仿真场景摆放';return}
  const halfHeight=item.kind==='sphere'?item.size[0]:item.size[2]/2
  const spec=catalogSpec(kind,[0,0,Math.max(halfHeight,Number(height.value))],{color:chosenColor.value,surface:chosenSurface.value})
  const session=snap.value.session_id
  const accepted=scene.beginPlacement(spec,{
    nativeDrag:!!event?.dataTransfer,
    async commit(placed){
      if(scene!==window.__elfinScene || snap.value?.session_id!==session || w.view!=='objects')throw Error('场景已变化，请重新选择资源')
      adding.value=true
      try{const result=await w.placeObject(placed);if(!result.ok || !result.object)throw Error(result.why || '摆放失败');w.selObj=result.object.id;sceneMsg.value=`已添加${item.label}`}finally{adding.value=false}
    },cancel(){placing.value=false},error(error){sceneMsg.value=error.message}
  })
  if(!accepted){sceneMsg.value='请先完成当前场景操作';return}
  selectedPreset.value=kind;placing.value=true;sceneMsg.value='在三维地面点击放置，Esc取消'
  if(event?.dataTransfer){event.dataTransfer.effectAllowed='copy';event.dataTransfer.setData('application/x-elfin-object',kind)}
}
watch(()=>[w.view,snap.value?.session_id,snap.value?.mode,w.stateFresh],()=>{if(w.view!=='objects' || snap.value?.mode!=='sim' || w.stateFresh===false)cancelPlacement()})
onBeforeUnmount(cancelPlacement)
async function dropAll() {
  if (!objs.value.length || !window.confirm(`清空当前 ${objs.value.length} 个场景物体？已保存的现场文件会保留。`)) return
  try {
    const r=await w.clearObjects()
    if(r?.ok===false){sceneMsg.value=r.why || '清空失败';return}
    w.selObj = null
  } catch (error) { sceneMsg.value = `清空失败：${error.message}` }
}
const hitOf = (id) => hits.value.find((h) => h.id === id)
async function saveScene() {
  const name = sceneName.value.trim()
  if (!name) { sceneMsg.value = '请先填写现场名称'; return }
  try {
    const r = await w.saveScene(name)
    sceneMsg.value = r.ok ? `已保存：${name}（${objs.value.length} 个物体）` : `保存失败：${r.why || '请重试'}；名称已保留`
  } catch (error) { sceneMsg.value = `保存失败：${error.message}；名称已保留` }
}
async function newScene() {
  if (objs.value.length && !window.confirm('新建现场会清空当前场景物体，是否继续？')) return
  try {
    const r = await w.clearObjects()
    if (r?.ok === false) { sceneMsg.value = `新建失败：${r.why || '未能清空当前场景'}；名称已保留`; return }
    w.activeScene = ''; sceneName.value = ''; sceneMsg.value = '已新建空白现场'
  } catch (error) { sceneMsg.value = `新建失败：${error.message}；名称已保留` }
}
async function loadScene(name) {
  if (name === activeScene.value) return
  if (objs.value.length && !window.confirm(`载入「${name}」会替换当前现场，是否继续？`)) return
  try {
    const r = await w.loadScene(name)
    if (r.ok) { sceneName.value = name; sceneMsg.value = `已载入：${name}` }
    else sceneMsg.value = `载入失败：${r.why || '请重试'}；请核对当前场景`
  } catch (error) { sceneMsg.value = `载入失败：${error.message}；请核对当前场景` }
}
async function removeScene(name) {
  if (!window.confirm(`删除现场文件「${name}」？当前场景物体不会被删除。`)) return
  try {
    const r = await w.deleteScene(name)
    sceneMsg.value = r.ok ? `已删除：${name}` : `删除失败：${r.why || '请重试'}`
  } catch (error) { sceneMsg.value = `删除失败：${error.message}` }
}
async function exportScene(name) {
  try {
    const r = await w.exportScene(name)
    sceneMsg.value = r.ok ? `已导出：${name}.scene.json` : `导出失败：${r.why || '请重试'}`
  } catch (error) { sceneMsg.value = `导出失败：${error.message}` }
}
async function importScene(ev) {
  const file = ev.target.files && ev.target.files[0]
  ev.target.value = ''
  if (!file) return
  let text
  try { text = await file.text() } catch (error) { sceneMsg.value = `导入失败：无法读取文件（${error.message}）；当前场景未改变`; return }
  let data
  // 解析在请求之前完成：坏 JSON 不会发出导入请求，也不会覆盖当前场景。
  try { data = JSON.parse(text) } catch { sceneMsg.value = '导入失败：文件不是有效 JSON；当前场景未改变'; return }
  const objects = Array.isArray(data) ? data : data?.objects
  const name = String((data && data.name) || file.name.replace(/\.scene\.json$|\.json$/i, '')).trim()
  if (!Array.isArray(objects) || !name) { sceneMsg.value = '导入失败：文件必须包含 objects 数组和现场名称；当前场景未改变'; return }
  if (objs.value.length && !window.confirm(`导入「${name}」并载入会替换当前场景，是否继续？`)) return
  let r
  try { r = await w.importScene(name, objects) } catch (error) { sceneMsg.value = `导入失败：${error.message}；当前场景未改变`; return }
  if (!r.ok) { sceneMsg.value = `导入失败：${r.why || '请重试'}；当前场景未改变`; return }
  sceneName.value = name
  try {
    const loaded = await w.loadScene(name)
    sceneMsg.value = loaded.ok ? `已导入并载入：${name}` : `已保存现场「${name}」，但载入失败：${loaded.why || '请重试'}；请核对当前场景`
  } catch (error) {
    sceneMsg.value = `已保存现场「${name}」，但载入失败：${error.message}；请核对当前场景`
  }
}
</script>

<template>
  <div class="col objects-workspace" :class="{'scene-files-active':tab==='scenes'}" @keydown.space.stop>
    <TaskTabs v-model="tab" :tabs="tabs" id="placement" label="场景分类" storage-key="huayan.tabs.placement.v1" />
    <div id="placement-panel" class="col" role="tabpanel" :aria-labelledby="`placement-tab-${tab}`" tabindex="0">
    <section v-show="tab === 'scenes'" class="panel corner scene-panel">
      <div class="panel-hd">
        <span class="panel-title">场景文件</span>
        <span class="grow"></span>
        <span v-if="activeScene" class="tag tag--mute scene-current" :title="'当前现场：' + activeScene">{{ activeScene }}</span>
      </div>
      <div class="panel-bd scene-bd">
        <div class="row nowrap scene-actions">
          <input class="input grow" v-model="sceneName" aria-label="现场名称" placeholder="现场名称" @keyup.enter="saveScene" />
          <button class="btn btn--primary btn--xs" :disabled="!sceneName.trim()" @click="saveScene">保存</button>
          <button class="btn btn--xs" @click="newScene">新建</button>
        </div>
        <div class="row nowrap scene-transfer">
          <button class="btn btn--xs" @click="fileInput?.click()">导入 JSON</button>
          <input ref="fileInput" class="file-input" type="file" accept=".json,.scene.json,application/json" @change="importScene" />
          <button class="btn btn--xs" :disabled="!activeScene" @click="exportScene(activeScene)">导出当前</button>
        </div>
        <div class="scene-list" role="list" aria-label="已保存现场">
          <div v-for="item in scenes" :key="item.name" class="scene-file" role="listitem"
               :class="{ selected: item.name === activeScene }">
            <button type="button" class="scene-file-load" :aria-label="'载入现场 ' + item.name"
                    :aria-pressed="item.name === activeScene" :title="item.name" @click="loadScene(item.name)">
              <svg class="scene-icon" viewBox="0 0 18 18" aria-hidden="true"><path d="M2 5h5l2 2h7v8H2z M2 5V3h5l2 2h7v2" fill="none" stroke="currentColor" stroke-width="1.2" /></svg>
              <span class="scene-file-main"><b>{{ item.name }}</b><small>{{ item.object_count }} 个物体</small></span>
            </button>
            <span class="scene-file-actions">
              <button type="button" class="btn btn--xs btn--sq icon-btn" title="导出现场" :aria-label="'导出现场 ' + item.name" @click="exportScene(item.name)">
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2v8m-3-3 3 3 3-3M3 10v3h10v-3" fill="none" stroke="currentColor" stroke-width="1.2" /></svg>
              </button>
              <button type="button" class="btn btn--xs btn--sq icon-btn danger" title="删除现场文件" :aria-label="'删除现场 ' + item.name" @click="removeScene(item.name)">
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8" fill="none" stroke="currentColor" stroke-width="1.2" /></svg>
              </button>
            </span>
          </div>
          <div v-if="!scenes.length" class="empty-scene">暂无现场文件，输入名称后点击“保存”。</div>
        </div>
        <div v-if="sceneMsg" class="scene-msg" role="status">{{ sceneMsg }}</div>
      </div>
    </section>

    <section v-show="tab === 'objects'" class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">场景资源库</span>
        <span class="grow"></span>
        <span class="tag" :class="hits.length ? 'tag--bad' : 'tag--mute'">{{ objs.length }} 个</span>
      </div>
      <div class="panel-bd">
        <div class="row"><input v-model="catalogSearch" class="input grow" aria-label="搜索场景类型" placeholder="搜索托盘、输送台、工件…" /><select v-model="category" class="select" aria-label="资源分类"><option v-for="group in OBJECT_CATEGORIES" :key="group.id" :value="group.id">{{ group.label }}</option></select></div>
        <div class="catalog-grid" aria-label="场景类型"><button v-for="item in resources" :key="item.id" type="button" class="catalog-item" :aria-pressed="selectedPreset===item.id" :class="{selected:selectedPreset===item.id}" draggable="true" @dragstart="add(item.id,$event)" @dragend="cancelPlacement" @click="add(item.id)"><img :src="catalogThumbnail(item.id)" alt="" width="80" height="70" /><span>{{ item.label }}</span></button></div>
        <p v-if="!resources.length" class="dim tiny">没有匹配类型，试试其他名称或分类。</p>
        <div v-if="selectedEntry" class="catalog-choice"><strong>{{ selectedEntry.label }}</strong><span class="tiny dim">{{ selectedEntry.kind==='cylinder'?'半径 / 高度':selectedEntry.kind==='sphere'?'半径':'X / Y / Z' }} {{ (selectedEntry.kind==='sphere'?selectedEntry.size.slice(0,1):selectedEntry.kind==='cylinder'?[selectedEntry.size[0],selectedEntry.size[2]]:selectedEntry.size).join(' / ') }} mm</span><div class="row"><input type="color" aria-label="添加颜色" :value="chosenColor || selectedEntry.color" @input="chosenColor=$event.target.value" /><select class="select grow" aria-label="添加材质" :value="chosenSurface || selectedEntry.surface" @change="chosenSurface=$event.target.value"><option v-for="(surface,key) in OBJECT_SURFACES" :key="key" :value="key">{{ surface.label }}</option></select><button class="btn btn--primary" :disabled="adding" @click="add()">{{ adding?'保存中…':'在地面放置' }}</button></div></div>
        <p class="dim tiny">点击或拖动资源卡到三维地面放置。点选已有物体后拖动坐标轴，松开保存。</p>
        <button v-if="placing && !adding" class="btn btn--xs" @click="cancelPlacement">取消放置</button>
        <SceneAssetImport :active="tab === 'objects'" />
        <p v-if="sceneMsg" class="scene-msg" role="status">{{ sceneMsg }}</p>
        <div class="row nowrap placement-height">
          <label for="placement-height" class="dim tiny">投放高度</label>
          <input id="placement-height" class="grow" type="range" min="100" max="1400" step="10" :value="height"
                 :style="{ '--fill': ((height - 100) / 1300 * 100) + '%' }"
                 @input="height = Number($event.target.value)" />
          <output for="placement-height" class="mono">{{ height }} <small class="dim">mm</small></output>
        </div>
        
      </div>
    </section>

    <section v-show="tab === 'current'" class="panel current-objects-panel">
      <div class="panel-hd">
        <span class="panel-title">场景物体</span>
        <span class="grow"></span>
        <button class="btn btn--xs btn--danger" :disabled="!objs.length" @click="dropAll">全部清空</button>
      </div>
      <div class="panel-bd flush">
        <div class="tscroll">
          <table>
            <thead><tr><th>名称</th><th>形状</th><th>位置 mm</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="o in objs" :key="o.id" :class="{ picked: selObj === o.id }"
                  @click="w.selObj = o.id">
                <td><button class="object-name" :aria-pressed="selObj === o.id" :title="o.name" @click.stop="w.selObj = o.id">{{ o.name }}</button></td>
                <td class="tiny dim">{{ objectLabel(o) }}</td>
                <td class="mono tiny">{{ o.xyz.map((v) => v.toFixed(0)).join(', ') }}</td>
                <td>
                  <span class="tag" :class="hitOf(o.id) ? 'tag--bad' : (o.floating ? 'tag--info' : (o.gravity ? 'tag--warn' : 'tag--mute'))">
                    {{ hitOf(o.id) ? '碰撞 · ' + hitOf(o.id).link : (o.floating ? '悬浮' : (o.gravity ? '重力' : '固定')) }}
                  </span>
                </td>
              </tr>
              <tr v-if="!objs.length"><td colspan="4" class="dim tiny empty">
                暂无场景物体，可从资源库选择类型添加。
              </td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
    </div>
  </div>
</template>

<style scoped>
.objects-workspace { gap: 6px; }
.objects-workspace.scene-files-active { flex:1;min-height:0;overflow:hidden; }
.scene-files-active #placement-panel { flex:1;min-height:0; }
.scene-files-active .scene-panel:not(.folded) { display:flex;flex-direction:column;flex:1;min-height:0; }
.scene-files-active #placement-panel:has(>.scene-panel.folded) { flex:0 0 auto; }
.catalog-grid { display:grid;grid-template-columns:repeat(auto-fit,minmax(84px,1fr));gap:4px;max-height:300px;overflow:auto; }
.catalog-item { display:grid;justify-items:center;gap:2px;padding:4px;color:var(--tx-2);background:var(--bg-inset);border:1px solid var(--line);font:inherit;cursor:pointer; }
.catalog-item.selected { border-color:var(--acc);color:var(--acc);background:var(--acc-soft); }
.catalog-item img { width:80px;height:70px;object-fit:contain; }
.catalog-item span { font-size:11px; }
.catalog-choice { display:grid;gap:4px;padding-block:6px;border-block:1px solid var(--line); }
.objects-workspace .panel-hd { gap: 6px; padding: 8px 10px; }
.objects-workspace .panel-bd:not(.flush) { padding: 8px 10px; gap: 8px; }
.tscroll { max-height: 40vh; overflow: auto; }
tbody tr { cursor: pointer; }
.empty { padding: 10px; }
.scene-bd { display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden;gap:6px; }
.scene-current { max-width: 42%; overflow: hidden; text-overflow: ellipsis; }
.scene-actions { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 4px; }
.scene-transfer { gap: 4px; }
.scene-actions,.scene-transfer,.scene-msg { flex:0 0 auto; }
.file-input { display: none; }
.scene-list { display:flex;flex-direction:column;flex:1;min-height:0;overflow:auto; border: 1px solid var(--line); background: var(--bg-inset); }
.scene-file { flex:0 0 auto;display: flex; align-items: center; gap: 4px; width: 100%; min-width: 0; padding-right: 4px;
              color: var(--tx-2); border-bottom: 1px solid var(--line); border-left: 2px solid transparent; }
.scene-file:last-child { border-bottom: 0; }
.scene-file:hover { color: var(--tx-1); background: var(--surface-3); }
.scene-file.selected { color: var(--tx-1); border-left-color: var(--acc); background: var(--acc-soft); }
.scene-file-load { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; padding: 6px;
                   background: transparent; border: 0; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.scene-file-load:focus-visible { outline-offset: -2px; box-shadow: none; }
.scene-icon { width: 18px; height: 18px; flex: 0 0 auto; color: var(--tx-3); }
.scene-file-main { display: grid; gap: 1px; min-width: 0; flex: 1; }
.scene-file-main b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; font-weight: 500; }
.scene-file-main small { color: var(--tx-3); font-size: 10px; }
.scene-file-actions { display: flex; flex: 0 0 auto; gap: 2px; }
.icon-btn.btn { width: 24px; height: 24px; padding: 0; background: transparent; border-color: transparent; border-radius: 0; }
.icon-btn svg { width: 14px; height: 14px; }
.icon-btn:hover { color: var(--acc); }
.icon-btn.danger:hover { color: var(--danger); }
.empty-scene { padding: 10px 8px; color: var(--tx-3); font-size: 11px; }
.scene-msg { padding: 4px 6px; color: var(--tx-2); border-left: 2px solid var(--line-3); background: var(--bg-inset); font-size: 11px; overflow-wrap: anywhere; }
.placement-height { gap: 6px; }
.placement-height output { flex: 0 0 64px; text-align: right; font-size: 12px; }
.object-name { max-width: 120px; padding: 2px 0; font: inherit; color: inherit; background: transparent;
               border: 0; overflow-wrap: anywhere; text-align: left; cursor: pointer; }
td .tag { max-width: 130px; white-space: normal; overflow-wrap: anywhere; }
</style>
