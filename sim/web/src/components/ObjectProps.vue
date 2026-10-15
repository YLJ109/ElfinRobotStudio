<script setup>
import { computed, reactive } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { snap, selObj } = storeToRefs(w)
const obj = computed(() => ((snap.value && snap.value.objects) || []).find((o) => o.id === selObj.value) || null)
const hit = computed(() => ((snap.value && snap.value.obj_hits) || []).find((h) => h.id === selObj.value) || null)
const draft = reactive({ xyz: [], size: [] })
function begin(kind, i, value) {
  if (!obj.value) return
  draft[kind] = obj.value[kind].slice()
  draft[kind][i] = value
}
function commit(kind, i, value) {
  if (!obj.value) return
  const values = (draft[kind].length ? draft[kind] : obj.value[kind].slice()).slice()
  values[i] = Number(value)
  if (!Number.isFinite(values[i])) return
  draft[kind] = []
  kind === 'xyz' ? setAxis(i, values[i]) : setSize(i, values[i])
}

function put(patch) { if (obj.value) w.patchObject(obj.value.id, patch) }
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
  await w.removeObject(obj.value.id)
  w.selObj = null
}
const AX = ['X', 'Y', 'Z']
</script>

<template>
  <section v-if="obj" class="panel corner props">
    <div class="panel-hd">
      <span class="panel-title">物体属性</span>
      <span class="panel-sub mono ellip">{{ obj.name }} · {{ obj.kind }}</span>
      <span class="grow"></span>
      <button class="btn btn--xs no-fold" title="关闭" @click="w.selObj = null">×</button>
    </div>
    <div class="panel-bd">
      <div class="lbl dim tiny">位置 mm（基座坐标，Z 朝上）</div>
      <div class="row nowrap" v-for="(a, i) in AX" :key="'p' + a">
        <span class="ax disp">{{ a }}</span>
        <input class="input mono grow" type="number" step="10" :value="draft.xyz.length ? draft.xyz[i] : obj.xyz[i].toFixed(0)"
               @focus="begin('xyz', i, $event.target.value)" @input="draft.xyz[i] = $event.target.value"
               @blur="commit('xyz', i, $event.target.value)" @keydown.enter.prevent="$event.target.blur()" />
      </div>
      <div class="lbl dim tiny">{{ obj.kind === 'sphere' ? '半径 mm' : '尺寸 mm' }}</div>
      <div class="row nowrap">
        <span class="ax disp">S</span>
        <input class="input mono grow" type="number" min="1" step="10" :value="draft.size.length ? draft.size[0] : obj.size[0].toFixed(0)"
               @focus="begin('size', 0, $event.target.value)" @input="draft.size[0] = $event.target.value"
               @blur="commit('size', 0, $event.target.value)" @keydown.enter.prevent="$event.target.blur()" />
        <input v-if="obj.kind !== 'sphere'" class="input mono grow" type="number" min="1" step="10"
               :value="draft.size.length ? draft.size[1] : obj.size[1].toFixed(0)"
               @focus="begin('size', 1, $event.target.value)" @input="draft.size[1] = $event.target.value"
               @blur="commit('size', 1, $event.target.value)" @keydown.enter.prevent="$event.target.blur()" />
        <input v-if="obj.kind !== 'sphere'" class="input mono grow" type="number" min="1" step="10"
               :value="draft.size.length ? draft.size[2] : obj.size[2].toFixed(0)"
               @focus="begin('size', 2, $event.target.value)" @input="draft.size[2] = $event.target.value"
               @blur="commit('size', 2, $event.target.value)" @keydown.enter.prevent="$event.target.blur()" />
      </div>

      <label class="sw"><input type="checkbox" :checked="obj.gravity" @change="put({ gravity: $event.target.checked })" />
        <span>重力<span class="dim tiny">9.81 m/s²</span></span></label>
      <label class="sw"><input type="checkbox" :checked="obj.floor" @change="put({ floor: $event.target.checked })" />
        <span>落地<span class="dim tiny">被 z=0 地面挡住</span></span></label>
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
        正在压到 <b class="mono">{{ hit.link }}</b>（重叠 {{ Math.abs(hit.gap_mm).toFixed(1) }}mm）—— 已按碰撞级告警。
      </div>
      
      <button class="btn btn--danger" @click="del">删除该物体</button>
    </div>
  </section>
</template>

<style scoped>
.props { max-height: calc(100% - 88px); overflow: auto; }
.lbl { margin-top: 2px; }
.ax { width: 14px; font-size: 11.5px; color: var(--tx-3); }
.input { font-size: 12px; padding: 3px 6px; }
.sw { display: flex; align-items: center; gap: 6px; font-size: 12.5px; cursor: pointer; }
.sw .dim { margin-left: 4px; }
.swatch { width: 34px; height: 22px; padding: 0; border: 1px solid var(--line);
          border-radius: 5px; background: none; }
</style>
