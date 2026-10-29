import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { CylinderGeometry, MeshStandardMaterial, ShaderLib, Matrix4, Euler, Vector3 } from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'meshoptimizer/decoder'
import { applyJointCaps, applyRobotFinishes } from '../src/jointCaps.js'
import { ROBOT_BANDS, ROBOT_JOINT_RINGS, robotBandDistance, createRobotSurfaceMaterial, smoothRobotSurface } from '../src/robotSurface.js'

test('six physical joints have exactly one assigned band each',()=>{
  assert.equal(ROBOT_JOINT_RINGS.length,6)
  assert.equal(new Set(ROBOT_JOINT_RINGS.map(r=>r.joint)).size,6)
  assert.equal(Object.values(ROBOT_BANDS).flat().length,6)
  assert.deepEqual(ROBOT_JOINT_RINGS.map(r=>r.mesh),['elfin_link1','elfin_link1','elfin_link3','elfin_link3','elfin_link5','elfin_link6'])
  assert.equal(ROBOT_BANDS.elfin_link2,undefined)
  assert.equal(ROBOT_BANDS.elfin_link4,undefined)
  for(const [name,bands] of Object.entries(ROBOT_BANDS)) for(const [axis,lo,hi,x,y,z,rlo,rhi] of bands) {
    for(let degree=0;degree<360;degree+=3) {
      const point=[x,y,z], radial=[0,1,2].filter(k=>k!==axis), angle=degree*Math.PI/180
      point[axis]=(lo+hi)/2
      point[radial[0]]+=Math.cos(angle)*(rlo+rhi)/2
      point[radial[1]]+=Math.sin(angle)*(rlo+rhi)/2
      assert.ok(robotBandDistance(name,point)<0)
      for(const boundary of [lo,hi]) {
        point[axis]=boundary
        assert.ok(Math.abs(robotBandDistance(name,point))<1e-12,'boundary does not follow triangle edges')
      }
    }
  }
})

const spec=JSON.parse(readFileSync(new URL('../public/models/elfin5/robot.json',import.meta.url)))
const cross2=(a,b)=>a[0]*b[1]-a[1]*b[0]

async function loadOriginals(low) {
  const meshes=new Map(),loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder)
  for(const [name,link] of Object.entries(spec.links)) {
    if(!link.visual)continue
    const bytes=readFileSync(new URL(`../public/models/elfin5/${name}${low?'.low':''}.glb`,import.meta.url))
    const loaded=await loader.parseAsync(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),'')
    loaded.scene.updateMatrixWorld(true)
    let geometry
    loaded.scene.traverse(object=>{
      if(object.isMesh && !geometry)geometry=object.geometry.clone().applyMatrix4(object.matrixWorld)
      object.geometry?.dispose()
      for(const material of (Array.isArray(object.material)?object.material:[object.material]).filter(Boolean))material.dispose()
    })
    applyJointCaps(geometry,name,link);applyRobotFinishes(geometry,name)
    meshes.set(name,geometry)
  }
  return meshes
}

function assemblyMatrices(pose) {
  const world={world:new Matrix4()}
  for(const joint of spec.joints) {
    const local=new Matrix4().makeRotationFromEuler(new Euler(...joint.rpy,'ZYX')).setPosition(...joint.xyz)
    if(joint.limit)local.multiply(new Matrix4().makeRotationAxis(new Vector3(...joint.axis),pose))
    world[joint.child]=world[joint.parent].clone().multiply(local)
  }
  return world
}

function jointFaces(meshes,world,ring) {
  const joint=spec.joints.find(j=>j.name===ring.joint), inverse=world[ring.mesh].clone().invert(),faces=[]
  assert.ok([joint.parent,joint.child].includes(ring.mesh),'ring belongs to an actual joint neighbour')
  const axis=new Vector3(...joint.axis).transformDirection(inverse.clone().multiply(world[joint.child]))
  assert.ok(Math.abs(axis.getComponent(ring.band[0]))>.999999,'mask axis matches physical URDF axis')
  for(const name of [joint.parent,joint.child]) {
    const geometry=meshes.get(name),position=geometry.getAttribute('position'),index=geometry.index
    const matrix=inverse.clone().multiply(world[name])
    const points=Array.from({length:position.count},(_,i)=>new Vector3().fromBufferAttribute(position,i).applyMatrix4(matrix).toArray())
    const kinds=new Uint8Array(index.count/3)
    for(const group of geometry.groups)kinds.fill(group.materialIndex,group.start/3,(group.start+group.count)/3)
    for(let i=0;i<index.count;i+=3)faces.push({name,kind:kinds[i/3],points:[0,1,2].map(k=>points[index.getX(i+k)])})
  }
  return faces
}

// Intersect ORIGINAL triangles with a plane, then cast 720 radial rays through
// the resulting segments. A local probe starts 10mm outside the measured
// collar envelope and includes the neighbouring link: a buried shaft cannot
// pass. Distant arm geometry (e.g. a point 394mm away) is ordinary view occlusion,
// not a missing circumference. No ideal cylinder is tested.
function surfaceSection(faces,band,axial,samples=720) {
  const [axis,,,x,y,z]=band,center=[x,y,z],radial=[0,1,2].filter(k=>k!==axis),segments=[]
  for(const face of faces) {
    const points=[]
    for(let k=0;k<3;k++) {
      const a=face.points[k],b=face.points[(k+1)%3]
      if((a[axis]<=axial && b[axis]>axial)||(b[axis]<=axial && a[axis]>axial)) {
        const t=(axial-a[axis])/(b[axis]-a[axis])
        points.push(radial.map(j=>a[j]+t*(b[j]-a[j])-center[j]))
      }
    }
    if(points.length===2)segments.push({a:points[0],delta:points[1].map((v,k)=>v-points[0][k]),face})
  }
  return Array.from({length:samples},(_,i)=>{
    const angle=(i+.173)*2*Math.PI/samples,dir=[Math.cos(angle),Math.sin(angle)]
    let hit=null
    for(const segment of segments) {
      const den=cross2(dir,segment.delta)
      if(Math.abs(den)<1e-14)continue
      const radius=cross2(segment.a,segment.delta)/den,t=cross2(segment.a,dir)/den
      if(t>=-1e-9 && t<=1+1e-9 && radius>0 && radius<=band[7]+.010 && (!hit || radius>hit.radius))hit={radius,face:segment.face}
    }
    if(!hit)return null
    const point=[x,y,z];point[axis]=axial
    for(let k=0;k<2;k++)point[radial[k]]+=hit.radius*dir[k]
    return {...hit,point}
  })
}

for(const low of [false,true])test(`actual ${low?'low':'full'} GLB: six exposed single seams, circumference AND width coverage`,async()=>{
  const meshes=await loadOriginals(low),reports=[]
  try {
    for(const pose of [0,.73]) {
      const world=assemblyMatrices(pose)
      for(const ring of ROBOT_JOINT_RINGS) {
        const faces=jointFaces(meshes,world,ring),[axis,lo,hi]=ring.band
        let covered=0,minRadius=Infinity,maxRadius=0
        for(const fraction of [.02,.25,.5,.75,.98]) {
          const hits=surfaceSection(faces,ring.band,lo+(hi-lo)*fraction)
          for(const [i,hit]of hits.entries()) {
            assert.ok(hit,`${ring.joint} angular sample ${i}: actual exterior exists`)
            assert.equal(hit.face.name,ring.mesh,`${ring.joint} pose ${pose} fraction ${fraction} angle ${i/2} radius ${hit.radius*1000}mm: assigned rim must be exposed, not buried in its neighbour`)
            assert.notEqual(hit.face.kind,2,`${ring.joint}: retain black shells`)
            assert.ok(robotBandDistance(ring.mesh,hit.point)<0,`${ring.joint} ${low?'low':'full'} pose ${pose} fraction ${fraction} angle ${i/2}: actual radius ${hit.radius*1000}mm must be inside mask`)
            covered++;minRadius=Math.min(minRadius,hit.radius);maxRadius=Math.max(maxRadius,hit.radius)
          }
        }
        // A narrow connected slab on each real collar, with both adjacent
        // sections unpainted: catches a second stripe and axial overspray.
        for(const axial of [lo-.0005,hi+.0005])for(const hit of surfaceSection(faces,ring.band,axial,72)) {
          if(hit?.face.name===ring.mesh)assert.ok(robotBandDistance(ring.mesh,hit.point)>0,`${ring.joint}: no adjacent extra stripe`)
        }
        assert.ok(hi-lo<=.0071,'bounded 4.5–7mm axial stripe')
        assert.ok(ring.band[7]-ring.band[6]<=.0026,'measured radial shell guard, not a widened arm coating')
        if(pose===0)reports.push({joint:ring.joint,mesh:ring.mesh,axis:'xyz'[axis],axial_mm:[lo*1000,hi*1000],coverage:`${covered}/3600`,radius_mm:[minRadius*1000,maxRadius*1000].map(v=>+v.toFixed(3))})
      }
    }
    console.log(JSON.stringify({lod:low?'low':'full',sections:5,angles:720,poses:[0,.73],rings:reports}))
  } finally {for(const geometry of meshes.values())geometry.dispose()}
})

test('installed Three standard shaders receive one shared PBR mask; skin and unknown models',()=>{
  const body=new MeshStandardMaterial({color:'#eeeeee',vertexColors:true})
  try {
    for(const name of Object.keys(ROBOT_BANDS)) {
      const geometry={userData:{robotFinishes:{applied:true,linkName:name}}}
      const material=createRobotSurfaceMaterial(body,geometry,name)
      const shader={uniforms:{},vertexShader:ShaderLib.standard.vertexShader,fragmentShader:ShaderLib.standard.fragmentShader}
      material.onBeforeCompile(shader)
      assert.ok(shader.vertexShader.includes('robotLocalPosition = position;'))
      assert.ok(shader.fragmentShader.includes('fwidth(bandDistance)'))
      assert.ok(shader.fragmentShader.includes('mix(diffuseColor.rgb, robotAccent, robotRingMask)'))
      assert.ok(shader.fragmentShader.includes('mix(roughnessFactor, 0.22, robotRingMask)'))
      assert.ok(shader.fragmentShader.includes('mix(metalnessFactor, 0.82, robotRingMask)'))
      const accent=shader.uniforms.robotAccent.value.getHexString()
      material.color.set('#8d9aaa');material.metalness=.6;material.roughness=.3
      assert.equal(shader.uniforms.robotAccent.value.getHexString(),accent)
      assert.equal(accent,'2fd6f0')
      material.dispose()
    }
    assert.equal(createRobotSurfaceMaterial(body,{userData:{}},'elfin_link1'),body)
    assert.equal(createRobotSurfaceMaterial(body,{userData:{robotFinishes:{applied:true}}},'unknown'),body)
  } finally {body.dispose()}
})

test('angle-weighted smoothing rounds a faceted cylinder and preserves its hard cap edge',()=>{
  const geometry=new CylinderGeometry(.05,.05,.1,24,1,false).toNonIndexed()
  geometry.computeVertexNormals() // Synthetic fixture only: deliberately faceted source.
  geometry.userData.jointCaps={applied:true}
  const position=geometry.getAttribute('position').array.slice(), index=geometry.index
  const normals=geometry.getAttribute('normal').array.slice()
  const p=geometry.getAttribute('position')
  smoothRobotSurface(geometry)
  const normal=geometry.getAttribute('normal')
  let rounded=0,caps=0
  for(let i=0;i<p.count;i++) {
    if(Math.abs(normals[i*3+1])>.99) {
      assert.ok(Math.abs(normal.getY(i))>.99999,'flat cap remains flat at rim')
      caps++
    } else {
      const radial=Math.hypot(p.getX(i),p.getZ(i))
      assert.ok((normal.getX(i)*p.getX(i)+normal.getZ(i)*p.getZ(i))/radial>.99999)
      rounded++
    }
  }
  assert.ok(rounded && caps)
  assert.deepEqual(geometry.getAttribute('position').array,position)
  assert.equal(geometry.index,index)
  geometry.dispose()
  const unknown=new CylinderGeometry()
  const original=unknown.getAttribute('normal')
  smoothRobotSurface(unknown)
  assert.equal(unknown.getAttribute('normal'),original)
  unknown.dispose()
})
