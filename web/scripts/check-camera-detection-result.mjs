// Run the actual CameraFrame result consumer with independent image/result clocks.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {ref} from 'vue'
import {handPipelineObservation} from '../src/handPipeline.js'
import {createPalmTracker} from '../src/handFollowInput.js'
const source=readFileSync(new URL('../src/components/CameraFrame.vue',import.meta.url),'utf8')
const body=source.slice(source.indexOf('function consumeDetection()'),source.indexOf('function startHands()'))
function fixture({empty=false,session='current',age=.05}={}){
 const landmarks=Array.from({length:21},()=>({x:.5,y:.3,z:0}))
 for(const [index,x,y] of [[0,.5,.8],[5,.3,.4],[9,.45,.3],[13,.6,.35],[17,.7,.45]])landmarks[index]={x,y,z:0}
 const packet={receivedAt:1000,requestStartedAt:990,snapshot:{enabled:true,source:'local',session,epoch:1,server_now:20,result:{session,source:'local',epoch:1,seq:101,captured_at:20-age,received_at:20-age,clock_domain:'server',valid_until:20-age+.3,coords:'image_normalized',valid:true,control_valid:!empty,observations:empty?[]:[{kind:'hand',landmarks}],inference_started_at:19.96,inference_finished_at:19.98}}}
 let publications=0
 const tracker=createPalmTracker()
 const dependencies={props:{hands:true,session:'current'},pipelinePacket:packet,latestFrame:{seq:100},frameIsFresh:()=>true,handPipelineObservation,performance:{now:()=>1000},palmTracker:tracker,displayHold:{observe(){}},publishFollow:()=>publications++,schedulePalmExpiry(){}}
 const code=`let handFailed=false,detectorReady=false,lastObservationSeq=0,lastDetectionKey=null,resultTimes=[];const handStatus={value:''},inferenceMs={value:null},roundTripMs={value:null},detectorDelegate={value:''};\n${body}\nreturn {run:consumeDetection,read:()=>({handFailed,detectorReady,resultTimes,lastObservationSeq,handStatus:handStatus.value})}`
 const api=new Function(...Object.keys(dependencies),code)(...Object.values(dependencies))
 return {api,tracker,packet,get publications(){return publications}}
}
test('fresh backend inference ahead of a delayed preview is delivered to the palm tracker',()=>{
 const h=fixture();h.api.run()
 assert.equal(h.api.read().detectorReady,true)
 assert.equal(h.api.read().lastObservationSeq,101)
 assert.equal(h.api.read().resultTimes.length,1)
 assert.ok(h.tracker.read(1000).point)
 assert.equal(h.publications,1)
})
test('valid no-palm inference still advances measured detection FPS',()=>{
 const h=fixture({empty:true});h.api.run()
 assert.equal(h.api.read().detectorReady,false)
 assert.equal(h.api.read().resultTimes.length,1)
 assert.equal(h.tracker.read(1000).point,null)
})
test('foreign or expired observations cannot create fresh motion input',()=>{
 for(const changes of [{session:'old'},{age:.4}]){
  const h=fixture(changes);h.api.run()
  assert.equal(h.api.read().detectorReady,false)
  assert.equal(h.tracker.read(1000).point,null)
 }
})


test('duplicate image and poll delivery does not republish or restart skeleton interpolation',()=>{
 const h=fixture();h.api.run();h.api.run();h.api.run()
 assert.equal(h.publications,1);assert.equal(h.api.read().resultTimes.length,1)
 h.packet.snapshot.result.seq++
 h.api.run();assert.equal(h.publications,2)
})
