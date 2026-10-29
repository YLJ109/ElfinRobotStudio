const test=require('node:test')
const assert=require('node:assert/strict')
const path=require('node:path')
const {execFile}=require('node:child_process')
const {promisify}=require('node:util')
const run=promisify(execFile)
for(const file of ['backend_entry_test.py','camera_snapshot_test.py']) {
  test(`隔离Python入口回归：${file}`,async()=>{
    const root=path.resolve(__dirname,'../..')
    const result=await run(path.join(root,'.venv/Scripts/python.exe'),['-B',path.join(__dirname,file)],{
      cwd:root,timeout:15000,maxBuffer:128*1024,windowsHide:true,
      env:{...process.env,PYTHONIOENCODING:'utf-8',HUYAN_NO_REAL:'1'}
    })
    assert.match(result.stderr,/OK/)
  })
}
