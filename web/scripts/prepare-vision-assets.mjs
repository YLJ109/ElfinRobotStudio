import { readFileSync, existsSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'
const root=path.resolve('public/vision')
const manifest=JSON.parse(readFileSync(path.join(root,'sources.json'),'utf8'))
const model=readFileSync(path.join(root,'hand_landmarker.task'))
const hash=createHash('sha256').update(model).digest('hex')
if(hash!==manifest.model.sha256)throw new Error('工作站手掌模型身份与来源清单不一致')
if(!existsSync(path.join(root,manifest.model.license_file)))throw new Error('缺少模型许可')
console.log('Workstation hand model verified; no browser inference runtime is copied')
