import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'

for(const name of ['index','vision'])test(`${name}源码入口是同源应用，不依赖开发站点跳转`,()=>{
  const source=readFileSync(new URL(`../${name}.html`,import.meta.url),'utf8')
  assert.doesNotMatch(source,/http-equiv=["']refresh|location\.replace|(?:127\.0\.0\.1|localhost):5173/)
  assert.match(source,/<script[^>]+type="module"[^>]+src="\/src\//)
})

test('构建同时验证两个生产入口和源码身份',()=>{
  const pkg=JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8'))
  assert.equal(pkg.scripts.build,'node scripts/build-production.mjs')
  const builder=readFileSync(new URL('./build-production.mjs',import.meta.url),'utf8')
  assert.match(builder,/verifyProductionEntries/)
  assert.match(builder,/before !== fingerprint\(\)/)
})
