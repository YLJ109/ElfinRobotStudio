import test from 'node:test'
import assert from 'node:assert/strict'
import {editSceneObject} from '../src/sceneObjectEditing.js'
import {objectGroundHeight} from '../src/objectGround.js'
const state=(objects)=>({ok:true,revision:'opaque-revision',objects,templates:['workbench'],can_undo:true,can_redo:false})
const object={id:'stable-id',name:'box',kind:'box',size:[200,40,20],xyz:[0,0,300],rotation_deg:[0,0,0],scale:1,locked:false}
const world=()=>({connected:true,stateFresh:true,snap:{mode:'sim',session_id:'s',moving:false,objects:[structuredClone(object)]}})
const response=value=>({ok:true,status:200,json:async()=>value})
test('ground support uses rotation and scale for box, cylinder and sphere',()=>{
 assert.equal(objectGroundHeight({...object,scale:2}),20)
 assert.ok(Math.abs(objectGroundHeight({...object,rotation_deg:[0,90,0],scale:2})-200)<1e-9)
 assert.equal(objectGroundHeight({...object,kind:'sphere',scale:2}),400)
 assert.ok(Math.abs(objectGroundHeight({...object,kind:'cylinder',rotation_deg:[90,0,0]})-200)<1e-9)
})
test('property edit uses stable ID and the existing opaque server revision without changing local snapshot',async()=>{
 const w=world(),before=JSON.stringify(w.snap),calls=[]
 const result=await editSceneObject(w,'object',object.id,{rotation_deg:[0,0,45],scale:2},async(path,options)=>{
  calls.push([path,options]);return response(state(options.method?[{...object,scale:2}]:[object]))})
 assert.equal(calls[0][0],'/api/workspace/scene');assert.equal(calls[1][0],'/api/workspace/scene/object')
 assert.deepEqual(JSON.parse(calls[1][1].body),{id:object.id,revision:'opaque-revision',patch:{rotation_deg:[0,0,45],scale:2}})
 assert.equal(result.object.id,object.id);assert.equal(JSON.stringify(w.snap),before)
})
test('duplicate locates only the acknowledged new stable object; delete never changes original IDs',async()=>{
 const w=world();let n=0
 const copy=await editSceneObject(w,'duplicate',object.id,undefined,async()=>response(state(++n===1?[object]:[object,{...object,id:'new-copy'}])))
 assert.equal(copy.object.id,'new-copy')
 n=0;const removed=await editSceneObject(w,'remove',object.id,undefined,async()=>response(state(++n===1?[object]:[])))
 assert.equal(removed.object,null)
})
test('locked objects block edits, copies and removal; only a standalone explicit unlock is sent',async()=>{
 const w=world(),calls=[],fetcher=async(path,options)=>{calls.push([path,options]);return response(state([{...object,locked:true}]))}
 for(const action of ['object','duplicate','remove'])await assert.rejects(editSceneObject(w,action,object.id,{scale:2},fetcher),/锁定/)
 assert.ok(calls.every(([,options])=>!options.method))
 w.snap.objects[0].locked=true
 await editSceneObject(w,'object',object.id,{locked:false},fetcher)
 assert.equal(calls.at(-1)[1].method,'POST')
})
test('session change during revision read and save failure cannot mutate or silently retry the scene',async()=>{
 const w=world(),calls=[]
 await assert.rejects(editSceneObject(w,'object',object.id,{scale:2},async(path)=>{calls.push(path);w.snap.session_id='new';return response(state([object]))}),/变化/)
 assert.equal(calls.length,1)
 const other=world();let count=0
 await assert.rejects(editSceneObject(other,'object',object.id,{scale:2},async()=>++count===1?response(state([object])):{ok:false,status:409,json:async()=>({why:'revision conflict'})}),/revision conflict/)
 assert.equal(count,2);assert.equal(other.snap.objects[0].scale,1)
})


test('a concurrently changed property cannot be overwritten using a newly read revision and stale visible value',async()=>{
 const w=world(),calls=[]
 await assert.rejects(editSceneObject(w,'object',object.id,{scale:3},async(path,options)=>{calls.push([path,options]);return response(state([{...object,scale:2}]))}),/字段已变化/)
 assert.equal(calls.length,1)
})
