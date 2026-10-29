const bridge = window.elfinDesktop
document.getElementById('close').addEventListener('click', () => bridge.close())
document.getElementById('retry').addEventListener('click', () => {
  document.body.classList.remove('error'); document.getElementById('actions').hidden = true
  bridge.retry()
})
bridge.onBootProgress(({ phase, message, error }) => {
  document.getElementById('status').textContent = message
  document.body.classList.toggle('error', error)
  document.getElementById('actions').hidden = !error
  const steps = [...document.querySelectorAll('[data-phase]')]
  const index = steps.findIndex(el => el.dataset.phase === phase)
  if (index >= 0) {
    const el = steps[index]
    if (/完成|就绪|已加载|已收到/.test(message)) { el.classList.add('done'); el.classList.remove('active') }
    else el.classList.add('active')
  }
})
