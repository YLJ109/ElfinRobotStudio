import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'meshoptimizer/decoder'
import { applyJointCaps, applyRobotFinishes } from '../src/jointCaps.js'
import { smoothRobotSurface, createRobotSurfaceMaterial, ROBOT_BANDS, ROBOT_JOINT_RINGS } from '../src/robotSurface.js'
import { MeshStandardMaterial } from 'three'

const spec=JSON.parse(readFileSync(new URL('../public/models/elfin5/robot.json',import.meta.url)))
for(const low of [false,true])test(`实际${low?'低精度':'完整'}GLB：连续金属遮罩、保留黑接口、仅平滑法线`,async()=>{
  const loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder)
  const chromeLinks=new Set()
  for(const [name,link] of Object.entries(spec.links)){
    if(!link.visual)continue
    const bytes=readFileSync(new URL('../public/models/elfin5/'+link.visual.mesh.replace(/\.STL$/i,low?'.low.glb':'.glb'),import.meta.url))
    const loaded=await loader.parseAsync(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),'')
    loaded.scene.updateMatrixWorld(true)
    let geometry
    loaded.scene.traverse(object=>{
      if(object.isMesh && !geometry)geometry=object.geometry.clone().applyMatrix4(object.matrixWorld)
      object.geometry?.dispose()
      for(const material of (Array.isArray(object.material)?object.material:[object.material]).filter(Boolean))material.dispose()
    })
    try {
      applyJointCaps(geometry,name,link)
      assert.equal(geometry.userData.jointCaps.applied,true,name)
      const vertices=geometry.getAttribute('position').array.slice(),normals=geometry.getAttribute('normal').array.slice()
      const sourceNormal=geometry.getAttribute('normal')
      const directions=Array.from({length:sourceNormal.count},(_,i)=>[sourceNormal.getX(i),sourceNormal.getY(i),sourceNormal.getZ(i)])
      const count=geometry.index?.count || geometry.getAttribute('position').count
      applyRobotFinishes(geometry,name)
      const info=geometry.userData.robotFinishes
      assert.ok(info,name)
      assert.equal(info.boundary,'analytic-local-fragment')
      if(process.env.ELFIN_RING_REPORT)console.log(JSON.stringify(info))
      assert.deepEqual(geometry.getAttribute('position').array,vertices,'original vertex positions unchanged')
      assert.deepEqual(geometry.getAttribute('normal').array,normals,'source normals unchanged')
      assert.equal(geometry.index.count,count)
      assert.equal(geometry.groups.reduce((sum,group)=>sum+group.count,0),count)
      assert.ok(geometry.groups.length<=2,'body/accent share one draw; black parts use at most one more')
      assert.ok(info.paintTriangles>0,'do not coat whole arm in chrome')
      if(info.chromeTriangles)chromeLinks.add(name)
      if(name==='elfin_link6')assert.equal(info.portTriangles,low?1812:4474,'original wrist socket shell must remain black')
      const index=geometry.index
      applyRobotFinishes(geometry,name)
      assert.equal(geometry.index,index,'repeat application does not allocate again')
      const indices=index.array.slice(),groups=structuredClone(geometry.groups)
      smoothRobotSurface(geometry)
      assert.deepEqual(geometry.getAttribute('position').array,vertices)
      assert.deepEqual(geometry.index.array,indices)
      assert.deepEqual(geometry.groups,groups)
      const normal=geometry.getAttribute('normal')
      assert.ok(normal.array.every(Number.isFinite))
      for(let i=0;i<normal.count;i++) {
        const n=[normal.getX(i),normal.getY(i),normal.getZ(i)]
        const old=directions[i]
        if(Math.hypot(...old)<1e-10) {
          assert.deepEqual(n,old,'degenerate source corner stays unchanged')
          continue
        }
        assert.ok(Math.abs(Math.hypot(...n)-1)<.002,'unit output normal')
        assert.ok(n.reduce((s,v,k)=>s+v*old[k],0)/Math.hypot(...old)>Math.cos(36*Math.PI/180),`${name} normal ${i} stays in original crease cone: ${old} -> ${n}`)
      }
      assert.ok(geometry.userData.robotSurface.changedNormals>0,`${name}: improve curved shading`)
      smoothRobotSurface(geometry)
      assert.equal(geometry.getAttribute('normal'),normal,'smoothing is once per geometry')
      const body=new MeshStandardMaterial({vertexColors:true})
      const surface=createRobotSurfaceMaterial(body,geometry,name)
      if(ROBOT_BANDS[name])assert.notEqual(surface,body,'each verified ring owner has its shader')
      else assert.equal(surface,body,'unassigned links have no extra rings')
      if(surface!==body)surface.dispose()
      body.dispose()
    } finally {geometry?.dispose()}
  }
  assert.deepEqual([...chromeLinks].sort(),[...new Set(ROBOT_JOINT_RINGS.map(r=>r.mesh))].sort(),'diagnostic groups follow physical joint ownership; coverage is checked on actual surface sections separately')
})
