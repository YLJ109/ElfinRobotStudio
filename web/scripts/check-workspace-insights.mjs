import * as numberDisplay from '../src/numberDisplay.js'
// Offline only: real Vue reactivity + SFC compilation; all service calls are mocks.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { createPinia } from 'pinia'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as helpers from '../src/workspaceInsights.js'
import * as runtimeMetrics from '../src/runtimeMetrics.js'
import * as performanceComparison from '../src/performanceComparison.js'
import { acceptanceView } from '../src/acceptanceView.js'
import { parseResponse } from '../src/connectionState.js'
import { useLogFollow } from '../src/logFollow.js'
import * as logSummary from '../src/logSummary.js'
import { OPERATION_NOTES } from '../src/operationNotes.js'
import { useWorld } from '../src/store.js'
import { toolActions } from '../src/shared/toolActions.ts'

const read = path => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
const q = [1, 2, 3, 4, 5, 6]
const valid = () => ({ mode:'real', joints:{ known:true, q:[0,0,0,0,0,0] }, preview:q.slice(),
  real:{ joints:q.map(v => v - .5), stream_up:true, cmd_up:true, quality:'valid' }, exec:{} })
const response = (value, status = 200) => ({ ok:status < 400, status, json:async () => value })
const settle = async () => { for (let i = 0; i < 6; i++) { await Promise.resolve(); await Vue.nextTick() } }

function fixture(t, file, exports, overrides = {}) {
  const hooks = { mounted:[], unmounted:[], activated:[], deactivated:[] }, timers = new Map()
  let timer = 0
  const world = Vue.reactive({ connected:true, snap:{ mode:'sim', moving:false, joints:{known:true,q}, settings:{ghost_opacity:.3}, exec:{state:'idle'}, real:{} },
    tele:null, play:{ on:false, loaded:true, n:2, idx:0 }, logs:[], meta:null,
    setSettings:async () => { throw new Error('Unexpected settings write') },
    call:async () => { throw new Error('Unexpected service write') },
    playLoad:async () => {}, playSeek() {} })
  const deps = { ...numberDisplay, computed:Vue.computed, ref:Vue.ref, reactive:Vue.reactive, watch:Vue.watch, nextTick:Vue.nextTick,
    ...helpers, ...runtimeMetrics, ...performanceComparison, acceptanceView, parseResponse, useWorld:() => world, storeToRefs:Vue.toRefs,
    defineProps:() => Vue.reactive({active:true}), defineExpose() {},
    onMounted:fn => hooks.mounted.push(fn), onBeforeUnmount:fn => hooks.unmounted.push(fn),
    onActivated:fn => hooks.activated.push(fn), onDeactivated:fn => hooks.deactivated.push(fn),
    setInterval:fn => { timers.set(++timer, fn); return timer }, clearInterval:id => timers.delete(id),
    fetch:async () => { throw new Error('Network forbidden') }, AbortController,
    ...overrides }
  const scope = Vue.effectScope()
  const source = parse(read(file)).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const panel = scope.run(() => new Function(...Object.keys(deps), source + `\nreturn {${exports.join(',')}}`)(...Object.values(deps)))
  t.after(() => { hooks.unmounted.forEach(fn => fn()); scope.stop(); assert.equal(timers.size, 0) })
  return { panel, world, hooks, timers, mount:() => hooks.mounted.forEach(fn => fn()) }
}

test('feedback never falls back to FK, cached real values, invalid arrays or simulation zeros', () => {
  const snap = valid(), run = helpers.workspaceRun(snap, true)
  assert.deepEqual(run.errors, [.5,.5,.5,.5,.5,.5]); assert.equal(run.maxError,.5)
  for (const change of [s => { s.real.quality = 'stale' }, s => { s.real.stream_up = false },
    s => { s.real.joints = q.slice(1) }, s => { s.real.joints[0] = NaN }, s => { s.joints.known = false }]) {
    const value = valid(); change(value)
    assert.equal(helpers.workspaceRun(value,true).feedback,null)
    assert.equal(helpers.workspaceRun(value,true).maxError,null)
  }
  assert.equal(helpers.workspaceRun(snap,false).maxError,null)
  snap.preview = null; assert.equal(helpers.workspaceRun(snap,true).errors,null)
  snap.mode = 'sim'; assert.deepEqual(helpers.workspaceRun(snap,true).feedback,[0,0,0,0,0,0])
})
test('targets are explicitly preview only; error is direct and not angle wrapped', () => {
  const s = valid(); s.preview[0] = 359; s.real.joints[0] = -1
  assert.equal(helpers.workspaceRun(s,true).errors[0],360)
  s.preview = null; s.exec.execution = {busy:true,action:'MOVEJ 1,2,3,4,5,6'}
  assert.equal(helpers.workspaceRun(s,true).target,null)
})
test('recovery distinguishes stale feedback, channels, faults and unknown stops', () => {
  assert.match(helpers.driveDiagnostics(valid(),false)[0].state,/未知/)
  const s = valid(); s.exec.stop_result = {acknowledged:true,settled:false}; s.real.cmd_up = false; s.real.err_code = 42
  const rows = helpers.driveDiagnostics(s,true)
  assert.ok(rows.some(row => row.state === '停止待核对'))
  assert.ok(rows.some(row => row.state === '命令断开'))
  assert.ok(rows.some(row => row.why.includes('42')))
  s.real.cmd_up = true; s.real.err_code = 0; s.exec.stop_result.settled = true
  assert.equal(helpers.driveDiagnostics(s,true)[0].state,'链路有效')
})
test('log filtering, task identity and consecutive counts preserve source records and chronology', () => {
  const logs = [1,2,3,4].map(seq => ({seq,t:seq,kind:seq === 3 ? 'alarm' : 'execution',text:seq === 3 ? '别的事件' : '等待',extra:{epoch:7}}))
  const original = JSON.stringify(logs)
  const rows = helpers.filterLogRows(logs,{kind:'execution',task:'7',keyword:'等待',collapse:true})
  assert.deepEqual(rows.map(row => [row.seq,row.lastSeq,row.count]),[[1,2,2],[4,4,1]])
  assert.equal(JSON.stringify(logs),original)
  assert.equal(helpers.filterLogRows(logs,{task:'missing'}).length,0)
  assert.equal(helpers.filterLogRows(logs,{keyword:'EPOCH'}).length,4)
  logs[1].extra.epoch = 8
  assert.equal(helpers.filterLogRows(logs,{collapse:true}).length,4)
})
test('tab persistence tolerates corrupt/blocked storage; keyboard focus wraps', t => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis,'localStorage')
  t.after(() => descriptor ? Object.defineProperty(globalThis,'localStorage',descriptor) : delete globalThis.localStorage)
  const tabs = [{id:'a'},{id:'b'}], memory = new Map()
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{ getItem:key => memory.get(key),setItem:(key,value) => memory.set(key,value) }})
  helpers.saveTaskTab('test','b'); assert.equal(helpers.readTaskTab('test',tabs,'a'),'b')
  helpers.saveTaskTab('test','invalid'); assert.equal(helpers.readTaskTab('test',tabs,'a'),'a')
  Object.defineProperty(globalThis,'localStorage',{configurable:true,get() { throw new Error('blocked') }})
  assert.doesNotThrow(() => helpers.saveTaskTab('test','b'))
  assert.equal(helpers.readTaskTab('test',tabs,'a'),'a')
  assert.equal(helpers.tabFocusIndex('ArrowLeft',0,2),1)
  assert.equal(helpers.tabFocusIndex('ArrowRight',1,2),0)
  assert.equal(helpers.tabFocusIndex('Home',1,2),0); assert.equal(helpers.tabFocusIndex('End',0,2),1)
  assert.equal(helpers.tabFocusIndex('Escape',0,2),null)
})
test('TaskTabs manual keyboard activation consumes global preview-space without device calls', t => {
  const emitted = [], props = Vue.reactive({tabs:[{id:'a'},{id:'b'}],modelValue:'a',id:'test',label:'test'})
  const {panel} = fixture(t,'components/TaskTabs.vue',['keydown','buttons'],{ defineProps:() => props,defineEmits:() => (...args) => emitted.push(args) })
  let focus = 0, prevented = 0, stopped = 0
  panel.buttons.value = [{focus() {focus=1}},{focus() {focus=2}}]
  const event = key => ({key,preventDefault() {prevented++},stopPropagation() {stopped++}})
  panel.keydown(event('ArrowRight'),0); assert.equal(focus,2); assert.deepEqual(emitted,[])
  panel.keydown(event(' '),1); assert.deepEqual(emitted,[['update:modelValue','b']])
  panel.keydown(event('Escape'),0); assert.equal(prevented,2); assert.equal(stopped,2)
})
test('settings stage, restore one item, reject conflict and failed save without claiming success', async t => {
  let current = {ghost_opacity:.3,trail_len:40}, writes = 0
  const {panel,world} = fixture(t,'panels/SettingsPanel.vue',['patch','restore','changes','draft','saved','failed','applyChanges'], {fetch:async () => response(current)})
  panel.patch({ghost_opacity:.5,trail_len:50}); assert.equal(panel.changes.value.length,2)
  panel.restore('trail_len'); assert.deepEqual(panel.draft.value,{ghost_opacity:.5})
  current.ghost_opacity = .4; await panel.applyChanges(); assert.equal(panel.failed.value,true); assert.match(panel.saved.value,/其他操作/)
  current.ghost_opacity = .3; world.setSettings = async () => { writes++; return {ok:false,why:'拒绝'} }
  await panel.applyChanges(); assert.equal(writes,1); assert.equal(panel.saved.value,'拒绝'); assert.equal(panel.changes.value.length,1)
  world.setSettings = async patch => { writes++; return {ok:true,settings:{...current,...patch}} }
  await panel.applyChanges(); assert.equal(panel.failed.value,false); assert.equal(panel.changes.value.length,0)
})
test('profiles stay unavailable on missing API and use actual compare revision; real/busy apply blocked', async t => {
  const calls = []
  const comparedRevision = 'c'.repeat(64), listedRevision = 'a'.repeat(64)
  const {panel,world} = fixture(t,'panels/SettingsPanel.vue',['tab','profiles','comparison','profileStatus','compareProfile','applyProfile','offlineIdle'], {fetch:async () => response({why:'not implemented'},404)})
  panel.tab.value = 'profiles'; await settle(); assert.equal(panel.profiles.value,null); assert.match(panel.profileStatus.value,/HTTP 404/)
  panel.profiles.value = [{id:'p1',revision:listedRevision}]
  world.call = async (url,body) => { calls.push([url,body]); return url.endsWith('/compare') ? {ok:true,profile:{id:'p1',revision:comparedRevision},changes:[{key:'trail_len',current:40,saved:50}]} : {ok:true} }
  panel.compareProfile('p1'); await settle(); assert.equal(panel.comparison.value.profile.revision,comparedRevision)
  world.snap.mode = 'real'; panel.applyProfile(); await settle(); assert.equal(calls.length,1)
  world.snap.mode = 'sim'; world.snap.moving = true; panel.applyProfile(); assert.equal(calls.length,1)
  world.snap.moving = false; assert.equal(panel.offlineIdle.value,true)
  panel.applyProfile(); await settle(); assert.deepEqual(calls[1],['/api/workspace/profiles/p1/apply',{revision:comparedRevision}])
  assert.notEqual(calls[1][1].revision,listedRevision)
})
test('profiles reject numeric or missing comparison revision and never use a list token as fallback', async t => {
  const {panel,world} = fixture(t,'panels/SettingsPanel.vue',['profiles','comparison','profileStatus','compareProfile','applyProfile'])
  panel.profiles.value = [{id:'p1',revision:'list-only-token'}]
  let writes = 0
  for (const revision of [9, null, '', undefined]) {
    world.call = async () => {writes++;return {ok:true,profile:{id:'p1',revision},changes:[]}}
    panel.compareProfile('p1');await settle();assert.equal(panel.comparison.value,null)
    assert.match(panel.profileStatus.value,/响应不完整/)
    panel.applyProfile();await settle()
  }
  assert.equal(writes,4)
})
test('profiles show actual HTTPException detail via real store request handling; conflict requires recompare', async t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis,'fetch'), requests = []
  let rejectApply = false
  const fakeFetch = async (url,options) => {
    requests.push([url,options?.method || 'GET'])
    if (url.endsWith('/compare')) return response({ok:true,profile:{id:'p1',revision:'0'.repeat(64)},changes:[{key:'trail_len',current:40,saved:50}]})
    if (rejectApply && url.endsWith('/apply')) return response({detail:'revision冲突：请重新比较'},409)
    if (options?.method === 'DELETE') return response({detail:'找不到配置档案'},404)
    if (options?.method === 'POST') return response({detail:[{msg:'档案名称需要1～64个字符'}]},422)
    return response({detail:'档案存储损坏，请先备份检查'},409)
  }
  Object.defineProperty(globalThis,'fetch',{configurable:true,value:fakeFetch})
  t.after(() => previous ? Object.defineProperty(globalThis,'fetch',previous) : delete globalThis.fetch)
  const {panel,world} = fixture(t,'panels/SettingsPanel.vue',['profileTask','loadProfiles','profileStatus','comparison','compareProfile','applyProfile','deleteProfile','saveProfile','profileName'],{fetch:fakeFetch})
  world.call = useWorld(createPinia()).call
  await panel.profileTask(panel.loadProfiles);assert.match(panel.profileStatus.value,/档案存储损坏/)
  panel.profileName.value = '新档案';panel.saveProfile();await settle();assert.match(panel.profileStatus.value,/档案名称需要1～64个字符/)
  panel.deleteProfile('missing');await settle();assert.match(panel.profileStatus.value,/找不到配置档案/)
  panel.compareProfile('p1');await settle();assert.equal(panel.comparison.value.profile.revision,'0'.repeat(64))
  rejectApply = true;panel.applyProfile();await settle()
  assert.match(panel.profileStatus.value,/revision冲突：请重新比较/);assert.equal(panel.comparison.value,null)
  const attempts = requests.length;panel.applyProfile();await settle();assert.equal(requests.length,attempts)
})
test('profile controls match supported backend keys and exclude inert legacy ghost mode and safety/workcell state', t => {
  const {panel} = fixture(t,'panels/SettingsPanel.vue',['profileKeys'])
  const backend = readFileSync(new URL('../../backend/workspace_routes.py',import.meta.url),'utf8')
  const block = backend.match(/PROFILE_KEYS = frozenset\(\{([\s\S]*?)\}\)/)[1]
  const keys = [...block.matchAll(/'([^']+)'/g)].map(match => match[1])
  assert.deepEqual([...panel.profileKeys].sort(),keys.filter(key=>key!=='ghost_mode').sort())
  assert.doesNotMatch(read('panels/SettingsPanel.vue'),/patch\(\{ ghost_mode/)
  for (const text of ['不含围栏、工具、限位、真机速度','不是完整工位备份','导入不应用']) assert.ok(read('panels/SettingsPanel.vue').includes(text))
})
test('profiles save and import never apply; delete uses its actual resource and rejects failures', async t => {
  const requests = [], writes = []
  const {panel,world} = fixture(t,'panels/SettingsPanel.vue',['profileName','importText','saveProfile','importProfile','deleteProfile','profiles','profileStatus'], {
    fetch:async (url, options) => { requests.push([url,options?.method || 'GET']); return response(options?.method === 'DELETE' ? {ok:true} : {ok:true,items:[]}) },
  })
  world.call = async (url,body) => {writes.push([url,body]); return {ok:true}}
  panel.profileName.value = '调试配置'; panel.saveProfile(); await settle()
  assert.deepEqual(writes[0],['/api/workspace/profiles',{name:'调试配置'}])
  panel.importText.value = JSON.stringify({settings:{trail_len:60}}); panel.importProfile(); await settle()
  assert.deepEqual(writes[1],['/api/workspace/profiles/import',{name:'调试配置',settings:{trail_len:60}}])
  assert.ok(writes.every(([url]) => !url.endsWith('/apply')))
  panel.deleteProfile('p 1'); await settle(); assert.ok(requests.some(([url,method]) => url.endsWith('/p%201') && method === 'DELETE'))
  world.call = async () => ({ok:false,why:'接口拒绝'})
  panel.saveProfile(); await settle(); assert.equal(panel.profileStatus.value,'接口拒绝')
})
test('telemetry polls only trend; leaving playback invalidates a pending load and clears timers', async t => {
  let requests = 0, resolveLoad
  const {panel,world,mount,timers} = fixture(t,'panels/TelemetryPanel.vue',['tab','togglePlay','trend'], {fetch:async () => {requests++; return response({n:0})}})
  mount(); assert.equal(timers.size,0); assert.equal(requests,0)
  panel.tab.value = 'trend'; await settle(); assert.equal(timers.size,1); assert.equal(requests,1)
  panel.tab.value = 'io'; await settle(); assert.equal(timers.size,0)
  panel.tab.value = 'playback'; await settle(); await panel.togglePlay(); assert.equal(timers.size,1)
  panel.tab.value = 'health'; await settle(); assert.equal(world.play.on,false); assert.equal(timers.size,0)
  panel.tab.value = 'playback'; await settle(); world.play.loaded = false
  world.playLoad = () => new Promise(resolve => {resolveLoad = resolve})
  const pending = panel.togglePlay(); panel.tab.value = 'io'; await settle(); resolveLoad(); await pending
  assert.equal(world.play.on,false); assert.equal(timers.size,0)
})

test('settings search resolves exact visible labels and key names to the matching category', async t => {
  const {panel} = fixture(t,'panels/SettingsPanel.vue',['search','tab','matchField','matches'])
  panel.search.value = '日志保留'; await settle(); assert.equal(panel.tab.value,'advanced'); assert.equal(panel.matches('advanced'),true)
  assert.equal(panel.matchField('回放缓冲 日志保留 play_len_s log_keep'),true)
  assert.equal(panel.matchField('速度口径 vel_source'),false)
  panel.search.value = 'ghost_opacity'; await settle(); assert.equal(panel.tab.value,'display')
})

// Render every branch using the compiled Vue templates and actual imported
// helpers. Only the initial UI tab is supplied by the fixture. SSR never runs
// mounted hooks, device calls or timers, and all fetches are forbidden.
function renderComponent(file, world, selectedTab) {
  let source = selectedTab ? read(file).replace(/\b(localTab|tab) = ref\('[^']+'\)/, (_match,name)=>`${name} = ref('${selectedTab}')`) : read(file)
  // SSR does not run mounted hooks. Seed the same real local snapshot that the
  // active performance tab reads on mount, so its actual table rows render too.
  if (file === 'panels/TelemetryPanel.vue' && selectedTab === 'performance') {
    source = source.replace('const runtimeStats = ref({})', 'const runtimeStats = ref(imports["../runtimeMetrics.js"].snapshotMetrics())')
  }
  const {descriptor} = parse(source,{filename:file})
  const dummy = {render:() => Vue.h('span')}
  const imports = { '../numberDisplay.js':numberDisplay, vue:Vue, pinia:{storeToRefs:Vue.toRefs}, '../store':{useWorld:() => world},
    '../workspaceInsights.js':helpers, '../runtimeMetrics.js':runtimeMetrics, '../performanceComparison.js':performanceComparison, '../acceptanceView.js':{acceptanceView}, '../connectionState.js':{parseResponse,isSixAxis:helpers.sixFinite},
    '../operationNotes':{OPERATION_NOTES}, '../logFollow.js':{useLogFollow}, '../logSummary.js':logSummary,
    '../components/SuctionControls.vue':dummy, '../components/Spark.vue':dummy, '../components/ReadyControl.vue':dummy, '../components/ProgramRules.vue':dummy,
    '../shared/toolActions.ts':{toolActions} }
  if (file !== 'components/TaskTabs.vue') imports['../components/TaskTabs.vue'] = renderComponent('components/TaskTabs.vue',world)
  if (file === 'panels/ControlPanel.vue') {
    imports['./SimPanel.vue']=renderComponent('panels/SimPanel.vue',world)
    imports['./PowerPanel.vue']=renderComponent('panels/PowerPanel.vue',world)
  }
  if (file === 'panels/SettingsPanel.vue') imports['../components/DataBackup.vue'] = renderComponent('components/DataBackup.vue',world)
  if (file === 'panels/EngineeringPanel.vue') imports['../components/RunJournal.vue'] = renderComponent('components/RunJournal.vue',world)
  const code = compileScript(descriptor,{id:file,inlineTemplate:true}).content
    .replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm, (_, bindings, path) =>
      `const ${bindings.replace(/\bas\b/g,':')} = imports[${JSON.stringify(path)}];`)
    .replace('export default','return')
  const storage = {getItem:() => null,setItem() {}}
  return new Function('imports','fetch','localStorage',code)(imports,() => {throw new Error('Network forbidden')},storage)
}
const branches = { Sim:['run','kinematics'], Power:['connection','control','diagnostics'],
  Engineering:['path','calibration','io','tooling','diagnostics','display'], Telemetry:['health','trend','io','playback','performance'],
  Settings:['connection','display','advanced','changes','profiles'], About:['overview','guide','diagnostics','validation'] }
for (const [name,tabs] of Object.entries(branches)) for (const tab of tabs) {
  test(`${name}/${tab}: actual template renders with no undefined binding or device calls`, async () => {
    const world = Vue.reactive({connected:true, snap:{...valid(),settings:{ghost_opacity:.3,ghost_align_tol_deg:.5,ghost_align_hold_s:.3},
      tcp:{xyz_mm:[0,0,0],rpy_deg:[0,0,0]},safety:{per_joint:[],capsules:[]},
      joints:{known:true,q,lower:q.map(() => -180),upper:q.map(() => 180),vmax:q.map(() => 90)},mode:'sim'},
      logs:[], tele:null, tools:{items:[],tool:null,mounted:null}, play:{on:false,n:0,marks:[]},meta:null })
    const app = Vue.createSSRApp(renderComponent(`panels/${name}Panel.vue`,world,tab))
    const warnings = []; app.config.warnHandler = message => warnings.push(message)
    const html = await renderToString(app)
    assert.match(html,/role="tabpanel"/); assert.match(html,/role="tablist"/)
    assert.deepEqual(warnings,[])
    if (name === 'Telemetry' && tab === 'performance') {
      assert.match(html,/telemetry-tab-performance/)
      assert.match(html,/本地性能/); assert.match(html,/重置样本/)
      for (const heading of ['n','p50','p95','max']) assert.match(html,new RegExp(`<th\\b[^>]*>${heading}</th>`))
      for (const metric of runtimeMetrics.METRIC_NAMES) assert.ok(html.includes(metric), `performance template missing ${metric}`)
      assert.ok(html.includes(String(runtimeMetrics.METRIC_WINDOW_SIZE)))
      assert.match(html,/GPU 未测量/); assert.match(html,/<summary\b[^>]*>统计说明<\/summary>/)
      assert.doesNotMatch(html,/<details[^>]*\sopen(?:\s|=|>)/)
    }
  })
}
test('Logs: actual template preserves virtual rows and count display with real follow helper', async () => {
  const world = Vue.reactive({logs:[{seq:1,t:1,kind:'execution',text:'等待',extra:{epoch:2}}]})
  const app = Vue.createSSRApp(renderComponent('panels/LogsPanel.vue',world))
  const warnings = []; app.config.warnHandler = message => warnings.push(message)
  const html = await renderToString(app)
  assert.match(html,/data-log-seq="1"/); assert.match(html,/等待/); assert.match(html,/1 事件 \/ 1 行/)
  assert.match(html,/title="execution"[^>]*>执行<\/span>/)
  assert.match(html,/<option value="execution">执行<\/option>/)
  assert.equal((html.match(/data-log-seq=/g)||[]).length,1)
  assert.equal(world.logs[0].kind,'execution');assert.equal(world.logs[0].text,'等待')
  assert.deepEqual(warnings,[])
})

for (const file of ['components/TaskTabs.vue',...['System','Engineering','Logs','Telemetry','Settings','About','Sim','Power'].map(name => `panels/${name}Panel.vue`)]) {
  test(`${file}: SFC script, template and scoped style compile`, () => {
    const {descriptor,errors} = parse(read(file),{filename:file}); assert.deepEqual(errors,[])
    const script = compileScript(descriptor,{id:file})
    const template = compileTemplate({source:descriptor.template.content,filename:file,id:file,compilerOptions:{bindingMetadata:script.bindings}})
    assert.deepEqual(template.errors,[])
    for (const style of descriptor.styles) assert.deepEqual(compileStyle({source:style.content,id:file,scoped:style.scoped}).errors,[])
  })
}


test('TaskTabs skips disabled items when moving focus and retains a reachable focused tab',t=>{
 const props=Vue.reactive({tabs:[{id:'a'},{id:'b',disabled:true},{id:'c'}],modelValue:'a',id:'skip',label:'skip'})
 const {panel}=fixture(t,'components/TaskTabs.vue',['keydown','buttons'],{defineProps:()=>props,defineEmits:()=>()=>{}})
 let focused=-1
 panel.buttons.value=props.tabs.map((_tab,i)=>({tabIndex:i===0?0:-1,focus(){focused=i}}))
 panel.keydown({key:'ArrowRight',preventDefault(){},stopPropagation(){}},0)
 assert.equal(focused,2);assert.equal(panel.buttons.value[2].tabIndex,0)
})


test('unified control renders one tab layer with the original panels and no duplicate active connection controls',async()=>{
 for(const selected of ['run','connection','kinematics','diagnostics']){
  const world=Vue.reactive({connected:true,controlTab:selected,snap:{...valid(),mode:'sim',settings:{},tcp:{xyz_mm:[0,0,0],rpy_deg:[0,0,0]},safety:{per_joint:[],capsules:[]},joints:{known:true,q,lower:q.map(()=>-180),upper:q.map(()=>180),vmax:q.map(()=>90)}},tele:null,tools:{items:[]},play:{on:false}})
  const app=Vue.createSSRApp(renderComponent('panels/ControlPanel.vue',world)),warnings=[]
  app.config.warnHandler=message=>warnings.push(message)
  const html=await renderToString(app)
  assert.deepEqual(warnings,[]);assert.equal((html.match(/role="tablist"/g)||[]).length,1)
  assert.ok(!html.includes('id="drive-tab-')&&!html.includes('id="overview-tab-'),'embedded panels must not add nested tabs')
  assert.equal([...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].filter(match=>match[1].replace(/<[^>]+>/g,'').trim()==='连接真机').length,selected==='diagnostics'?0:1,'one original connection action only in the connection section')
  assert.match(html,new RegExp('id="control-tab-'+selected+'"[^>]*aria-selected="true"'))
  assert.doesNotMatch(html,/id="control-tab-control"/,'simulation does not show real enable controls')
 }
})
