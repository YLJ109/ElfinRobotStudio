import test from 'node:test'
import assert from 'node:assert/strict'
import {expandedCameraFence,constrainCameraOrbit} from '../src/cameraFence.js'
const bounds=expandedCameraFence({x:[-900,900],y:[-900,900],z:[0,1400]})
const inside=(p,b=bounds)=>p.every((v,i)=>Number.isFinite(v)&&v>=b.min[i]-1e-9&&v<=b.max[i]+1e-9)
test('110 percent expansion is centered and each face adds five percent full span',()=>{
 for(const [key,expected] of Object.entries({min:[-.99,-.07,-.99],max:[.99,1.47,.99]}))for(let i=0;i<3;i++)assert.ok(Math.abs(bounds[key][i]-expected[i])<1e-12)
 const shifted=expandedCameraFence({x:[100,300],y:[200,400],z:[500,700]});for(const [key,expected] of Object.entries({min:[.09,.49,-.41],max:[.31,.71,-.19]}))for(let i=0;i<3;i++)assert.ok(Math.abs(shifted[key][i]-expected[i])<1e-12)
 assert.equal(expandedCameraFence({x:[1,1],y:[0,1],z:[0,1]}),null)
})
test('far orbit and target are clamped and the reported spherical state exactly reconstructs eye',()=>{
 const out=constrainCameraOrbit({theta:.72,phi:1.16,r:5.3,target:[10,-20,30],fov:22},bounds)
 assert.ok(inside(out.eye)&&inside(out.target));assert.ok(out.r>0);assert.ok(out.fov>=22&&out.fov<=60)
 const p=[out.target[0]+out.r*Math.sin(out.phi)*Math.cos(out.theta),out.target[1]+out.r*Math.cos(out.phi),out.target[2]+out.r*Math.sin(out.phi)*Math.sin(out.theta)]
 for(let i=0;i<3;i++)assert.ok(Math.abs(p[i]-out.eye[i])<1e-9)
})
test('repeated orbit application does not jitter, and extreme pan/zoom remain finite inside tiny or shifted bounds',()=>{
 let seed=7;const random=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296}
 for(let n=0;n<1200;n++){
  const b=n%2?bounds:expandedCameraFence({x:[12000,12001],y:[-2000,-1999],z:[30,31]})
  let out=constrainCameraOrbit({theta:(random()-.5)*100,phi:random()*4,r:random()*100,target:[random()*40-20,random()*40-20,random()*40-20],fov:22},b)
  assert.ok(inside(out.eye,b)&&inside(out.target,b));assert.ok(Number.isFinite(out.r)&&out.r>0)
  const again=constrainCameraOrbit(out,b);for(let i=0;i<3;i++)assert.ok(Math.abs(out.eye[i]-again.eye[i])<1e-8)
 }
})
test('default angle remains useful inside bounds and boundary zoom changes optics without stale radius',()=>{
 const home=constrainCameraOrbit({theta:.72,phi:1.16,r:5.3,target:[0,.62,0],fov:22},bounds)
 assert.ok(inside(home.eye));assert.ok(home.fov>22)
 const zoom=constrainCameraOrbit({...home,r:home.r*1.09},bounds);assert.ok(inside(zoom.eye));assert.ok(zoom.fov>=home.fov)
 const changed=expandedCameraFence({x:[0,500],y:[0,500],z:[0,500]});const next=constrainCameraOrbit(home,changed);assert.ok(inside(next.eye,changed)&&inside(next.target,changed))
})
test('extreme or previously saved wide angles do not distort the view',()=>{
 for(const fov of [60,90,110]){
  const out=constrainCameraOrbit({theta:.72,phi:1.16,r:5.3,target:[0,.62,0],fov},bounds)
  assert.ok(inside(out.eye));assert.ok(out.fov<=60)
 }
})
