import {writingInk,writingStroke,writingDefaultInk} from './writingConfig.js'
import * as THREE from 'three'
import {LineSegments2} from 'three/addons/lines/LineSegments2.js'
import {LineSegmentsGeometry} from 'three/addons/lines/LineSegmentsGeometry.js'
import {LineMaterial} from 'three/addons/lines/LineMaterial.js'

// Visual projection only. Millimetre robot coordinates -> existing Three world metres.
export function createDrawingVisual({scene,now=()=>performance.now(),maxPoints=4096,particleCount=96}={}) {
  if(!scene?.add)throw Error('drawing scene required')
  const capacity=Number.isFinite(maxPoints)?Math.min(4096,Math.max(2,Math.floor(maxPoints))):4096,stars=Number.isFinite(particleCount)?Math.min(128,Math.max(1,Math.floor(particleCount))):96
  const group=new THREE.Group();group.name='drawing-visual';scene.add(group)
  const previewArray=new Float32Array(capacity*3),actualArray=new Float32Array((capacity-1)*6),directionArray=new Float32Array(6)
  const compositeArray=new Float32Array((capacity+64)*6),compositeColors=new Float32Array((capacity+64)*6),frameArray=new Float32Array(64*6)
  const previewGeometry=new THREE.BufferGeometry();previewGeometry.setAttribute('position',new THREE.BufferAttribute(compositeArray,3).setUsage(THREE.DynamicDrawUsage));previewGeometry.setAttribute('color',new THREE.BufferAttribute(compositeColors,3).setUsage(THREE.DynamicDrawUsage));previewGeometry.setDrawRange(0,0)
  const previewMaterial=new THREE.LineBasicMaterial({color:0xffffff,vertexColors:true,transparent:true,opacity:.78,depthWrite:false,depthTest:false,toneMapped:false})
  const preview=new THREE.LineSegments(previewGeometry,previewMaterial);preview.frustumCulled=false;group.add(preview)
  const actualGeometry=new LineSegmentsGeometry(),actualBuffer=new THREE.InstancedInterleavedBuffer(actualArray,6).setUsage(THREE.DynamicDrawUsage)
  actualGeometry.setAttribute('instanceStart',new THREE.InterleavedBufferAttribute(actualBuffer,3,0));actualGeometry.setAttribute('instanceEnd',new THREE.InterleavedBufferAttribute(actualBuffer,3,3));actualGeometry.instanceCount=0
  const strokeColors=new Float32Array((capacity-1)*6),strokeColorBuffer=new THREE.InstancedInterleavedBuffer(strokeColors,6).setUsage(THREE.DynamicDrawUsage)
  actualGeometry.setAttribute('instanceColorStart',new THREE.InterleavedBufferAttribute(strokeColorBuffer,3,0));actualGeometry.setAttribute('instanceColorEnd',new THREE.InterleavedBufferAttribute(strokeColorBuffer,3,3))
  const pink=new THREE.Color(0xff26b4),peach=new THREE.Color(0xffbc83)
  const lineMaterial=new LineMaterial({color:0xffffff,vertexColors:true,linewidth:6,transparent:true,opacity:.95,depthWrite:false,depthTest:false,toneMapped:false})
  const glowMaterial=new LineMaterial({color:0xffffff,vertexColors:true,linewidth:16,transparent:true,opacity:.22,depthWrite:false,depthTest:false,toneMapped:false,blending:THREE.AdditiveBlending})
  const glow=new LineSegments2(actualGeometry,glowMaterial),actual=new LineSegments2(actualGeometry,lineMaterial)
  for(const line of [glow,actual]){line.frustumCulled=false;line.visible=false;group.add(line)}
  const directionGeometry=new THREE.BufferGeometry();directionGeometry.setAttribute('position',new THREE.BufferAttribute(directionArray,3).setUsage(THREE.DynamicDrawUsage))
  const directionMaterial=new THREE.LineBasicMaterial({color:0xffb2df,depthWrite:false,depthTest:false,toneMapped:false}),direction=new THREE.Line(directionGeometry,directionMaterial);direction.frustumCulled=false;direction.visible=false;group.add(direction)
  const starPositions=new Float32Array(stars*3),seeds=new Float32Array(stars)
  for(let i=0;i<stars;i++){const angle=i*2.39996323,r=.012+.045*((i*37%stars)/stars);starPositions[i*3]=Math.cos(angle)*r;starPositions[i*3+1]=.006+.045*((i*17%stars)/stars);starPositions[i*3+2]=Math.sin(angle)*r;seeds[i]=(i+.5)/stars}
  const starGeometry=new THREE.BufferGeometry();starGeometry.setAttribute('position',new THREE.BufferAttribute(starPositions,3));starGeometry.setAttribute('seed',new THREE.BufferAttribute(seeds,1))
  const starMaterial=new THREE.ShaderMaterial({transparent:true,depthWrite:false,depthTest:false,toneMapped:false,blending:THREE.AdditiveBlending,uniforms:{age:{value:0}},
    vertexShader:'attribute float seed; uniform float age; varying float alpha; void main(){float t=age/2800.; vec3 p=position*(.4+t*1.7);p.y+=t*.045;vec4 v=modelViewMatrix*vec4(p,1.);gl_Position=projectionMatrix*v;gl_PointSize=clamp(4.+6.*sin(seed*29.+age*.005),2.,11.);alpha=(1.-t)*(.35+.65*pow(sin(seed*31.+age*.004),2.));}',
    fragmentShader:'varying float alpha;void main(){vec2 p=abs(gl_PointCoord-.5);float core=exp(-dot(p,p)*42.);float rays=exp(-min(p.x,p.y)*45.)*max(0.,1.-max(p.x,p.y)*2.);float a=(core+rays*.5)*alpha;gl_FragColor=vec4(1.,.72,.89,a);}'})
  const particles=new THREE.Points(starGeometry,starMaterial);particles.visible=false;particles.frustumCulled=false;group.add(particles)
  // Trajectory is an overlay of measured motion, so the tool cannot obscure
  // the stroke it just traced. Fixed passes keep it legible without postprocessing.
  for(const [index,object] of [preview,glow,actual,direction,particles].entries())object.renderOrder=20+index
  const previous=new Float64Array(6)
  // Filled glyphs are clipped to the original contours (including holes), then
  // revealed only near measured pen motion. No clock or completion shortcut.
  const fillBudget=16384,fillCell=6,fillRadius=6,fillVertices=21,reliefHeight=8,reliefDuration=500
  const fillMaterial=new THREE.MeshStandardMaterial({color:0xffffff,vertexColors:true,emissive:0x542278,emissiveIntensity:.65,roughness:.3,metalness:.18,side:THREE.DoubleSide,depthWrite:true,toneMapped:false})
  const fillGeometry=new THREE.BufferGeometry(),fillPositions=new Float32Array(fillBudget*fillVertices*3),fillColors=new Float32Array(fillPositions.length)
  const fillNormals=new Int8Array(fillPositions.length)
  const fillTopColor=new THREE.Color(0x542278),fillSideColor=new THREE.Color(0x35124e),growing=new Map()
  const topIndices=[0,1,2,5,7,8,11,13,14,17,19,20]
  for(let i=0;i<fillBudget*fillVertices;i++){
    const color=i%fillVertices<3?fillTopColor:fillSideColor
    fillColors.set([color.r,color.g,color.b],i*3)
  }
  fillGeometry.setAttribute('position',new THREE.BufferAttribute(fillPositions,3).setUsage(THREE.DynamicDrawUsage));fillGeometry.setDrawRange(0,0)
  fillGeometry.setAttribute('color',new THREE.BufferAttribute(fillColors,3))
  fillGeometry.setAttribute('normal',new THREE.BufferAttribute(fillNormals,3,true).setUsage(THREE.DynamicDrawUsage))
  const fill=new THREE.Mesh(fillGeometry,fillMaterial);fill.name='writing-fill';fill.frustumCulled=false;fill.visible=false;fill.renderOrder=19;group.add(fill)
  let fillTriangles=[],fillGrid=new Map(),fillShown=new Uint8Array(0),filled=0,fillBase=.8
  function resetFill(){fillTriangles=[];fillGrid.clear();fillShown=new Uint8Array(0);growing.clear();filled=0;fillBase=.8;fill.visible=false;fillGeometry.setDrawRange(0,0)}
  function growFill(timeMs){
    if(!growing.size)return false
    for(const [index,birth] of growing){
      const t=Math.max(0,Math.min(1,(timeMs-birth)/reliefDuration)),height=(fillBase+reliefHeight*t*t*(3-2*t))/1000
      for(const vertex of topIndices)fillPositions[index*fillVertices*3+vertex*3+1]=height
      if(t>=1)growing.delete(index)
    }
    fillGeometry.attributes.position.needsUpdate=true;return true
  }
  function prepareFill(paths){
    if(binding?.shape!=='text'||!paths)return
    fillBase=(paths.find(path=>path.length)?.[0]?.[2]??1)-.2
    const outline=new THREE.ShapePath()
    for(const path of paths){
      if(path.length<4)continue
      outline.moveTo(path[0][0],path[0][1]);for(const point of path.slice(1))outline.lineTo(point[0],point[1])
    }
    const largest=outline.subPaths.map(path=>path.getPoints()).sort((a,b)=>Math.abs(THREE.ShapeUtils.area(b))-Math.abs(THREE.ShapeUtils.area(a)))[0]
    const geometry=new THREE.ShapeGeometry(outline.toShapes(largest?!THREE.ShapeUtils.isClockWise(largest):false)),position=geometry.getAttribute('position'),index=geometry.index
    const point=i=>[position.getX(i),position.getY(i)],stack=[]
    for(let i=0;i<(index?.count??position.count);i+=3)stack.push([0,1,2].map(j=>point(index?index.getX(i+j):i+j)))
    geometry.dispose()
    while(stack.length&&fillTriangles.length<fillBudget){
      const triangle=stack.pop(),lengths=triangle.map((a,i)=>Math.hypot(a[0]-triangle[(i+1)%3][0],a[1]-triangle[(i+1)%3][1])),longest=Math.max(...lengths)
      if(longest>fillCell&&fillTriangles.length+stack.length+2<=fillBudget){
        const i=lengths.indexOf(longest),a=triangle[i],b=triangle[(i+1)%3],c=triangle[(i+2)%3],middle=[(a[0]+b[0])/2,(a[1]+b[1])/2]
        stack.push([a,middle,c],[middle,b,c]);continue
      }
      const cx=triangle.reduce((n,p)=>n+p[0],0)/3,cy=triangle.reduce((n,p)=>n+p[1],0)/3,id=fillTriangles.length,key=`${Math.floor(cx/fillCell)},${Math.floor(cy/fillCell)}`
      fillTriangles.push({triangle,cx,cy});if(!fillGrid.has(key))fillGrid.set(key,[]);fillGrid.get(key).push(id)
    }
    fillShown=new Uint8Array(fillTriangles.length)
  }
  function revealFill(a,b){
    if(!fillTriangles.length)return
    const dx=b[0]-a[0],dy=b[1]-a[1],distance=dx*dx+dy*dy
    for(let x=Math.floor((Math.min(a[0],b[0])-fillRadius)/fillCell);x<=Math.floor((Math.max(a[0],b[0])+fillRadius)/fillCell);x++)for(let y=Math.floor((Math.min(a[1],b[1])-fillRadius)/fillCell);y<=Math.floor((Math.max(a[1],b[1])+fillRadius)/fillCell);y++){
      for(const id of fillGrid.get(`${x},${y}`)||[]){
        if(fillShown[id])continue
        const {triangle,cx,cy}=fillTriangles[id],t=distance?Math.max(0,Math.min(1,((cx-a[0])*dx+(cy-a[1])*dy)/distance)):0
        if(Math.hypot(cx-a[0]-dx*t,cy-a[1]-dy*t)>fillRadius)continue
        fillShown[id]=1
        const vertices=[...triangle]
        for(let j=0;j<3;j++){const a=triangle[j],b=triangle[(j+1)%3];vertices.push(a,b,b,a,b,a)}
        for(let j=0;j<fillVertices;j++)write(fillPositions,filled*fillVertices*3+j*3,[...vertices[j],fillBase])
        for(let j=0;j<3;j++)fillNormals[filled*fillVertices*3+j*3+1]=127
        for(let j=0;j<3;j++){
          const a=triangle[j],b=triangle[(j+1)%3],dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy)||1
          for(let k=0;k<6;k++){
            const offset=filled*fillVertices*3+(3+j*6+k)*3
            fillNormals[offset]=Math.round(dy/length*127);fillNormals[offset+2]=Math.round(dx/length*127)
          }
        }
        growing.set(filled,now())
        filled++
      }
    }
    fillGeometry.setDrawRange(0,filled*fillVertices);fillGeometry.attributes.position.needsUpdate=true;fillGeometry.attributes.normal.needsUpdate=true;fill.visible=filled>0
  }
  let previewBreaks=new Set(),lastStroke=null,paletteKey=''
  function palette(value){
    const key=value.shape==='text'?'text:'+(value.ink_style||writingDefaultInk):'drawing'
    if(key===paletteKey)return
    paletteKey=key
    const colors=key.startsWith('text:')?writingInk.cyan.colors:[0xff26b4,0xffbc83]
    if(key.startsWith('text:')){
      fillTopColor.set((writingInk[value.ink_style]||writingInk[writingDefaultInk]).colors[0]);fillSideColor.copy(fillTopColor).multiplyScalar(.5)
      fillMaterial.emissive.copy(fillTopColor)
      for(let i=0;i<fillBudget*fillVertices;i++){
        const color=i%fillVertices<3?fillTopColor:fillSideColor,offset=i*3
        fillColors[offset]=color.r;fillColors[offset+1]=color.g;fillColors[offset+2]=color.b
      }
      fillGeometry.attributes.color.needsUpdate=true
    }
    pink.set(colors[0]);peach.set(colors[1]);lineMaterial.linewidth=key.startsWith('text:')?writingStroke.width:6;glowMaterial.linewidth=key.startsWith('text:')?writingStroke.glowWidth:16;glowMaterial.opacity=key.startsWith('text:')?writingStroke.glowOpacity:.22
    // This simulation ink is an overlay of measured motion. Render it after
    // robot/tool meshes so their depth cannot erase finished or partial ink.
    lineMaterial.depthTest=glowMaterial.depthTest=fillMaterial.depthTest=false
    fillMaterial.depthWrite=false
    for(let i=0;i<segments;i++)for(let k=0;k<2;k++)for(let j=0;j<3;j++)strokeColors[i*6+k*3+j]=pink.getComponent?pink.getComponent(j):[pink.r,pink.g,pink.b][j]
    strokeColorBuffer.needsUpdate=true;rebuildPreview()
  }
  let binding=null,disposed=false,count=0,segments=0,previewCount=0,showPreview=true,havePrevious=false,lastSeq=-1,eligible=false,terminated=false,celebrated=false,celebrationStart=null,truncated=false,lastX=0,lastY=0,lastZ=0,lastColorT=0,frameOwner=null,frameSession=null,frameSegments=0,frameBounds=null,compositeSegments=0
  const finite=p=>Array.isArray(p)&&p.length>=3&&Number.isFinite(p[0])&&Number.isFinite(p[1])&&Number.isFinite(p[2])
  function write(array,index,p){array[index]=p[0]/1000;array[index+1]=p[2]/1000;array[index+2]=-p[1]/1000}
  function rebuildPreview(){
    compositeSegments=0
    for(let i=1;showPreview&&i<previewCount;i++){
      if(previewBreaks.has(i))continue
      const offset=compositeSegments++*6
      for(let j=0;j<3;j++){compositeArray[offset+j]=previewArray[(i-1)*3+j];compositeArray[offset+3+j]=previewArray[i*3+j]}
      for(let j=0;j<6;j++)compositeColors[offset+j]=paletteKey.startsWith('text:')?[pink.r,pink.g,pink.b][j%3]:(j%3===0?1:j%3===1?.58:.82)
    }
    compositeArray.set(frameArray.subarray(0,frameSegments*6),compositeSegments*6)
    for(let i=0;i<frameSegments*6;i++)compositeColors[compositeSegments*6+i]=i%3===0?.12:i%3===1?.83:.94
    compositeSegments+=frameSegments;previewGeometry.setDrawRange(0,compositeSegments*2)
    previewGeometry.attributes.position.needsUpdate=true;previewGeometry.attributes.color.needsUpdate=true
  }
  function reset(){resetFill();count=segments=previewCount=0;previewBreaks=new Set();lastStroke=null;havePrevious=false;lastSeq=-1;eligible=terminated=celebrated=truncated=false;celebrationStart=null;actualGeometry.instanceCount=0;particles.visible=actual.visible=glow.visible=direction.visible=false;lastColorT=0;rebuildPreview()}
  function matches(value){return binding&&value.owner===binding.owner&&value.plan_id===binding.plan_id&&(value.session??null)===binding.session}
  function clear(owner){
    if(disposed)return false
    const owned=owner===undefined||owner===binding?.owner||owner===frameOwner
    if(!owned||(!binding&&!frameOwner))return false
    if(owner===undefined||owner===frameOwner){frameOwner=frameSession=frameBounds=null;frameSegments=0}
    if(owner===undefined||owner===binding?.owner){reset();binding=null}else rebuildPreview()
    return true
  }
  function setBounds(value){
    if(disposed||!value?.owner)return false
    if(value.mode!=='sim'){clear(value.owner);return false}
    const axes=value.plane==='xy'?[0,1,2]:value.plane==='yz'?[1,2,0]:[0,2,1]
    let low=value.bounds?.min,high=value.bounds?.max
    if(!low&&!high&&finite(value.center)){
      const size=value.size_mm??value.range_size_mm
      low=value.center.slice(0,3);high=low.slice()
      if(size!==undefined){
        if(!Array.isArray(size)||size.length!==3||!size.every(v=>Number.isFinite(v)&&v>0&&v<=5000))return false
        for(let i=0;i<3;i++){low[i]-=size[i]/2;high[i]+=size[i]/2}
      }else{
        const width=value.width_mm,height=value.height_mm,depth=value.depth_mm??20
        if(![width,height,depth].every(v=>Number.isFinite(v)&&v>0&&v<=5000))return false
        for(const [index,span] of [[axes[0],width],[axes[1],height],[axes[2],depth]]){low[index]-=span/2;high[index]+=span/2}
      }
    }
    if(!finite(low)||!finite(high)||low.some((v,i)=>Math.abs(v)>10000||Math.abs(high[i])>10000||high[i]<=v||high[i]-v>5000))return false
    const key=JSON.stringify([low,high,value.plane])
    if(frameOwner===value.owner&&frameSession===(value.session??null)&&frameBounds?.key===key)return false
    if((frameOwner&&frameOwner!==value.owner)||(binding&&binding.owner!==value.owner)||(binding&&value.session!==undefined&&binding.session!==value.session)){frameSegments=0;reset();binding=null}
    frameOwner=value.owner;frameSession=value.session??null;frameBounds={min:low.slice(),max:high.slice(),key};frameSegments=0
    function segment(a,b){const offset=frameSegments++*6;write(frameArray,offset,a);write(frameArray,offset+3,b)}
    const corners=[]
    for(let i=0;i<8;i++)corners.push([i&1?high[0]:low[0],i&2?high[1]:low[1],i&4?high[2]:low[2]])
    for(let i=0;i<8;i++)for(const bit of [1,2,4])if(!(i&bit))segment(corners[i],corners[i|bit])
    const digits=[[[.5,1,.5,0]],[[0,1,1,1],[1,1,1,.5],[1,.5,0,.5],[0,.5,0,0],[0,0,1,0]],[[0,1,1,1],[1,1,1,0],[0,.5,1,.5],[0,0,1,0]],[[0,1,0,.5],[0,.5,1,.5],[1,1,1,0]]]
    const glyph=Math.max(3,Math.min(9,(high[axes[0]]-low[axes[0]])*.045)),middle=(low[axes[2]]+high[axes[2]])/2
    for(let i=0;i<4;i++){
      const left=i===0||i===3,bottom=i>=2,base=[0,0,0];base[axes[0]]=left?low[axes[0]]+glyph*.4:high[axes[0]]-glyph*1.4;base[axes[1]]=bottom?low[axes[1]]+glyph*.4:high[axes[1]]-glyph*1.4;base[axes[2]]=middle
      for(const line of digits[i]){const a=base.slice(),b=base.slice();a[axes[0]]+=line[0]*glyph;a[axes[1]]+=line[1]*glyph;b[axes[0]]+=line[2]*glyph;b[axes[1]]+=line[3]*glyph;segment(a,b)}
    }
    rebuildPreview();return true
  }
  function setPreview(value){
    if(disposed)return false
    if(!value||value.mode!=='sim'||!value.owner||!value.plan_id||!Array.isArray(value.path)||value.path.some(p=>!finite(p))){clear();return false}
    palette(value)
    const nextPreview=value.preview_visible!==false&&!['running','paused','completed','cancelled','stopped','aborted'].includes(value.phase)
    if(showPreview!==nextPreview){showPreview=nextPreview;rebuildPreview()}
    if(value.bounds)setBounds({...value,bounds:value.bounds})
    if(matches(value))return false
    if(frameOwner&&(frameOwner!==value.owner||frameSession!==(value.session??null))){frameOwner=frameSession=frameBounds=null;frameSegments=0}
    reset();binding={owner:value.owner,plan_id:value.plan_id,session:value.session??null,shape:value.shape,mode:value.mode}
    const paths=Array.isArray(value.trace_paths)&&value.trace_paths.length?value.trace_paths:null
    const intended=paths?paths.flat():value.path
    if(paths){let offset=0;for(const path of paths){if(path.some(p=>!finite(p))){clear();return false}previewBreaks.add(offset);offset+=path.length}}
    prepareFill(paths)
    previewCount=Math.min(intended.length,capacity);truncated=intended.length>capacity
    // Uniformly retain the whole intended outline if an oversized path is supplied.
    for(let i=0;i<previewCount;i++)write(previewArray,i*3,intended[Math.floor(i*(intended.length-1)/Math.max(1,previewCount-1))])
    rebuildPreview();return true
  }
  function sample(value){
    if(Array.isArray(value.actual_samples)&&value.valid&&matches(value)){
      for(const row of value.actual_samples){
        if(!Number.isSafeInteger(row.seq)||row.seq<=lastSeq||!finite(row.point))continue
        if(row.stroke!==lastStroke){havePrevious=false;lastStroke=row.stroke}
        sample({...value,actual_samples:undefined,tcp:row.point,frame_seq:row.seq,phase:'running',trace_active:true})
      }
      if(value.phase==='running'||value.phase==='paused')return true
      value={...value,actual_samples:undefined,frame_seq:undefined}
    }
    if(disposed||!matches(value))return false
    if(value.mode!==undefined&&value.mode!=='sim'){clear(value.owner);return false}
    if(!value.valid||!finite(value.tcp)){eligible=false;havePrevious=false;particles.visible=false;celebrationStart=null;return false}
    if(value.frame_seq!==undefined&&(!Number.isSafeInteger(value.frame_seq)||value.frame_seq<lastSeq))return false
    if(['failed','cancelled','aborted','stopped'].includes(value.phase)){terminated=true;eligible=false;particles.visible=false;celebrationStart=null;havePrevious=false;return false}
    if(value.phase==='running'&&!terminated){
      if(value.trace_active===false){havePrevious=false;return false}
      eligible=true
      if(value.frame_seq!==undefined&&value.frame_seq===lastSeq)return false
      if(value.frame_seq!==undefined)lastSeq=value.frame_seq
      const p=value.tcp,dx=havePrevious?p[0]-previous[0]:0,dy=havePrevious?p[1]-previous[1]:0,dz=havePrevious?p[2]-previous[2]:0
      if(binding.shape==='text')revealFill(havePrevious?previous:p,p)
      let angularChange=false
      for(let i=3;i<6;i++)if(Number.isFinite(p[i])&&Math.abs(p[i]-previous[i])>.05)angularChange=true
      if(havePrevious&&dx*dx+dy*dy+dz*dz<.09&&!angularChange)return false
      if(havePrevious&&segments<capacity-1&&dx*dx+dy*dy+dz*dz>=.09){write(actualArray,segments*6,previous);write(actualArray,segments*6+3,p)
        // Presentation offset keeps the cyan ink legible above the shallow
        // relief; measured TCP and robot trajectories remain unchanged.
        if(binding.shape==='text'){actualArray[segments*6+1]+=.009;actualArray[segments*6+4]+=.009}
        const t=Number.isFinite(value.progress?.completed)&&value.progress.total>0?Math.max(0,Math.min(1,value.progress.completed/value.progress.total)):lastColorT
        for(let j=0;j<3;j++){const a=j===0?pink.r:j===1?pink.g:pink.b,b=j===0?peach.r:j===1?peach.g:peach.b;strokeColors[segments*6+j]=a+(b-a)*lastColorT;strokeColors[segments*6+3+j]=a+(b-a)*t}
        lastColorT=t;strokeColorBuffer.needsUpdate=true;segments++;actualGeometry.instanceCount=segments;actualBuffer.needsUpdate=true;actual.visible=glow.visible=true}
      else if(segments>=capacity-1)truncated=true
      for(let i=0;i<6;i++)previous[i]=Number.isFinite(p[i])?p[i]:0
      havePrevious=true;count=Math.min(capacity,count+1);lastX=p[0]/1000;lastY=p[2]/1000;lastZ=-p[1]/1000
      if(['nod','shake'].includes(binding.shape)&&p.length>=6&&p.slice(3,6).every(Number.isFinite)){
        // The short indicator is the measured TCP's local Z direction, not a synthetic circle.
        const rx=p[3]*Math.PI/180,ry=p[4]*Math.PI/180,rz=p[5]*Math.PI/180
        const x=Math.cos(rz)*Math.sin(ry)*Math.cos(rx)+Math.sin(rz)*Math.sin(rx),y=Math.sin(rz)*Math.sin(ry)*Math.cos(rx)-Math.cos(rz)*Math.sin(rx),z=Math.cos(ry)*Math.cos(rx)
        directionArray[0]=lastX;directionArray[1]=lastY;directionArray[2]=lastZ;directionArray[3]=lastX+x*.035;directionArray[4]=lastY+z*.035;directionArray[5]=lastZ-y*.035;directionGeometry.attributes.position.needsUpdate=true;direction.visible=true
      }
      return true
    }
    if(value.phase==='completed'&&!terminated&&!celebrated&&eligible&&count>=2&&Number.isSafeInteger(value.progress?.total)&&value.progress.total>0&&value.progress.completed===value.progress.total){
      celebrated=true;celebrationStart=now();particles.position.set(lastX,lastY,lastZ);particles.visible=true;starMaterial.uniforms.age.value=0;return true
    }
    return false
  }
  function tick(timeMs=now()){if(disposed)return false;const changed=growFill(timeMs);if(celebrationStart===null)return changed;const age=Math.max(0,timeMs-celebrationStart);if(age>=2800){particles.visible=false;celebrationStart=null;return changed}starMaterial.uniforms.age.value=age;return true}
  function resize(w,h){if(disposed)return;const width=Math.max(1,Number(w)||1),height=Math.max(1,Number(h)||1);lineMaterial.resolution.set(width,height);glowMaterial.resolution.set(width,height)}
  function info(){return {owner:binding?.owner??frameOwner??null,frameOwner,frameSegments,bounds:frameBounds?{min:frameBounds.min.slice(),max:frameBounds.max.slice()}:null,plan_id:binding?.plan_id??null,samples:count,lastTcp:count?Array.from(previous):null,segments,previewPoints:previewCount,capacity,particles:stars,fillTriangles:fillTriangles.length,filledTriangles:filled,fillBudget,reliefVertices:fillVertices*filled,reliefBytes:fillPositions.byteLength+fillColors.byteLength+fillNormals.byteLength,truncated,completedEffect:celebrated,animating:celebrationStart!==null||growing.size>0,drawCalls:(compositeSegments>0?1:0)+(segments?2:0)+(direction.visible?1:0)+(particles.visible?1:0)+(fill.visible?1:0),disposed}}
  function dispose(){if(disposed)return;disposed=true;group.removeFromParent();for(const resource of [previewGeometry,previewMaterial,actualGeometry,lineMaterial,glowMaterial,directionGeometry,directionMaterial,starGeometry,starMaterial,fillGeometry,fillMaterial])resource.dispose();resetFill();binding=frameOwner=frameSession=frameBounds=null;frameSegments=compositeSegments=0;celebrationStart=null}
  resize(1,1);return {setBounds,setPreview,sample,tick,resize,clear,info,dispose}
}
