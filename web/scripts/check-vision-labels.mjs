import test from 'node:test'
import assert from 'node:assert/strict'
import {layoutVisionLabels} from '../src/visionLabelLayout.js'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse,compileScript,compileStyle} from '@vue/compiler-sfc'
import {COLOR_NAMES,SHAPE_NAMES} from '../src/visionWorkspace.js'
import postcss from 'postcss'

test('compiled overlay owns non-occluding contour and center styles inside its child scope',async()=>{
 const {descriptor}=parse(readFileSync(new URL('../src/components/VisionObjectOverlay.vue',import.meta.url),'utf8'))
 const imports={vue:Vue,'../visionLabelLayout.js':{layoutVisionLabels},'../visionWorkspace.js':{COLOR_NAMES,SHAPE_NAMES}}
 const code=compileScript(descriptor,{id:'overlay',inlineTemplate:true}).content.replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,path)=>`const ${bindings.replace(/\bas\b/g,':')}=imports[${JSON.stringify(path)}];`).replace('export default','return')
 const component=new Function('imports',code)(imports);component.__scopeId='data-v-overlay'
 const objects=[{id:'V1',color:'red',shape:'rectangle',status:'observed',contour:[[10,10],[30,10],[30,30],[10,30]],center_px:[20,20]},{id:'V2',color:'blue',shape:'circle',status:'holding',contour:[[40,10],[60,10],[60,30],[40,30]],center_px:[50,20]}]
 const html=await renderToString(Vue.createSSRApp({render:()=>Vue.h('svg',{'data-v-parent':''},[Vue.h(component,{objects,width:100,height:80})])}))
 const shapes=[...html.matchAll(/<(polygon|circle)\b([^>]*)>/g)]
 assert.equal(shapes.length,4)
 const css=compileStyle({source:descriptor.styles.map(style=>style.content).join('\n'),id:'data-v-overlay',scoped:true});assert.deepEqual(css.errors,[])
 const rules=[];postcss.parse(css.code).walkRules(rule=>{const declarations={};rule.walkDecls(decl=>{declarations[decl.prop]=decl.value});rules.push({selector:rule.selector,declarations})})
 for(const [,tag,attributes]of shapes){
   assert.match(attributes,/data-v-overlay/);assert.doesNotMatch(attributes,/data-v-parent/)
   const className=/class="([^"]+)"/.exec(attributes)?.[1]
   const own=rules.find(rule=>rule.selector.split(',').some(selector=>selector.trim()===`.${className}[data-v-overlay]`))
   assert.ok(own,`${tag} must own its style rather than rely on parent scoped CSS`)
   assert.equal(own.declarations.fill,'none');assert.ok(own.declarations.stroke);assert.equal(own.declarations['stroke-width'],'2');assert.equal(own.declarations['vector-effect'],'non-scaling-stroke')
 }
 assert.ok(rules.some(rule=>rule.selector.includes('.held[data-v-overlay]')&&rule.declarations.opacity==='.4'))
 assert.ok(rules.some(rule=>rule.selector.includes('.vision-label')&&rule.declarations.fill==='rgba(5,12,18,.85)'),'label backdrop remains intentional and separate from target fill')
})
test('adjacent detection labels use non-overlapping boxes and bounded leaders',()=>{
 const objects=[{id:'V1',center_px:[267,268]},{id:'V2',center_px:[320,268]},{id:'V3',center_px:[355,317]},{id:'edge',center_px:[639,0]}]
 const labels=layoutVisionLabels(objects,640,480,o=>o.id+' · 蓝色 矩形')
 for(const [i,a] of labels.entries()){
  assert.ok(a.x>=0 && a.y>=0 && a.x+a.width<=640 && a.y+a.height<=480)
  assert.ok(a.lineX>=a.x && a.lineX<=a.x+a.width && a.lineY>=a.y && a.lineY<=a.y+a.height)
  for(const b of labels.slice(i+1))assert.ok(a.x+a.width<=b.x || b.x+b.width<=a.x || a.y+a.height<=b.y || b.y+b.height<=a.y)
 }
 assert.deepEqual(layoutVisionLabels(objects,640,480,o=>o.id+' · 蓝色 矩形'),labels)
})
