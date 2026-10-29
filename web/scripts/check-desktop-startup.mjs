import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive, watch, nextTick } from 'vue'
import { createDesktopStartup } from '../src/desktopStartup.js'

const flush = async () => { for(let i=0;i<5;i++){await Promise.resolve();await nextTick()} }
function fixture(t) {
  const listeners = new Map(), messages = [], frames = []
  const target = { addEventListener:(key,fn)=>listeners.set(key,fn), removeEventListener:key=>listeners.delete(key) }
  const world = reactive({connected:false,snap:null})
  let fontReady
  const fonts = {ready:new Promise(resolve=>{fontReady=resolve})}
  const dispose=createDesktopStartup({bridge:{reportBoot:message=>messages.push(message)},world,watch,target,fonts,afterPaint:fn=>frames.push(fn)})
  t.after(dispose)
  return {world,messages,listeners,frames,fontReady,dispose,
    config:detail=>listeners.get('elfin-boot-config')?.({detail}),models:detail=>listeners.get('elfin-boot-models')?.({detail}),
    connect(){world.connected=true;world.snap={session_id:'offline',joints:{q:[0,0,0,0,0,0]},safety:{level:'green'}}},
    ready:()=>messages.filter(m=>m.ready)}
}
test('桌面只在资源、模型、真实首帧、字体及绘制全部完成后显示',async t=>{
  const h=fixture(t)
  h.models({ok:true});h.config({ok:true});await flush();assert.equal(h.ready().length,0)
  h.connect();await flush();assert.equal(h.frames.length,0)
  h.fontReady();await flush();assert.equal(h.ready().length,0)
  h.frames.shift()();assert.equal(h.ready().length,1)
  h.config({ok:true});await flush();assert.equal(h.ready().length,1)
})
test('模型失败保持加载错误，后续状态不能伪装启动完成',async t=>{
  const h=fixture(t)
  h.models({ok:false,why:'模型文件缺失'});h.config({ok:true});h.connect();h.fontReady();await flush()
  assert.equal(h.ready().length,0);assert.ok(h.messages.some(m=>m.error&&m.message==='模型文件缺失'))
})
test('未连接或不完整六轴反馈不能通过启动屏障',async t=>{
  const h=fixture(t);h.config({ok:true});h.models({ok:true});h.fontReady()
  h.world.snap={session_id:'offline',joints:{q:[0,0]},safety:{}};await flush()
  assert.equal(h.ready().length,0);assert.equal(h.frames.length,0)
})
test('卸载清理监听，不接纳延迟绘制回调',async t=>{
  const h=fixture(t);h.config({ok:true});h.models({ok:true});h.connect();h.fontReady();await flush()
  h.dispose();for(const fn of h.frames)fn()
  assert.equal(h.ready().length,0);assert.equal(h.listeners.size,0)
})
