import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {taskProjection,readinessProjection} from '../src/domain/readiness.js'
test('server preflight is occupied without inventing robot motion or clearing stale state',()=>{
 const s={mode:'sim',exec:{state:'idle'},robot_play:{planning:true,progress:{phase:'checking',step:5,total:21}}}
 const p=taskProjection(s,true);assert.equal(p.owner,'取放路径预检');assert.equal(p.phase,'预检中 · 尚未运动');assert.equal(p.count,'已检查 5/21 步')
 assert.equal(readinessProjection(s,true).find(r=>r.id==='owner').state,'blocked')
 s.robot_play.progress.phase='cancelling';assert.equal(taskProjection(s,true).phase,'正在取消预检')
 assert.equal(taskProjection(s,false).owner,'状态未知');s.robot_play.planning=false;assert.equal(taskProjection(s,true).owner,'无活动任务')
})
test('disconnect expires owner, counts and completion immediately',()=>{const s={exec:{state:'running',completed_index:2,total:5}};assert.equal(taskProjection(s,true).count,'已确认 2/5 步');assert.equal(taskProjection(s,false).owner,'状态未知');assert.equal(taskProjection(s,false).count,'')})
test('ACK never implies settled; dynamic script never invents percent',()=>{const s={exec:{state:'waiting',dynamic:true,completed_index:2,total:3,stop_result:{acknowledged:true,settled:false}}};const p=taskProjection(s,true);assert.equal(p.stop,'pending');assert.equal(p.phase,'停止待确认');assert.equal(p.count,'已确认 2 步');assert.doesNotMatch(JSON.stringify(p),/%/)})
test('follow and free ownership use only server snapshot',()=>{assert.equal(taskProjection({hand_follow:{active:true,status:'保持位置'}},true).owner,'交互跟随');assert.equal(taskProjection({free:{on:true}},true).owner,'手拖示教');assert.equal(taskProjection({moving:true},true).owner,'运动中 · 来源未报告')})
test('readiness never grants hardware qualification or stale feedback',()=>{const rows=readinessProjection({mode:'real',real:{cmd_up:true,stream_up:true,quality:'stale',controller_info:{}},tools:{}},true);assert.equal(rows.find(x=>x.id==='feedback').state,'blocked');assert.equal(rows.find(x=>x.id==='qualification').state,'blocked');assert.ok(rows.every(x=>x.next&&x.done&&x.view));assert.equal(readinessProjection({},false).length,1)})
test('readiness escapes app stacking context above camera and below safety masks',()=>{
 const read=p=>readFileSync(new URL('../src/'+p,import.meta.url),'utf8')
 const z=(source,selector)=>Number(source.match(new RegExp(selector+'\\s*\\{[^}]*?z-index:\\s*(\\d+)'))?.[1])
 const source=read('components/StatusStrip.vue'),panel=z(source,'\\.readiness-popover')
 assert.match(source,/<Teleport to="body">\s*<section v-if="detailsOpen" class="readiness-popover"/)
 assert.match(source,/\.readiness-popover\s*\{\s*position:\s*fixed;/)
 assert.ok(panel>z(read('components/CameraWidget.vue'),'\\.camera-pop'))
 assert.ok(panel<z(read('App.vue'),'\\.mask'))
})
