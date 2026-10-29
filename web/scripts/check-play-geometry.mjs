// Offline geometry checks: actual triangles/rays, no WebGL or service required.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as THREE from 'three'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { catalogEntry, catalogSpec, catalogThumbnail } from '../src/objectCatalog.js'

const source=readFileSync(new URL('../src/scene.js',import.meta.url),'utf8')
const tokenSource=source.slice(source.indexOf('function createPlayTokenGeometry'),source.indexOf('export async function createScene'))
const createToken=new Function('THREE',tokenSource+';return createPlayTokenGeometry')(THREE)
const objectSource=source.slice(source.indexOf('  function objGeometry(o)'),source.indexOf('  function clearPlayLabel'))
const geometryFor=new Function('THREE','catalogEntry','mergeGeometries','createPlayTokenGeometry',objectSource+';return objGeometry')(THREE,catalogEntry,mergeGeometries,createToken)
const hit=(mesh,x,z)=>new THREE.Raycaster(new THREE.Vector3(x,1,z),new THREE.Vector3(0,-1,0)).intersectObject(mesh)
function withMesh(spec,check){
  const geometry=geometryFor(spec),material=new THREE.MeshBasicMaterial({side:THREE.DoubleSide}),mesh=new THREE.Mesh(geometry,material)
  mesh.updateMatrixWorld(true)
  try{check(mesh,geometry)}finally{geometry.dispose();material.dispose()}
}

test('play presets use conservative boxes, physical dimensions and safe distinct thumbnails',()=>{
  const thumbnails=new Set()
  for(const id of ['token_o','token_x','zone_a','zone_b']){
    const spec=catalogSpec(id,[500,0,40])
    assert.equal(spec.kind,'box');assert.equal(spec.preset,id)
    assert.deepEqual(spec.size,id.startsWith('token')?[70,70,16]:[330,330,4])
    const svg=decodeURIComponent(catalogThumbnail(id).split(',')[1])
    assert.match(svg,/<svg /);assert.doesNotMatch(svg,/<script|<image|foreignObject|\bon\w+=|href=/i)
    thumbnails.add(svg)
    if(id.startsWith('token'))assert.equal(catalogEntry(id).parts,undefined)
  }
  assert.equal(thumbnails.size,4)
  assert.match(decodeURIComponent(catalogThumbnail('token_o')),/fill-rule="evenodd"/)
})

test('O is an open ring through its full thickness; X has diagonal arms and empty quadrants',()=>{
  withMesh(catalogSpec('token_o',[0,0,0]),mesh=>{
    for(const [x,z] of [[0,0],[.015,0],[0,.015]])assert.equal(hit(mesh,x,z).length,0)
    for(const [x,z] of [[.029,0],[-.029,0],[0,.029],[0,-.029]])assert.ok(hit(mesh,x,z).length)
  })
  withMesh(catalogSpec('token_x',[0,0,0]),mesh=>{
    for(const [x,z] of [[0,0],[.024,.024],[-.024,.024],[.024,-.024],[-.024,-.024]])assert.ok(hit(mesh,x,z).length)
    for(const [x,z] of [[.025,0],[-.025,0],[0,.025],[0,-.025]])assert.equal(hit(mesh,x,z).length,0)
  })
})

test('all play solids stay inside their box envelope, including resized tokens and chess grid',()=>{
  for(const preset of ['token_o','token_x','zone_a','zone_b'])for(const size of [[70,70,16],[120,90,8]]){
    const spec={...catalogSpec(preset,[0,0,0]),size,task:{group:'offline',mode:'chess',role:preset,slot:-1,symbol:''}}
    withMesh(spec,(_mesh,geometry)=>{
      geometry.computeBoundingBox()
      const bounds=geometry.boundingBox
      for(const [axis,index] of [['x',0],['y',2],['z',1]]){
        assert.ok(bounds.min[axis]>=-size[index]/2000-1e-8,`${preset} min ${axis}`)
        assert.ok(bounds.max[axis]<=size[index]/2000+1e-8,`${preset} max ${axis}`)
      }
    })
  }
})

test('B chess board has four raised planar dividers; sorting and A stay plain',()=>{
  for(const preset of ['zone_a','zone_b'])for(const mode of ['chess','sorting']){
    const spec={...catalogSpec(preset,[0,0,2]),task:{group:'offline',mode,role:preset,slot:-1,symbol:''}}
    withMesh(spec,(mesh,geometry)=>{
      const chess=preset==='zone_b' && mode==='chess'
      assert.equal(geometry.userData.parts,chess?9:5)
      const center=hit(mesh,0,0)[0].point.y
      for(const offset of [-.055,.055]){
        const vertical=hit(mesh,offset,0)[0].point.y,horizontal=hit(mesh,0,offset)[0].point.y
        if(chess){assert.ok(vertical>center+.001);assert.ok(horizontal>center+.001)}
        else {assert.ok(Math.abs(vertical-center)<1e-8);assert.ok(Math.abs(horizontal-center)<1e-8)}
      }
      geometry.computeBoundingBox();assert.ok(geometry.boundingBox.max.y<=.00200001)
    })
  }
})
