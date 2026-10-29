import test from 'node:test'
import assert from 'node:assert/strict'
import {createWorkspaceUiState,readPreference,savePreference} from '../src/workspaceState.js'

const memory=()=>{const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v)}}
test('per-tab view survives another tab updating the shared default',t=>{
  const descriptors=['sessionStorage','localStorage'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)])
  t.after(()=>{for(const [k,d] of descriptors)d?Object.defineProperty(globalThis,k,d):delete globalThis[k]})
  const own=memory(), shared=memory()
  Object.defineProperty(globalThis,'sessionStorage',{value:own,configurable:true})
  Object.defineProperty(globalThis,'localStorage',{value:shared,configurable:true})
  savePreference('view','current-tab-view');shared.setItem('view','other-tab-view')
  assert.equal(readPreference('view'),'current-tab-view')
})
test('folds, details and scroll restore without serializing form data',()=>{
  const storage=memory(), listeners=new Map(), classes=new Set(),details=new Set()
  const node=(name,types)=>({id:name,className:'',tagName:'DIV',textContent:'',scrollTop:0,scrollLeft:0,open:false,
    matches:s=>s==='details'&&types==='details',querySelector:()=>null,getAttribute:()=>null,
    classList:{contains:k=>(types==='details'?details:classes).has(k),toggle(k,on){const set=types==='details'?details:classes;on?set.add(k):set.delete(k)}}})
  const panel=node('panel'), disclosure=node('details','details'), list=[panel,disclosure]
  const root={querySelectorAll:()=>list,addEventListener:(k,fn)=>listeners.set(k,fn),removeEventListener:k=>listeners.delete(k)}
  const opts={storage,Observer:null,raf:fn=>{fn();return 1},cancel(){}}
  const ui=createWorkspaceUiState(root,()=>'/settings',opts)
  panel.classList.toggle('folded',true);panel.scrollTop=170;disclosure.open=true;ui.capture();ui.dispose()
  panel.classList.toggle('folded',false);panel.scrollTop=0;disclosure.open=false
  const next=createWorkspaceUiState(root,()=>'/settings',opts)
  assert.equal(panel.classList.contains('folded'),true);assert.equal(panel.scrollTop,170);assert.equal(disclosure.open,true)
  assert.equal(listeners.size,3);next.dispose();assert.equal(listeners.size,0)
})
