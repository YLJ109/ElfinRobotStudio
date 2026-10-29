import { ref, watch, nextTick, onMounted, onActivated, onDeactivated, onBeforeUnmount } from 'vue'

// No log data is owned or changed here. Input events, rather than scroll events,
// distinguish a reader from our own scrollTop writes (including delayed events).
export function createLogFollow({ getElement, afterRender = fn => Promise.resolve().then(fn),
  onChange = () => {}, onScroll = () => {}, now = () => Date.now(),
  schedule = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id),
  idleMs = 10000, bottomGap = 4, initialFollowing = true } = {}) {
  let active = false, following = initialFollowing, disposed = false, timer = null, pending = null
  let lastInput = initialFollowing ? null : now()
  const pointers = new Set(), keys = new Set()
  let touching = false
  const held = () => pointers.size > 0 || keys.size > 0 || touching
  function clearTimer() { if (timer !== null) cancel(timer); timer = null }
  function invalidate() { pending = null; clearTimer() }
  function atBottom() {
    const el = getElement()
    return !!el && el.clientHeight > 0 &&
      el.scrollHeight - el.clientHeight - el.scrollTop <= bottomGap
  }
  function setFollowing(value) {
    if (following !== value) { following = value; onChange(value) }
  }
  function requestBottom() {
    if (!active || disposed || !following || held() || pending) return
    const ticket = pending = {}
    afterRender(() => {
      if (pending !== ticket) return
      pending = null
      if (!active || disposed || !following || held()) return
      const el = getElement()
      if (!el || !(el.clientHeight > 0)) return
      el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight)
      onScroll(el) // Virtual lists must update even before the native scroll event.
    })
  }
  function contentChanged() {
    if (following) { requestBottom(); return }
    if (!active || disposed || held() || pending) return
    const el = getElement()
    if (!el?.querySelectorAll || !el.getBoundingClientRect) return
    const viewport = el.getBoundingClientRect()
    const anchor = Array.from(el.querySelectorAll('[data-log-seq]')).find(row => {
      const rect = row.getBoundingClientRect()
      return rect.bottom > viewport.top && rect.top < viewport.bottom
    })
    if (!anchor) return
    const seq = anchor.getAttribute('data-log-seq')
    const offset = anchor.getBoundingClientRect().top - viewport.top
    const ticket = pending = {}
    afterRender(() => {
      if (pending !== ticket) return
      pending = null
      if (!active || disposed || following || held() || el !== getElement()) return
      const row = Array.from(el.querySelectorAll('[data-log-seq]'))
        .find(candidate => candidate.getAttribute('data-log-seq') === seq)
      if (!row) return // The reader's record itself was evicted or filtered out.
      const delta = row.getBoundingClientRect().top - el.getBoundingClientRect().top - offset
      if (Math.abs(delta) > 0.5) {
        el.scrollTop += delta
        onScroll(el)
      }
    })
  }
  function resume() {
    if (disposed) return
    invalidate()
    setFollowing(true)
    requestBottom()
  }
  function arm() {
    clearTimer()
    if (!active || disposed || following || held() || lastInput === null) return
    const remaining = Math.max(0, idleMs - (now() - lastInput))
    const ticket = schedule(() => {
      if (timer !== ticket) return
      timer = null
      if (active && !held()) resume()
    }, remaining)
    timer = ticket
  }
  function interact() {
    if (!active || disposed) return
    // Cancel a nextTick scroll immediately, before the event's default scroll.
    invalidate(); lastInput = now(); setFollowing(false); arm()
  }
  function release() {
    if (disposed) return
    lastInput = now()
    if (!active) return
    if (held()) return
    if (following) requestBottom()
    else if (atBottom()) resume()
    else arm()
  }
  function scroll() {
    const el = getElement()
    if (el) onScroll(el)
    // A scroll event alone never pauses following: programmatic scrolls and
    // content growth may deliver one long after the initiating DOM update.
    if (active && !following && !held() && atBottom()) resume()
  }
  function setActive(value) {
    if (disposed) return
    const next = !!value
    if (active === next) return
    active = next; invalidate()
    // Keep a still-pressed input until its global release/cancel/blur arrives.
    // A hidden/visible transition must not steal a held scrollbar or key.
    if (!active || held()) return
    if (following) requestBottom()
    else if (lastInput !== null && now() - lastInput >= idleMs) resume()
    else arm()
  }
  const handlers = {
    wheel: interact,
    pointerdown(event) {
      if (event.button !== undefined && event.button !== 0) return
      pointers.add(event.pointerId ?? 0); interact()
    },
    pointermove(event) { if (pointers.has(event.pointerId ?? 0)) interact() },
    pointerup(event) { if (pointers.delete(event.pointerId ?? 0)) release() },
    pointercancel(event) { if (pointers.delete(event.pointerId ?? 0)) release() },
    touchstart() { touching = true; interact() },
    touchmove() { if (touching) interact() },
    touchend(event) { if (touching) { touching = !!event.touches?.length; release() } },
    touchcancel() { if (touching) { touching = false; release() } },
    keydown(event) { keys.add(event.code || event.key); interact() },
    keyup(event) { if (keys.delete(event.code || event.key)) release() },
    blur() {
      if (!held()) return
      pointers.clear(); keys.clear(); touching = false; release()
    },
    scroll,
    toggle: requestBottom,
  }
  return {
    handlers, atBottom, resume, setActive, contentChanged,
    get following() { return following },
    dispose() { disposed = true; active = false; invalidate(); pointers.clear(); keys.clear(); touching = false },
  }
}

// Each surface gets independent reader state. KeepAlive, v-show and document
// visibility all gate timers and queued scrolls; no global store is involved.
export function useLogFollow({ box, content, active, onScroll, interactionRoot = box, resizeTarget = box, storageKey } = {}) {
  let saved=null
  try { if(storageKey)saved=JSON.parse(globalThis.sessionStorage?.getItem(storageKey)||'null') } catch {}
  const following = ref(saved?.following !== false)
  let restorePending=saved?.following===false && Number.isFinite(saved.top)
  const persist=()=>{
    try {if(storageKey)globalThis.sessionStorage?.setItem(storageKey,JSON.stringify({following:following.value,top:box.value?.scrollTop || 0}))}catch{}
  }
  function restoreReader() {
    if(!restorePending || !box.value || !content.value?.length)return
    box.value.scrollTop=Math.max(0,saved.top);restorePending=false
  }
  const controller = createLogFollow({ getElement: () => box.value, afterRender: nextTick,
    initialFollowing:following.value,
    onChange: value => { following.value = value;persist() }, onScroll:el=>{onScroll?.(el);if(!restorePending)persist()} })
  let mounted = false, suspended = false, disposed = false, bound = null, inputBound = null, resizeBound = null, observer = null
  const doc = typeof document === 'undefined' ? null : document
  const win = typeof window === 'undefined' ? null : window
  const bindings = [
    ['wheel', 'wheel'], ['pointerdown', 'pointerdown'], ['pointermove', 'pointermove'],
    ['touchstart', 'touchstart'], ['touchmove', 'touchmove'], ['keydown', 'keydown'],
  ]
  const viewportBindings = [['scroll', 'scroll'], ['toggle', 'toggle']]
  const globalBindings = [
    ['pointerup', 'pointerup'], ['pointercancel', 'pointercancel'],
    ['touchend', 'touchend'], ['touchcancel', 'touchcancel'], ['keyup', 'keyup'], ['blur', 'blur'],
  ]
  function unbind() {
    for (const [event, handler] of bindings) inputBound?.removeEventListener?.(event, controller.handlers[handler], true)
    for (const [event, handler] of viewportBindings) bound?.removeEventListener?.(event, controller.handlers[handler], true)
    observer?.disconnect(); observer = null; bound = null; inputBound = null; resizeBound = null
  }
  function sync() {
    const enabled = mounted && !suspended && !disposed && active.value && !doc?.hidden
    const input = interactionRoot.value || box.value
    if (bound !== box.value || inputBound !== input || resizeBound !== resizeTarget.value || !enabled) unbind()
    if (enabled && box.value && !bound) {
      bound = box.value
      inputBound = input
      resizeBound = resizeTarget.value
      for (const [event, handler] of bindings) inputBound?.addEventListener?.(event, controller.handlers[handler], { capture: true, passive: true })
      for (const [event, handler] of viewportBindings) bound.addEventListener?.(event, controller.handlers[handler], { capture: true, passive: true })
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(sync)
        observer.observe(bound)
        // 正确：字体加载、文字换行和展开原文会改变内容高度，视口本身可能完全没变。
        if (resizeBound && resizeBound !== bound) observer.observe(resizeBound)
      }
    }
    if(enabled)restoreReader()
    controller.setActive(enabled && !!box.value && box.value.clientHeight > 0)
    // A v-if box can be replaced while enabled (e.g. collapsed dock reopened).
    if (enabled) controller.contentChanged()
  }
  onMounted(() => {
    mounted = true
    doc?.addEventListener('visibilitychange', sync)
    for (const [event, handler] of globalBindings) win?.addEventListener(event, controller.handlers[handler], true)
    sync()
  })
  watch([box, active, interactionRoot, resizeTarget], sync, { flush: 'post' })
  // Capture the visible record before the DOM patch. This also keeps a reader
  // anchored when the bounded log buffer drops rows from its oldest end.
  watch(content, controller.contentChanged, { flush: 'pre' })
  watch(content,()=>{if(restorePending)void nextTick(restoreReader)}, {flush:'post'})
  onActivated(() => { suspended = false; sync() })
  onDeactivated(() => { suspended = true; sync() })
  onBeforeUnmount(() => {
    persist();disposed = true; controller.dispose(); unbind()
    doc?.removeEventListener('visibilitychange', sync)
    for (const [event, handler] of globalBindings) win?.removeEventListener(event, controller.handlers[handler], true)
  })
  return { following, goLatest: controller.resume }
}
