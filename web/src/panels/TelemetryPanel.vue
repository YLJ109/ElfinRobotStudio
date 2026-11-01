<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import Spark from '../components/Spark.vue'
import TaskTabs from '../components/TaskTabs.vue'
import { snapshotMetrics, resetMetrics, METRIC_WINDOW_SIZE } from '../runtimeMetrics.js'
import { createPerformanceComparison, performanceIdentity } from '../performanceComparison.js'

const props = defineProps({ active: { type: Boolean, default: true } })
const w = useWorld()
const { snap, tele, play } = storeToRefs(w)
const tab = ref('health')
const tabs = [{id:'health',label:'健康'},{id:'trend',label:'趋势'},{id:'io',label:'IO'},{id:'playback',label:'回放'},{id:'performance',label:'性能'}]
const trendError = ref('')
const runtimeStats = ref({})
const comparison=createPerformanceComparison(), comparisonSlot=ref(''),comparisonNotice=ref(''),comparisonVersion=ref(0)
let comparisonRequest=null,comparisonBuild=null
const comparisonReady=computed(()=>{comparisonVersion.value;return !comparisonSlot.value && !!(comparison.before && comparison.after)})
const comparisonHasBefore=computed(()=>{comparisonVersion.value;return !!comparison.before})
const comparisonVision=computed(()=>{comparisonVersion.value;return comparison.latest})
const comparisonViewport=()=>({width:window.innerWidth,height:window.innerHeight,dpr:window.devicePixelRatio})
const comparisonIdentity=()=>performanceIdentity(snap.value || {},comparisonBuild || {},comparisonViewport())
async function sampleComparison() {
  if(!comparison.active || comparisonRequest || !metricsActive())return
  const request=new AbortController();comparisonRequest=request
  try {
    const response=await fetch('/api/vision/pipeline',{signal:AbortSignal.any([request.signal,AbortSignal.timeout(3000)]),cache:'no-store'})
    if(!response.ok)throw Error('视觉状态读取失败')
    const vision=await response.json()
    if(vision?.ok!==true || typeof vision.enabled!=='boolean')throw Error('视觉状态响应不完整')
    if(!request.signal.aborted && comparison.active && metricsActive()){comparison.sample(comparisonIdentity(),runtimeStats.value,vision);comparisonVersion.value++}
  }catch(error){if(!request.signal.aborted){comparison.stop();comparisonSlot.value='';comparisonNotice.value=error.message}}
  finally{if(comparisonRequest===request)comparisonRequest=null}
}
async function startComparison(slot) {
  if(!metricsActive() || comparison.active || comparisonRequest)return
  const request=new AbortController();comparisonRequest=request;comparisonSlot.value=slot;comparisonNotice.value='正在核对当前版本'
  try {
    const response=await fetch('/api/build',{signal:AbortSignal.any([request.signal,AbortSignal.timeout(3000)]),cache:'no-store'})
    if(!response.ok)throw Error('当前版本读取失败，未开始采样')
    const build=await response.json()
    if(request.signal.aborted || comparisonRequest!==request || !metricsActive())return
    const identity=performanceIdentity(snap.value || {},build,comparisonViewport())
    if(slot==='after' && comparison.before?.identity!==identity)throw Error('前后会话、场景、工具或版本不一致，请重新采样')
    comparisonBuild=build;resetMetrics();comparison.start(identity);comparisonNotice.value=''
    comparisonRequest=null;readMetrics()
  }catch(error){if(!request.signal.aborted){comparisonNotice.value=error.message;comparisonSlot.value=''}}
  finally{if(comparisonRequest===request)comparisonRequest=null}
}
function finishComparison() {
  comparisonRequest?.abort();comparisonRequest=null
  try {const slot=comparisonSlot.value;comparison.finish(slot);comparisonNotice.value=slot==='before'?'前样本已保存':'后样本已保存';comparisonVersion.value++}
  catch(error){comparisonNotice.value=error.message;comparison.stop()}
  comparisonSlot.value=''
}
function exportComparison() {
  try {
    const value=comparison.export(),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}))
    const anchor=document.createElement('a');anchor.href=url;anchor.download='performance-comparison.json'
    try{anchor.click()}finally{URL.revokeObjectURL(url)}
  }catch(error){comparisonNotice.value=error.message}
}
let metricsTimer = null
const metricLabels = {
  'http.request': 'HTTP 请求', 'ik.roundtrip': 'IK 往返', 'preview.roundtrip': '预览往返',
  'state.decode': '状态解码', 'state.interval': '状态到达间隔', 'scene.frame': '场景回调间隔',
  'scene.update': '场景 CPU 更新', 'scene.render': '渲染 CPU 提交', 'longtask': '主线程长任务',
  'scene.load': '三维初始化', 'scene.prepare': '模型表面处理',
}
const metricRows = computed(() => Object.entries(runtimeStats.value).map(([name, stats]) => ({ name, label: metricLabels[name], ...stats })))
function metricsActive() {
  return props.active && tab.value === 'performance' && !suspended && !disposed &&
    (typeof document === 'undefined' || !document.hidden)
}
function readMetrics() { if (metricsActive()) {runtimeStats.value = snapshotMetrics();void sampleComparison()} }
function clearMetrics() { if (metricsActive()) { resetMetrics(); readMetrics() } }

const f = (v, d = 1) => (v === null || v === undefined || v === '' ? '--' : Number(v).toFixed(d))
const st = computed(() => (snap.value ? (snap.value.mode === 'real' ? snap.value.real : snap.value.sim_state) : null))
const pf = computed(() => (tele.value && tele.value.preflight) || null)
const dev = computed(() => (tele.value && tele.value.deviation) || null)
const health = computed(() => (tele.value && tele.value.health) || null)
const isReal = computed(() => !!snap.value && snap.value.mode === 'real')

/* 六维力：前 3 项是力(N)，后 3 项是力矩(N·m)。基线扣除后才是"外力"。 */
const zero = ref(null)
const ft = computed(() => {
  const d = (tele.value || {}).ft_data
  if (!d || d.length < 6) return null
  const z = zero.value
  return d.map((v, i) => (z ? v - z[i] : v))
})
function captureZero() {
  const d = (tele.value || {}).ft_data
  zero.value = d && d.length === 6 ? d.slice() : null
}

const FT_LABEL = ['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz']
const FT_UNIT = ['N', 'N', 'N', 'N·m', 'N·m', 'N·m']
function ftBar(i) {
  const v = ft.value ? ft.value[i] : 0
  const full = i < 3 ? 60 : 20
  return Math.min(100, Math.abs(v) / full * 100).toFixed(0)
}

/* 轴状态/错误码：3 是实测的正常档，其余一律标出来（含义官方表缺失，不猜） */
const STATUS_NORMAL = 3

const ioRows = computed(() => {
  const t = tele.value || {}
  const mk = (name, arr, n, writable) => ({ name, arr: arr || [], n, writable })
  return {
    end: [mk('末端 DO', t.end_do, 3, true), mk('末端 DI', t.end_di, 4),
          mk('末端按键', t.end_button, 4), mk('末端 AI', t.end_ai, 2)],
    box: [mk('电箱 DO', t.box_do, 8, true), mk('电箱 DI', t.box_di, 8),
          mk('电箱 CI', t.box_ci, 8), mk('电箱 CO', t.box_co, 8),
          mk('电箱 AO', t.box_ao, 2), mk('电箱 AI', t.box_ai, 2)],
  }
})
const probing = ref('')
const probeMsg = ref(null)
const probeChannel = ref({ end: '', box: '' })
async function probe(group, bit) {
  if (probing.value || bit === '' || !Number.isInteger(Number(bit)) || Number(bit) < 0 || Number(bit) >= (group === 'end' ? 3 : 8)) return
  bit = Number(bit)
  if (snap.value?.mode === 'real' && !window.confirm(`将对${group === 'end' ? '末端' : '电箱'} DO ${bit + 1}进行实际输出试探，请确认气路和现场安全。`)) return
  probing.value = `${group}:${bit}`
  probeMsg.value = null
  try {
    const r = await w.call('/api/io/probe', { group, bit, val: 1 })
    probeMsg.value = r
  } finally { probing.value = '' }
}

/* ---- 趋势曲线：/api/trend 是 1Hz 落格的缓冲，页面开着才每 5s 拉一次 ---- */
const AX_COLORS = ['#2fd6f0', '#3b82f6', '#2ee6a8', '#ffb020', '#9b7bff', '#ff4d6d']
const trend = ref(null)
let trendTimer = null
let trendAbort = null
let suspended = false
let disposed = false
async function pullTrend() {
  if (!props.active || tab.value !== 'trend' || suspended || disposed || trendAbort) return
  const request = new AbortController()
  trendAbort = request
  try {
    const response = await fetch('/api/trend', { signal: request.signal })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const value = await response.json()
    if (trendAbort === request && props.active && tab.value === 'trend' && !suspended && !disposed) { trend.value = value; trendError.value = '' }
  } catch (e) { if (trendAbort === request && !request.signal.aborted) trendError.value = `趋势刷新失败：${e.message}；保留的是历史数据。` }
  finally { if (trendAbort === request) trendAbort = null }
}
function syncActivity() {
  if (metricsActive()) {
    if (metricsTimer === null) { readMetrics(); metricsTimer = setInterval(readMetrics, 1000) }
  } else {
    clearInterval(metricsTimer); metricsTimer = null
    comparisonRequest?.abort();comparisonRequest=null
    if(comparison.active || comparisonSlot.value){comparison.stop();comparisonSlot.value='';comparisonNotice.value='采样已停止，请保持本页可见后重新开始'}
  }
  if (props.active && tab.value === 'trend' && !suspended && !disposed) {
    if (trendTimer === null) { pullTrend(); trendTimer = setInterval(pullTrend, 5000) }
  } else {
    clearInterval(trendTimer); trendTimer = null
    trendAbort?.abort(); trendAbort = null
  }
  if (!props.active || tab.value !== 'playback' || suspended || disposed) stopPlay()
}
onMounted(syncActivity)
onMounted(() => { if (typeof document !== 'undefined') document.addEventListener('visibilitychange', syncActivity) })
watch(() => props.active, syncActivity, { flush: 'post' })
watch(tab, syncActivity, { flush: 'post' })
onActivated(() => { suspended = false; syncActivity() })
onDeactivated(() => { suspended = true; syncActivity() })
onBeforeUnmount(() => { disposed = true; syncActivity(); if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', syncActivity) })
const tempSeries = computed(() => {
  const t = trend.value
  if (!t || !t.n) return []
  return t.temp.map((d, i) => ({ name: 'J' + (i + 1), color: AX_COLORS[i], data: d, unit: '℃' }))
})
const curSeries = computed(() => {
  const t = trend.value
  if (!t || !t.n) return []
  return t.cur.map((d, i) => ({ name: 'J' + (i + 1), color: AX_COLORS[i], data: d, unit: 'A' }))
})
const devSeries = computed(() => (trend.value && trend.value.n
  ? [{ name: 'FK↔控制器偏差', color: '#2fd6f0', data: trend.value.dev, unit: 'mm' }] : []))
const trendSpan = computed(() => (trend.value ? Math.round(trend.value.dur_s / 60) : 0))

const errAdvice = ref(null)
const errBusy = ref(false)
async function explain() {
  errBusy.value = true
  try { errAdvice.value = await w.call('/api/ai/explain_error', {}) }
  finally { errBusy.value = false }
}

/* ---- 时间轴回放 ---- */
let timer = null
let playbackGeneration = 0
async function togglePlay() {
  if (!props.active || tab.value !== 'playback' || suspended || disposed) return
  if (play.value.on) {
    stopPlay()
    return
  }
  const generation = ++playbackGeneration
  if (!play.value.loaded) await w.playLoad()
  if (generation !== playbackGeneration || !props.active || tab.value !== 'playback' || suspended || disposed || !play.value.n) return
  play.value.on = true
  timer = setInterval(() => {
    const n = play.value.n
    if (!n) return
    w.playSeek(((play.value.idx + 1) % n) / (n - 1 || 1))
  }, 50)
}
function onScrub(ev) { w.playSeek(Number(ev.target.value) / 100) }
function stopPlay() {
  playbackGeneration++
  clearInterval(timer); timer = null
  play.value.on = false
}

const fracPct = computed(() => (play.value.n > 1 ? play.value.idx / (play.value.n - 1) * 100 : 0))
const curT = computed(() => (play.value.t && play.value.t[play.value.idx]) ?? null)
const playQ = computed(() => {
  if (!play.value.on || !play.value.n || !play.value.cols.length) return null
  return [0, 1, 2, 3, 4, 5].map((j) => play.value.cols[j][play.value.idx])
})
defineExpose({ playQ, stopPlay })
</script>

<template>
  <div class="col tele">
    <TaskTabs v-model="tab" :tabs="tabs" id="telemetry" label="遥测分类" storage-key="huayan.tabs.telemetry.v1" />
    <div id="telemetry-panel" class="col" role="tabpanel" :aria-labelledby="`telemetry-tab-${tab}`" tabindex="0">
    <div v-if="tab !== 'performance'" class="note" :class="!w.connected ? 'warn' : ''" role="status">{{ !w.connected ? '后端已断开，以下为最后一次遥测，现态未知。' : isReal ? '真机遥测 · 1Hz 状态流；缺失字段不代表正常。' : '仿真遥测 · 温度、电流和真实 IO 未验证。' }}</div>
    <section v-if="tab === 'performance'" class="panel">
      <div class="panel-hd"><span class="panel-title">本地性能</span><span class="grow"></span><button class="btn btn--xs no-fold" @click="clearMetrics">重置样本</button></div>
      <div class="panel-bd col">
        <p class="dim tiny">最近 {{ METRIC_WINDOW_SIZE }} 样本，ms，GPU 未测量。</p>
        <div class="row"><button class="btn btn--xs" :disabled="!!comparisonSlot" @click="startComparison('before')">采集前样本</button><button class="btn btn--xs" :disabled="!!comparisonSlot || !comparisonHasBefore" @click="startComparison('after')">采集后样本</button><button class="btn btn--xs" :disabled="!comparisonSlot" @click="finishComparison">结束采样</button><button class="btn btn--xs" :disabled="!comparisonReady" @click="exportComparison">导出对比</button></div>
        <p v-if="comparisonSlot || comparisonNotice" class="tiny" role="status">{{comparisonSlot ? '正在采样，完成后点结束采样' : comparisonNotice}}</p>
        <p class="dim tiny">前后须保持会话、场景、工具、版本及窗口一致。每秒采样，最多保留120条；工件检测、物理采集、真实显示帧率、GPU与进程占用未测。</p>
        <p v-if="comparisonVision" class="tiny">手掌检测输入 {{f(comparisonVision.input_hz,1)}} / 推理 {{f(comparisonVision.detection_hz,1)}} 次/秒 · 手掌可信帧龄 {{f(comparisonVision.frame_age_ms,0)}} ms</p>
        <div class="metrics-scroll"><table class="metrics-table"><thead><tr><th>测量项</th><th>n</th><th>p50</th><th>p95</th><th>max</th></tr></thead><tbody>
          <tr v-for="row in metricRows" :key="row.name"><td>{{ row.label }}</td><td class="mono">{{ row.n }}</td><template v-if="row.n"><td class="mono">{{ f(row.p50, 2) }}</td><td class="mono">{{ f(row.p95, 2) }}</td><td class="mono">{{ f(row.max, 2) }}</td></template><td v-else colspan="3" class="dim">未采样</td></tr>
        </tbody></table></div>
        <details class="tiny"><summary>统计说明</summary><div class="col">
          <p>每项最近 {{ METRIC_WINDOW_SIZE }} 个样本，每秒刷新。n 是窗口样本数；p50 / p95 使用最近秩法，max 为窗口最大值。</p>
          <p>scene.frame 是可见页面的场景回调间隔，max 可用于定位长帧；CPU 更新与渲染提交分别计时。提交耗时不是 GPU 执行耗时或实际 GPU 帧率。</p>
          <p>longtask 仅记录浏览器支持时新发生的主线程长任务；无样本不代表没有卡顿。切回页面不计入后台停留时长。</p>
          <p>仅在本机内存保存名称与耗时，重置不写后端。</p>
          <dl class="kv"><template v-for="row in metricRows" :key="row.name"><dt>{{ row.label }}</dt><dd class="mono">{{ row.name }}</dd></template></dl>
        </div></details>
      </div>
    </section>
    <!-- 就绪体检 -->
    <div v-if="tab === 'health'" class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">就绪体检</span>
        <span class="grow"></span>
        <span v-if="pf" class="tag" :class="pf.all_ok ? 'tag--ok' : 'tag--bad'">
          {{ pf.all_ok ? '全绿' : '挡住 ' + (pf.blocking || []).length + ' 项' }}
        </span>
      </div>
      <div class="panel-bd">
        <div v-if="!pf" class="note">还没收到遥测帧（1Hz 通道），稍等或检查链路。</div>
        <div v-else class="checks">
          <div v-for="r in pf.rows" :key="r.k" class="chk-row" :class="r.state ? 'ok' : 'no'">
            <span class="mk">{{ r.state ? '✓' : '✗' }}</span>
            <span class="lb">{{ r.label }}<i class="en mono">{{ r.sub }}</i></span>
            <span class="dt mono">{{ r.detail }}</span>
            <span class="fx muted tiny" v-if="!r.state">{{ r.fix }}</span>
          </div>
        </div>
        <div v-if="pf && pf.sim_note" class="note tiny">仿真模式：真机相关的项不阻塞，但也不会假装通过。</div>
      </div>
    </div>

    <!-- 影子孪生：模型可信度 -->
    <div v-if="tab === 'health'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">模型可信度</span>
        <span class="panel-sub mono">FK(q) ↔ CONTROLLER PCS</span>
      </div>
      <div class="panel-bd">
        <div v-if="!isReal" class="note">仿真模式下没有控制器读数可比，这条只在真机模式出数。</div>
        <div v-else-if="dev && !dev.ok" class="note warn">{{ dev.why }}</div>
        <template v-else-if="dev">
          <div class="cells">
            <div class="cell"><span class="lbl">位置偏差</span>
              <span class="val" :class="dev.level === 'green' ? '' : 'bad'">{{ f(dev.pos_mm, 2) }}
                <i class="u">mm</i></span></div>
            <div class="cell"><span class="lbl">姿态偏差</span>
              <span class="val" :class="dev.level === 'green' ? '' : 'bad'">{{ f(dev.rot_deg, 2) }}
                <i class="u">°</i></span></div>
            <div class="cell"><span class="lbl">判定</span>
              <span class="val">{{ { green: '模型可信', red: '开始偏离', alarm: '明显不符' }[dev.level] || dev.level }}</span></div>
          </div>
          <div class="note tiny">{{ dev.note }}；阈值 3mm/1° 起黄、10mm/3° 起红，为本软件自设。</div>
        </template>
      </div>
    </div>

    <!-- 轴健康 -->
    <div v-if="tab === 'health'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">轴健康</span>
      </div>
      <div class="panel-bd flush">
        <table v-if="health">
          <thead><tr><th>轴</th><th>角度</th><th>限位占用</th><th>温度</th><th>电流</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="a in health.axes" :key="a.j" :class="{ pick: a.pct >= (w.snap?.settings?.safety_yellow_pct ?? 90) }">
              <td class="mono">J{{ a.j }}</td>
              <td class="mono">{{ f(a.q, 2) }}°</td>
              <td>
                <div class="bar" :class="a.pct >= (w.snap?.settings?.safety_red_pct ?? 95) ? 'bad' : a.pct >= (w.snap?.settings?.safety_yellow_pct ?? 90) ? 'warn' : ''">
                  <i :style="{ width: Math.min(100, a.pct) + '%' }"></i>
                </div>
                <span class="mono tiny">{{ f(a.pct, 1) }}%</span>
              </td>
              <td class="mono">{{ f(a.temp, 1) }}℃</td>
              <td class="mono">{{ f(a.current, 2) }}</td>
              <td><span class="tag" :class="a.err ? 'tag--bad' : a.status === 3 ? 'tag--mute' : 'tag--warn'">
                {{ a.status }}<template v-if="a.err"> / {{ a.err }}</template></span></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="note" style="padding: 10px">无遥测帧。</div>
        <div v-if="health" class="note tiny" style="margin: 6px 10px">{{ health.note }}</div>
      </div>
    </div>

    <!-- 趋势曲线 -->
    <div v-if="tab === 'trend'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">趋势</span>
        <span class="panel-sub mono">1Hz · 约 {{ trendSpan }} 分钟窗口</span>
        <span class="grow"></span>
        <button class="btn btn--xs no-fold" @click="pullTrend">刷新</button>
      </div>
      <div class="panel-bd">
        <div v-if="trendError" class="note warn" role="status">{{ trendError }}</div>
        <div class="tgrp">
          <span class="upper mono">关节温度 ℃（虚线=60℃ 本软件自设阈值）</span>
          <Spark :series="tempSeries" :ref-lines="[{ v: 60, color: 'rgba(255,176,32,.5)' }]" />
        </div>
        <div class="tgrp">
          <span class="upper mono">关节电流 A</span>
          <Spark :series="curSeries" :height="54" />
        </div>
        <div class="tgrp">
          <span class="upper mono">模型可信度：FK(q) ↔ 控制器 PCS 位置偏差 mm</span>
          <Spark :series="devSeries" :height="46" />
        </div>
        <div v-if="!trend || !trend.n" class="note tiny">
          趋势要 1Hz 遥测通道跑起来才有数据；仿真模式下没有温度/电流，只会画偏差那条。
        </div>
      </div>
    </div>

    <!-- 力与力矩 -->
    <div v-if="tab === 'health'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">末端六维力 / 力矩</span>
        <span class="panel-sub mono">FTData</span>
        <span class="grow"></span>
        <button class="btn btn--xs" @click="captureZero" :disabled="!ft">取当前为零位</button>
      </div>
      <div class="panel-bd">
        <div v-if="!ft" class="note">控制器没给 FTData（这台机可能没装力传感器或该字段未启用）。</div>
        <div v-else class="ft">
          <div v-for="(l, i) in FT_LABEL" :key="l" class="ftrow">
            <span class="fl mono">{{ l }}</span>
            <div class="bar"><i :style="{ width: ftBar(i) + '%' }"></i></div>
            <b class="fv mono">{{ f(ft[i], 2) }}<i class="u">{{ FT_UNIT[i] }}</i></b>
          </div>
          <div class="note tiny">基线：{{ zero ? '已扣除取零时刻的读数' : '未取零（下面含末端自重与安装偏置）' }}；
            FTControlState={{ (tele || {}).ft_state ?? '--' }}。手拖示教的力保护用这组数。</div>
        </div>
      </div>
    </div>

    <!-- 电源与通讯 -->
    <div v-if="tab === 'health'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">电源与通讯</span>
      </div>
      <div class="panel-bd">
        <dl class="kv">
          <dt>48V 输入</dt><dd>{{ f((tele || {}).volt48_in, 2) }} V / {{ f((tele || {}).amp48_in, 2) }} A</dd>
          <dt>48V 输出</dt><dd>{{ f((tele || {}).volt48_out, 2) }} V / {{ f((tele || {}).amp48_out, 2) }} A</dd>
          <dt>轴供电</dt><dd>{{ (((tele || {}).joint_volt) || []).map(v => f(v, 1)).join(' / ') || '--' }} V</dd>
          <dt>EtherCAT</dt><dd>{{ f((tele || {}).ecat_fps, 0) }} 帧/秒 · 累计 {{ (tele || {}).ecat_total ?? '--' }}</dd>
          <dt>丢帧/错帧</dt>
          <dd :class="((tele || {}).ecat_lost || 0) > 0 ? 'bad' : 'ok'">
            lost {{ (tele || {}).ecat_lost ?? '--' }} · tx {{ (tele || {}).ecat_tx_err ?? '--' }} · rx {{ (tele || {}).ecat_rx_err ?? '--' }}</dd>
          <dt>取流</dt><dd>{{ f((snap || {}).real && (snap.real || {}).rate_hz, 1) }} Hz ·
            滞后 {{ f((snap || {}).real && (snap.real || {}).lag_ms, 0) }} ms</dd>
        </dl>
      </div>
    </div>

    <!-- IO 全景 + 标定向导 -->
    <div v-if="tab === 'io'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">IO 全景与标定</span>
      </div>
      <div class="panel-bd">
        <div v-for="(rows, grp) in ioRows" :key="grp" class="io-grp">
          <div class="upper mono">{{ grp === 'end' ? '末端 END' : '电箱 BOX' }}</div>
          <div v-for="r in rows" :key="r.name" class="io-row">
            <span class="io-n">{{ r.name }}</span>
            <span v-for="(v, i) in Array.from({ length: r.n })" :key="i" class="bit"
                  :class="{ hi: Number((r.arr || [])[i]) }">
              {{ (r.arr || [])[i] ?? '-' }}<i>{{ (grp === 'end' ? 'E' : 'B') + r.name.slice(-2) + (i + 1) }}</i>
            </span>
            <template v-if="r.writable">
              <select v-model="probeChannel[grp]" class="select" :disabled="!!probing" aria-label="标定输出通道">
                <option value="" disabled>选择通道</option>
                <option v-for="i in r.n" :key="i" :value="i - 1">DO {{ i }}</option>
              </select>
              <button class="btn btn--xs no-fold" :disabled="!!probing || probeChannel[grp] === ''" @click="probe(grp, probeChannel[grp])">
                {{ probing === `${grp}:${probeChannel[grp]}` ? '采样中…' : '标定向导' }}
              </button>
            </template>
          </div>
        </div>
        <div v-if="probeMsg" class="note tiny" :class="probeMsg.ok ? '' : 'warn'">
          {{ probeMsg.why || '试探完成' }}
          <template v-if="probeMsg.diff && probeMsg.diff.length">
            <ul><li v-for="d in probeMsg.diff" :key="d.ch" class="mono">
              {{ d.ch }}：灭 {{ d.off }} → 亮 {{ d.on }}（复位后 {{ d.back }}）</li></ul>
          </template>
        </div>
        <div class="note tiny">标定向导只点亮你点的那一路，然后回读输入变化；不盲扫全部通道，
          免得把吸盘和气阀一起激活。</div>
      </div>
    </div>

    <!-- 控制器里的脚本 -->
    <div v-if="tab === 'playback'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">控制器脚本执行位</span>
        <span class="panel-sub mono">Script.cmdid</span>
      </div>
      <div class="panel-bd">
        <div class="row">
          <span class="tag tag--mute mono">errorCode {{ (tele || {}).script_err ?? '--' }}</span>
          <span class="tag tag--mute mono">changed {{ (tele || {}).script_changed ?? '--' }}</span>
        </div>
        <div class="mono tiny cmdid">
          {{ ((tele || {}).script_cmdid || []).join(' | ') || '（无：控制器侧当前没有在执行脚本，或该字段为空）' }}
        </div>
        <div class="note tiny">这是控制器自己的程序执行栈（callstack:
          {{ ((tele || {}).script_callstack || []).join(',') || '空' }}）。
          本软件的程序执行走的是网络指令，两边不同源，别把它当成本页进度条。</div>
      </div>
    </div>

    <!-- 故障解说 -->
    <div v-if="tab === 'health'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">故障码解说</span>
        <span class="grow"></span>
        <button class="btn btn--xs no-fold" @click="explain" :disabled="errBusy">
          {{ errBusy ? '推理中…' : '给出处置建议' }}</button>
      </div>
      <div class="panel-bd">
        <div class="row">
          <span class="tag" :class="st && st.err_code ? 'tag--bad' : 'tag--ok'">
            Error_Code {{ (st || {}).err_code ?? '--' }}</span>
          <span class="tag tag--mute">轴 {{ (st || {}).err_axis ?? '--' }}</span>
          <span class="tag tag--mute">FSM {{ (st || {}).fsm ?? '--' }} {{ ((snap || {}).ctrl || {}).fsm_hint || '' }}</span>
        </div>
        <template v-if="errAdvice">
          <div class="note tiny">{{ errAdvice.disclaimer }}</div>
          <div v-if="errAdvice.risk" class="note warn tiny">先做：{{ errAdvice.risk }}</div>
          <ol v-if="(errAdvice.likely || []).length" class="adv">
            <li v-for="(a, i) in errAdvice.likely" :key="i">
              <b>{{ a.cause }}</b><div class="muted tiny">确认：{{ a.check }}</div>
              <div class="tiny">处置：{{ a.fix }}</div>
            </li>
          </ol>
          <div v-if="errAdvice.need_vendor" class="note tiny bad">{{ errAdvice.need_vendor }}</div>
          <div v-if="errAdvice.why" class="note tiny">{{ errAdvice.why }}</div>
        </template>
      </div>
    </div>

    <!-- 设备身份与诊断包 -->
    <div v-if="tab === 'health'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">设备与诊断包</span>
      </div>
      <div class="panel-bd">
        <dl class="kv">
          <dt>型号</dt><dd>{{ (tele || {}).model_name || ((snap || {}).real || {}).model || '--' }}</dd>
          <dt>SN</dt><dd class="mono">{{ (tele || {}).device_sn || '--' }}</dd>
          <dt>MAC</dt><dd class="mono">{{ (tele || {}).device_mac || '--' }}</dd>
          <dt>授权</dt><dd class="mono">{{ (tele || {}).auth_left || '--' }}
            / 已用 {{ (tele || {}).auth_used || '--' }} 分钟</dd>
          <dt>控制器时钟</dt><dd class="mono">{{ (tele || {}).sys_date || '--' }}
            · 帧时 {{ (snap || {}).real && (snap.real || {}).update_time }}</dd>
        </dl>
        <div class="row">
          <a class="btn btn--xs" href="/api/diag" download>下载诊断包</a>
          <span class="note tiny grow">含状态、设置、最近 200 条事件、最近一帧原始 Data Sheet。
            默认对 SN/MAC/授权码打码。</span>
        </div>
      </div>
    </div>

    <!-- 时间轴回放 -->
    <div v-if="tab === 'playback'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">时间轴回放</span>
        <span class="grow"></span>
        <button class="btn btn--xs no-fold" :class="play.on ? 'btn--on' : 'btn--primary'" @click="togglePlay">
          {{ play.on ? '停止回放' : '开始回放' }}</button>
      </div>
      <div class="panel-bd">
        <div v-if="!play.n" class="note tiny">缓冲里还没有数据（后端 20Hz 落格，真机取流断开时不会增长）。</div>
        <template v-else>
          <div class="pbar">
            <input type="range" min="0" max="100" step="0.2" :value="fracPct.toFixed(1)"
                   @input="onScrub" @pointerdown="stopPlay" aria-label="回放游标" />
            <b class="mono">{{ f(curT, 1) }}<i class="u">s</i> / {{ f(play.dur, 1) }}s</b>
          </div>
          <div class="mono tiny">{{ play.n }} 帧 · 抽稀后本地播放（不再打网络）。
            回放臂用那台半透明假手显示，与残影互斥。</div>
          <ul v-if="play.marks.length" class="marks">
            <li v-for="(m, i) in play.marks" :key="i" class="tiny mono" :class="m[2]">
              {{ f(m[0], 1) }}s — {{ m[1] }}</li>
          </ul>
        </template>
      </div>
    </div>
    </div>
  </div>
</template>


<style scoped>
.metrics-scroll { overflow-x: auto; min-width: 0; }
.metrics-table { width: 100%; font-size: 11px; }
.metrics-table th, .metrics-table td { padding: 4px; }
.tele { gap: 6px; }
.tgrp { display: flex; flex-direction: column; gap: 2px; }
.checks { display: flex; flex-direction: column; gap: 2px; }
.chk-row { display: grid; grid-template-columns: 16px minmax(0, 1.1fr) minmax(0, 1fr); gap: 6px;
           align-items: baseline; font-size: 12px; padding: 3px 4px; border-radius: 5px; }
.chk-row:nth-child(odd) { background: rgba(122, 162, 220, .04); }
.chk-row .mk { font-weight: 700; }
.chk-row.ok .mk { color: var(--ok); }
.chk-row.no { background: var(--warn-soft); }
.chk-row.no .mk { color: var(--danger); }
.chk-row .lb { color: var(--tx-1); }
.chk-row .lb .en { display: block; font-size: 9px; letter-spacing: .12em; color: var(--tx-3); font-style: normal; }
.chk-row .dt { color: var(--acc); font-size: 11.5px; }
.chk-row .fx { grid-column: 2 / -1; color: var(--warn); }

.cell .val .u { font-style: normal; font-size: 10px; color: var(--tx-3); margin-left: 1px; }
tr.pick { background: rgba(255, 176, 32, .08); }

.ft { display: flex; flex-direction: column; gap: 3px; }
.ftrow { display: grid; grid-template-columns: 30px 1fr 84px; gap: 6px; align-items: center; }
.fl { font-size: 11px; color: var(--tx-3); }
.fv { font-size: 12.5px; color: var(--acc); text-align: right; font-variant-numeric: tabular-nums; }
.fv .u { font-style: normal; font-size: 9.5px; color: var(--tx-3); margin-left: 2px; }

.io-grp { display: flex; flex-direction: column; gap: 3px; }
.upper { font-size: 9.5px; letter-spacing: .14em; color: var(--tx-3); }
.io-row { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.io-n { font-size: 11.5px; color: var(--tx-2); width: 62px; flex: 0 0 62px; }
.bit { display: inline-flex; align-items: center; gap: 2px; font-family: var(--font-mono);
       font-size: 11px; padding: 1px 5px; border: 1px solid var(--line-2); border-radius: 5px;
       color: var(--tx-3); background: var(--bg-inset); }
.bit i { font-style: normal; font-size: 8.5px; opacity: .5; }
.bit.hi { color: #04131a; background: var(--acc); border-color: transparent; font-weight: 700; }
.cmdid { color: var(--acc); word-break: break-all; }
.adv { display: flex; flex-direction: column; gap: 4px; padding-left: 14px; font-size: 12px; }
.pbar { display: flex; align-items: center; gap: 8px; }
.pbar input { flex: 1; }
.pbar b { font-size: 12.5px; color: var(--acc); font-variant-numeric: tabular-nums; }
.pbar .u { font-style: normal; font-size: 9.5px; color: var(--tx-3); }
.marks { display: flex; flex-direction: column; gap: 1px; max-height: 78px; overflow: auto;
         color: var(--tx-3); padding-left: 10px; }
.marks .bad { color: var(--danger); }
</style>
