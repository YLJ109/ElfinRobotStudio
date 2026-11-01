import test from 'node:test'
import assert from 'node:assert/strict'
import {acceptanceView} from '../src/acceptanceView.js'
const sha='a'.repeat(64),other='b'.repeat(64)
const value=()=>({ok:true,build:{backend_sha256:sha,frontend_sha256:other,verified:true},matches_current:true,evidence:{backend_sha256:sha,frontend_sha256:other,features:[{id:'APP-001',title:'场景编辑',source:true,offline:true,ui:true,field:false,unverified:['真实设备'],evidence:['output/scene.png']}]}})
test('current verified fingerprints report per feature and never grant field validation from UI',()=>{
 const view=acceptanceView(value());assert.equal(view.current,true);assert.equal(view.rows[0].offline,'已通过');assert.equal(view.rows[0].field,'未现场验证');assert.deepEqual(view.rows[0].unverified,['真实设备'])
})
test('stale, unverified or inconsistent identity is historical and current checks remain pending',()=>{
 for(const change of [v=>v.matches_current=false,v=>v.build.verified=false,v=>v.evidence.frontend_sha256=sha]){const v=value();change(v);const view=acceptanceView(v);assert.equal(view.current,false);assert.equal(view.rows[0].offline,'当前待核验');assert.equal(view.rows[0].ui,'当前待核验');assert.match(view.rows[0].history,/界面已通过/)}
})
test('missing manifest and malformed evidence never fabricate accepted features',()=>{
 assert.deepEqual(acceptanceView({ok:true,build:{},evidence:null}).rows,[])
 const v=value();v.evidence.features[0].ui='true';assert.equal(acceptanceView(v).rows[0].ui,'未验证')
})
