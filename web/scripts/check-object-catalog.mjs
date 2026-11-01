// Local original catalogue, real component setup/compilation; no network/assets.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as catalog from '../src/objectCatalog.js'
import { alignedPosition, gridArrayPreview } from '../src/sceneTools.js'

const read=path=>readFileSync(new URL('../src/'+path,import.meta.url),'utf8')
test('thumbnail cache encodes each known preset once across repeated snapshot renders',()=>{
  let encodes=0
  const isolated=new Function('encodeURIComponent',read('objectCatalog.js').replace(/^export /gm,'')+'\nreturn {OBJECT_CATALOG,catalogThumbnail,thumbnailCache}')(
    value=>{encodes++;return encodeURIComponent(value)})
  const expected=isolated.OBJECT_CATALOG.map(item=>isolated.catalogThumbnail(item.id))
  for(let frame=0;frame<100;frame++){
    isolated.OBJECT_CATALOG.forEach((item,index)=>assert.equal(isolated.catalogThumbnail(item.id),expected[index]))
    assert.equal(isolated.catalogThumbnail(`unknown-${frame}`),'')
  }
  assert.equal(encodes,19)
  assert.equal(isolated.thumbnailCache.size,19)
})
test('19 searchable types, 13 distinct assemblies plus solid tokens, original safe inline thumbnails',()=>{
  assert.equal(catalog.OBJECT_CATALOG.length,19)
  assert.equal(new Set(catalog.OBJECT_CATALOG.map(item=>item.id)).size,19)
  assert.equal(catalog.filterObjectCatalog('滚筒')[0].id,'conveyor')
  assert.equal(catalog.filterObjectCatalog('PALLET')[0].id,'pallet')
  assert.equal(catalog.filterObjectCatalog('料','storage').length,2)
  assert.equal(catalog.filterObjectCatalog('not-a-type').length,0)
  assert.equal(new Set(catalog.OBJECT_CATALOG.filter(item=>item.parts).map(item=>JSON.stringify(item.parts))).size,13)
  const thumbnails=new Set()
  for(const item of catalog.OBJECT_CATALOG){
    const uri=catalog.catalogThumbnail(item.id),svg=decodeURIComponent(uri.split(',')[1])
    assert.match(uri,/^data:image\/svg\+xml/);assert.match(svg,/<svg /)
    assert.doesNotMatch(svg,/<script|<image|<foreignObject|\bon\w+=|href=/i)
    thumbnails.add(uri)
    const spec=catalog.catalogSpec(item.id,[1,2,3])
    assert.equal(spec.preset,item.id);assert.equal(spec.kind,item.kind)
    assert.ok(Object.hasOwn(catalog.OBJECT_SURFACES,spec.surface))
    assert.match(spec.color,/^#[0-9a-f]{6}$/i)
    assert.equal(alignedPosition({id:'p',...spec},'floor')[2],item.kind==='sphere'?item.size[0]:item.size[2]/2)
    assert.equal(gridArrayPreview({id:'p',...spec},[2,1,1],[1200,0,0],1).added,1)
  }
  assert.equal(thumbnails.size,19)
  for(const appearance of [{surface:'script'},{color:'url(x)'},{color:'#ff0000;'}])assert.throws(()=>catalog.catalogSpec('box',[0,0,0],appearance))
  assert.throws(()=>catalog.catalogSpec('unknown',[0,0,0]))
  assert.throws(()=>catalog.catalogSpec('box',[Infinity,0,0]))
})
test('catalogue field values exist in backend whitelist and legacy labels still resolve',()=>{
  const schema=readFileSync(new URL('../../backend/schemas.py',import.meta.url),'utf8')
  for(const item of catalog.OBJECT_CATALOG)assert.ok(schema.includes(`'${item.id}'`),item.id)
  for(const surface of Object.keys(catalog.OBJECT_SURFACES))assert.ok(schema.includes(`'${surface}'`))
  assert.equal(catalog.objectLabel({kind:'box'}),'长方体')
  assert.equal(catalog.objectLabel({kind:'box',preset:'rack'}),'三层货架')
})
test('catalogue placement previews locally and commits one validated preset through shared scene history',async()=>{
  const source=read('panels/ObjectsPanel.vue'),descriptor=parse(source).descriptor
  const code=compileScript(descriptor,{id:'catalog'}).content.replace(/^import[^\n]*\n/gm,'').replace('export default','return')
  let complete
  const requests=[];let placement
  const previousWindow=globalThis.window;globalThis.window={__elfinScene:{beginPlacement(spec,callbacks){placement={spec,callbacks};return true},cancelPlacement(){placement?.callbacks.cancel?.();return true}}}
  const w=Vue.reactive({connected:true,stateFresh:true,view:'objects',snap:{session_id:'s',mode:'sim',objects:[],obj_hits:[]},selObj:null,scenes:[],activeScene:'',
    placeObject:async spec=>{requests.push(spec);return new Promise(resolve=>{complete=resolve})}})
  const deps={...Vue,...catalog,useWorld:()=>w,storeToRefs:Vue.toRefs,TaskTabs:{},SceneTools:{},SceneAssetImport:{}}
  const keys=Object.keys(deps).filter(key=>key!=='default'&&/^[\w$]+$/.test(key)),scope=Vue.effectScope()
  try{
    const api=scope.run(()=>new Function(...keys,code)(...keys.map(key=>deps[key])).setup({}, {expose(){}}))
    api.selectedPreset.value='crate';await Vue.nextTick()
    api.catalogSearch.value='crate';assert.equal(api.resources.value.length,1)
    assert.equal(requests.length,0)
    api.chosenColor.value='#abcdef';api.chosenSurface.value='steel'
    api.add();assert.equal(requests.length,0);assert.equal(api.placing.value,true)
    const pending=placement.callbacks.commit({...placement.spec,xyz:[125,-250,300]});api.add()
    assert.equal(requests.length,1);assert.deepEqual(requests[0].xyz,[125,-250,300])
    assert.equal(requests[0].preset,'crate');assert.equal(requests[0].surface,'steel');assert.equal(requests[0].color,'#abcdef')
    complete({ok:true,object:{id:'new-crate'}});await pending
    assert.equal(w.selObj,'new-crate');assert.equal(api.adding.value,false)
  }finally{scope.stop();globalThis.window=previousWindow}
})
for(const path of ['panels/ObjectsPanel.vue','components/ObjectProps.vue','components/NavRail.vue'])test(`${path} compiles with catalogue and meaningful movement controls`,()=>{
  const {descriptor,errors}=parse(read(path));assert.deepEqual(errors,[])
  const script=compileScript(descriptor,{id:path})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:path,id:path,compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:path,id:path,scoped:style.scoped}).errors,[])
})
