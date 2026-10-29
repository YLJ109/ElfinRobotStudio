const test=require('node:test')
const assert=require('node:assert/strict')
const fs=require('node:fs/promises')
const os=require('node:os')
const path=require('node:path')
const {BoundedLog}=require('../logSink.cjs')
test('日志队列有上限，正常退出会清空待写内容',async t=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'jiushu-log-'))
  t.after(()=>fs.rm(dir,{recursive:true,force:true}))
  const file=path.join(dir,'backend.log'),errors=[]
  const log=new BoundedLog(file,{maxBytes:1024,maxPending:64,onError:error=>errors.push(error.message)})
  for(let i=0;i<1000;i++)log.write('abcdefgh\n')
  assert.ok(log.pendingBytes<=64);assert.ok(log.droppedBytes>0)
  await log.end();assert.deepEqual(errors,[]);assert.equal(log.pendingBytes,0)
  assert.ok((await fs.readFile(file,'utf8')).includes('日志过载'))
  assert.equal(log.write('退出后不再写'),false)
})
test('日志轮转仅保留三个限量文件',async t=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'jiushu-rotate-'))
  t.after(()=>fs.rm(dir,{recursive:true,force:true}))
  const file=path.join(dir,'backend.log'),errors=[]
  const log=new BoundedLog(file,{maxBytes:32,onError:error=>errors.push(error.message)})
  for(let i=0;i<12;i++){log.write('abcdefghij\n');while(log.writing)await log.writing}
  await log.end();assert.deepEqual(errors,[])
  const files=await fs.readdir(dir);assert.equal(files.length,3)
  for(const name of files)assert.ok((await fs.stat(path.join(dir,name))).size<=32)
})
