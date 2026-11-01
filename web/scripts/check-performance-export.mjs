import test from 'node:test'
import assert from 'node:assert/strict'
import {createPerformanceComparison,performanceIdentity,COMPARISON_LIMIT} from '../src/performanceComparison.js'
const build={verified:true,backend_sha256:'a'.repeat(64),frontend_sha256:'b'.repeat(64)}, viewport={width:1280,height:800,dpr:1}
const identity=s=>performanceIdentity({session_id:'session',tools:{mounted:{tool_id:'actual',revision:1}},...s},build,viewport)

test('sampling is explicit bounded and unmeasured remains unknown',()=>{
 const c=createPerformanceComparison();assert.equal(c.sample('A',{}),false);c.start('A',0)
 for(let i=0;i<125;i++)c.sample('A',{},null,i*1000)
 const record=c.finish('before');assert.equal(record.samples.length,COMPARISON_LIMIT);assert.equal(record.truncated,true);assert.equal(record.samples[0].vision.gpu_percent,null)
 assert.equal(c.sample('A',{}),false);c.start('A');c.sample('A',{});c.finish('after');assert.equal(c.export().unmeasured.includes('工件颜色轮廓检测速率'),true)
})
test('changed scene invalidates sampling and incompatible captures cannot be compared',()=>{
 const c=createPerformanceComparison();c.start('A');c.sample('A',{});c.finish('before');c.start('B')
 assert.throws(()=>c.sample('C',{}),/场景/);assert.equal(c.active,false);c.start('B');c.sample('B',{});assert.throws(()=>c.finish('after'),/不一致/)
 assert.throws(()=>c.export(),/同一场景/);assert.notEqual(identity({objects:[{id:'x',xyz:[0,0,0]}]}),identity({objects:[{id:'x',xyz:[1,0,0]}]}))
})
test('counter deltas and backend capture clock are measured without substituting receipt time',()=>{
 const c=createPerformanceComparison();c.start('A',0)
 c.sample('A',{}, {server_now:10,result:{clock_domain:'server',captured_at:9.9},stats:{submitted:10,inferred:5}},0)
 c.sample('A',{}, {server_now:11,result:{clock_domain:'server',captured_at:10.8},stats:{submitted:30,inferred:8}},1000)
 const rows=c.finish('before').samples;assert.equal(rows[1].vision.input_hz,20);assert.equal(rows[1].vision.detection_hz,3);assert.ok(Math.abs(rows[1].vision.frame_age_ms-200)<.001)
 c.start('A');c.sample('A',{});c.finish('after');const out=c.export();out.before.samples[0].metrics.fake=1;assert.equal(c.export().before.samples[0].metrics.fake,undefined)
})
test('remote clocks remain unknown and changed detector configuration cannot renew a comparison',()=>{
 const c=createPerformanceComparison();c.start('A');c.sample('A',{}, {source:'remote',session:'s',epoch:1,server_now:50,result:{clock_domain:'remote',captured_at:49}})
 assert.equal(c.latest.frame_age_ms,null)
 assert.throws(()=>c.sample('A',{}, {source:'remote',session:'s',epoch:2}),/检测来源/);assert.equal(c.active,false)
})

test('identity requires verified real dual SHA and includes actual session tool profile visual and viewport',()=>{
 const snap={session_id:'S',mode:'sim',tools:{mounted:{tool_id:'vacuum-reference',revision:2,state:'simulated'},tool:{id:'vacuum-reference',revision:'r2',tcp:{translation_mm:[0,0,5]}},visual:{kind:'vacuum-reference'}},settings:{sim_tool:{kind:'vacuum-reference'}},objects:[]}
 const id=JSON.parse(performanceIdentity(snap,build,viewport));assert.deepEqual(id.build,{backend_sha256:build.backend_sha256,frontend_sha256:build.frontend_sha256});assert.equal(id.session_id,'S');assert.equal(id.tool.mounted.tool_id,'vacuum-reference');assert.deepEqual(id.tool.visual,snap.tools.visual);assert.deepEqual(id.viewport,viewport)
 for(const invalid of [{},{...build,verified:false},{...build,backend_sha256:null},{...build,frontend_sha256:'unknown'}])assert.throws(()=>performanceIdentity(snap,invalid,viewport),/已验证版本/)
 const original=performanceIdentity(snap,build,viewport)
 for(const changed of [{...snap,session_id:'new'},{...snap,tools:{...snap.tools,mounted:{...snap.tools.mounted,revision:3}}},{...snap,settings:{sim_tool:null}}])assert.notEqual(performanceIdentity(changed,build,viewport),original)
 assert.notEqual(performanceIdentity(snap,build,{...viewport,dpr:2}),original)
})
