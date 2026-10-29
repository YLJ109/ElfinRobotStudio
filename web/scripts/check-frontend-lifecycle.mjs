// 离线行为回归：真实组件逻辑，替换时钟、网络和绘图，不打开设备或服务。
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import * as Vue from 'vue'
import { createPinia, setActivePinia, defineStore } from 'pinia'
import { parse } from '@vue/compiler-sfc'
import { parseResponse } from '../src/connectionState.js'
import { targetFor, createTargetState } from '../src/targetState.js'
import { createWorkspaceUiState, saveTabState } from '../src/workspaceState.js'

const read = path => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
const strip = source => source.replace(/^import .*\r?\n/gm, '')
const response = body => ({ ok: true, status: 200, json: async () => body })
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a;reject=b }); return {promise,resolve,reject} }
const settle = async () => { for (let i=0;i<12;i++) await Promise.resolve();await Vue.nextTick() }
function store(fetcher) {
  setActivePinia(createPinia())
  const context = vm.createContext({ defineStore, parseResponse, targetFor, createTargetState, fetch:fetcher })
  vm.runInContext(strip(read('store.js')).replace(/^export /gm,'') + '\nglobalThis.world=useWorld()',context)
  const w=context.world
  let connected=0
  w.connectWs=()=>{connected++}
  return {w,connections:()=>connected}
}
function component(t,path,props,extra={}) {
  const scope=Vue.effectScope(), mounted=[],unmounted=[],frames=new Map(),timers=new Map(),observers=[]
  let sequence=0
  class ResizeObserver {
    constructor(callback){this.callback=callback;this.disconnected=false;observers.push(this)}
    observe(){} disconnect(){this.disconnected=true}
  }
  const deps={...Vue,defineProps:()=>props,
    onMounted:fn=>mounted.push(fn),onBeforeUnmount:fn=>unmounted.push(fn),
    requestAnimationFrame:fn=>{frames.set(++sequence,fn);return sequence},cancelAnimationFrame:id=>frames.delete(id),
    setTimeout:fn=>{timers.set(++sequence,fn);return sequence},clearTimeout:id=>timers.delete(id),
    ResizeObserver,window:{devicePixelRatio:1},devicePixelRatio:1,...extra}
  const keys=Object.keys(deps).filter(k=>k!=='default' && /^[A-Za-z_$][\w$]*$/.test(k))
  const code=strip(parse(read(path)).descriptor.scriptSetup.content)
  const names=path.includes('Spark')?'cv,draw,legend,span':'host,count,message'
  const panel=scope.run(()=>new Function(...keys,code+`\nreturn {${names}}`)(...keys.map(k=>deps[k])))
  let disposed=false
  const h={panel,props,frames,timers,observers,mount:()=>mounted.forEach(fn=>fn()),
    flushFrames(){const jobs=[...frames.values()];frames.clear();jobs.forEach(fn=>fn())},
    dispose(){if(disposed)return;disposed=true;unmounted.forEach(fn=>fn());scope.stop()}}
  t.after(()=>h.dispose());return h
}

test('boot invalidation prevents stale writes and the remaining startup requests',async()=>{
  const pending=deferred(),requests=[]
  const h=store(path=>{requests.push(path);return pending.promise})
  const boot=h.w.boot();h.w.disconnectWs()
  pending.resolve(response({marker:'obsolete'}));await boot
  assert.equal(h.w.meta,null)
  assert.deepEqual(requests,['/api/meta'])
  assert.equal(h.connections(),0)
})
test('overlapping boot cannot replace newer metadata or report an obsolete failure',async()=>{
  const old=deferred();let metaRequests=0
  const h=store(path=>path==='/api/meta' && ++metaRequests===1 ? old.promise : Promise.resolve(response({marker:'new',items:[]})))
  const first=h.w.boot();await h.w.boot()
  old.resolve(response({marker:'old'}));await first
  assert.equal(h.w.meta.marker,'new');assert.equal(h.connections(),1)
  const failed=deferred(),x=store(()=>failed.promise)
  const request=x.w.boot();x.w.disconnectWs();failed.reject(new Error('obsolete failure'));await request
  assert.equal(x.w.error,'')
})

test('Spark draws initial data, reacts to size/height, and releases scheduled work',async t=>{
  const props=Vue.reactive({series:[{data:[1,2],name:'J1',color:'cyan'}],height:62,ref_lines:[]})
  let clears=0
  const context=new Proxy({clearRect(){clears++}}, {get:(obj,k)=>obj[k] || (()=>{})})
  const h=component(t,'components/Spark.vue',props)
  h.panel.cv.value={clientWidth:200,width:0,height:0,getContext:()=>context}
  h.mount();h.flushFrames();assert.equal(clears,1,'首次挂载必须绘制已有曲线')
  h.panel.cv.value.clientWidth=400
  h.observers[0].callback();h.observers[0].callback()
  assert.equal(h.frames.size,1,'连续尺寸变化只安排一次重绘')
  h.flushFrames();assert.equal(h.panel.cv.value.width,400)
  props.height=90;await Vue.nextTick();h.flushFrames();assert.equal(h.panel.cv.value.height,90)
  h.observers[0].callback();h.dispose();assert.equal(h.frames.size,0);assert.ok(h.observers[0].disconnected)
})

function spark(t, series, ref_lines=[]) {
  const props=Vue.reactive({series,ref_lines,height:62}),calls=[]
  const context=new Proxy({}, {get:(_obj,method)=>(...args)=>{
    if (['moveTo','lineTo'].includes(method)) assert.ok(args.every(Number.isFinite),'绘图坐标必须有限')
    calls.push([method,...args])
  }})
  const h=component(t,'components/Spark.vue',props)
  h.panel.cv.value={clientWidth:200,width:0,height:0,getContext:()=>context}
  return {...h,calls,draw(){h.panel.draw()}}
}
for (const [name,value] of [['NaN',NaN],['Infinity',Infinity],['-Infinity',-Infinity],['numeric string','2'],
  ['string','bad'],['boolean',true],['object',{}],['array',[]],['null',null],['undefined',undefined],['bigint',2n]]) {
  test(`Spark skips ${name} without coercion and preserves gaps, scale and last valid value`,t=>{
    const h=spark(t,[{name:'J1',data:[1,value,3,4,value]}],[{v:value}])
    assert.equal(h.panel.legend.value[0].last,'4.0')
    assert.deepEqual(h.panel.span.value,{lo:1,hi:4})
    h.draw()
    const moves=h.calls.filter(c=>c[0]==='moveTo')
    assert.equal(moves.length,3,'底线及两个不相连的有效采样段')
    assert.equal(moves[1][1],0);assert.equal(moves[2][1],100,'无效采样保留时间索引，不压缩曲线')
    assert.equal(h.calls.filter(c=>c[0]==='setLineDash').length,0,'无效参考线不能绘制')
  })
}
test('Spark all-invalid data shows empty state and recovers when valid samples arrive',async t=>{
  const h=spark(t,[{name:'J1',data:[NaN,Infinity,'5',{},true]}],[{v:NaN},{v:'6'},null])
  assert.equal(h.panel.legend.value[0].last,'--');assert.equal(h.panel.span.value,null)
  h.draw();assert.ok(h.calls.some(c=>c[0]==='fillText' && c[1]==='暂无数据'))
  h.props.series=[{name:'J1',data:[0,2]}];h.props.ref_lines=[{v:5}]
  await Vue.nextTick();h.calls.length=0;h.flushFrames()
  assert.equal(h.panel.legend.value[0].last,'2.0');assert.deepEqual(h.panel.span.value,{lo:0,hi:5})
  assert.ok(h.calls.some(c=>c[0]==='fillText' && c[1]==='5.0'))
})
test('Spark malformed rows and data containers do not throw during legend or drawing',t=>{
  const h=spark(t,[null,undefined,'bad',[],{}, {name:'J1',data:'123'}, {name:'J2',data:{}}],
    [null,undefined,'bad',{},[],{v:Infinity},{v:'2'},{v:0}])
  assert.deepEqual(h.panel.legend.value.map(l=>l.last),['--','--','--'])
  assert.doesNotThrow(()=>h.draw())
  assert.equal(h.calls.filter(c=>c[0]==='setLineDash').length,2,'只绘制有限数值参考线')
})
test('Spark null/non-array top-level inputs render an empty state',t=>{
  for (const value of [null,undefined,{},'bad']) {
    const h=spark(t,value,value)
    assert.deepEqual(h.panel.legend.value,[]);assert.equal(h.panel.span.value,null)
    assert.doesNotThrow(()=>h.draw())
  }
})

function cloud(t) {
  const props=Vue.reactive({session:'old'}),requests=[],geometries=[],renderers=[],controls=[]
  const vector=()=>({set(){},copy(){return this},add(){return this}})
  class Geometry {
    constructor(){this.attributes={};geometries.push(this)}
    setAttribute(k,v){this.attributes[k]=v}
    dispose(){this.disposed=true}
    computeBoundingSphere(){this.boundingSphere={center:{},radius:1}}
  }
  const THREE={BufferGeometry:Geometry,Float32BufferAttribute:class {constructor(array){this.array=array}},
    Scene:class {add(){}},Color:class {},Vector3:class {},
    PerspectiveCamera:class {up=vector();position=vector();updateProjectionMatrix(){}},
    WebGLRenderer:class {constructor(){renderers.push(this)} setPixelRatio(){}setSize(){}render(){}dispose(){this.disposed=true}forceContextLoss(){this.lost=true}},
    PointsMaterial:class {dispose(){this.disposed=true}},Points:class {constructor(g,m){this.geometry=g;this.material=m;this.visible=true}}}
  const h=component(t,'components/CameraCloud.vue',props,{THREE,AbortController,
    OrbitControls:class {constructor(){controls.push(this);this.target=vector()}update(){}dispose(){this.disposed=true}},
    fetch(url,options){const pending=deferred();requests.push({url,options,...pending});return pending.promise}})
  h.panel.host.value={clientWidth:300,appendChild(){}}
  h.mount();return {...h,requests,geometries,renderers,controls}
}
const points=n=>({ok:true,xyz_m:Array.from({length:n},()=>[1,2,3])})
test('CameraCloud session switch cancels old request and ignores its late response',async t=>{
  const h=cloud(t);assert.equal(h.requests.length,1)
  h.props.session='new';await Vue.nextTick()
  assert.equal(h.requests[0].options.signal.aborted,true)
  assert.equal(h.requests.length,2)
  h.requests[1].resolve(response(points(2)));await settle();assert.equal(h.panel.count.value,2)
  h.requests[0].resolve(response(points(5)));await settle()
  assert.equal(h.panel.count.value,2);assert.equal(h.timers.size,1)
  h.dispose();assert.equal(h.timers.size,0);assert.equal(h.frames.size,0)
  assert.ok(h.geometries.every(g=>g.disposed));assert.ok(h.renderers[0].lost);assert.ok(h.controls[0].disposed)
})
test('CameraCloud clears old geometry immediately when the session changes',async t=>{
  const h=cloud(t);h.requests[0].resolve(response(points(3)));await settle()
  assert.equal(h.panel.count.value,3)
  h.props.session='new';await Vue.nextTick()
  assert.equal(h.panel.count.value,0)
  assert.ok(h.geometries.at(-2).disposed)
  assert.equal(h.timers.size,0)
  h.dispose();h.requests.at(-1).resolve(response(points(7)));await settle()
  assert.equal(h.panel.count.value,0);assert.equal(h.timers.size,0)
})
test('CameraCloud late error cannot erase newer data or create an extra retry',async t=>{
  const h=cloud(t);h.props.session='new';await Vue.nextTick()
  h.requests[1].resolve(response(points(2)));await settle()
  h.requests[0].reject(new Error('old session failed'));await settle()
  assert.equal(h.panel.count.value,2);assert.equal(h.panel.message.value,'');assert.equal(h.timers.size,1)
  h.props.session='';await Vue.nextTick()
  assert.equal(h.requests.length,2);assert.equal(h.timers.size,0);assert.equal(h.panel.count.value,0)
})
test('CameraCloud current HTTP error retries once and disposal prevents a pending retry',async t=>{
  const h=cloud(t)
  h.requests[0].resolve({ok:false,status:503,json(){throw new Error('不应解析错误页')}});await settle()
  assert.match(h.panel.message.value,/HTTP 503/);assert.equal(h.timers.size,1)
  const retry=[...h.timers.values()][0]
  h.dispose();retry();await settle()
  assert.equal(h.requests.length,1);assert.equal(h.frames.size,0);assert.equal(h.timers.size,0)
})

function denyStorage(t,value) {
  const original=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage')
  Object.defineProperty(globalThis,'sessionStorage',{configurable:true,...value})
  t.after(()=>original?Object.defineProperty(globalThis,'sessionStorage',original):delete globalThis.sessionStorage)
}
test('missing storage reports unsuccessful tab-state save',t=>{
  denyStorage(t,{value:undefined});assert.equal(saveTabState('test',{}),false)
})
test('blocked storage getter does not prevent workspace mounting and cleanup',t=>{
  denyStorage(t,{get(){throw new Error('SecurityError')}})
  const listeners=new Set(),root={querySelectorAll:()=>[],addEventListener:k=>listeners.add(k),removeEventListener:k=>listeners.delete(k)}
  const ui=createWorkspaceUiState(root,()=>'/sim',{Observer:null,raf:()=>1,cancel(){}})
  ui.capture();ui.restore();ui.dispose();assert.equal(listeners.size,0)
})
test('tab save returns false on quota failure and true only after an actual write',t=>{
  let fail=true,stored=null
  denyStorage(t,{value:{setItem(key,value){if(fail)throw new Error('QuotaExceededError');stored=[key,value]}}})
  assert.equal(saveTabState('test',{x:1}),false)
  fail=false;assert.equal(saveTabState('test',{x:1}),true);assert.deepEqual(stored,['test','{"x":1}'])
})
