import { objectGroundHeight } from './objectGround.js'
import { reactive } from 'vue'

export function playAllowed(world) {
  const snap=world?.snap
  return !!(world?.connected && snap?.session_id && snap.mode==='sim' && !snap.real
    && !snap.hand_follow?.active && snap.moving===false && !['running','waiting','paused'].includes(snap.exec?.state))
}

export function sortingCounts(counts) {
  if(!counts || Object.keys(counts).sort().join(',')!=='blue,green,red'
    || Object.values(counts).some(value=>!Number.isInteger(value) || value<0 || value>9))
    throw new Error('红、绿、蓝数量分别需为 0—9 的整数')
  const total=Object.values(counts).reduce((sum,value)=>sum+value,0)
  if(total<1 || total>27)throw new Error('工件总数需为 1—27')
  return {red:counts.red,green:counts.green,blue:counts.blue}
}

export function scenePlay(objects=[],mode) {
  const valid=objects.filter(object=>object.task?.mode===mode && typeof object.task.group==='string'
    && object.task.group && ['piece','zone_a','zone_b'].includes(object.task.role))
  const group=valid[0]?.task.group || ''
  const items=valid.filter(object=>object.task.group===group)
  const pieces=items.filter(object=>object.task.role==='piece')
  const board=Array(9).fill(null)
  let error=''
  if(mode==='chess')for(const object of pieces){
    const {slot,symbol}=object.task
    if(!['O','X'].includes(symbol) || !Number.isInteger(slot) || slot< -1 || slot>8){error='场景棋子元数据无效，请重新布置棋局';continue}
    if(slot===-1)continue
    if(board[slot])error='场景棋子格位重复，请重新布置棋局'
    board[slot]=symbol
  }
  return {group,items,pieces,board,result:gameResult(board),error,
    atA:pieces.filter(object=>object.task.slot===-1).length,
    atB:pieces.filter(object=>Number.isInteger(object.task.slot) && object.task.slot>=0).length}
}

function playPayload(action,payload) {
  if(!payload || typeof payload!=='object' || Array.isArray(payload))throw new Error('任务参数无效')
  const fields=Object.keys(payload).sort().join(',')
  if(action==='setup'){
    if(!['chess','sorting'].includes(payload.mode) || !['mode','counts,mode'].includes(fields))throw new Error('任务模式无效')
    if(payload.mode==='chess' && payload.counts!==undefined)throw new Error('棋局无需工件数量')
    return {mode:payload.mode,...(payload.counts===undefined?{}:{counts:sortingCounts(payload.counts)})}
  }
  if(typeof payload.group!=='string' || !payload.group)throw new Error('请先布置场景任务')
  if(action==='chess' && fields==='group,slot' && Number.isInteger(payload.slot) && payload.slot>=0 && payload.slot<=8)
    return {group:payload.group,slot:payload.slot}
  if(action==='sort' && fields==='criterion,group' && ['color','kind'].includes(payload.criterion))
    return {group:payload.group,criterion:payload.criterion}
  if(action==='reset' && fields==='group')return {group:payload.group}
  throw new Error('任务操作或参数无效')
}

export async function playRequest(action,payload,{signal,fetcher=globalThis.fetch}={}) {
  if(!['setup','chess','sort','reset'].includes(action))throw new Error('任务接口无效')
  const body=playPayload(action,payload)
  const response=await fetcher('/api/play/'+action,{method:'POST',cache:'no-store',
    headers:{'content-type':'application/json'},body:JSON.stringify(body),
    signal:signal?AbortSignal.any([signal,AbortSignal.timeout(10000)]):AbortSignal.timeout(10000)})
  let value
  try{value=await response.json()}catch(error){
    if(['AbortError','TimeoutError'].includes(error.name))throw error
    throw new Error('任务服务返回无效 JSON')
  }
  if(!response.ok || value?.ok!==true)throw new Error(value?.why || value?.detail || '场景任务未完成')
  return value
}

export function createPlayController(world,{request=playRequest}={}) {
  const state=reactive({busy:false,message:'',action:''})
  let controller=null
  async function run(action,payload){
    if(state.busy)return null
    state.message=''
    if(!playAllowed(world)){state.message='需要静止的仿真会话，并停止程序与交互跟随';return null}
    let body
    try{
      body=playPayload(action,payload)
      if(action!=='setup' && !(world.snap.objects || []).some(object=>object.task?.group===body.group
        && (action==='reset' || object.task.mode===(action==='chess'?'chess':'sorting'))))
        throw new Error('场景任务已变化，请重新选择或布置')
    }catch(error){state.message=error.message;return null}
    const session=world.snap.session_id
    state.busy=true;state.action=action;controller=new AbortController()
    try{
      const value=await request(action,body,{signal:controller.signal})
      if(controller.signal.aborted){state.message='已停止等待，操作可能已完成；以共享场景状态为准';return null}
      if(world.snap?.session_id!==session){state.message='会话已变化，请查看当前共享场景';return null}
      state.message=action==='setup'?'任务已布置：A 原料区 → B 放料区；以共享场景为准'
        :action==='reset'?'任务物体已回到 A 原料区'
        :action==='sort'?`已将 ${value.count} 个工件分拣到 B 放料区`
        :'已更新三维棋子：你执 O，电脑执 X'
      return value
    }catch(error){
      state.message=controller.signal.aborted?'已停止等待，操作可能已完成；以共享场景状态为准'
        :error.name==='TimeoutError'?'任务等待超时，操作可能已完成；请查看共享场景后重试':error.message
      return null
    }finally{state.busy=false;state.action='';controller=null}
  }
  return {state,run,cancel(){controller?.abort()}}
}

// Component teardown/navigation does not discard an in-flight atomic operation.
// The world store owns this controller; scene snapshots remain the source of truth.
const playControllers=new WeakMap()
export function playFor(world) {
  if(!playControllers.has(world))playControllers.set(world,createPlayController(world))
  return playControllers.get(world)
}
const LINES = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]]
export function gameResult(board) {
  for (const [a,b,c] of LINES) if (board[a] && board[a] === board[b] && board[a] === board[c]) return board[a]
  return board.every(Boolean) ? 'draw' : null
}
export function robotMove(board) {
  if (gameResult(board)) return null
  function score(cells, player) {
    const result = gameResult(cells)
    if (result) return result === 'O' ? 10 : result === 'X' ? -10 : 0
    const scores = cells.flatMap((value,index) => {
      if (value) return []
      const next = cells.slice(); next[index] = player
      return [score(next, player === 'O' ? 'X' : 'O')]
    })
    return player === 'O' ? Math.max(...scores) : Math.min(...scores)
  }
  let chosen = null, best = -Infinity
  for (const index of [4,0,2,6,8,1,3,5,7]) {
    if (board[index]) continue
    const next = board.slice(); next[index] = 'O'
    const value = score(next, 'X')
    if (value > best) { best = value; chosen = index }
  }
  return chosen
}
export function sortingPlan(objects, criterion = 'kind') {
  if (!['kind','color'].includes(criterion)) throw new Error('不支持的分拣规则')
  if (objects.length > 36) throw new Error('每次最多分拣 36 个物体')
  const groups = [...new Set(objects.map(object => String(object[criterion])))].sort()
  const counts = new Map()
  return objects.map(object => {
    if (!object.id || !object.size?.every(value => Number.isFinite(value) && value > 0)) throw new Error('物体数据无效')
    const footprint=object.kind==='box'?Math.max(object.size[0],object.size[1]):object.size[0]*2
    if(footprint>100)throw new Error('此演示仅分拣底面不超过 100mm 的小工件')
    const group = String(object[criterion]), slot = counts.get(group) || 0
    counts.set(group, slot + 1)
    return { id: object.id, name: object.name, group,
      xyz: [groups.indexOf(group) * 400 - 600, Math.floor(slot / 3) * 220 + 350, objectGroundHeight(object)],
      column: slot % 3 }
  }).map(item => ({ ...item, xyz: [item.xyz[0] + item.column * 110, item.xyz[1], item.xyz[2]] }))
}
