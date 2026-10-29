<script setup>
import { ref, computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import { summarizeLog, logKindLabel } from '../logSummary.js'
import { useLogFollow } from '../logFollow.js'

const w = useWorld()
const { logs } = storeToRefs(w)
const open = ref(localStorage.getItem('huayan.log.open') !== '0')
watch(open, (v) => localStorage.setItem('huayan.log.open', v ? '1' : '0'))
const box = ref(null)
const contentBox = ref(null)

const CK = { alarm: 'bad', interlock: 'warn', ai: 'info', program: 'acc', point: 'acc',
             cmd: 'acc', speed: 'dim', jog: 'dim', ready: 'dim', perm: 'warn', link: 'dim', sys: 'dim' }
const rows = computed(() => logs.value.slice(-120).map((r) => ({ ...r, summary: summarizeLog(r) })))
const bad = computed(() => rows.value.filter((r) => r.kind === 'alarm' || r.kind === 'interlock').length)
const hm = (t) => new Date(t * 1000).toTimeString().slice(0, 8)
const { following, goLatest } = useLogFollow({ box, content: rows, active: open, resizeTarget: contentBox, storageKey:'huayan.log.reader.dock.v1' })
</script>

<template>
  <button v-if="!open" class="dock log-trigger btn btn--xs" aria-controls="runtime-log-panel"
          :aria-expanded="false" title="展开运行日志" @click="open = true">运行日志</button>
  <section v-else id="runtime-log-panel" class="dock panel no-fold" aria-label="运行日志">
    <div class="panel-hd">
      <span class="panel-title">执行日志</span>
      <span class="panel-sub">运行记录</span>
      <span class="grow"></span>
      <span v-if="bad" class="tag tag--bad">{{ bad }}</span>
      <span class="dim tiny mono">{{ rows.length }}</span>
      <button type="button" class="btn btn--xs no-fold log-close" title="收起运行日志" aria-label="收起运行日志"
              :aria-expanded="true" aria-controls="runtime-log-panel" @click.stop="open = false">
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.4" /></svg>
      </button>
    </div>
    <div ref="box" class="body mono" tabindex="0" aria-label="运行日志内容">
      <div ref="contentBox" class="log-lines">
      <div v-for="r in rows" :key="r.seq" :data-log-seq="r.seq" class="ln">
        <span class="t">{{ hm(r.t) }}</span>
        <span class="k" :class="CK[r.kind] || 'dim'" :title="r.kind">{{ logKindLabel(r.kind) }}</span>
        <details class="message" :class="{bad:r.kind==='alarm'}">
          <summary :title="r.summary.original">{{ r.summary.text }}</summary>
          <div class="original"><span class="original-label">原文</span>{{ r.summary.original }}</div>
          <pre v-if="r.extra" class="original">{{ JSON.stringify(r.extra, null, 2) }}</pre>
        </details>
      </div>
      <div v-if="!rows.length" class="ln dim">暂无记录</div>
      </div>
    </div>
    <div class="log-follow-bar">
      <span class="tiny dim" role="status">{{ following ? '自动跟随最新' : '查看历史 · 空闲10秒后跟随' }}</span>
      <button v-if="!following" type="button" class="btn btn--xs" @click="goLatest">回到最新</button>
    </div>
  </section>
</template>

<style scoped>
.dock { display: flex; flex-direction: column; overflow: hidden; backdrop-filter: blur(6px); }
.dock > .panel-hd { cursor: default; user-select: text; }
.dock.panel > .panel-hd::after { content: none !important; display: none !important; }
.log-close { padding: 0 5px; min-width: 26px; }
.dock.log-trigger { width:max-content !important; max-width:calc(100% - 20px) !important;
                    flex-direction:row; backdrop-filter:none; white-space:nowrap; }
.body { height: 210px; overflow: auto; padding: 4px 6px; font-size: 11px; line-height: 1.55;
        background: rgba(2, 5, 9, .72); border-top: 1px solid var(--line); overflow-anchor:none; scroll-behavior:auto; }
.log-follow-bar { display:flex; align-items:center; justify-content:space-between; gap:6px;
                  min-height:25px; padding:2px 6px; border-top:1px solid var(--line); }
.ln { display: flex; gap: 6px; white-space: nowrap; color: var(--tx-2); }
.t { flex: 0 0 49px; color: var(--tx-3); font-size:10px; }
.k { flex: 0 0 46px; font-size:10px; overflow:hidden; text-overflow:ellipsis; }
.message { flex:1; min-width:0; }
summary { cursor:pointer; list-style:none; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
summary::-webkit-details-marker { display:none; }
summary::after { content:' ＋'; color:var(--tx-3); font-size:9px; }
details[open] summary::after { content:' −'; }
summary:focus-visible { outline:1px solid var(--acc); outline-offset:2px; }
.original { margin:3px 0 5px; padding-left:6px; border-left:1px solid var(--line); color:var(--tx-3); white-space:pre-wrap; overflow-wrap:anywhere; }
.original-label { display:block; font-size:9px; }
.k.acc { color: var(--acc); }
.k.warn { color: var(--warn); }
.k.bad, .ln .bad { color: #ff8fa3; }
.k.info { color: #7ad2ff; }
.k.dim { color: var(--tx-3); }
</style>
