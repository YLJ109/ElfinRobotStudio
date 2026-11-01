const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm')
test('entry acceptance renderer snippets compile after template cooking without device write routes',()=>{
 const source=fs.readFileSync(path.join(__dirname,'..','entryTruthSmoke.cjs'),'utf8');let count=0
 for(const match of source.matchAll(/evaluate\((`[^`]*`)\)/g)){
   if(match[1].includes('${'))continue
   const script=vm.runInNewContext(match[1],Object.create(null),{timeout:1000});assert.doesNotThrow(()=>new vm.Script(script));count++
 }
 assert.ok(count>=15)
 assert.doesNotMatch(source,/request\(['"]\/api\/(?:real|cameras\/scan|cameras\/open|vision\/remote\/(?:pair|join|offer)|movej|movel|program\/start)/)
})
