// Offline adapters only. No HTTP, hardware, service startup or build.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { createInteractionTarget, gamepadAxis } from '../src/interactionCore.js'
import { createMouseFollowInput } from '../src/mouseFollowInput.js'
import { createInteractionInput, interactionModeIssue } from '../src/interactionInput.js'
import { createHandFollowClient, handFollowFrameIssue, publishHandFollowInput, getHandFollowInput } from '../src/handFollowInput.js'

function fixture(t, input = 'keyboard') {
  class Events extends EventTarget {
    listeners = new Map()
    addEventListener(type, fn) { super.addEventListener(type, fn); this.listeners.set(fn, type) }
    removeEventListener(type, fn) { super.removeEventListener(type, fn); this.listeners.delete(fn) }
  }
  const win = new Events(), doc = new Events(), frames = new Map()
  let id = 0, state, pads = [], enabled = true, focused = true
  doc.hidden = false; doc.activeElement = null; doc.hasFocus = () => focused
  const adapter = createInteractionInput({session:'mouse-offline-core', win, doc,
    pads:() => pads, enabled:() => enabled, onChange:s => {state = s},
    raf:fn => { frames.set(++id, fn); return id }, cancel:id => frames.delete(id)})
  t.after(() => adapter.dispose())
  adapter.select(input)
  function emit(target, type, props = {}) {
    const event = new Event(type, {cancelable:true})
    for (const [key, value] of Object.entries(props)) Object.defineProperty(event, key, {value})
    target.dispatchEvent(event)
    return event
  }
  return {adapter, win, doc, frames, state:() => state,
    key:(code, type = 'keydown', props = {}) => emit(win, type, {code, ...props}),
    emit, pads:value => {pads = value}, enable:value => {enabled = value}, focus:value => {focused = value}}
}
const near = (a,b) => assert.ok(Math.abs(a-b) < 1e-12, `${a} != ${b}`)
const center = h => {assert.equal(h.state().present,false); assert.equal(h.adapter.frame(200),null); assert.equal(h.state().x,.5); assert.equal(h.state().depth,.5)}

test('mouse, keyboard and gamepad produce identical normalized targets and compatibility frames', t => {
  const keyboard = fixture(t), gamepad = fixture(t, 'gamepad')
  const pad = {id:'offline', index:0, connected:true, mapping:'standard', axes:[0,0,0,0]}
  gamepad.pads([pad]); gamepad.adapter.step(0) // Neutral before arming.
  keyboard.key('KeyD'); keyboard.key('KeyW'); keyboard.key('KeyE'); keyboard.adapter.step(0)
  pad.axes = [1,-1,0,-1]
  keyboard.adapter.step(50); gamepad.adapter.step(50)
  const mouse = createMouseFollowInput('mouse-offline-core')
  mouse.move(52.25,47.75,{left:0,top:0,width:100,height:100}); mouse.depth(.5225)
  const expected = mouse.target()
  const expectedFrame = mouse.frame(50)
  for (const adapter of [keyboard.adapter, gamepad.adapter]) {
    const frame = adapter.frame(50), point = frame.point
    near(point.x,expected.u); near(point.y,expected.v); near(point.hand_open,expected.depth)
    assert.deepEqual(frame,expectedFrame)
  }
})

test('normalization rejects invalid deltas, clamps ranges and validates deadzones', () => {
  const target = createInteractionTarget()
  for (const velocity of [[NaN,1,1], [1,Infinity,1], [1,1], null]) assert.equal(target.advance(velocity,.02),false)
  assert.equal(target.nudge(NaN,0),false); assert.deepEqual(target.read(),{u:.5,v:.5,depth:.5})
  target.advance([1,-1,1],100); assert.deepEqual(target.read(),{u:.5225,v:.4775,depth:.5225})
  target.nudge(10,-10,10); assert.deepEqual(target.read(),{u:1,v:0,depth:1})
  target.depth(NaN); assert.equal(target.read().depth,1)
  assert.equal(target.move(1,1,{left:0,top:0,width:0,height:1}),false)
  for (const deadzone of [-1,1,Infinity,NaN]) assert.equal(gamepadAxis(1,deadzone),0)
  for (const value of [-.18,0,.18,NaN]) assert.equal(gamepadAxis(value),0)
  near(gamepadAxis(.59),.5); near(gamepadAxis(-.59),-.5)
  assert.equal(gamepadAxis(2),1)
})

test('opposing keys, last release and neutral gamepad never leave an active target', t => {
  const h = fixture(t)
  h.key('KeyD'); h.adapter.step(0); h.adapter.step(50); assert.equal(h.state().present,true)
  h.key('KeyA'); h.adapter.step(60); center(h)
  h.key('KeyA','keyup'); h.adapter.step(70); assert.equal(h.state().present,true)
  h.key('KeyD','keyup'); center(h)
  const g = fixture(t,'gamepad'), pad = {id:'offline',index:1,connected:true,axes:[0,0,0,0]}
  g.pads([pad]); g.adapter.step(0); pad.axes[3] = -1; g.adapter.step(50)
  assert.equal(g.state().present,true)
  pad.axes[3] = .1; g.adapter.step(60); center(g)
})

test('editor focus releases immediately and repeats cannot resurrect held keys', t => {
  const h = fixture(t)
  h.key('KeyD'); h.adapter.step(0); h.adapter.step(50)
  h.emit(h.doc,'focusin',{target:{isContentEditable:true}}); center(h)
  h.key('KeyD','keydown',{repeat:true}); h.adapter.step(60); center(h)
  for (const target of [{isContentEditable:true},{closest:() => ({})}]) {
    assert.equal(h.key('KeyD','keydown',{target}).defaultPrevented,false)
  }
  for (const modifier of ['isComposing','ctrlKey','metaKey','altKey']) {
    assert.equal(h.key('KeyD','keydown',{[modifier]:true}).defaultPrevented,false)
  }
})

test('blur, hidden, disabled and disconnected gamepad require neutral before resuming', t => {
  const h = fixture(t,'gamepad'), pad = {id:'offline',index:0,connected:true,axes:[0,0,0,0]}
  h.pads([pad]); h.adapter.step(0)
  let time = 0
  for (const release of [
    () => {h.focus(false); h.emit(h.win,'blur')},
    () => {h.doc.hidden=true; h.emit(h.doc,'visibilitychange')},
    () => h.enable(false),
    () => h.emit(h.win,'gamepaddisconnected',{gamepad:pad}),
    () => h.pads([]),
  ]) {
    pad.axes[0]=0; h.adapter.step(time+=10); pad.axes[0]=1; h.adapter.step(time+=10)
    assert.equal(h.state().present,true)
    release(); h.adapter.step(time+=10); center(h)
    h.focus(true); h.doc.hidden=false; h.enable(true); h.pads([pad])
    h.adapter.step(time+=10); center(h)
  }
})

test('selection and disposal clean all listeners and scheduled frames', t => {
  const h = fixture(t)
  for (const input of ['gamepad','keyboard','gamepad']) {
    h.adapter.select(input); assert.equal(h.frames.size,1)
    assert.equal(h.win.listeners.size,4); assert.equal(h.doc.listeners.size,2)
  }
  h.adapter.dispose(); h.adapter.dispose()
  assert.equal(h.frames.size,0); assert.equal(h.win.listeners.size,0); assert.equal(h.doc.listeners.size,0)
  h.key('KeyD'); h.adapter.step(50); center(h)
  for (const input of ['mouse','keyboard','gamepad']) {
    assert.ok(interactionModeIssue('real',input)); assert.equal(h.adapter.select(input,'real'),false)
    assert.equal(h.frames.size,0)
  }
})

test('camera provenance stays separate; four directions send the same unmirrored adapter coordinates', async t => {
  const mouse = createMouseFollowInput('mouse-offline-core')
  mouse.move(80,20,{left:0,top:0,width:100,height:100}); mouse.depth(.75)
  const original = getHandFollowInput(), frame = mouse.frame(1000)
  assert.ok(handFollowFrameIssue(frame))
  publishHandFollowInput(frame,Symbol('synthetic')); assert.equal(getHandFollowInput(),original)
  for (const direction of [0,90,180,270]) {
    const calls = []
    const client = createHandFollowClient({endpoint:'/api/sim/mouse-follow',now:() => 1000,
      fetcher:async (url,request) => {
        const body=JSON.parse(request.body), action=url.split('/').at(-1)
        calls.push({action,body})
        return {ok:true,json:async () => ({ok:true,state:{token:'offline',session:frame.session,configured:true,active:action!=='configure'}})}
      }})
    t.after(() => client.dispose())
    await client.configure({session:frame.session,direction})
    await client.start(frame.session); await client.sample(frame)
    assert.equal(calls[0].body.direction,direction)
    const sample = calls.find(call => call.action==='sample').body
    assert.deepEqual([sample.u,sample.v,sample.hand_open],[.8,.2,.75])
    client.dispose()
  }
})

test('measured camera fixture and synthetic adapter share the client coordinate contract', async t => {
  const mouse = createMouseFollowInput('mouse-offline-core')
  mouse.move(80,20,{left:0,top:0,width:100,height:100}); mouse.depth(.75)
  // An isolated measured-camera fixture, never published onto the camera bus.
  const camera = {session:'camera-offline-core',source:'camera',stream:'color',fresh:true,
    width:640,height:480,frameSeq:1,timestamp:1000,trackingState:'tracking',hands:true,
    point:{x:.8,y:.2,palm_scale:1,dx:1,dy:0,hand_open:.75}}
  const samples = []
  for (const [endpoint, frame] of [['/api/sim/hand-follow',camera],['/api/sim/mouse-follow',mouse.frame(1000)]]) {
    const client = createHandFollowClient({endpoint,now:() => 1000,fetcher:async (url,request) => {
      const body=JSON.parse(request.body), action=url.split('/').at(-1)
      if (action==='sample') samples.push(body)
      return {ok:true,json:async () => ({ok:true,state:{token:'offline',session:frame.session,configured:true,active:action!=='configure'}})}
    }})
    t.after(() => client.dispose())
    await client.configure({session:frame.session}); await client.start(frame.session); await client.sample(frame)
    client.dispose()
  }
  assert.equal(samples.length,2)
  const coordinates = sample => [sample.u,sample.v,sample.hand_open,sample.angle,sample.hand_present]
  assert.deepEqual(coordinates(samples[0]),[.8,.2,.75,0,true])
  assert.deepEqual(coordinates(samples[1]),coordinates(samples[0]))
})

test('backend owns four distinct quarter turns; adapters contain no direction mapping', () => {
  // Execute only the current pure rotation method, extracted with Python AST.
  // Do not import backend modules, instantiate planners or start services.
  const result = spawnSync('python',['-B','-c',`
import ast, math, pathlib, types
tree=ast.parse(pathlib.Path('backend/core/hand_follow.py').read_text(encoding='utf-8-sig'))
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HandFollow')
method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_rotate_pose')
scope={'math':math}
exec(compile(ast.Module(body=[method],type_ignores=[]),'<rotation-contract>','exec'),scope)
expected=[((1,0),(0,-1)),((0,1),(1,0)),((-1,0),(0,1)),((0,-1),(-1,0))]
for direction,(right,front) in zip((0,90,180,270),expected):
    owner=types.SimpleNamespace(direction=direction,_lo=[-1,-1,-1],_hi=[1,1,1])
    for point,want in (([1,0,0],right),([0,-1,0],front)):
        actual=scope['_rotate_pose'](owner,point)
        assert all(abs(a-b)<1e-12 for a,b in zip(actual[:2],want)),(direction,actual,want)
        restored=scope['_rotate_pose'](owner,actual,inverse=True)
        assert all(abs(a-b)<1e-12 for a,b in zip(restored,point))
`],{cwd:fileURLToPath(new URL('../../',import.meta.url)),encoding:'utf8'})
  assert.equal(result.status,0,result.error?.message || result.stderr)
  for (const name of ['interactionCore.js','interactionInput.js','mouseFollowInput.js']) {
    const source=readFileSync(new URL('../src/'+name,import.meta.url),'utf8')
    assert.doesNotMatch(source,/Math\.(sin|cos)|direction\s*===|direction\s*===?\s*(90|180|270)/)
  }
})
