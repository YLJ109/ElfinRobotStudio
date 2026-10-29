// 页面Tab手动激活：方向键只移动焦点，Enter/空格才切页；不调用业务接口。
export function installTabNavigation(doc) {
  function keydown(event) {
    if(event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.isComposing)return
    const tab=event.target?.closest?.('[role="tab"]')
    const list=tab?.closest?.('[role="tablist"]')
    if(!tab || !list || tab.disabled)return
    const tabs=Array.from(list.querySelectorAll('[role="tab"]')).filter(item=>!item.disabled && item.closest('[role="tablist"]')===list)
    const index=tabs.indexOf(tab)
    if(index<0)return
    const vertical=list.getAttribute('aria-orientation')==='vertical'
    let next=null
    if(event.key===(vertical?'ArrowDown':'ArrowRight'))next=(index+1)%tabs.length
    if(event.key===(vertical?'ArrowUp':'ArrowLeft'))next=(index+tabs.length-1)%tabs.length
    if(event.key==='Home')next=0
    if(event.key==='End')next=tabs.length-1
    if(next!==null) {
      event.preventDefault();event.stopPropagation()
      tabs.forEach((item,i)=>{item.tabIndex=i===next?0:-1})
      tabs[next].focus();tabs[next].scrollIntoView?.({block:'nearest',inline:'nearest'})
    } else if(event.key===' ' || event.key==='Enter') {
      event.preventDefault();event.stopPropagation();tab.click()
    }
  }
  doc.addEventListener('keydown',keydown)
  return ()=>doc.removeEventListener('keydown',keydown)
}
