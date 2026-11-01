import { toDisplayString } from 'vue'

// Presentation only: never use this projection for feedback, IK or permissions.
export function displayNumber(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  const rounded = Number(value.toFixed(2))
  return String(Object.is(rounded, -0) ? 0 : rounded)
}

export function displayNumericText(value) {
  return String(value ?? '').replace(
    /(?<![\w.])[-+]?(?:\d+\.\d+(?:e[-+]?\d+)?|\d+e[-+]?\d+)(?![\d.])/gi,
    token => { const number = Number(token); return Number.isFinite(number) ? displayNumber(number) : token },
  )
}

export function displayNumericJson(value) {
  return JSON.stringify(value, (_key, item) => typeof item === 'number' && Number.isFinite(item)
    ? Number(item.toFixed(2)) : item, 2)
}

export function displayValue(value) {
  if (value == null) return value
  if (typeof value === 'number') return displayNumber(value)
  // Vue's serializer preserves its existing array, Map, Set, ref and BigInt
  // text semantics. Format only the resulting text, never the original data.
  return displayNumericText(toDisplayString(value))
}

// Render the browser value only. No input/change event is synthesized and no
// model assignment is made, so feedback and submitted data retain precision.
const numberInputListeners = new WeakMap()
function renderNumberInput(el, force = false) {
  if (el.type !== 'number' || el.composing || el.value === '') return
  if (!force && (el.getRootNode?.()?.activeElement === el || el.ownerDocument?.activeElement === el)) return
  const number = Number(el.value)
  if (!Number.isFinite(number)) return
  const display = displayNumber(number)
  if (el.value !== display) el.value = display
}

export const numberPrecision = {
  mounted(el) {
    const blur = () => renderNumberInput(el, true)
    numberInputListeners.set(el, blur)
    el.addEventListener('blur', blur)
    renderNumberInput(el)
  },
  updated(el) { renderNumberInput(el) },
  beforeUnmount(el) {
    const blur = numberInputListeners.get(el)
    if (blur) el.removeEventListener('blur', blur)
    numberInputListeners.delete(el)
  },
}
