import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import * as THREE from 'three'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { parseSceneAsset } from '../src/sceneAssetVisual.js'
import { disposeAsset } from '../src/toolVisual.js'
import { parseResponse } from '../src/connectionState.js'
import { sceneEditBlock } from '../src/sceneTools.js'

function fixture(external=false) {
  const doc={asset:{version:'2.0'},buffers:[{byteLength:36,...(external?{uri:'https://invalid.example/no.bin'}:{})}],
    bufferViews:[{buffer:0,byteLength:36}],accessors:[{bufferView:0,componentType:5126,count:3,type:'VEC3',min:[0,0,0],max:[1,1,1]}],
    meshes:[{primitives:[{attributes:{POSITION:0},mode:4}]}],nodes:[{mesh:0}],scenes:[{nodes:[0]}],scene:0}
  const encoded=Buffer.from(JSON.stringify(doc)),json=Buffer.concat([encoded,Buffer.alloc((4-encoded.length%4)%4,32)])
  const raw=Buffer.alloc(28+json.length+36);raw.writeUInt32LE(0x46546c67);raw.writeUInt32LE(2,4);raw.writeUInt32LE(raw.length,8)
  raw.writeUInt32LE(json.length,12);raw.writeUInt32LE(0x4e4f534a,16);json.copy(raw,20)
  raw.writeUInt32LE(36,20+json.length);raw.writeUInt32LE(0x004e4942,24+json.length)
  ;[0,0,0,1,0,0,0,1,1].forEach((value,index)=>raw.writeFloatLE(value,28+json.length+index*4))
  return raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)
}

test('actual GLTF loader uses physical XYZ dimensions and releases owned geometry/material',async()=>{
  const root=await parseSceneAsset(fixture(),[100,200,300]);root.updateMatrixWorld(true)
  const box=new THREE.Box3().setFromObject(root),size=box.getSize(new THREE.Vector3()).toArray()
  size.forEach((value,index)=>assert.ok(Math.abs(value-[.1,.3,.2][index])<1e-7))
  assert.ok(box.getCenter(new THREE.Vector3()).length()<1e-7)
  let geometries=0,materials=0
  root.traverse(node=>{node.geometry?.addEventListener('dispose',()=>geometries++);node.material?.addEventListener('dispose',()=>materials++)})
  disposeAsset(root);assert.equal(geometries,1);assert.equal(materials,1)
})
test('loader rejects external dependencies before a network request',async()=>{
  const original=globalThis.fetch;let requests=0
  globalThis.fetch=()=>{requests++;throw new Error('network forbidden')}
  try{await assert.rejects(parseSceneAsset(fixture(true),[100,100,100]));assert.equal(requests,0)}finally{globalThis.fetch=original}
})

const source=readFileSync(new URL('../src/components/SceneAssetImport.vue',import.meta.url),'utf8')
function component(fetch) {
  const hooks=[];const world=Vue.reactive({connected:true,snap:{mode:'sim',moving:false,objects:[],real:{},exec:{state:'idle',execution:{busy:false}},settings:{}}})
  const code=compileScript(parse(source).descriptor,{id:'asset-import'}).content.replace(/^import[^\n]*\n/gm,'').replace('export default','return')
  const deps={...Vue,THREE,OrbitControls:class{},useWorld:()=>world,parseResponse,sceneEditBlock,parseSceneAsset,disposeAsset,
    onBeforeUnmount:fn=>hooks.push(fn),window:{addEventListener(){},removeEventListener(){}},fetch}
  const keys=Object.keys(deps).filter(key=>key!=='default'&&/^[\w$]+$/.test(key));const scope=Vue.effectScope()
  const api=scope.run(()=>new Function(...keys,code)(...keys.map(key=>deps[key])).setup({active:true},{expose(){}}))
  return {api,world,close(){hooks.forEach(fn=>fn());scope.stop()}}
}

for(const [label,value,expected] of [
  ['server rejects invalid GLB',{ok:false,why:'GLB文件头损坏，未导入'},/GLB文件头损坏/],
  ['success missing asset',{ok:true,revision:'r'},/响应不完整/],
  ['success missing size',{ok:true,asset:{},revision:'r'},/响应不完整/],
  ['success invalid size',{ok:true,asset:{size_mm:[100,NaN,100]},revision:'r'},/响应不完整/],
  ['success missing revision',{ok:true,asset:{size_mm:[100,100,100]}},/响应不完整/],
])test(`actual import component: ${label} never enables confirmation or changes scene`,async()=>{
  const calls=[],ctx=component(async path=>{calls.push(path);return {ok:true,status:200,json:async()=>value}})
  try{
    const before=JSON.stringify(ctx.world.snap.objects),api=ctx.api
    await api.choose({target:{files:[{name:'invalid.glb',size:4,arrayBuffer:async()=>new Uint8Array([1,2,3,4]).buffer}],value:'selected'}})
    api.units.value='m';api.source.value='self-generated';api.license.value='CC0';await Vue.nextTick()
    await api.inspect()
    assert.equal(api.failed.value,true);assert.match(api.message.value,expected)
    assert.doesNotMatch(api.message.value,/TypeError|Cannot read/)
    assert.equal(api.inspected.value,null);assert.equal(api.ready.value,false)
    await api.place();assert.deepEqual(calls,['/api/scene-assets/inspect'])
    assert.equal(JSON.stringify(ctx.world.snap.objects),before)
  }finally{ctx.close()}
})

for(const [value,expected] of [[{ok:false,why:'资源校验失败，未添加'},/资源校验失败/],
  [{ok:true},/添加响应不完整/]])test('actual import placement rejection preserves selection and revokes confirmation',async()=>{
  const ctx=component(async()=>({ok:true,status:200,json:async()=>value}))
  try{
    const api=ctx.api
    await api.choose({target:{files:[{name:'own.glb',size:fixture().byteLength,arrayBuffer:async()=>fixture()}],value:'selected'}})
    api.units.value='m';api.source.value='self-generated';api.license.value='CC0';await Vue.nextTick()
    ctx.world.selObj='existing';api.inspected.value={revision:'r'};api.ready.value=true
    await api.place()
    assert.match(api.message.value,expected);assert.doesNotMatch(api.message.value,/TypeError|Cannot read/)
    assert.equal(ctx.world.selObj,'existing');assert.deepEqual(ctx.world.snap.objects,[])
    assert.equal(api.ready.value,false);assert.equal(api.inspected.value,null)
  }finally{ctx.close()}
})
test('selection and readonly inspection never place; cancel rejects a late inspection result',async()=>{
  const calls=[];let finish
  const ctx=component(async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return new Promise(resolve=>{finish=resolve})})
  try {
    const api=ctx.api
    await api.choose({target:{files:[{name:'own.glb',size:fixture().byteLength,arrayBuffer:async()=>fixture()}],value:'selected'}})
    api.units.value='m';api.source.value='self-generated geometry';api.license.value='CC0-1.0';await Vue.nextTick()
    assert.equal(calls.length,0);assert.equal(api.ready.value,false)
    const pending=api.inspect();assert.equal(calls[0].path,'/api/scene-assets/inspect')
    api.cancel();finish({ok:true,status:200,json:async()=>({ok:true,asset:{size_mm:[100,100,100]},revision:'old'})});await pending
    assert.equal(api.inspected.value,null);assert.equal(api.ready.value,false);assert.equal(api.file.value,null)
    await api.place();assert.equal(calls.length,1)
  } finally{ctx.close()}
})
test('resource import component script template and styles compile',()=>{
  const {descriptor}=parse(source),script=compileScript(descriptor,{id:'asset-import'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'asset-import',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'asset-import',scoped:true}).errors,[])
})
