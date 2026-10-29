import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {runInNewContext} from 'node:vm'

for(const name of ['index','vision'])test(`旧${name}入口不再加载旧资源，只转到同一实时开发站点`,()=>{
  const source=readFileSync(new URL(`../dist/${name}.html`,import.meta.url),'utf8')
  assert.doesNotMatch(source,/<script[^>]+src=|modulepreload|assets\/main-/)
  const script=source.match(/<script>([\s\S]*?)<\/script>/)[1]
  let destination
  runInNewContext(script,{location:{search:'?view=vision',hash:'#camera',replace:url=>{destination=url}}})
  assert.equal(destination,`http://127.0.0.1:5173/${name==='vision'?'vision.html':''}?view=vision#camera`)
  assert.match(source,/http-equiv="refresh"/)
})
