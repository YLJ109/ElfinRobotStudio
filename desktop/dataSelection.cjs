const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto')
function within(root,target){const relative=path.relative(root,target);return relative && !relative.startsWith('..'+path.sep) && relative!=='..' && !path.isAbsolute(relative)}
function selectedData(userData){
  const base=path.resolve(userData,'data'),file=path.join(userData,'data-selection.json')
  if(!fs.existsSync(file))return base
  const value=JSON.parse(fs.readFileSync(file,'utf8'))
  if(value.version===1 && value.default===true)return base
  if(value.version!==1 || typeof value.relative!=='string')throw Error('数据目录选择损坏，请恢复 data-selection.previous.json')
  const target=path.resolve(base,value.relative)
  if(!within(base,target) || !within(fs.realpathSync(base),fs.realpathSync(target)))throw Error('数据目录越界')
  const bytes=fs.readFileSync(path.join(target,'restore-manifest.json'))
  if(crypto.createHash('sha256').update(bytes).digest('hex')!==value.manifest_digest)throw Error('恢复副本清单已改变')
  const manifest=JSON.parse(bytes)
  if(manifest.version!==1 || manifest.copy_id!==value.copy_id)throw Error('恢复副本标识不符')
  return target
}
function selectData(userData,target,copyId,manifestDigest){
  const base=path.resolve(userData,'data');target=path.resolve(target)
  if(!/^[a-f0-9]{32}$/.test(copyId) || !within(base,target) || !within(fs.realpathSync(base),fs.realpathSync(target)))throw Error('恢复副本不属于本桌面数据目录')
  const bytes=fs.readFileSync(path.join(target,'restore-manifest.json'))
  if(typeof manifestDigest!=='string' || crypto.createHash('sha256').update(bytes).digest('hex')!==manifestDigest)throw Error('恢复清单不属于本次确认')
  const manifest=JSON.parse(bytes)
  if(manifest.version!==1 || manifest.copy_id!==copyId)throw Error('恢复副本身份不符')
  for(const [name,expected] of Object.entries(manifest.files)){
    const file=path.resolve(target,name)
    if(!within(target,file) || !within(fs.realpathSync(target),fs.realpathSync(file)) || crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')!==expected)throw Error('恢复副本已修改，请重新导入')
  }
  const file=path.join(userData,'data-selection.json'),temporary=file+'.tmp'
  if(fs.existsSync(file))fs.copyFileSync(file,path.join(userData,'data-selection.previous.json'))
  else fs.writeFileSync(path.join(userData,'data-selection.previous.json'),JSON.stringify({version:1,default:true}))
  try{
    const fd=fs.openSync(temporary,'w');try{fs.writeFileSync(fd,JSON.stringify({version:1,relative:path.relative(base,target),copy_id:copyId,manifest_digest:manifestDigest}));fs.fsyncSync(fd)}finally{fs.closeSync(fd)}
    fs.renameSync(temporary,file)
  }finally{if(fs.existsSync(temporary))fs.unlinkSync(temporary)}
}
module.exports={selectedData,selectData}
