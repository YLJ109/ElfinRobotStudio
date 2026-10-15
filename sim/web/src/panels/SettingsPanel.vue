<script setup>
import { ref, computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { snap, meta } = storeToRefs(w)
const s = computed(() => (snap.value ? snap.value.settings : {}))
const saved = ref('')
const adv = ref(false)

async function patch(obj) {
  await w.setSettings(obj)
  saved.value = Object.keys(obj).join('、') + ' 已保存'
  clearTimeout(patch._t)
  patch._t = setTimeout(() => { saved.value = '' }, 2200)
}

/* 围栏：x/y/z 各一对 min/max，改一项就要重算安全层（后端 _cfg_rev 会自己 +1） */
const fence = computed(() => s.value.fence || { x: [0, 0], y: [0, 0], z: [0, 0], enabled: true })
const AXES = [['x', 'X 前后'], ['y', 'Y 左右'], ['z', 'Z 高低']]
function setFence(axis, which, v) {
  const next = JSON.parse(JSON.stringify(fence.value))
  next[axis][which === 0 ? 0 : 1] = Number(v)
  patch({ fence: next })
}
const badFence = computed(() => AXES.some(([k]) => fence.value[k][0] >= fence.value[k][1]))
const thresholdBad = computed(() => {
  const a = Number(s.value.safety_yellow_pct), b = Number(s.value.safety_red_pct), c = Number(s.value.safety_alarm_pct)
  return !(a > 0 && a < b && b < c && c <= 100)
})
const fenceSummary = computed(() => badFence.value ? '范围无效' : (fence.value.enabled ? '已生效' : '已关闭'))

/* 示教器实测限位：只有填全 6 对才生效，否则后端退回 URDF（不猜） */
const cust = ref([[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]])
const custFilled = computed(() => cust.value.every((p) => p[0] !== null && p[1] !== null))
watch(() => s.value.custom_limits, (v) => {
  if (Array.isArray(v) && v.length === 6) cust.value = v.map((p) => [p[0], p[1]])
}, { immediate: true })

const limitOpts = computed(() => Object.entries((meta.value && meta.value.limit_sources) || {}))
const speedKeys = ['sim_speed_pct', 'real_speed_pct']
</script>

<template>
  <div class="col" v-if="snap">
    <div v-if="saved" class="saved mono tiny">{{ saved }}</div>

    <section class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">连接与模式</span>
        <span class="panel-sub mono">LINK / MODE</span>
      </div>
      <div class="panel-bd">
        <div class="row nowrap">
          <span class="lbl">驱动模式</span>
          <button class="btn" :class="{ 'btn--on': snap.mode === 'sim' }" @click="w.disconnectReal()">仿真</button>
          <button class="btn" :class="{ 'btn--on': snap.mode === 'real' }" @click="w.connectReal()">真机</button>
        </div>
        <div class="row nowrap">
          <span class="lbl">控制器 IP</span>
          <input class="input mono grow" :value="snap.settings.robot_ip"
                 @change="patch({ robot_ip: $event.target.value.trim() })" />
        </div>
        <label class="row no-fold" style="gap:6px">
          <input type="checkbox" :checked="snap.settings.auto_connect"
                 @change="patch({ auto_connect: $event.target.checked })" />
          <span class="tiny">开机自动连接真机</span>
        </label>
        <div class="row nowrap">
          <span class="lbl">真机运动指令</span>
          <select class="select grow" :value="snap.settings.real_move_cmd"
                  @change="patch({ real_move_cmd: $event.target.value })">
            <option value="relj">MoveRelJ 逐轴逼近（本机已实测）</option>
            <option value="movejto">MoveJTo 绝对点动（协议 p198，本机未取证）</option>
          </select>
        </div>
        
      </div>
    </section>

    <section class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">安全与限位</span>
        <span class="panel-sub mono">SAFETY / LIMITS</span>
      </div>
      <div class="panel-bd">
        <div class="setting-intro">
          <div><b>工作空间围栏</b><span class="status-chip" :class="{ bad: badFence || !fence.enabled }">{{ fenceSummary }}</span></div>
          <p>限制 TCP 与连杆活动范围。每个轴填写最小值 / 最大值，单位为 mm；超出范围会进入安全告警。</p>
        </div>
        <div class="row nowrap safety-source">
          <span class="lbl">限位来源</span>
          <select class="select grow" :value="snap.settings.limit_source"
                  @change="patch({ limit_source: $event.target.value })">
            <option v-for="[k, label] in limitOpts" :key="k" :value="k">{{ label }}</option>
          </select>
        </div>
        <div class="fence-grid">
          <div v-for="[k, label] in AXES" :key="k" class="fence-axis">
            <div class="fence-axis-hd"><b>{{ label }}</b><span class="mono">{{ fence[k][0] }} … {{ fence[k][1] }} mm</span></div>
            <div class="fence-inputs">
              <label><span>最小</span><input class="input mono" type="number" :value="fence[k][0]" :aria-label="label + '最小值（毫米）'"
                       @change="setFence(k, 0, $event.target.value)" /></label>
              <span class="range-arrow">→</span>
              <label><span>最大</span><input class="input mono" type="number" :value="fence[k][1]" :aria-label="label + '最大值（毫米）'"
                       @change="setFence(k, 1, $event.target.value)" /></label>
            </div>
          </div>
        </div>
        <div v-if="badFence" class="note bad tiny">范围无效：每个轴的最小值必须小于最大值；保存后不会参与安全计算。</div>
        <label class="row no-fold" style="gap:6px">
          <input type="checkbox" :checked="fence.enabled" @change="patch({ fence: { ...fence, enabled: $event.target.checked } })" />
          <span class="tiny">启用安全围栏</span>
        </label>
        <label class="row no-fold" style="gap:6px">
          <input type="checkbox" :checked="snap.settings.self_collision"
                 @change="patch({ self_collision: $event.target.checked })" />
          <span class="tiny">自碰撞检测（连杆胶囊体互相接近即告警）</span>
        </label>
        <label class="row no-fold" style="gap:6px">
          <input type="checkbox" :checked="snap.settings.ground_enabled"
                 @change="patch({ ground_enabled: $event.target.checked })" />
          <span class="tiny">地面检测</span>
        </label>
        <div class="threshold-box">
          <div class="threshold-hd"><b>关节限位告警阈值</b><span>按六轴最大占用率分级</span></div>
          <div class="threshold-grid">
            <label><span class="threshold-dot yellow"></span>预警（黄）<input class="input input--num" type="number" min="1" max="99" :value="snap.settings.safety_yellow_pct"
                   @change="patch({ safety_yellow_pct: Number($event.target.value) })" />%</label>
            <label><span class="threshold-dot red"></span>严重（红）<input class="input input--num" type="number" min="1" max="99" :value="snap.settings.safety_red_pct"
                   @change="patch({ safety_red_pct: Number($event.target.value) })" />%</label>
            <label><span class="threshold-dot alarm"></span>告警/停止<input class="input input--num" type="number" min="1" max="100" :value="snap.settings.safety_alarm_pct"
                   @change="patch({ safety_alarm_pct: Number($event.target.value) })" />%</label>
          </div>
          <div class="threshold-help" :class="{ bad: thresholdBad }">{{ thresholdBad ? '阈值必须满足：黄 < 红 < 告警 ≤ 100' : '计算方式：|当前角| ÷ 对应方向限位 × 100%，取六轴最大值。' }}</div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-hd">
        <span class="panel-title">显示与播报</span>
        <span class="panel-sub mono">VIEW / VOICE</span>
      </div>
      <div class="panel-bd">
        <div class="row nowrap">
          <span class="lbl">残影模式</span>
          <button class="btn" :class="{ 'btn--on': snap.settings.ghost_mode === 'ghost' }"
                  @click="patch({ ghost_mode: 'ghost' })">残影预览</button>
          <button class="btn" :class="{ 'btn--on': snap.settings.ghost_mode === 'live' }"
                  @click="patch({ ghost_mode: 'live' })">实时跟随</button>
        </div>
        <div class="row nowrap">
          <span class="lbl">假手透明度</span>
          <input type="range" min="0.1" max="0.7" step="0.02" :value="snap.settings.ghost_opacity"
                 :style="{ '--fill': ((snap.settings.ghost_opacity - 0.1) / 0.6 * 100) + '%' }"
                 @change="patch({ ghost_opacity: Number($event.target.value) })" />
          <b class="mono tiny">{{ Number(snap.settings.ghost_opacity).toFixed(2) }}</b>
        </div>
        <div class="row nowrap">
          <span class="lbl">轨迹拖尾</span>
          <input type="range" min="0" max="100" step="5" :value="snap.settings.trail_len ?? 40"
                 :style="{ '--fill': (snap.settings.trail_len ?? 40) + '%' }"
                 @change="patch({ trail_len: Number($event.target.value) })" />
          <b class="mono tiny">{{ snap.settings.trail_len ?? 40 }} 帧</b>
        </div>
        <div class="row nowrap">
          <span class="lbl">残影重合判据</span>
          ±<input class="input input--num" type="number" step="0.1" :value="snap.settings.ghost_align_tol_deg"
                 @change="patch({ ghost_align_tol_deg: Number($event.target.value) })" />°
          站住
          <input class="input input--num" type="number" step="0.1" :value="snap.settings.ghost_align_hold_s"
                 @change="patch({ ghost_align_hold_s: Number($event.target.value) })" />s
        </div>
        <label class="row no-fold" style="gap:6px">
          <input type="checkbox" :checked="snap.settings.tts" @change="patch({ tts: $event.target.checked })" />
          <span class="tiny">告警语音播报（浏览器不支持时只显示文字，不影响告警）</span>
        </label>
        <div class="row nowrap">
          <span class="lbl">语速</span>
          <input type="range" min="0.6" max="1.6" step="0.1" :value="snap.settings.tts_rate"
                 :style="{ '--fill': ((snap.settings.tts_rate - 0.6) / 1 * 100) + '%' }"
                 @change="patch({ tts_rate: Number($event.target.value) })" />
          <b class="mono tiny">×{{ Number(snap.settings.tts_rate).toFixed(1) }}</b>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-hd" @click="adv = !adv">
        <span class="panel-title">高级</span>
        <span class="panel-sub mono">ADVANCED · 一般不用动</span>
        <span class="grow"></span>
        <span class="dim tiny">{{ adv ? '收起' : '展开' }}</span>
      </div>
      <div class="panel-bd" v-if="adv">
        <div class="row nowrap">
          <span class="lbl">速度口径</span>
          <select class="select grow" :value="snap.settings.vel_source"
                  @change="patch({ vel_source: $event.target.value })">
            <option value="urdf">URDF（六轴 90°/s）</option>
            <option value="manual">手册 E05（J1-4 180，J5-6 200°/s）</option>
          </select>
        </div>
        <div class="field">
          <label>示教器实测软限位（{{ snap.settings.limit_source === 'manual' ? '当前生效' : '填了并把上面口径切成"示教器实测"才生效' }}）</label>
          <div v-for="(p, i) in cust" :key="i" class="row nowrap">
            <span class="lbl">J{{ i + 1 }}</span>
            <input class="input mono" type="number" :value="p[0]"
                   @change="cust[i][0] = Number($event.target.value); patch({ custom_limits: cust })" />
            <input class="input mono" type="number" :value="p[1]"
                   @change="cust[i][1] = Number($event.target.value); patch({ custom_limits: cust })" />
          </div>
          <div v-if="!custFilled" class="note warn tiny">请填写六轴限位；未填全时使用 URDF 限位。</div>
        </div>
        

        <div class="row nowrap">
          <span class="lbl">手拖按住上限</span>
          <input class="input input--num" type="number" step="1" :value="snap.settings.free_hold_max_s"
                 @change="patch({ free_hold_max_s: Number($event.target.value) })" />s
          <span class="lbl">力保护阈值</span>
          <input class="input input--num" type="number" step="5" :value="snap.settings.free_force_max_n"
                 @change="patch({ free_force_max_n: Number($event.target.value) })" />N
          <span class="dim tiny grow">0 = 不设力闸（本机 FTData 是否可用需先实测）</span>
        </div>
        <div class="row nowrap">
          <span class="lbl">回放缓冲</span>
          <input class="input input--num" type="number" step="30" :value="snap.settings.play_len_s"
                 @change="patch({ play_len_s: Number($event.target.value) })" />s
          <span class="lbl">日志保留</span>
          <input class="input input--num" type="number" step="200" :value="snap.settings.log_keep"
                 @change="patch({ log_keep: Number($event.target.value) })" />条
        </div>
        <div class="row nowrap" v-for="k in speedKeys" :key="k">
          <span class="lbl">{{ k === 'sim_speed_pct' ? '仿真速度原始值' : '真机速度原始值' }}</span>
          <input class="input input--num" type="number" step="1" :value="snap.settings[k]"
                 @change="patch({ [k]: Number($event.target.value) })" />%
        </div>
        <div class="field">
          <label>AI 模型 / 接口地址（密钥只放项目根 .env，界面上看不到也不写入设置）</label>
          <div class="row nowrap">
            <input class="input grow mono" :value="snap.settings.ai_model"
                   @change="patch({ ai_model: $event.target.value.trim() })" />
          </div>
          <input class="input mono" :value="snap.settings.ai_base_url"
                 @change="patch({ ai_base_url: $event.target.value.trim() })" />
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.lbl { font-size: 11.5px; color: var(--tx-2); white-space: nowrap; }
.saved { color: var(--ok); padding: 3px 8px; background: var(--ok-soft);
         border-radius: 6px; border: 1px solid rgba(46, 230, 168, .3); }
.col.s4 { gap: 4px; }
.field .row + .row { margin-top: 2px; }
.setting-intro { padding: 7px 8px; border: 1px solid var(--line-2); border-radius: 5px; background: rgba(20,35,51,.45); }
.setting-intro > div, .threshold-hd { display: flex; align-items: center; gap: 7px; }
.setting-intro b, .threshold-hd b { color: var(--tx-1); font-size: 12px; }
.setting-intro p { margin: 4px 0 0; color: var(--tx-3); font-size: 10px; line-height: 1.45; }
.status-chip { padding: 2px 5px; border-radius: 3px; color: var(--ok); background: var(--ok-soft); font-size: 10px; }
.status-chip.bad { color: var(--warn); background: var(--warn-soft); }
.safety-source { margin-top: 7px; }
.fence-grid { display: grid; grid-template-columns: 1fr; gap: 6px; margin-top: 7px; }
.fence-axis { min-width: 0; padding: 6px; border: 1px solid var(--line-2); border-radius: 5px; background: rgba(7,16,27,.45); }
.fence-axis-hd { display: flex; justify-content: space-between; gap: 4px; align-items: baseline; margin-bottom: 5px; }
.fence-axis-hd b { font-size: 11px; color: var(--tx-2); }
.fence-axis-hd span { color: var(--tx-3); font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fence-inputs { display: grid; grid-template-columns: minmax(72px,1fr) auto minmax(72px,1fr); gap: 5px; align-items: end; }
.fence-inputs label { display: grid; gap: 2px; min-width: 0; }
.fence-inputs label span { color: var(--tx-3); font-size: 9px; }
.fence-inputs .input { width: 100%; min-width: 0; box-sizing: border-box; }
.fence-inputs input[type=number]::-webkit-inner-spin-button, .fence-inputs input[type=number]::-webkit-outer-spin-button { opacity: .7; }
.range-arrow { color: var(--tx-3); padding-bottom: 5px; }
.threshold-box { margin-top: 8px; padding: 7px 8px; border: 1px solid var(--line-2); border-radius: 5px; }
.threshold-hd span { margin-left: auto; color: var(--tx-3); font-size: 9px; }
.threshold-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 5px; margin-top: 6px; }
.threshold-grid label { display: flex; align-items: center; gap: 4px; color: var(--tx-2); font-size: 10px; white-space: nowrap; }
.threshold-grid .input { width: 42px; min-width: 0; margin-left: auto; }
.threshold-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.threshold-dot.yellow { background: #ffd166; }.threshold-dot.red { background: #ff6b6b; }.threshold-dot.alarm { background: #ff2d55; }
.threshold-help { margin-top: 6px; color: var(--tx-3); font-size: 9px; line-height: 1.4; }
.threshold-help.bad { color: var(--danger, #ff6b6b); }
@media (max-width: 390px) { .fence-grid, .threshold-grid { grid-template-columns: 1fr; } }
</style>
