// Screen composition only. Robot base, ground and all task ranges stay fixed.
import {frontTaskView} from './taskFrontView.js'
export function textWritingView(){return frontTaskView({phi:.72,r:4.3,target:[-.18,.18,0],fov:32})}
