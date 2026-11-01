import test from 'node:test'
import assert from 'node:assert/strict'
import {INTERACTION_RANGE_FACTORY,interactionRange,readInteractionRangeDefault,saveInteractionRangeDefault} from '../src/interactionRange.js'
const range=(x=-500)=>({center:[x,0,700],size:[900,300,500],direction:270,depthRange:[.06,.3]})
const storage=values=>({getItem:key=>values[key]??null})
test('shared factory is the exact requested negative-X range with no motion data',()=>{
 assert.deepEqual(interactionRange(INTERACTION_RANGE_FACTORY),range())
 const result=readInteractionRangeDefault({snap:{settings:{interaction_range_default:null}}},{storage:null})
 result.center[0]=0;assert.deepEqual(interactionRange(INTERACTION_RANGE_FACTORY),range())
 for(const patch of [{center:[null,0,700]},{size:[5001,300,500]},{direction:45},{depthRange:[.1,.105]}])assert.equal(interactionRange({...range(),...patch}),null)
})
test('backend persisted default wins over legacy saved/draft and survives a changed browser origin',()=>{
 const old=range(200),saved=range(-600),draft={version:1,...range(300)}
 const world={snap:{settings:{interaction_range_default:saved}}}
 assert.deepEqual(readInteractionRangeDefault(world,{storage:storage({'huayan.hand-follow.range-default.v1':JSON.stringify(old),'huayan.hand-follow.draft.v1':JSON.stringify(draft)})}),saved)
 assert.deepEqual(readInteractionRangeDefault(world,{storage:null}),saved)
 assert.deepEqual(readInteractionRangeDefault({}, {storage:storage({'huayan.hand-follow.range-default.v1':JSON.stringify(old),'huayan.hand-follow.draft.v1':JSON.stringify(draft)})}),old)
 assert.deepEqual(readInteractionRangeDefault({}, {storage:storage({'huayan.hand-follow.draft.v1':JSON.stringify(draft)})}),range(300))
 assert.deepEqual(readInteractionRangeDefault({}, {storage:storage({'huayan.hand-follow.draft.v1':JSON.stringify({...draft,version:2})})}),range())
})
test('saving awaits actual backend success and rejects failure without replacing its previous default',async()=>{
 const prior=range(),next=range(-550),calls=[];let resolve
 const world={snap:{settings:{interaction_range_default:prior}},setSettings:async patch=>{calls.push(patch);return new Promise(r=>resolve=r)}}
 let complete=false;const pending=saveInteractionRangeDefault(world,next).then(()=>complete=true)
 await Promise.resolve();assert.equal(complete,false);assert.deepEqual(world.snap.settings.interaction_range_default,prior)
 resolve({ok:true});await pending;assert.equal(complete,true);assert.deepEqual(calls,[{interaction_range_default:next}]);assert.deepEqual(world.snap.settings.interaction_range_default,prior,'only the server readback owns active settings')
 world.setSettings=async()=>({ok:false,why:'磁盘保存失败'});await assert.rejects(()=>saveInteractionRangeDefault(world,next),/磁盘保存失败/)
 world.setSettings=async()=>{throw Error('连接失败')};await assert.rejects(()=>saveInteractionRangeDefault(world,next),/连接失败/)
 assert.deepEqual(world.snap.settings.interaction_range_default,prior)
 let invalidRequests=0;world.setSettings=()=>invalidRequests++;await assert.rejects(()=>saveInteractionRangeDefault(world,{...next,size:['',300,500]}));assert.equal(invalidRequests,0)
})
