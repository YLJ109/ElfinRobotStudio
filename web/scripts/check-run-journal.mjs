import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {ref} from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {parseResponse} from '../src/connectionState.js'
const source=readFileSync(new URL('../src/components/RunJournal.vue',import.meta.url),'utf8')
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
const response=value=>({ok:true,status:200,json:async()=>value})
function fixture(t,fetcher){
  const hooks=[],clicks=[],urls=[],revoked=[]
  const deps={ref,parseResponse,onMounted:()=>{},onActivated:()=>{},onDeactivated:fn=>hooks.push(fn),onBeforeUnmount:fn=>hooks.push(fn),fetch:fetcher,
    AbortController,setTimeout,clearTimeout,Blob,URL:{createObjectURL:()=>{urls.push('blob:test');return 'blob:test'},revokeObjectURL:v=>revoked.push(v)},document:{createElement:()=>({click:()=>clicks.push(true)})}}
  const script=parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
  const panel=new Function(...Object.keys(deps),script+'\nreturn {refresh,exportRun,cancel,runs,busy,message,source}')(...Object.values(deps))
  t.after(()=>hooks.forEach(fn=>fn()))
  return {panel,clicks,urls,revoked}
}
test('run journal compiles in existing engineering blackbox with only read actions',()=>{
  const {descriptor}=parse(source),script=compileScript(descriptor,{id:'run-journal'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'RunJournal.vue',id:'run-journal',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'run-journal',scoped:style.scoped}).errors,[])
  assert.doesNotMatch(source,/method:\s*['"]POST|\/run[/'"]|\/resume/)
  assert.match(readFileSync(new URL('../src/panels/EngineeringPanel.vue',import.meta.url),'utf8'),/<RunJournal\s*\/>/)
})
test('refresh reports partial confirmation and true source while stale responses cannot revive it',async t=>{
  const pending=deferred(),calls=[]
  const {panel}=fixture(t,(...args)=>{calls.push(args);return pending.promise})
  const task=panel.refresh();panel.cancel()
  pending.resolve(response({ok:true,analysis_only:true,runs:[{run_id:'a'.repeat(32)}]}));await task
  assert.deepEqual(panel.runs.value,[]);assert.equal(panel.busy.value,false)
  assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/runs');assert.equal(calls[0][1].method,undefined)
  assert.equal(panel.source({context:{mode:'sim',action:'sorting'}}),'仿真 · 分拣')
})
test('single record export validates identity and never starts or replays it',async t=>{
  const calls=[],row={run_id:'a'.repeat(32)}
  const {panel,clicks}=fixture(t,async(...args)=>{calls.push(args);return response({ok:true,analysis_only:true,record:{run_id:row.run_id,phase:'aborted',confirmed_steps:1}})})
  await panel.exportRun(row)
  assert.equal(clicks.length,1);assert.equal(calls[0][0],'/api/runs/'+row.run_id+'/export')
  assert.equal(calls[0][1].method,undefined)
  const invalid=fixture(t,async()=>response({ok:true,analysis_only:true,record:{run_id:'other'}}))
  await invalid.panel.exportRun(row);assert.equal(invalid.clicks.length,0);assert.match(invalid.panel.message.value,/身份不符/)
})
