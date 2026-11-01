import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {reactive,toRefs,computed} from 'vue'

test('rail exposes nine flat first-level entries and opens the merged hubs without task APIs',()=>{
 const source=readFileSync(new URL('../src/components/NavRail.vue',import.meta.url),'utf8')
 const descriptor=parse(source).descriptor
 const world=reactive({view:'interaction',workTab:'program',displayMode:'pro'}),events=[]
 const script=compileScript(descriptor,{id:'function-navigation'}).content.replace(/^import[^\n]*\n/gm,'').replace('export default','return')
 const component=new Function('computed','storeToRefs','useWorld',script)(computed,toRefs,()=>world)
 const api=component.setup({expanded:true},{expose(){},emit:(...args)=>events.push(args)})
 const groups=api.GROUPS.map(group=>group.label)
 assert.deepEqual(groups,[''])
 const items=api.GROUPS.flatMap(group=>group.items)
 assert.deepEqual(items.map(item=>item[2]),['控制','点位','程序','AI','视觉','娱乐','互动','场景','系统'])
 assert.equal(items.find(item=>item[2]==='控制')[0],'control')
 assert.equal(items.find(item=>item[2]==='点位')[1],'points')
 assert.equal(items.find(item=>item[2]==='程序')[1],'program')
 assert.equal(items.find(item=>item[2]==='AI')[1],'ai')
 assert.equal(items.find(item=>item[2]==='视觉')[0],'vision')
 assert.equal(items.find(item=>item[2]==='娱乐')[0],'games')
 assert.equal(items.find(item=>item[2]==='互动')[0],'interaction')
 assert.equal(items.find(item=>item[2]==='场景')[0],'scene')
 assert.equal(items.find(item=>item[2]==='系统')[0],'engineering')
 assert.ok(!items.some(item=>['跟随','追踪','绘制','写字','围栏','分拣'].includes(item[2]),
   'merged tools must not remain separate rail entries'))
 api.openItem(...items.find(item=>item[2]==='点位'));assert.equal(world.view,'work');assert.equal(world.workTab,'points')
 api.openItem(...items.find(item=>item[2]==='互动'));assert.equal(world.view,'interaction')
 world.view='game-chess'
 api.openItem('games');assert.equal(world.view,'game-chess')
 assert.equal(api.isActive('games'),true)
 world.view='control'
 api.openItem('games');assert.equal(world.view,'game-ttt')
 assert.equal(api.isActive('games'),true)
 assert.equal(api.isActive('interaction'),false)
 assert.equal(events.length,4)
 world.displayMode='simple'
 assert.deepEqual(api.groups.value.map(group=>group.label),[''])
 assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'function-navigation',
   compilerOptions:{bindingMetadata:compileScript(descriptor,{id:'function-navigation'}).bindings}}).errors,[])
 const app=readFileSync(new URL('../src/App.vue',import.meta.url),'utf8')
 assert.match(app,/interaction: InteractionHub/)
 assert.match(app,/scene: SceneHub/)
 assert.match(app,/vision: VisionPanel/)
 assert.match(app,/'game-ttt': GamesHub/)
 assert.match(app,/game: v\.slice\(5\)/)
 assert.doesNotMatch(app,/FunctionsPanel/)
 assert.doesNotMatch(app,/DrawWritePanel/)
 assert.doesNotMatch(app,/FencePanel/)
})
