// Wrist + the four MCP joints are stable when fingers bend or close.
// Scale is relative image width, NOT a calibrated camera-to-hand distance.
export const PALM_ANCHORS = Object.freeze([0, 5, 9, 13, 17])
// MediaPipe's 21 hand connections. Only thin lines / dots are drawn, no filled palm.
export const HAND_CONNECTIONS = Object.freeze([
  [0,1],[1,2],[2,3],[3,4], [0,5],[5,6],[6,7],[7,8],
  [5,9],[9,10],[10,11],[11,12], [9,13],[13,14],[14,15],[15,16],
  [13,17],[0,17],[17,18],[18,19],[19,20],
])
export function cleanHandLandmarks(landmarks) {
  if (!Array.isArray(landmarks)) return []
  return Array.from({length:21}, (_, i) => {
    const p = landmarks[i]
    return p && [p.x, p.y].every(Number.isFinite)
      ? {x:p.x,y:p.y,...(Number.isFinite(p.z) ? {z:p.z} : {})} : null
  })
}

// Relative finger extension, not apparent hand size or physical depth. Use the
// median four-finger reach so one pointing finger cannot mean "open palm".
// Ratios remove common image scale; optional relative z helps folded fingers.
export function handOpennessFromLandmarks(landmarks) {
  const scores=[]
  for(const base of [5,9,13,17]) {
    const points=Array.from({length:4},(_,i)=>landmarks?.[base+i])
    if(points.some(p=>!p || ![p.x,p.y].every(Number.isFinite))) continue
    const withZ=points.every(p=>Number.isFinite(p.z))
    const distance=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y,withZ ? a.z-b.z : 0)
    const length=distance(points[0],points[1])+distance(points[1],points[2])+distance(points[2],points[3])
    if(length<.005) continue
    const reach=distance(points[0],points[3])/length
    scores.push(Math.max(0,Math.min(1,(reach-.55)/.35)))
  }
  if(scores.length<3) return null
  scores.sort((a,b)=>a-b)
  return scores.length===4 ? (scores[1]+scores[2])/2 : scores[1]
}
export function palmFromLandmarks(landmarks) {
  const anchors = PALM_ANCHORS.map(i => landmarks?.[i])
  if (anchors.some(p => !p || ![p.x, p.y].every(Number.isFinite))) return null
  if (anchors.some(p => p.x < -.1 || p.x > 1.1 || p.y < -.1 || p.y > 1.1)) return null
  const [wrist, index, middle, , pinky] = anchors
  const x = anchors.reduce((sum, p) => sum + p.x, 0) / anchors.length
  const y = anchors.reduce((sum, p) => sum + p.y, 0) / anchors.length
  const palm_scale = Math.hypot(index.x - pinky.x, index.y - pinky.y)
  const dx = middle.x - wrist.x, dy = middle.y - wrist.y
  // Less than ~3px at 640px is not a usable palm, even if technically nonzero.
  if (x < 0 || x > 1 || y < 0 || y > 1 || palm_scale < .005 || Math.hypot(dx, dy) < .005) return null
  return { x, y, palm_scale, dx, dy }
}

export const DEFAULT_HAND_DEPTH_RANGE = Object.freeze([.06, .30])
export function validHandDepthRange(range) {
  return Array.isArray(range) && range.length === 2 && range.every(Number.isFinite) &&
    range[0] > 0 && range[1] - range[0] >= .01 - Number.EPSILON
}
export function palmMarkerDiameter(scale, range = DEFAULT_HAND_DEPTH_RANGE) {
  const [min,max] = validHandDepthRange(range) ? range : DEFAULT_HAND_DEPTH_RANGE
  const fraction = Number.isFinite(scale) ? Math.max(0,Math.min(1,(scale-min)/(max-min))) : 0
  return 8 + fraction * 24
}

// Compatibility exports for existing geometry consumers; not the tracking target.
export function fingerMarkerDiameter(scale) {
  return Number.isFinite(scale) && scale > 0 ? Math.max(10,Math.min(30,scale*90)) : 14
}

export function imageBoundsInView(viewWidth, viewHeight, imageWidth, imageHeight) {
  if (![viewWidth, viewHeight, imageWidth, imageHeight].every(Number.isFinite) ||
      Math.min(viewWidth, viewHeight, imageWidth, imageHeight) <= 0) return null
  const ratio = Math.min(viewWidth / imageWidth, viewHeight / imageHeight)
  const width = imageWidth * ratio, height = imageHeight * ratio
  return { left: (viewWidth - width) / 2, top: (viewHeight - height) / 2, width, height }
}

export function fingertipInView(point, viewWidth, viewHeight, imageWidth, imageHeight, mirrored = false) {
  if (!point || ![point.x, point.y, viewWidth, viewHeight, imageWidth, imageHeight].every(Number.isFinite) ||
      point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1 || Math.min(viewWidth, viewHeight, imageWidth, imageHeight) <= 0) return null
  const ratio = Math.min(viewWidth / imageWidth, viewHeight / imageHeight)
  const width = imageWidth * ratio, height = imageHeight * ratio
  return { x: (viewWidth - width) / 2 + (mirrored ? 1 - point.x : point.x) * width, y: (viewHeight - height) / 2 + point.y * height }
}

export const palmInView = fingertipInView

export function handLandmarksInView(landmarks, ...view) {
  const points = cleanHandLandmarks(landmarks).flatMap((p, id) => {
    const projected = palmInView(p, ...view)
    return projected ? [{id,...projected,palm:PALM_ANCHORS.includes(id)}] : []
  })
  const lookup = new Map(points.map(p => [p.id, p]))
  const bones = HAND_CONNECTIONS.flatMap(([a,b]) => lookup.has(a) && lookup.has(b)
    ? [{id:`${a}-${b}`,from:lookup.get(a),to:lookup.get(b)}] : [])
  return {points,bones}
}

// Undo the display-only mirror before asking the backend for raw depth pixels.
export function imagePixelAt(x, y, viewWidth, viewHeight, imageWidth, imageHeight, mirrored = false) {
  if (![x, y, viewWidth, viewHeight, imageWidth, imageHeight].every(Number.isFinite) ||
      Math.min(viewWidth, viewHeight, imageWidth, imageHeight) <= 0) return null
  const ratio = Math.min(viewWidth / imageWidth, viewHeight / imageHeight)
  const px = x - (viewWidth - imageWidth * ratio) / 2
  const py = y - (viewHeight - imageHeight * ratio) / 2
  if (px < 0 || py < 0 || px >= imageWidth * ratio || py >= imageHeight * ratio) return null
  const column = Math.floor(px / ratio)
  return { x: mirrored ? imageWidth - 1 - column : column, y: Math.floor(py / ratio) }
}
