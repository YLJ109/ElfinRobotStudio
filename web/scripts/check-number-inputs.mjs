import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import * as Vue from 'vue'
import { compile } from '@vue/compiler-dom'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import * as SSR from 'vue/server-renderer'
import viteConfig, { numberInputPrecisionTransform } from '../vite.config.js'
import { numberPrecision, displayValue } from '../src/numberDisplay.js'

// Real compiler, v-model runtime and directive lifecycle; host elements are
// isolated event targets, never the operator page or a patched browser DOM.
class TestDocument { activeElement = null }
const previousDocument = Object.getOwnPropertyDescriptor(globalThis, 'Document')
const previousShadow = Object.getOwnPropertyDescriptor(globalThis, 'ShadowRoot')
Object.defineProperty(globalThis, 'Document', { configurable: true, value: TestDocument })
Object.defineProperty(globalThis, 'ShadowRoot', { configurable: true, value: class {} })
test.after(() => {
  for (const [name, descriptor] of [['Document',previousDocument],['ShadowRoot',previousShadow]]) {
    if (descriptor) Object.defineProperty(globalThis,name,descriptor)
    else delete globalThis[name]
  }
})

function element(tag, document) {
  const listeners = new Map()
  let value = ''
  return {
    tag, tagName:tag.toUpperCase(), type:'text', children:[], parentNode:null, ownerDocument:document, props:{}, writes:0,
    get value() { return value }, set value(next) { this.writes++; value=String(next ?? '') },
    getRootNode() { return document },
    addEventListener(name,fn) { if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn) },
    removeEventListener(name,fn) { listeners.get(name)?.delete(fn) },
    fire(name) { for(const fn of listeners.get(name)||[])fn({target:this,currentTarget:this}) },
    listenerCount(name) { return listeners.get(name)?.size || 0 },
    focus() { document.activeElement=this;this.fire('focus') },
    blur() { document.activeElement=null;this.fire('blur') },
  }
}

async function mount(t,template,initial) {
  const document=new TestDocument(),state=Vue.reactive(initial),warnings=[],errors=[]
  const renderer=Vue.createRenderer({
    createElement:tag=>element(tag,document),createText:text=>({...element('#text',document),text}),
    createComment:text=>({...element('#comment',document),text}),
    insert(node,parent,anchor) { if(node.parentNode)this.remove(node);node.parentNode=parent;const at=parent.children.indexOf(anchor);at<0?parent.children.push(node):parent.children.splice(at,0,node) },
    remove(node) { const parent=node.parentNode;if(parent)parent.children.splice(parent.children.indexOf(node),1);node.parentNode=null },
    setText(node,text) { node.text=text },setElementText(node,text) { node.text=text;node.children=[] },
    parentNode:node=>node.parentNode,nextSibling:node=>node.parentNode?.children[node.parentNode.children.indexOf(node)+1]||null,
    patchProp(node,key,_old,next) { node.props[key]=next;if(key==='value'){node.value=next;node._value=next}if(key==='type')node.type=next },
  })
  const {code}=compile(template,{mode:'function',prefixIdentifiers:true,nodeTransforms:[numberInputPrecisionTransform]})
  const render=new Function('Vue',code)(Vue)
  const component={setup:()=>state,render}
  const root=element('root',document),app=renderer.createApp(component)
  app.config.warnHandler=message=>warnings.push(message);app.config.errorHandler=error=>errors.push(error)
  app.directive('two-decimals',numberPrecision);app.config.globalProperties.$displayValue=displayValue
  app.mount(root);await Vue.nextTick()
  let mounted=true
  const unmount=()=>{if(mounted){app.unmount();mounted=false}}
  t.after(()=>{unmount();assert.deepEqual(errors,[]);assert.deepEqual(warnings,[])})
  const inputs=()=>{
    const visit=node=>[...(node.tag==='input'?[node]:[]),...node.children.flatMap(visit)]
    return visit(root)
  }
  return {state,app,inputs,unmount}
}

test('installed Vite Vue compiler injects the directive once and leaves other controls alone',()=>{
  const transform=viteConfig.plugins[0].api.options.template.compilerOptions.nodeTransforms[0]
  assert.equal(transform,numberInputPrecisionTransform)
  const source='<div><input type="number" v-model="n"><input type="number" v-two-decimals><input type="text"><input type="range"><input :type="kind"><canvas /></div>'
  const {code,ast}=compile(source,{mode:'function',nodeTransforms:[transform]})
  const inputs=ast.children[0].children.filter(node=>node.tag==='input')
  assert.deepEqual(inputs.map(node=>node.props.filter(p=>p.name==='two-decimals').length),[1,1,0,0,0])
  assert.match(code,/resolveDirective\("two-decimals"\)/)
})

test('programmatic v-model numbers display two decimals while raw data and submissions retain precision',async t=>{
  const fixture=await mount(t,'<input type="number" v-model="n">',{n:406.2203392985117})
  const input=fixture.inputs()[0]
  assert.equal(input.value,'406.22');assert.equal(fixture.state.n,406.2203392985117)
  fixture.state.n=-3.9458535389364035e-15;await Vue.nextTick()
  assert.equal(input.value,'0');assert.equal(fixture.state.n,-3.9458535389364035e-15)
  fixture.state.n=123.456789123;await Vue.nextTick()
  assert.equal(input.value,'123.46');assert.equal(JSON.stringify({n:fixture.state.n}),'{"n":123.456789123}')
})

test('active editing is not reformatted; blur formats without assigning to v-model',async t=>{
  const fixture=await mount(t,'<input type="number" v-model="n">',{n:12.345678})
  const input=fixture.inputs()[0];input.focus();input.value='3.14159265'
  const writes=input.writes;input.fire('input');await Vue.nextTick()
  assert.equal(input.value,'3.14159265');assert.equal(input.writes,writes)
  assert.equal(fixture.state.n,3.14159265)
  input.blur();assert.equal(input.value,'3.14');assert.equal(fixture.state.n,3.14159265)
})

test('empty and composing inputs are preserved and listeners are removed on unmount',async t=>{
  const fixture=await mount(t,'<input type="number" v-model="n">',{n:null})
  const input=fixture.inputs()[0];assert.equal(input.value,'');assert.equal(fixture.state.n,null)
  input.composing=true;input.value='1.2345';numberPrecision.updated(input)
  assert.equal(input.value,'1.2345');input.composing=false
  assert.equal(input.listenerCount('blur'),1);fixture.unmount();assert.equal(input.listenerCount('blur'),0)
})

test('new v-for rows and readonly value bindings are covered without altering model arrays',async t=>{
  const fixture=await mount(t,'<div><input v-for="(n,i) in values" :key="i" type="number" v-model="values[i]"><input type="number" readonly :value="reading"></div>',
    {values:[1.23456],reading:98.7654321})
  assert.deepEqual(fixture.inputs().map(el=>el.value),['1.23','98.77'])
  fixture.state.values.push(22.222222);await Vue.nextTick()
  assert.deepEqual(fixture.inputs().map(el=>el.value),['1.23','22.22','98.77'])
  assert.deepEqual([...fixture.state.values],[1.23456,22.222222]);assert.equal(fixture.state.reading,98.7654321)
})

test('all current SFC templates compile with the production transform and cover static numeric inputs',()=>{
  const sourceRoot=fileURLToPath(new URL('../src/',import.meta.url))
  const walk=directory=>readdirSync(directory,{withFileTypes:true}).flatMap(entry=>entry.isDirectory()?walk(directory+'/'+entry.name):entry.name.endsWith('.vue')?[directory+'/'+entry.name]:[])
  let controls=0,components=0
  for(const filename of walk(sourceRoot)){
    const {descriptor,errors}=parse(readFileSync(filename,'utf8'),{filename});assert.deepEqual(errors,[])
    if(!descriptor.template)continue
    const result=compileTemplate({source:descriptor.template.content,filename,id:'number-input-coverage',compilerOptions:{nodeTransforms:[(node,context)=>{
      const length=node.props?.length,exit=numberInputPrecisionTransform(node,context)
      if(node.props?.length>length)controls++
      return exit
    }]}})
    assert.deepEqual(result.errors,[],filename);components++
  }
  assert.ok(components>40);assert.ok(controls>=50,'numeric controls: '+controls)
})

function compiledRender(source,{isProd=false,ssr=false}={}) {
  const result=compileTemplate({source,filename:'NumericPresentation.vue',id:'numeric-presentation',isProd,ssr,ssrCssVars:[],
    compilerOptions:{nodeTransforms:[numberInputPrecisionTransform]}})
  assert.deepEqual(result.errors,[])
  const code=result.code.replace(/^import (.+?) from ["']([^"']+)["'];?\s*$/gm,(_match,bindings,name)=>
    'const '+bindings.replace(/\bas\b/g,':')+' = modules['+JSON.stringify(name)+'];')
    .replace(/export function (?:ssrRender|render)\(/,'return function(')
  return {code:result.code,render:new Function('modules',code)({vue:Vue,'vue/server-renderer':SSR})}
}

test('dev, production and SSR text/tooltip projection preserves raw code, geometry and source values',async()=>{
  const source='<main :title="n" :aria-label="label" :data-q="n"><p>{{n}}</p><span>{{label}}</span><div>{{values}}</div><pre><code><span>{{program}}</span></code></pre><textarea>{{program}}</textarea><output>{{n}}</output><svg><path :d="path" /></svg></main>'
  const data={n:406.2203392985117,label:'误差0.370189603mm',values:[1.234567,{x:2.345678}],
    program:'MOVEJ 1.23456789,2,3,4,5,6',path:'M 1.23456789 2.3456789'}
  const before=structuredClone(data)
  for(const isProd of [false,true]){
    const dom=compiledRender(source,{isProd})
    assert.match(dom.code,/_ctx\.\$displayValue/)
    // The corresponding SSR render uses the same compiler transform and real
    // Vue global-property resolution, including HTML escaping.
    const {render}=compiledRender(source,{isProd,ssr:true})
    const app=Vue.createSSRApp({data:()=>structuredClone(data),ssrRender:render})
    app.config.globalProperties.$displayValue=displayValue
    app.directive('two-decimals',numberPrecision)
    const html=await SSR.renderToString(app)
    assert.match(html,/title="406\.22"/);assert.match(html,/aria-label="误差0\.37mm"/)
    assert.match(html,/data-q="406\.2203392985117"/);assert.match(html,/<p>406\.22<\/p>/)
    assert.match(html,/<span>误差0\.37mm<\/span>/)
    assert.ok(html.includes('1.23'));assert.ok(html.includes('2.35'))
    assert.match(html,/<pre><code><span>MOVEJ 1\.23456789,2,3,4,5,6<\/span><\/code><\/pre>/)
    assert.match(html,/<textarea>MOVEJ 1\.23456789,2,3,4,5,6<\/textarea>/)
    assert.match(html,/<output>406\.22<\/output>/)
    assert.match(html,/d="M 1\.23456789 2\.3456789"/)
  }
  assert.deepEqual(data,before)
})

test('display projections preserve Vue special values and do not touch payloads',()=>{
  const data={value:1.234567,array:[2.345678],map:new Map([['x',3.456789]]),set:new Set([4.567891])}
  assert.equal(displayValue(null),null);assert.equal(displayValue(undefined),undefined)
  assert.equal(displayValue(NaN),'—');assert.equal(displayValue(123456789123456789n),'123456789123456789')
  assert.match(displayValue(data),/1\.23/);assert.match(displayValue(data.map),/3\.46/);assert.match(displayValue(data.set),/4\.57/)
  assert.equal(data.value,1.234567);assert.equal(data.array[0],2.345678)
})
