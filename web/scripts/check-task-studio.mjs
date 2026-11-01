// User-authorized retirement. Original complete contracts retained with SHA manifest.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync,existsSync} from 'node:fs'
import {createHash} from 'node:crypto'
import vm from 'node:vm'
const read=name=>readFileSync(new URL('../src/'+name,import.meta.url),'utf8')
const store=read('store.js'),app=read('App.vue'),nav=read('components/NavRail.vue')
const chunk=store.slice(store.indexOf('export const SYSTEM_TABS'),store.indexOf('function savedNavigation()')).replaceAll('export ','')
const {normalizeNavigation,NAV_VIEWS}=vm.runInNewContext(chunk+';({normalizeNavigation,NAV_VIEWS})')
test('retired studio bookmarks and saved pages explicitly land on the program editor',()=>{
 for(const args of [{view:'studio'},{view:'studio',workTab:'points'},{view:'studio',workTab:'ai'}]){const n=normalizeNavigation(args);assert.equal(n.view,'work');assert.equal(n.workTab,'program')}
 assert.ok(!NAV_VIEWS.includes('studio'));assert.match(app,/\['sim','power','drive','studio'\]\.includes\(v\)/)
 assert.equal(normalizeNavigation({view:'work',workTab:'program'}).workTab,'program')
})
test('no official studio renderer, navigation or recording session import remains',()=>{
 assert.doesNotMatch(nav,/\['studio',|工艺/);assert.doesNotMatch(app,/TaskStudioPanel|studio:|engineering','studio/)
 for(const name of ['panels/TaskStudioPanel.vue','taskStudio.js'])assert.equal(existsSync(new URL('../src/'+name,import.meta.url)),false)
 assert.doesNotMatch(read('panels/AiPanel.vue'),/taskStudio|loadStudioDraft|读取工艺会话/)
 assert.doesNotMatch(read('panels/AiPanel.vue'),/taskStudio|studioDraftText|inspectionPayload|aiReadOnly/)
})
test('original retired files and test assertions remain byte-verifiable in the archive',()=>{
 const manifest=JSON.parse(readFileSync(new URL('../../tools/legacy_contracts_20261030/studio-retirement/manifest.json',import.meta.url),'utf8'))
 assert.ok(manifest.files.length>=10)
 for(const item of manifest.files){const bytes=readFileSync(new URL('../../'+item.archive,import.meta.url));assert.equal(createHash('sha256').update(bytes).digest('hex'),item.sha256)}
 assert.ok(manifest.files.some(item=>item.source==='web/scripts/check-task-studio.mjs'))
})
