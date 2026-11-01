import boundsContract from '../../contracts/scene_bounds.json' with {type:'json'}
// Main display camera only. Robot coordinates are mm, Three world is X/Z/-Y metres.
export function expandedCameraFence(fence){
 if(!fence||!['x','y','z'].every(k=>Array.isArray(fence[k])&&fence[k].length===2&&fence[k].every(v=>typeof v==='number'&&Number.isFinite(v))&&fence[k][1]>fence[k][0]))return null
 const scale=boundsContract.expansion_scale
 if(!Number.isFinite(scale)||scale<1||scale>2)throw Error('invalid shared scene bounds contract')
 const range=k=>{const [a,b]=fence[k],c=(a+b)/2,h=(b-a)*scale/2;return [(c-h)/1000,(c+h)/1000]}
 const x=range('x'),y=range('y'),z=range('z');return {min:[x[0],z[0],-y[1]],max:[x[1],z[1],-y[0]]}
}
export function constrainCameraOrbit(input,bounds){
 const finite=v=>typeof v==='number'&&Number.isFinite(v),clamp=(v,a,b)=>Math.max(a,Math.min(b,v))
 const target=Array.isArray(input.target)&&input.target.length===3&&input.target.every(finite)?input.target.slice():[0,.62,0]
 const theta=finite(input.theta)?input.theta:.72,phi=clamp(finite(input.phi)?input.phi:1.16,0,Math.PI),requested=clamp(finite(input.r)?input.r:1,.000001,12)
 // Very wide perspective visibly stretches the robot near the image edges.
 const baseFov=clamp(finite(input.fov)?input.fov:22,10,60)
 if(bounds)for(let i=0;i<3;i++)target[i]=clamp(target[i],bounds.min[i],bounds.max[i])
 const eye=[target[0]+requested*Math.sin(phi)*Math.cos(theta),target[1]+requested*Math.cos(phi),target[2]+requested*Math.sin(phi)*Math.sin(theta)]
 if(bounds)for(let i=0;i<3;i++)eye[i]=clamp(eye[i],bounds.min[i],bounds.max[i])
 let dx=eye[0]-target[0],dy=eye[1]-target[1],dz=eye[2]-target[2],r=Math.hypot(dx,dy,dz)
 if(r<1e-9){
  // A target on a corner still needs a real, nonzero viewing direction.
  let axis=0,distance=0
  for(let i=0;i<3;i++)for(const edge of [bounds?.min[i]??target[i]-1,bounds?.max[i]??target[i]+1])if(Math.abs(edge-target[i])>Math.abs(distance)){axis=i;distance=edge-target[i]}
  eye[axis]=target[axis]+Math.sign(distance)*Math.min(Math.abs(distance)*.5,.01)
  dx=eye[0]-target[0];dy=eye[1]-target[1];dz=eye[2]-target[2];r=Math.hypot(dx,dy,dz)
 }
 const fov=clamp(2*Math.atan(Math.tan(baseFov*Math.PI/360)*requested/r)*180/Math.PI,10,60)
 return {eye,target,theta:Math.atan2(dz,dx),phi:Math.acos(clamp(dy/r,-1,1)),r,fov}
}
