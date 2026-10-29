<script setup>
import { computed, ref, watch } from 'vue'
import { readTaskTab, saveTaskTab, tabFocusIndex } from '../workspaceInsights.js'

const props = defineProps({ modelValue: String, tabs: { type: Array, required: true },
  id: { type: String, required: true }, label: { type: String, required: true }, storageKey: String })
const emit = defineEmits(['update:modelValue'])
const buttons = ref([])
const selected = computed(() => props.tabs.some(tab => tab.id === props.modelValue) ? props.modelValue : props.tabs[0]?.id)
// Restore UI selection only. No tab activation starts an action or loads a device.
if (props.storageKey) {
  const saved = readTaskTab(props.storageKey, props.tabs, props.modelValue)
  if (saved !== props.modelValue) emit('update:modelValue', saved)
}
watch(selected, value => { if (props.storageKey) saveTaskTab(props.storageKey, value) })
function select(id) { if (!props.tabs.find(tab=>tab.id===id)?.disabled) emit('update:modelValue', id) }
function keydown(event, index) {
  if (event.altKey || event.ctrlKey || event.metaKey) return
  // Space is reserved globally for cancelling a preview. Consume it here so
  // native tab activation cannot accidentally cancel a robot preview.
  if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); select(props.tabs[index].id); return }
  const next = tabFocusIndex(event.key, index, props.tabs.length)
  if (next === null) return
  event.preventDefault(); event.stopPropagation(); buttons.value[next]?.focus()
}
</script>

<template>
  <div class="task-tabs no-fold" role="tablist" :aria-label="label">
    <button v-for="(tab, index) in tabs" :id="`${id}-tab-${tab.id}`" :key="tab.id"
            :ref="el => buttons[index] = el" class="btn" type="button" role="tab"
            :disabled="!!tab.disabled"
            :aria-selected="selected === tab.id" :aria-controls="`${id}-panel`"
            :tabindex="selected === tab.id ? 0 : -1" :class="{ 'btn--on': selected === tab.id }"
            @click="select(tab.id)" @keydown="keydown($event, index)">{{ tab.label }}</button>
  </div>
</template>

