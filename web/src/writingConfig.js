// Writing presentation and ground geometry live here; no robot ownership/state.
export const writingDefaults=Object.freeze({text:'一零九',x:-500,y:0,z:90,size:90,fontStyle:'thin'})
export const writingDefaultInk='purple'
export const writingInk=Object.freeze({
  purple:{label:'暗紫',colors:[0x542278,0x783bad]},
  cyan:{label:'科技青',colors:[0x25e6ff,0x8dfce8]},
  blue:{label:'极光蓝',colors:[0x489dff,0x99e7ff]},
  pink:{label:'浪漫粉',colors:[0xff58c7,0xffac9e]},
})
export const writingStroke=Object.freeze({width:1.8,glowWidth:2.8,glowOpacity:.035})
export function writingGroundPaths(strokes,{x,y,z=90,size}){
  // Human at robot -X: left-to-right is -Y and glyph top is +X.
  return strokes.map(stroke=>stroke.map(([a,b])=>[x+b*size,y-a*size,z,180,0,0]))
}
