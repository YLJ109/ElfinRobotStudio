const test=require('node:test')
const assert=require('node:assert/strict')
const fs=require('node:fs')
const os=require('node:os')
const path=require('node:path')
const {sourceDigest,compatible}=require('../buildIdentity.cjs')

test('compatibility requires both current source and production page identity',()=>{
  const expected={version:1,backend_sha256:'code',frontend_sha256:'page'}
  assert.equal(compatible(expected,{...expected,verified:true}),true)
  for(const actual of [{...expected},{...expected,verified:false},{...expected,verified:true,backend_sha256:'old'},
      {...expected,verified:true,frontend_sha256:'old'},{version:1}])assert.equal(compatible(expected,actual),false)
})
test('source identity changes for source deletion and contracts, ignoring bytecode',t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'elfin-identity-'))
  assert.equal(path.dirname(path.resolve(root)),path.resolve(os.tmpdir()))
  assert.ok(path.basename(root).startsWith('elfin-identity-'))
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}))
  fs.mkdirSync(path.join(root,'backend'));fs.mkdirSync(path.join(root,'contracts'))
  fs.writeFileSync(path.join(root,'backend/main.py'),'a');fs.writeFileSync(path.join(root,'contracts/actions.json'),'{}')
  const first=sourceDigest(root)
  fs.writeFileSync(path.join(root,'backend/main.pyc'),'ignored');assert.equal(sourceDigest(root),first)
  fs.writeFileSync(path.join(root,'contracts/actions.json'),'{"version":2}');assert.notEqual(sourceDigest(root),first)
  const next=sourceDigest(root);fs.unlinkSync(path.join(root,'backend/main.py'));assert.notEqual(sourceDigest(root),next)
})
