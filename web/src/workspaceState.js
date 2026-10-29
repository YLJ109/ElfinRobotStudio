// Tab-local UI state prevents a second open viewer from overwriting this view.
// These preferences contain no device commands or control tokens.
export const WORKSPACE_VIEW_KEY='huayan.view.workspace.v1'
export function readPreference(key, fallback=null) {
  for(const store of ['sessionStorage','localStorage']) {
    try { const value=globalThis[store]?.getItem(key);if(value!==null && value!==undefined)return value } catch {}
  }
  return fallback
}
export function savePreference(key,value) {
  for(const store of ['sessionStorage','localStorage']) {
    try { globalThis[store]?.setItem(key,String(value)) } catch {}
  }
}
export function readTabState(key) {
  try { return JSON.parse(globalThis.sessionStorage?.getItem(key) || 'null') } catch { return null }
}
export function saveTabState(key,value) {
  try {
    const storage=globalThis.sessionStorage
    if (!storage) return false
    storage.setItem(key,JSON.stringify(value));return true
  } catch { return false }
}

function tabStorage() {
  // 部分浏览器的属性访问本身就会抛异常，不能放在未保护的默认参数中。
  try { return globalThis.sessionStorage } catch { return null }
}

export function createWorkspaceUiState(root, route, {storage=tabStorage(),
  Observer=globalThis.MutationObserver, raf=globalThis.requestAnimationFrame,
  cancel=globalThis.cancelAnimationFrame}={}) {
  const key='huayan.workspace.ui.v1', saved=(()=>{try{return JSON.parse(storage?.getItem(key)||'{}')}catch{return {}}})()
  const pages=saved?.version===1 && saved.pages && typeof saved.pages==='object'?saved.pages:{}
  let frame=null, restoring=false, disposed=false
  const entries=()=>{
    const counts=new Map()
    return [...root.querySelectorAll('.panel,details,[data-persist-scroll],.body,.system-content,.side,.wbody')].map(el=>{
      const heading=el.matches('details')?el.querySelector('summary'):el.querySelector('.panel-title,.panel-hd,h3')
      const logRow=el.closest?.('[data-log-seq]')?.getAttribute('data-log-seq')
      const name=(logRow?'log-'+logRow:el.id || el.getAttribute('aria-label') || heading?.textContent || el.className || el.tagName).trim().slice(0,120)
      const n=counts.get(name)||0;counts.set(name,n+1)
      return [name+':'+n,el]
    })
  }
  function capture() {
    if(disposed || restoring)return
    const rows={}
    for(const [name,el] of entries())rows[name]={folded:el.classList.contains('folded'),
      ...(el.matches('details')?{open:el.open}:{}),top:el.scrollTop,left:el.scrollLeft}
    pages[route()]=rows
    // Bound route history, never persist form values or the camera image.
    const recent=Object.fromEntries(Object.entries(pages).slice(-20))
    try { storage?.setItem(key,JSON.stringify({version:1,pages:recent})) } catch {}
  }
  function restore() {
    const rows=pages[route()];if(!rows)return
    restoring=true
    for(const [name,el] of entries()) {
      const row=rows[name];if(!row)continue
      if(typeof row.folded==='boolean')el.classList.toggle('folded',row.folded)
      if(el.matches('details') && typeof row.open==='boolean')el.open=row.open
      if(Number.isFinite(row.top))el.scrollTop=Math.max(0,row.top)
      if(Number.isFinite(row.left))el.scrollLeft=Math.max(0,row.left)
    }
    restoring=false
  }
  function scheduleCapture() {
    if(restoring || frame!==null || disposed)return
    frame=raf(()=>{frame=null;capture()})
  }
  // Restore asynchronously mounted panels, but do not force scrolling on each
  // log append. Only genuinely new panels trigger restoration.
  const observer=Observer?new Observer(records=>{
    if(records.some(record=>[...record.addedNodes].some(node=>node.nodeType===1 &&
      (node.matches?.('.panel,.system-content') || node.querySelector?.('.panel,.system-content')))))restore()
  }):null
  observer?.observe(root,{childList:true,subtree:true})
  for(const event of ['click','toggle','scroll'])root.addEventListener(event,scheduleCapture,true)
  restore()
  return {capture,restore,dispose(){capture();disposed=true;observer?.disconnect();if(frame!==null)cancel(frame)
    for(const event of ['click','toggle','scroll'])root.removeEventListener(event,scheduleCapture,true)}}
}
