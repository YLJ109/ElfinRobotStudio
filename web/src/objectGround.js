// 场景无姿态字段：球用半径，其他代理形状用高度的一半。
export function objectGroundHeight(object) {
  return object.kind === 'sphere' ? Math.max(1, object.size[0]) : Math.max(1, object.size[2]) / 2
}
export function constrainObjectToGround(object, xyz) {
  return [xyz[0], xyz[1], Math.max(objectGroundHeight(object), xyz[2])]
}
