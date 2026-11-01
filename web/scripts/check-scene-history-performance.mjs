// CPU-only actual App function fixture. No UI, GPU, service or camera.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {performance} from 'node:perf_hooks'
import {readTabState} from '../src/workspaceState.js'

const source=readFileSync(new URL('../src/App.vue',import.meta.url),'utf8')
const body=source.slice(source.indexOf('function restoreSceneHistory()'),source.indexOf('function onResize()'))
function fixture({frame={session_id:'s1'},scene=true,savedSession='s1',restoreResult=true}={}){
 let current=frame,reads=0,restores=0,cleared=false
 const saved=JSON.stringify({session:savedSession,model:'elfin5',tool:'pen',state:{trail:Array.from({length:4096},(_,i)=>[i*.001,i*.002,i*.003])}})
 const old=globalThis.sessionStorage
 globalThis.sessionStorage={getItem(){reads++;return saved}}
 const api=scene?{restoreHistory(){restores++;return restoreResult},clearTrail(){cleared=true}}:null
 const restore=new Function('latestFrame','readTabState','visualModel','toolSignature','scene','visualTool',
  'let restoredHistorySession=null;'+body+';return restoreSceneHistory')(()=>current,readTabState,{value:'elfin5'},()=> 'pen',api,{})
 return {restore,setFrame(value){current=value},clear(){api?.clearTrail()},get reads(){return reads},get restores(){return restores},get cleared(){return cleared},close(){if(old===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=old}}
}
test('1000 same-session state frames read and parse saved history once, instead of every frame',()=>{
 const h=fixture();try{
  const start=performance.now()
  for(let i=0;i<1000;i++)h.restore()
  console.log(JSON.stringify({fixture:'saved-scene-history',frames:1000,storageReads:h.reads,restoreCalls:h.restores,cpuMs:+(performance.now()-start).toFixed(2)}))
  assert.equal(h.reads,1);assert.equal(h.restores,1)
 }finally{h.close()}
})
test('unready scene or missing state never reads history; late first state restores only once',()=>{
 for(const options of [{scene:false},{frame:null}]){
  const h=fixture(options);try{for(let i=0;i<100;i++)h.restore();assert.equal(h.reads,0)}finally{h.close()}
 }
 const h=fixture({frame:null});try{h.restore();h.setFrame({session_id:'s1'});h.restore();h.restore();assert.equal(h.restores,1);assert.equal(h.reads,1)}finally{h.close()}
})
test('session change cannot replay old history; failed restore or clearing is not retried by subsequent frames',()=>{
 const h=fixture();try{
  h.restore();h.clear();for(let i=0;i<100;i++)h.restore()
  assert.equal(h.cleared,true);assert.equal(h.restores,1)
  h.setFrame({session_id:'s2'});h.restore();h.restore()
  assert.equal(h.reads,2);assert.equal(h.restores,1)
 }finally{h.close()}
 const failure=fixture({restoreResult:false});try{for(let i=0;i<100;i++)failure.restore();assert.equal(failure.reads,1);assert.equal(failure.restores,1)}finally{failure.close()}
})
