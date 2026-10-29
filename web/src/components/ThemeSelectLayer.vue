<script setup>
import { ref, nextTick, onMounted, onBeforeUnmount } from 'vue'

// Mount once at the application root. Keep the native select as the focus and
// value owner so existing v-model, numeric values and change handlers still work.
// Add data-native-select to a select to retain its browser-provided picker.
const menu = ref(null)
const opened = ref(false)
const options = ref([])
const activeIndex = ref(-1)
const selectedIndex = ref(-1)
const menuStyle = ref({})
const menuLabel = ref('选择选项')
const menuId = 'theme-select-listbox'
let source = null
let savedAttributes = new Map()
let observer = null
let animationFrame = 0
let lastRect = ''
let search = ''
let searchAt = 0

function eligible(element) {
  return element instanceof HTMLSelectElement && !element.disabled
    && !element.matches(':disabled') && !element.multiple && element.size <= 1
    && !element.hasAttribute('data-native-select')
}
function selectFrom(event) {
  const element = event.target instanceof Element ? event.target.closest('select') : null
  return eligible(element) ? element : null
}
function readOptions() {
  if (!source) return
  options.value = Array.from(source.options, (option, index) => {
    const group = option.parentElement instanceof HTMLOptGroupElement ? option.parentElement : null
    return {
      index, label: option.label || option.text,
      group: group?.label || '',
      disabled: option.disabled || !!group?.disabled,
      hidden: option.hidden || !!group?.hidden,
    }
  }).filter(option => !option.hidden)
  selectedIndex.value = source.selectedIndex
}
function enabledOptions() { return options.value.filter(option => !option.disabled) }
function scrollActive() {
  nextTick(() => menu.value?.querySelector(`[data-option-index="${activeIndex.value}"]`)
    ?.scrollIntoView({ block: 'nearest' }))
}
function activate(index) {
  activeIndex.value = index
  if (source && index >= 0) source.setAttribute('aria-activedescendant', `${menuId}-${index}`)
  else source?.removeAttribute('aria-activedescendant')
  scrollActive()
}
function move(delta) {
  const enabled = enabledOptions()
  if (!enabled.length) return
  const index = enabled.findIndex(option => option.index === activeIndex.value)
  const next = index < 0 ? (delta > 0 ? 0 : enabled.length - 1)
    : Math.max(0, Math.min(enabled.length - 1, index + delta))
  activate(enabled[next].index)
}
function close(restoreFocus = false) {
  const previous = source
  opened.value = false
  cancelAnimationFrame(animationFrame)
  observer?.disconnect()
  observer = null
  for (const [name, value] of savedAttributes) {
    if (value === null) previous?.removeAttribute(name)
    else previous?.setAttribute(name, value)
  }
  source = null
  savedAttributes.clear()
  search = ''
  lastRect = ''
  if (restoreFocus && previous?.isConnected) previous.focus({ preventScroll: true })
}
function positionMenu() {
  if (!source) return
  const rect = source.getBoundingClientRect()
  const width = window.innerWidth
  const height = window.innerHeight
  const signature = [rect.x, rect.y, rect.width, rect.height, width, height].join(':')
  if (signature === lastRect) return
  lastRect = signature
  if (rect.bottom < 0 || rect.top > height || rect.right < 0 || rect.left > width) { close(); return }
  const gap = 4, edge = 8
  const below = Math.max(0, height - rect.bottom - gap - edge)
  const above = Math.max(0, rect.top - gap - edge)
  const placeAbove = below < 180 && above > below
  const available = Math.min(280, placeAbove ? above : below)
  const menuWidth = Math.min(Math.max(rect.width, 160), width - edge * 2)
  menuStyle.value = {
    left: `${Math.max(edge, Math.min(rect.left, width - menuWidth - edge))}px`,
    width: `${menuWidth}px`, maxHeight: `${available}px`,
    ...(placeAbove ? { bottom: `${height - rect.top + gap}px` } : { top: `${rect.bottom + gap}px` }),
  }
}
function monitorSource() {
  if (!source || !source.isConnected || !eligible(source)) { close(); return }
  selectedIndex.value = source.selectedIndex
  positionMenu()
  if (opened.value) animationFrame = requestAnimationFrame(monitorSource)
}
function open(select, fromEnd = false) {
  close()
  source = select
  for (const attribute of ['aria-expanded', 'aria-controls', 'aria-activedescendant', 'data-theme-select-open']) {
    savedAttributes.set(attribute, select.getAttribute(attribute))
  }
  select.setAttribute('aria-expanded', 'true')
  select.setAttribute('aria-controls', menuId)
  select.setAttribute('data-theme-select-open', '')
  menuLabel.value = select.getAttribute('aria-label')
    || Array.from(select.labels || []).map(label => label.textContent.trim()).join(' ') || '选择选项'
  readOptions()
  const enabled = enabledOptions()
  const current = enabled.find(option => option.index === selectedIndex.value)
  opened.value = true
  select.focus({ preventScroll: true })
  activate(current?.index ?? (fromEnd ? enabled.at(-1)?.index : enabled[0]?.index) ?? -1)
  observer = new MutationObserver(() => {
    readOptions()
    if (!enabledOptions().some(option => option.index === activeIndex.value)) activate(enabledOptions()[0]?.index ?? -1)
  })
  observer.observe(select, { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ['disabled', 'hidden', 'label', 'selected', 'value'] })
  monitorSource()
}
function choose(index) {
  const select = source
  const option = options.value.find(option => option.index === index)
  if (!eligible(select) || !option || option.disabled) return
  const changed = select.selectedIndex !== index
  select.selectedIndex = index
  close(true)
  if (changed) {
    select.dispatchEvent(new Event('input', { bubbles: true }))
    select.dispatchEvent(new Event('change', { bubbles: true }))
  }
}
function typeahead(key) {
  const now = Date.now()
  search = now - searchAt < 700 ? search + key.toLocaleLowerCase() : key.toLocaleLowerCase()
  searchAt = now
  const repeated = Array.from(search).every(char => char === search[0])
  const query = repeated ? search[0] : search
  const enabled = enabledOptions()
  const current = enabled.findIndex(option => option.index === activeIndex.value)
  const start = repeated ? current + 1 : Math.max(0, current)
  for (let step = 0; step < enabled.length; step++) {
    const option = enabled[(start + step) % enabled.length]
    if (option.label.trim().toLocaleLowerCase().startsWith(query)) { activate(option.index); break }
  }
}
function onPointerDown(event) {
  if (event.button !== 0) return
  const select = selectFrom(event)
  if (select) {
    event.preventDefault()
    if (source === select) close(true)
    else open(select)
  } else if (!menu.value?.contains(event.target)) close()
}
function onClick(event) {
  const select = selectFrom(event)
  if (!select) return
  event.preventDefault()
  // Assistive technology can activate the focused native control without a pointer.
  if (event.detail === 0 && source !== select) open(select)
}
function onKeyDown(event) {
  const select = selectFrom(event)
  if (!select || event.isComposing || event.ctrlKey || event.metaKey) return
  const key = event.key
  if (key === 'Tab') { if (source === select) close(); return }
  if (key === 'Escape') {
    if (source === select) { event.preventDefault(); event.stopPropagation(); close(true) }
    return
  }
  const navigation = ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp', 'Enter', ' '].includes(key)
  const printable = key.length === 1 && !event.altKey
  if (!navigation && !printable) return
  event.preventDefault()
  event.stopPropagation()
  if (source !== select) {
    open(select, key === 'ArrowUp' || key === 'End')
    if (key === 'Home') activate(enabledOptions()[0]?.index ?? -1)
    if (key === 'End') activate(enabledOptions().at(-1)?.index ?? -1)
    if (printable && key !== ' ') typeahead(key)
    return
  }
  if (event.altKey && key === 'ArrowUp') { close(true); return }
  if (key === 'ArrowDown') move(1)
  else if (key === 'ArrowUp') move(-1)
  else if (key === 'PageDown') move(8)
  else if (key === 'PageUp') move(-8)
  else if (key === 'Home') activate(enabledOptions()[0]?.index ?? -1)
  else if (key === 'End') activate(enabledOptions().at(-1)?.index ?? -1)
  else if (key === 'Enter' || key === ' ') choose(activeIndex.value)
  else if (printable) typeahead(key)
}
function onFocusIn(event) {
  if (source && event.target !== source && !menu.value?.contains(event.target)) close()
}
onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown, true)
  document.addEventListener('click', onClick, true)
  document.addEventListener('keydown', onKeyDown, true)
  document.addEventListener('focusin', onFocusIn, true)
})
onBeforeUnmount(() => {
  close()
  document.removeEventListener('pointerdown', onPointerDown, true)
  document.removeEventListener('click', onClick, true)
  document.removeEventListener('keydown', onKeyDown, true)
  document.removeEventListener('focusin', onFocusIn, true)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="opened" :id="menuId" ref="menu" class="theme-select-menu no-fold" :style="menuStyle"
         role="listbox" :aria-label="menuLabel" @pointerdown.prevent>
      <template v-for="(option, index) in options" :key="option.index">
        <div v-if="option.group && option.group !== options[index - 1]?.group" class="theme-select-group" role="presentation">{{ option.group }}</div>
        <div :id="`${menuId}-${option.index}`" class="theme-select-option"
             :class="{ active: activeIndex === option.index, selected: selectedIndex === option.index, disabled: option.disabled }"
             :data-option-index="option.index" role="option" :aria-selected="selectedIndex === option.index"
             :aria-disabled="option.disabled || undefined" @pointermove="!option.disabled && activate(option.index)"
             @click.stop="choose(option.index)">
          <span class="theme-select-label">{{ option.label || '\u00a0' }}</span>
          <svg v-if="selectedIndex === option.index" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8 3 3 7-7" fill="none" stroke="currentColor" stroke-width="1.4" /></svg>
        </div>
      </template>
      <div v-if="!options.length" class="theme-select-empty">暂无选项</div>
    </div>
  </Teleport>
</template>
