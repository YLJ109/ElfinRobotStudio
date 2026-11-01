// Pure component verification: no requests, live stream, or robot commands.
import {readFileSync} from 'node:fs'
import {test} from 'node:test'
import assert from 'node:assert/strict'
import {parse,compileScript} from '@vue/compiler-sfc'
import {computed,ref,reactive,compile,proxyRefs} from 'vue'
import {taskProjection,readinessProjection} from '../src/domain/readiness.js'

const read = path => readFileSync(new URL('../src/'+path,import.meta.url),'utf8')
const script = compileScript(parse(read('components/StatusStrip.vue')).descriptor,{id:'safety-test'}).content
  .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm,'').replace('export default','return')
function setup(level, configured=false) {
  const world=reactive({connected:true,lastFrameAt:Date.now(),view:'hand-follow',snap:{mode:'sim',real:null,
    safety:{level,reasons:level==='green'?[]:['J1 limit']},
    hand_follow:{configured,status:'跟随中'}}})
  // Supply refs just as Pinia does; reactive values alone would be unwrapped.
  const fields={snap:computed(()=>world.snap),view:computed(()=>world.view)}
  const actual=new Function('computed','ref','storeToRefs','useWorld','onMounted','onBeforeUnmount','taskProjection','readinessProjection',script)(
    computed,ref,()=>fields,()=>world,()=>{},()=>{},taskProjection,readinessProjection)
  const props=reactive({interactionVisible:true})
  return {panel:actual.setup(props, {expose(){}}),world,props}
}
test('chess under the fun entry does not inherit the follow-only pending status',()=>{
  const {panel,world,props}=setup('green')
  props.interactionVisible=false
  world.snap.hand_follow={configured:false,status:'保持位置',why:'旧跟随提示'}
  assert.equal(panel.handScene.value,false)
  assert.equal(panel.level.value,'green')
  assert.equal(panel.reason.value,'')
})
for (const severity of ['yellow','red','alarm']) test('follow page keeps '+severity+' safety cause visible',()=>{
  const {panel}=setup(severity)
  assert.equal(panel.level.value,severity)
  assert.equal(panel.reason.value,'J1 limit')
})
test('unreachable target is yellow only when combined safety is green',()=>{
  const {panel,world}=setup('green',true)
  world.snap.hand_follow.status='保持位置';world.snap.hand_follow.why='目标暂不可达'
  assert.equal(panel.level.value,'yellow');assert.equal(panel.reason.value,'目标暂不可达')
  world.snap.safety={level:'red',reasons:['floor']}
  assert.equal(panel.level.value,'red');assert.equal(panel.reason.value,'floor')
})
test('walls and floor get their own cause, not the combined joint level',()=>{
  assert.match(read('App.vue'),/scene\.setFenceSafety\(s\.safety\?\.fence_level \|\| 'green'\)/)
  assert.match(read('App.vue'),/scene\.setGroundSafety\(s\.safety\?\.ground_level \|\| 'green'\)/)
  assert.doesNotMatch(read('App.vue'),/scene\.setFenceSafety\(s\.safety\?\.level\)/)
})

test('real status never renders simulation pending/holding text and keeps controller safety visible',()=>{
  const render=compile(parse(read('components/StatusStrip.vue')).descriptor.template.content)
  const text=value=>value==null ? '' : Array.isArray(value) ? value.map(text).join('') :
    typeof value==='object' ? text(value.children) : String(value)
  const {panel,world}=setup('green')
  const context=proxyRefs(panel),display=()=>text(render.call(context,context,[]))
  assert.match(display(),/待开始/)
  world.snap.mode='real';world.snap.real={stream_up:true,cmd_up:true}
  world.snap.real_hand_follow={configured:true,active:true,status:'真机点动'}
  for(const configured of [false,true]) {
    world.snap.hand_follow={configured,status:'保持位置',why:'仿真独有等待原因'}
    assert.equal(panel.handScene.value,false)
    assert.equal(panel.level.value,'green');assert.equal(panel.reason.value,'')
    assert.doesNotMatch(display(),/待开始|仿真独有等待原因/)
  }
  world.snap.safety={level:'alarm',reasons:['真机急停']}
  assert.equal(panel.level.value,'alarm');assert.equal(panel.reason.value,'真机急停')
  assert.match(display(),/真机急停/)
})


test('disconnected and stale snapshots never display normal or a ready primary action',()=>{
 const {panel,world}=setup('green',true)
 assert.equal(panel.level.value,'green')
 world.connected=false
 assert.equal(panel.level.value,'unknown')
 assert.match(panel.reason.value,/最后快照/)
 assert.equal(panel.primary.value.label,'等待有效状态')
 world.connected=true;world.lastFrameAt=Date.now()-5000
 assert.equal(panel.level.value,'unknown')
 assert.match(panel.reason.value,/最后快照/)
 world.lastFrameAt=0
 assert.equal(panel.level.value,'unknown')
 world.lastFrameAt=Date.now()
 assert.equal(panel.level.value,'green')
})

test('cross-page task details project confirmed steps and navigation without actions',()=>{
 const {panel,world}=setup('green',true)
 world.view='objects';world.snap.exec={state:'waiting',completed_index:2,total:8,current:{line:12},stop_result:{acknowledged:true,settled:false}}
 assert.equal(panel.taskStatus.value.count,'已确认 2/8 步')
 assert.equal(panel.taskStatus.value.phase,'停止待确认')
 assert.ok(panel.readiness.value.every(row=>row.next&&row.done))
 panel.detailsOpen.value=true
 panel.locate({view:'engineering',systemTab:'tele'})
 assert.equal(world.view,'engineering');assert.equal(world.systemTab,'tele');assert.equal(panel.detailsOpen.value,false)
 world.connected=false
 assert.equal(panel.taskStatus.value.owner,'状态未知');assert.equal(panel.taskStatus.value.count,'')
})
