// Shared display defaults only. No motion ticket, device session or permission is stored.
export const INTERACTION_RANGE_FACTORY=Object.freeze({center:Object.freeze([-500,0,700]),size:Object.freeze([900,300,500]),direction:270,depthRange:Object.freeze([.06,.30])})
export const FOLLOW_RANGE_KEY='huayan.hand-follow.range-default.v1'
export const FOLLOW_DRAFT_KEY='huayan.hand-follow.draft.v1'
const finite=v=>typeof v==='number'&&Number.isFinite(v)
export function rotateInteractionCenter(center,from,to){
 if(!Array.isArray(center)||center.length!==3||!center.every(finite)||![from,to].every(v=>[0,90,180,270].includes(v)))throw Error('范围朝向或中心无效')
 const angle=(to-from)*Math.PI/180,[x,y,z]=center
 return [+(x*Math.cos(angle)-y*Math.sin(angle)).toFixed(1)||0,+(x*Math.sin(angle)+y*Math.cos(angle)).toFixed(1)||0,z]
}
export function interactionRange(value){
 if(!value||!Array.isArray(value.center)||value.center.length!==3||!value.center.every(v=>finite(v)&&Math.abs(v)<=10000)||!Array.isArray(value.size)||value.size.length!==3||!value.size.every(v=>finite(v)&&v>=1&&v<=5000)||![0,90,180,270].includes(value.direction))return null
 const depth=value.depthRange
 if(!Array.isArray(depth)||depth.length!==2||!depth.every(v=>finite(v)&&v>=.005&&v<=2)||depth[1]-depth[0]<.01)return null
 return {center:[...value.center],size:[...value.size],direction:value.direction,depthRange:[...depth]}
}
function browserStorage(){try{return globalThis.localStorage}catch{return null}}
export function readInteractionRangeDefault(w,{storage=browserStorage()}={}){
 const server=interactionRange(w?.snap?.settings?.interaction_range_default)
 if(server)return server
 for(const key of [FOLLOW_RANGE_KEY,FOLLOW_DRAFT_KEY]){
  try{const value=JSON.parse(storage?.getItem(key)||'null');if(key===FOLLOW_DRAFT_KEY&&value?.version!==1)continue;const legacy=interactionRange({...value,direction:value?.direction??270,depthRange:value?.depthRange??[.06,.30]});if(legacy)return legacy}catch{}
 }
 return interactionRange(INTERACTION_RANGE_FACTORY)
}
export async function saveInteractionRangeDefault(w,value){
 const range=interactionRange(value)
 if(!range)throw Error('范围默认值无效')
 const result=await w.setSettings({interaction_range_default:range})
 if(result===false||result==null||result?.ok===false)throw Error(result?.why||'默认范围保存失败')
 return range
}
