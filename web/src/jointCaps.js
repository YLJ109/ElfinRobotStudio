import { BufferAttribute, Float32BufferAttribute } from 'three'
import { robotBandDistance } from './robotSurface.js'

// Measured from the bundled official Elfin5 STL, in mesh-local metres.
// Covers are separate closed shells in these single-mesh links. Seed a known
// planar face, then follow welded triangle edges to the real part boundary.
// In particular, the Link1/3/5 cover faces are tilted 5 degrees, not axis-aligned.
const SIN5 = Math.sin(Math.PI / 36), COS5 = Math.cos(Math.PI / 36)
const DARK = [0.014, 0.017, 0.020] // linear RGB, approximately sRGB #202327
const SIGNATURES = {
  elfin_base: { triangles: 9110, min: [-.077984,-.115440,0], max: [.077984,.077881,.073], caps: [] },
  elfin_link1: { triangles: 33402, min: [-.130,-.063630,-.043500], max: [.082987,.063631,.212114], caps: [
    { name: 'shoulder-cover', normal: [COS5,0,SIN5], center: [.074949,.000080,.149713], radius: .037,
      min: [-.000010,-.063041,.085527], max: [.082987,.063202,.212114], area: .046978 },
  ] },
  elfin_link2: { triangles: 19016, min: [-.421549,-.058852,.001997], max: [.058930,.123472,.096098], caps: [
    { name: 'upper-arm-proximal-cover', normal: [0,0,1], center: [0,0,.093], radius: .0415,
      min: [-.047477,-.047500,.055], max: [.047475,.047500,.093], area: .025134 },
    { name: 'upper-arm-distal-cover', normal: [0,0,1], center: [-.372618,.074541,.074497], radius: .0325,
      min: [-.411108,.036061,.042497], max: [-.334128,.113021,.074497], area: .016834 },
  ] },
  elfin_link3: { triangles: 31360, min: [-.052362,-.053741,-.147292], max: [.052364,.161500,.044500], caps: [
    { name: 'elbow-cover', normal: [0,-SIN5,-COS5], center: [0,-.002586,-.141082], radius: .0274,
      min: [-.051535,-.053741,-.147292], max: [.051536,.049322,-.070395], area: .033988 },
  ] },
  elfin_link4: { triangles: 24106, min: [-.121396,-.050,.006], max: [.050,.050,.340954], caps: [
    { name: 'forearm-proximal-cover', normal: [0,0,-1], center: [0,0,.046], radius: .040,
      min: [-.039498,-.039500,.046], max: [.039464,.039500,.087942], area: .020626 },
    { name: 'forearm-distal-cover', normal: [-1,0,0], center: [-.120,0,.301], radius: .0275,
      min: [-.120,-.031500,.269550], max: [-.104,.031500,.332450], area: .011803 },
  ] },
  elfin_link5: { triangles: 27246, min: [-.040523,-.139720,-.125010], max: [.040557,.043017,.038720], caps: [
    { name: 'wrist-cover', normal: [0,SIN5,-COS5], center: [0,.001733,-.120584], radius: .021,
      min: [-.040002,-.038547,-.125010], max: [.040002,.041566,-.058992], area: .020857 },
  ] },
  elfin_link6: { triangles: 56560, min: [-.059200,-.042506,.006], max: [.042500,.042506,.054500], caps: [] },
}
const BOUNDS_TOLERANCE = .0004 // covers full GLB quantization and the bundled low LOD
const closeBounds = (a,b,tolerance=BOUNDS_TOLERANCE) => a?.length === 3 && b?.length === 3
  && a.every((value,i) => Number.isFinite(value) && Math.abs(value-b[i]) <= tolerance)

function triangleData(geometry) {
  const position = geometry.getAttribute('position'), index = geometry.index
  const count = (index ? index.count : position.count) / 3
  if (!Number.isInteger(count) || count < 1) return null
  const faces = [], point = id => [position.getX(id),position.getY(id),position.getZ(id)]
  for (let i=0;i<count;i++) {
    const ids = [0,1,2].map(k => index ? index.getX(i*3+k) : i*3+k)
    const vertices = ids.map(point), [a,b,c] = vertices
    const u=b.map((v,k)=>v-a[k]), v=c.map((value,k)=>value-a[k])
    const cross=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
    const length=Math.hypot(...cross)
    faces.push({ ids, vertices, area:length/2, normal:cross.map(value=>length ? value/length : 0),
      center:a.map((value,k)=>(value+b[k]+c[k])/3) })
  }
  return faces
}

function shellRoots(geometry, faces) {
  const position=geometry.getAttribute('position'), vertices=new Map(), welded=new Uint32Array(position.count)
  for (let i=0;i<position.count;i++) {
    const key=[position.getX(i),position.getY(i),position.getZ(i)].map(v=>Math.round(v*1e6)).join(',')
    if (!vertices.has(key)) vertices.set(key,vertices.size)
    welded[i]=vertices.get(key)
  }
  const parent=Uint32Array.from(faces,(_,i)=>i), edges=new Map(), stride=vertices.size
  const find = i => {
    let root=i
    while (parent[root] !== root) root=parent[root]
    while (parent[i] !== i) { const next=parent[i];parent[i]=root;i=next }
    return root
  }
  for (let i=0;i<faces.length;i++) {
    const ids=faces[i].ids.map(id=>welded[id])
    for (let k=0;k<3;k++) {
      const a=ids[k], b=ids[(k+1)%3]
      if (a===b) continue
      const key=Math.min(a,b)*stride+Math.max(a,b)
      if (edges.has(key)) parent[find(i)]=find(edges.get(key))
      else edges.set(key,i)
    }
  }
  return parent.map((_,i)=>find(i))
}

function matchesSeed(face, cap) {
  const alignment=face.normal.reduce((sum,v,k)=>sum+v*cap.normal[k],0)
  if (alignment < .995 || face.area < 1e-11) return false
  const delta=face.center.map((v,k)=>v-cap.center[k])
  const axial=delta.reduce((sum,v,k)=>sum+v*cap.normal[k],0)
  return Math.abs(axial) < .0004 && Math.hypot(...delta) < cap.radius + .0004
}

function shellMask(geometry, faces, caps) {
  const roots=shellRoots(geometry,faces), mask=new Uint8Array(faces.length), regions=[]
  for (const cap of caps) {
    const seeds=new Set(faces.flatMap((face,i)=>matchesSeed(face,cap) ? [roots[i]] : []))
    // A known plane must belong to exactly one independently bounded cover.
    if (seeds.size !== 1) continue
    const root=seeds.values().next().value, selected=[], min=[Infinity,Infinity,Infinity], max=[-Infinity,-Infinity,-Infinity]
    let area=0
    for (let i=0;i<faces.length;i++) {
      if (roots[i] !== root) continue
      selected.push(i);area+=faces[i].area
      for (const p of faces[i].vertices) for (let k=0;k<3;k++) { min[k]=Math.min(min[k],p[k]);max[k]=Math.max(max[k],p[k]) }
    }
    if (!closeBounds(min,cap.min) || !closeBounds(max,cap.max) || Math.abs(area/cap.area-1) > .2) continue
    for (const i of selected) mask[i]=1
    regions.push({ name:cap.name, triangles:selected.length, area, min, max })
  }
  return { mask, regions }
}

function paint(geometry, faces, mask) {
  let position=geometry.getAttribute('position'), index=geometry.index
  if (index) {
    const states=new Uint8Array(position.count)
    faces.forEach((face,i)=>face.ids.forEach(id=>{ states[id] |= mask[i] ? 2 : 1 }))
    if (states.includes(3)) {
      // Split only vertices used by both colors, preserving indexed GLB savings.
      const copies=new Map()
      states.forEach((state,id)=>{ if (state===3) copies.set(id,position.count+copies.size) })
      for (const [name,attribute] of Object.entries(geometry.attributes)) {
        if (name==='color') continue
        const source=attribute.isInterleavedBufferAttribute ? attribute.data.array : attribute.array
        const array=new source.constructor((position.count+copies.size)*attribute.itemSize)
        const offset=attribute.isInterleavedBufferAttribute ? attribute.offset : 0
        const stride=attribute.isInterleavedBufferAttribute ? attribute.data.stride : attribute.itemSize
        const copyVertex=(from,to)=>{
          for (let k=0;k<attribute.itemSize;k++) array[to*attribute.itemSize+k]=source[from*stride+offset+k]
        }
        for (let i=0;i<position.count;i++) copyVertex(i,i)
        for (const [from,to] of copies) copyVertex(from,to)
        geometry.setAttribute(name,new BufferAttribute(array,attribute.itemSize,attribute.normalized))
      }
      const indices=Array.from(index.array)
      faces.forEach((face,i)=>{ if (mask[i]) face.ids.forEach((id,k)=>{ if (copies.has(id)) indices[i*3+k]=copies.get(id) }) })
      geometry.setIndex(indices)
      position=geometry.getAttribute('position'); index=geometry.index
    }
  }
  const colors=new Float32Array(position.count*3).fill(1)
  for (let i=0;i<mask.length;i++) if (mask[i]) for (let k=0;k<3;k++) {
    const id=index ? index.getX(i*3+k) : i*3+k
    colors.set(DARK,id*3)
  }
  geometry.setAttribute('color',new Float32BufferAttribute(colors,3))
}

/** Mutates and returns the same mesh-local BufferGeometry, before any axis transform.
 * Body materials must enable vertexColors. White vertices preserve body silver.
 * This changes albedo only; a separate cap roughness needs a material/shader change.
 * Mesh bounds + source triangle count are an explicit Elfin5 compatibility gate;
 * other models are left white unless their part has the same measured signature.
 */
export function applyJointCaps(geometry, linkName, specLink) {
  const position=geometry?.getAttribute?.('position')
  if (!position) return geometry
  const signature=SIGNATURES[linkName], declared=specLink?.visual?.bbox
  const previous=geometry.userData.jointCaps
  if (previous?.version===1 && previous.applied && previous.linkName===linkName && geometry.getAttribute('color')
      && declared?.triangles===signature?.triangles && closeBounds(declared?.min,signature?.min)
      && closeBounds(declared?.max,signature?.max)) return geometry
  geometry.setAttribute('color',new Float32BufferAttribute(new Float32Array(position.count*3).fill(1),3))
  const info={ version:1, linkName, applied:false, reason:'unverified-part', capTriangles:0, triangleRatio:0, areaRatio:0, regions:[] }
  geometry.userData.jointCaps=info
  if (!signature || declared?.triangles !== signature.triangles || !closeBounds(declared.min,signature.min)
      || !closeBounds(declared.max,signature.max) || geometry.morphAttributes?.position?.length) return geometry
  geometry.computeBoundingBox()
  if (!closeBounds(geometry.boundingBox.min.toArray(),signature.min)
      || !closeBounds(geometry.boundingBox.max.toArray(),signature.max)) return geometry
  const faces=triangleData(geometry)
  if (!faces) return geometry
  let mask,regions
  if (signature.caps.length) ({mask,regions}=shellMask(geometry,faces,signature.caps))
  else {
    mask=new Uint8Array(faces.length);regions=[]
    for (let i=0;i<faces.length;i++) {
      const face=faces[i]
      if (linkName==='elfin_base') {
        // Actual outer base ring: retain the underside, central bore and port body.
        mask[i]=face.normal[2] >= -.2 && face.vertices.every(p=>p[2]>=-.00001 && p[2]<=.07301
          && Math.hypot(p[0],p[1])>=.055 && Math.hypot(p[0],p[1])<=.079)
      } else {
        // Actual tool flange face + its existing bevel, not a replacement disc.
        mask[i]=face.normal[2] >= -.2 && face.vertices.every(p=>p[2]>=.0474 && p[2]<=.0546
          && Math.hypot(p[0],p[1])<=.0427)
      }
    }
    regions.push({name:linkName==='elfin_base' ? 'base-outer-ring' : 'tool-flange-face'})
  }
  const totalArea=faces.reduce((sum,f)=>sum+f.area,0)
  const capArea=faces.reduce((sum,f,i)=>sum+(mask[i] ? f.area : 0),0)
  const count=mask.reduce((sum,v)=>sum+v,0), ratio=totalArea ? capArea/totalArea : 0
  // Reject a failed shell boundary rather than blackening an entire link.
  if (!count || ratio < .002 || ratio > .60 || count===faces.length) { info.reason='unverified-cap-boundary';return geometry }
  paint(geometry,faces,mask)
  Object.assign(info,{ applied:true, reason:'measured-elfin5-boundary', capTriangles:count,
    triangleRatio:count/faces.length, areaRatio:ratio, regions })
  return geometry
}

// Separate finishes on ORIGINAL triangles; no chrome tubes/discs laid over the
// robot. The validated Elfin5 signature gate above excludes unrelated models.
// Intervals are mesh-local connector bands, not changes to joint dimensions.
export function applyRobotFinishes(geometry,linkName) {
  if(!geometry?.userData.jointCaps?.applied || geometry.userData.jointCaps.linkName!==linkName || geometry.userData.robotFinishes?.version===2)return geometry
  const faces=triangleData(geometry),colors=geometry.getAttribute('color')
  if(!faces || !colors)return geometry
  const kinds=new Uint8Array(faces.length)
  for(let i=0;i<faces.length;i++){
    if(faces[i].ids.every(id=>colors.getX(id)<.1 && colors.getY(id)<.1 && colors.getZ(id)<.1))kinds[i]=2
    // Diagnostic batching only. BOTH non-black groups use the same analytic
    // body shader, so no face centroid can define a visible colour boundary.
    else if(robotBandDistance(linkName,faces[i].center)<=0)kinds[i]=1
  }
  let portTriangles=0
  if(linkName==='elfin_link6'){
    const roots=shellRoots(geometry,faces),shells=new Map()
    for(let i=0;i<faces.length;i++){
      const root=roots[i]
      if(!shells.has(root))shells.set(root,{min:[Infinity,Infinity,Infinity],max:[-Infinity,-Infinity,-Infinity],ids:[]})
      const shell=shells.get(root);shell.ids.push(i)
      for(const p of faces[i].vertices)for(let k=0;k<3;k++){shell.min[k]=Math.min(shell.min[k],p[k]);shell.max[k]=Math.max(shell.max[k],p[k])}
    }
    // Independently bounded original socket inserts. The adjacent silver wrist
    // casing and its connector rims keep their own material.
    for(const shell of shells.values())if(closeBounds(shell.min,[-.0592,-.02225,.01575]) && closeBounds(shell.max,[-.0535,.02225,.04325])){
      for(const i of shell.ids){kinds[i]=2;portTriangles++}
    }
  }
  const indices=[[],[],[]]
  for(let i=0;i<faces.length;i++)indices[kinds[i]].push(...faces[i].ids)
  // Paint and accent share one analytic shader, so submit them together.
  // Keep their counts for diagnostics, not an unnecessary second draw call.
  geometry.setIndex([...indices[0],...indices[1],...indices[2]])
  geometry.clearGroups()
  const bodyCount=indices[0].length+indices[1].length
  if(bodyCount)geometry.addGroup(0,bodyCount,0)
  if(indices[2].length)geometry.addGroup(bodyCount,indices[2].length,2)
  geometry.userData.robotFinishes={version:2,applied:true,boundary:'analytic-local-fragment',linkName,paintTriangles:indices[0].length/3,
    chromeTriangles:indices[1].length/3,blackTriangles:indices[2].length/3,portTriangles}
  return geometry
}
