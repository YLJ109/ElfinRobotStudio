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

import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {ref,onMounted,onBeforeUnmount} from 'vue'
const reply=value=>({ok:true,json:async()=>value})
test('workpiece capability is selected by trusted station and client reuses one sendonly stream',async()=>{
 const calls=[];const h=fixture({request:async(path,init)=>{calls.push(path);return reply(path.endsWith('/join')?{ok:true,token:'secret',session:'remote-session',detector:'workpiece'}:path.endsWith('/offer')?{type:'answer',sdp:'answer',session:'remote-session'}:path.endsWith('/heartbeat')?{ok:true,state:{session:'remote-session',detector:'workpiece'},result:{source:'remote',session:'remote-session',objects:[{color:'red',shape:'circle'}],observations:[{color:'red',shape:'circle'}],valid:true,captured_at:null,control_valid:false}}:{ok:true})}})
 const stream=h.preview.state.stream;assert.equal(await h.remote.startDetection('123'),true);assert.equal(h.remote.state.detector,'workpiece');assert.equal(h.remote.state.result.objects[0].shape,'circle');assert.equal(h.peers.length,1);assert.equal(h.peers[0].transceiver.value,stream.getVideoTracks()[0]);assert.equal(h.peers[0].transceiver.config.direction,'sendonly');assert.equal(h.remote.state.result.control_valid,false);assert.ok(!('image' in h.remote.state.result));h.remote.stop()
})
test('detector switch cannot silently reinterpret an old result/session',async()=>{
 const h=fixture({request:async(path)=>reply(path.endsWith('/join')?{ok:true,token:'secret',session:'remote-session',detector:'workpiece'}:path.endsWith('/offer')?{type:'answer',sdp:'answer',session:'remote-session'}:path.endsWith('/heartbeat')?{ok:true,state:{session:'remote-session',detector:'hand'},result:null}:{ok:true})})
 assert.equal(await h.remote.startDetection('123'),false);assert.equal(h.remote.state.result,null);assert.equal(h.peers[0].closed,true);assert.ok(h.stopped)
})
test('actual pairing SFC sends explicit detector, rendering uses no second inference or echoed video',async()=>{
 const source=readFileSync(new URL('../src/components/RemoteCameraPairing.vue',import.meta.url),'utf8'),parsed=parse(source).descriptor,calls=[]
 const compiled=compileScript(parsed,{id:'pair-mode'}),code=compiled.content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
 const request=async(path,init)=>{calls.push({path,body:init.body?JSON.parse(init.body):null});return reply(path.endsWith('/pair')?{ok:true,code:'pair',expires_in:120,detector:'workpiece'}:{ok:true,available:true,phase:'pairing',detector:'workpiece'})}
 const panel=new Function('ref','onMounted','onBeforeUnmount','trustedRemoteLink','fetch',code)(ref,()=>{},()=>{},()=>'',request).setup({}, {expose(){}})
 panel.detector.value='workpiece';await panel.act('pair');assert.deepEqual(calls[0].body,{detector:'workpiece'})
 assert.deepEqual(compileTemplate({source:parsed.template.content,filename:'RemoteCameraPairing.vue',id:'pair-mode',compilerOptions:{bindingMetadata:compiled.bindings}}).errors,[])
 for(const path of ['RemoteCameraStation.vue','VisionStation.vue']){const value=parse(readFileSync(new URL('../src/'+path,import.meta.url),'utf8')).descriptor;assert.deepEqual(compileTemplate({source:value.template.content,filename:path,id:path}).errors,[])}
 const station=readFileSync(new URL('../src/RemoteCameraStation.vue',import.meta.url),'utf8');assert.match(station,/工件颜色与轮廓检测/);assert.doesNotMatch(station,/result\.image|createHandDetector|getUserMedia/)
})
