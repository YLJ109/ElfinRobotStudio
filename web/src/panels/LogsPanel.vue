<script setup>
import { ref, computed, nextTick, watch, onActivated, onBeforeUnmount, onDeactivated, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { useLogFollow } from '../logFollow.js'
import { filterLogRows } from '../workspaceInsights.js'
import { summarizeLog, logKindLabel } from '../logSummary.js'

const props = defineProps({ active: { type: Boolean, default: true } })
const w = useWorld()
const { logs } = storeToRefs(w)
const kind = ref('')
const keyword = ref(''), task = ref(''), collapse = ref(false), selected = ref(null)
const tasks = computed(() => [...new Set(logs.value.map(row => row.extra?.task_id ?? row.extra?.epoch).filter(value => value != null).map(String))])
const KINDS = ['', 'cmd', 'event', 'alarm', 'ai', 'program', 'point', 'link', 'sys',
               'interlock', 'perm', 'jog', 'ready', 'speed', 'power', 'free', 'execution']
// Match the dock: oldest first, newest at the bottom. Never reverse store data.
const rows = computed(() => filterLogRows(logs.value, { kind: kind.value, task: task.value, keyword: keyword.value, collapse: collapse.value }))
const matchedCount = computed(() => rows.value.reduce((n, row) => n + row.count, 0))
const COLOR = {
  alarm: 'bad', interlock: 'warn', ai: 'info', cmd: 'acc', program: 'acc', point: 'mute',
  power: 'warn', free: 'warn',
}
const t = (s) => new Date(s * 1000).toTimeString().slice(0, 8)

/* 虚拟滚动：log_keep 默认 2000 条，全量渲染会让日志页一打开就掉帧。
   行高固定 24px，只画视口上下各一屏的余量。 */
const RH = 24
const box = ref(null)
const surface = ref(null)
const top = ref(0)
const view = ref(400)
function onScroll() { if (box.value) top.value = box.value.scrollTop }
const { following, goLatest } = useLogFollow({ box, content: rows, interactionRoot: surface,
  active: computed(() => props.active), onScroll, storageKey:'huayan.log.reader.full.v1' })
let suspended = false
let disposed = false
let resizeObserver = null
function measure() {
  const height = box.value?.clientHeight
  if (props.active && !suspended && !disposed && height > 0) view.value = height
}
function syncMeasurement() {
  resizeObserver?.disconnect(); resizeObserver = null
  if (!props.active || suspended || disposed) return
  nextTick(() => {
    if (!props.active || suspended || disposed || !box.value) return
    measure()
    if (!resizeObserver && typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(measure)
      resizeObserver.observe(box.value)
    }
  })
}
onMounted(syncMeasurement)
watch(() => props.active, syncMeasurement, { flush: 'post' })
onActivated(() => { suspended = false; syncMeasurement() })
onDeactivated(() => { suspended = true; syncMeasurement() })
onBeforeUnmount(() => { disposed = true; syncMeasurement() })
watch(rows, () => nextTick(measure))
// Filtering/clearing can shrink the spacer before the browser delivers its
// clamped scroll event. Keep the virtual range valid during that whole update.
const first = computed(() => Math.min(Math.max(0, Math.floor(top.value / RH) - 20),
  Math.max(0, rows.value.length - Math.ceil((view.value + 40 * RH) / RH))))
const shown = computed(() => rows.value.slice(first.value,
  first.value + Math.ceil((view.value + 40 * RH) / RH)).map(row=>({...row,summary:summarizeLog(row)})))
</script>

<template>
  <div ref="surface" class="logs" @keydown.space.stop>
    <section class="panel grow">
      <div class="panel-hd">
        <span class="panel-title">日志</span>
        <span class="panel-sub">指令审计 · 运行事件</span>
        <span class="grow"></span>
        <span class="mono tiny dim">{{ matchedCount }} 事件 / {{ rows.length }} 行 · 屏内 {{ shown.length }}</span>
      </div>
      <div class="panel-bd fill">
        <div class="row nowrap no-fold">
          <select class="select slim" v-model="kind" aria-label="按类别筛选">
            <option v-for="k in KINDS" :key="k" :value="k">{{ k ? logKindLabel(k) : '全部类别' }}</option>
          </select>
          <a href="./api/logs/export.csv" download><button class="btn">导出 CSV</button></a>
          <button class="btn btn--danger" @click="w.call('/api/logs/clear')">清空</button>
          <span class="grow"></span>
          <span class="dim tiny">超过一屏只渲染视口内的行（虚拟滚动）</span>
        </div>
        <div class="row no-fold">
          <input class="input grow" v-model="keyword" type="search" placeholder="关键词（含事件元数据）" aria-label="日志关键词" />
          <select class="select slim" v-model="task" aria-label="按任务筛选"><option value="">全部任务</option><option v-for="id in tasks" :key="id" :value="id">任务 {{ id }}</option></select>
          <label class="tiny"><input type="checkbox" v-model="collapse" />折叠连续重复事件</label>
        </div>
        <div ref="box" class="tscroll" tabindex="0" aria-label="日志内容">
          <div class="thead"><span class="c1">时间</span><span class="c2">类别</span><span>内容</span></div>
          <div class="spacer" :style="{ height: rows.length * RH + 'px' }">
            <div v-for="(r, i) in shown" :key="r.seq" :data-log-seq="r.seq" class="lrow"
                 :style="{ transform: `translateY(${(first + i) * RH}px)` }">
              <span class="c1 mono dim">{{ t(r.t) }}</span>
              <span class="c2"><span class="tag" :title="r.kind" :class="'tag--' + (COLOR[r.kind] || 'mute')">{{ logKindLabel(r.kind) }}</span></span>
              <button type="button" class="mono txt ellip log-detail" :title="r.summary.original" @click="selected = r">{{ r.summary.text }}<b v-if="r.count > 1"> ×{{ r.count }}</b></button>
            </div>
          </div>
          <div v-if="!rows.length" class="dim tiny empty">{{ logs.length ? '没有匹配事件，请调整筛选条件' : '暂无日志' }}</div>
        </div>
        <div v-if="selected" class="event-detail note" role="region" aria-label="事件详情">
          <div class="row"><b>事件 #{{ selected.seq }}<template v-if="selected.count > 1">–#{{ selected.lastSeq }} · {{ selected.count }} 次 · {{ t(selected.t) }}–{{ t(selected.lastT) }}</template></b><button type="button" class="btn btn--xs" @click="selected = null">关闭详情</button></div>
          <p>{{ selected.text }}</p><pre v-if="selected.extra">{{ JSON.stringify(selected.extra, null, 2) }}</pre>
          <span class="tiny dim">仅显示实际记录。当前编辑器与三维未提供可验证的日志定位入口，因此不显示跳转按钮；历史行号也不等于当前程序版本。</span>
        </div>
        <div class="log-follow-bar">
          <span class="tiny dim" role="status">{{ following ? '自动跟随最新' : '查看历史 · 空闲10秒后跟随' }}</span>
          <button v-if="!following" type="button" class="btn btn--xs" @click="goLatest">回到最新</button>
        </div>
        <div class="note">
          落盘在 <span class="mono">backend/data/events.jsonl</span>；仿真模式下的动作会标注"仿真"，
          不会与真机记录混在一起。导出 CSV 已对以 <span class="mono">= + - @</span> 开头的值加前缀，
          防 Excel 把它当公式执行。
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 表格撑满整栏，只有它自己滚 */
.logs { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.grow { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.panel-bd.fill { flex: 1; min-height: 0; overflow: hidden; }
/* 全局 .tscroll 有条 max-height:46vh，会把拉伸后的表格重新锁死，这里必须显式放开 */
.tscroll { flex: 1; min-height: 0; max-height: none; overflow: auto; position: relative;
           overflow-anchor:none; scroll-behavior:auto; }
.log-follow-bar { display:flex; align-items:center; justify-content:space-between; gap:8px;
                  flex-shrink:0; min-height:28px; padding:2px 0; }
.thead { display: flex; gap: 6px; position: sticky; top: 0; z-index: 2; padding: 4px 6px;
         font-size: 10.5px; letter-spacing: .1em; text-transform: uppercase; color: var(--tx-3);
         background: var(--bg-panel); border-bottom: 1px solid var(--line-2); }
.spacer { position: relative; }
.lrow { display: flex; gap: 6px; align-items: center; height: 24px; padding: 0 6px;
        position: absolute; left: 0; right: 0; top: 0; }
.lrow:hover { background: rgba(122, 162, 220, .05); }
.c1 { flex: 0 0 64px; width: 64px; }
.c2 { flex: 0 0 74px; width: 74px; }
.txt { flex: 1; min-width: 0; font-size: 11.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.empty { padding: 10px; }
.slim { width: 130px; }
.log-detail { border:0; background:transparent; color:var(--tx-1); text-align:left; cursor:pointer; padding:2px 0; }
.log-detail:focus-visible { outline:2px solid var(--acc); }
.event-detail { max-height:180px; overflow:auto; flex:none; overflow-wrap:anywhere; }
.event-detail p, .event-detail pre { white-space:pre-wrap; margin:4px 0; font-size:11px; }
</style>
