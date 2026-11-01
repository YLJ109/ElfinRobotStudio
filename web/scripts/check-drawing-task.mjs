import test from 'node:test'
import assert from 'node:assert/strict'
import {reactive} from 'vue'
import {Euler,Vector3} from 'three'
import {drawingConfig,drawingBounds,drawingRange,drawingToolPose,createDrawingTask} from '../src/drawingTask.js'
const base=()=>({shape:'heart',center:[400,0,450,180,0,0],plane:'xy',width_mm:80,height_mm:60,angle_deg:5,speed_pct:15,repeats:1})
const world=()=>reactive({connected:true,stateFresh:true,programSrc:'old',view:'hand-follow',workTab:'points',snap:{session_id:'s1',mode:'sim',config_rev:1,motion_granted:true,joints:{known:true,q:[0,0,0,0,0,0]},tools:{mounted:{tool_id:'none',revision:1}}}})
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:value=>resolve(value)}}

test('drawing validates bounded finite configuration without silently coercing empty values',()=>{
 assert.equal(drawingConfig({...base(),shape:'square'},'sim').height_mm,80)
 for(const patch of [{center:null},{center:[null,0,0,0,0,0]},{width_mm:''},{height_mm:501},{speed_pct:101},{repeats:4},{shape:'unknown'},{plane:'ab'},{shape:'nod',angle_deg:16}])assert.throws(()=>drawingConfig({...base(),...patch},'sim'))
 assert.throws(()=>drawingConfig(base(),'real'))
 assert.equal(drawingConfig({...base(),width_mm:50,height_mm:40,speed_pct:5},'real').width_mm,50)
 for(const patch of [{width_mm:51},{shape:'nod',angle_deg:6},{speed_pct:6}])assert.throws(()=>drawingConfig({...base(),width_mm:40,height_mm:40,speed_pct:5,...patch},'real'))
})

const frame=(phase='ready',extra={})=>({ok:true,plan_id:'own-plan',phase,mode:'sim',session_id:'s1',config:base(),path:[[400,0,450,180,0,0],[410,0,450,180,0,0]],program:'SPEED 15\nMOVEJ [1,2,3,4,5,6]',expires_in_s:phase==='ready'?120:null,can_run:phase==='ready',progress:{completed:0,total:2},...extra})
function fixture(handler=()=>frame()){
 const w=world(),calls=[],paths=[],clears=[],failures=[];let clock=1000
 const task=createDrawingTask(w,{request:async(action,payload,options)=>{calls.push({action,payload,options});return handler(action,payload,options)},now:()=>clock,setPath:(...args)=>paths.push(args),clearPath:id=>clears.push(id),onLeaveError:reason=>failures.push(reason),confirmReplace:()=>true})
 return {w,task,calls,paths,clears,failures,time:value=>{clock=value}}
}

test('checking returns a scoped ticket, only ready publishes checked path and execution uses the exact same plan',async()=>{
 let value=frame('checking')
 const h=fixture(action=>action==='preview'?value:action==='run'?frame('running'):action==='pause'?frame('paused'):action==='resume'?frame('running'):action==='cancel'?frame('cancelled'):value)
 try{
  assert.equal(await h.task.preview(base()),true);assert.equal(h.task.owned(),true);assert.equal(h.paths.length,0)
  await h.task.act('run');assert.equal(h.calls.filter(c=>c.action==='run').length,0)
  value=frame();await h.task.read();assert.equal(h.paths.length,1);assert.equal(h.paths[0][1].owner,h.task.owner)
  assert.equal(await h.task.act('run'),true);assert.deepEqual(h.calls.at(-1).payload,{plan_id:'own-plan'})
  await h.task.act('pause');assert.equal(h.task.state.snapshot.phase,'paused')
  h.w.snap.motion_granted=false;await h.task.act('resume');assert.equal(h.calls.at(-1).action,'pause');assert.match(h.task.state.error,/未就绪/)
  h.w.snap.motion_granted=true;await h.task.act('resume');assert.equal(h.calls.at(-1).action,'resume')
  await h.task.cancel();assert.deepEqual(h.calls.at(-1).payload,{plan_id:'own-plan'});assert.equal(h.task.state.snapshot.phase,'cancelled')
 }finally{h.task.dispose()}
})

test('one-click simulation execution waits for the owned checked plan and starts once',async()=>{
 let phase='checking'
 const h=fixture(action=>frame(action==='run'?'running':action==='cancel'?'cancelled':phase))
 try{
  assert.equal(await h.task.requestRun(base()),true)
  assert.equal(h.task.state.runQueued,true)
  assert.equal(h.calls.filter(c=>c.action==='run').length,0)
  await h.task.requestRun(base())
  assert.equal(h.calls.filter(c=>c.action==='preview').length,1)
  phase='ready';await h.task.read();await Promise.resolve()
  assert.equal(h.calls.filter(c=>c.action==='run').length,1)
  assert.equal(h.task.state.runQueued,false)
 }finally{h.task.dispose()}
})

test('editing, stop and failed planning revoke queued execution',async()=>{
 for(const action of ['edit','cancel','failed']){
  let phase='checking';const h=fixture(a=>frame(a==='cancel'?'cancelled':phase))
  try{
   await h.task.requestRun(base())
   if(action==='failed'){phase='failed';await h.task.read()}else await h.task[action]()
   assert.equal(h.task.state.runQueued,false)
   phase='ready';await h.task.read();await Promise.resolve()
   assert.equal(h.calls.filter(c=>c.action==='run').length,0)
  }finally{h.task.dispose()}
 }
})

test('strict validation sends zero invalid requests; real base and execution confirmation cannot carry across contexts',async()=>{
 const h=fixture((action)=>frame(action==='run'?'running':'ready',{mode:'real'}))
 try{
  await h.task.preview({...base(),width_mm:''});assert.equal(h.calls.length,0)
  h.w.snap.mode='real';h.w.snap.real={connected:true,epoch:1}
  const config={...base(),width_mm:40,height_mm:30,speed_pct:5}
  await h.task.preview(config);assert.equal(h.calls.length,0)
  h.task.adoptBase();await h.task.preview(config);assert.equal(h.calls.at(-1).action,'preview')
  await h.task.act('run');assert.equal(h.calls.length,1)
  h.task.state.confirmedReal=true;await h.task.act('run');assert.equal(h.calls.at(-1).payload.confirm_real,true)
  h.w.snap.real.epoch=2;assert.equal(h.task.state.baseAccepted,false);assert.equal(h.task.state.plan,null)
  await h.task.act('run');assert.equal(h.calls.filter(c=>c.action==='run').length,1)
 }finally{h.task.dispose()}
})

test('late preview after session change is rejected and cancels only the returned obsolete ticket',async()=>{
 const waiting=deferred(),h=fixture(action=>action==='preview'?waiting.promise:frame('cancelled'))
 try{
  const pending=h.task.preview(base());h.w.snap.session_id='s2'
  waiting.resolve(frame('checking'));assert.equal(await pending,false)
  assert.equal(h.task.owned(),false);assert.equal(h.task.state.plan,null);assert.equal(h.paths.length,0)
  assert.deepEqual(h.calls.filter(c=>c.action==='cancel').map(c=>c.payload),[{plan_id:'own-plan'}])
 }finally{h.task.dispose()}
})

test('readback races cannot overwrite a newer mutation and no foreign task is adopted or cancelled on leave',async()=>{
 const waiting=deferred(),h=fixture(action=>action==='state'?waiting.promise:frame('checking'))
 try{
  const read=h.task.read();await h.task.preview(base());waiting.resolve(frame('running',{plan_id:'foreign'}));await read
  assert.equal(h.task.state.snapshot.plan_id,'own-plan');assert.equal(h.task.state.snapshot.phase,'checking')
 }finally{h.task.dispose()}
 const f=fixture(()=>frame('running',{plan_id:'other-owner'}))
 await f.task.read();assert.equal(f.task.owned(),false);assert.match(f.task.blocked(),/另一/)
 await f.task.preview(base());await f.task.cancel();f.task.leave()
 assert.deepEqual(f.calls.map(c=>c.action),['state']);assert.equal(f.paths.length,0);f.task.dispose()
})

test('expiry never extends through polls and releases only its owner path; loading edits the official program without executing',async()=>{
 const h=fixture(()=>frame())
 try{
  await h.task.preview(base());assert.equal(h.task.loadProgram(),true);assert.equal(h.w.programSrc,frame().program);assert.equal(h.w.view,'work');assert.equal(h.w.workTab,'program')
  assert.equal(h.calls.some(c=>c.action==='run'),false)
  h.time(120900);await h.task.read();h.time(121001);h.task.refreshValidity()
  assert.equal(h.task.state.plan,null);assert.ok(h.task.expired());assert.ok(h.clears.every(id=>id===h.task.owner))
  assert.equal(h.task.loadProgram(),false);await h.task.act('run');assert.equal(h.calls.some(c=>c.action==='run'),false)
 }finally{h.task.dispose()}
})

test('null or malformed state and actual server failures revoke execution, preserving the reason',async()=>{
 let response=frame(),h=fixture(()=>response)
 try{
  await h.task.preview(base());response=null;await h.task.read();assert.equal(h.task.state.readFailed,true);assert.equal(h.task.state.plan,null)
  response={ok:false,why:'实际路径碰撞'};await h.task.read();assert.match(h.task.state.error,/实际路径碰撞/)
  await h.task.act('run');assert.equal(h.calls.some(c=>c.action==='run'),false)
 }finally{h.task.dispose()}
})

test('stop preempts pending pause, late pause cannot restore the cancelled task, and leave retains real stop errors',async()=>{
 const waiting=deferred(),h=fixture(action=>action==='pause'?waiting.promise:frame(action==='run'?'running':action==='cancel'?'cancelled':'ready'))
 try{
  await h.task.preview(base());await h.task.act('run');const pause=h.task.act('pause');await h.task.cancel()
  waiting.resolve(frame('paused'));await pause;assert.equal(h.task.state.snapshot.phase,'cancelled')
 }finally{h.task.dispose()}
 const f=fixture(action=>action==='cancel'?Promise.reject(Error('连接断开')):frame('running'))
 await f.task.preview(base());f.task.leave();for(let i=0;i<10;i++)await Promise.resolve()
 assert.match(f.failures[0],/连接断开/);f.task.dispose()
})


test('expired plan never regains authority from a delayed positive TTL and cancelled program replacement keeps the draft',async()=>{
 const h=fixture(()=>frame())
 try{
  await h.task.preview(base());h.time(121001);h.task.refreshValidity();h.time(121010);await h.task.read()
  assert.equal(h.task.state.plan,null);assert.ok(h.task.expired());await h.task.act('run');assert.equal(h.calls.some(c=>c.action==='run'),false)
 }finally{h.task.dispose()}
 const w=world(),task=createDrawingTask(w,{request:async()=>frame(),confirmReplace:()=>false})
 await task.preview(base());assert.equal(task.loadProgram(),false);assert.equal(w.programSrc,'old');assert.equal(w.view,'hand-follow');task.dispose()
})


test('own sim speed application across pending run and running readback does not cancel or lose the plan',async()=>{
 for(const timing of ['before-response','after-response']){
  const waiting=deferred();let h
  const speed=100,config={...base(),speed_pct:speed}
  const reply=phase=>frame(phase,{config})
  const applySpeed=()=>{
   h.w.snap={...h.w.snap,config_rev:2,sim_speed_pct:speed,speed_pct:speed,speed_ack:{target:'sim',req:speed,ok:true},settings:{...h.w.snap.settings,sim_speed_pct:speed},tools:{...h.w.snap.tools,mounted:{...h.w.snap.tools.mounted,revision:2}}}
   h.w.tools={...h.w.tools,mounted:{tool_id:'none',revision:2}}
  }
  h=fixture(action=>{if(action==='run'){if(timing==='before-response')applySpeed();return waiting.promise}return reply(action==='cancel'?'cancelled':'ready')})
  h.w.snap.sim_speed_pct=10;h.w.snap.settings={sim_speed_pct:10};h.w.tools={mounted:{tool_id:'none',revision:1}}
  try{
   await h.task.preview(config);const run=h.task.act('run')
   if(timing==='before-response'){assert.equal(h.task.state.pending,'run');assert.equal(h.task.owned(),true)}
   waiting.resolve(reply('running'));assert.equal(await run,true)
   if(timing==='after-response')applySpeed()
   assert.equal(h.task.state.snapshot.phase,'running');assert.equal(h.task.owned(),true)
   assert.equal(h.calls.filter(c=>c.action==='cancel').length,0)
   h.w.snap={...h.w.snap,config_rev:3,settings:{...h.w.snap.settings,tts:true,ghost_opacity:.6},tools:{...h.w.snap.tools,mounted:{...h.w.snap.tools.mounted,revision:3}}}
   h.w.tools={...h.w.tools,mounted:{tool_id:'none',revision:3}}
   assert.equal(h.task.owned(),true);assert.equal(h.calls.filter(c=>c.action==='cancel').length,0)
   h.w.snap={...h.w.snap,sim_speed_pct:80,settings:{...h.w.snap.settings,sim_speed_pct:80}}
   assert.equal(h.task.owned(),true);assert.ok(h.task.state.plan)
   assert.equal(h.calls.filter(c=>c.action==='cancel').length,0)
  }finally{h.task.dispose()}
 }
})

test('motion-specific settings, model, tool and connection fingerprints still invalidate exactly the owned plan',async()=>{
 const changes=[w=>{w.snap.settings.fence={enabled:true,x:[-200,200]}},w=>{w.snap.settings.custom_limits={lower:[-90],upper:[90]}},w=>{w.snap.settings.self_collision=false},w=>{w.snap.settings.ground_enabled=false},w=>{w.snap.settings.real_move_cmd='movel'},w=>{w.snap.settings.vel_source='controller'},w=>{w.meta.model.variant='elfin10'},w=>{w.snap.tcp_frame={translation_mm:[0,0,100],rpy_deg:[0,0,0]}},w=>{w.snap.tools.visual={kind:'vacuum',angle_deg:90}},w=>{w.snap.tools.tool={id:'none',type:'none',tcp:{translation_mm:[0,0,10],status:'verified'}}},w=>{w.stateFresh=false}]
 for(const change of changes){
  const h=fixture(action=>frame(action==='cancel'?'cancelled':'ready'))
  h.w.snap.settings={fence:{enabled:true},self_collision:true,ground_enabled:true,real_move_cmd:'relj',vel_source:'manual'};h.w.meta={model:{variant:'elfin5'}}
  try{await h.task.preview(base());change(h.w);assert.equal(h.task.owned(),false);assert.deepEqual(h.calls.filter(c=>c.action==='cancel').map(c=>c.payload),[{plan_id:'own-plan'}])}finally{h.task.dispose()}
 }
})


test('unrelated config metadata arriving during preview does not discard its returned ticket',async()=>{
 const waiting=deferred(),h=fixture(action=>action==='preview'?waiting.promise:frame('cancelled'))
 try{
  h.w.snap.sim_speed_pct=10
  const pending=h.task.preview(base())
  h.w.snap={...h.w.snap,config_rev:7,settings:{tts:true,ghost_opacity:.2,robot_ip:'metadata-only'},tools:{...h.w.snap.tools,mounted:{...h.w.snap.tools.mounted,revision:7}}}
  h.w.tools={mounted:{tool_id:'none',revision:7},items:[{name:'catalogue metadata'}]}
  waiting.resolve(frame('checking'));assert.equal(await pending,true);assert.equal(h.task.owned(),true);assert.equal(h.calls.filter(c=>c.action==='cancel').length,0)
 }finally{h.task.dispose()}
})

test('real acknowledgement loss or actual installation revision change still revokes the same plan',async()=>{
 for(const change of [w=>{w.snap.speed_ack.ok=false},w=>{w.snap.tools.mounted.revision=2}]){
  const config={...base(),width_mm:40,height_mm:30,speed_pct:5}
  const h=fixture(action=>frame(action==='run'?'running':action==='cancel'?'cancelled':'ready',{mode:'real',config}))
  h.w.snap={...h.w.snap,mode:'real',real:{connected:true,epoch:1},real_speed_pct:5,speed_ack:{target:'real',req:5,ok:true}}
  try{
   h.task.adoptBase();await h.task.preview(config);h.task.state.confirmedReal=true;await h.task.act('run')
   change(h.w);assert.equal(h.task.owned(),false);assert.equal(h.task.state.confirmedReal,false)
   assert.deepEqual(h.calls.filter(c=>c.action==='cancel').map(c=>c.payload),[{plan_id:'own-plan'}])
   await h.task.act('resume');assert.equal(h.calls.filter(c=>c.action==='resume').length,0)
  }finally{h.task.dispose()}
 }
})


test('drawing ranges have one consumed size parameter and enforce finite bounds and actual margins',()=>{
 const range_size_mm=[220,80,220],bounds=drawingRange([400,0,450],range_size_mm)
 assert.deepEqual(bounds,{min:[290,-40,340],max:[510,40,560]})
 const config=drawingConfig({...base(),plane:'xz',width_mm:180,height_mm:140,range_size_mm},'sim')
 assert.deepEqual(config.range_size_mm,range_size_mm);assert.notEqual(config.range_size_mm,range_size_mm)
 assert.equal(config.bounds,undefined,'no second editable bounds entry')
 assert.deepEqual(drawingBounds(config,'sim'),bounds)
 assert.throws(()=>drawingRange([400,'',450],[220,80,220]))
 assert.throws(()=>drawingRange([400,0,450],[0,80,180]))
 assert.throws(()=>drawingConfig({...config,range_size_mm:[5001,80,180]},'sim'))
 assert.throws(()=>drawingConfig({...config,width_mm:200,range_size_mm:[200,80,180]},'sim'),/图形超出/)
 assert.throws(()=>drawingRange([400,0,450],[51,50,50],'real'))
 const h=fixture()
 try{h.task.preview({...config,range_size_mm:[null,80,180]});assert.equal(h.calls.length,0)}finally{h.task.dispose()}
})


test('drawing contact transform matches fixed-axis RzRyRx at mixed orientations and fails closed for unknown geometry',()=>{
 for(const rpy of [[0,0,0],[180,0,0],[0,90,90],[-122,37,81]]){
 const offset=[-98,3,24],xyz=[400,-30,450],expected=new Vector3(...offset).applyEuler(new Euler(...rpy.map(v=>v*Math.PI/180),'ZYX')).add(new Vector3(...xyz)).toArray()
 const actual=drawingToolPose({tcp:{xyz_mm:xyz,rpy_deg:rpy}},{valid:true,offset_in_snapshot_tcp_mm:offset})
 assert.ok(actual.slice(0,3).every((v,i)=>Math.abs(v-expected[i])<1e-9));assert.deepEqual(actual.slice(3),rpy)
 }
 const snap={tcp:{xyz_mm:[1,2,3],rpy_deg:[40,50,60]}}
 assert.deepEqual(drawingToolPose(snap,{valid:true,offset_in_snapshot_tcp_mm:[0,0,0]}),[1,2,3,40,50,60],'official catalogue TCP must not be applied twice')
 assert.equal(drawingToolPose(snap,{valid:false,offset_in_snapshot_tcp_mm:[0,0,0]}),null);assert.equal(drawingToolPose(snap,null),null);assert.equal(drawingToolPose(snap,{valid:true,offset_in_snapshot_tcp_mm:[null,0,0]}),null);assert.equal(drawingToolPose({tcp:{xyz_mm:[1,2,3]}},{valid:true,offset_in_snapshot_tcp_mm:[0,0,0]}),null)
})


test('idle initialization and state metadata settling never invent a revoked-plan warning or erase a real error',async()=>{
 const h=fixture(()=>({ok:true,plan_id:null,phase:'idle',mode:'sim',session_id:'s1',config:null,path:[],program:'',progress:{completed:0,total:0},can_run:false}))
 try{
 await h.task.read();h.w.meta={model:{variant:'elfin5'}};h.w.snap.settings={fence:{enabled:true},self_collision:true};h.w.stateFresh=false;h.w.stateFresh=true
 assert.equal(h.task.state.error,'');assert.equal(h.task.owned(),false);assert.deepEqual(h.calls.map(c=>c.action),['state'])
 h.task.state.error='状态读取失败';h.w.meta.model.variant='elfin10';assert.equal(h.task.state.error,'状态读取失败','unrelated idle metadata cannot erase an actual failure')
 }finally{h.task.dispose()}
})


test('range_lower separates the fixed user frame from the checked shape center without allowing real auto placement',()=>{
 const config=drawingConfig({...base(),center:[-500,0,700,90,0,0],range_center_mm:[-500,0,700],range_size_mm:[900,300,500],center_policy:'range_lower',plane:'xz',width_mm:280,height_mm:240},'sim')
 assert.deepEqual(config.range_center_mm,[-500,0,700]);assert.deepEqual(drawingBounds({...config,center:[-425,0,650,90,0,0]},'sim'),{min:[-950,-150,450],max:[-50,150,950]})
 assert.throws(()=>drawingConfig({...config,width_mm:40,height_mm:30,speed_pct:5,range_size_mm:[50,50,50]},'real'),/不允许自动/)
 assert.throws(()=>drawingConfig({...config,center_policy:'fixed',width_mm:40,height_mm:30,speed_pct:5,range_center_mm:[-501,0,700],range_size_mm:[50,50,50]},'real'),/当前有效TCP/)
})
