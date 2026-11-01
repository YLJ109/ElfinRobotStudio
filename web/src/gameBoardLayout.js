import * as THREE from 'three'

const finite3 = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite)
export const baseToGameScene = xyz => new THREE.Vector3(xyz[0] / 1000, xyz[2] / 1000, -xyz[1] / 1000)
export function readBoardLayout(value, game) {
  if (!value || value.game !== game || !finite3(value.center_mm) || !finite3(value.axis_col) || !finite3(value.axis_row)
      || !finite3(value.tray_center_mm) || !finite3(value.board_size_mm) || !finite3(value.tray_size_mm)
      || !Number.isFinite(value.span_mm) || value.span_mm <= 0) return null
  const col = new THREE.Vector3(value.axis_col[0], value.axis_col[2], -value.axis_col[1])
  const row = new THREE.Vector3(value.axis_row[0], value.axis_row[2], -value.axis_row[1])
  const up = new THREE.Vector3(0, 1, 0)
  if (Math.abs(col.length() - 1) > 1e-6 || Math.abs(row.length() - 1) > 1e-6 || Math.abs(col.dot(row)) > 1e-6
      || col.clone().cross(up).distanceTo(row) > 1e-6) return null
  return { ...value, center: baseToGameScene(value.center_mm), basis: new THREE.Matrix4().makeBasis(col, up, row) }
}
export function boardCellBase(layout, r, c) {
  const pitch = layout.span_mm / Math.max(layout.rows, layout.cols)
  const col = (c - (layout.cols - 1) / 2) * pitch
  const row = (r - (layout.rows - 1) / 2) * pitch
  return layout.center_mm.map((v, i) => v + layout.axis_col[i] * col + layout.axis_row[i] * row)
}
