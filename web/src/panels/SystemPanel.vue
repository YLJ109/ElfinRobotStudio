<script setup>
import { defineAsyncComponent, nextTick, onActivated, onDeactivated, reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { systemTab } = storeToRefs(w)
const visible = ref(true)
onActivated(() => { visible.value = true })
onDeactivated(() => { visible.value = false })
const TABS = [
  { id: 'engineering', label: '工具', panel: defineAsyncComponent(() => import('./EngineeringPanel.vue')) },
  { id: 'fence', label: '围栏', panel: defineAsyncComponent(() => import('./FencePanel.vue')) },
  { id: 'logs', label: '日志', panel: defineAsyncComponent(() => import('./LogsPanel.vue')) },
  { id: 'tele', label: '遥测', panel: defineAsyncComponent(() => import('./TelemetryPanel.vue')) },
  { id: 'settings', label: '设置', panel: defineAsyncComponent(() => import('./SettingsPanel.vue')) },
  { id: 'about', label: '关于', panel: defineAsyncComponent(() => import('./AboutPanel.vue')) },
]
// Mount on first visit, then retain each panel's DOM, unsaved inputs and scroll.
// Existing panels remain untouched; navigation never invokes a device action.
const visited = reactive(new Set())
watch(systemTab, (id) => { if (TABS.some((tab) => tab.id === id)) visited.add(id) }, { immediate: true })

function focusTab(event, index) {
  if (event.altKey || event.ctrlKey || event.metaKey) return
  if (event.key === ' ' || event.key === 'Enter') {
    event.preventDefault(); event.stopPropagation?.(); selectTab(TABS[index].id, event); return
  }
  let next
  if (event.key === 'ArrowRight') next = (index + 1) % TABS.length
  else if (event.key === 'ArrowLeft') next = (index + TABS.length - 1) % TABS.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = TABS.length - 1
  else return
  event.preventDefault()
  // Manual activation avoids loading a page just because its tab receives focus.
  const buttons = event.currentTarget.parentElement.querySelectorAll('[role="tab"]')
  buttons[next]?.focus()
}

async function selectTab(id, event) {
  const button = event?.currentTarget
  systemTab.value = id
  await nextTick()
  button?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
}
</script>

<template>
  <div class="system-workspace">
    <nav class="system-tabs panel corner no-fold" role="tablist" aria-label="系统工作页">
      <button v-for="(tab, index) in TABS" :id="'system-tab-' + tab.id" :key="tab.id"
              type="button" role="tab" class="btn system-tab" :class="{ selected: systemTab === tab.id }"
              :aria-selected="systemTab === tab.id" :aria-controls="'system-panel-' + tab.id"
              :tabindex="systemTab === tab.id ? 0 : -1"
              @click="selectTab(tab.id, $event)" @keydown="focusTab($event, index)">{{ tab.label }}</button>
    </nav>
    <section v-for="tab in TABS" v-show="systemTab === tab.id" :id="'system-panel-' + tab.id" :key="tab.id"
             class="system-content" :class="{ 'logs-tab': tab.id === 'logs' }" role="tabpanel"
             :aria-labelledby="'system-tab-' + tab.id" :tabindex="systemTab === tab.id ? 0 : -1">
      <component :is="tab.panel" v-if="visited.has(tab.id)"
                 v-bind="['tele', 'logs'].includes(tab.id) ? { active: visible && systemTab === tab.id } : {}" />
    </section>
  </div>
</template>

<style scoped>
.system-workspace { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.system-tab:focus-visible, .system-content:focus-visible { outline: 2px solid var(--acc); outline-offset: -2px; }
.system-content { display: flex; flex: 1; flex-direction: column; min-width: 0; min-height: 0; overflow: auto; }
.system-content > :deep(.col) { flex: 0 0 auto; min-height: 100%; }
.system-content.logs-tab { overflow: hidden; }
.logs-tab > :deep(.logs) { flex: 1; min-height: 0; }
</style>
