import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {toolActions as actionDefinitions} from '../src/shared/toolActions.ts'
import {HEAD_CAMERA_MOUNT} from '../src/virtualCameraMount.js'
import {COLOR_NAMES,SHAPE_NAMES} from '../src/visionWorkspace.js'
const read=name=>readFileSync(new URL('../src/panels/'+name,import.meta.url),'utf8')
function fixture(name,extra={}){
 const world=Vue.reactive({connected:true,snap:{mode:'sim',exec:{state:'running'},moving:true},tools:{tool:null,items:[]},draft:'未保存草稿'})
 const memory=new Map([['huayan.modelQuality','full']]),calls=[],hooks=[]
 const deps={...Vue,useWorld:()=>world,storeToRefs:Vue.toRefs,actionDefinitions,HEAD_CAMERA_MOUNT,COLOR_NAMES,SHAPE_NAMES,
   onMounted:()=>{},onBeforeUnmount:fn=>hooks.push(fn),window:{},location:{pathname:'/',reload(){throw Error('不得刷新丢失现场')}},
   localStorage:{getItem:key=>memory.get(key),setItem:(key,value)=>memory.set(key,value)},v:Vue.reactive({config:{enabled:true,detection_enabled:true,mapping:true}}),
   visionCall:async(...args)=>{calls.push(args);return {ok:true}},readMarkerFile:()=>{throw Error('不得读文件')},...extra}
 const code=parse(read(name)).descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
 const exports=name==='EngineeringPanel.vue'?'quality,savedQuality,changeQuality,cancelQuality,samples,target,localTarget,tabs,failed,message': 'tabs,tab,pauseDetection,busy'
 const keys=Object.keys(deps).filter(key=>key!=='default'&&/^[\w$]+$/.test(key)),scope=Vue.effectScope()
 const api=scope.run(()=>new Function(...keys,code+'\nreturn {'+exports+'}')( ...keys.map(key=>deps[key])))
 return {api,world,memory,calls,close(){hooks.forEach(fn=>fn());scope.stop()}}
}
test('protocol catalogue is explicitly reference and cannot claim every listed command implemented',()=>{
 const source=read('AboutPanel.vue')
 assert.doesNotMatch(source,/已接入指令/);assert.match(source,/协议参考目录/);assert.match(source,/不代表当前支持/)
 assert.match(source,/软件接入.*离线验证.*当前资格/)
})
test('quality preference preserves unsaved drafts and running task without refreshing or sending control',()=>{
 const ctx=fixture('EngineeringPanel.vue')
 try{ctx.api.samples.value='尚未保存的采样';ctx.api.localTarget.value='1,2,3,4,5,6';ctx.api.quality.value='low';ctx.api.changeQuality()
 assert.equal(ctx.memory.get('huayan.modelQuality'),'low');assert.equal(ctx.api.savedQuality.value,'low')
 assert.equal(ctx.api.samples.value,'尚未保存的采样');assert.equal(ctx.api.localTarget.value,'1,2,3,4,5,6');assert.equal(ctx.world.snap.exec.state,'running');assert.equal(ctx.world.draft,'未保存草稿');assert.deepEqual(ctx.calls,[])
 assert.ok(ctx.api.tabs.some(tab=>tab.id==='display'))
 }finally{ctx.close()}
})
test('cancelling quality draft keeps persisted value; invalid selection never writes or reloads',()=>{
 const ctx=fixture('EngineeringPanel.vue')
 try{ctx.api.quality.value='low';ctx.api.cancelQuality();assert.equal(ctx.api.quality.value,'full');assert.equal(ctx.memory.get('huayan.modelQuality'),'full')
 ctx.api.quality.value='invalid';ctx.api.changeQuality();assert.equal(ctx.memory.get('huayan.modelQuality'),'full')
 }finally{ctx.close()}
})
test('storage failure never reports saved quality or affects the current task',()=>{
 const ctx=fixture('EngineeringPanel.vue',{localStorage:{getItem:()=>null,setItem(){throw Error('offline storage')}}})
 try{ctx.api.quality.value='low';ctx.api.changeQuality();assert.equal(ctx.api.savedQuality.value,'full');assert.equal(ctx.api.failed.value,true);assert.match(ctx.api.message.value,/未保存/);assert.equal(ctx.world.snap.exec.state,'running')
 }finally{ctx.close()}
})
test('vision consolidates actual pause control into recognition and removes empty recipe navigation',async()=>{
 const ctx=fixture('VisionPanel.vue')
 try{assert.deepEqual(ctx.api.tabs.map(tab=>tab.id),['camera','objects','find','count','position','calibration']);await ctx.api.pauseDetection();assert.equal(ctx.calls.length,1);assert.deepEqual(ctx.calls[0].slice(0,2),['config',{detection_enabled:false,mapping:false}])
 ctx.api.busy.value=true;await ctx.api.pauseDetection();assert.equal(ctx.calls.length,1)
 const source=read('VisionPanel.vue');assert.doesNotMatch(source,/tab==='recipe'|多方案库将在后续增加/)
 assert.match(source,/tab==='objects'[\s\S]*暂停识别与映射/)
 }finally{ctx.close()}
})
for(const name of ['AboutPanel.vue','EngineeringPanel.vue','VisionPanel.vue'])test(name+' real template compiles after entry consolidation',()=>{
 const {descriptor}=parse(read(name)),script=compileScript(descriptor,{id:name})
 assert.deepEqual(compileTemplate({source:descriptor.template.content,id:name,compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
})
