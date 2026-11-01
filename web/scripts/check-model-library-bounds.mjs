import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import * as assets from '../src/visualAssets.js'
const source=readFileSync(new URL('../src/components/ModelLibrary.vue',import.meta.url),'utf8')
function fixture(){
 const mounted=[],unmount=[],listeners=new Map(),observed=new Set();let disconnected=false
 const deps={...Vue,...assets,loadToolCatalog:async()=>({tools:[]}),onMounted:fn=>mounted.push(fn),onBeforeUnmount:fn=>unmount.push(fn),localStorage:{getItem:()=>null,setItem(){}},
   window:{innerWidth:900,innerHeight:515,addEventListener:(key,fn)=>listeners.set(key,fn),removeEventListener:key=>listeners.delete(key)},
   ResizeObserver:class{constructor(fn){this.fn=fn;fixture.resize=fn}observe(target){observed.add(target)}disconnect(){disconnected=true;observed.clear()}}}
 const code=compileScript(parse(source).descriptor,{id:'bounds'}).content.replace(/^import[^\n]*\n/gm,'').replace('export default','return')
 const keys=Object.keys(deps).filter(key=>key!=='default'&&/^[\w$]+$/.test(key)),scope=Vue.effectScope()
 const state=scope.run(()=>new Function(...keys,code)(...keys.map(key=>deps[key])).setup(Vue.reactive({selectedToolId:'none'}),{expose(){},emit(){}}))
 return {state,mounted,listeners,observed,viewport:deps.window,get disconnected(){return disconnected},close(){unmount.forEach(fn=>fn());scope.stop()}}
}
test('model library bounds follow actual stage at zoom and never inherit viewport 70vh clipping',async()=>{
 const h=fixture();let stage={left:402,top:56.3986,right:876,bottom:468},anchor={left:414,top:437,bottom:459,height:22}
 const stageNode={getBoundingClientRect:()=>stage},anchorNode={getBoundingClientRect:()=>anchor,closest:()=>stageNode}
 try{
   h.state.anchor.value=anchorNode;h.mounted.forEach(fn=>fn());h.state.open.value=true;await Vue.nextTick()
   function check(){const style=h.state.libraryStyle.value,width=parseFloat(style.width),height=parseFloat(style.maxHeight),left=anchor.left+parseFloat(style.left),bottom=anchor.bottom-parseFloat(style.bottom);assert.ok(left>=stage.left&&left+width<=stage.right);assert.ok(bottom-height>=stage.top&&bottom<=stage.bottom);return {width,height}}
   check();assert.equal(h.observed.size,2)
   stage={left:642,top:90,right:882,bottom:440};anchor={left:654,top:409,bottom:431,height:22};fixture.resize();const narrow=check();assert.ok(narrow.width<240)
   h.viewport.innerHeight=300;stage.bottom=270;anchor.top=239;anchor.bottom=261;h.listeners.get('resize')();const short=check();assert.ok(short.height<narrow.height)
 }finally{h.close()}
 assert.equal(h.disconnected,true);assert.equal(h.listeners.size,0);assert.equal(h.observed.size,0)
 const previous=h.state.libraryStyle.value;stage.left=0;fixture.resize();assert.equal(h.state.libraryStyle.value,previous,'queued resize after unmount must not update disposed UI')
})
