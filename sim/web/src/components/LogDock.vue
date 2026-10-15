<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { logs } = storeToRefs(w)
const open = ref(true)
const box = ref(null)

const CK = { alarm: 'bad', interlock: 'warn', ai: 'info', program: 'acc', point: 'acc',
             cmd: 'acc', speed: 'dim', jog: 'dim', ready: 'dim', perm: 'warn', link: 'dim', sys: 'dim' }
const rows = computed(() => logs.value.slice(-120))
const bad = computed(() => rows.value.filter((r) => r.kind === 'alarm' || r.kind === 'interlock').length)
const hm = (t) => new Date(t * 1000).toTimeString().slice(0, 8)
watch(() => logs.value.length, async () => {
  if (!open.value) return
  await nextTick()
  if (box.value) box.value.scrollTop = box.value.scrollHeight
})
</script>

<template>
  <section class="dock panel" :class="{ folded: !open }">
    <div class="panel-hd" @click="open = !open">
      <span class="panel-title">执行日志</span>
      <span class="panel-sub">CONSOLE</span>
      <span class="grow"></span>
      <span v-if="bad" class="tag tag--bad">{{ bad }}</span>
      <span class="dim tiny mono">{{ rows.length }}</span>
      <button class="btn btn--xs no-fold" :title="open ? '收起' : '展开'" @click.stop="open = !open">
        {{ open ? '▾' : '▴' }}
      </button>
    </div>
    <div ref="box" class="body mono">
      <div v-for="r in rows" :key="r.seq" class="ln">
        <span class="t">{{ hm(r.t) }}</span>
        <span class="k" :class="CK[r.kind] || 'dim'">{{ r.kind }}</span>
        <span :class="r.kind === 'alarm' ? 'bad' : ''">{{ r.text }}</span>
      </div>
      <div v-if="!rows.length" class="ln dim">暂无记录</div>
    </div>
  </section>
</template>

<style scoped>
.dock { display: flex; flex-direction: column; overflow: hidden; backdrop-filter: blur(6px); }
.body { height: 210px; overflow: auto; padding: 4px 6px; font-size: 11px; line-height: 1.55;
        background: rgba(2, 5, 9, .72); border-top: 1px solid var(--line); }
.folded .body { display: none; }
.ln { display: flex; gap: 6px; white-space: pre-wrap; word-break: break-word; color: var(--tx-2); }
.t { flex: 0 0 56px; color: var(--tx-3); }
.k { flex: 0 0 62px; }
.k.acc { color: var(--acc); }
.k.warn { color: var(--warn); }
.k.bad, .ln .bad { color: #ff8fa3; }
.k.info { color: #7ad2ff; }
.k.dim { color: var(--tx-3); }
</style>
