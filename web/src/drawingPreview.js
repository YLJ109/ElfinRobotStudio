// Local intent geometry only; this never grants execution or substitutes for
// the backend's feasible TCP trajectory.
import {drawingConfig,drawingRange,drawingView} from './drawingTask.js'
import {frontTaskView} from './taskFrontView.js'
export function drawingToolFrame(center,direction,mode='sim'){
 const pose=center.slice()
 // The geometry is turned about base Z; apply that same world rotation to
 // the pen frame, without rewriting the stored range or real TCP base.
 if(mode==='sim'&&typeof pose[5]==='number'&&Number.isFinite(pose[5])&&typeof direction==='number'&&Number.isFinite(direction))pose[5]=((pose[5]+direction+180)%360+360)%360-180
 return pose
}
export function localDrawingPreview(value){
 const config=drawingConfig(value,'sim'),center=config.center,shape=config.shape
 let rows=[]
 if(['nod','shake'].includes(shape)){
  const axis=shape==='nod'?3:5
  rows=[center.slice(),... [1,-1,0].map(sign=>{const p=center.slice();p[axis]+=sign*config.angle_deg;return p})]
 }else{
  let points
  if(['square','rectangle'].includes(shape))points=[[-.5,-.5],[.5,-.5],[.5,.5],[-.5,.5],[-.5,-.5]]
  else if(shape==='circle')points=Array.from({length:33},(_,i)=>[.5*Math.cos(2*Math.PI*i/32),.5*Math.sin(2*Math.PI*i/32)])
  else {
   points=Array.from({length:33},(_,i)=>{const t=2*Math.PI*i/32;return [16*Math.sin(t)**3,13*Math.cos(t)-5*Math.cos(2*t)-2*Math.cos(3*t)-Math.cos(4*t)]})
   const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys)
   points=points.map(([x,y])=>[(x-(xmin+xmax)/2)/(xmax-xmin),(y-(ymin+ymax)/2)/(ymax-ymin)])
  }
  const [a,b]=({xy:[0,1],xz:[0,2],yz:[1,2]})[config.plane]
  rows=points.map(([x,y])=>{const p=center.slice();p[a]+=x*config.width_mm;p[b]+=y*config.height_mm;return p})
 }
 const angle=config.direction*Math.PI/180,co=Math.round(Math.cos(angle)),si=Math.round(Math.sin(angle))
 for(const p of rows){const x=p[0]-center[0],y=p[1]-center[1];p[0]=center[0]+co*x-si*y;p[1]=center[1]+si*x+co*y}
 return {path:rows,bounds:drawingRange(config.range_center_mm,config.range_size_mm,'sim',config.direction),config}
}
export function frontDrawingView(bounds,plane,framing){
 const view=drawingView(bounds,plane,framing)
 return view?frontTaskView({...view,phi:1.15}):null
}
