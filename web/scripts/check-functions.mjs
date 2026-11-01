import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { gameResult, robotMove, scenePlay, playAllowed, sortingCounts,
  playRequest, createPlayController, playFor } from '../src/functionPlay.js'
import { objectGroundHeight, constrainObjectToGround } from '../src/objectGround.js'
import { createRobotPlayController } from '../src/robotPlayProgram.js'
import { createChessMatch } from '../src/chessMatch.js'
import { visionWorkspace } from '../src/visionWorkspace.js'
test('井字棋完整搜索所有玩家落点，电脑不会败、不会覆盖已有棋子',()=>{
  let leaves=0
  function visit(board){
    const result=gameResult(board)
    if(result){assert.notEqual(result,'X');leaves++;return}
    for(let i=0;i<9;i++)if(!board[i]){
      const next=board.slice();next[i]='X'
      if(gameResult(next)){assert.notEqual(gameResult(next),'X');leaves++;continue}
      const move=robotMove(next);assert.equal(next[move],null);next[move]='O';visit(next)
    }
  }
  visit(Array(9).fill(null));assert.ok(leaves>100)
  assert.equal(robotMove(['X','X','X',null,null,null,null,null,null]),null)
})
test('所有基础形状拖动坐标按底面限幅，并保留合法悬空位置',()=>{
  for(const kind of ['box','sphere','cylinder']){
    const object={kind,size:[40,60,100]};const min=kind==='sphere'?40:50
    assert.equal(objectGroundHeight(object),min)
    assert.deepEqual(constrainObjectToGround(object,[1,2,-100]),[1,2,min])
    assert.deepEqual(constrainObjectToGround(object,[1,2,500]),[1,2,500])
  }
})
test('功能页可编译，复用标签及交互组件，演示不会发送机器人运动命令',()=>{
  const source=readFileSync(new URL('../src/panels/FunctionsPanel.vue',import.meta.url),'utf8')
  const {descriptor}=parse(source);const script=compileScript(descriptor,{id:'functions-test'})
  const template=compileTemplate({source:descriptor.template.content,filename:'FunctionsPanel.vue',id:'functions-test',compilerOptions:{bindingMetadata:script.bindings}})
  assert.deepEqual(template.errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'functions-test',scoped:style.scoped}).errors,[])
  assert.match(source,/<KeepAlive><HandFollowPanel/)
  assert.doesNotMatch(source,/w\.(moveJ|moveL|jog|connectReal|execute|runProgram)\(/)
  assert.match(source,/createChessMatch\(w/)
  assert.doesNotMatch(source,/patchObject/,'formal task UI cannot directly reposition objects')
})

const taskObject=(mode,group,role,home,slot=-1,symbol='')=>({id:`${group}-${role}-${home}`,name:'task',
  kind:'box',size:[30,30,20],xyz:[100,200,10],task:{mode,group,role,home,slot,symbol}})
function taskObjects(mode='chess',group='task-1') {
  return [taskObject(mode,group,'zone_a',-1),taskObject(mode,group,'zone_b',-2),
    ...Array.from({length:9},(_,index)=>taskObject(mode,group,'piece',index,-1,mode==='chess'?(index<5?'O':'X'):''))]
}
function world(objects=taskObjects()) {
  return Vue.reactive({connected:true,snap:{session_id:'session-a',mode:'sim',real:null,moving:false,
    exec:{state:'idle'},tools:{visual:{kind:'vacuum-reference'}},hand_follow:{active:false},objects}})
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}

test('task snapshots restore group, O-first board and result without moving shared objects',()=>{
  const objects=taskObjects(),before=structuredClone(objects)
  objects[2].task.slot=0;objects[3].task.slot=4;objects[4].task.slot=8;objects[7].task.slot=1
  const original=structuredClone(objects)
  const value=scenePlay(objects,'chess')
  assert.equal(value.group,'task-1');assert.equal(value.result,'O')
  assert.deepEqual(value.board,['O','X',null,null,'O',null,null,null,'O'])
  assert.equal(value.atA,5);assert.equal(value.atB,4)
  assert.deepEqual(objects,original)
  assert.equal(scenePlay(before,'chess').board.filter(Boolean).length,0)
  assert.equal(scenePlay(objects,'sorting').group,'')
  assert.deepEqual(scenePlay([{id:'user',task:null}],'chess').pieces,[])
  const duplicate=structuredClone(objects);duplicate[3].task.slot=0
  assert.match(scenePlay(duplicate,'chess').error,/重复/)
  const invalid=structuredClone(objects);invalid[2].task.slot=9
  assert.match(scenePlay(invalid,'chess').error,/无效/)
})

test('RGB counts accept 0..9 independently, copy input and reject fractions, coercion and extra fields',()=>{
  for(const counts of [{red:3,green:3,blue:3},{red:0,green:9,blue:9},{red:9,green:9,blue:9}]){
    assert.deepEqual(sortingCounts(counts),counts);assert.notEqual(sortingCounts(counts),counts)
  }
  for(const counts of [null,{}, {red:0,green:0,blue:0}, {red:3,green:3}, {red:10,green:0,blue:0},
    {red:-1,green:3,blue:3},{red:1.5,green:3,blue:3},{red:'3',green:3,blue:3},
    {red:true,green:3,blue:3},{red:NaN,green:3,blue:3},{red:3,green:3,blue:3,url:'bad'}]){
    assert.throws(()=>sortingCounts(counts))
  }
})

test('play requests use only fixed contracts, never object patches or robot movement',async()=>{
  const calls=[]
  const fetcher=async(...args)=>{calls.push(args);return {ok:true,json:async()=>({ok:true})}}
  for(const [action,payload] of [['setup',{mode:'chess'}],['setup',{mode:'sorting',counts:{red:3,green:3,blue:3}}],
    ['chess',{group:'chess-1',slot:0}],['sort',{group:'sorting-1',criterion:'color'}],['reset',{group:'chess-1'}]]){
    await playRequest(action,payload,{fetcher})
    const [url,options]=calls.at(-1)
    assert.equal(url,'/api/play/'+action);assert.equal(options.method,'POST')
    assert.deepEqual(JSON.parse(options.body),payload);assert.equal(options.cache,'no-store')
    assert.ok(options.signal instanceof AbortSignal)
  }
  const before=calls.length
  for(const [action,payload] of [['http://external',{}],['setup',{mode:'url'}],['chess',{group:'g',slot:9}],
    ['chess',{group:'g',slot:true}],['chess',{group:'g',slot:1,xyz:[0,0,0]}],
    ['sort',{group:'g',criterion:'size'}],['reset',{group:''}]]){
    await assert.rejects(playRequest(action,payload,{fetcher}))
  }
  assert.equal(calls.length,before)
})

test('play request reports HTTP, application and JSON errors and supports abort/timeout',async t=>{
  for(const [response,pattern] of [[{ok:false,json:async()=>({why:'仅仿真'})},/仅仿真/],
    [{ok:true,json:async()=>({ok:false,why:'格位已占用'})},/格位已占用/],
    [{ok:true,json:async()=>{throw Error('bad')}},/JSON/]]){
    await assert.rejects(playRequest('reset',{group:'g'},{fetcher:async()=>response}),pattern)
  }
  const controller=new AbortController()
  const aborting=(_url,{signal})=>new Promise((_resolve,reject)=>{
    if(signal.aborted)reject(signal.reason)
    else signal.addEventListener('abort',()=>reject(signal.reason),{once:true})
  })
  const pending=playRequest('reset',{group:'g'},{signal:controller.signal,fetcher:aborting})
  controller.abort();await assert.rejects(pending,error=>error.name==='AbortError')
  const timeout=new AbortController()
  t.mock.method(AbortSignal,'timeout',ms=>{assert.equal(ms,10000);return timeout.signal})
  const expired=playRequest('reset',{group:'g'},{fetcher:aborting})
  timeout.abort(new DOMException('expired','TimeoutError'))
  await assert.rejects(expired,error=>error.name==='TimeoutError')
  await assert.rejects(playRequest('reset',{group:'g'},{fetcher:async()=>({ok:true,
    json:async()=>{throw new DOMException('expired while reading body','TimeoutError')}})}),error=>error.name==='TimeoutError')
})

test('controller permits only stationary simulation and blocks duplicate requests and stale groups',async()=>{
  const w=world(),calls=[],pending=deferred()
  const controller=createPlayController(w,{request:(...args)=>{calls.push(args);return pending.promise}})
  for(const patch of [{mode:'real'},{real:{}},{moving:true},{moving:undefined},
    {hand_follow:{active:true}},{exec:{state:'running'}},{exec:{state:'waiting'}},{exec:{state:'paused'}}]){
    const snap=w.snap;w.snap={...snap,...patch}
    assert.equal(playAllowed(w),false)
    assert.equal(await controller.run('setup',{mode:'chess'}),null)
    w.snap=snap
  }
  w.connected=false;assert.equal(await controller.run('setup',{mode:'chess'}),null);w.connected=true
  assert.equal(calls.length,0)
  await controller.run('chess',{group:'deleted',slot:0});assert.equal(calls.length,0)
  const first=controller.run('chess',{group:'task-1',slot:0})
  assert.equal(controller.state.busy,true)
  assert.equal(await controller.run('reset',{group:'task-1'}),null);assert.equal(calls.length,1)
  assert.deepEqual(calls[0].slice(0,2),['chess',{group:'task-1',slot:0}])
  const original=JSON.stringify(w.snap.objects)
  pending.resolve({ok:true,board:['O','X',null,null,null,null,null,null,null],result:null});await first
  assert.equal(JSON.stringify(w.snap.objects),original,'response must not invent scene positions')
  assert.equal(controller.state.busy,false);assert.match(controller.state.message,/三维棋子/)
})

test('controller cancellation, timeout and changed session preserve authoritative scene and allow retry',async()=>{
  const w=world(),pending=deferred()
  const controller=createPlayController(w,{request:()=>pending.promise})
  const first=controller.run('reset',{group:'task-1'});controller.cancel()
  pending.resolve({ok:true});await first
  assert.match(controller.state.message,/可能已完成/);assert.equal(controller.state.busy,false)
  const expired=createPlayController(w,{request:async()=>{throw new DOMException('expired','TimeoutError')}})
  await expired.run('reset',{group:'task-1'});assert.match(expired.state.message,/超时/)
  const delayed=deferred(),changed=createPlayController(w,{request:()=>delayed.promise})
  const second=changed.run('reset',{group:'task-1'})
  w.snap.session_id='session-b';delayed.resolve({ok:true});await second
  assert.match(changed.state.message,/会话已变化/)
  assert.equal(changed.state.busy,false)
  assert.equal(playFor(w),playFor(w),'remount must reuse the store-owned controller')
  assert.notEqual(playFor(w),playFor(world()),'different stores must not share requests')
})

function panelFixture(t,w,{controller=createPlayController(w,{request:async()=>({ok:true})}),program=createRobotPlayController(w,{request:async()=>({ok:true,plan_id:'plan-1',source:'SPEED 10',points:[]})}),confirm=()=>true,
  props=Vue.reactive({category:'fun'}),vision=Vue.reactive({...visionWorkspace,config:null,calibration:null,result:null,connected:false,stale:false})}={}){
  const source=readFileSync(new URL('../src/panels/FunctionsPanel.vue',import.meta.url),'utf8')
  const descriptor=parse(source).descriptor,hooks=[],activations=[],events=[],timers=new Map()
  const deps={ref:Vue.ref,computed:Vue.computed,watch:Vue.watch,defineAsyncComponent:()=>({}),
    onActivated:fn=>activations.push(fn),onDeactivated:fn=>hooks.push(fn),onBeforeUnmount:fn=>hooks.push(fn),
    defineProps:()=>props,defineEmits:()=>(...args)=>events.push(args),useWorld:()=>w,
    gameResult,robotMove,scenePlay,playAllowed,sortingCounts,playFor:()=>controller,robotPlayFor:()=>program,
    createChessMatch:world=>createChessMatch(world,{request:async()=>({ok:true,active:false,planning:false,held:null,execution:{state:'idle'}}),random:()=>0}),
    visionWorkspace:vision,
    // 进入分拣/有趣时会自动开仿真摄像头；开启动作收口在视觉模块，这里只记一次
    // 调用，不碰真实视觉状态，免得清单检查变成后端请求。
    ensureVirtualVision:async()=>{deps.__visionCalls?.push('ensure');return true},
    globalThis:{confirm},setTimeout:fn=>{timers.set(1,fn);return 1},clearTimeout:id=>timers.delete(id)}
  deps.__visionCalls=[]
  const scope=Vue.effectScope()
  const script=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
  const panel=scope.run(()=>new Function(...Object.keys(deps),script+
    '\nreturn {tabs,board,chess,sorting,counts,sortingCriterion,shapeCounts,countError,setupTask,play,resetGame,tab,taskState,deactivate,startGame,match,matchState}')(...Object.values(deps)))
  t.after(()=>{panel.deactivate();scope.stop()})
  return {panel,controller,program,hooks,activations,events,timers}
}

test('编号工作区框只随交互页显示：井字棋/分拣/离开隐藏，缓存恢复按当前标签恢复且不发后端指令',async t=>{
  const calls=[],w=world(),controller=createPlayController(w,{request:(...args)=>calls.push(args)})
  const {panel,events,activations}=panelFixture(t,w,{controller})
  assert.deepEqual(events.at(-1),['interaction-visible',true])
  for(const tab of ['chess','sorting']){
    panel.tab.value=tab
    assert.deepEqual(events.at(-1),['interaction-visible',false])
    panel.deactivate();activations.forEach(fn=>fn())
    assert.deepEqual(events.at(-1),['interaction-visible',false])
  }
  panel.tab.value='interaction'
  assert.deepEqual(events.at(-1),['interaction-visible',true])
  panel.deactivate();assert.deepEqual(events.at(-1),['interaction-visible',false])
  activations.forEach(fn=>fn());assert.deepEqual(events.at(-1),['interaction-visible',true])
  assert.equal(calls.length,0)
  const app=readFileSync(new URL('../src/App.vue',import.meta.url),'utf8')
  assert.match(app,/view\.value==='hand-follow' && interactionVisible\.value/)
  assert.match(app,/setHandBoxPreview\(interactionShowing\.value\?box:null/)
  assert.match(app,/const following =[^\n]+interactionShowing\.value/)
})

test('simulation layout switches task mode without prompts and preserves ordinary objects',async t=>{
  const ordinary={id:'user',name:'user',size:[20,20,20],kind:'box',xyz:[0,0,10]}
  const w=world([...taskObjects(),ordinary]),calls=[],confirmations=[]
  const controller=createPlayController(w,{request:async(...args)=>{calls.push(args);return {ok:true,group:'sorting-new',objects:[]}}})
  const {panel}=panelFixture(t,w,{controller,confirm:text=>{confirmations.push(text);return false}})
  panel.counts.value={red:0,green:9,blue:3}
  await panel.setupTask('sorting')
  assert.deepEqual(calls[0].slice(0,2),['setup',{mode:'sorting',counts:{red:0,green:9,blue:3},criterion:'color'}])
  assert.deepEqual(w.snap.objects.filter(v=>!v.task).map(v=>v.id),['user'])
  await panel.setupTask('chess');assert.equal(confirmations.length,0)
  assert.equal(w.snap.objects.length,12,'frontend never deletes or replaces shared objects itself')
})

test('task chess requires explicit match start and scene reset never teleports frontend objects',async t=>{
  const w=world(),calls=[]
  const controller=createPlayController(w,{request:async(...args)=>{calls.push(args);return {ok:true}}})
  const {panel,hooks,timers}=panelFixture(t,w,{controller})
  assert.equal(await panel.play(4),false)
  assert.equal(timers.size,0);assert.equal(panel.board.value[4],null)
  assert.equal(calls.length,0)
  assert.equal(await panel.startGame(),true);assert.equal(panel.matchState.human,'O')
  assert.equal(panel.matchState.phase,'human')
  panel.tab.value='chess';panel.tab.value='sorting'
  assert.equal(panel.matchState.phase,'cancelled')
  hooks.forEach(fn=>fn())
  await panel.resetGame();assert.deepEqual(calls[0].slice(0,2),['reset',{group:'task-1'}])
  assert.equal(w.snap.objects.filter(p=>p.task.slot>=0).length,0)
})

test('chess automatically mounts dual suction once before starting, and failed mounting cannot start',async t=>{
  const w=world(),pending=deferred(),calls=[]
  w.snap.tools.visual={kind:'pen'}
  w.call=(...args)=>{calls.push(args);return pending.promise}
  const {panel}=panelFixture(t,w)
  const first=panel.startGame()
  await panel.startGame()
  assert.equal(calls.length,1)
  assert.deepEqual(calls[0],['/api/sim/task-tool',{task:'chess'}])
  assert.notEqual(panel.matchState.phase,'human')
  pending.resolve({ok:true});assert.equal(await first,true)
  assert.equal(panel.matchState.phase,'human')
})

test('chess mounting errors are visible and never start the match',async t=>{
  const w=world();w.snap.tools.visual={kind:'pen'}
  w.call=async()=>({ok:false,why:'工具切换失败'})
  const {panel}=panelFixture(t,w)
  await panel.startGame()
  assert.notEqual(panel.matchState.phase,'human')
  assert.equal(panel.matchState.message,'工具切换失败')
})

test('without a three-dimensional chess scene the board stays locked and requires setup',async t=>{
  const w=world([]),calls=[]
  const {panel,timers}=panelFixture(t,w,{controller:createPlayController(w,{request:(...args)=>calls.push(args)})})
  assert.equal(await panel.play(0),false);assert.equal(panel.board.value[0],null)
  assert.equal(await panel.startGame(),undefined);assert.equal(timers.size,0)
  assert.equal(calls.length,0)
  panel.resetGame();assert.deepEqual(panel.board.value,Array(9).fill(null))
})

test('sorting task generates a robot program, never teleports task pieces, and reflects server slots',async t=>{
  const pieces=Array.from({length:27},(_,home)=>taskObject('sorting','sorting-27','piece',home))
  const w=world([...taskObjects('sorting','sorting-27').slice(0,2),...pieces])
  w.patchObject=()=>{throw Error('task sort must not patch individual objects')}
  const calls=[],controller=createPlayController(w,{request:async(...args)=>{calls.push(args);return {ok:true,count:27}}})
  const program=createRobotPlayController(w,{request:async(...args)=>{calls.push(args);return {ok:true,plan_id:'sort-plan',source:'SPEED 10',points:[]}}})
  const {panel}=panelFixture(t,w,{controller,program})
  await program.generate({group:panel.sorting.value.group,action:'sorting',clearance:175,speed:12,wait:[400,20,450,180,0,0]})
  assert.equal(calls.length,1)
  assert.deepEqual(calls[0].slice(0,2),['generate',{group:'sorting-27',action:'sorting',clearance:175,speed:12,wait:[400,20,450,180,0,0]}])
  assert.match(program.state.message,/任务已准备/);assert.doesNotMatch(program.state.message,/人工确认/)
  assert.equal(panel.sorting.value.atA,27)
  for(const object of w.snap.objects)if(object.task.role==='piece')object.task.slot=object.task.home
  assert.equal(panel.sorting.value.atB,27);assert.equal(panel.sorting.value.atA,0)
  assert.equal(panel.sorting.value.error,'')
  assert.deepEqual(w.snap.objects.filter(v=>!v.task),[])
})

test('task panel has no direct object-position patch bypass for ordinary or task pieces',async t=>{
  const ordinary={id:'ordinary-piece',name:'普通工件',kind:'box',color:'#fff',size:[20,20,20],xyz:[0,0,10]}
  const w=world([...taskObjects(),ordinary]),patches=[]
  w.patchObject=async(id,patch)=>{patches.push({id,patch});return {ok:true}}
  const {panel}=panelFixture(t,w)
  const source=readFileSync(new URL('../src/panels/FunctionsPanel.vue',import.meta.url),'utf8')
  assert.equal(panel.sort,undefined);assert.doesNotMatch(source,/patchObject|@click="sort"|整理场景/)
  assert.equal(patches.length,0);assert.deepEqual(w.snap.objects.find(o=>o.id===ordinary.id).xyz,[0,0,10])
})

test('simulation sorting uses shared setup and never manages a second camera pipeline',async t=>{
  const calls=[],w=world(),controller=createPlayController(w,{request:async(...args)=>{calls.push(args);return {ok:true}}})
  const {panel}=panelFixture(t,w,{controller})
  assert.deepEqual(panel.counts.value,{red:3,green:3,blue:3})
  assert.equal(calls.length,0)
  await panel.setupTask('sorting')
  assert.deepEqual(calls[0].slice(0,2),['setup',{mode:'sorting',criterion:'color',counts:{red:3,green:3,blue:3}}])
  const source=readFileSync(new URL('../src/panels/FunctionsPanel.vue',import.meta.url),'utf8')
  assert.doesNotMatch(source,/visionCall\(|createVisionPolling\(|createVirtualCapture\(|patchObject/)
  assert.match(source,/<template #placement><button[^>]*@click="setupTask\('sorting'\)">摆放<\/button>/)
  assert.doesNotMatch(source,/:prepare=/,'start must not implicitly replace the laid-out materials')
})

test('shape sorting setup preserves explicit observed-rule contract and rejects bad counts without any patch motion',async t=>{
  const calls=[],w=world(),controller=createPlayController(w,{request:async(...args)=>{calls.push(args);return {ok:true,group:'shape-new',objects:[]}}})
  const {panel}=panelFixture(t,w,{controller})
  panel.sortingCriterion.value='shape';panel.shapeCounts.value={rectangle:1,circle:2}
  await panel.setupTask('sorting')
  assert.deepEqual(calls[0].slice(0,2),['setup',{mode:'sorting',criterion:'shape',shape_counts:{rectangle:1,circle:2}}])
  panel.shapeCounts.value={rectangle:0,circle:0}
  assert.match(panel.countError.value,/至少1个/)
  await panel.setupTask('sorting');assert.equal(calls.length,1)
})


test('fun and sorting navigation filter one task instance, cancel chess and release follow preview',async t=>{
 const props=Vue.reactive({category:'fun'}),w=world()
 const {panel,events}=panelFixture(t,w,{props})
 assert.deepEqual(panel.tabs.value.map(t=>[t.id,t.label]),[['interaction','跟随'],['track','追踪'],['chess','井字棋'],['drawing','绘制'],['writing','写字']])
 let cancelled=0;panel.match.cancel=()=>cancelled++
 panel.tab.value='chess';props.category='sorting';await Vue.nextTick()
 assert.equal(panel.tab.value,'sorting');assert.ok(cancelled>=1)
 assert.deepEqual(panel.tabs.value.map(t=>t.id),['sorting','handling','kit','pattern'])
 assert.deepEqual(events.at(-1),['interaction-visible',false])
 props.category='fun';await Vue.nextTick();assert.equal(panel.tab.value,'interaction')
 assert.deepEqual(events.at(-1),['interaction-visible',true])
})


test('chess marks use large scalable rounded SVG strokes instead of font-dependent glyphs',()=>{
 const source=readFileSync(new URL('../src/panels/FunctionsPanel.vue',import.meta.url),'utf8')
 assert.match(source,/<svg v-if="cell" class="chess-piece" viewBox="0 0 64 64"/)
 assert.match(source,/<circle v-if="cell===\'O\'" cx="32" cy="32" r="21"/)
 assert.match(source,/stroke-linecap:round/);assert.match(source,/width:80%;height:80%/)
})
