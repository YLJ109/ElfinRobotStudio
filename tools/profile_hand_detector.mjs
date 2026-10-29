// 本地模型对照：只处理用户给出的截图，不连接摄像头、不加载控制页面。
// node tools/profile_hand_detector.mjs <chrome.exe> <截图.png>
import {spawn} from 'node:child_process'
import {readFile, mkdtemp} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join,resolve} from 'node:path'
import {createServer} from 'node:http'
const [chromePath,samplePath]=process.argv.slice(2)
if(!chromePath||!samplePath)throw new Error('需要Chrome路径与用户截图路径')
const root=resolve('web/public/vision'),profile=await mkdtemp(join(tmpdir(),'elfin-detector-'))
const sample=await readFile(samplePath)
const worker=`
importScripts('/vision/vision_bundle.js');
self.onmessage=async({data})=>{
 try{
 const files=await Vision.FilesetResolver.forVisionTasks('/vision/wasm');
 const started=performance.now();
 const detector=await Vision.HandLandmarker.createFromOptions(files,{
 baseOptions:{modelAssetPath:'/vision/hand_landmarker.task',delegate:data.delegate},
 canvas:new OffscreenCanvas(1,1),runningMode:'VIDEO',numHands:1,
 minHandDetectionConfidence:.5,minHandPresenceConfidence:.5,minTrackingConfidence:.5});
 const setupMs=performance.now()-started;
 const image=await createImageBitmap(data.blob),canvas=new OffscreenCanvas(640,480);
 const ctx=canvas.getContext('2d');
 // 用户截图右上摄像头原图，排除机器人场景；此数据只用于同图性能对照。
 ctx.drawImage(image,1549,128,338,253,0,0,640,480);image.close();
 const bitmap=await createImageBitmap(canvas),times=[],counts=[];
 for(let i=0;i<18;i++){
 const t=performance.now(),result=detector.detectForVideo(bitmap,i*67+1);
 const ms=performance.now()-t;if(i>=3){times.push(ms);counts.push(result.landmarks?.length||0)}
 }
 bitmap.close();detector.close();
 self.postMessage({delegate:data.delegate,setupMs,times,counts});
 }catch(error){self.postMessage({delegate:data.delegate,error:String(error)})}
};`
const server=createServer(async(req,res)=>{
 try{
 const path=new URL(req.url,'http://local').pathname
 if(path==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>Local detector benchmark</title>');return}
 if(path==='/sample'){res.setHeader('Content-Type','image/png');res.end(sample);return}
 if(path==='/worker.js'){res.setHeader('Content-Type','text/javascript');res.end(worker);return}
 if(!/^\/vision\/[a-zA-Z0-9_./-]+$/.test(path)||path.includes('..')){res.writeHead(404).end();return}
 const file=join(root,path.slice(8));res.setHeader('Content-Type',file.endsWith('.wasm')?'application/wasm':file.endsWith('.js')?'text/javascript':'application/octet-stream');res.end(await readFile(file))
 }catch{res.writeHead(404).end()}
})
await new Promise(r=>server.listen(0,'127.0.0.1',r))
const address=`http://127.0.0.1:${server.address().port}`
const child=spawn(chromePath,['--headless=new','--no-first-run','--no-default-browser-check',`--user-data-dir=${profile}`,'--remote-debugging-port=0',address],{stdio:'ignore',windowsHide:true})
const pause=ms=>new Promise(r=>setTimeout(r,ms));let ws
const pending=new Map();let id=0
try{
 let port
 for(let i=0;i<80;i++){try{port=Number((await readFile(join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]);break}catch{await pause(100)}}
 if(!port)throw new Error('独立测试浏览器未就绪')
 const tabs=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json(),tab=tabs.find(t=>t.type==='page')
 ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej})
 ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(new Error(JSON.stringify(m.error))):p.resolve(m.result)}}}
 const send=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))})
 await send('Page.enable');await send('Page.navigate',{url:address})
 for(let i=0;i<80;i++){
 const loaded=await send('Runtime.evaluate',{expression:`location.origin===${JSON.stringify(address)} && document.readyState==='complete'`,returnByValue:true})
 if(loaded.result.value)break
 if(i===79)throw new Error('测试页面未加载')
 await pause(100)
 }
 const expression=`(async()=>{
 const blob=await(await fetch('/sample')).blob(),results=[];
 for(const delegate of ['GPU','CPU','CPU','GPU']){
 const result=await new Promise(resolve=>{const w=new Worker('/worker.js');
 const timer=setTimeout(()=>{w.terminate();resolve({delegate,error:'timeout'})},25000);
 w.onmessage=({data})=>{clearTimeout(timer);w.terminate();resolve(data)};
 w.onerror=e=>{clearTimeout(timer);w.terminate();resolve({delegate,error:e.message})};
 w.postMessage({delegate,blob});});results.push(result);
 }
 return {kind:'local model, repeated screenshot crop, isolated headless browser; not live FPS',results};})()`
 const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,timeout:115000})
 if(r.exceptionDetails)throw new Error(r.exceptionDetails.text)
 console.log(JSON.stringify(r.result.value,null,2))
 await send('Browser.close').catch(()=>{})
}finally{ws?.close();server.close();if(child.exitCode===null)child.kill()}
