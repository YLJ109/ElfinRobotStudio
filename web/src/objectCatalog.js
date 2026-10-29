// Original local geometry catalogue. kind is the conservative physics proxy;
// preset is a visual assembly entirely contained inside that proxy's envelope.
export const OBJECT_SURFACES = Object.freeze({
  painted: { label: '喷涂', metalness: .15, roughness: .7 }, steel: { label: '钢', metalness: .85, roughness: .28 },
  aluminum: { label: '铝', metalness: .7, roughness: .4 }, rubber: { label: '橡胶', metalness: 0, roughness: .95 },
  wood: { label: '木材', metalness: 0, roughness: .85 }, plastic: { label: '塑料', metalness: 0, roughness: .45 },
})
export const OBJECT_CATEGORIES = [{ id: 'all', label: '全部' }, { id: 'geometry', label: '基础形体' },
  { id: 'storage', label: '物流容器' }, { id: 'equipment', label: '工位设备' }, { id: 'fixture', label: '工件夹具' }, { id: 'safety', label: '隔离防护' }]
const box = (size, at = [0, 0, 0]) => ({ shape: 'box', size, at })
const cylinder = (size, at = [0, 0, 0], axis = 'z', shape = 'cylinder') => ({ shape, size, at, axis })
const posts = (z = -.05, height = .9) => [-.43, .43].flatMap(x => [-.43, .43].map(y => box([.1, .1, height], [x, y, z])))
const zoneParts = () => [box([1,1,.4],[0,0,-.3]),
  ...[-.48,.48].map(x=>box([.04,1,.6],[x,0,.2])),
  ...[-.48,.48].map(y=>box([.92,.04,.6],[0,y,.2]))]
export const OBJECT_CATALOG = [
  { id:'box',label:'长方体',category:'geometry',kind:'box',size:[160,160,160],surface:'painted',color:'#4fd8cf',tags:'box 方块 箱体' },
  { id:'sphere',label:'球体',category:'geometry',kind:'sphere',size:[120,120,120],surface:'rubber',color:'#e0a33b',tags:'sphere 球 半径' },
  { id:'cylinder',label:'圆柱',category:'geometry',kind:'cylinder',size:[100,100,240],surface:'steel',color:'#9aafc2',tags:'cylinder 圆柱体' },
  { id:'token_o',label:'圆环棋子 O',category:'fixture',kind:'box',size:[70,70,16],surface:'plastic',color:'#2fd6f0',tags:'token O 圆环 空心 井字棋 工件' },
  { id:'token_x',label:'叉形棋子 X',category:'fixture',kind:'box',size:[70,70,16],surface:'plastic',color:'#ffb020',tags:'token X 叉 井字棋 工件' },
  { id:'zone_a',label:'A 原料区',category:'fixture',kind:'box',size:[330,330,4],surface:'painted',color:'#368b99',tags:'zone A 原料区 分拣 棋子',parts:zoneParts() },
  { id:'zone_b',label:'B 放料区',category:'fixture',kind:'box',size:[330,330,4],surface:'painted',color:'#688da4',tags:'zone B 放料区 分拣 棋盘',parts:zoneParts() },
  { id:'pallet',label:'工业托盘',category:'storage',kind:'box',size:[800,600,140],surface:'wood',color:'#ba8b56',tags:'pallet 栈板 物流 木托',parts:[...[-.4,-.2,0,.2,.4].map(x=>box([.16,1,.2],[x,0,.4])),...[-.38,0,.38].map(y=>box([1,.16,.65],[0,y,-.075]))] },
  { id:'crate',label:'周转料箱',category:'storage',kind:'box',size:[500,400,300],surface:'plastic',color:'#3b82c4',tags:'crate bin 容器 料盒',parts:[box([1,1,.1],[0,0,-.45]),...[-.46,.46].map(x=>box([.08,1,1],[x,0,0])),...[-.46,.46].map(y=>box([.84,.08,1],[0,y,0]))] },
  { id:'workbench',label:'工业工作台',category:'equipment',kind:'box',size:[1000,650,750],surface:'steel',color:'#8da0ae',tags:'workbench 桌 台面 装配台',parts:[box([1,1,.1],[0,0,.45]),...posts(-.05,.9),box([.8,.06,.08],[0,0,-.2])] },
  { id:'conveyor',label:'滚筒输送台',category:'equipment',kind:'box',size:[1100,550,650],surface:'aluminum',color:'#9cadb6',tags:'conveyor 输送机 传送 辊道',parts:[...posts(-.1,.8),...[-.46,.46].map(y=>box([1,.08,.15],[0,y,.4])),...[-.4,-.24,-.08,.08,.24,.4].map(x=>cylinder([.12,.8,.1],[x,0,.4],'y'))] },
  { id:'column',label:'设备立柱',category:'equipment',kind:'box',size:[300,300,900],surface:'steel',color:'#a4b5c4',tags:'column 支柱 支架',parts:[box([1,1,.08],[0,0,-.46]),box([.25,.25,.85],[0,0,0]),box([.65,.65,.08],[0,0,.46])] },
  { id:'guardrail',label:'安全护栏',category:'safety',kind:'box',size:[1200,180,1000],surface:'painted',color:'#e0a33b',tags:'guardrail 围挡 栏杆 隔离 护栏',parts:[...[-.42,.42].map(x=>box([.08,.4,1],[x,0,0])),...[-.1,.38].map(z=>box([.9,.3,.08],[0,0,z])),...[-.42,.42].map(x=>box([.16,1,.04],[x,0,-.48]))] },
  { id:'workpiece',label:'阶梯工件',category:'fixture',kind:'box',size:[300,220,160],surface:'aluminum',color:'#b5c0cc',tags:'workpiece 零件 毛坯 阶梯',parts:[box([1,1,.3],[0,0,-.35]),box([.65,.8,.35],[-.1,0,-.025]),box([.3,.6,.35],[-.25,0,.325])] },
  { id:'drum',label:'工业料桶',category:'storage',kind:'cylinder',size:[220,220,600],surface:'painted',color:'#407ca3',tags:'drum barrel 桶 油桶 料桶',parts:[cylinder([.94,.94,.94]),...[-.47,.47].map(z=>cylinder([1,1,.06],[0,0,z])),...[-.22,.22].map(z=>cylinder([1,1,.04],[0,0,z]))] },
  { id:'cabinet',label:'电气机柜',category:'equipment',kind:'box',size:[600,450,1100],surface:'painted',color:'#9aa5af',tags:'cabinet 电控柜 机柜 箱柜',parts:[box([1,.88,1],[0,.06,0]),...[-.245,.245].map(x=>box([.47,.1,.94],[x,-.45,0])),box([.025,.08,.12],[.05,-.46,0])] },
  { id:'rack',label:'三层货架',category:'storage',kind:'box',size:[900,450,1200],surface:'steel',color:'#397b8e',tags:'rack shelf 货架 置物 架子',parts:[...posts(0,1),...[-.46,0,.46].map(z=>box([1,1,.06],[0,0,z]))] },
  { id:'hopper',label:'投料漏斗',category:'equipment',kind:'box',size:[600,600,900],surface:'aluminum',color:'#9caeb9',tags:'hopper 漏斗 投料 料斗',parts:[cylinder([1,1,.55],[0,0,.225],'z','hopper'),...posts(-.25,.5),cylinder([.22,.22,.2],[0,0,-.15])] },
  { id:'fixture',label:'夹具工装',category:'fixture',kind:'box',size:[350,280,220],surface:'steel',color:'#68878d',tags:'fixture jig 夹具 工装 夹持',parts:[box([1,1,.15],[0,0,-.425]),...[-.34,.34].map(x=>box([.16,.65,.7],[x,0,0])),box([.75,.2,.15],[0,0,.4]),cylinder([.18,.18,.4],[0,0,.15])] },
]
export const catalogEntry = id => OBJECT_CATALOG.find(item => item.id === id)
export function filterObjectCatalog(search = '', category = 'all') {
  const terms = String(search).trim().toLowerCase().split(/\s+/).filter(Boolean)
  return OBJECT_CATALOG.filter(item => (category === 'all' || item.category === category) && terms.every(term => `${item.id} ${item.label} ${item.tags}`.toLowerCase().includes(term)))
}
export function catalogSpec(id, xyz, appearance = {}) {
  const item = catalogEntry(id)
  if (!item || !Array.isArray(xyz) || xyz.length !== 3 || !xyz.every(v => Number.isFinite(v) && Math.abs(v) <= 1e6)) throw new Error('类型或位置无效')
  const color = appearance.color || item.color, surface = appearance.surface || item.surface
  if (!/^#[0-9a-f]{6}$/i.test(color) || !Object.hasOwn(OBJECT_SURFACES, surface)) throw new Error('颜色或材质无效')
  return { name:item.label,kind:item.kind,preset:item.id,surface,color,size:item.size.slice(),xyz:xyz.slice(),gravity:item.category==='geometry',floor:true,collide:true,floating:false }
}
export function objectLabel(object) { return catalogEntry(object?.preset || object?.kind)?.label || '物体' }
// Fixed catalogue artwork, independent of live snapshots and object appearance.
// Cache only known IDs so arbitrary search/input cannot grow this map.
const thumbnailCache = new Map()
export function catalogThumbnail(id) {
  if (thumbnailCache.has(id)) return thumbnailCache.get(id)
  const item = catalogEntry(id)
  if (!item) return ''
  const project = (x,y,z) => [48+(x-y)*28,56+(x+y)*12-z*38]
  const polygon = points => `<polygon points="${points.map(p=>project(...p).map(v=>v.toFixed(1)).join(',')).join(' ')}"/>`
  const parts = item.parts || [item.kind==='box' ? box([1,1,1]) : cylinder([1,1,1])]
  let shapes = parts.map(part => {
    const [x,y,z]=part.at,[w,d,h]=part.size
    if (part.shape!=='box') {
      const [cx,top]=project(x,y,z+h/2),[,bottom]=project(x,y,z-h/2)
      const taper=part.shape==='hopper'?.3:1
      return `<path d="M${cx-w*17},${top}L${cx-w*17*taper},${bottom}a${w*17*taper},${d*7*taper} 0 0 0 ${w*34*taper},0L${cx+w*17},${top}Z"/><ellipse cx="${cx}" cy="${top}" rx="${w*17}" ry="${d*7}"/>`
    }
    return polygon([[x-w/2,y-d/2,z+h/2],[x+w/2,y-d/2,z+h/2],[x+w/2,y+d/2,z+h/2],[x-w/2,y+d/2,z+h/2]])+polygon([[x-w/2,y-d/2,z-h/2],[x+w/2,y-d/2,z-h/2],[x+w/2,y-d/2,z+h/2],[x-w/2,y-d/2,z+h/2]])+polygon([[x+w/2,y-d/2,z-h/2],[x+w/2,y+d/2,z-h/2],[x+w/2,y+d/2,z+h/2],[x+w/2,y-d/2,z+h/2]])
  }).join('')
  if(item.id==='sphere')shapes='<circle cx="48" cy="42" r="24"/><path d="M24 42q24 13 48 0M48 18q-18 24 0 48" fill="none"/>'
  if(item.id==='token_o')shapes='<path fill-rule="evenodd" d="M48 16a27 27 0 1 1 0 54a27 27 0 1 1 0-54ZM48 27a16 16 0 1 0 0 32a16 16 0 1 0 0-32Z"/>'
  if(item.id==='token_x')shapes='<path d="M24 17L48 37L72 17L80 25L56 45L80 65L72 73L48 53L24 73L16 65L40 45L16 25Z"/>'
  if(item.id==='zone_a' || item.id==='zone_b')shapes+=`<text x="48" y="43" text-anchor="middle" fill-opacity="1" stroke="none" font-size="19" font-family="sans-serif">${item.id==='zone_a'?'A':'B'}</text>`
  const uri = 'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 88"><g fill="${item.color}" fill-opacity=".5" stroke="${item.color}" stroke-width="1.2" stroke-linejoin="round">${shapes}</g></svg>`)
  thumbnailCache.set(id, uri)
  return uri
}
