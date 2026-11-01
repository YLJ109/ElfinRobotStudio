const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm')
test('quality acceptance renderer scripts compile after JavaScript template cooking',()=>{
  const source=fs.readFileSync(path.join(__dirname,'..','qualitySmoke.cjs'),'utf8')
  let checked=0,draftReads=0
  for(const match of source.matchAll(/evaluate\((`[^`]*`)\)/g)){
    const literal=match[1]
    if(literal.includes('${'))continue
    const script=vm.runInNewContext(literal,Object.create(null),{timeout:1000})
    assert.doesNotThrow(()=>new vm.Script(script),`invalid generated renderer script: ${script}`)
    if(script.includes("map(line=>line.textContent).join('\\n')"))draftReads++
    checked++
  }
  assert.ok(checked>=15,'must compile actual renderer snippets, not just the CommonJS wrapper')
  assert.equal(draftReads,3,'both draft reads and exact editor replacement check must compile with escaped newlines')
})
