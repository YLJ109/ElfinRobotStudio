const fs=require('node:fs')
const path=require('node:path')
const crypto=require('node:crypto')
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex')
function sourceDigest(root){
  const rows=[]
  function walk(directory,extension){
    for(const entry of fs.readdirSync(directory,{withFileTypes:true})){
      const file=path.join(directory,entry.name)
      if(entry.isSymbolicLink())throw new Error('Source identity does not accept links')
      if(entry.isDirectory())walk(file,extension)
      else if(entry.name.endsWith(extension))rows.push([path.relative(root,file).replaceAll('\\','/'),digest(fs.readFileSync(file))])
    }
  }
  walk(path.join(root,'backend'),'.py');walk(path.join(root,'contracts'),'.json')
  rows.sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)
  return digest(rows.map(([name,hash])=>name+'\0'+hash+'\n').join(''))
}
function currentIdentity(root){
  return {version:1,backend_sha256:sourceDigest(root),frontend_sha256:JSON.parse(fs.readFileSync(path.join(root,'web/dist/build-identity.json'),'utf8')).source_sha256}
}
function compatible(expected,actual){
  return expected.version===actual?.version && actual.verified===true &&
    typeof expected.backend_sha256==='string' && expected.backend_sha256===actual.backend_sha256 &&
    typeof expected.frontend_sha256==='string' && expected.frontend_sha256===actual.frontend_sha256
}
module.exports={sourceDigest,currentIdentity,compatible}
