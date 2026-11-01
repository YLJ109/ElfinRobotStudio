export function objectRotationMatrix(object) {
  const [x,y,z]=(object.rotation_deg || [0,0,0]).map(v=>v*Math.PI/180)
  const sx=Math.sin(x),cx=Math.cos(x),sy=Math.sin(y),cy=Math.cos(y),sz=Math.sin(z),cz=Math.cos(z)
  return [[cz*cy,cz*sy*sx-sz*cx,cz*sy*cx+sz*sx],
    [sz*cy,sz*sy*sx+cz*cx,sz*sy*cx-cz*sx],[-sy,cy*sx,cy*cx]]
}
// Base-Z support of the same rotated/scaled proxy used by backend collisions.
export function objectGroundHeight(object) {
  const size=object.size.map(v=>v*(object.scale ?? 1))
  if(object.kind==='sphere')return Math.max(.1,size[0])
  const row=objectRotationMatrix(object)[2]
  if(object.kind==='cylinder')return Math.hypot(row[0],row[1])*size[0]+Math.abs(row[2])*size[2]/2
  return row.reduce((sum,v,i)=>sum+Math.abs(v)*size[i]/2,0)
}
export function constrainObjectToGround(object, xyz) {
  return [xyz[0], xyz[1], Math.max(objectGroundHeight(object), xyz[2])]
}
