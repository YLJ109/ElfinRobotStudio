import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {parse} from '@vue/compiler-sfc'
import {baseParse} from '@vue/compiler-dom'

test('syntax tab is independent of the hidden library panel',()=>{
 const s=readFileSync(new URL('../src/panels/ProgramPanel.vue',import.meta.url),'utf8')
 const ast=baseParse(parse(s).descriptor.template.content)
 let found=false
 function walk(node,ancestors=[]){
  if(node.type===1 && node.props.some(p=>p.type===6 && p.name==='id' && p.value?.content==='program-pane-rules')){
   found=true
   assert.ok(!ancestors.some(a=>a.props?.some(p=>p.type===7 && ['show','if'].includes(p.name) && p.exp?.content.includes("programTab==='library'"))),'syntax must not inherit library-only visibility')
  }
  for(const child of node.children||[])walk(child,[...ancestors,node])
 }
 walk(ast);assert.ok(found)
})
