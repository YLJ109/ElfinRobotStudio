import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {computed} from 'vue'
const read=p=>readFileSync(new URL('../src/'+p,import.meta.url),'utf8')
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
test('release before free-drive enable reply must close and never renew',async()=>{
 const pending=deferred(),calls=[],intervals=new Set(),snap={value:{free:{on:false}}}
 const code=read('panels/PointsPanel.vue').split('/* ---- 手拖示教')[1].split('// One explanation')[0]
 const body=code.slice(code.indexOf('const free ='))
 const api=new Function('computed','snap','isReal','granted','w','setInterval','clearInterval',body+';return {freeDown,freeUp}')(
 computed,snap,{value:true},{value:true},{free:async(on)=>{calls.push(on);if(on)return pending.promise;return {ok:true}}},fn=>{intervals.add(fn);return fn},fn=>intervals.delete(fn))
 const request=api.freeDown();api.freeUp();pending.resolve({ok:true});await request
 assert.equal(intervals.size,0);assert.equal(calls.at(-1),false)
})
test('Escape arbitration precedes editing guards and uses capture',()=>{
 const app=read('App.vue'),key=app.split('function onKey(e) {')[1].split('/* 点卡片')[0]
 assert.ok(key.indexOf("e.key === 'Escape'")<key.indexOf('isContentEditable'))
 assert.ok(app.includes("addEventListener('keydown', onKey, true)"))
 assert.ok(!read('components/CameraWidget.vue').includes('ev.stopPropagation()'))
})
test('IO calibration requires explicit channel and ordinary busy does not disable stop',()=>{
 const io=read('panels/TelemetryPanel.vue'),power=read('panels/PowerPanel.vue')
 assert.ok(!io.includes('@click="probe(grp, 0)"'))
 assert.match(io,/window.confirm/)
 assert.match(power,/stopBusy/)
})


test('Escape issues software stop from every editing target and ignores key repeat',()=>{
 const body=read('App.vue').match(/function onKey\(e\) \{([\s\S]*?)\n\}/)[1]
 let stops=0,prevented=0,cancelled=0
 const execute=new Function('w','e','scene',body)
 const key=(w,e)=>execute(w,e,{cancelPlacement(){cancelled++}})
 for(const target of [{tagName:'INPUT'},{tagName:'TEXTAREA'},{tagName:'SELECT'},{isContentEditable:true},{closest:()=>({})}]) {
  key({estop(){stops++}},{key:'Escape',target,preventDefault(){prevented++}})
 }
 key({estop(){stops++}},{key:'Escape',repeat:true,preventDefault(){prevented++}})
 assert.equal(stops,5);assert.equal(prevented,6);assert.equal(cancelled,6)
})
