// Only replace published defaults, never a user's custom installation.
export const CAMERA_FORWARD_TRIM_MM=11
export const HEAD_CAMERA_MOUNT=Object.freeze({offset_mm:Object.freeze([-36,0,31]),direction:Object.freeze([0,0,1000])})
const oldMounts=[[[100,0,50],[400,0,-1000]],[[0,0,25],[0,0,1000]],[[69.6,0,-24.25],[0,1000,0]],[[0,115.1,-24],[1000,0,0]],[[72.1,0,-24.25],[1000,0,0]],[[-58.6,0,-6],[0,0,1000]],[[-36,0,20],[0,0,1000]],[[-36,0,23],[0,0,1000]],[[-36,0,26],[0,0,1000]]]
export function headCameraSettings(value){
  if(!value || !oldMounts.some(([offset,direction])=>JSON.stringify(value.offset_mm)===JSON.stringify(offset) && JSON.stringify(value.direction)===JSON.stringify(direction)))return value
  return {...value,offset_mm:[...HEAD_CAMERA_MOUNT.offset_mm],direction:[...HEAD_CAMERA_MOUNT.direction]}
}
