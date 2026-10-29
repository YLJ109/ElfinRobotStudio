// Local input coordinates only: u grows right, v grows down, depth grows forward.
// Camera display mirroring, world direction, motion and IK belong to their owners.
export function unitInput(value, fallback = .5) {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : fallback
}

export function gamepadAxis(value, deadzone = .18) {
  if (!Number.isFinite(deadzone) || deadzone < 0 || deadzone >= 1 ||
      !Number.isFinite(value) || Math.abs(value) <= deadzone) return 0
  return Math.sign(value) * unitInput((Math.abs(value) - deadzone) / (1 - deadzone), 0)
}

export function createInteractionTarget(saved) {
  const restored = saved && [saved.x, saved.y, saved.depth].every(v => Number.isFinite(v) && v >= 0 && v <= 1)
  let u = restored ? saved.x : .5, v = restored ? saved.y : .5, depth = restored ? saved.depth : .5
  const read = () => ({u, v, depth})
  function nudge(du, dv, dd = 0) {
    if (![du, dv, dd].every(Number.isFinite)) return false
    u = unitInput(u + du); v = unitInput(v + dv); depth = unitInput(depth + dd)
    return true
  }
  return {
    read, nudge,
    move(clientX, clientY, rect) {
      if (![clientX, clientY, rect?.left, rect?.top, rect?.width, rect?.height].every(Number.isFinite) ||
          rect.width <= 0 || rect.height <= 0) return false
      u = unitInput((clientX - rect.left) / rect.width)
      v = unitInput((clientY - rect.top) / rect.height)
      return true
    },
    depth(value) { if (Number.isFinite(value)) depth = unitInput(value) },
    center() { u = v = depth = .5 },
    advance(velocity, dt) {
      if (!Array.isArray(velocity) || velocity.length !== 3 || !velocity.every(Number.isFinite) || !Number.isFinite(dt)) return false
      const elapsed = Math.max(0, Math.min(.05, dt)) * .45
      return nudge(...velocity.map(value => Math.max(-1, Math.min(1, value)) * elapsed))
    },
  }
}
