// Shared pointer/keyboard folding. Native buttons keep focus and activation semantics.
export function installPanelFolding(doc, Observer = globalThis.MutationObserver) {
  const headings = () => [...doc.querySelectorAll('.panel > .panel-hd')]
  function refresh() {
    for (const heading of headings()) {
      if (heading.matches('.no-fold') || heading.parentElement.matches('.no-fold')) continue
      let button = heading.querySelector('.panel-fold-toggle')
      if (!button) {
        button = doc.createElement('button')
        button.type = 'button'; button.className = 'panel-fold-toggle'
        heading.append(button)
      }
      const expanded = !heading.parentElement.classList.contains('folded')
      const title = heading.querySelector('.panel-title')?.textContent?.trim() || '面板'
      const label = `${expanded ? '收起' : '展开'}${title}`
      if (button.getAttribute('aria-expanded') !== String(expanded)) button.setAttribute('aria-expanded', String(expanded))
      if (button.getAttribute('aria-label') !== label) button.setAttribute('aria-label', label)
    }
  }
  function click(event) {
    const target = event.target
    const heading = target?.closest?.('.panel-hd')
    if (!heading || !heading.querySelector('.panel-fold-toggle')) return
    if (!target.closest('.panel-fold-toggle') && target.closest('button,input,select,textarea,a,label,.no-fold')) return
    heading.parentElement.classList.toggle('folded')
    refresh()
  }
  refresh()
  const observer = Observer ? new Observer(refresh) : null
  observer?.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] })
  doc.addEventListener('click', click, true)
  return () => { observer?.disconnect(); doc.removeEventListener('click', click, true) }
}
