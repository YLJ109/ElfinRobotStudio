import { Color, Float32BufferAttribute } from 'three'

// Exactly ONE physical seam per URDF joint, on its exposed original casing.
// Ownership is intentionally not one ring per link: J2/J4 use the PARENT rim.
// In particular the child shafts at positive z of J3/J5 sit inside their parent
// housing. Painting both a shaft and a parent rim made duplicate/hidden bands.
// band = [axis, axial min/max, centre xyz, radial min/max], mesh-local metres.
// Radial guards bound the measured exterior collar, not the entire arm radius.
export const ROBOT_JOINT_RINGS = [
  { joint:'elfin_joint1', mesh:'elfin_link1', band:[2,.002,.009,0,0,0,.0575,.0600] },
  { joint:'elfin_joint2', mesh:'elfin_link1', band:[0,-.083,-.078,0,0,.1465,.0575,.0600] },
  { joint:'elfin_joint3', mesh:'elfin_link3', band:[2,-.004,.002,0,0,0,.0465,.0490] },
  { joint:'elfin_joint4', mesh:'elfin_link3', band:[1,.118,.123,0,0,-.072,.0465,.0490] },
  { joint:'elfin_joint5', mesh:'elfin_link5', band:[2,-.004,.002,0,0,0,.0360,.0385] },
  { joint:'elfin_joint6', mesh:'elfin_link6', band:[2,.007,.0115,0,0,0,.0410,.0435] },
]
export const ROBOT_BANDS = {}
for (const {mesh,band} of ROBOT_JOINT_RINGS) (ROBOT_BANDS[mesh] ||= []).push(band)

export function robotBandDistance(linkName, point) {
  let distance = Infinity
  for (const [axis,lo,hi,x,y,z,rlo,rhi] of ROBOT_BANDS[linkName] || []) {
    const center=[x,y,z], radial=point.filter((_,k)=>k!==axis).map((v,i)=>v-center.filter((_,k)=>k!==axis)[i])
    const radius=Math.hypot(...radial)
    distance=Math.min(distance,Math.max(lo-point[axis],point[axis]-hi,rlo-radius,radius-rhi))
  }
  return distance
}

/** A dedicated body clone per verified link; slots 0 AND 1 use this material.
 * Black shells keep slot 2. Skin changes update only the standard body values.
 * PBR colour, roughness and metalness all use the same analytic fragment mask.
 */
export function createRobotSurfaceMaterial(body, geometry, linkName) {
  if (!geometry?.userData.robotFinishes?.applied || geometry.userData.robotFinishes.linkName!==linkName || !ROBOT_BANDS[linkName]) return body
  const material=body.clone(), f=value=>Number(value).toFixed(7)
  const distances=ROBOT_BANDS[linkName].map(([axis,lo,hi,x,y,z,rlo,rhi],i)=>{
    const axial='xyz'[axis], radial=['yz','xz','xy'][axis], center=[x,y,z].filter((_,k)=>k!==axis)
    return `float radius${i} = length( robotLocalPosition.${radial} - vec2(${center.map(f).join(',')}) );
    bandDistance = min(bandDistance, max(max(${f(lo)} - robotLocalPosition.${axial}, robotLocalPosition.${axial} - ${f(hi)}), max(${f(rlo)} - radius${i}, radius${i} - ${f(rhi)})));`
  }).join('\n')
  material.envMapIntensity=1.2
  material.userData.surface='analytic-body-and-accent'
  material.onBeforeCompile=shader=>{
    shader.uniforms.robotAccent={value:new Color('#2fd6f0')}
    shader.vertexShader=shader.vertexShader.replace('#include <common>','#include <common>\nvarying vec3 robotLocalPosition;')
      .replace('#include <begin_vertex>','#include <begin_vertex>\nrobotLocalPosition = position;')
    shader.fragmentShader=shader.fragmentShader.replace('#include <common>',
      '#include <common>\nvarying vec3 robotLocalPosition;\nuniform vec3 robotAccent;')
      .replace('#include <color_fragment>',`#include <color_fragment>
    float bandDistance = 1e6;
    ${distances}
    float bandAA = max(fwidth(bandDistance), 0.000001);
    float robotRingMask = 1.0 - smoothstep(-bandAA, bandAA, bandDistance);
    diffuseColor.rgb = mix(diffuseColor.rgb, robotAccent, robotRingMask);`)
      .replace('#include <roughnessmap_fragment>','#include <roughnessmap_fragment>\nroughnessFactor = mix(roughnessFactor, 0.22, robotRingMask);')
      .replace('#include <metalnessmap_fragment>','#include <metalnessmap_fragment>\nmetalnessFactor = mix(metalnessFactor, 0.82, robotRingMask);')
  }
  material.customProgramCacheKey=()=>`robot-analytic-v2:${linkName}`
  return material
}

/** Smooth only coincident corners with compatible original normals and finish
 * ownership. Corner-angle weights avoid large planar triangles dominating a
 * curved strip. Positions, index, groups and hard-normal splits stay intact.
 * One load-time pass; no temporary structures retained by the geometry.
 */
export function smoothRobotSurface(geometry) {
  if (!geometry?.userData.jointCaps?.applied || geometry.userData.robotSurface) return geometry
  const position=geometry.getAttribute('position'), normal=geometry.getAttribute('normal')
  if (!position || !normal || normal.count!==position.count) return geometry
  const count=position.count, weights=new Float64Array(count), owners=new Uint8Array(count)
  const index=geometry.index, ids=i=>index ? index.getX(i) : i, total=index?.count || count
  for (const group of geometry.groups) for (let i=group.start;i<group.start+group.count;i++) owners[ids(i)]|=group.materialIndex===2 ? 2 : 1
  for (let i=0;i<total;i+=3) {
    const face=[ids(i),ids(i+1),ids(i+2)]
    for (let k=0;k<3;k++) {
      const a=face[k],b=face[(k+1)%3],c=face[(k+2)%3]
      const u=[position.getX(b)-position.getX(a),position.getY(b)-position.getY(a),position.getZ(b)-position.getZ(a)]
      const v=[position.getX(c)-position.getX(a),position.getY(c)-position.getY(a),position.getZ(c)-position.getZ(a)]
      const length=Math.hypot(...u)*Math.hypot(...v)
      if (length) weights[a]+=Math.acos(Math.max(-1,Math.min(1,u.reduce((sum,value,j)=>sum+value*v[j],0)/length)))
    }
  }
  const buckets=new Map(), normals=new Float32Array(count*3), threshold=Math.cos(35*Math.PI/180)
  for (let i=0;i<count;i++) {
    const key=[position.getX(i),position.getY(i),position.getZ(i)].map(v=>Math.round(v*1e6)).join(',')
    if (!buckets.has(key)) buckets.set(key,[])
    buckets.get(key).push(i)
  }
  let changed=0
  for (const bucket of buckets.values()) for (const i of bucket) {
    const original=[normal.getX(i),normal.getY(i),normal.getZ(i)], sum=[0,0,0], originalLength=Math.hypot(...original)
    for (const j of bucket) {
      if (owners[i]!==owners[j]) continue
      const other=[normal.getX(j),normal.getY(j),normal.getZ(j)], length=Math.hypot(...other)
      if (!length || !originalLength || original.reduce((s,v,k)=>s+v*other[k],0)/(length*originalLength)<threshold) continue
      for (let k=0;k<3;k++) sum[k]+=other[k]/length*weights[j]
    }
    const length=Math.hypot(...sum)
    const result=length ? sum.map(v=>v/length) : originalLength ? original.map(v=>v/originalLength) : original
    if (result.some((v,k)=>Math.abs(v-original[k])>1e-5)) changed++
    normals.set(result,i*3)
  }
  geometry.setAttribute('normal',new Float32BufferAttribute(normals,3))
  geometry.userData.robotSurface={version:1,changedNormals:changed,creaseDegrees:35}
  return geometry
}
