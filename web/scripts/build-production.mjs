import {createHash} from 'node:crypto'
import {existsSync, readFileSync, readdirSync, writeFileSync} from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {spawnSync} from 'node:child_process'

const root=fileURLToPath(new URL('../',import.meta.url))
const digest=bytes=>createHash('sha256').update(bytes).digest('hex')
function files(directory){
  return readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(entry=>{
    const target=path.join(directory,entry.name)
    if(entry.isSymbolicLink())throw new Error(`构建输入不接受链接：${target}`)
    return entry.isDirectory()?files(target):[target]
  })
}
function fingerprint(){
  const inputs=['src','public','scripts','package.json','package-lock.json','vite.config.js','vite.remote.config.js','index.html','vision.html','remote-camera.html',
    '../contracts'].flatMap(relative=>{
      const target=path.resolve(root,relative)
      if(!existsSync(target))return []
      return ['src','public','scripts','../contracts'].includes(relative)?files(target):[target]
    })
  for(const name of readdirSync(root).filter(name=>name==='.env'||name.startsWith('.env.')))inputs.push(path.join(root,name))
  return digest(inputs.sort().map(file=>`${path.relative(root,file)}:${digest(readFileSync(file))}`).join('\n'))
}
export function verifyProductionEntries(){
  for(const name of ['index','vision','remote-camera']){
    const html=readFileSync(path.join(root,'dist',`${name}.html`),'utf8')
    if(/http-equiv\s*=\s*["']refresh|location\.replace|(?:127\.0\.0\.1|localhost):5173/i.test(html))throw new Error(`${name}仍依赖开发站点`)
    const assets=[...html.matchAll(/(?:src|href)="(\.\/assets\/[^"?#]+)"/g)].map(match=>match[1])
    if(!assets.some(asset=>asset.endsWith('.js')))throw new Error(`${name}缺少生产脚本`)
    for(const asset of assets)if(!existsSync(path.join(root,'dist',asset)))throw new Error(`缺少生产资源：${asset}`)
    if(name==='remote-camera' && assets.some(asset=>!asset.startsWith('./assets/remote-camera/')))throw new Error('远程相机入口包含非访客资源')
  }
}
const before=fingerprint()
for(const args of [['scripts/prepare-vision-assets.mjs'],['node_modules/vite/bin/vite.js','build'],['node_modules/vite/bin/vite.js','build','--config','vite.remote.config.js']]){
  const result=spawnSync(process.execPath,args,{cwd:root,stdio:'inherit'})
  if(result.error)throw result.error
  if(result.status!==0)process.exit(result.status??1)
}
verifyProductionEntries()
if(before !== fingerprint())throw new Error('构建期间源码发生变化，请重新构建')
const artifacts=Object.fromEntries(files(path.join(root,'dist')).filter(file=>!file.endsWith('build-identity.json'))
  .map(file=>[path.relative(path.join(root,'dist'),file).replaceAll('\\','/'),digest(readFileSync(file))]))
writeFileSync(path.join(root,'dist/build-identity.json'),JSON.stringify({version:1,source_sha256:before,artifacts},null,2))
console.log('生产入口与源码身份校验通过')
