import { createMouseFollowInput } from './mouseFollowInput.js'
import { gamepadAxis } from './interactionCore.js'
export { gamepadAxis } from './interactionCore.js'

export const INTERACTION_ENDPOINT = '/api/sim/mouse-follow'
export function interactionModeIssue(mode, input) {
  return mode === 'real' && input !== 'camera' ? '键盘、手柄和鼠标仅供仿真使用' : ''
}
export function editableTarget(target) {
  return !!(target?.isContentEditable || target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])'))
}
const keys = { KeyA:[-1,0,0], ArrowLeft:[-1,0,0], KeyD:[1,0,0], ArrowRight:[1,0,0],
  KeyW:[0,-1,0], ArrowUp:[0,-1,0], KeyS:[0,1,0], ArrowDown:[0,1,0], KeyQ:[0,0,-1], KeyE:[0,0,1] }

// Owns only local synthetic input. Never publishes to the camera bus or starts motion.
export function createInteractionInput({ session, onChange = () => {}, enabled = () => true,
  win = globalThis.window, doc = globalThis.document, pads = () => globalThis.navigator?.getGamepads?.() || [],
  raf = fn => requestAnimationFrame(fn), cancel = id => cancelAnimationFrame(id) } = {}) {
  const point = createMouseFollowInput(session)
  const held = new Set()
  let mode = null, running = false, handle = null, previous = null, present = false, identity = null, neutralRequired = true
  function notify() { onChange({ ...point.position(), present, gamepad:identity ? {...identity} : null }) }
  function reset() { held.clear(); point.center(); present = false; previous = null; neutralRequired = true; notify() }
  function down(event) {
    if (mode !== 'keyboard' || !enabled() || doc?.hidden || doc?.hasFocus?.() === false || editableTarget(event.target) || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return
    if (event.code !== 'Space' && !keys[event.code]) return
    event.preventDefault()
    if (event.code === 'Space') { reset(); return }
    if (!event.repeat) held.add(event.code)
  }
  function up(event) { if (held.delete(event.code) && !held.size) reset() }
  function focusIn(event) { if (editableTarget(event.target)) reset() }
  function step(now) {
    if (!running || !Number.isFinite(now)) return
    const dt = previous == null ? 0 : (now - previous) / 1000
    previous = now
    let velocity = [0,0,0]
    let pad = null
    if (mode === 'gamepad') {
      const connected = Array.from(pads()).filter(p => p?.connected)
      pad = identity && connected.find(p => p.index === identity.index && p.id === identity.id)
      if (identity && !pad) { identity = null; reset(); return }
      if (!pad && connected[0]) {
        pad = connected[0]; identity = {index:pad.index,id:pad.id,mapping:pad.mapping}; neutralRequired = true
      }
    }
    if (!enabled() || doc?.hidden || doc?.hasFocus?.() === false || editableTarget(doc?.activeElement)) { reset(); return }
    if (mode === 'keyboard') {
      for (const code of held) velocity = velocity.map((v,i) => v + keys[code][i])
      velocity = velocity.map(v => Math.max(-1,Math.min(1,v)))
    } else if (mode === 'gamepad') {
      if (pad) {
        velocity = [gamepadAxis(pad.axes?.[0]),gamepadAxis(pad.axes?.[1]),-gamepadAxis(pad.axes?.[3])]
        if (neutralRequired) {
          if (velocity.every(v => v === 0)) neutralRequired = false
          velocity = [0,0,0]
        }
      }
    }
    present = velocity.some(v => v !== 0)
    if (present) point.advance(velocity, dt)
    else point.center()
    notify()
  }
  function tick(now) { if (!running) return; step(now); if (running) handle = raf(tick) }
  function hidden() { if (doc?.hidden) reset() }
  function disconnected(event) { if (identity?.index === event.gamepad?.index) { identity = null; reset() } }
  function stop() {
    running = false
    if (handle != null) cancel(handle)
    handle = null
    win?.removeEventListener('keydown',down); win?.removeEventListener('keyup',up)
    win?.removeEventListener('blur',reset); win?.removeEventListener('gamepaddisconnected',disconnected)
    doc?.removeEventListener('visibilitychange',hidden)
    doc?.removeEventListener('focusin',focusIn)
    identity = null; reset()
  }
  return {
    select(input, robotMode = 'sim') {
      stop(); mode = null
      if (interactionModeIssue(robotMode,input) || !['keyboard','gamepad'].includes(input)) return false
      mode = input; running = true
      win?.addEventListener('keydown',down); win?.addEventListener('keyup',up)
      win?.addEventListener('blur',reset); win?.addEventListener('gamepaddisconnected',disconnected)
      doc?.addEventListener('visibilitychange',hidden)
      doc?.addEventListener('focusin',focusIn)
      handle = raf(tick); return true
    },
    step, reset, dispose:stop,
    frame(now, configuration = false) { return configuration || present ? point.frame(now) : null },
  }
}
