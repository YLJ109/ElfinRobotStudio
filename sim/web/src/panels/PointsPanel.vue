<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { points, snap, tele } = storeToRefs(w)
const name = ref('')
const renameId = ref(null), renameText = ref('')
const pointTab = ref('execute')
async function renamePoint(p) {
  const r = await w.renamePoint(p.id, renameText.value.trim())
  if (r.ok) renameId.value = null
}
const granted = computed(() => !!(snap.value && snap.value.motion_granted))
const isReal = computed(() => snap.value && snap.value.mode === 'real')
const paused = computed(() => !!(snap.value && snap.value.paused))
const q = computed(() => (snap.value ? snap.value.joints.q : []))
const lo = computed(() => (snap.value ? snap.value.joints.lower : []))
const hi = computed(() => (snap.value ? snap.value.joints.upper : []))
/* 残影是否在场：读**后端**登记的 preview，不读前端本地变量。
   这样"真机已经站到假手上、后端自动收掉"能立刻反映到界面，不会出现两边不一致。 */
const preview = computed(() => (snap.value ? snap.value.preview : null))

/* 滑块是「预设目标角」。没有残影、且用户没在拖的时候，它跟着机器人实际角实时更新 ——
   进页面第一眼看到的就是真机现在的姿态，而不是六个 0。 */
const tgt = ref(null)
const editing = ref(0)          // 最近一次触摸滑块的时间戳，拖拽期间不被实际值盖掉
function adopt(a) { if (!tgt.value) tgt.value = a.slice(); else tgt.value = a.slice() }
const tv = computed(() => tgt.value || [0, 0, 0, 0, 0, 0])

function slide(i, ev) {
  if (!tgt.value) tgt.value = q.value.slice()
  editing.value = Date.now()
  const next = tgt.value.slice()
  next[i] = Number(ev.target.value)
  tgt.value = next
  // 点位目标统一先显示残影，最终只通过「执行目标」下发。
  w.setPreview(next)
}
function fillStyle(i) {
  const l = lo.value[i] ?? -180, h = hi.value[i] ?? 180
  return { '--fill': (((tv.value[i] ?? 0) - l) / Math.max(1e-6, h - l)) * 100 + '%' }
}
const cls = (i) => {
  const m = Math.max(Math.abs(hi.value[i] ?? 180), Math.abs(lo.value[i] ?? -180), 1e-6)
  const r = Math.abs(tv.value[i] ?? 0) / m
  return r >= 0.9 ? 'bad' : r >= 0.8 ? 'warn' : 'acc'
}
const delta = (i) => ((tv.value[i] ?? 0) - (q.value[i] ?? 0))

/* ---- 点动：轻点走一个步距，按住 0.32s 以上变连续点动，松手即停 ---- */
const STEPS = [0.1, 0.5, 1, 5, 10]
const step = ref(1)
let holdTimer = null
const holding = ref(null)
const clamp = (i, v) => Math.max(lo.value[i], Math.min(hi.value[i], v))
async function tap(i, dir) {
  if (holding.value) return
  editing.value = Date.now()
  const base = (preview.value || tv.value || q.value).slice()
  base[i] = clamp(i, base[i] + (dir ? step.value : -step.value))
  tgt.value = base
  w.setPreview(base)
}
function press(i, dir) {
  holding.value = null
  clearTimeout(holdTimer)
  holdTimer = setTimeout(async () => {
    holding.value = [i, dir]
    const r = await w.jog(i, dir, true)
    if (!r.ok) w.error = r.why || '点动被拒'
  }, 320)
}
async function release(i, dir) {
  clearTimeout(holdTimer)
  if (holding.value) {
    await w.jog(i, dir, false)
    holding.value = null
  }
}
onBeforeUnmount(() => { clearTimeout(holdTimer); clearTimeout(fkTimer); clearTimeout(cartTimer); cartRevision++; fkSeq++; disposed = true; if (freeOn.value) w.free(false) })

/* 执行目标：下发之后**不能**马上清残影 —— 残影要留在原地等真机开上来，
   由后端判"六轴都进 ±0.3° 且站住 0.5s"才收。这是用户明确要的口径。 */
async function runTarget() {
  if (!granted.value || !preview.value || cartBusy.value || cartQueued.value) return
  await w.moveJ(preview.value.slice())
}
function cancel() {
  resetCart()
  w.cancelPreview()
  if (q.value.length === 6) adopt(q.value)
}
function toHome() {
  const z = [0, 0, 0, 0, 0, 0]
  tgt.value = z.slice()
  w.setPreview(z)
}
function applyPoint(p) {
  tgt.value = p.joints.slice()
  w.setPreview(tgt.value)
}
function goPoint(p) {
  applyPoint(p)
  pointTab.value = 'execute'
}

/* ---- 笛卡尔目标 → 逆解 ---- */
const ikRes = ref(null)
const ikTouched = ref(false)
const pose = ref([0, 0, 0, 0, 0, 0])
const cartMode = ref(false)
const cartNames = ['X', 'Y', 'Z', 'A', 'B', 'C']
const cartRanges = [[-1200, 1200, 5], [-1200, 1200, 5], [-600, 1400, 5], [-180, 180, 1], [-180, 180, 1], [-180, 180, 1]]
const cartTarget = ref([0, 0, 1195, 0, 0, 0])
const tcpNow = computed(() => (snap.value ? snap.value.tcp : null))
let cartTimer = null
let fkTimer = null
let fkSeq = 0
let cartRevision = 0
const cartQueued = ref(false)
let disposed = false
const cartSolving = ref(false)
const cartLoading = ref(false)
const cartBusy = computed(() => cartSolving.value || cartLoading.value)
const cartDirty = ref(false)
let lastCartPose = null
let lastCartJoints = null
const poseValues = (p) => [...p.xyz_mm, ...(p.rpy_deg || [0, 0, 0])].slice(0, 6).map(Number)
watch(q, (a) => {
  if (!a || a.length !== 6 || preview.value || cartBusy.value || cartQueued.value) return
  if (Date.now() - editing.value < 1200) return
  adopt(a)
  if (cartMode.value) cartDirty.value = false
}, { immediate: true })
watch(tcpNow, (p) => {
  if (p && !preview.value && !cartBusy.value && !cartDirty.value && Date.now() - editing.value > 1200 && Array.isArray(p.xyz_mm)) {
    cartTarget.value = poseValues(p)
    lastCartPose = cartTarget.value.slice()
    lastCartJoints = q.value.slice()
  }
}, { immediate: true })
watch(tv, (a) => {
  if (cartMode.value || !a || a.length !== 6) return
  clearTimeout(fkTimer)
  const seq = ++fkSeq
  fkTimer = setTimeout(async () => {
    try {
      const r = await w.fk(a.slice())
      if (disposed || seq !== fkSeq || cartMode.value || !r.ok) return
      cartTarget.value = poseValues(r)
      lastCartPose = cartTarget.value.slice()
      lastCartJoints = a.slice()
    } catch (e) { if (!disposed) w.error = e.message }
  }, 80)
})
function useActualPose() {
  const t = tcpNow.value
  if (t) pose.value = [...t.xyz_mm, ...t.rpy_deg]
}
watch(tcpNow, () => { if (!ikTouched.value) useActualPose() }, { immediate: true })
watch(cartMode, async (on) => {
  cartRevision++
  cartQueued.value = false
  clearTimeout(cartTimer)
  clearTimeout(fkTimer)
  const seq = ++fkSeq
  cartLoading.value = false
  if (!on) return
  const joints = (tgt.value && tgt.value.length === 6) ? tgt.value.slice() : q.value.slice()
  if (joints.length !== 6) return
  cartLoading.value = true
  try {
    const r = await w.fk(joints)
    if (disposed || seq !== fkSeq || !cartMode.value) return
    if (r.ok) {
      cartTarget.value = poseValues(r)
      lastCartPose = cartTarget.value.slice()
      lastCartJoints = joints
      ikRes.value = null
      cartDirty.value = !!preview.value
    }
  } catch (e) { if (!disposed) w.error = e.message }
  finally { if (seq === fkSeq) cartLoading.value = false }
})
function setPose(i, v) { ikTouched.value = true; pose.value[i] = Number(v) }
async function previewCart() {
  if (cartBusy.value || disposed) return
  cartSolving.value = true
  try {
    while (cartQueued.value && cartMode.value && !disposed) {
      cartQueued.value = false
      const revision = cartRevision
      const goal = cartTarget.value.slice()
      const seed = (lastCartJoints || tgt.value || q.value).slice()
      const r = await w.ik(goal, { seed, allow_partial: true })
      if (disposed || revision !== cartRevision || !cartMode.value) continue
      ikRes.value = r
      if (r.ok) {
        editing.value = Date.now()
        tgt.value = r.joints.slice()
        lastCartJoints = r.joints.slice()
        cartTarget.value = r.achieved.slice()
        lastCartPose = cartTarget.value.slice()
        w.setPreview(tgt.value)
      } else if (lastCartPose) {
        // Keep controls and ghost at the same last valid pose. A failed target
        // must never leave an enabled execution button pointing at a stale ghost.
        cartTarget.value = lastCartPose.slice()
      }
    }
  } catch (e) { if (!disposed) w.error = e.message }
  finally { cartSolving.value = false }
}
function slideCart(i, ev) {
  cartTarget.value[i] = Number(ev.target.value)
  cartDirty.value = true
  editing.value = Date.now()
  cartRevision++
  cartQueued.value = true
  clearTimeout(cartTimer)
  cartTimer = setTimeout(previewCart, 80)
}
function resetCart() {
  cartRevision++
  cartQueued.value = false
  cartLoading.value = false
  clearTimeout(cartTimer)
  fkSeq++
  clearTimeout(fkTimer)
  cartDirty.value = false
  const p = tcpNow.value
  if (!p) return
  cartTarget.value = poseValues(p)
  lastCartPose = cartTarget.value.slice()
  lastCartJoints = q.value.slice()
  adopt(q.value)
  ikRes.value = null
  w.cancelPreview()
}
async function solveIke() {
  ikRes.value = await w.ik(pose.value.slice())
  if (ikRes.value.ok) {
    tgt.value = ikRes.value.joints.slice()
    w.setPreview(tgt.value)
  }
}
async function teach() {
  const r = await w.teach(name.value || `P${points.value.length + 1}`)
  if (r.ok) name.value = ''
}
async function addPreset() {
  await w.addPoint(name.value || `P${points.value.length + 1}`, (tgt.value || q.value).slice())
  name.value = ''
}
/* ---- 手拖示教（Basic Free Drive）：按住才生效，松手即退出 ---- */
const free = computed(() => (snap.value ? snap.value.free : { on: false }))
const freeOn = computed(() => !!(free.value && free.value.on))
const ctrlFsm = computed(() => (snap.value || {}).live_fsm)
const freeReadback = computed(() => {
  const r = (snap.value || {}).real || {}
  return r.free_drive
})
let holdBeat = null
async function freeDown() {
  if (!granted.value) return
  await w.free(true, true)
  clearInterval(holdBeat)
  // 按住期间每 0.5s 续一次：浏览器丢 pointerup（Alt+Tab、弹窗抢焦点）时不至于一直松着闸
  holdBeat = setInterval(() => { if (freeOn.value) w.free(true, true) }, 500)
}
function freeUp() {
  clearInterval(holdBeat); holdBeat = null
  if (freeOn.value) w.free(false)
}

/* ---- 末端 IO：吸盘/气阀是作业动作，所以放在作业页 ---- */
const endDo = computed(() => (((tele.value || {}).end_do) || ((snap.value || {}).real || {}).end_do || []))
const endDi = computed(() => (((snap.value || {}).real || {}).end_di) || null)
async function setDo(bit, val) { await w.call('/api/io/enddo', { bit, val }) }
</script>

<template>
  <div class="col points-workspace" :class="{ 'storage-view': pointTab === 'storage' }">
    <div class="point-tabs" role="tablist" aria-label="点位工作区">
      <button class="point-tab" :class="{ on: pointTab === 'execute' }" role="tab" :aria-selected="pointTab === 'execute'" @click="pointTab = 'execute'">直接执行</button>
      <button class="point-tab" :class="{ on: pointTab === 'storage' }" role="tab" :aria-selected="pointTab === 'storage'" @click="pointTab = 'storage'">存储</button>
      <span class="point-tab-help">执行控制与点位库分开管理</span>
    </div>
    <section v-if="pointTab === 'execute'" class="panel corner">
      <div class="panel-hd motion-head">
        <span class="panel-title">预设 / 点动</span>
        <span v-if="preview" class="tag tag--warn motion-state">目标残影</span>
        <span v-else class="tag tag--mute motion-state">跟随实际</span>
        <div class="mode-bar no-fold">
          <div class="mode-group" role="group" aria-label="运动模式">
            <span class="mode-label">运动</span>
            <div class="seg">
              <button :class="{ on: !cartMode }" :aria-pressed="!cartMode" @click.stop="cartMode = false">关节</button>
              <button :class="{ on: cartMode }" :aria-pressed="cartMode" :disabled="q.length !== 6" @click.stop="cartMode = true">笛卡尔</button>
            </div>
          </div>
        </div>
      </div>
      <div class="panel-bd">
        <div class="row nowrap">
          <span class="dim tiny">步距</span>
          <button v-for="s in STEPS" :key="s" class="btn btn--xs" :class="{ 'btn--on': step === s }"
                  @click="step = s">{{ s }}°</button>
          <span class="grow"></span>
          <button class="btn btn--xs" :disabled="!granted" @click="toHome">回零位</button>
        </div>

        <div v-if="!cartMode" v-for="i in 6" :key="i" class="ax">
          <span class="axn disp">J{{ i }}</span>
          <span class="real mono dim" title="实际角（控制器上报）">{{ q[i - 1] !== undefined ? q[i - 1].toFixed(1) : '--' }}</span>
          <button class="btn btn--xs btn--sq" :disabled="!granted"
                  @mousedown="press(i - 1, 0)" @mouseup="release(i - 1, 0)"
                  @mouseleave="release(i - 1, 0)" @click="tap(i - 1, 0)">−</button>
          <input type="range" :style="fillStyle(i - 1)"
                 :min="lo[i - 1] ?? -180" :max="hi[i - 1] ?? 180" step="0.05"
                 :value="tv[i - 1]" @input="slide(i - 1, $event)"
                 :aria-label="'J' + i + ' 目标角'" />
          <button class="btn btn--xs btn--sq" :disabled="!granted"
                  @mousedown="press(i - 1, 1)" @mouseup="release(i - 1, 1)"
                  @mouseleave="release(i - 1, 1)" @click="tap(i - 1, 1)">+</button>
          <input class="tgtv mono" :class="cls(i - 1)" type="text" readonly :aria-label="'J' + i + ' 目标角（只读）'" title="目标角只读，请使用左侧滑块或 ± 按钮调整"
                 :value="tv[i - 1] !== undefined ? tv[i - 1].toFixed(2) : ''"
                 tabindex="-1" />
          <span class="dl mono tiny" :class="Math.abs(delta(i - 1)) > 0.05 ? 'acc' : 'dim'"
                title="目标 − 实际">{{ (delta(i - 1) >= 0 ? '+' : '') + delta(i - 1).toFixed(1) }}</span>
        </div>
        <div v-else class="cart-sliders">
          <div class="row nowrap cart-actions">
            <span class="dim tiny">目标 TCP</span>
            <button class="btn btn--xs" @click="resetCart">回到实际</button>
            <span class="grow"></span>
            <span v-if="cartBusy || cartQueued" class="tag tag--mute">求解中…</span>
            <span v-else-if="ikRes" class="tag" :class="ikRes.ok ? (ikRes.clipped ? 'tag--warn' : 'tag--ok') : 'tag--warn'">{{ ikRes.ok ? (ikRes.clipped ? '已退回有效目标' : '逆解已收敛') : '已保留有效目标' }}</span>
          </div>
          <div v-for="(lab, i) in cartNames" :key="lab" class="cart-row">
            <span class="axn disp">{{ lab }}</span>
            <input type="range" :disabled="cartLoading" :min="cartRanges[i][0]" :max="cartRanges[i][1]" :step="cartRanges[i][2]"
                   :value="cartTarget[i]" @input="slideCart(i, $event)" :aria-label="lab + ' 笛卡尔目标'" />
            <input class="tgtv mono" type="text" readonly tabindex="-1" :value="cartTarget[i].toFixed(i < 3 ? 0 : 1)" />
            <span class="dim tiny">{{ i < 3 ? 'mm' : '°' }}</span>
          </div>
          <div v-if="ikRes && !ikRes.ok" class="note bad tiny">
            此次请求未找到有效解（{{ ikRes.pos_err_mm ?? '--' }} mm / {{ ikRes.rot_err_deg ?? '--' }}°）。
            滑块和残影已保留上一组有效目标，可先调整其他轴或关节姿态。
          </div>
          <div v-if="ikRes && ikRes.ok && ikRes.clipped" class="note warn tiny">
            当前方向只能求解部分行程，已退回有效目标；数值显示的是残影实际 TCP。可调整其他轴或关节后继续。
          </div>
          
        </div>

        <div class="row">
          <button class="btn btn--primary grow" :disabled="!granted || !preview || cartBusy || cartQueued" @click="runTarget">
            执行目标
          </button>
          <button class="btn" :disabled="!preview" @click="cancel">取消</button>
          <button class="btn" @click="adopt(q)">同步回实际</button>
        </div>
        <div v-if="paused" class="note bad">
          已因安全告警自动暂停 <button class="btn btn--xs" @click="w.resume()">解除暂停</button>
        </div>
        
        <div v-if="!granted" class="note warn">
          当前只读，先在顶部完成就绪。
        </div>
      </div>
    </section>

    <section v-if="pointTab === 'execute'" class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">手拖示教</span>
        <span class="grow"></span>
        <span class="tag" :class="freeOn ? 'tag--warn' : 'tag--mute'">{{ freeOn ? '松闸中' : '已抱闸' }}</span>
      </div>
      <div class="panel-bd">
        <button class="btn free no-fold" :class="freeOn ? 'btn--on' : 'btn--arm'"
                :disabled="!granted && !freeOn"
                @pointerdown.prevent="freeDown" @pointerup.prevent="freeUp"
                @pointerleave="freeUp" @contextmenu.prevent>
          <span class="fl1">{{ freeOn ? '松开即抱闸' : '按住松开抱闸' }}</span>
        </button>
        <div class="note warn tiny">松闸无重力补偿，请托住机械臂。</div>
        
        <div v-if="free && free.why" class="note bad tiny">{{ free.why }}</div>
        <div class="row">
          <button class="btn grow" :disabled="!granted" @click="teach">示教保存当前点</button>
          <button class="btn" :disabled="!preview" @click="cancel">收残影</button>
        </div>
      </div>
    </section>

    <section v-if="pointTab === 'execute'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">末端 IO</span>
      </div>
      <div class="panel-bd">
        <div class="row nowrap">
          <span class="dim tiny">可写 DO1–DO3</span>
          <button v-for="b in 3" :key="b" class="btn btn--xs"
                  :class="{ 'btn--on': Number(endDo[b - 1]) === 1 }"
                  :disabled="!granted" @click="setDo(b - 1, Number(endDo[b - 1]) === 1 ? 0 : 1)">
            DO{{ b }} {{ Number(endDo[b - 1]) === 1 ? '亮' : '灭' }}
          </button>
        </div>
        <div class="row nowrap" v-if="endDi">
          <span class="dim tiny">回读 DI1–DI4</span>
          <span v-for="(v, i) in endDi" :key="i" class="tag" :class="Number(v) ? 'tag--ok' : 'tag--mute'">
            DI{{ i + 1 }}={{ v }}</span>
        </div>
        
      </div>
    </section>

    <section v-if="pointTab === 'execute'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">笛卡尔目标</span>
        <span class="panel-sub">IK · 只求解不动机器</span>
        <span class="grow"></span>
        <span v-if="ikRes" class="tag" :class="ikRes.ok ? 'tag--ok' : 'tag--bad'">
          {{ ikRes.ok ? '已收敛' : '未收敛' }}
        </span>
      </div>
      <div class="panel-bd">
        <div class="row nowrap" v-for="(lab, i) in ['X', 'Y', 'Z', 'A', 'B', 'C']" :key="lab">
          <span class="axn disp">{{ lab }}</span>
          <input class="input mono grow" type="number" :step="i < 3 ? 5 : 1" :value="pose[i]"
                 @change="setPose(i, $event.target.value)" />
          <span class="dim tiny unit">{{ i < 3 ? 'mm' : '°' }}</span>
        </div>
        <div class="row">
          <button class="btn grow" @click="solveIke">求解并预览</button>
          <button class="btn" @click="useActualPose">取当前位姿</button>
        </div>
        <div v-if="ikRes" class="note" :class="ikRes.ok ? '' : 'bad'">
          残差 位置 {{ ikRes.pos_err_mm }}mm / 姿态 {{ ikRes.rot_err_deg }}°
          <template v-if="ikRes.ok">—— 已载入上面的滑块，确认后再点「执行目标」。</template>
          <template v-else>—— {{ ikRes.why }}</template>
        </div>
        
      </div>
    </section>

    <section v-if="pointTab === 'storage'" class="panel point-create">
      <div class="panel-hd">
        <span class="panel-title">点位</span>
        <span class="panel-sub">POINTS · {{ points.length }}</span>
      </div>
      <div class="panel-bd">
        <div class="row">
          <input class="grow" v-model="name" placeholder="点位名，留空自动编号" />
          <button class="btn" @click="addPreset">存目标值</button>
        </div>
        
      </div>
    </section>

    <section v-if="pointTab === 'storage'" class="panel point-library">
      <div class="panel-hd">
        <span class="panel-title">点位表</span>
        <span class="panel-sub">{{ points.length }} 个点位 · 到点先预览残影</span>
      </div>
      <div class="panel-bd flush point-library-body">
        <div class="point-list" role="list" aria-label="已保存点位">
          <article v-for="p in points" :key="p.id" class="point-card" role="listitem">
            <div class="point-card-head">
              <div class="disp point-identity">
                  <form v-if="renameId === p.id" @submit.prevent="renamePoint(p)"><input v-model="renameText" aria-label="点位名称" maxlength="80" /><button class="btn btn--xs" type="submit">存</button><button class="btn btn--xs" type="button" @click="renameId = null">取消</button></form>
                  <button v-else class="point-name" title="修改点位名称" @click="renameId = p.id; renameText = p.name">
                    <b>{{ p.name || p.code }}</b>
                    <span v-if="p.name && p.name !== p.code" class="point-code mono">{{ p.code }}</span>
                    <span class="edit-mark" aria-hidden="true">✎</span>
                  </button>
              </div>
              <span class="tag source-tag" :class="p.source === 'real' ? 'tag--ok' : 'tag--mute'">
                {{ p.source === 'real' ? '真机' : p.source === 'sim' ? '仿真' : p.source }}</span>
              <div class="ops">
                  <button class="btn btn--xs go-btn" title="载入目标残影并转到直接执行" @click="goPoint(p)">到点</button>
                  <button class="btn btn--xs btn--sq btn--danger" @click="w.removePoint(p.id)">删</button>
              </div>
            </div>
            <div class="joint-strip mono" aria-label="六轴关节角">
              <span v-for="(v, i) in p.joints" :key="i"><small>J{{ i + 1 }}</small><b>{{ Number(v).toFixed(3) }}°</b></span>
            </div>
            <details v-if="p.tcp" class="tcp-detail">
              <summary>TCP 坐标</summary>
              <div class="tcp-strip mono">
                <span v-for="(v, i) in [...p.tcp.xyz_mm, ...p.tcp.rpy_deg]" :key="i"><small>{{ ['X','Y','Z','A','B','C'][i] }}</small><b>{{ Number(v).toFixed(3) }}{{ i < 3 ? 'mm' : '°' }}</b></span>
              </div>
            </details>
          </article>
          <div v-if="!points.length" class="dim tiny empty">
            还没有点位 —— 先摆一个姿态（或手拖到位），再点「示教保存当前点」
          </div>
        </div>
      </div>
    </section>

  </div>
</template>
<style scoped>
.point-tabs { display:flex; align-items:center; gap:3px; padding:3px; flex:0 0 auto;
  background:var(--bg-inset); border:1px solid var(--line); border-radius:9px; }
.point-tab { min-width:78px; height:27px; padding:0 14px; border:0; border-radius:6px;
  color:var(--tx-2); background:transparent; font:inherit; font-size:12px; cursor:pointer; }
.point-tab:hover { color:var(--tx-1); background:rgba(122,162,220,.08); }
.point-tab.on { color:#04131a; font-weight:600; background:linear-gradient(135deg,#2fd6f0,#4f92f8); }
.point-tab-help { margin-left:auto; padding:0 8px; color:var(--tx-3); font-size:10px; }
.motion-head { display:grid; grid-template-columns:auto minmax(0,1fr) auto 7px;
  grid-template-areas:'title space state arrow' 'modes modes modes arrow'; gap:7px 8px; align-items:center; }
.motion-head::after { grid-area:arrow; margin-left:0; justify-self:end; }
.motion-head > .panel-title { grid-area:title; }
.motion-state { grid-area:state; justify-self:end; white-space:nowrap; }
.mode-bar { grid-area:modes; display:grid; grid-template-columns:minmax(0,1fr); gap:8px; min-width:0; }
.mode-group { display:grid; grid-template-columns:auto minmax(0,1fr); align-items:center; gap:5px; min-width:0; }
.mode-label { color:var(--tx-3); font-size:10px; letter-spacing:.04em; white-space:nowrap; }
.seg { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:2px; min-width:0; padding:2px;
  border:1px solid var(--line-2); background:var(--bg-inset); }
.seg button { min-width:0; height:24px; padding:0 5px; border:0; color:var(--tx-2); background:transparent;
  font:inherit; font-size:11px; white-space:nowrap; cursor:pointer; transition:background .16s,color .16s; }
.seg button:hover:not(:disabled) { color:var(--tx-1); background:rgba(122,162,220,.09); }
.seg button:focus-visible { outline:2px solid var(--acc); outline-offset:1px; }
.seg button:disabled { cursor:not-allowed; opacity:.42; }
.point-name { border: 0; background: none; padding: 3px 0; color: var(--tx-1); font: inherit; text-align: left; cursor: pointer; overflow-wrap: anywhere; }
.point-name { display:flex; align-items:center; flex-wrap:wrap; gap:4px; width:100%; }
.point-name b { color:var(--acc); font-family:var(--font-mono); font-size:12px; }
.point-code { padding:1px 4px; border:1px solid var(--line-2); border-radius:4px; color:var(--tx-3); font-size:9.5px; }
.edit-mark { color:var(--tx-3); font-size:10px; opacity:.75; }
.point-name:hover .edit-mark { color:var(--acc); opacity:1; }
td.disp form { display: flex; flex-wrap: wrap; gap: 3px; }
td.disp form input { width: 100%; min-width: 70px; }
</style>

<style scoped>
.ax { display: grid; grid-template-columns: 20px 40px 22px minmax(0, 1fr) 22px 46px 34px;
      align-items: center; gap: 3px; padding: 2px 0; }
.axn { font-size: 12px; font-weight: 700; color: var(--tx-2); }
.real { font-size: 10.5px; text-align: right; }
.dl { text-align: right; font-size: 10px; }
.btn--sq { width: 22px; padding: 0; font-size: 12px; }
.ops { display: flex; gap: 2px; }
.points-workspace.storage-view { flex:1; min-height:0; height:100%; overflow:hidden; }
.point-create { flex:0 0 auto; }
.point-library { flex:1 1 auto; min-height:0; display:flex; flex-direction:column; overflow:hidden; }
.point-library > .panel-hd { flex:0 0 auto; }
.point-library-body { flex:1 1 auto; min-height:0; overflow:hidden; }
.point-list { flex:1 1 auto; min-height:0; overflow:auto; display:flex; flex-direction:column; gap:5px; padding:5px; }
.point-card { padding:7px; border:1px solid var(--line); border-radius:6px; background:rgba(255,255,255,.018); }
.point-card:hover { border-color:var(--line-2); background:rgba(122,162,220,.04); }
.point-card-head { display:grid; grid-template-columns:minmax(0,1fr) auto auto; align-items:center; gap:6px; }
.point-identity { min-width:0; }
.source-tag { padding:1px 5px; font-size:10px; }
.joint-strip,.tcp-strip { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:3px; margin-top:6px; }
.joint-strip > span,.tcp-strip > span { min-width:0; padding:4px 2px; border-radius:4px; text-align:center; background:var(--bg-inset); }
.joint-strip small,.tcp-strip small { display:block; color:var(--tx-3); font-size:9px; line-height:1.1; }
.joint-strip b,.tcp-strip b { display:block; margin-top:2px; overflow:hidden; color:var(--tx-1); font-size:10px; line-height:1.2; text-overflow:ellipsis; white-space:nowrap; }
.tcp-detail { margin-top:4px; color:var(--tx-3); font-size:10px; }
.tcp-detail summary { width:max-content; cursor:pointer; color:var(--acc); }
.go-btn { min-width:38px; padding:0 6px; color:var(--ok); border-color:rgba(46,230,168,.3); }
.go-btn:hover:not(:disabled) { border-color:var(--ok); background:var(--ok-soft); }
.unit { width: 24px; text-align: right; }
.empty { padding: 10px; }
/* 目标角为只读回显：统一通过滑块或 ± 按钮修改，避免出现输入值与预设状态不同步。 */
.tgtv { width: 46px; min-width: 0; padding: 0 3px; height: 22px; font-size: 12px; text-align: right;
        background: var(--bg-inset); border: 1px solid var(--line-2); border-radius: 5px;
        color: var(--acc); font-family: var(--font-mono); }
.tgtv.warn { color: var(--warn); }
.tgtv.bad { color: var(--danger); }
.tgtv:focus { outline: none; border-color: var(--acc); }
.tgtv[readonly] { cursor: default; opacity: .9; }
.cart-sliders { display: flex; flex-direction: column; gap: 7px; }
.cart-row { display: grid; grid-template-columns: 22px minmax(0, 1fr) 54px 18px; align-items: center; gap: 6px; }
.cart-row input[type='range'] { min-width: 0; }
.free { flex-direction: column; gap: 0; height: 46px; }
.fl1 { font-family: var(--font-display); font-size: 13px; font-weight: 700; letter-spacing: .05em; }
.fl2 { font-size: 9px; letter-spacing: .14em; opacity: .7; }
@media (max-width: 350px) {
  .mode-bar { grid-template-columns:1fr; }
  .point-tab-help { display:none; }
  .point-card-head { grid-template-columns:minmax(0,1fr) auto; }
  .source-tag { display:none; }
  .joint-strip,.tcp-strip { grid-template-columns:repeat(3,minmax(0,1fr)); }
}
</style>
