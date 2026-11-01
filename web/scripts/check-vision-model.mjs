import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync,existsSync} from 'node:fs'
import {createHash} from 'node:crypto'
test('workstation model bytes match the provenance manifest and retain the model license',()=>{
 const root=new URL('../public/vision/',import.meta.url),source=JSON.parse(readFileSync(new URL('sources.json',root),'utf8'))
 const model=readFileSync(new URL('hand_landmarker.task',root))
 assert.equal(createHash('sha256').update(model).digest('hex'),source.model.sha256)
 assert.ok(existsSync(new URL(source.model.license_file,root)));assert.match(source.model.source,/mediapipe-models\/hand_landmarker/)
 assert.equal(existsSync(new URL('hand-worker.js',root)),false)
 assert.equal(existsSync(new URL('vision_bundle.js',root)),false)
 assert.equal(existsSync(new URL('wasm',root)),false)
 const pkg=JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8'))
 assert.equal(pkg.dependencies['@mediapipe/tasks-vision'],undefined)
})
