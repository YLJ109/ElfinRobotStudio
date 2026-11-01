const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm')
test('task smoke renderer snippets compile after template cooking and use visible split navigation',()=>{
 let checked=0
 for(const name of ['practicalSmoke.cjs','renewalSmoke.cjs','robotPlaySmoke.cjs']){
  const source=fs.readFileSync(path.join(__dirname,'..',name),'utf8')
  assert.doesNotMatch(source,/title="功能"/)
  for(const match of source.matchAll(/evaluate\((`[^`]*`)\)/g)){
   if(match[1].includes('${'))continue
   const script=vm.runInNewContext(match[1],Object.create(null),{timeout:1000})
   assert.doesNotThrow(()=>new vm.Script(script),script);checked++
  }
 }
 assert.ok(checked>30,'must check cooked real renderer operations')
})
test('legacy play entry delegates official workflows and cannot report success after a failed run',async()=>{
 const filename=path.join(__dirname,'..','playSmoke.cjs'),source=fs.readFileSync(filename,'utf8')
 for(const fail of [false,true]){
  const mod={exports:{}},report={};let calls=0
  vm.runInNewContext(source,{module:mod,exports:mod.exports,process:{env:{HUYAN_NO_REAL:'1'}},require(name){
   if(name==='node:assert/strict')return assert
   assert.equal(name,'./renewalSmoke.cjs');return {verifyRenewal:async options=>{calls++;if(fail)throw Error('真实运动失败');options.report.renewal={completed:true,chessPlaced:2};options.report.robotPlay={completed:true,score:{total:3,correct:3,score:100}}}}
  }},{filename})
  if(fail){await assert.rejects(mod.exports.verifyPlay({report}),/真实运动失败/);assert.equal(report.play.completed,false)}
  else {await mod.exports.verifyPlay({report});assert.equal(report.play.completed,true);assert.equal(report.play.sorted,3)}
  assert.equal(calls,1)
 }
})
