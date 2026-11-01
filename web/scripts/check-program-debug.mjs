import {createPinia} from 'pinia'
import {useWorld} from '../src/store.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

// 改造说明：标题栏左侧的「语法」快捷链接已被用户要求删除，语法只保留为程序页内的
// 标签页（唯一进入 ProgramRules 的入口）。这里断言新结构：快捷链接不再存在，
// 标签页仍能切到 rules 并渲染 ProgramRules，且不会跳去无关概览页。
test('程序语法在本页打开，明确项目语言边界，不再跳转到无关概览', () => {
  const panel = readFileSync(new URL('../src/panels/ProgramPanel.vue', import.meta.url), 'utf8')
  const rules = readFileSync(new URL('../src/components/ProgramRules.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(panel, /@click="programTab\s*=\s*'rules'"/u)
  assert.doesNotMatch(panel, /help-link/u)
  assert.match(panel, /\['rules','语法'\]/u)
  assert.match(panel, /v-if="programTab==='rules'"/u)
  assert.match(panel, /<ProgramRules\s*\/>/u)
  assert.doesNotMatch(panel, /@click="w.view = 'about'"/)
  assert.match(rules, /不是完整 Python 或厂商示教器格式/)
  assert.match(rules, /输出\(0,1\)/)
})

test('program panel compiles, keeps the syntax tab, and exposes no simulation debug entry', () => {
  const source = readFileSync(new URL('../src/panels/ProgramPanel.vue', import.meta.url), 'utf8')
  const { descriptor, errors } = parse(source)
  assert.deepEqual(errors, [])
  compileScript(descriptor, { id: 'program-debug-check' })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content,
    filename: 'ProgramPanel.vue', id: 'program-debug-check' }).errors, [])
  assert.match(source, /<ProgramEditor v-model="programSrc"/u)
  assert.match(source, /v-show="programTab==='editor'"/u)
  assert.match(source, /\['rules','语法'\]/u)
  assert.doesNotMatch(source, /仿真调试|program-pane-debug|debugTimer|breakpointText|单步|断点/u)
})


import {ref,computed,watch,reactive,toRefs,effectScope} from 'vue'
import {PROGRAM_COMMANDS,TOOL_ACTIONS,PYTHON_WORDS,CONTAINER_METHODS} from '../src/programLanguage.js'
function programHarness(t,options={}){
 const calls=[],scope=effectScope(),cleanups=[],timers=[]
 const world=reactive({snap:{mode:'sim',session_id:'boot-a',motion_granted:true,exec:{state:'idle'}},programSrc:'WAIT 0',programs:[],points:[],logs:[],
  async loadProgram(src){calls.push(['load',src]);return options.load?options.load(world):{ok:true}},
  async startProgram(){calls.push(['start']);return {ok:true,state:'running'}},
  async saveProgram(name,src){calls.push(['save',name,src]);return options.save?options.save(name,src):{ok:true}},
  async openProgram(name,settings){calls.push(['open',name,settings]);if(options.open)return options.open(name,settings,world);world.programSrc='WAIT 3';return {ok:true}}})
 const source=readFileSync(new URL('../src/panels/ProgramPanel.vue',import.meta.url),'utf8')
 const code=compileScript(parse(source).descriptor,{id:'program-direct'}).content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
 const deps={ref,computed,watch,storeToRefs:toRefs,useWorld:()=>world,PROGRAM_COMMANDS,TOOL_ACTIONS,PYTHON_WORDS,CONTAINER_METHODS,
  defineAsyncComponent:()=>({}),onMounted:()=>{},onBeforeUnmount:fn=>cleanups.push(fn),
  window:{prompt:()=>Object.hasOwn(options,'name')?options.name:'Demo',confirm:()=>options.confirm??true,addEventListener(){},removeEventListener(){}},
  localStorage:{getItem(){return null},setItem(){}},
  setTimeout:fn=>{timers.push(fn);return fn},clearTimeout(){},
  fetch:async path=>{calls.push(['fetch',path]);return {json:async()=>path.endsWith('/validate')?{ok:true,diagnostics:[],executable:true}:{ok:true}}},
  parseResponse:response=>response.json()}
 const component=new Function(...Object.keys(deps),code)(...Object.values(deps))
 const panel=scope.run(()=>component.setup({}, {expose(){}}))
 t.after(()=>{cleanups.forEach(fn=>fn());scope.stop()})
 return {panel,world,calls,timers}
}

test('simulation run starts immediately through compiler/load and never awaits a separate validate request',async t=>{
 const h=programHarness(t)
 assert.equal(h.panel.validation.value,'checking');assert.equal(h.panel.canRun.value,true)
 await h.panel.toggleRun()
 assert.deepEqual(h.calls,[['load','WAIT 0'],['start']])
 assert.equal(h.panel.active.value,true)
})

test('simulation rejects compile errors and changed source/environment before issuing start',async t=>{
 for(const load of [()=>({ok:false,why:'Syntax error'}),w=>{w.programSrc='WAIT 1';return {ok:true}},w=>{w.snap.mode='real';return {ok:true}},w=>{w.snap.session_id='boot-b';return {ok:true}},w=>{w.snap.motion_granted=false;return {ok:true}}]){
  const h=programHarness(t,{load});await h.panel.toggleRun()
  assert.equal(h.calls.filter(row=>row[0]==='start').length,0)
 }
})

test('direct program execution serializes clicks and retains real-mode validation',async t=>{
 let finish;const h=programHarness(t,{load:()=>new Promise(resolve=>finish=resolve)})
 const pending=h.panel.toggleRun();await h.panel.toggleRun()
 assert.equal(h.calls.filter(row=>row[0]==='load').length,1)
 finish({ok:true});await pending;assert.equal(h.calls.filter(row=>row[0]==='start').length,1)
 const real=programHarness(t);real.world.snap.mode='real';assert.equal(real.panel.canRun.value,false)
 await real.panel.toggleRun();assert.deepEqual(real.calls.map(row=>row[0]==='fetch'?row[1]:row[0]),['/api/program/validate','load','start'])
})

test('program pause resume and stop remain actual runner requests',async t=>{
 const h=programHarness(t);await h.panel.toggleRun();await h.panel.togglePause();await h.panel.togglePause();await h.panel.toggleRun()
 assert.deepEqual(h.calls.filter(row=>row[0]==='fetch').map(row=>row[1]),['/api/program/pause','/api/program/resume','/api/motion/cancel'])
 assert.equal(h.panel.active.value,false)
})


test('editor saves unnamed draft with a name once and subsequent saves retain that current name',async t=>{
 const h=programHarness(t,{name:' Demo one '});assert.equal(h.panel.dirty.value,true)
 await h.panel.save();assert.deepEqual(h.calls,[['save','Demo one','WAIT 0']])
 assert.equal(h.panel.libName.value,'Demo one');assert.equal(h.panel.dirty.value,false)
 h.world.programSrc='WAIT 2';assert.equal(h.panel.dirty.value,true)
 await h.panel.save();assert.deepEqual(h.calls.at(-1),['save','Demo one','WAIT 2']);assert.equal(h.panel.dirty.value,false)
})

test('cancelled naming and dirty new/load keep the unsaved draft intact',async t=>{
 const h=programHarness(t,{name:null,confirm:false})
 await h.panel.save();assert.equal(h.calls.length,0)
  h.panel.libName.value='';h.panel.savedProgram.value={name:'',source:''}
 h.world.programSrc='UNSAVED'
 assert.equal(h.panel.newProgram(),false);assert.equal(h.world.programSrc,'UNSAVED')
 h.panel.selectedName.value='Other';await h.panel.openProgram()
 assert.equal(h.calls.length,0);assert.equal(h.world.programSrc,'UNSAVED')
})

test('failed save preserves dirty draft, shows its cause, and does not assign a current filename',async t=>{
 const h=programHarness(t,{save:()=>({ok:false,why:'Disk unavailable'})})
 await h.panel.save();assert.equal(h.panel.dirty.value,true)
 assert.equal(h.panel.libName.value,'');assert.equal(h.world.programSrc,'WAIT 0')
 assert.equal(h.panel.fileError.value,'Disk unavailable')
})

test('editing during a successful save remains dirty and replacement of an existing filename requires consent',async t=>{
 let finish;const h=programHarness(t,{save:()=>new Promise(resolve=>finish=resolve)})
 const pending=h.panel.save();h.world.programSrc='NEWER DRAFT';finish({ok:true});await pending
 assert.equal(h.panel.dirty.value,true);assert.equal(h.panel.savedProgram.value.source,'WAIT 0')
 const denied=programHarness(t,{name:'Existing',confirm:false});denied.world.programs=[{name:'Existing'}]
 await denied.panel.save();assert.deepEqual(denied.calls,[])
})

test('editor exposes direct file controls and protects a dirty draft before window exit',t=>{
  const h=programHarness(t);let prevented=false
  const event={preventDefault(){prevented=true},returnValue:undefined}
  h.panel.protectDraft(event);assert.equal(prevented,true);assert.equal(event.returnValue,'')
  const source=readFileSync(new URL('../src/panels/ProgramPanel.vue',import.meta.url),'utf8')
  // 改造说明：「新建」「保存」已移到运行/暂停所在的底部运行条，标题栏不再放它们。
  // 断言新结构：四个操作按钮同处 .row run 组，且编辑器上方只剩文件名。
  const runRow=source.match(/<div class="row run">([\s\S]*?)<\/div>/u)
  assert.ok(runRow,'底部运行条必须存在')
  for(const action of ['@click="newProgram"','@click="save"','@click="toggleRun"','@click="togglePause"'])
    assert.ok(runRow[1].includes(action),`运行条缺少 ${action}`)
  const fileRow=source.match(/<div class="row editor-file-actions">([\s\S]*?)<\/div>/u)
  assert.ok(fileRow,'文件信息行必须存在')
  assert.doesNotMatch(fileRow[1],/@click="newProgram"|@click="save"/u)
  assert.ok(source.indexOf('<ProgramEditor')<source.indexOf('<div class="row run">'))
})


test('late program file response cannot overwrite a newer editor draft with expectedSource guard',async t=>{
 const world=useWorld(createPinia());world.programSrc='ORIGINAL'
 let resolve
 t.mock.method(globalThis,'fetch',()=>new Promise(done=>resolve=done))
 const pending=world.openProgram('Demo',{expectedSource:'ORIGINAL'})
 world.programSrc='USER EDITED WHILE LOADING'
 resolve({ok:true,status:200,json:async()=>({ok:true,src:'FILE CONTENT'})})
 const result=await pending
 assert.equal(result.ok,false);assert.match(result.why,/草稿已修改/)
 assert.equal(world.programSrc,'USER EDITED WHILE LOADING')
})

test('normal explicit file load still works, and component keeps failed loads dirty',async t=>{
 const world=useWorld(createPinia());world.programSrc='OLD'
 t.mock.method(globalThis,'fetch',async()=>({ok:true,status:200,json:async()=>({ok:true,src:'LOADED'})}))
 assert.equal((await world.openProgram('Demo')).ok,true);assert.equal(world.programSrc,'LOADED')
 const h=programHarness(t,{open:async()=>({ok:false,why:'草稿已修改'})})
 h.panel.selectedName.value='Demo';await h.panel.openProgram()
 assert.equal(h.world.programSrc,'WAIT 0');assert.equal(h.panel.dirty.value,true)
 assert.equal(h.panel.libName.value,'');assert.equal(h.panel.fileError.value,'草稿已修改')
 assert.equal(h.calls[0][2].expectedSource,'WAIT 0')
})

test('confirmed new program returns to editor without saving or loading a file',t=>{
 const h=programHarness(t);h.panel.programTab.value='library'
 assert.equal(h.panel.newProgram(),true)
 assert.equal(h.panel.programTab.value,'editor');assert.equal(h.panel.libName.value,'')
 assert.equal(h.panel.selectedName.value,'');assert.deepEqual(h.calls,[])
})
