import test from 'node:test'
import assert from 'node:assert/strict'
import {shallowReactive,nextTick} from 'vue'
import {readFileSync} from 'node:fs'
import {createRemoteCamera,limitVideoSdp,trustedRemoteLink} from '../src/remoteCamera.ts'
const SDP='v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\nc=IN IP4 0.0.0.0\r\na=sendonly\r\n'
function fixture(options={}){
 const calls=[],peers=[],jobs=new Map();let stopped=0,id=0,constraints=null,params=null
 const track={applyConstraints:async value=>{constraints=value;if(options.constraintPromise)await options.constraintPromise},getSettings:()=>({width:1280,height:720,frameRate:15,...options.settings}),stop:()=>stopped++}
 const stream={getVideoTracks:()=>[track],getTracks:()=>[track]}
 const preview={state:shallowReactive({stream,status:'streaming',frameReady:true,frameSeq:1}),stop(){track.stop();this.state.stream=null;this.state.status='idle'}}
 class Peer{
  constructor(config){this.config=config;this.connectionState='new';this.iceGatheringState='complete';peers.push(this)}
  addTransceiver(value,config){this.transceiver={value,config};return {sender:{getParameters:()=>({encodings:[{}]}),setParameters:async value=>{params=value}}}}
  async createOffer(){return {type:'offer',sdp:SDP}}
  async setLocalDescription(value){this.localDescription=value}
  async setRemoteDescription(value){this.remoteDescription=value}
  close(){this.closed=true}
 }
 const request=async(path,init)=>{
  calls.push({path,init,body:JSON.parse(init.body)})
  if(options.request)return options.request(path,init)
  const body=path.endsWith('/join')?{ok:true,token:'secret',session:'remote-session'}:path.endsWith('/offer')?{type:'answer',sdp:'answer',session:'remote-session'}:path.endsWith('/heartbeat')?(options.heartbeat || {ok:true,state:{session:'remote-session'},result:{valid:true,source:'remote',session:'remote-session',control_valid:true,observations:[]}}):{ok:true}
  return {ok:true,json:async()=>body}
 }
 const remote=createRemoteCamera({preview,Peer,request,secure:()=>true,schedule:fn=>{jobs.set(++id,fn);return id},cancel:key=>jobs.delete(key),...options,request})
 return {remote,preview,calls,peers,jobs,get stopped(){return stopped},get constraints(){return constraints},get params(){return params}}
}

const response=body=>({ok:true,json:async()=>body})
function clockRequest(transform=value=>value){return async(path,init)=>{
 const body=JSON.parse(init.body)
 if(path.endsWith('/join'))return response({ok:true,token:'secret',session:'remote-session'})
 if(path.endsWith('/offer'))return response({type:'answer',sdp:'answer',session:'remote-session'})
 if(path.endsWith('/heartbeat'))return response(transform({ok:true,state:{session:'remote-session',clock:{status:'estimated',offset_ms:880,uncertainty_ms:20,scope:'heartbeat_round_trip',capture_age_available:true,control_qualified:true}},clock_probe:{nonce:'nonce-'+body.clock.sent_ms,client_sent_ms:body.clock.sent_ms,server_received_ms:1000,server_sent_ms:1001},result:{source:'remote',session:'remote-session',valid:true,control_valid:true}}))
 return response({ok:true})
}}
test('heartbeat probe acknowledgement bounded display only, existing stream reused',async()=>{
 let now=100;const h=fixture({now:()=>now++,request:clockRequest()});const stream=h.preview.state.stream
 assert.equal(await h.remote.startDetection('123'),true)
 assert.equal(h.peers.length,1);assert.equal(h.peers[0].transceiver.value,stream.getVideoTracks()[0]);assert.equal(h.remote.state.clock.uncertainty_ms,20)
 assert.equal(h.remote.state.clock.capture_age_available,false);assert.equal(h.remote.state.clock.control_qualified,false);assert.equal(h.remote.state.result.control_valid,false)
 const first=h.calls.find(c=>c.path.endsWith('/heartbeat'));assert.deepEqual(first.body,{clock:{sent_ms:100}})
 const job=[...h.jobs.entries()][0];h.jobs.delete(job[0]);job[1]();for(let i=0;i<20;i++)await Promise.resolve()
 const beats=h.calls.filter(c=>c.path.endsWith('/heartbeat'));assert.equal(beats.length,2);assert.deepEqual(beats[1].body.clock.previous,{nonce:'nonce-100',received_ms:101})
 h.remote.stop();assert.equal(h.remote.state.clock,null);assert.equal(h.peers[0].closed,true);assert.ok(h.stopped)
})
test('malformed probe never becomes estimated or grants control',async()=>{
 for(const patch of [{client_sent_ms:999},{nonce:''},{server_sent_ms:999},{server_received_ms:NaN}]){
  const h=fixture({now:()=>100,request:clockRequest(v=>({...v,clock_probe:{...v.clock_probe,...patch}}))})
  assert.equal(await h.remote.startDetection('123'),false);assert.equal(h.remote.state.phase,'error');assert.equal(h.remote.state.clock,null);assert.equal(h.remote.state.result,null);assert.equal(h.peers[0].closed,true);assert.ok(h.stopped)
 }
})
test('cancelled late heartbeat cannot restore clock, result or session',async()=>{
 let resolve,entered;const pending=new Promise(r=>resolve=r),waiting=new Promise(r=>entered=r),normal=clockRequest()
 const h=fixture({now:()=>100,request:async(path,init)=>{if(path.endsWith('/heartbeat')){entered();return pending}return normal(path,init)}})
 const starting=h.remote.startDetection('123');await waiting;h.remote.stop()
 resolve(response({ok:true,state:{session:'remote-session',clock:{status:'estimated',control_qualified:true}},clock_probe:{nonce:'late',client_sent_ms:100,server_received_ms:1000,server_sent_ms:1001},result:{source:'remote',session:'remote-session',valid:true}}))
 assert.equal(await starting,false);assert.equal(h.remote.state.clock,null);assert.equal(h.remote.state.result,null);assert.equal(h.remote.state.session,'');assert.equal(h.jobs.size,0);assert.equal(h.peers[0].closed,true)
})
test('legacy heartbeat without probe remains readonly and session changes reject clock',async()=>{
 const h=fixture({request:clockRequest(v=>({...v,clock_probe:null}))});assert.equal(await h.remote.startDetection('123'),true);assert.equal(h.remote.state.clock.control_qualified,false);h.remote.stop()
 const other=fixture({request:clockRequest(v=>({...v,state:{...v.state,session:'old'}}))});assert.equal(await other.remote.startDetection('123'),false);assert.equal(other.remote.state.clock,null)
})
