import assert from 'node:assert/strict'
import test from 'node:test'
import * as geometry from '../src/handGeometry.js'
import { createPalmTracker } from '../src/handFollowInput.js'

function hand(open) {
  const points=Array.from({length:21},()=>({x:.5,y:.7,z:0}))
  points[0]={x:.5,y:.8,z:0}
  for(const [finger,x] of [[5,.38],[9,.46],[13,.54],[17,.62]]) {
    const coords=open ? [[x,.6,0],[x,.5,0],[x,.4,0],[x,.3,0]]
      : [[x,.6,0],[x,.52,0],[x,.55,-.06],[x,.63,-.04]]
    coords.forEach(([x,y,z],i)=>points[finger+i]={x,y,z})
  }
  return points
}

test('fist is backward (0), open palm forward (1), independent of image scale / mirror / roll',()=>{
  for(const open of [false,true]) for(const scale of [.35,1,1.5]) for(const roll of [0,.8,2.4]) {
    const points=hand(open).map(p=>({x:.5+scale*((p.x-.5)*Math.cos(roll)-(p.y-.5)*Math.sin(roll)),
      y:.5+scale*((p.x-.5)*Math.sin(roll)+(p.y-.5)*Math.cos(roll)),z:p.z*scale}))
    assert.equal(geometry.handOpennessFromLandmarks(points),Number(open))
    assert.equal(geometry.handOpennessFromLandmarks(points.map(p=>({...p,x:1-p.x}))),Number(open))
  }
})
test('one extended finger is not an open palm; incomplete / degenerate fingers give no depth command',()=>{
  const points=hand(false), open=hand(true)
  for(let i=5;i<=8;i++) points[i]=open[i]
  assert.equal(geometry.handOpennessFromLandmarks(points),0)
  assert.equal(geometry.handOpennessFromLandmarks([]),null)
  assert.equal(geometry.handOpennessFromLandmarks(Array(21).fill({x:.5,y:.5})),null)
  const broken=hand(true);broken[12]=null;broken[16]=null
  assert.equal(geometry.handOpennessFromLandmarks(broken),null)
})
test('gesture smoothing retains genuine capture age, converges and resets after loss',()=>{
  const tracker=createPalmTracker()
  const first=tracker.update({landmarks:hand(false),timestamp:1000,frameSeq:1},1000)
  assert.equal(first.point.hand_open,0)
  const changed=tracker.update({landmarks:hand(true),timestamp:1067,frameSeq:2},1067)
  assert.ok(changed.point.hand_open>0 && changed.point.hand_open<1)
  for(let i=3;i<15;i++) tracker.update({landmarks:hand(true),timestamp:1000+(i-1)*67,frameSeq:i},1000+(i-1)*67)
  assert.ok(tracker.read(1871).point.hand_open>.99)
  const held=tracker.update({landmarks:[],timestamp:1900,frameSeq:15},1900)
  assert.equal(held.timestamp,1871)
  assert.equal(tracker.read(2600).point,null)
  assert.equal(tracker.update({landmarks:hand(false),timestamp:2700,frameSeq:16},2700).point.hand_open,0)
})
