// Trace actual rasterized glyph boundaries; no character-shaped substitute paths.
export function writingText(value) {
  if(typeof value!=='string')throw Error('请输入文字')
  const text=value.trim(),letters=Array.from(text)
  if(!letters.length||letters.length>5||/[\u0000-\u001f\u007f]/u.test(text))throw Error('请输入1～5个字符')
  return text
}
function distance(p,a,b){const x=b[0]-a[0],y=b[1]-a[1],d=x*x+y*y,t=d?Math.max(0,Math.min(1,((p[0]-a[0])*x+(p[1]-a[1])*y)/d)):0;return Math.hypot(p[0]-a[0]-t*x,p[1]-a[1]-t*y)}
function simplify(points,epsilon){
  if(points.length<=2)return points
  const kept=new Uint8Array(points.length);kept[0]=kept[points.length-1]=1;const stack=[[0,points.length-1]]
  while(stack.length){const [a,b]=stack.pop();let maximum=epsilon,index=-1;for(let i=a+1;i<b;i++){const d=distance(points[i],points[a],points[b]);if(d>maximum){maximum=d;index=i}}if(index>=0){kept[index]=1;stack.push([a,index],[index,b])}}
  return points.filter((_,i)=>kept[i])
}
export function bitmapContours(alpha,width,height,{epsilon=.85}={}){
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<2||height<2||width*height>300000||alpha.length!==width*height)throw Error('字形图像无效')
  const edges=new Map(),stride=width+1
  const filled=(x,y)=>x>=0&&x<width&&y>=0&&y<height&&alpha[y*width+x]>=128
  const edge=(x,y,a,b)=>{const key=y*stride+x,end=b*stride+a;let rows=edges.get(key);if(!rows)edges.set(key,rows=[]);rows.push(end)}
  for(let y=0;y<height;y++)for(let x=0;x<width;x++)if(filled(x,y)){
    if(!filled(x,y-1))edge(x,y,x+1,y)
    if(!filled(x+1,y))edge(x+1,y,x+1,y+1)
    if(!filled(x,y+1))edge(x+1,y+1,x,y+1)
    if(!filled(x-1,y))edge(x,y+1,x,y)
  }
  const contours=[]
  while(edges.size){const first=edges.keys().next().value,path=[];let key=first,steps=0
    do{path.push([key%stride,Math.floor(key/stride)]);const rows=edges.get(key);if(!rows?.length)break;key=rows.pop();if(!rows.length)edges.delete(path.at(-1)[1]*stride+path.at(-1)[0]);if(++steps>200000)throw Error('字形轮廓过于复杂')}while(key!==first)
    if(key!==first||path.length<5)continue
    path.push(path[0]);const reduced=simplify(path,epsilon)
    if(reduced.length>=4)contours.push(reduced)
  }
  if(!contours.length||contours.length>256||contours.reduce((n,p)=>n+p.length,0)>1800)throw Error('字形过于复杂，请减少文字或更换字符')
  return contours
}
export const writingFonts=Object.freeze({
  thin:{label:'纤细黑体',font:'300 112px "Microsoft YaHei Light", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif'},
  song:{label:'宋体',font:'400 112px "SimSun", "Noto Serif CJK SC", serif'},
  mono:{label:'等宽字体',font:'300 112px "Consolas", "Microsoft YaHei", monospace'},
})
export function rasterText(text,fontStyle='thin'){
  text=writingText(text)
  if(!Object.hasOwn(writingFonts,fontStyle))throw Error('请选择有效字体')
  const count=Array.from(text).length,size=112,pad=8,width=count*120+pad*2,height=144
  const canvas=new OffscreenCanvas(width,height),ctx=canvas.getContext('2d',{willReadFrequently:true})
  if(!ctx)throw Error('当前浏览器无法生成字形')
  ctx.font=writingFonts[fontStyle].font;ctx.textBaseline='alphabetic';ctx.fillStyle='#fff'
  let x=pad
  for(const letter of Array.from(text)){ctx.fillText(letter,x,116);x+=Math.max(ctx.measureText(letter).width,letter===' '?size*.35:0)+2}
  const bytes=ctx.getImageData(0,0,width,height).data,alpha=new Uint8Array(width*height)
  let xmin=width,xmax=0,ymin=height,ymax=0
  for(let i=0;i<alpha.length;i++){alpha[i]=bytes[i*4+3];if(alpha[i]>=128){const a=i%width,b=Math.floor(i/width);xmin=Math.min(xmin,a);xmax=Math.max(xmax,a);ymin=Math.min(ymin,b);ymax=Math.max(ymax,b)}}
  if(xmax<=xmin||ymax<=ymin)throw Error('这些字符没有可书写的字形')
  const strokes=bitmapContours(alpha,width,height).map(path=>path.map(([a,b])=>[(a-(xmin+xmax)/2)/size,((ymin+ymax)/2-b)/size]))
  return {text,strokes,em_width:(xmax-xmin)/size,em_height:(ymax-ymin)/size}
}
