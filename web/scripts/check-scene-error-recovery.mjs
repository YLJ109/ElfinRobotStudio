// Actual Pinia transaction actions, fake HTTP only; no server or device.
import test from 'node:test'
import assert from 'node:assert/strict'
import {createPinia} from 'pinia'
import {useWorld} from '../src/store.js'
const response=(body,status=200)=>({ok:status<400,status,json:async()=>body})
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:value=>resolve(value)}}
function fixture(t){
  const world=useWorld(createPinia())
  const objects=['a','b'].map(id=>({id,name:id,kind:'box',xyz:[0,0,100],size:[100,100,100],scale:1,locked:false}))
  world.connected=true;world.stateFresh=true
  world.snap={session_id:'test',mode:'sim',moving:false,objects,exec:{execution:{busy:false}}}
  const status=()=>({ok:true,revision:'r',objects,templates:[],can_undo:false,can_redo:false})
  let edit=async()=>response(status())
  t.mock.method(globalThis,'fetch',async(path,options={})=>{
    if(path==='/api/workspace/scene')return response(status())
    if(path==='/api/workspace/scene/object')return edit(JSON.parse(options.body))
    if(path==='/other-failure')return response({ok:false,why:'different actionable error'})
    return response({ok:true})
  })
  return {world,status,setEdit:value=>edit=value}
}

test('scene error belongs to object id, not every request sharing the endpoint',async t=>{
  const h=fixture(t);let broken=new Set(['a','b'])
  h.setEdit(async body=>broken.has(body.id)?response({detail:'same floor error'},422):response(h.status()))
  await assert.rejects(h.world.editObject('a',{scale:1.2}),/floor/)
  const first=h.world.error
  await h.world.call('/api/speed',{});assert.equal(h.world.error,first)
  broken.delete('b');await h.world.editObject('b',{locked:false});assert.equal(h.world.error,first)
  broken.add('b');await assert.rejects(h.world.editObject('b',{scale:1.2}),/floor/)
  broken.delete('a');await h.world.editObject('a',{xyz:[0,0,150]})
  assert.equal(h.world.error,first,'same error text from object B still owns the banner')
  broken.delete('b');await h.world.editObject('b',{xyz:[0,0,150]});assert.equal(h.world.error,'')
})

test('late scene failure is handled but cannot replace a newer unrelated failure',async t=>{
  const h=fixture(t),old=deferred();let calls=0
  h.setEdit(async()=>++calls===1?old.promise:response(h.status()))
  const first=h.world.editObject('a',{scale:1.2})
  await new Promise(resolve=>setImmediate(resolve))
  await h.world.editObject('a',{xyz:[0,0,150]})
  await h.world.call('/other-failure',{})
  old.resolve(response({detail:'old floor failure'},422))
  await assert.rejects(first,error=>error.requestHandled===true && /old floor/.test(error.message))
  assert.equal(h.world.error,'different actionable error')
})

test('old scene success cannot erase a newer failure; manual errors remain independent',async t=>{
  const h=fixture(t),old=deferred();let calls=0
  h.setEdit(async()=>++calls===1?old.promise:response({detail:'new failure'},422))
  const first=h.world.editObject('a',{xyz:[0,0,150]})
  await new Promise(resolve=>setImmediate(resolve))
  await assert.rejects(h.world.editObject('a',{scale:1.2}),/new failure/)
  old.resolve(response(h.status()));await first
  assert.match(h.world.error,/new failure/)
  h.world.error='manual validation failure'
  h.setEdit(async()=>response(h.status()))
  await h.world.editObject('a',{xyz:[0,0,150]})
  assert.equal(h.world.error,'manual validation failure')
})

test('late object A failure cannot replace a newer object B failure',async t=>{
  const h=fixture(t),old=deferred()
  h.setEdit(async body=>body.id==='a'?old.promise:response({detail:'new object B failure'},422))
  const first=h.world.editObject('a',{scale:1.2})
  await new Promise(resolve=>setImmediate(resolve))
  await assert.rejects(h.world.editObject('b',{scale:1.2}),/object B/)
  old.resolve(response({detail:'old object A failure'},422))
  await assert.rejects(first,/object A/)
  assert.match(h.world.error,/object B/)
})
