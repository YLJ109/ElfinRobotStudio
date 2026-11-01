import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {computed,ref,reactive,watch,effectScope} from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import * as helpers from '../src/suctionControls.js'
const contract=JSON.parse(readFileSync(new URL('../../contracts/tool-actions.json',import.meta.url),'utf8'))
const source=readFileSync(new URL('../src/components/SuctionControls.vue',import.meta.url),'utf8')
const {descriptor}=parse(source)
const script=compileScript(descriptor,{id:'suction-test'}).content.replace(/^import .*\r?\n/gm,'').replace('export default','return')
const aliases=Object.fromEntries(['a','b','c','d'].map((name,bit)=>[name,{group:'box',bit,direction:'output',confirmed_by_user:true}]))
function fixture(t,options={}) {
 const calls=[],w=reactive({connected:true,stateFresh:true,snap:{mode:'sim'},toolPending:{},tools:{tool:{id:'dual',revision:'r1',type:'suction'},mounted:{suction:{left:{vacuum:false,blow:false,holding:null},right:{vacuum:true,blow:false,holding:'part'}}}},
  async toolAction(...args){calls.push(['action',...args]);return options.actionHandler ? await options.actionHandler(...args) : options.actionReply || {ok:true,sim:true}},
  async call(path,body){calls.push([path,body]);return options.reply || {ok:true}},async refreshTools(){calls.push(['refresh'])}})
 const deps={computed,ref,watch,useWorld:()=>w,...helpers,toolActions:contract.actions,
  window:{confirm:()=>options.confirm!==false},fetch:async path=>{calls.push(['GET',path]);return {ok:true,json:async()=>({io:aliases})}}}
 const component=new Function(...Object.keys(deps),script)(...Object.values(deps))
 const scope=effectScope(),panel=scope.run(()=>component.setup({configuration:true},{expose(){}}))
 t.after(()=>scope.stop());return {w,panel,calls}
}
test('actual suction component compiles and construction does not scan or change IO',t=>{
 assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'SuctionControls.vue',id:'suction'}).errors,[])
 assert.deepEqual(compileStyle({source:descriptor.styles[0].content,id:'suction'}).errors,[])
 const {panel,calls}=fixture(t)
 assert.equal(panel.actions.length,4);assert.deepEqual(calls,[])
})
test('actual component sends exact side; success does not invent local holding or feedback',async t=>{
 const {w,panel,calls}=fixture(t),before=JSON.stringify(w.tools.mounted)
 await panel.act('left','vacuum_on')
 assert.deepEqual(calls,[['action','vacuum_on',0,'left']])
 assert.equal(JSON.stringify(w.tools.mounted),before)
 assert.match(panel.message.value,/仿真/)
})
test('failed actions remain failures; conflicting and unconfirmed real actions are never sent',async t=>{
 const {w,panel,calls}=fixture(t,{actionReply:{ok:false,why:'输出反馈未知'},confirm:false})
 await panel.act('right','blow_on');assert.deepEqual(calls,[])
 await panel.act('left','vacuum_on');assert.equal(panel.failed.value,true);assert.equal(panel.message.value,'输出反馈未知')
 calls.length=0;w.snap.mode='real';await panel.act('left','vacuum_on');assert.deepEqual(calls,[])
})
test('mapping draft validates all four routes and retains user edits after rejected save',async t=>{
 const {panel,calls}=fixture(t,{reply:{ok:false,why:'revision conflict'}})
 await panel.loadAliases()
 const mapping={left:{vacuum:{alias:'a',on:1},blow:{alias:'b',on:0}},right:{vacuum:{alias:'c',on:1},blow:{alias:'d',on:0}}}
 panel.draft.value=mapping
 assert.equal(panel.mappingIssue.value,'');assert.equal(panel.dirty.value,true)
 await panel.save()
 assert.equal(panel.failed.value,true);assert.equal(panel.message.value,'revision conflict')
 assert.deepEqual(JSON.parse(JSON.stringify(panel.draft.value)),mapping)
 const submitted=calls.find(([path])=>path==='/api/tools/profile')[1]
 assert.deepEqual(JSON.parse(JSON.stringify(submitted.suction_io)),{version:1,sides:mapping})
 assert.ok(!calls.some(([path])=>path==='/api/tools/io-confirm'))
})
test('IO confirmation is a separate non-output request bound to current tool revision',async t=>{
 const {w,panel,calls}=fixture(t)
 await panel.loadAliases()
 panel.draft.value={left:{vacuum:{alias:'a',on:1},blow:{alias:'b',on:0}},right:{vacuum:{alias:'c',on:1},blow:{alias:'d',on:0}}}
 panel.original.value=JSON.parse(JSON.stringify(panel.draft.value))
 await panel.confirmMapping()
 assert.deepEqual(calls.find(([path])=>path==='/api/tools/io-confirm'),['/api/tools/io-confirm',{tool_id:'dual',revision:'r1'}])
 assert.ok(!calls.some(([path])=>path==='action'))
 w.tools.tool.revision='r2';panel.draft.value.left.vacuum.alias='changed'
 assert.notEqual(panel.mappingIssue.value,'')
})


test('four toggle controls use authoritative lane feedback, never optimistic labels',async t=>{
 const {w,panel,calls}=fixture(t)
 assert.equal(panel.toggleLabel('left','vacuum'),'吸气')
 assert.equal(panel.toggleLabel('right','vacuum'),'停止吸气')
 await panel.toggle('left','vacuum');assert.deepEqual(calls.at(-1),['action','vacuum_on',0,'left'])
 assert.equal(panel.toggleLabel('left','vacuum'),'吸气')
 w.tools.mounted.suction.left.vacuum=true
 assert.equal(panel.toggleLabel('left','vacuum'),'停止吸气')
 await panel.toggle('left','vacuum');assert.deepEqual(calls.at(-1),['action','vacuum_off',0,'left'])
 w.tools.mounted.suction.left.vacuum=false
 await panel.toggle('left','blow');assert.deepEqual(calls.at(-1),['action','blow_on',0,'left'])
 w.tools.mounted.suction.left.blow=true
 assert.equal(panel.toggleLabel('left','blow'),'停止放气')
 await panel.toggle('left','blow');assert.deepEqual(calls.at(-1),['action','blow_off',0,'left'])
 assert.doesNotMatch(descriptor.template.content,/单路动作|action in actions|>吸取<|>释放</)
 assert.match(descriptor.template.content,/channel in channels/)
})

test('toggle rejects unknown feedback and stale state, and failure does not flip state',async t=>{
 const {w,panel,calls}=fixture(t,{actionReply:{ok:false,why:'输出失败'}})
 w.tools.mounted.suction.left.vacuum=null
 assert.match(panel.toggleReason('left','vacuum'),/未知/)
 await panel.toggle('left','vacuum');assert.equal(calls.length,0)
 w.tools.mounted.suction.left.vacuum=false
 await panel.toggle('left','vacuum');assert.equal(panel.failed.value,true)
 assert.equal(panel.toggleLabel('left','vacuum'),'吸气');assert.equal(w.tools.mounted.suction.left.vacuum,false)
 w.stateFresh=false;await panel.toggle('right','vacuum');assert.equal(calls.length,1)
})

test('pending is bounded per lane; other side stop remains reachable and duplicates send nothing',async t=>{
 let finish
 const {w,panel,calls}=fixture(t,{actionHandler:()=>new Promise(resolve=>{finish=resolve})})
 const first=panel.toggle('left','vacuum')
 assert.match(panel.toggleReason('left','blow'),/处理/)
 assert.equal(panel.toggleReason('right','vacuum'),'')
 await panel.toggle('left','vacuum');assert.equal(calls.length,1)
 finish({ok:false,why:'超时'});await first
 assert.equal(panel.toggleReason('left','vacuum'),'')
 assert.equal(w.tools.mounted.suction.left.vacuum,false)
})
