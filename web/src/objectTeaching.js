// 场景物体移动与机器人示教完全分离：这里只保存场景坐标。
export const validObjectPosition = xyz => Array.isArray(xyz) && xyz.length === 3
  && xyz.every(v => Number.isFinite(v) && Math.abs(v) <= 1e6)

export function objectTeachingContext(w) {
  const s = w.snap
  return w.connected !== false && (w.mode === undefined || w.mode === 'sim') && s?.mode === 'sim' && !s.real
    && typeof s.session_id === 'string' ? s.session_id : ''
}

export function createObjectTeaching({ state, save, allowed }) {
  let binding = null, revision = 0
  function clear() {
    Object.assign(state, { active: false, dragging: false, busy: false, xyz: null, error: '' })
  }
  clear()
  function detach() {
    const old = binding
    binding = null; revision++
    old?.scene.setObjectTarget(null)
    clear()
  }
  function attach(scene, id, context) {
    detach()
    if (!scene || !id || !context) return false
    const token = ++revision
    const live = () => token === revision && !!binding && allowed(id, context, scene)
    binding = { scene, id, context }
    const resetPreview = () => Object.assign(state, { dragging: false, busy: false, xyz: null })
    const callbacks = {
      start(xyz) {
        if (!live() || state.busy) return
        state.error = ''; state.dragging = true
        state.xyz = validObjectPosition(xyz) ? [...xyz] : null
      },
      change(xyz) {
        if (!live() || state.busy) return
        if (validObjectPosition(xyz)) state.xyz = [...xyz]
      },
      cancel() { if (token === revision) resetPreview() },
      async commit(xyz) {
        if (!live() || state.busy) return
        state.dragging = false
        if (!validObjectPosition(xyz)) {
          scene.cancelObjectMove(); state.error = '坐标超出范围'; return
        }
        state.busy = true; state.error = ''; state.xyz = [...xyz]
        try {
          const result = await save(id, { xyz: [...xyz] })
          if (!live()) return
          if (result?.ok !== true || result.object?.id !== id || !validObjectPosition(result.object?.xyz)) {
            throw new Error(result?.why || '物体位置保存失败')
          }
          scene.finishObjectMove([...result.object.xyz])
          resetPreview()
        } catch (error) {
          if (!live()) return
          scene.cancelObjectMove(); resetPreview()
          state.error = error?.message || '物体位置保存失败'
        }
      },
    }
    if (scene.setObjectTarget(id, callbacks) === false) { detach(); return false }
    state.active = true
    return true
  }
  return { attach, detach, cancel() {
    if (!state.busy) binding?.scene.cancelObjectMove()
  } }
}
