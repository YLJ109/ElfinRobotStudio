import test from 'node:test'
import assert from 'node:assert/strict'
import {handPipelineObservation,createHandPipelineClient} from '../src/handPipeline.js'
const landmarks=Array.from({length:21},(_,i)=>({x:.3+i*.01,y:.4,z:0}))
const packet=(patch={})=>({requestStartedAt:990,receivedAt:1000,snapshot:{enabled:true,source:'local',session:'cam',epoch:2,server_now:5,
 result:{source:'local',session:'cam',epoch:2,seq:8,captured_at:4.95,received_at:4.96,valid_until:5.25,valid:true,control_valid:true,coords:'image_normalized',clock_domain:'server',observations:[{kind:'hand',landmarks}],model:{name:'hand',version:'v1'},inference_started_at:4.96,inference_finished_at:4.99},...patch}})

test('result polling compensates request time within 33ms and never overlaps requests',async()=>{
 let clock=0,reply,reads=0,id=0;const jobs=new Map()
 const client=createHandPipelineClient({state:{enabled:false},now:()=>clock,
  schedule:(fn,ms)=>{jobs.set(++id,{fn,ms});return id},cancel:n=>jobs.delete(n),
  fetcher:()=>{reads++;return new Promise(resolve=>{reply=()=>resolve({ok:true,json:async()=>packet().snapshot})})}})
 const off=client.subscribe(()=>{});assert.equal(reads,1)
 clock=20;reply();for(let i=0;i<12;i++)await Promise.resolve()
 assert.equal(jobs.size,1);const [key,job]=[...jobs][0];assert.equal(job.ms,13)
 jobs.delete(key);job.fn();assert.equal(reads,2)
 assert.equal(jobs.size,1,'only the request deadline exists until this request completes')
 client.dispose();off()
})

test('concurrent identical detection enables share one configuration and one epoch',async()=>{
 let release,posts=0
 const client=createHandPipelineClient({state:{enabled:false},fetcher:()=>{posts++;return new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({ok:true,enabled:false})})})}})
 const a=client.configure(true,'local','cam'),b=client.configure(true,'local','cam')
 assert.equal(a,b);await Promise.resolve();assert.equal(posts,1)
 release();assert.equal(await a,true);assert.equal(await b,true);client.dispose()
})
test('unified result preserves original capture age, sequence and all 21 normalized landmarks',()=>{
 const p=packet(),a=handPipelineObservation(p,'cam',1000),b=handPipelineObservation(p,'cam',1050)
 assert.equal(a.frameSeq,8);assert.equal(a.landmarks.length,21);assert.equal(a.controlValid,true)
 assert.ok(Math.abs(a.timestamp-940)<1e-9);assert.ok(Math.abs(b.timestamp-940)<1e-9,'polling a result never renews its capture clock')
 assert.equal(Math.round(a.inferenceMs),30)
})
test('delayed transport and expired capture cannot become motion-fresh from a recent receipt',()=>{
 const p=packet();p.requestStartedAt=600
 assert.equal(handPipelineObservation(p,'cam',1000).controlValid,false)
 const old=handPipelineObservation(packet(),'cam',1350)
 assert.equal(old.controlValid,false);assert.equal(old.displayValid,true)
 assert.equal(handPipelineObservation(packet(),'cam',2100).displayValid,false)
})
test('remote and virtual results are display-only even if a sender claims control validity',()=>{
 for(const source of ['remote','virtual']){
  const p=packet({source});p.snapshot.result.source=source;p.snapshot.result.clock_domain='remote';p.snapshot.result.captured_at=1200000
  const result=handPipelineObservation(p,'cam',1000)
  assert.equal(result.controlValid,false);assert.equal(result.displayValid,true)
 }
})
for(const mutation of [p=>p.snapshot.result.session='old',p=>p.snapshot.result.epoch=1,p=>p.snapshot.result.source='remote',p=>p.snapshot.result.coords='pixel',p=>p.snapshot.result.seq=0,p=>p.snapshot.server_now=NaN])test('invalid identity/contract never publishes a usable palm: '+mutation,()=>{
 const p=packet();mutation(p);const result=handPipelineObservation(p,'cam',1000)
 assert.equal(result.controlValid,false);assert.equal(result.displayValid,false)
})
test('model failure, missing landmarks, malformed points and disabled detection stay visibly invalid',()=>{
 for(const mutation of [p=>p.snapshot.result.valid=false,p=>p.snapshot.result.observations=[],p=>p.snapshot.result.observations[0].landmarks[3].x=NaN,p=>p.snapshot.enabled=false]){
  const p=packet();p.snapshot.result.observations[0].landmarks=landmarks.map(v=>({...v}));mutation(p)
  assert.equal(handPipelineObservation(p,'cam',1000).controlValid,false)
 }
 const p=packet();p.snapshot.result.error='model unavailable';p.snapshot.result.valid=false
 assert.equal(handPipelineObservation(p,'cam',1000).error,'model unavailable')
})
test('client construction is inert and multiple subscribers share one read, with source change revoking input immediately',async()=>{
 const calls=[],jobs=new Map();let id=0,reply
 const state={enabled:false},client=createHandPipelineClient({state,schedule:fn=>{jobs.set(++id,fn);return id},cancel:n=>jobs.delete(n),now:()=>1000,
  fetcher:(path,options)=>{calls.push([path,options]);return new Promise(resolve=>{reply=()=>resolve({ok:true,json:async()=>packet().snapshot})})}})
 assert.deepEqual(calls,[])
 const updates=[],off=client.subscribe(value=>updates.push(value)),off2=client.subscribe(()=>{})
 assert.equal(calls.length,1);reply();for(let i=0;i<8;i++)await Promise.resolve()
 assert.equal(updates.length,1)
 const change=client.configure(false,'local','cam');await Promise.resolve()
 assert.equal(updates.at(-1),null);assert.equal(calls.at(-1)[0],'/api/vision/pipeline/config')
 reply();await change
 assert.ok(calls.every(([path])=>!path.endsWith('/frame')),'local source never uploads its own preview')
 off();off2();client.dispose()
})
test('only the operator that enabled the detection may release it',async()=>{
 const jobs=new Map();let id=0
 const state={enabled:false},client=createHandPipelineClient({state,schedule:fn=>{jobs.set(++id,fn);return id},cancel:n=>jobs.delete(n),now:()=>1000,
  fetcher:()=>Promise.resolve({ok:true,json:async()=>({ok:true,enabled:true,source:'local',session:'cam',epoch:1,server_now:1})})})
 await client.configure(true,'local','cam','camera-source')
 assert.deepEqual(client.owner(),{source:'local',session:'cam',token:'camera-source'})
 // 检测已被别人接管时，本方不得越权关停全局检测。
 assert.equal(await client.release('local','other-cam'),false)
 assert.equal(client.owner().session,'cam')
 // 同一个会话上还有另一个组件（画面组件）也在申请关闭：来源与会话都相同，
 // 只有身份标记能把两方分开，否则画面组件切页就会掐掉相机面板的检测。
 assert.equal(await client.release('local','cam','camera-frame'),false)
 assert.equal(client.owner().token,'camera-source')
 // 自己持有：允许关闭；关掉之后这份检测不再有主人。
 assert.equal(await client.release('local','cam','camera-source'),true)
 assert.equal(client.owner(),null)
 client.dispose()
})
