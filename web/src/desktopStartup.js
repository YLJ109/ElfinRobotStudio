// 桌面真实就绪屏障：资源、模型和首帧均完成后才进入工作区。
export function createDesktopStartup({ bridge, world, watch, target = window,
  fonts = document.fonts, afterPaint = fn => requestAnimationFrame(fn) }) {
  if (!bridge) return () => {}
  const complete = new Set()
  let disposed = false, failed = false, reported = false
  const report = (phase, message, error = false) => bridge.reportBoot({ phase, message, error })
  async function check() {
    if (disposed || failed || reported || complete.size !== 3) return
    reported = true
    await fonts?.ready
    afterPaint(() => {
      if (!disposed && !failed) bridge.reportBoot({ phase: 'ready', ready: true, message: '工作区已就绪' })
    })
  }
  function finish(phase, detail, message) {
    if (disposed || failed) return
    if (detail?.ok !== true) { failed = true; report(phase, detail?.why || '初始化失败', true); return }
    complete.add(phase); report(phase, message); void check()
  }
  const config = event => finish('frontend', event.detail, '界面资源已加载')
  const models = event => finish('models', event.detail, '三维模型已加载')
  target.addEventListener('elfin-boot-config', config)
  target.addEventListener('elfin-boot-models', models)
  const stop = watch(() => world.snap, value => {
    if (world.connected && value?.session_id && Array.isArray(value.joints?.q)
        && value.joints.q.length === 6 && value.joints.q.every(Number.isFinite) && value.safety) {
      if (!complete.has('state')) finish('state', { ok: true }, '首帧状态已收到')
    }
  }, { immediate: true })
  report('frontend', '正在初始化工作区与三维资源')
  return () => { disposed = true; stop(); target.removeEventListener('elfin-boot-config', config); target.removeEventListener('elfin-boot-models', models) }
}
