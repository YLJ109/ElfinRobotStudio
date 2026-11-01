import { parseResponse } from './connectionState.js'
import { readSceneStatus, sceneEditBlock } from './sceneTools.js'
// No state or history lives here: every edit uses the existing server revision.
export async function editSceneObject(world, action, id, patch, fetcher=(...args)=>fetch(...args)) {
  const block=sceneEditBlock(world.snap,world.connected && world.stateFresh!==false)
  if(block)throw Error(block)
  const session=world.snap.session_id
  const viewed=world.snap.objects?.find(object=>object.id===id)
  const expected=viewed?JSON.parse(JSON.stringify(viewed)):null
  const prefix='/api/workspace/scene'
  const before=readSceneStatus(await parseResponse(await fetcher(prefix,{cache:'no-store'}),prefix))
  if(world.snap?.session_id!==session || sceneEditBlock(world.snap,world.connected && world.stateFresh!==false))throw Error('场景会话或运动状态已变化，未提交修改')
  const source=before.objects.find(object=>object.id===id)
  if(action!=='add' && !source)throw Error('选中物体已不存在')
  if(action==='object' && expected && Object.keys(patch || {}).some(key=>key in expected && JSON.stringify(source[key])!==JSON.stringify(expected[key])))throw Error('物体字段已变化，请核对后重新编辑')
  if(source?.locked && !(action==='object' && Object.keys(patch || {}).length===1 && patch.locked===false))throw Error('物体已锁定，请先解锁')
  const path=`${prefix}/${action}`,body={revision:before.revision,...(action==='add'?{spec:patch}:{id}),...(action==='object'?{patch}:{})}
  const after=readSceneStatus(await parseResponse(await fetcher(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),path))
  const object=['duplicate','add'].includes(action)?after.objects.find(item=>!before.objects.some(old=>old.id===item.id)):after.objects.find(item=>item.id===id)
  return {...after,object:object || null}
}
