// The retired diagnostic/diff workspace has no runtime entry or hidden state.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync,existsSync} from 'node:fs'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
const source=readFileSync(new URL('../src/panels/AiPanel.vue',import.meta.url),'utf8'),descriptor=parse(source).descriptor
 test('AI workspace directly renders its single assistant without retired tabs or duplicate controls',()=>{
 const script=compileScript(descriptor,{id:'ai-workspace'})
 assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'ai-workspace',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
 for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'ai-workspace',scoped:true}).errors,[])
 assert.match(source,/<LegacyAiAssistant class="assistant-body"\s*\/>/)
 assert.doesNotMatch(source,/状态诊断|草稿差异|workspace-tabs|tablist|<button|<textarea|v-show|v-if/)
})
test('retired AI diagnostics have no data, storage, network or mutation entry',()=>{
 assert.equal(existsSync(new URL('../src/aiReadOnly.js',import.meta.url)),false)
 assert.doesNotMatch(source,/useWorld|aiReadOnly|programSrc|sessionStorage|localStorage|fetch\(|\/api\/|aiRun|aiSay|call\(/)
})