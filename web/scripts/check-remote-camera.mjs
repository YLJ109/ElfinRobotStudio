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
 const remote=createRemoteCamera({preview,Peer,request,secure:()=>true,schedule:fn=>{jobs.set(++id,fn);return id},cancel:key=>jobs.delete(key),...options})
 return {remote,preview,calls,peers,jobs,get stopped(){return stopped},get constraints(){return constraints},get params(){return params}}
}
test('default preview is inert: no pair, network, peer or second capture; explicit detection reuses same stream',async()=>{
 const h=fixture();assert.equal(h.calls.length,0);assert.equal(h.peers.length,0)
 const stream=h.preview.state.stream
 assert.equal(await h.remote.startDetection('123456'),true)
 assert.equal(h.peers[0].transceiver.value,stream.getVideoTracks()[0])
 assert.deepEqual(h.peers[0].config,{iceServers:[]});assert.equal(h.peers[0].transceiver.config.direction,'sendonly')
 assert.deepEqual(h.constraints,{width:{max:1280},height:{max:720},frameRate:{max:15}})
 assert.deepEqual(h.params.encodings,[{maxBitrate:1500000,maxFramerate:15}])
 assert.match(h.calls.find(c=>c.path.endsWith('/offer')).body.sdp,/b=AS:1500\r\n/)
 assert.equal(h.calls.find(c=>c.path.endsWith('/offer')).init.headers.Authorization,'Bearer secret')
 assert.equal(h.remote.state.phase,'detecting');assert.equal(h.remote.state.result.control_valid,false)
 assert.equal('token' in h.remote.state,false);h.remote.stop();assert.equal(h.peers[0].closed,true);assert.ok(h.stopped)
 assert.ok(h.calls.some(c=>c.path.endsWith('/leave')))
})
test('HTTPS and explicit preview are prerequisites with no join request',async()=>{
 const h=fixture({secure:()=>false});assert.equal(await h.remote.startDetection('123'),false);assert.equal(h.calls.length,0)
 const p=fixture();p.preview.stop();assert.equal(await p.remote.startDetection('123'),false);assert.equal(p.calls.length,0)
})
test('late join after cancellation cannot create peer and releases newly issued token',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),calls=[]
 const h=fixture({request:async(path,init)=>{calls.push(path);return path.endsWith('/join')?pending:{ok:true,json:async()=>({ok:true})}}})
 const starting=h.remote.startDetection('123');h.remote.stop()
 resolve({ok:true,json:async()=>({ok:true,token:'late',session:'retired'})})
 assert.equal(await starting,false);assert.equal(h.peers.length,0);assert.ok(calls.some(p=>p.endsWith('/leave')));assert.ok(h.stopped)
})
test('detector failure, unexpected video and disconnect close peer/tracks without success',async()=>{
 for(const failure of ['detector','video','disconnect']){
  const h=fixture();await h.remote.startDetection('123')
  if(failure==='video')h.peers[0].ontrack()
  else if(failure==='disconnect'){h.peers[0].connectionState='disconnected';h.peers[0].onconnectionstatechange()}
  else {h.remote.state.result=null;h.preview.state.frameReady=false;await nextTick()}
  assert.equal(h.remote.state.phase,'error');assert.equal(h.remote.state.result,null);assert.equal(h.peers[0].closed,true);assert.ok(h.stopped)
 }
})
test('permission/size rejection does not continue negotiation and retires token',async()=>{
 const h=fixture({settings:{width:1920}});assert.equal(await h.remote.startDetection('123'),false)
 assert.equal(h.peers.length,0);assert.match(h.remote.state.error,/尺寸/);assert.ok(h.calls.some(c=>c.path.endsWith('/leave')))
})
test('model failure, stale session and revoked session never report successful detection',async()=>{
 for(const heartbeat of [
  {state:{session:'remote-session'},result:{source:'remote',session:'remote-session',valid:false,error:'模型不可用'}},
  {state:{session:'remote-session'},result:{source:'remote',session:'old-session',valid:true}},
  {state:{session:null},result:null},
 ]){
  const h=fixture({heartbeat});assert.equal(await h.remote.startDetection('123'),false)
  assert.equal(h.remote.state.phase,'error');assert.equal(h.remote.state.result,null);assert.ok(h.stopped);assert.ok(h.peers[0].closed)
 }
})
test('cancel and timeout settle hung browser negotiation without starting a peer later',async()=>{
 for(const cancel of [true,false]){
  let resolve;const constraintPromise=new Promise(r=>resolve=r),h=fixture({constraintPromise})
  const pending=h.remote.startDetection('123')
  for(let i=0;i<8;i++)await Promise.resolve()
  if(cancel)h.remote.stop();else for(const fn of [...h.jobs.values()])fn()
  assert.equal(await pending,false);resolve();await Promise.resolve()
  assert.equal(h.peers.length,0);assert.ok(h.stopped);assert.equal(h.jobs.size,0)
 }
})
test('hidden/pagehide stop all resources and next start requires a new preview',async()=>{
 for(const event of ['visibilitychange','pagehide']){
  const win=new EventTarget(),doc=new EventTarget();doc.hidden=true
  const h=fixture(),release=h.remote.lifecycle(win,doc);await h.remote.startDetection('123')
  ;(event==='pagehide'?win:doc).dispatchEvent(new Event(event))
  assert.equal(h.remote.state.phase,'idle');assert.ok(h.stopped);assert.equal(h.jobs.size,0)
  assert.equal(await h.remote.startDetection('123'),false);release()
 }
})
test('SDP limits are applied only to video and reject audio/data/multiple streams',()=>{
 assert.match(limitVideoSdp(SDP.replace('a=sendonly','b=AS:9000\r\na=sendonly')),/b=AS:1500/)
 for(const invalid of [SDP+'m=audio 9 RTP/AVP 0\r\n',SDP+SDP,SDP+'m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n'])assert.throws(()=>limitVideoSdp(invalid))
})
test('pairing link rejects cross-origin, HTTP, extra path and embedded credentials',()=>{
 const origin='https://station.example:8711'
 assert.equal(trustedRemoteLink(origin+'/remote-camera.html',origin),origin+'/remote-camera.html')
 for(const value of ['http://station.example/remote-camera.html','https://other/remote-camera.html',origin+'/index.html',origin+'/remote-camera.html?token=secret','https://user@station.example:8711/remote-camera.html'])assert.equal(trustedRemoteLink(value,origin),'')
})
test('visitor entry has isolated imports and dedicated assets with no privileged SPA graph',()=>{
 const main=readFileSync(new URL('../src/remoteCameraMain.ts',import.meta.url),'utf8')
 assert.doesNotMatch(main,/App.vue|store|main.js/)
 for(const file of ['remoteCamera.ts','RemoteCameraStation.vue','components/BrowserCameraPreview.vue','browserCameraPreview.ts']){
  const source=readFileSync(new URL('../src/'+file,import.meta.url),'utf8');assert.doesNotMatch(source,/from ['"].*(?:store|App.vue)|\/api\/(?:control|robot|tools)|publishHandFollowInput/)
 }
 const build=readFileSync(new URL('../vite.remote.config.js',import.meta.url),'utf8');assert.match(build,/inlineDynamicImports:true/);assert.match(build,/assets\/remote-camera\//)
})
