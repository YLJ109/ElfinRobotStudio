import test from 'node:test'
import assert from 'node:assert/strict'
import {createTextDraft} from '../src/textDraft.js'

test('同一稳定ID的后台快照和切换物体保留未完成名称',()=>{
  const state={},draft=createTextDraft({state,save:async()=>({ok:true})})
  draft.sync('a','旧名');draft.entry().focused=true;draft.entry().text='新'
  draft.sync('a','旧名');assert.equal(draft.entry().text,'新')
  draft.sync('b','另一个');draft.sync('a','旧名');assert.equal(draft.entry().text,'新')
})
test('失败和冲突保留草稿，不向其他物体写入',async()=>{
  const calls=[],state={},draft=createTextDraft({state,save:async(...args)=>{calls.push(args);return {ok:false,why:'磁盘写入失败'}}})
  draft.sync('a','旧名');draft.entry().text='新名'
  assert.equal(await draft.commit('旧名'),false);assert.equal(draft.entry().text,'新名')
  assert.match(draft.entry().error,/磁盘/)
  assert.equal(await draft.commit('外部改名'),false);assert.equal(calls.length,1)
  draft.cancel('外部改名');assert.equal(draft.entry().text,'外部改名')
})
test('异步保存绑定原ID且禁止重入，切换后不覆盖新物体',async()=>{
  let resolve;const calls=[],state={},draft=createTextDraft({state,save:(...args)=>{calls.push(args);return new Promise(r=>resolve=r)}})
  draft.sync('a','旧');draft.entry().text='新';const pending=draft.commit('旧')
  assert.equal(await draft.commit('旧'),false)
  draft.sync('b','B');resolve({ok:true});assert.equal(await pending,true)
  assert.equal(draft.entry().text,'B');assert.deepEqual(calls,[['a','新']])
})
test('空名保持错误；成功只改名称，稳定ID不变',async()=>{
  const calls=[],state={},draft=createTextDraft({state,save:async(...args)=>{calls.push(args);return {ok:true}}})
  draft.sync('a','旧');draft.entry().text='  ';assert.equal(await draft.commit('旧'),false);assert.equal(calls.length,0)
  draft.entry().text=' 新名 ';assert.equal(await draft.commit('旧'),true)
  assert.equal(state.key,'a');assert.equal(draft.entry().text,'新名');assert.deepEqual(calls,[['a','新名']])
  draft.sync('a','旧');assert.equal(draft.entry().text,'新名');assert.equal(draft.entry().awaiting,'新名')
  draft.sync('a','新名');assert.equal(draft.entry().awaiting,null)
})
