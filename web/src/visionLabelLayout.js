// Place labels in image coordinates, sharing the same layout in both viewers.
export function layoutVisionLabels(objects,width,height,textOf=object=>object.id){
  const font=Math.max(10,width/65),padding=3,lineHeight=font+6,placed=[]
  const intersects=(a,b)=>a.x<b.x+b.width+3 && a.x+a.width+3>b.x && a.y<b.y+b.height+3 && a.y+a.height+3>b.y
  return objects.map(object=>{
    const text=String(textOf(object)),boxWidth=Math.min(width-2*padding,[...text].reduce((n,c)=>n+font*(/[^\x00-\x7f]/.test(c)?1:.62),0)+6)
    const [cx,cy]=object.center_px,clampX=x=>Math.max(padding,Math.min(width-padding-boxWidth,x))
    const candidates=[]
    for(let row=0;row<Math.ceil(height/lineHeight);row++){
      for(const sign of [-1,1]){
        const y=Math.max(padding,Math.min(height-padding-lineHeight,cy+sign*(row+1)*(lineHeight+3)))
        for(const x of [cx+7,cx-boxWidth-7,(width-boxWidth)/2,padding,width-padding-boxWidth])candidates.push({x:clampX(x),y,width:boxWidth,height:lineHeight})
      }
    }
    const box=candidates.find(candidate=>placed.every(other=>!intersects(candidate,other))) || candidates[0]
    placed.push(box)
    return {...box,id:object.id,text,font,cx,cy,textY:box.y+font+1,lineX:Math.max(box.x,Math.min(box.x+box.width,cx)),lineY:Math.max(box.y,Math.min(box.y+box.height,cy))}
  })
}
