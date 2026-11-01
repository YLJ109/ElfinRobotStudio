import {createFixtureCameraSource} from './fixture-camera-source.mjs'
import * as interactionRanges from '../src/interactionRange.js'
// Actual SFC/store code, isolated browser storage and lifecycle; no devices/HTTP.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import * as Pinia from 'pinia'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as assets from '../src/visualAssets.js'
import * as follow from '../src/handFollowInput.js'
import { createMouseFollowInput } from '../src/mouseFollowInput.js'
import { createInteractionInput, interactionModeIssue } from '../src/interactionInput.js'
import { normalizeNavigation, NAV_VIEWS, WORK_TABS, SYSTEM_TABS } from '../src/store.js'
import { targetFor, createTargetState } from '../src/targetState.js'
import { isSixAxis, parseResponse, displayJoints } from '../src/connectionState.js'

const read = path => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
const storage = values => {
  const data = new Map(Object.entries(values || {}))
  return { data, getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key) }
}
const hub = () => { const listeners = new Map(); return {
  addEventListener(key, fn) { if (!listeners.has(key)) listeners.set(key, new Set()); listeners.get(key).add(fn) },
  removeEventListener(key, fn) { listeners.get(key)?.delete(fn) },
} }
async function settle() { for (let i = 0; i < 5; i++) await Vue.nextTick() }
function fixture(t, path, preferences = storage(), opts = {}) {
  const scope = Vue.effectScope(), hooks = { mounted: [], unmounted: [] }, intervals = new Map(), requests = [], events = []
  let interval = 0, frameHandler = null, lastFrame = null
  const w = Vue.reactive({ connected: true, mode: 'sim', view: 'control', controlTab:'run', workTab: 'points', systemTab: 'engineering',
    snap: { session_id:'backend',mode:'sim',joints:{q:[12,23,34,45,56,67],known:true},
      settings:{fence:{x:[-900,900],y:[-900,900],z:[0,1500]}},safety:{level:'green',capsules:[]},
      tcp:{xyz_mm:[0,-400,700],rpy_deg:[0,0,0]}, hand_follow:opts.hand || {},...opts.snap },
    cameras:{active:null}, preview:null, selObj:null, play:{on:false}, tools:{},
    $patch(patch) { Object.assign(this, patch) },
    async boot() {}, disconnectWs() {}, async refreshPoints() {}, async refreshTools() {},
    async call(...args) { requests.push(args); throw new Error('No automatic command allowed') },
  })
  const refs = world => Object.fromEntries(Object.keys(world).map(key => [key, Vue.toRef(world,key)]))
  const window = hub(), document = { ...hub(), hidden:false, createElement:() => ({style:{},remove(){}}) }
  const location = {hash:opts.hash || ''}, history = {replaceState(_a,_b,value){location.hash=value}}
  let view = null, changed = null
  const scene = new Proxy({
    setToolVisual:async value => { events.push(['tool-display',value]) },
    setSkin:async value => { events.push(['skin-display',value]) },
    setHandWorkspace:(state,visible)=>events.push(['hand-workspace',state,visible]),
    setHandBoxPreview:(...args)=>events.push(['hand-preview',...args]),
    setFollowLatency:active=>events.push(['follow-latency',active]),
    setImmediate:q=>events.push(['immediate-joints',q]),setAngles:q=>events.push(['reported-joints',q]),
    setView(value) { view=value;return !!value }, getView:()=>view,
    onViewChange(fn){changed=fn}, fitView(){events.push(['fit'])},
  }, {get:(target,key)=>key==='then'?undefined:key in target?target[key]:()=>{}})
  const tab=opts.tab || storage()
  const deps = {useCameraSource:()=>createFixtureCameraSource(Vue),setTimeout:fn=>setTimeout(fn,0),clearTimeout,...interactionRanges, ...Vue, ...assets, ...follow, createMouseFollowInput, createInteractionInput, interactionModeIssue, useWorld:()=>w, storeToRefs:refs,
    createVisionPolling:()=>()=>{},createVirtualCapture:()=>()=>{},mappedVisionObjects:()=>[],visionWorkspace:{},
    readPreference:key=>preferences.getItem(key),savePreference:(key,value)=>preferences.setItem(key,value),
    readTabState:key=>JSON.parse(tab.getItem(key)||'null'),saveTabState:(key,value)=>tab.setItem(key,JSON.stringify(value)),createWorkspaceUiState:()=>({capture(){},restore(){},dispose(){}}),
    normalizeNavigation,NAV_VIEWS,WORK_TABS,SYSTEM_TABS,isSixAxis,displayJoints,
    setFrameHandler:fn=>{frameHandler=fn}, latestFrame:()=>lastFrame,
    localStorage:preferences, document, window, location, history,
    performance:{now:()=>1000}, requestAnimationFrame:fn=>fn(),
    onMounted:fn=>hooks.mounted.push(fn), onBeforeUnmount:fn=>hooks.unmounted.push(fn),
    onActivated(){},onDeactivated(){},
    setInterval(fn){intervals.set(++interval,fn);return interval},clearInterval:id=>intervals.delete(id),
    defineAsyncComponent:loader=>({loader}),
    createSceneModule:async()=>({createScene:async()=>scene}),
    fetch(){throw new Error('No network')},
    createHandFollowClient:options=>opts.client?.(options) || follow.createHandFollowClient({...options, fetcher:async(...args)=>{
      requests.push(args);throw new Error('No automatic follow command')
    }}),
  }
  for (const name of ['TopBar','NavRail','WorkPanel','StatusStrip','LogDock','ObjectProps','CameraWidget','ModelLibrary','ThemeSelectLayer','ControlPanel','SimPanel','PowerPanel']) deps[name]={}
  const { descriptor } = parse(read(path))
  const script = compileScript(descriptor,{id:path})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:path,id:path,compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  const code=script.content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'')
    .replace("await import('./scene')",'await createSceneModule()').replace('export default','return')
  const props=Vue.reactive({visualModel:'elfin5',direction:0,fence:null,...opts.props})
  const keys=Object.keys(deps).filter(key=>key!=='default' && /^[A-Za-z_$][\w$]*$/.test(key))
  const panel=scope.run(()=>new Function(...keys,code)(...keys.map(key=>deps[key])).setup(props,{expose(){},emit:(...args)=>events.push(args)}))
  let stopped=false
  const h={panel,w,props,requests,events,preferences,scene,location,intervals,
    async mount(){if(panel.canvasBox)panel.canvasBox.value={appendChild(){}};for(const fn of hooks.mounted)await fn();await settle()},
    changeView(value){view=value;changed?.(value)},
    feed(frame){lastFrame=frame;frameHandler?.(frame)},
    stop(){if(stopped)return;stopped=true;hooks.unmounted.forEach(fn=>fn());scope.stop()},
  }
  t.after(()=>h.stop()); return h
}

test('App restores existing route, system tab, sidebar width/open and skin without command',async t=>{
  const pref=storage({'huayan.side.open':'0','huayan.side.width':'477','huayan.visualSkin':'factory'})
  const h=fixture(t,'App.vue',pref,{hash:'#/engineering/logs'})
  assert.equal(h.w.view,'engineering');assert.equal(h.w.systemTab,'logs')
  assert.equal(pref.getItem('huayan.systemTab'),'logs')
  assert.equal(h.panel.sideOpen.value,false);assert.equal(h.panel.sideWidth.value,477)
  assert.equal(h.panel.visualSkin.value,'factory');assert.deepEqual(h.requests,[])
})
test('shared view restores and saves exact zoom/orbit/pan, without fitting or changing backend q',async t=>{
  const view={r:3.2,theta:-.8,phi:1.2,target:[.12,.45,-.31]}
  const h=fixture(t,'App.vue',storage({'huayan.view.workspace.v1':JSON.stringify(view)}))
  const q=[...h.w.snap.joints.q];await h.mount()
  assert.deepEqual(h.scene.getView(),view)
  assert.deepEqual(JSON.parse(h.preferences.getItem('huayan.view.workspace.v1')),view,'legacy custom view is retained during migration')
  const moved={...view,r:2.6,target:[.3,.6,.2]};h.changeView(moved)
  assert.deepEqual(JSON.parse(h.preferences.getItem('huayan.view.workspace.v2')),moved)
  assert.equal(h.events.some(event=>event[0]==='fit'),false)
  assert.deepEqual(h.w.snap.joints.q,q);assert.deepEqual(h.requests,[])
})
for(const tool of [null,{kind:'library',id:'robotiq-2f85',name:'夹爪',rotation:[0,0,45]}]) {
  test(`App refresh restores ${tool?.id || 'explicit no tool'} and mounting angle as display only`,async t=>{
    const h=fixture(t,'App.vue',storage({'huayan.visualTool':JSON.stringify(tool)}))
    assert.deepEqual(h.panel.visualTool,tool)
    await h.mount()
    const displays=h.events.filter(event=>event[0]==='tool-display')
    if(tool)assert.deepEqual(displays[0][1],tool)
    assert.deepEqual(h.requests,[])
    assert.deepEqual(h.w.snap.joints.q,[12,23,34,45,56,67])
  })
}
test('hand page restores its current draft, toggles and expanded settings, not just saved defaults',async t=>{
  const pref=storage(), first=fixture(t,'panels/HandFollowPanel.vue',pref)
  first.panel.rangeCenter.value=[120,-350,620];first.panel.rangeSize.value=[700,240,420]
  first.panel.depthRange.value=[.08,.42];first.panel.depth.value=false;first.panel.rotation.value=true
  first.panel.changeDirection(90)
  assert.ok(first.panel.settingsOpen,'range disclosure must be persisted')
  first.panel.settingsOpen.value=true;first.panel.depthSettingsOpen.value=true
  await settle();const center=[...first.panel.rangeCenter.value];first.stop()
  const next=fixture(t,'panels/HandFollowPanel.vue',pref)
  assert.deepEqual([...next.panel.rangeCenter.value],center)
  assert.deepEqual([...next.panel.rangeSize.value],[700,240,420])
  assert.deepEqual([...next.panel.depthRange.value],[.08,.42])
  assert.equal(next.panel.rangeDirection.value,90);assert.equal(next.panel.depth.value,false)
  assert.equal(next.panel.rotation.value,true);assert.equal(next.panel.settingsOpen.value,true)
  assert.equal(next.panel.depthSettingsOpen.value,true);assert.deepEqual(next.requests,[])
})
test('refresh never adopts a foreign WS token or restores active control',async t=>{
  const h=fixture(t,'panels/HandFollowPanel.vue',storage(),{hand:{active:true,configured:true,token:'foreign',session:'other'}})
  await h.mount()
  assert.equal(h.panel.local.value.active,false);assert.equal(h.panel.local.value.server,null)
  assert.deepEqual(h.requests,[])
  assert.deepEqual(h.w.snap.joints.q,[12,23,34,45,56,67])
  assert.match(h.panel.refreshNotice.value,/未接管|未恢复|不会自动/)
  assert.ok(![...h.preferences.data.values()].some(value=>value.includes('foreign')))
})

test('refresh detaches its own running client without stopping backend, and retains only tab credential',async t=>{
  const tab=storage(), calls=[]
  const h=fixture(t,'panels/HandFollowPanel.vue',storage(),{tab,client:()=>({
    getState:()=>({active:true,configured:true,busy:false,seq:10,server:{active:true,configured:true,token:'owned',session:'camera',seq:9}}),
    stop:()=>calls.push('stop'),detach:()=>calls.push('detach'),dispose:()=>calls.push('dispose')})})
  h.panel.local.value={active:true,busy:false,server:{active:true,configured:true,token:'owned',session:'camera',seq:9}}
  h.panel.pageLeaving()
  assert.deepEqual(calls,['detach'])
  assert.equal(JSON.parse(tab.getItem('huayan.hand-follow.owner.v1')).server.token,'owned')
  assert.equal(h.preferences.getItem('huayan.hand-follow.owner.v1'),null)
  assert.deepEqual(h.requests,[])
})

test('refresh may reattach only a previously owned matching active backend session, without start',async t=>{
  const live={active:true,configured:true,token:'owned',session:'camera',seq:10}
  const saved={active:true,configured:true,backendSession:'backend',server:{...live,seq:9},seq:9}
  const tab=storage({'huayan.hand-follow.owner.v1':JSON.stringify(saved)}),calls=[]
  const h=fixture(t,'panels/HandFollowPanel.vue',storage(),{tab,hand:live,props:{visualModel:'elfin5'},client:options=>({
    resumeOwned:(record,state,backend)=>{calls.push([record,state,backend]);options.onChange({active:true,busy:false,configured:true,server:state});return true},
    stop(){},dispose(){},sample(){}})})
  h.w.mode='sim';h.panel.resumeRefreshOwner()
  assert.equal(calls.length,1);assert.equal(calls[0][2],'backend');assert.equal(h.panel.local.value.active,true)
  assert.deepEqual(h.requests,[])
})

test('no-tool choice roundtrips through the actual selector without changing the old descriptor contract',async t=>{
  const pref=storage(), first=fixture(t,'App.vue',pref)
  await first.mount();first.w.snap.mode='real'
  await first.panel.selectVisualTool(null)
  assert.equal(pref.getItem('huayan.visualTool'),null)
  assert.equal(pref.getItem('huayan.visualTool.none'),'1')
  first.stop()
  const fresh=fixture(t,'App.vue',pref)
  assert.equal(fresh.panel.visualTool,null);await fresh.mount()
  assert.deepEqual(first.requests,[]);assert.deepEqual(fresh.requests,[])
})
test('standalone remembered direction survives even before a draft has been saved',async t=>{
  const pref=storage({'huayan.hand.direction':'270'})
  const h=fixture(t,'panels/HandFollowPanel.vue',pref,{props:{direction:270}})
  await h.mount()
  assert.equal(h.panel.rangeDirection.value,270)
  assert.equal(h.events.find(event=>event[0]==='direction')[1],270)
  assert.deepEqual(h.requests,[])
})

test('fresh interaction defaults to front; explicit saved side remains unchanged and neither sends commands',async t=>{
  const fresh=fixture(t,'panels/HandFollowPanel.vue',storage())
  assert.equal(fresh.panel.rangeDirection.value,270)
  await fresh.panel.resetRange();assert.equal(fresh.panel.rangeDirection.value,270)
  assert.deepEqual([...fresh.panel.rangeCenter.value],[-500,0,700])
  assert.deepEqual(fresh.requests,[])
  const side=fixture(t,'panels/HandFollowPanel.vue',storage({'huayan.hand-follow.range-default.v1':JSON.stringify({center:[100,200,700],size:[900,300,500],direction:0,depthRange:[.06,.3]})}))
  assert.equal(side.panel.rangeDirection.value,0);assert.deepEqual(side.requests,[])
  const app=fixture(t,'App.vue',storage())
  assert.equal(app.panel.handDirection.value,270)
})
test('corrupt draft and unauthorized library preference fall back safely, with no command',async t=>{
  const pref=storage({'huayan.hand-follow.draft.v1':'{"version":1,"center":["bad"]}',
    'huayan.visualTool':'{"kind":"library","id":"../../remote","rotation":[0,0,90]}'})
  const hand=fixture(t,'panels/HandFollowPanel.vue',pref)
  assert.deepEqual([...hand.panel.rangeCenter.value],[-500,0,700])
  const app=fixture(t,'App.vue',pref)
  assert.equal(app.panel.visualTool.kind,'vacuum-reference')
  assert.deepEqual(hand.requests,[]);assert.deepEqual(app.requests,[])
})
test('actual store boot restores navigation while only reading metadata and accepting backend q',async()=>{
  const pref=storage({'huayan.view':'engineering','huayan.systemTab':'settings','huayan.workTab':'program'})
  const calls=[];let transport
  const deps={defineStore:Pinia.defineStore,parseResponse,targetFor,createTargetState,globalThis:{localStorage:pref},
    performance:{now:()=>1000},location:{protocol:'http:',host:'offline.invalid'},
    createStateConnection(options){transport=options;return {connect(){},stop(){}}},
    async fetch(path,options){calls.push([path,options?.method || 'GET']);assert.equal(options?.method || 'GET','GET')
      return {ok:true,status:200,json:async()=>({items:[]})}},
  }
  const code=read('store.js').replace(/^import .*$/gm,'').replace(/^export /gm,'')+'\nreturn useWorld'
  const useWorld=new Function(...Object.keys(deps),code)(...Object.values(deps))
  const w=useWorld(Pinia.createPinia());await w.boot()
  assert.equal(w.view,'engineering');assert.equal(w.systemTab,'settings');assert.equal(w.workTab,'program')
  transport.onOpen()
  const q=[-12,24,63,-18,35,0]
  transport.onMessage({data:JSON.stringify({type:'state',data:{mode:'sim',session_id:'same-backend',
    joints:{q,known:true},safety:{level:'green'},settings:{tts:false},hand_follow:{active:true,token:'foreign'}}})})
  assert.deepEqual([...w.snap.joints.q],q)
  assert.equal(w.systemTab,'settings')
  assert.deepEqual(calls.map(([path])=>path),['/api/meta','/api/points','/api/programs','/api/tools','/api/cameras','/api/scenes'])
  w.disconnectWs()
})

test('App scene subscription selects each mode workspace and world markers without substituting planned joints',async t=>{
  const h=fixture(t,'App.vue',storage(),{hash:'#/hand-follow'})
  await h.mount()
  const sim={configured:true,active:true,bounds:{min:[-400,-550,450],max:[400,-250,950]},
    target:[100,-400,700],actual:[0,-400,700],q:[90,90,90,90,90,90]}
  const real={...sim,direction:90,bounds:{min:[-300,-400,450],max:[500,-100,950]},
    world_bounds:{min:[-150,-300,450],max:[150,500,950]},target:[20,50,650],actual:[15,45,640],q:[80,80,80,80,80,80]}
  const draft={min:[-100,-200,300],max:[100,200,900]}
  h.panel.setInteractionVisible(true)
  h.panel.previewHandBox(draft)
  const frame={...h.w.snap,mode:'real',real:{stream_up:true,lag_ms:20,joints:h.w.snap.joints.q},hand_follow:sim,real_hand_follow:real}
  h.feed(frame)
  const rendered=h.events.filter(e=>e[0]==='hand-workspace').at(-1)
  assert.equal(rendered[1],real);assert.equal(rendered[2],true)
  assert.deepEqual(rendered[1].target,[20,50,650]);assert.deepEqual(rendered[1].actual,[15,45,640])
  assert.deepEqual(rendered[1].bounds,real.bounds);assert.deepEqual(rendered[1].world_bounds,real.world_bounds)
  assert.deepEqual(h.panel.handBoxDraft.value,draft)
  assert.deepEqual(h.events.filter(e=>e[0]==='immediate-joints').at(-1)[1],frame.joints.q)
  assert.equal(h.events.filter(e=>e[0]==='follow-latency').at(-1)[1],false)
  h.feed({...frame,mode:'sim',real:null})
  assert.equal(h.events.filter(e=>e[0]==='hand-workspace').at(-1)[1],sim)
  assert.equal(h.events.filter(e=>e[0]==='hand-workspace').at(-1)[2],true)
  assert.equal(h.events.filter(e=>e[0]==='follow-latency').at(-1)[1],true)
  h.feed({...frame,real_hand_follow:undefined})
  assert.equal(h.events.filter(e=>e[0]==='hand-workspace').at(-1)[1],undefined)
  assert.ok(!h.events.filter(e=>e[0]==='hand-workspace').at(-1)[2])
  h.panel.visualModel.value='elfin3';h.feed(frame)
  assert.equal(h.events.filter(e=>e[0]==='hand-workspace').at(-1)[2],false)
  h.w.view='work';await settle()
  assert.deepEqual(h.panel.handBoxDraft.value,draft,'隐藏交互框不清空用户范围草稿')
  assert.equal(h.events.filter(e=>e[0]==='hand-workspace').at(-1)[2],false)
  assert.deepEqual(h.requests,[])
})

test('Elfin 5 is the only model: no switch entry and stale preferences fall back to it',async t=>{
  const pref=storage({'huayan.visualModel':'elfin10'})
  const h=fixture(t,'App.vue',pref)
  await h.mount()
  assert.equal(h.panel.visualModel.value,'elfin5')
  assert.equal(h.panel.selectVisualModel,undefined)
  assert.equal(pref.getItem('huayan.visualModel'),'elfin5')
  assert.deepEqual(h.requests,[])
})
test('App tool guards follow the current mode, including stale active data from the other mode',async t=>{
  const h=fixture(t,'App.vue',storage(),{snap:{mode:'real',real:{stream_up:true},
    hand_follow:{active:false},real_hand_follow:{active:true}}})
  await h.mount();const before=h.events.length
  assert.equal(h.panel.visualModel.value,'elfin5')
  assert.equal(h.panel.handFollow.value,h.w.snap.real_hand_follow)
  await h.panel.selectVisualTool(null)
  assert.match(h.w.error,/停止跟随.*工具/)
  assert.equal(h.events.length,before)
  h.w.snap.hand_follow.active=true;h.w.snap.real_hand_follow.active=false
  assert.equal(h.panel.handFollow.value.active,false)
  await h.panel.selectVisualTool(null)
  assert.equal(h.events.at(-1)[0],'tool-display')
  h.w.snap.mode='sim'
  assert.equal(h.panel.handFollow.value,h.w.snap.hand_follow)
  h.w.snap.hand_follow.active=false;h.w.snap.real_hand_follow.active=true
  assert.equal(h.panel.handFollow.value.active,false)
  assert.match(read('App.vue'),/:tool-locked="!!handFollow\?\.active"/)
  assert.deepEqual(h.requests,[])
})
