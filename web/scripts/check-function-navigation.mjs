import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {reactive,toRefs} from 'vue'

test('rail exposes distinct fun/sorting entries and opens the matching route without task APIs',()=>{
 const source=readFileSync(new URL('../src/components/NavRail.vue',import.meta.url),'utf8')
 const descriptor=parse(source).descriptor
 const world=reactive({view:'hand-follow',workTab:'program'}),events=[]
 const script=compileScript(descriptor,{id:'function-navigation'}).content.replace(/^import[^\n]*\n/gm,'').replace('export default','return')
 const component=new Function('storeToRefs','useWorld',script)(toRefs,()=>world)
 const api=component.setup({expanded:true},{expose(){},emit:(...args)=>events.push(args)})
 const fun=api.ITEMS.find(item=>item[2]==='有趣'),sorting=api.ITEMS.find(item=>item[2]==='分拣')
 assert.equal(api.ITEMS.filter(item=>item[2]==='控制').length,1)
 assert.ok(!api.ITEMS.some(item=>['总览','驱动'].includes(item[2])),'overview and drive must not remain separate visible entries')
 assert.equal(fun[0],'hand-follow');assert.equal(sorting[0],'sorting')
 assert.notEqual(fun[3],sorting[3],'fun and sorting must have distinct actual SVG paths')
 assert.doesNotMatch(fun[3],/M3 3 H7 V7 H3 Z/,'obsolete four-grid function icon must be removed')
 assert.match(fun[3],/[Cc]/,'gamepad outline uses curved grips')
 assert.match(fun[3],/V10\.5 M3\.5 9 H6\.5/,'gamepad includes a directional pad')
 assert.ok(!api.ITEMS.some(item=>item[2]==='功能'),'old function entry must not remain visible')
 api.openItem(...sorting);assert.equal(world.view,'sorting');assert.equal(world.workTab,'program')
 assert.equal(api.isActive(...sorting),true);assert.equal(api.isActive(...fun),false)
 api.openItem(...fun);assert.equal(world.view,'hand-follow');assert.equal(events.length,2)
 assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'function-navigation',compilerOptions:{bindingMetadata:compileScript(descriptor,{id:'function-navigation'}).bindings}}).errors,[])
 const app=readFileSync(new URL('../src/App.vue',import.meta.url),'utf8')
 assert.match(app,/'hand-follow': FunctionsPanel, sorting: FunctionsPanel/)
 assert.match(app,/category:view==='sorting'\?'sorting':'fun'/)
 assert.doesNotMatch(app,/<component[^>]+:key="view"/,'same task instance must be reused across categories')
})
