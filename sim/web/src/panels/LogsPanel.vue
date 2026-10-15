<script setup>
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { logs } = storeToRefs(w)
const kind = ref('')
const KINDS = ['', 'cmd', 'event', 'alarm', 'ai', 'program', 'point', 'link', 'sys',
               'interlock', 'perm', 'jog', 'ready', 'speed', 'power', 'free']
/* 倒序只算一次：原来每次渲染都 slice+reverse，2000 条时白扔两个数组 */
const rows = computed(() => {
  const src = kind.value ? logs.value.filter((r) => r.kind === kind.value) : logs.value
  return src.slice().reverse()
})
const COLOR = {
  alarm: 'bad', interlock: 'warn', ai: 'info', cmd: 'acc', program: 'acc', point: 'mute',
  power: 'warn', free: 'warn',
}
const t = (s) => new Date(s * 1000).toTimeString().slice(0, 8)

/* 虚拟滚动：log_keep 默认 2000 条，全量渲染会让日志页一打开就掉帧。
   行高固定 24px，只画视口上下各一屏的余量。 */
const RH = 24
const box = ref(null)
const top = ref(0)
const view = ref(400)
function onScroll() { if (box.value) top.value = box.value.scrollTop }
onMounted(measure)
function measure() {
  if (box.value) view.value = box.value.clientHeight
}
watch(rows, () => nextTick(measure))
const first = computed(() => Math.max(0, Math.floor(top.value / RH) - 20))
const shown = computed(() => rows.value.slice(first.value,
  first.value + Math.ceil((view.value + 40 * RH) / RH)))
</script>

<template>
  <div class="logs">
    <section class="panel grow">
      <div class="panel-hd">
        <span class="panel-title">日志</span>
        <span class="panel-sub">双轨：指令审计 + 运行事件</span>
        <span class="grow"></span>
        <span class="mono tiny dim">{{ rows.length }} 条 · 屏内 {{ shown.length }}</span>
      </div>
      <div class="panel-bd fill">
        <div class="row nowrap no-fold">
          <select class="select slim" v-model="kind" aria-label="按类别筛选">
            <option v-for="k in KINDS" :key="k" :value="k">{{ k || '全部类别' }}</option>
          </select>
          <a href="./api/logs/export.csv" download><button class="btn">导出 CSV</button></a>
          <button class="btn btn--danger" @click="w.call('/api/logs/clear')">清空</button>
          <span class="grow"></span>
          <span class="dim tiny">超过一屏只渲染视口内的行（虚拟滚动）</span>
        </div>
        <div ref="box" class="tscroll" @scroll.passive="onScroll">
          <div class="thead"><span class="c1">时间</span><span class="c2">类别</span><span>内容</span></div>
          <div class="spacer" :style="{ height: rows.length * RH + 'px' }">
            <div v-for="(r, i) in shown" :key="r.seq" class="lrow"
                 :style="{ transform: `translateY(${(first + i) * RH}px)` }">
              <span class="c1 mono dim">{{ t(r.t) }}</span>
              <span class="c2"><span class="tag" :class="'tag--' + (COLOR[r.kind] || 'mute')">{{ r.kind }}</span></span>
              <span class="mono txt ellip">{{ r.text }}</span>
            </div>
          </div>
          <div v-if="!rows.length" class="dim tiny empty">暂无日志</div>
        </div>
        <div class="note">
          落盘在 <span class="mono">sim/backend/data/events.jsonl</span>；仿真模式下的动作会标注"仿真"，
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
.tscroll { flex: 1; min-height: 0; max-height: none; overflow: auto; position: relative; }
.thead { display: flex; gap: 6px; position: sticky; top: 0; z-index: 2; padding: 4px 6px;
         font-size: 10.5px; letter-spacing: .1em; text-transform: uppercase; color: var(--tx-3);
         background: var(--bg-panel); border-bottom: 1px solid var(--line-2); }
.spacer { position: relative; }
.lrow { display: flex; gap: 6px; align-items: center; height: 24px; padding: 0 6px;
        position: absolute; left: 0; right: 0; top: 0; }
.lrow:hover { background: rgba(122, 162, 220, .05); }
.c1 { flex: 0 0 64px; width: 64px; }
.c2 { flex: 0 0 74px; width: 74px; }
.txt { flex: 1; min-width: 0; font-size: 11.5px; }
.empty { padding: 10px; }
.slim { width: 130px; }
</style>
