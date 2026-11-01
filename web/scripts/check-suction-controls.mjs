import test from 'node:test'
import assert from 'node:assert/strict'
import {suctionFeedback,suctionBlockReason,validateSuctionMapping} from '../src/suctionControls.js'
const world=()=>({connected:true,stateFresh:true,snap:{mode:'sim'},toolPending:{},tools:{tool:{type:'suction'},mounted:{suction:{left:{vacuum:false,blow:false,holding:null},right:{vacuum:true,blow:false,holding:'piece'}}}}})
test('left and right feedback are independent and absent/stale feedback stays unknown',()=>{
 const w=world();assert.equal(suctionFeedback(w,'left').vacuum,false);assert.equal(suctionFeedback(w,'right').vacuum,true)
 delete w.tools.mounted.suction.left;assert.equal(suctionFeedback(w,'left').vacuum,null)
 w.stateFresh=false;assert.equal(suctionFeedback(w,'right').vacuum,null)
})
test('same-side vacuum and blow activation are mutually exclusive; off stays available',()=>{
 const w=world();assert.match(suctionBlockReason(w,'right','blow_on'),/停吸/)
 assert.equal(suctionBlockReason(w,'right','vacuum_off'),'')
 w.toolPending.left='vacuum_on';assert.match(suctionBlockReason(w,'left','blow_off'),/处理/)
 assert.equal(suctionBlockReason(w,'right','vacuum_off'),'')
})
test('mapping needs four distinct confirmed output channels and explicit levels',()=>{
 const io=Object.fromEntries(['a','b','c','d'].map((name,bit)=>[name,{group:'box',direction:'output',bit,confirmed_by_user:true}]))
 const mapping={left:{vacuum:{alias:'a',on:1},blow:{alias:'b',on:0}},right:{vacuum:{alias:'c',on:1},blow:{alias:'d',on:0}}}
 assert.equal(validateSuctionMapping(mapping,io),'')
 mapping.right.blow.alias='a';assert.match(validateSuctionMapping(mapping,io),/不同/)
 mapping.right.blow.alias='d';io.d.direction='input';assert.match(validateSuctionMapping(mapping,io),/输出/)
 io.d.direction='output';mapping.left.vacuum.on=null;assert.match(validateSuctionMapping(mapping,io),/电平/)
})
