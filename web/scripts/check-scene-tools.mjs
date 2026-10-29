// Offline: pure geometry/request tests + actual SFC setup and template rendering.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { parseResponse } from '../src/connectionState.js'
import * as helpers from '../src/sceneTools.js'
import * as insights from '../src/workspaceInsights.js'
import * as objectCatalog from '../src/objectCatalog.js'

const read = path => readFileSync(new URL('../src/' + path,import.meta.url),'utf8')
const source = () => ({id:'box1',name:'源物体',kind:'box',xyz:[100,-200,80],size:[160,160,160],gravity:false,floor:true,collide:true})
const status = () => ({ok:true,revision:'opaque-revision',objects:[source()],templates:['workbench','pallet'],can_undo:false,can_redo:false})
const snap = () => ({mode:'sim',moving:false,objects:[source()],real:{},exec:{state:'idle',execution:{busy:false}},settings:{}})
const response = (value,code = 200) => ({ok:code < 400,status:code,json:async () => value})
const settle = async () => {for(let i=0;i<8;i++){await Promise.resolve();await Vue.nextTick()}}

test('3D array keeps source cell, enumerates only additions and computes negative-spacing bounds', () => {
  const original = source(), frozen = JSON.stringify(original)
  const result = helpers.gridArrayPreview(original,[2,2,2],[-200,150,100],1)
  assert.equal(result.total,8); assert.equal(result.added,7); assert.equal(result.positions.length,7)
  assert.deepEqual(result.positions[0],[100,-200,180]); assert.deepEqual(result.min,[-100,-200,80]); assert.deepEqual(result.max,[100,-50,180])
  assert.ok(!result.positions.some(p => JSON.stringify(p) === JSON.stringify(original.xyz)))
  assert.equal(JSON.stringify(original),frozen)
})
test('array rejects invalid counts, overlaps, non-finite/oversized coordinates and scene overflow', () => {
  for(const counts of [[1,1,1],[13,1,1],[5,5,6],[2.5,1,1],[true,2,1],[2,2]]) assert.throws(() => helpers.gridArrayPreview(source(),counts,[200,200,200],1))
  for(const spacing of [[0,200,0],[NaN,200,0],[Infinity,200,0],['200',200,0],[1000001,200,0]]) assert.throws(() => helpers.gridArrayPreview(source(),[2,2,1],spacing,1))
  assert.throws(() => helpers.gridArrayPreview(null,[2,2,1],[200,200,0],1),/选择/)
  assert.throws(() => helpers.gridArrayPreview(source(),[2,2,1],[200,200,0],998),/1000/)
  const edge = source(); edge.xyz[0] = 999999
  assert.throws(() => helpers.gridArrayPreview(edge,[2,1,1],[2,0,0],1),/范围/)
  assert.equal(helpers.gridArrayPreview(source(),[2,1,1],[200,0,0],999).added,1)
})
test('floor alignment uses geometric radius/half-height and never mutates source flags', () => {
  const box = source(); box.xyz[2] = 500
  assert.deepEqual(helpers.alignedPosition(box,'floor'),[100,-200,80]); assert.equal(box.xyz[2],500)
  const sphere = {...box,kind:'sphere',size:[70,70,70]}
  assert.equal(helpers.alignedPosition(sphere,'floor')[2],70)
  const cylinder = {...box,kind:'cylinder',size:[50,50,300]}
  assert.equal(helpers.alignedPosition(cylinder,'floor')[2],150)
  assert.equal(box.gravity,false); assert.equal(box.collide,true)
  assert.throws(() => helpers.alignedPosition({...box,kind:'mesh'},'floor'),/不支持/)
})
test('grid alignment respects selected axes, finite positive steps and range limits', () => {
  const object = {...source(),xyz:[121,-226,83]}
  assert.deepEqual(helpers.alignedPosition(object,'grid',50,[true,true,false]),[100,-250,83])
  assert.deepEqual(helpers.alignedPosition({...object,xyz:[-1,0,1]},'grid',50,[true,true,true]),[0,0,0])
  for(const step of [0,-1,NaN,Infinity,'50']) assert.throws(() => helpers.alignedPosition(object,'grid',step))
  assert.throws(() => helpers.alignedPosition(object,'grid',50,[false,false,false]),/至少/)
  assert.throws(() => helpers.alignedPosition(object,'unknown'),/未知/)
})
test('edit guard distinguishes disconnected, real, moving, paused, following and falling states', () => {
  assert.equal(helpers.sceneEditBlock(snap(),true),'')
  assert.match(helpers.sceneEditBlock(snap(),false),/未连接/)
  for(const patch of [{mode:'real'},{real:{cmd_up:true}},{real:{stream_up:true}}]) assert.match(helpers.sceneEditBlock({...snap(),...patch},true),/仿真/)
  for(const patch of [{moving:true},{moving:undefined},{paused:true},{exec:{state:'paused'}},{exec:{queue_state:'running'}},{hand_follow:{active:true}},{free:{on:true}},{ready:{running:true}},{exec:{stop_result:{settled:false}}}]) assert.match(helpers.sceneEditBlock({...snap(),...patch},true),/停止/)
  const value = snap(); value.objects[0].gravity = true; value.objects[0].xyz[2] = 500
  assert.match(helpers.sceneEditBlock(value,true),/下落/)
  value.objects[0].floating = true; assert.equal(helpers.sceneEditBlock(value,true),'')
})
test('request bodies match backend contract and retain opaque revisions verbatim', () => {
  const current = status()
  assert.deepEqual(helpers.sceneActionBody('template',current,{template:'pallet'}),{template:'pallet',revision:'opaque-revision'})
  assert.deepEqual(helpers.sceneActionBody('array',current,{id:'box1',counts:[2,2,1],spacing:[200,200,0]}),{id:'box1',counts:[2,2,1],spacing:[200,200,0],revision:'opaque-revision'})
  assert.throws(() => helpers.sceneActionBody('template',current,{template:'invented'}),/未提供/)
  assert.throws(() => helpers.sceneActionBody('undo',current),/没有/)
  current.can_undo = true; assert.deepEqual(helpers.sceneActionBody('undo',current),{revision:'opaque-revision'})
  current.can_redo = true; assert.deepEqual(helpers.sceneActionBody('redo',current),{revision:'opaque-revision'})
  assert.deepEqual(helpers.sceneActionBody('align',current,{id:'box1',mode:'floor'}),{ids:['box1'],ground:true,revision:'opaque-revision'})
  assert.deepEqual(helpers.sceneActionBody('align',current,{id:'box1',mode:'grid',step:25,axes:[true,false,true]}),{ids:['box1'],axis:['x','z'],step:25,revision:'opaque-revision'})
  assert.throws(() => helpers.sceneActionBody('align',current,{ids:['box1','missing'],mode:'floor'}),/不存在/)
  assert.throws(() => helpers.sceneActionBody('align',current,{ids:['box1','box1'],mode:'floor'}),/不存在/)
  assert.throws(() => helpers.readSceneStatus({ok:true}),/不完整/)
})

function fixture(t, fetch) {
  const world = Vue.reactive({snap:snap(),connected:true,selObj:'box1',patchObject:async () => {throw new Error('Unexpected property write')}})
  const props = Vue.reactive({active:false}), cleanup = [], scope = Vue.effectScope()
  const deps = {...helpers,parseResponse,useWorld:() => world,storeToRefs:Vue.toRefs,computed:Vue.computed,ref:Vue.ref,watch:Vue.watch,
    defineProps:() => props,onBeforeUnmount:fn => cleanup.push(fn),AbortController,fetch}
  const script = parse(read('components/SceneTools.vue')).descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
  const panel = scope.run(() => new Function(...Object.keys(deps),script+'\nreturn {refresh,act,align,status,busy,message,failed,counts,spacing,template,alignMode,alignment,source}')(...Object.values(deps)))
  t.after(() => {cleanup.forEach(fn => fn());scope.stop()})
  return {world,props,panel,cleanup}
}
test('inactive tools issue no request; activation reads only status and missing API hides operations', async t => {
  const requests = []
  const {panel,props} = fixture(t,async url => {requests.push(url);return response({detail:'not installed'},404)})
  await settle(); assert.deepEqual(requests,[])
  props.active = true; await settle(); assert.deepEqual(requests,['/api/workspace/scene'])
  assert.equal(panel.status.value,null); assert.equal(panel.failed.value,true); assert.match(panel.message.value,/404/)
  await panel.act('template'); assert.equal(requests.length,1)
})
test('template/array/history use real endpoints and exact revision; no writes occur on selection or preview', async t => {
  const requests = [], current = status()
  const {panel,props,world} = fixture(t,async (url,options) => {requests.push([url,options]);return response({...current,can_undo:true,can_redo:true})})
  props.active = true; await settle(); assert.equal(requests.length,1)
  panel.counts.value = [3,1,1]; panel.spacing.value = [-200,0,0]; await settle(); assert.equal(requests.length,1)
  await panel.act('array')
  assert.equal(requests[1][0],'/api/workspace/scene/array')
  assert.deepEqual(JSON.parse(requests[1][1].body),{id:'box1',counts:[3,1,1],spacing:[-200,0,0],revision:current.revision})
  panel.template.value = 'pallet'; await panel.act('template'); assert.equal(requests[2][0],'/api/workspace/scene/template')
  await panel.act('undo'); await panel.act('redo'); assert.deepEqual(requests.slice(3).map(([url]) => url),['/api/workspace/scene/undo','/api/workspace/scene/redo'])
  world.snap.mode = 'real'; await panel.act('template'); assert.equal(requests.length,5)
})
test('version conflict disables history and never retries or overwrites a newer scene', async t => {
  const requests = []
  const {panel,props} = fixture(t,async (url,options) => {requests.push([url,options]);return options?.method ? response({detail:'revision冲突'},409) : response({...status(),can_undo:true})})
  props.active = true; await settle(); await panel.act('undo')
  assert.equal(requests.length,2); assert.equal(panel.status.value,null); assert.equal(panel.failed.value,true)
  assert.match(panel.message.value,/revision冲突/); assert.match(panel.message.value,/未自动重试/)
  await panel.act('redo'); assert.equal(requests.length,2)
})
test('alignment uses revision-gated atomic API and keeps server history without changing gravity', async t => {
  const writes = []
  const {panel,props,world} = fixture(t,async (url,options) => {
    if (options?.method) writes.push([url,JSON.parse(options.body)])
    return response({...status(),revision:options?.method ? 'after-alignment' : 'opaque-revision',can_undo:!!options?.method})
  })
  props.active = true; await settle()
  world.snap.objects[0].xyz = [111,222,88]; panel.alignMode.value = 'grid'
  await panel.align(); assert.deepEqual(writes,[['/api/workspace/scene/align',{ids:['box1'],axis:['x','y'],step:50,revision:'opaque-revision'}]])
  assert.equal(panel.status.value.revision,'after-alignment'); assert.equal(panel.status.value.can_undo,true)
  assert.match(panel.message.value,/服务端确认/)
  panel.alignMode.value = 'floor';await panel.align()
  assert.deepEqual(writes[1],['/api/workspace/scene/align',{ids:['box1'],ground:true,revision:'after-alignment'}])
  assert.equal(world.snap.objects[0].gravity,false)
})
test('alignment rejects missing status, real mode and server mode-change race without a legacy patch fallback', async t => {
  const writes = []
  const {panel,props,world} = fixture(t,async (url,options) => {
    if (!options?.method) return response(status())
    writes.push([url,JSON.parse(options.body)])
    return response({detail:'仅允许未连接真机的仿真模式'},409)
  })
  world.snap.objects[0].xyz[2] = 300
  await panel.align(); assert.equal(writes.length,0)
  props.active = true; await settle()
  world.snap.mode = 'real';await panel.align();assert.equal(writes.length,0)
  world.snap.mode = 'sim';await panel.align();assert.equal(writes.length,1)
  assert.equal(writes[0][0],'/api/workspace/scene/align');assert.equal(writes[0][1].revision,'opaque-revision')
  assert.equal(panel.status.value,null);assert.match(panel.message.value,/仅允许未连接真机/)
  await panel.align();assert.equal(writes.length,1)
})
test('concurrent clicks cannot duplicate a write; unmount aborts a pending status read', async t => {
  let resolveWrite, signal, writes = 0
  const {panel,props,cleanup} = fixture(t,async (_url,options) => {
    if (!options?.method) {signal = options?.signal;return response(status())}
    writes++; return new Promise(resolve => {resolveWrite = () => resolve(response(status()))})
  })
  props.active = true; await settle()
  const first = panel.act('template'); await settle(); await panel.act('template'); assert.equal(writes,1)
  resolveWrite(); await first
  cleanup.forEach(fn => fn()); assert.equal(signal.aborted,false)
  const h = fixture(t,(_url,options) => {signal = options.signal;return new Promise((_resolve,reject) => signal.addEventListener('abort',() => reject(Object.assign(new Error('aborted'),{name:'AbortError'}))))})
  h.props.active = true; await settle(); h.cleanup.forEach(fn => fn()); await settle(); assert.equal(signal.aborted,true)
})

for(const file of ['components/SceneTools.vue','panels/ObjectsPanel.vue']) {
  test(`${file}: script/template/style compile and tab/action accessibility remains explicit`, () => {
    const {descriptor,errors} = parse(read(file),{filename:file});assert.deepEqual(errors,[])
    const script = compileScript(descriptor,{id:file})
    assert.deepEqual(compileTemplate({source:descriptor.template.content,id:file,filename:file,compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
    for(const style of descriptor.styles) assert.deepEqual(compileStyle({source:style.content,id:file,scoped:style.scoped}).errors,[])
    if(file.includes('ObjectsPanel')) {assert.match(read(file),/role="tabpanel"/);assert.match(read(file),/:aria-labelledby=/);assert.match(read(file),/label:'物体库'.*label:'现场文件'.*label:'批量'/)}
    else {assert.match(read(file),/原子修改 · 支持批量历史/);assert.match(read(file),/不覆盖其他编辑/)}
  })
}
test('backend source provides every batch route used; alignment cannot fall back to property writes', () => {
  const backend = readFileSync(new URL('../../backend/workspace_routes.py',import.meta.url),'utf8')
  for(const path of ['scene','scene/template','scene/array','scene/align','scene/undo','scene/redo']) assert.ok(backend.includes(`('/${path}')`))
  assert.doesNotMatch(read('components/SceneTools.vue'),/patchObject|api\/objects\/update|setInterval|connectReal|askReal|setPreview|moveJ|jog\(/)
})

function component(file, world, initialStatus = null, tab = null) {
  let sourceText = read(file)
  if(file === 'components/SceneTools.vue') sourceText = sourceText.replace('const status = ref(null)','const status = ref(initialStatus)')
  if(tab) sourceText = sourceText.replace("tab = ref('objects')",`tab = ref('${tab}')`)
  const {descriptor} = parse(sourceText,{filename:file})
  const imports = {vue:Vue,pinia:{storeToRefs:Vue.toRefs},'../store':{useWorld:() => world},
    '../sceneTools.js':helpers,'../objectCatalog.js':objectCatalog,'../connectionState.js':{parseResponse},'../workspaceInsights.js':insights}
  if(file === 'panels/ObjectsPanel.vue') {
    imports['../components/TaskTabs.vue'] = component('components/TaskTabs.vue',world)
    imports['../components/SceneTools.vue'] = component('components/SceneTools.vue',world)
  }
  const code = compileScript(descriptor,{id:file,inlineTemplate:true}).content
    .replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,path) => `const ${bindings.replace(/\bas\b/g,':')} = imports[${JSON.stringify(path)}];`)
    .replace('export default','return')
  return new Function('imports','initialStatus','fetch',code)(imports,initialStatus,() => {throw new Error('Network forbidden')})
}
for(const available of [false,true]) test(`actual SceneTools template renders ${available ? 'available controls' : 'unavailable state'} with no fake backend`,async () => {
  const world = Vue.reactive({snap:snap(),connected:true,selObj:'box1'})
  const app = Vue.createSSRApp(component('components/SceneTools.vue',world,available ? status() : null),{active:false})
  const warnings = [];app.config.warnHandler = message => warnings.push(message)
  const html = await renderToString(app)
  assert.deepEqual(warnings,[])
  if(available) {assert.match(html,/生成数组/);assert.match(html,/工作台布局/);assert.match(html,/新增 3 个/)}
  else {assert.doesNotMatch(html,/生成数组|追加所选模板|撤销批量操作<\/button>/);assert.match(html,/未开放/)}
  if(available) assert.match(html,/原子修改 · 支持批量历史/)
  else assert.doesNotMatch(html,/应用选中物体对齐/)
})
for(const tab of ['objects','scenes']) test(`Objects/${tab} retains existing content under accessible tab panel`,async () => {
  const world = Vue.reactive({snap:snap(),connected:true,selObj:null,scenes:[],activeScene:''})
  const app = Vue.createSSRApp(component('panels/ObjectsPanel.vue',world,null,tab))
  const warnings = [];app.config.warnHandler = message => warnings.push(message)
  const html = await renderToString(app)
  assert.deepEqual(warnings,[]);assert.match(html,/role="tablist"/);assert.match(html,/role="tabpanel"/)
  for(const text of ['场景资源库','投放高度','场景物体','保存','新建','导入 JSON','导出当前','搜索场景类型','添加并选中']) assert.ok(html.includes(text))
})
