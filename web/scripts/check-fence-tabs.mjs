// Offline only: compile the real SFCs, mount with Vue's renderer and a fake World.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as insights from '../src/workspaceInsights.js'

const read = path => readFileSync(new URL('../src/' + path,import.meta.url),'utf8')
const clone = value => JSON.parse(JSON.stringify(value))
const settle = async () => {for(let i=0;i<6;i++){await Promise.resolve();await Vue.nextTick()}}
function world() {
  const writes = []
  const w = Vue.reactive({fenceVisible:true,meta:{limit_sources:{urdf:'URDF',manual:'示教器实测'}},snap:{mode:'sim',settings:{
    fence:{enabled:true,x:[-500,500],y:[-600,600],z:[0,1000]},ground_enabled:true,self_collision:true,
    safety_yellow_pct:90,safety_red_pct:95,safety_alarm_pct:100,custom_limits:Array.from({length:6},() => [-180,180]),limit_source:'urdf',tts:false,tts_rate:1,
  },safety:{fence_level:'green',ground_level:'green',joint_level:'green'}},
    async setSettings(patch) {writes.push(clone(patch));return {ok:true}},
    call() {throw new Error('Unexpected API call')},connectReal() {throw new Error('Hardware forbidden')},
  })
  return {w,writes}
}
function component(path,w,inline = true) {
  const {descriptor,errors} = parse(read(path),{filename:path});assert.deepEqual(errors,[])
  const modules = {vue:Vue,pinia:{storeToRefs:Vue.toRefs},'../store':{useWorld:() => w},'../workspaceInsights.js':insights}
  if(path === 'panels/FencePanel.vue') modules['../components/TaskTabs.vue'] = component('components/TaskTabs.vue',w)
  const code = compileScript(descriptor,{id:path,inlineTemplate:inline}).content
    .replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,name) => `const ${bindings.replace(/\bas\b/g,':')} = modules[${JSON.stringify(name)}];`)
    .replace('export default','return')
  return new Function('modules',code)(modules)
}
function setup(t) {
  const {w,writes} = world(), scope = Vue.effectScope()
  const panel = scope.run(() => component('panels/FencePanel.vue',w,false).setup({}, {expose() {}}))
  t.after(() => scope.stop())
  return {w,writes,panel}
}
const all = node => [node,...node.children.flatMap(all)]
const text = node => node.text + node.children.map(text).join('')
function node(tag,value = '') {
  const listeners = new Map()
  return {tag,tagName:tag.toUpperCase(),text:value,props:{},children:[],parent:null,style:{display:''},value:'',selected:false,
    addEventListener(name,fn) {if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn)},
    removeEventListener(name,fn) {listeners.get(name)?.delete(fn)},
    fire(name,event) {for(const fn of listeners.get(name) || [])fn(event)},
    get options() {return all(this).filter(el => el.tag === 'option')},
    setAttribute(name,value) {this.props[name] = value},removeAttribute(name) {delete this.props[name]},
    getAttribute(name) {return this.props[name] ?? null},focus() {this.focused = true},
  }
}
const renderer = Vue.createRenderer({
  createElement:tag => node(tag),createText:value => node('#text',value),createComment:value => node('#comment',value),
  insert(el,parent,anchor) {if(el.parent)el.parent.children.splice(el.parent.children.indexOf(el),1);el.parent = parent;const index = anchor ? parent.children.indexOf(anchor) : -1;index < 0 ? parent.children.push(el) : parent.children.splice(index,0,el)},
  remove(el) {if(el.parent)el.parent.children.splice(el.parent.children.indexOf(el),1);el.parent = null},
  setText(el,value) {el.text = value},setElementText(el,value) {el.text = value;el.children = []},
  parentNode:el => el.parent,nextSibling:el => el.parent?.children[el.parent.children.indexOf(el) + 1] || null,
  patchProp(el,key,_old,value) {el.props[key] = value;if(key === 'value'){el.value = value;el._value = value}if(key === 'style' && value)Object.assign(el.style,value)},
})
async function emit(el,type,extra = {}) {
  assert.ok(el,`target for ${type} exists`)
  const event = {target:el,currentTarget:el,key:'',preventDefault() {},stopPropagation() {},...extra}
  el.fire(type,event)
  const handler = el.props['on' + type[0].toUpperCase() + type.slice(1)]
  for(const fn of Array.isArray(handler) ? handler : handler ? [handler] : [])fn(event)
  await settle()
}
async function mount(t,savedTab = null) {
  const {w,writes} = world(), root = node('root'), storage = new Map(savedTab ? [['huayan.tabs.fence.v1',savedTab]] : [])
  const previous = ['localStorage','document'].map(key => [key,Object.getOwnPropertyDescriptor(globalThis,key)])
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key => storage.get(key) ?? null,setItem:(key,value) => storage.set(key,value)}})
  Object.defineProperty(globalThis,'document',{configurable:true,value:{activeElement:null}})
  const warnings = [],app = renderer.createApp(component('panels/FencePanel.vue',w))
  app.config.warnHandler = message => warnings.push(message)
  app.mount(root)
  t.after(() => {app.unmount();for(const [key,descriptor] of previous)descriptor ? Object.defineProperty(globalThis,key,descriptor) : delete globalThis[key];assert.deepEqual(warnings,[])})
  await settle()
  const tabs = () => all(root).filter(el => el.props.role === 'tab')
  const sections = all(root).filter(el => el.tag === 'section')
  return {w,writes,root,storage,tabs,sections,
    select:async id => emit(tabs().find(el => el.props.id === 'fence-tab-' + id),'click'),
    input:label => all(root).find(el => el.props['aria-label'] === label),
  }
}

test('four actual tabs share accessible panel and change views with zero settings writes',async t => {
  const h = await mount(t)
  assert.deepEqual(h.tabs().map(text),['范围','检测','阈值','播报'])
  const panel = all(h.root).find(el => el.props.id === 'fence-panel')
  assert.equal(panel.props.role,'tabpanel');assert.equal(panel.props.tabindex,'0')
  for(const [index,id] of ['range','detection','thresholds','voice','range'].entries()) {
    await h.select(id)
    assert.equal(panel.props['aria-labelledby'],'fence-tab-' + id)
    assert.equal(h.tabs().filter(el => el.props['aria-selected']).length,1)
    for(const tab of h.tabs())assert.equal(tab.props['aria-controls'],'fence-panel')
    assert.equal(h.sections.filter(el => el.style.display !== 'none').length,1)
    assert.equal(h.sections[index % 4].style.display,'')
  }
  assert.deepEqual(h.writes,[])
})
test('v-show retains exact form nodes, draft values and expanded six-axis details across tabs',async t => {
  const h = await mount(t), x = h.input('X 最小值（毫米）'), yellow = h.input('黄色预警阈值（百分比）'), limit = h.input('J1 最小限位（度）')
  x.value = '-450';await emit(x,'input')
  await h.select('thresholds');yellow.value = '89';await emit(yellow,'input')
  await h.select('detection');const details = all(h.root).find(el => el.tag === 'details');details.open = true
  limit.value = '-170';await emit(limit,'input')
  for(const id of ['voice','range','thresholds','detection'])await h.select(id)
  assert.equal(h.input('X 最小值（毫米）'),x);assert.equal(x.value,'-450')
  assert.equal(h.input('黄色预警阈值（百分比）'),yellow);assert.equal(yellow.value,'89')
  assert.equal(h.input('J1 最小限位（度）'),limit);assert.equal(limit.value,'-170');assert.equal(details.open,true)
  assert.deepEqual(h.writes,[])
  assert.deepEqual(h.w.snap.settings.fence.x,[-500,500])
})
test('real TaskTabs restores selection and handles manual keyboard focus/activation without settings calls',async t => {
  const h = await mount(t,'voice')
  assert.equal(h.tabs()[3].props['aria-selected'],true)
  await emit(h.tabs()[3],'keydown',{key:'ArrowLeft'})
  assert.equal(h.tabs()[2].focused,true);assert.equal(h.tabs()[3].props['aria-selected'],true)
  let stopped = false
  await emit(h.tabs()[2],'keydown',{key:' ',stopPropagation() {stopped = true}})
  assert.equal(stopped,true);assert.equal(h.tabs()[2].props['aria-selected'],true)
  assert.equal(h.storage.get('huayan.tabs.fence.v1'),'thresholds');assert.deepEqual(h.writes,[])
})
test('real detection controls stay disabled and both direct guards refuse disabling protection',async t => {
  const {w,writes,panel} = setup(t);w.snap.mode = 'real';await settle()
  for(const key of ['fence','ground_enabled','self_collision']) {
    const event = {target:{checked:false}};panel.changeDetection(key,event);assert.equal(event.target.checked,true)
  }
  await panel.patch({fence:{...w.snap.settings.fence,enabled:false}},'围栏')
  await panel.patch({ground_enabled:false},'地面');await panel.patch({self_collision:false},'自碰撞')
  assert.deepEqual(writes,[])
  await panel.patch({tts:true},'播报');assert.deepEqual(writes,[{tts:true}])
})
test('mounted real-mode detector checkboxes remain disabled, but already-disabled protection can be enabled',async t => {
  const h = await mount(t);h.w.snap.mode = 'real';await h.select('detection')
  const checks = all(h.sections[1]).filter(el => el.tag === 'input' && el.props.type === 'checkbox')
  assert.equal(checks.length,2);assert.ok(checks.every(el => el.props.disabled === true))
  h.w.snap.settings.ground_enabled = false;await settle();assert.equal(checks[1].props.disabled,false)
  assert.deepEqual(h.writes,[])
})
test('range save still validates numbers, retains live enabled flag and only writes on explicit save',async t => {
  const {w,writes,panel} = setup(t)
  panel.saveRange();assert.deepEqual(writes,[])
  panel.range.value.x[0] = NaN;panel.saveRange();assert.deepEqual(writes,[])
  panel.range.value.x[0] = -400;panel.range.value.enabled = true
  w.snap.settings.fence.enabled = false;await settle()
  panel.range.value.x[0] = -400;panel.range.value.enabled = true
  panel.saveRange();await settle();assert.equal(writes.length,1)
  assert.deepEqual(writes[0].fence,{enabled:false,x:[-400,500],y:[-600,600],z:[0,1000]})
})
test('threshold and six-axis validation and original request shapes remain intact',async t => {
  const {writes,panel} = setup(t)
  panel.thresholds.value.safety_yellow_pct = 96;panel.saveThresholds();assert.equal(writes.length,0)
  panel.thresholds.value.safety_yellow_pct = 89;panel.saveThresholds();await settle()
  assert.deepEqual(writes[0],{safety_yellow_pct:89,safety_red_pct:95,safety_alarm_pct:100})
  panel.limits.value[0] = [180,-180];panel.saveLimits();assert.equal(writes.length,1)
  panel.limits.value[0] = [-170,170];panel.saveLimits();await settle()
  assert.deepEqual(writes[1],{custom_limits:[[-170,170],...Array.from({length:5},() => [-180,180])]})
})
test('busy prevents duplicate save; error feedback survives view changes and restores busy state',async t => {
  const {w,writes,panel} = setup(t);let finish
  w.setSettings = patch => {writes.push(clone(patch));return new Promise(resolve => {finish = resolve})}
  const pending = panel.patch({tts:true},'播报');await panel.patch({tts_rate:1.2},'语速');assert.equal(writes.length,1)
  finish({ok:false,why:'服务器拒绝'});await pending
  panel.tab.value = 'range';await settle();assert.equal(panel.message.value,'服务器拒绝');assert.equal(panel.failed.value,true);assert.equal(panel.busy.value,false)
  w.setSettings = async () => {throw new Error('请求失败')};await panel.patch({tts:true},'播报')
  assert.equal(panel.message.value,'请求失败');assert.equal(panel.busy.value,false)
})
test('FencePanel script/template/styles compile; four sections use v-show and units/safety information remain',() => {
  const source = read('panels/FencePanel.vue'),{descriptor,errors} = parse(source,{filename:'FencePanel.vue'});assert.deepEqual(errors,[])
  const script = compileScript(descriptor,{id:'fence'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'fence',filename:'FencePanel.vue',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'fence',scoped:style.scoped}).errors,[])
  assert.equal([...source.matchAll(/<section v-show=/g)].length,4)
  assert.doesNotMatch(source,/BASE ·|COLLISION \/ LIMITS|>VOICE</)
  for(const text of ['最小 mm','最大 mm','真机模式不可关闭必要检测','固定 Z=0','阈值须满足','保存六轴限位'])assert.ok(source.includes(text))
})
