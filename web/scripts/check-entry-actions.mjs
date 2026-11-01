import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
const read=path=>readFileSync(new URL('../src/'+path,import.meta.url),'utf8')
test('only driver has one active connect/disconnect pair; auto-connect has one draft editor',()=>{
 const settings=read('panels/SettingsPanel.vue'),power=read('panels/PowerPanel.vue')
 assert.doesNotMatch(settings,/w\.(?:connectReal|disconnectReal)\(/)
 assert.equal((power.match(/w\.connectReal\(/g)||[]).length,1);assert.equal((power.match(/w\.disconnectReal\(/g)||[]).length,1)
 assert.doesNotMatch(power,/setSettings\(\{\s*auto_connect/)
 assert.match(settings,/patch\(\{ auto_connect:/);assert.match(settings,/w.controlTab='connection';w.view='control'/)
 assert.match(power,/w.systemTab='settings';w.view='engineering'/)
 assert.match(power,/已保存的开机自动连接/)
})
test('model selection describes display reference and point loading never promises completed motion',()=>{
 const model=read('components/ModelLibrary.vue'),point=read('panels/PointsPanel.vue')
 assert.doesNotMatch(model,/已安装末端工具|已安装的末端工具/);assert.match(model,/末端显示模型/);assert.match(model,/实物安装与标定须单独确认/)
 assert.match(point,/@click="goPoint\(p\)">载入预览<\/button>/);assert.match(point,/尚未运动/)
 assert.doesNotMatch(point,/@click="goPoint\(p\)">到点/)
})
for(const file of ['panels/SettingsPanel.vue','panels/PowerPanel.vue','components/ModelLibrary.vue','panels/PointsPanel.vue','panels/FunctionsPanel.vue'])test(file+' final actions compile',()=>{
 const {descriptor}=parse(read(file)),script=compileScript(descriptor,{id:file})
 assert.deepEqual(compileTemplate({source:descriptor.template.content,id:file,compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
})
