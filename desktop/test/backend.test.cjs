const test=require('node:test')
const assert=require('node:assert/strict')
const http=require('node:http')
const {BackendSession,requestJson}=require('../backend.cjs')
test('未公布端口的自有启动进程可取消，不要求先启动完成',async()=>{
  const session=new BackendSession({});session.owned=true
  let killed=false
  session.child={pid:12345,exitCode:null,kill(){killed=true;this.exitCode=0;return true}}
  await session.close();assert.equal(killed,true)
})
test('进程未能创建时允许关闭，不调用无效kill',async()=>{
  const session=new BackendSession({});session.owned=true;session.failure='spawn失败'
  session.child={exitCode:null,kill(){throw new Error('不应kill不存在的进程')}}
  await session.close()
})
test('已公布端口但仍未完成就绪的服务也走认证停止',async t=>{
  const base=await server(t,(req,res)=>{
    assert.equal(req.url,'/api/desktop/shutdown')
    assert.ok(req.headers['x-elfin-desktop-key'])
    res.setHeader('content-type','application/json');res.end(JSON.stringify({ok:true,settled:true}))
  })
  const session=new BackendSession({});session.owned=true;session.startingBase=base
  await session.close();assert.equal(session.base,null)
})
async function server(t,handler){
  const server=http.createServer(handler)
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
  t.after(()=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections()}))
  return `http://127.0.0.1:${server.address().port}`
}
test('共享服务关闭只调用正常停止，不终止其他进程',async t=>{
  const requests=[]
  const base=await server(t,(req,res)=>{requests.push([req.method,req.url]);res.setHeader('content-type','application/json');res.end(JSON.stringify({ok:true,settled:true}))})
  const session=new BackendSession({});session.base=base
  await session.close();assert.deepEqual(requests,[['POST','/api/points/stop']]);assert.equal(session.base,null)
})
test('未确认停止时保留会话并阻止关闭',async t=>{
  const base=await server(t,(_req,res)=>{res.setHeader('content-type','application/json');res.end(JSON.stringify({ok:false,settled:false,why:'停止未知'}))})
  const session=new BackendSession({});session.base=base
  await assert.rejects(session.close(),/停止未知/);assert.equal(session.base,base)
})
test('私有服务退出携带本次会话凭据',async t=>{
  let key
  const base=await server(t,(req,res)=>{key=req.headers['x-elfin-desktop-key'];assert.equal(req.url,'/api/desktop/shutdown');res.setHeader('content-type','application/json');res.end(JSON.stringify({ok:true,settled:true}))})
  const session=new BackendSession({});session.base=base;session.owned=true
  await session.close();assert.equal(key,session.key)
})
test('错误HTTP状态返回原因，不被当成服务已就绪',async t=>{
  const base=await server(t,(_req,res)=>{res.statusCode=503;res.setHeader('content-type','application/json');res.end(JSON.stringify({why:'服务未就绪'}))})
  await assert.rejects(requestJson(base,'/api/state'),/服务未就绪/)
})
test('打包引导器和工作进程PID不同仍识别真实就绪，重试不重复启动',async t=>{
  const base=await server(t,(req,res)=>{
    assert.equal(req.url,'/api/desktop/status')
    assert.ok(req.headers['x-elfin-desktop-key'])
    res.setHeader('content-type','application/json');res.end(JSON.stringify({ready:true,pid:222}))
  })
  const session=new BackendSession({})
  const child={pid:111,exitCode:null};session.child=child;session.startingBase=base;session.owned=true
  assert.equal(await session.start(),base)
  assert.equal(session.backendPid,222);assert.equal(session.child,child)
})
