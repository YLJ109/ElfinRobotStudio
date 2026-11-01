import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {ref,effectScope} from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {parseResponse} from '../src/connectionState.js'
const {descriptor}=parse(readFileSync(new URL('../src/components/DataBackup.vue',import.meta.url),'utf8'))
const script=compileScript(descriptor,{id:'backup-test'}).content.replace(/^import .*\r?\n/gm,'').replace('export default','return')
function fixture(t,options={}){
 const calls=[],mounted=[],unmounted=[]
 const deps={ref,parseResponse,onMounted:f=>mounted.push(f),onBeforeUnmount:f=>unmounted.push(f),globalThis:{elfinDesktop:options.desktop},
  AbortController,setTimeout,clearTimeout,JSON,Blob,URL,confirm:()=>true,
  fetch:async(path,init)=>{calls.push({path,init});return options.fetch?options.fetch(path,init):{ok:true,status:200,json:async()=>({ok:true,token:'preview',changes:[]})}}}
 const component=new Function(...Object.keys(deps),script)(...Object.values(deps));const scope=effectScope()
 const panel=scope.run(()=>component.setup({},{expose(){}}));t.after(()=>{unmounted.forEach(f=>f());scope.stop()})
 return {panel,calls,mounted,unmounted}
}
test('backup UI compiles and never starts export/import on construction',t=>{assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'DataBackup.vue',id:'backup'}).errors,[]);const f=fixture(t);assert.equal(f.calls.length,0);assert.equal(f.panel.preview.value,null)})
test('program difference renders actual encoded API summary as readable source',t=>{const f=fixture(t);assert.equal(f.panel.displayValue(JSON.stringify({first_changed_line:2,lines:3,text:'WAIT 2'})),'第2行起 · 3行\nWAIT 2');assert.equal(f.panel.displayValue('无'),'无');assert.equal(f.panel.displayValue('{invalid'),' {invalid'.trim())})
test('invalid input and failed preview never leave an applicable old token',async t=>{const f=fixture(t,{fetch:async()=>({ok:false,status:422,json:async()=>({detail:'坏文件'})})});f.panel.preview.value={token:'old'};await f.panel.inspect({target:{files:[{size:2,text:async()=>'{}'}],value:'x'}});assert.equal(f.panel.preview.value,null);assert.match(f.panel.message.value,/坏文件/);assert.equal(f.panel.restored.value,null)})
test('cancelled late preview and unmounted response cannot restore an old UI success',async t=>{let resolve;const promise=new Promise(r=>resolve=r);const f=fixture(t,{fetch:()=>promise});const pending=f.panel.inspect({target:{files:[{size:2,text:async()=>'{}'}],value:'x'}});await Promise.resolve();await Promise.resolve();f.panel.cancel();resolve({ok:true,status:200,json:async()=>({ok:true,token:'late',changes:[]})});await pending;assert.equal(f.panel.preview.value,null);assert.equal(f.panel.busy.value,false);assert.equal(f.panel.message.value,'已取消')})
test('restore uses exactly the reviewed token and does not claim the active workspace changed',async t=>{const f=fixture(t,{fetch:async()=>({ok:true,status:200,json:async()=>({ok:true,copy_id:'copy',data_dir:'isolated-copy',active:false})})});f.panel.preview.value={token:'reviewed'};await f.panel.restore();assert.equal(JSON.parse(f.calls[0].init.body).token,'reviewed');assert.equal(f.panel.restored.value.active,false);assert.match(f.panel.message.value,/当前工作区未替换/)})
