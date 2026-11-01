import test from 'node:test'
import assert from 'node:assert/strict'
import {createBrowserCameraPreview,cameraPermissionMessage} from '../src/browserCameraPreview.ts'
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function stream(){let stopped=0;const listeners=new Map(),track={stop(){stopped++},addEventListener:(n,fn)=>listeners.set(n,fn),removeEventListener:(n,fn)=>{if(listeners.get(n)===fn)listeners.delete(n)}};return {getTracks:()=>[track],getVideoTracks:()=>[track],stopped:()=>stopped,end:()=>listeners.get('ended')?.()}}
function fixture(options={}){const calls=[],revoked=[],jobs=new Map();let id=0
 const preview=createBrowserCameraPreview({mediaDevices:{getUserMedia:async value=>{calls.push(value);return options.pending?options.pending.promise:stream()}},secure:()=>true,revoke:why=>revoked.push(why),schedule:fn=>{jobs.set(++id,fn);return id},cancel:key=>jobs.delete(key),...options})
 return {preview,calls,revoked,jobs}}
test('construction does not request permission, enumerate, upload, or infer; explicit start uses one local stream',async()=>{
 const h=fixture();assert.deepEqual(h.calls,[]);assert.equal(h.preview.state.status,'idle')
 assert.equal(await h.preview.start(),true);assert.equal(h.calls.length,1);assert.equal(h.calls[0].audio,false)
 assert.equal(h.preview.state.status,'streaming');assert.ok(h.preview.state.session)
 const old=h.preview.state.stream;h.preview.stop();assert.equal(old.stopped(),1);assert.equal(h.preview.state.stream,null)
})
test('quick close before permission resolves stops the late granted stream and cannot reopen preview',async()=>{
 const pending=deferred(),h=fixture({pending}),granted=stream()
 const opening=h.preview.start();h.preview.stop();pending.resolve(granted)
 assert.equal(await opening,false);assert.equal(granted.stopped(),1);assert.equal(h.preview.state.status,'idle')
})
test('permission timeout is bounded and its late grant is released',async()=>{
 const pending=deferred(),h=fixture({pending}),granted=stream()
 const opening=h.preview.start();for(const fn of h.jobs.values())fn()
 assert.equal(await opening,false);assert.equal(h.preview.state.status,'error');assert.match(h.preview.state.error,/超时/)
 pending.resolve(granted);for(let i=0;i<4;i++)await Promise.resolve();assert.equal(granted.stopped(),1)
})
test('duplicate pending starts never request a second camera and switching releases the previous stream',async()=>{
 const pending=deferred(),h=fixture({pending}),first=stream()
 const opening=h.preview.start('first');assert.equal(await h.preview.start('second'),false)
 pending.resolve(first);await opening;assert.equal(h.calls.length,1)
 await h.preview.start('second');assert.ok(first.stopped()>0)
 h.preview.stop()
})
test('insecure, denied, occupied and missing devices give distinct errors without pretending streaming',async()=>{
 const h=fixture({secure:()=>false});assert.equal(await h.preview.start(),false);assert.deepEqual(h.calls,[]);assert.match(h.preview.state.error,/HTTPS/)
 for(const name of ['NotAllowedError','NotReadableError','NotFoundError']){
  const p=createBrowserCameraPreview({secure:()=>true,mediaDevices:{getUserMedia:async()=>{throw {name}}}})
  assert.equal(await p.start(),false);assert.equal(p.state.status,'error');assert.equal(p.state.error,cameraPermissionMessage({name}));p.stop()
 }
})
test('track ending, hidden page and page exit release resources and revoke old control input',async()=>{
 const h=fixture(),events=new Map(),win={addEventListener:(n,fn)=>events.set(n,fn),removeEventListener:n=>events.delete(n)},doc={...win,hidden:false}
 const dispose=h.preview.lifecycle(win,doc)
 await h.preview.start();const first=h.preview.state.stream;first.end();assert.equal(first.stopped(),1);assert.equal(h.preview.state.status,'error')
 await h.preview.start();const next=h.preview.state.stream;doc.hidden=true;events.get('visibilitychange')();assert.equal(next.stopped(),1)
 doc.hidden=false;assert.equal(h.preview.state.status,'idle','returning never reopens')
 dispose();assert.equal(events.size,0);assert.ok(h.revoked.length>=3)
})


test('cancel releases a pending permission request immediately even if the browser prompt never resolves',async()=>{
 const pending=deferred(),h=fixture({pending})
 const request=h.preview.start();h.preview.stop()
 assert.equal(await request,false);assert.equal(h.preview.state.status,'idle');assert.equal(h.jobs.size,0)
})
