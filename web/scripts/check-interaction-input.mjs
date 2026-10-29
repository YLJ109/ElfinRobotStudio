// Offline: no camera, controller, gamepad hardware or live HTTP.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as follow from '../src/handFollowInput.js'
import { createInteractionInput, gamepadAxis, editableTarget, interactionModeIssue, INTERACTION_ENDPOINT } from '../src/interactionInput.js'
import { createMouseFollowInput } from '../src/mouseFollowInput.js'
import { createHandFollowClient, handFollowFrameIssue, publishHandFollowInput, getHandFollowInput } from '../src/handFollowInput.js'

function fixture(t) {
  const win = new EventTarget(), doc = new EventTarget(), frames = new Map()
  doc.hidden = false; doc.activeElement = null
  let index = 0, state, connected = [], enabled = true
  const controller = createInteractionInput({session:'mouse-offline-test',win,doc,
    enabled:() => enabled, pads:() => connected, onChange:value => {state=value},
    raf:fn => {frames.set(++index,fn);return index},cancel:id => frames.delete(id)})
  t.after(() => controller.dispose())
  function key(code,type='keydown',patch={}) {
    const event = new Event(type,{cancelable:true})
    Object.defineProperty(event,'code',{value:code})
    for (const [k,v] of Object.entries(patch)) Object.defineProperty(event,k,{value:v})
    win.dispatchEvent(event);return event
  }
  return {controller,win,doc,frames,key,state:() => state,pads:value => {connected=value},enable:value => {enabled=value}}
}
test('editable input domains and modifiers never capture motion keys', t => {
  const h=fixture(t);h.controller.select('keyboard')
  for (const tag of ['input','textarea','select','contenteditable']) {
    const target={closest:selector => selector.includes(tag) ? {} : null}
    assert.equal(editableTarget(target),true)
    assert.equal(h.key('KeyW','keydown',{target}).defaultPrevented,false)
  }
  assert.equal(editableTarget({isContentEditable:true}),true)
  for (const modifier of ['ctrlKey','metaKey','altKey','isComposing']) assert.equal(h.key('KeyA','keydown',{[modifier]:true}).defaultPrevented,false)
  h.controller.step(0);h.controller.step(50);assert.equal(h.state().present,false)
})
test('held keys integrate dt, ignore repeat, release and space return to center', t => {
  const h=fixture(t);h.controller.select('keyboard');h.key('KeyD');h.key('KeyE')
  h.controller.step(0);h.controller.step(20)
  assert.ok(Math.abs(h.state().x-.509)<1e-10)
  h.key('KeyD','keydown',{repeat:true});h.controller.step(40)
  assert.ok(Math.abs(h.state().x-.518)<1e-10)
  assert.ok(Math.abs(h.state().depth-.518)<1e-10)
  h.key('KeyD','keyup');h.key('KeyE','keyup');assert.equal(h.state().x,.5)
  h.key('ArrowUp');h.controller.step(100);h.controller.step(150);assert.ok(h.state().y<.5)
  h.key('Space');assert.equal(h.controller.frame(151),null)
  h.key('ArrowUp','keydown',{repeat:true});h.controller.step(200);assert.equal(h.state().present,false)
})
test('equal elapsed time yields equal positions; long frame gaps are capped', t => {
  const a=fixture(t),b=fixture(t)
  for (const h of [a,b]) {h.controller.select('keyboard');h.key('KeyA');h.controller.step(0)}
  for (let n=10;n<=100;n+=10) a.controller.step(n)
  b.controller.step(50);b.controller.step(100)
  assert.ok(Math.abs(a.state().x-b.state().x)<1e-10)
  const before=b.state().x;b.controller.step(10000);assert.ok(before-b.state().x<=.022500001)
})
test('blur, hidden, focus in editor and disabled input clear held keys', t => {
  const h=fixture(t);h.controller.select('keyboard')
  for (const reset of [() => h.win.dispatchEvent(new Event('blur')),
    () => {h.doc.hidden=true;h.doc.dispatchEvent(new Event('visibilitychange'))},
    () => {h.doc.activeElement={isContentEditable:true};h.controller.step(80)},
    () => {h.enable(false);h.controller.step(80)}]) {
    h.key('KeyD');h.controller.step(0);h.controller.step(50);reset()
    assert.equal(h.state().present,false);assert.equal(h.state().x,.5)
    h.doc.hidden=false;h.doc.activeElement=null;h.enable(true)
    h.key('KeyD','keydown',{repeat:true});h.controller.step(100);assert.equal(h.state().present,false)
  }
})
test('gamepad deadzone, actual identity, neutral rearm and disconnect', t => {
  assert.equal(gamepadAxis(.18),0);assert.equal(gamepadAxis(NaN),0)
  assert.equal(gamepadAxis(-1),-1);assert.equal(gamepadAxis(1),1)
  assert.ok(gamepadAxis(.6)>0 && gamepadAxis(.6)<1)
  const h=fixture(t),pad={id:'Actual USB Controller',index:2,connected:true,mapping:'standard',axes:[1,0,0,0]}
  h.pads([null,null,pad]);h.controller.select('gamepad');h.controller.step(0)
  assert.deepEqual(h.state().gamepad,{id:pad.id,index:2,mapping:'standard'})
  assert.equal(h.state().present,false)
  pad.axes[0]=0;h.controller.step(20);pad.axes[0]=1;h.controller.step(40)
  assert.ok(h.state().x>.5)
  h.win.dispatchEvent(new Event('blur'));h.controller.step(60);assert.equal(h.state().present,false)
  pad.axes[0]=0;h.controller.step(80);pad.axes[0]=1;h.controller.step(100);assert.equal(h.state().present,true)
  h.pads([]);h.controller.step(120);assert.equal(h.state().gamepad,null);assert.equal(h.state().x,.5)
  h.pads([pad]);h.controller.step(140);assert.equal(h.state().present,false)
})
test('only selected supported simulation modes schedule; disposal cancels listeners and frames', t => {
  const h=fixture(t)
  assert.equal(h.frames.size,0)
  for (const mode of ['camera','mouse']) {assert.equal(h.controller.select(mode),false);assert.equal(h.frames.size,0)}
  h.controller.select('keyboard');assert.equal(h.frames.size,1)
  h.controller.select('gamepad');assert.equal(h.frames.size,1)
  h.controller.dispose();assert.equal(h.frames.size,0)
  h.key('KeyD');assert.equal(h.state().present,false)
  assert.equal(h.controller.select('keyboard','real'),false);assert.equal(h.frames.size,0)
  assert.ok(interactionModeIssue('real','mouse'));assert.equal(interactionModeIssue('real','camera'),'')
})
test('synthetic frames cannot enter camera bus and real rejects without sample HTTP', async t => {
  const mouse=createMouseFollowInput('mouse-offline-test'),frame=mouse.frame(1000)
  assert.ok(handFollowFrameIssue(frame));const previous=getHandFollowInput()
  publishHandFollowInput(frame,Symbol());assert.equal(getHandFollowInput(),previous)
  const calls=[]
  const client=createHandFollowClient({endpoint:'/api/real/hand-follow',now:()=>1000,fetcher:async (url,options) => {
    calls.push(url);const action=url.split('/').at(-1)
    return {ok:true,json:async()=>({ok:true,state:{token:'own-token',session:frame.session,configured:true,active:action==='start'}})}
  }})
  t.after(()=>client.dispose())
  assert.equal(await client.configure({session:frame.session}),true)
  assert.equal(await client.start(frame.session,{confirm_real:true}),true)
  await client.sample(frame)
  assert.equal(client.getState().active,false)
  assert.equal(calls.some(url=>url.endsWith('/sample')),false)
})
test('synthetic traffic uses only independent endpoint; release waits without stopping session', async t => {
  let now=1000;const calls=[],mouse=createMouseFollowInput('mouse-offline-test')
  const client=createHandFollowClient({endpoint:INTERACTION_ENDPOINT,now:()=>now,fetcher:async (url,options) => {
    const body=JSON.parse(options.body),action=url.split('/').at(-1);calls.push({url,body,action})
    return {ok:true,json:async()=>({ok:true,state:{token:'own-token',session:'mouse-offline-test',configured:true,active:action!=='configure'}})}
  }})
  t.after(()=>client.dispose())
  await client.configure({session:'mouse-offline-test'});await client.start('mouse-offline-test')
  await client.sample(mouse.frame(now));now+=40;await client.sample(null)
  assert.equal(client.getState().active,true);assert.equal(client.getState().waiting,true)
  assert.equal(calls.at(-1).body.hand_present,false)
  assert.equal(calls.filter(c=>c.action==='stop').length,0)
  assert.ok(calls.every(c=>c.url.startsWith(INTERACTION_ENDPOINT+'/')))
})
test('panel compiles tabs, mode guards and lifecycle integration', () => {
  const source=readFileSync(new URL('../src/panels/HandFollowPanel.vue',import.meta.url),'utf8')
  const {descriptor}=parse(source)
  const script=compileScript(descriptor,{id:'interaction-panel'})
  const template=compileTemplate({source:descriptor.template.content,filename:'HandFollowPanel.vue',id:'interaction-panel',compilerOptions:{bindingMetadata:script.bindings}})
  assert.deepEqual(template.errors,[])
  assert.match(source,/role="tablist"/);assert.match(source,/onDeactivated\([^\n]*interaction.dispose/)
  assert.match(source,/saved\.endpoint/);assert.match(source,/interactionModeIssue\(w.mode,inputMode.value\)/)
})

test('real panel setup switches tabs, keeps release active, retires old endpoint and cleans up', async t => {
  const source=readFileSync(new URL('../src/panels/HandFollowPanel.vue',import.meta.url),'utf8')
  const script=compileScript(parse(source).descriptor,{id:'runtime'}).content
    .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
  const win=new EventTarget(),doc=new EventTarget(),frames=new Map(),intervals=new Set(),hooks={},calls=[]
  doc.hidden=false
  const world=Vue.reactive({mode:'sim',connected:true,snap:{session_id:'offline',hand_follow:null},cameras:{active:null}})
  let seq=0
  const tab=new Map(),storage=new Map()
  const deps={computed:Vue.computed,ref:Vue.ref,watch:Vue.watch,...follow,createMouseFollowInput,interactionModeIssue,
    createInteractionInput:options=>createInteractionInput({...options,win,doc,pads:()=>[],
      raf:fn=>{frames.set(++seq,fn);return seq},cancel:id=>frames.delete(id)}),
    useWorld:()=>world,window:win,document:doc,
    readTabState:key=>tab.get(key),saveTabState:(key,value)=>tab.set(key,value),
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
    setInterval:fn=>{intervals.add(fn);return fn},clearInterval:fn=>intervals.delete(fn),
    onMounted:fn=>{hooks.mount=fn},onActivated:fn=>{hooks.activate=fn},
    onDeactivated:fn=>{hooks.deactivate=fn},onBeforeUnmount:fn=>{hooks.unmount=fn},
    createHandFollowClient:options=>follow.createHandFollowClient({...options,fetcher:async(url,request)=>{
      const body=JSON.parse(request.body),action=url.split('/').at(-1);calls.push({url,body,action})
      return {ok:true,json:async()=>({ok:true,state:{session:body.session,token:'owned',configured:true,active:action!=='configure'}})}
    }})}
  const component=new Function(...Object.keys(deps),script)(...Object.values(deps)),scope=Vue.effectScope()
  const panel=scope.run(()=>component.setup({visualModel:'elfin5',direction:0,fence:null},{expose(){},emit(){}}))
  t.after(()=>{hooks.unmount();scope.stop()})
  hooks.mount();assert.equal(calls.length,0)
  panel.inputMode.value='keyboard';assert.equal(frames.size,1);assert.equal(calls.length,0)
  await panel.start();assert.equal(panel.local.value.active,true)
  panel.interaction.reset();assert.equal(panel.local.value.active,true)
  hooks.deactivate();assert.equal(frames.size,0);assert.equal(panel.local.value.active,true)
  hooks.activate();assert.equal(frames.size,1)
  panel.inputMode.value='mouse'
  assert.equal(panel.local.value.active,false);assert.equal(frames.size,0)
  assert.ok(calls.some(c=>c.action==='stop' && c.url===INTERACTION_ENDPOINT+'/stop'))
  assert.ok(calls.every(c=>c.url.startsWith(INTERACTION_ENDPOINT+'/')))
  world.mode='real';assert.ok(panel.startBlocked.value)
  const count=calls.length;await panel.start();assert.equal(calls.length,count)
})

test('synthetic sampling has one in flight and keeps only the latest waiting position', async t => {
  let now=1000,resolvePending,block=true
  const calls=[],mouse=createMouseFollowInput('mouse-offline-test')
  const reply=()=>({ok:true,json:async()=>({ok:true,state:{session:'mouse-offline-test',token:'own',configured:true,active:true}})})
  const client=createHandFollowClient({endpoint:INTERACTION_ENDPOINT,now:()=>now,fetcher:async(url,request)=>{
    const action=url.split('/').at(-1),body=JSON.parse(request.body);calls.push({action,body})
    if (action==='sample' && block) {block=false;return await new Promise(resolve=>{resolvePending=resolve})}
    return reply()
  }})
  t.after(()=>client.dispose())
  await client.configure({session:'mouse-offline-test'});await client.start('mouse-offline-test')
  const first=client.sample(mouse.frame(now))
  now+=40;mouse.nudge(.1,0);await client.sample(mouse.frame(now))
  now+=40;mouse.nudge(.1,0);await client.sample(mouse.frame(now))
  assert.equal(calls.filter(c=>c.action==='sample').length,1)
  resolvePending(reply());await first
  await new Promise(resolve=>setTimeout(resolve,10))
  const samples=calls.filter(c=>c.action==='sample')
  assert.equal(samples.length,2);assert.ok(Math.abs(samples[1].body.u-.7)<1e-10)
})
