// Offline geometry analysis/regression. No renderer, service, build or hardware.
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import * as THREE from '../web/node_modules/three/build/three.module.js'
import { STLLoader } from '../web/node_modules/three/examples/jsm/loaders/STLLoader.js'

const root = new URL('../web/public/models/', import.meta.url)
const spec = JSON.parse(readFileSync(new URL('elfin5/robot.json', root)))

function loadStl(model, mesh) {
  const bytes = readFileSync(new URL(`${model}/${mesh}`, root))
  return new STLLoader().parse(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
}

function facets(geometry) {
  const p = geometry.getAttribute('position'), index = geometry.index
  const n = index ? index.count : p.count
  const out = [], a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3()
  const ab = new THREE.Vector3(), ac = new THREE.Vector3()
  for (let i = 0; i < n; i += 3) {
    const ids = [0,1,2].map(k => index ? index.getX(i+k) : i+k)
    a.fromBufferAttribute(p,ids[0]); b.fromBufferAttribute(p,ids[1]); c.fromBufferAttribute(p,ids[2])
    ab.subVectors(b,a); ac.subVectors(c,a)
    const normal = new THREE.Vector3().crossVectors(ab,ac), area = normal.length()/2
    normal.normalize()
    out.push({ ids, normal: normal.toArray(), area, points: [a.toArray(),b.toArray(),c.toArray()],
      centroid: [0,1,2].map(k => (a.getComponent(k)+b.getComponent(k)+c.getComponent(k))/3) })
  }
  return out
}

function planes(geometry) {
  const groups = new Map()
  for (const face of facets(geometry)) {
    const axis = face.normal.findIndex(n => Math.abs(n) > .9999)
    if (axis < 0 || face.area < 1e-12) continue
    const level = face.centroid[axis]
    if (Math.max(...face.points.map(p => p[axis]))-Math.min(...face.points.map(p => p[axis])) > 2e-5) continue
    const key = `${axis}:${Math.sign(face.normal[axis])}:${Math.round(level/1e-5)}`
    let item = groups.get(key)
    if (!item) groups.set(key,item={axis:'xyz'[axis],sign:Math.sign(face.normal[axis]),level:0,area:0,count:0,
      min:[Infinity,Infinity,Infinity],max:[-Infinity,-Infinity,-Infinity],center:[0,0,0]})
    item.area += face.area; item.level += face.area*level; item.count++
    for (let k=0;k<3;k++) {
      item.center[k] += face.area*face.centroid[k]
      for (const p of face.points) { item.min[k]=Math.min(item.min[k],p[k]); item.max[k]=Math.max(item.max[k],p[k]) }
    }
  }
  return [...groups.values()].sort((a,b)=>b.area-a.area).map(p=>({
    axis:p.axis,sign:p.sign,mm:+(p.level/p.area*1000).toFixed(3),area_cm2:+(p.area*10000).toFixed(3),count:p.count,
    center_mm:p.center.map(x=>+(x/p.area*1000).toFixed(3)),
    min_mm:p.min.map(x=>+(x*1000).toFixed(3)),max_mm:p.max.map(x=>+(x*1000).toFixed(3)),
  }))
}

function obliquePlanes(geometry) {
  const groups = new Map()
  for (const face of facets(geometry)) {
    if (face.area < 1e-12) continue
    const n = face.normal.map(v=>Math.round(v*1000)/1000)
    const d = n.reduce((sum,v,k)=>sum+v*face.centroid[k],0)
    const key = `${n.join(',')}:${Math.round(d/1e-4)}`
    let p = groups.get(key)
    if (!p) groups.set(key,p={n,d:0,area:0,count:0,center:[0,0,0],min:[Infinity,Infinity,Infinity],max:[-Infinity,-Infinity,-Infinity]})
    p.area+=face.area; p.d+=d*face.area; p.count++
    for (let k=0;k<3;k++) {
      p.center[k]+=face.centroid[k]*face.area
      for (const point of face.points) { p.min[k]=Math.min(p.min[k],point[k]); p.max[k]=Math.max(p.max[k],point[k]) }
    }
  }
  return [...groups.values()].sort((a,b)=>b.area-a.area).slice(0,30).map(p=>({
    normal:p.n,mm:+(p.d/p.area*1000).toFixed(3),area_cm2:+(p.area*10000).toFixed(3),count:p.count,
    center_mm:p.center.map(x=>+(x/p.area*1000).toFixed(3)),
    min_mm:p.min.map(x=>+(x*1000).toFixed(3)),max_mm:p.max.map(x=>+(x*1000).toFixed(3)),
  }))
}

function components(geometry) {
  const faces=facets(geometry), parents=faces.map((_,i)=>i), edges=new Map()
  const find=i=>parents[i]===i ? i : (parents[i]=find(parents[i]))
  for (let i=0;i<faces.length;i++) {
    const points=faces[i].points.map(p=>p.map(v=>Math.round(v*1e6)).join(','))
    for (let k=0;k<3;k++) {
      const a=points[k], b=points[(k+1)%3], key=a<b?`${a}|${b}`:`${b}|${a}`
      if (edges.has(key)) parents[find(i)]=find(edges.get(key))
      else edges.set(key,i)
    }
  }
  const groups=new Map()
  for (let i=0;i<faces.length;i++) {
    const id=find(i),face=faces[i]
    let p=groups.get(id)
    if (!p) groups.set(id,p={count:0,area:0,min:[Infinity,Infinity,Infinity],max:[-Infinity,-Infinity,-Infinity]})
    p.count++;p.area+=face.area
    for (let k=0;k<3;k++) for (const v of face.points) { p.min[k]=Math.min(p.min[k],v[k]);p.max[k]=Math.max(p.max[k],v[k]) }
  }
  return [...groups.values()].sort((a,b)=>b.area-a.area).slice(0,12).map(p=>({
    count:p.count,area_cm2:+(p.area*10000).toFixed(2),
    min_mm:p.min.map(x=>+(x*1000).toFixed(3)),max_mm:p.max.map(x=>+(x*1000).toFixed(3)) }))
}

if (process.argv.includes('--analyze')) {
  for (const [name,link] of Object.entries(spec.links)) {
    if (!link.visual) continue
    const filter = process.argv.find(arg => arg.startsWith('--link='))?.slice(7)
    if (filter && name !== filter) continue
    const geometry = loadStl('elfin5',link.visual.mesh)
    console.log(name,JSON.stringify(process.argv.includes('--components') ? components(geometry) : process.argv.includes('--oblique') ? obliquePlanes(geometry)
      : planes(geometry).slice(0,process.argv.includes('--detail') ? 100 : 18)))
    geometry.dispose()
  }
} else {
  const { applyJointCaps } = await import('../web/src/jointCaps.js')
  const masks={}, summary=[]
  const { NodeIO } = await import('../web/node_modules/@gltf-transform/core/dist/index.js')
  const { ALL_EXTENSIONS } = await import('../web/node_modules/@gltf-transform/extensions/dist/index.js')
  const { MeshoptDecoder } = await import('../web/node_modules/meshoptimizer/meshopt_decoder.mjs')
  await MeshoptDecoder.ready
  const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder':MeshoptDecoder})
  async function loadGlb(mesh,low=false) {
    const path=fileURLToPath(new URL('elfin5/'+mesh.replace(/\.STL$/,low?'.low.glb':'.glb'),root))
    const document=await io.read(path), primitive=document.getRoot().listMeshes()[0].listPrimitives()[0]
    const g=new THREE.BufferGeometry()
    for (const [semantic,name] of [['POSITION','position'],['NORMAL','normal']]) {
      const a=primitive.getAttribute(semantic)
      if (a) g.setAttribute(name,new THREE.BufferAttribute(a.getArray().slice(),a.getElementSize(),a.getNormalized()))
    }
    const index=primitive.getIndices()
    if (index) g.setIndex(new THREE.BufferAttribute(index.getArray().slice(),1))
    // GLB quantization is represented by its node transform, as in scene.js.
    // Decode normalized attributes before baking that transform into local metres.
    for (const name of ['position','normal']) {
      const a=g.getAttribute(name)
      if (a) g.setAttribute(name,new THREE.Float32BufferAttribute(
        Array.from({length:a.count},(_,i)=>[a.getX(i),a.getY(i),a.getZ(i)]).flat(),3))
    }
    const node=document.getRoot().listNodes().find(n=>n.getMesh())
    if (node) g.applyMatrix4(new THREE.Matrix4().fromArray(node.getWorldMatrix()))
    return g
  }
  function verify(geometry,name,link,label) {
    const before=facets(geometry), oldGroups=structuredClone(geometry.groups)
    const normalsBefore=geometry.getAttribute('normal')?.clone()
    const start=performance.now()
    assert.equal(applyJointCaps(geometry,name,link),geometry)
    const elapsed=performance.now()-start, report=geometry.userData.jointCaps
    assert.ok(report.applied,`${label}/${name}: ${report.reason}`)
    assert.ok(report.areaRatio>.002 && report.areaRatio<.60,`${label}/${name}: partial surface area`)
    const after=facets(geometry), color=geometry.getAttribute('color'), dark=[]
    assert.equal(after.length,before.length)
    assert.deepEqual(geometry.groups,oldGroups,'existing material groups must remain intact')
    for (let i=0;i<before.length;i++) {
      assert.deepEqual(after[i].points,before[i].points,'no vertex may move or be added as an overlay')
      if (normalsBefore && i%101===0) {
        const normalsAfter=geometry.getAttribute('normal')
        for (let k=0;k<3;k++) {
          const a=after[i].ids[k], b=before[i].ids[k]
          assert.deepEqual([normalsAfter.getX(a),normalsAfter.getY(a),normalsAfter.getZ(a)],
            [normalsBefore.getX(b),normalsBefore.getY(b),normalsBefore.getZ(b)],'keep source normals')
        }
      }
      const values=after[i].ids.map(id=>color.getX(id))
      assert.ok(values.every(v=>v===values[0]),'color boundaries must not bleed across shared vertices')
      if (values[0]<.1) dark.push(i)
      if ((name==='elfin_link2' && before[i].centroid[0]>-.30 && before[i].centroid[0]<-.12)
          || (name==='elfin_link4' && before[i].centroid[2]>.12 && before[i].centroid[2]<.23)) {
        assert.equal(values[0],1,'the middle of a long silver link must remain white')
      }
    }
    assert.ok(dark.length>0 && dark.length<before.length)
    assert.equal(dark.length,report.capTriangles)
    const previous=geometry.getAttribute('color')
    applyJointCaps(geometry,name,link)
    assert.equal(geometry.getAttribute('color'),previous,'repeat application should be allocation-free')
    if (label==='STL') masks[name]=dark
    summary.push({asset:label,link:name,cap_triangles:dark.length,total_triangles:before.length,
      surface_percent:+(report.areaRatio*100).toFixed(2),regions:report.regions.map(r=>r.name),ms:+elapsed.toFixed(1)})
  }
  for (const [name,link] of Object.entries(spec.links)) {
    if (!link.visual) continue
    const geometry = loadStl('elfin5',link.visual.mesh)
    verify(geometry,name,link,'STL')
    geometry.dispose()
    for (const low of [false,true]) {
      const glb=await loadGlb(link.visual.mesh,low)
      verify(glb,name,link,low?'GLB-low':'GLB')
      glb.dispose()
    }
  }
  const unknown=new THREE.BoxGeometry(.1,.1,.1)
  applyJointCaps(unknown,'unknown',{})
  assert.ok(!unknown.userData.jointCaps.applied)
  assert.ok(unknown.getAttribute('color').array.every(v=>v===1))
  const wrong=loadStl('elfin5','elfin_link2.STL').scale(1.1,1,1)
  applyJointCaps(wrong,'elfin_link2',spec.links.elfin_link2)
  assert.ok(!wrong.userData.jointCaps.applied,'mismatched geometry must not be painted')
  assert.ok(wrong.getAttribute('color').array.every(v=>v===1))
  unknown.dispose();wrong.dispose()
  for (const model of ['elfin3','elfin10','elfin15','elfin5_l','elfin10_l']) {
    const other=JSON.parse(readFileSync(new URL(`${model}/robot.json`,root)))
    for (const name of ['elfin_link2','elfin_link4']) {
      const link=other.links[name], g=loadStl(model,link.visual.mesh)
      applyJointCaps(g,name,link)
      assert.ok(!g.userData.jointCaps.applied,`${model}/${name}: do not extrapolate a cap layout`)
      assert.ok(g.getAttribute('color').array.every(v=>v===1))
      g.dispose()
    }
  }
  const output=process.argv.find(arg=>arg.startsWith('--colors='))?.slice(9)
  if (output) writeFileSync(output,JSON.stringify(masks))
  console.log(JSON.stringify(summary,null,2))
  console.log('joint caps: 21 STL/full-GLB/low-GLB parts + 10 other-model links + mismatch/unknown passed')
}
