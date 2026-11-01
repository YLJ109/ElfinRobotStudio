import test from 'node:test'
import assert from 'node:assert/strict'
import {installPanelFolding} from '../src/panelFolding.js'
function fixture({folded=false,noFold=false}={}) {
  let handler,observer,disconnected=false,removed=false,button
  const classes=new Set(folded?['folded']:[])
  const panel={matches:s=>s==='.no-fold' && noFold,classList:{contains:s=>classes.has(s),toggle(s){classes.has(s)?classes.delete(s):classes.add(s)}}}
  const heading={parentElement:panel,matches:()=>false,append:value=>{button=value},querySelector:s=>s==='.panel-title'?{textContent:'目标位置'}:button}
  const title={closest:s=>s==='.panel-hd'?heading:null}
  const control={closest:s=>s==='.panel-hd'?heading:s.startsWith('button,')?control:null}
  const doc={body:{},querySelectorAll:()=>[heading],createElement:()=>{
    const attrs=new Map()
    const value={getAttribute:key=>attrs.get(key),setAttribute:(key,v)=>attrs.set(key,v),closest:s=>s==='.panel-hd'?heading:s==='.panel-fold-toggle'?value:null}
    return value
  },addEventListener:(_name,fn)=>{handler=fn},removeEventListener:(_name,fn)=>{removed=fn===handler}}
  class Observer {constructor(fn){observer=fn}observe(){}disconnect(){disconnected=true}}
  const dispose=installPanelFolding(doc,Observer)
  return {get button(){return button},panel,title,control,click:target=>handler({target}),refresh:()=>observer(),dispose,disposed:()=>disconnected&&removed}
}
test('folding creates one native button with truthful expanded state and accessible name',()=>{
 const f=fixture();assert.equal(f.button.type,'button');assert.equal(f.button.getAttribute('aria-expanded'),'true')
 assert.equal(f.button.getAttribute('aria-label'),'收起目标位置')
 f.click(f.button);assert.equal(f.panel.classList.contains('folded'),true);assert.equal(f.button.getAttribute('aria-expanded'),'false')
 assert.equal(f.button.getAttribute('aria-label'),'展开目标位置')
 f.click(f.button);assert.equal(f.panel.classList.contains('folded'),false)
 const original=f.button;f.refresh();assert.equal(f.button,original)
 f.dispose();assert.equal(f.disposed(),true)
})
test('heading pointer toggle remains available while child controls never collapse their panel',()=>{
 const f=fixture();f.click(f.control);assert.equal(f.panel.classList.contains('folded'),false)
 f.click(f.title);assert.equal(f.panel.classList.contains('folded'),true)
})
test('restored folds update button state; explicitly excluded panels receive no toggle',()=>{
 const f=fixture({folded:true});assert.equal(f.button.getAttribute('aria-expanded'),'false')
 f.panel.classList.toggle('folded');f.refresh();assert.equal(f.button.getAttribute('aria-expanded'),'true')
 assert.equal(fixture({noFold:true}).button,undefined)
})
