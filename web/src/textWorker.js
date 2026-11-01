import {rasterText} from './textContours.js'
self.onmessage=({data})=>{try{self.postMessage({id:data.id,ok:true,...rasterText(data.text,data.fontStyle)})}catch(error){self.postMessage({id:data.id,ok:false,why:error.message})}}
