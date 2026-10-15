<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { snap, selObj, scenes, activeScene } = storeToRefs(w)
const objs = computed(() => (snap.value ? snap.value.objects || [] : []))
const hits = computed(() => (snap.value ? snap.value.obj_hits || [] : []))
const height = ref(300)
const sceneName = ref('')
const sceneMsg = ref('')
const fileInput = ref(null)

const KINDS = [
  ['box', '长方体'],
  ['sphere', '球'],
  ['cylinder', '圆柱'],
]
async function add(kind) {
  const n = objs.value.length
  const spec = {
    kind,
    xyz: [260 + (n % 4) * 160, -260 + (n % 3) * 170, Number(height.value)],
    size: kind === 'sphere' ? [120] : [160, 160, 160],
    gravity: true, floor: true, collide: true, floating: false,
  }
  const r = await w.addObject(spec)
  if (r.ok) w.selObj = r.object.id
}
async function dropAll() {
  await w.clearObjects()
  w.selObj = null
}
const hitOf = (id) => hits.value.find((h) => h.id === id)
async function saveScene() {
  const name = sceneName.value.trim()
  if (!name) { sceneMsg.value = '请先填写现场名称'; return }
  const r = await w.saveScene(name)
  sceneMsg.value = r.ok ? `已保存：${name}（${objs.value.length} 个物体）` : (r.why || '保存失败')
}
async function newScene() {
  if (objs.value.length && !window.confirm('新建现场会清空当前摆放物，是否继续？')) return
  await w.clearObjects(); w.activeScene = ''; sceneName.value = ''; sceneMsg.value = '已新建空白现场'
}
async function loadScene(name) {
  if (name === activeScene.value) return
  if (objs.value.length && !window.confirm(`载入「${name}」会替换当前现场，是否继续？`)) return
  const r = await w.loadScene(name)
  if (r.ok) { sceneName.value = name; sceneMsg.value = `已载入：${name}` }
  else sceneMsg.value = r.why || '载入失败'
}
async function removeScene(name) {
  if (!window.confirm(`删除现场文件「${name}」？当前摆放不会被删除。`)) return
  const r = await w.deleteScene(name)
  sceneMsg.value = r.ok ? `已删除：${name}` : (r.why || '删除失败')
}
async function exportScene(name) {
  const r = await w.exportScene(name)
  if (r.ok) sceneMsg.value = `已导出：${name}.scene.json`
}
async function importScene(ev) {
  const file = ev.target.files && ev.target.files[0]
  ev.target.value = ''
  if (!file) return
  try {
    const data = JSON.parse(await file.text())
    const objects = Array.isArray(data) ? data : data.objects
    const name = String((data && data.name) || file.name.replace(/\.scene\.json$|\.json$/i, '')).trim()
    if (!Array.isArray(objects) || !name) throw new Error('文件必须包含 objects 数组和现场名称')
    const r = await w.importScene(name, objects)
    if (!r.ok) { sceneMsg.value = r.why || '导入失败'; return }
    sceneName.value = name
    const loaded = await w.loadScene(name)
    sceneMsg.value = loaded.ok ? `已导入并载入：${name}` : `已导入：${name}`
  } catch (e) { sceneMsg.value = `导入失败：${e.message}` }
}
</script>

<template>
  <div class="col">
    <section class="panel corner scene-panel">
      <div class="panel-hd">
        <span class="panel-title">摆放现场</span>
        <span class="panel-sub">SCENE FILES</span>
        <span class="grow"></span>
        <span v-if="activeScene" class="tag tag--ok">当前：{{ activeScene }}</span>
      </div>
      <div class="panel-bd scene-bd">
        <div class="row nowrap scene-actions">
          <input class="input grow" v-model="sceneName" placeholder="现场名称，例如：装配工位 A" @keyup.enter="saveScene" />
          <button class="btn btn--primary btn--xs" @click="saveScene">保存</button>
          <button class="btn btn--xs" @click="newScene">新建</button>
        </div>
        <div class="row nowrap scene-actions">
          <button class="btn btn--xs" @click="fileInput?.click()">导入 JSON</button>
          <input ref="fileInput" class="file-input" type="file" accept=".json,.scene.json,application/json" @change="importScene" />
          <button class="btn btn--xs" :disabled="!activeScene" @click="exportScene(activeScene)">导出当前</button>
        </div>
        <div class="scene-list">
          <div v-for="item in scenes" :key="item.name" class="scene-file" role="button" tabindex="0"
               :class="{ selected: item.name === activeScene }" @click="loadScene(item.name)"
               @keydown.enter="loadScene(item.name)">
            <span class="folder" aria-hidden="true">▱</span>
            <span class="scene-file-main"><b>{{ item.name }}</b><small>{{ item.object_count }} 个物体</small></span>
            <span class="scene-file-actions" @click.stop>
              <button class="icon-btn" title="导出" @click="exportScene(item.name)">⇩</button>
              <button class="icon-btn danger" title="删除" @click="removeScene(item.name)">×</button>
            </span>
          </div>
          <div v-if="!scenes.length" class="empty-scene">暂无现场文件，输入名称后点击“保存”。</div>
        </div>
        <div v-if="sceneMsg" class="scene-msg" role="status">{{ sceneMsg }}</div>
      </div>
    </section>

    <section class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">摆放物体</span>
        <span class="panel-sub">SCENE PROPS · 纯仿真道具</span>
        <span class="grow"></span>
        <span class="tag" :class="hits.length ? 'tag--bad' : 'tag--mute'">{{ objs.length }} 个</span>
      </div>
      <div class="panel-bd">
        <div class="row">
          <button v-for="[k, label] in KINDS" :key="k" class="btn grow" @click="add(k)">+ {{ label }}</button>
        </div>
        <div class="row nowrap">
          <span class="dim">新物体投放高度</span>
          <input class="grow" type="range" min="100" max="1400" step="10" :value="height"
                 :style="{ '--fill': ((height - 100) / 1300 * 100) + '%' }"
                 @input="height = Number($event.target.value)" />
          <b class="mono acc">{{ height }}mm</b>
        </div>
        
      </div>
    </section>

    <section class="panel">
      <div class="panel-hd">
        <span class="panel-title">已摆放</span>
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
                <td class="disp">{{ o.name }}</td>
                <td class="tiny dim">{{ o.kind }}</td>
                <td class="mono tiny">{{ o.xyz.map((v) => v.toFixed(0)).join(', ') }}</td>
                <td>
                  <span class="tag" :class="hitOf(o.id) ? 'tag--bad' : (o.floating ? 'tag--info' : (o.gravity ? 'tag--warn' : 'tag--mute'))">
                    {{ hitOf(o.id) ? '压到 ' + hitOf(o.id).link : (o.floating ? '漂浮' : (o.gravity ? '下落' : '静止')) }}
                  </span>
                </td>
              </tr>
              <tr v-if="!objs.length"><td colspan="4" class="dim tiny empty">
                还没有物体 —— 上面点一个形状，它会从指定高度掉下来
              </td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.tscroll { max-height: 40vh; overflow: auto; }
tbody tr { cursor: pointer; }
.empty { padding: 10px; }
.scene-bd { display: grid; gap: 6px; }
.scene-actions { gap: 4px; }
.file-input { display: none; }
.scene-list { display: grid; gap: 3px; max-height: 150px; overflow: auto; padding: 2px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg-inset); }
.scene-file { display: flex; align-items: center; gap: 6px; width: 100%; min-width: 0; padding: 5px 6px; color: var(--tx-2); background: transparent; border: 1px solid transparent; border-radius: 5px; font: inherit; text-align: left; cursor: pointer; }
.scene-file:hover, .scene-file.selected { color: var(--tx-1); border-color: var(--acc); background: var(--acc-soft); }
.folder { color: var(--warn); font-size: 16px; }
.scene-file-main { display: grid; gap: 1px; min-width: 0; flex: 1; }
.scene-file-main b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11.5px; }
.scene-file-main small { color: var(--tx-3); font-size: 9.5px; }
.scene-file-actions { display: flex; gap: 2px; }
.icon-btn { width: 22px; height: 22px; color: var(--tx-3); background: transparent; border: 1px solid transparent; border-radius: 4px; cursor: pointer; }
.icon-btn:hover { color: var(--acc); border-color: var(--line-3); }
.icon-btn.danger:hover { color: var(--danger); }
.empty-scene { padding: 12px 6px; color: var(--tx-3); font-size: 10.5px; }
.scene-msg { padding: 4px 6px; color: var(--acc); border-left: 2px solid var(--acc); background: var(--acc-soft); font-size: 10.5px; }
</style>
