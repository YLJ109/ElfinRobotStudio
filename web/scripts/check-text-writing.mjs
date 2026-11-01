import test from 'node:test'
import assert from 'node:assert/strict'
import {bitmapContours,writingText} from '../src/textContours.js'
import {drawingConfig} from '../src/drawingTask.js'
import {buildPen,disposeAsset} from '../src/toolVisual.js'
import * as THREE from 'three'
import {writingGroundPaths,writingStroke,writingDefaults,writingDefaultInk,writingInk} from '../src/writingConfig.js'
import {textWritingView} from '../src/textWritingView.js'

test('writing opens with the requested text and a restrained dark purple ink',()=>{
 assert.equal(writingDefaults.text,'一零九');assert.equal(writingDefaults.z,1);assert.equal(writingDefaultInk,'purple')
 for(const hex of writingInk[writingDefaultInk].colors){
  const r=hex>>16,g=(hex>>8)&255,b=hex&255
  assert.ok(b>r&&r>g,'purple palette remains distinct from cyan')
 }
 assert.ok(writingStroke.glowOpacity<.05)
})

test('front-facing glyphs run left-to-right from human -X and keep thin ink',()=>{
 const [path]=writingGroundPaths([[[-1,0],[1,0],[0,1]]],{x:-500,y:0,size:90})
 assert.deepEqual(path.map(p=>p.slice(0,3)),[[-500,90,1],[-500,-90,1],[-410,0,1]])
 assert.equal(textWritingView().theta,Math.PI)
 assert.ok(writingStroke.width<=2);assert.ok(writingStroke.glowWidth<=3)
 const raised=writingGroundPaths([[[-1,0],[1,0],[0,1]]],{x:-500,y:0,z:120,size:90})
 assert.ok(raised[0].every(point=>point[2]===120))
})
test('text accepts mixed visible characters and rejects more than five without truncation',()=>{
 for(const s of ['你好','A123!','中A1。?','12345'])assert.equal(writingText(s),s)
 for(const s of ['', '123456','a\nb'])assert.throws(()=>writingText(s))
})
test('actual bitmap boundaries keep the hole and separate glyphs instead of connecting letters',()=>{
 const w=30,h=16,a=new Uint8Array(w*h)
 for(let y=2;y<14;y++)for(let x=2;x<12;x++)if(x<5||x>8||y<5||y>10)a[y*w+x]=255
 for(let y=3;y<13;y++)for(let x=20;x<24;x++)a[y*w+x]=255
 const paths=bitmapContours(a,w,h)
 assert.equal(paths.length,3)
 for(const p of paths)assert.deepEqual(p[0],p.at(-1))
 assert.ok(paths.every(p=>Math.max(...p.map(v=>v[0]))<15||Math.min(...p.map(v=>v[0]))>15))
})
test('pen geometry matches the authoritative 160mm nib TCP and fits its envelope',()=>{
 const pen=buildPen();pen.updateMatrixWorld(true);const box=new THREE.Box3().setFromObject(pen)
 assert.ok(Math.abs(box.max.z*1000-160)<.01);assert.ok(box.min.z>=-1e-8)
 assert.ok(box.min.x>=-.006001&&box.max.x<=.006001)
 assert.deepEqual(pen.userData.tcp_mm,[0,0,160]);disposeAsset(pen)
})
