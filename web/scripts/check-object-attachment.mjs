import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as THREE from 'three'

// Exercise the production rendering function without creating a GPU context.
const source=readFileSync(new URL('../src/scene.js',import.meta.url),'utf8')
const body=source.slice(source.indexOf('  function presentObjectAttachment()'),source.indexOf('  function setObjects('))
function fixture(){
 const group=new THREE.Group(),flange=new THREE.Group(),mesh=new THREE.Object3D()
 group.add(mesh)
 const read=new Function('THREE','objMeshes','linkNodes','objGroup',`
 let objectAttachment=null,objectMove=null;const attachmentPoint=new THREE.Vector3();
 ${body}
 return {run:presentObjectAttachment,set(value){objectAttachment=value},drag(value){objectMove=value}}`)(THREE,{piece:mesh},{flange},group)
 return {read,group,flange,mesh}
}
test('held material follows the same rendered flange transform rather than the newer snapshot position',()=>{
 const h=fixture();h.read.set({id:'piece',link:'flange',local_m:[.1,0,.02]})
 h.flange.position.set(.4,.2,.3);h.flange.updateMatrixWorld(true)
 h.mesh.position.set(9,9,9);h.read.run()
 assert.deepEqual(h.mesh.position.toArray(),[.5,.2,.32])
 h.flange.rotation.z=Math.PI/2;h.flange.updateMatrixWorld(true);h.read.run()
 assert.ok(h.mesh.position.distanceTo(new THREE.Vector3(.4,.3,.32))<1e-10)
})
test('detached, missing and actively dragged materials are never moved by a stale attachment',()=>{
 const h=fixture();h.mesh.position.set(1,2,3);h.read.run()
 h.read.set({id:'missing',link:'flange',local_m:[0,0,0]});h.read.run()
 h.read.set({id:'piece',link:'missing',local_m:[0,0,0]});h.read.run()
 h.read.set({id:'piece',link:'flange',local_m:[0,0,0]});h.read.drag({id:'piece'});h.read.run()
 assert.deepEqual(h.mesh.position.toArray(),[1,2,3])
})
test('attachment position respects the scene object parent coordinate system',()=>{
 const h=fixture();h.group.position.set(.2,.1,0);h.group.updateMatrixWorld(true)
 h.flange.position.set(.4,.2,.3);h.flange.updateMatrixWorld(true)
 h.read.set({id:'piece',link:'flange',local_m:[.1,0,.02]});h.read.run()
 const world=h.mesh.getWorldPosition(new THREE.Vector3())
 assert.ok(world.distanceTo(new THREE.Vector3(.5,.2,.32))<1e-10)
})
