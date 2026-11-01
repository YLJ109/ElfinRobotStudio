import * as numberDisplay from '../src/numberDisplay.js'
import * as interactionRanges from '../src/interactionRange.js'
import test,{after} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {renderToString} from '@vue/server-renderer'
import * as drawing from '../src/drawingTask.js'
import * as drawingPreview from '../src/drawingPreview.js'
import * as simTaskPen from '../src/simTaskPen.js'
const originalBrowserTypes={Document:globalThis.Document,ShadowRoot:globalThis.ShadowRoot}
globalThis.Document=class Document{};globalThis.ShadowRoot=class ShadowRoot{}
after(()=>{for(const [key,value] of Object.entries(originalBrowserTypes)){if(value===undefined)delete globalThis[key];else globalThis[key]=value}})
const source=readFileSync(new URL('../src/components/DrawingTask.vue',import.meta.url),'utf8'),descriptor=parse(source).descriptor
function component(world){
 const imports={'../numberDisplay.js':numberDisplay,vue:Vue,'../interactionRange.js':interactionRanges,'../store':{useWorld:()=>world},'../drawingTask.js':drawing,'../drawingPreview.js':drawingPreview,'../simTaskPen.js':simTaskPen}
 const code=compileScript(descriptor,{id:'drawing-component',inlineTemplate:true}).content.replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,path)=>`const ${bindings.replace(/\bas\b/g,':')}=imports[${JSON.stringify(path)}];`).replace('export default','return')
 return new Function('imports',code)(imports)
}
test('drawing component compiles with native inputs and one shared execute/stop plus pause/resume pair',()=>{
 const script=compileScript(descriptor,{id:'drawing-component'})
 assert.deepEqual(compileTemplate({source:descriptor.template.content,id:'drawing-component',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
 for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,id:'drawing-component',scoped:true}).errors,[])
 assert.doesNotMatch(source,/connectReal|enableReal|setInterval[^\n]*progress|innerHTML|v-html/)
})

test('choosing a pattern previews locally from the front, and execution automatically mounts the pen once',async()=>{
 const h=liveFixture()
 try{
  assert.equal(h.requests.length,0);assert.equal(h.toolCalls.length,0)
  assert.match(h.calls.at(-1).plan_id,/^draft:/)
  const oldPath=h.calls.at(-1).path
  h.api.form.shape='square';await Vue.nextTick()
  assert.notDeepEqual(h.calls.at(-1).path,oldPath);assert.equal(h.calls.at(-1).valid,false)
  assert.equal(h.views.at(-1).theta,Math.PI)
  assert.deepEqual(h.api.form.center,[-500,0,700,90,0,0]);assert.equal(h.api.rangeDirection.value,270)
  assert.deepEqual(h.api.draftConfig.value.center,[-500,0,700,90,0,-90],'actual pen frame follows the original front plane')
  assert.ok(h.calls.at(-1).path.every(point=>point.slice(3).join(',')==='90,0,-90'),'local preview uses the same pen frame')
  assert.equal(h.requests.length,0);assert.equal(h.toolCalls.length,0,'automatic preview never changes tools')
  await h.api.toggleRun();await Vue.nextTick()
  assert.deepEqual(h.toolCalls,[{url:'/api/sim/task-tool',payload:{task:'drawing'}}])
  assert.ok(h.requests.includes('preview'));assert.ok(h.requests.includes('run'))
  assert.equal(h.calls.at(-1).preview_visible,false,'execution hides intent curves while preserving actual ink')
  const submitted=h.requestPayloads.find(row=>row.action==='preview').payload
  assert.deepEqual(submitted.center,[-500,0,700,90,0,-90])
  assert.deepEqual(submitted.range_center_mm,[-500,0,700]);assert.deepEqual(submitted.range_size_mm,[900,300,500]);assert.equal(submitted.direction,270)
 }finally{h.close()}
})
for(const mode of ['sim','real'])test(`actual ${mode} drawing template renders without implicit task/device action`,async()=>{
 const world=Vue.reactive({connected:true,stateFresh:true,snap:{mode,session_id:'s1',motion_granted:true,real:mode==='real'?{cmd_up:true,stream_up:true,quality:'valid',connection_epoch:1}:null}})
 const app=Vue.createSSRApp(component(world)),warnings=[];app.config.warnHandler=message=>warnings.push(message)
 const html=await renderToString(app)
 assert.deepEqual(warnings,[]);assert.match(html,/drawing-task/);assert.match(html,/绘制范围/);assert.match(html,/X · 宽中心坐标/);assert.match(html,/预览/)
 for(const label of ['爱心','正方形','长方形','圆圈','点头','摇头'])assert.ok(html.includes(label))
 assert.match(html,/>执行<|>执行<!--/)
 assert.ok(!html.includes('type="checkbox"'),'real confirmation appears only after an owned ready plan')
 const buttons=[...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map(match=>match[1].replace(/<[^>]*>/g,'').trim())
 if(mode==='sim')assert.deepEqual(buttons,['预览','执行'])
 else {assert.deepEqual(buttons,['读取当前TCP','采用基准','预览','执行']);assert.doesNotMatch(html,/value="400"/,'real does not inherit simulation center')}

})


function liveFixture(mode='sim',mounted=false,savedDefault=null,serverDefault=null){
 const calls=[],clears=[],hooks=[],views=[],requests=[],requestPayloads=[],bounds=[],settingsCalls=[],toolCalls=[],storage=new Map(savedDefault?[['huayan.hand-follow.range-default.v1',JSON.stringify(savedDefault)]]:[])
 const world=Vue.reactive({connected:true,stateFresh:true,snap:{mode,session_id:'s1',settings:{interaction_range_default:serverDefault},config_rev:1,motion_granted:true,real:mode==='real'?{cmd_up:true,stream_up:true,quality:'valid',connection_epoch:1}:null,joints:{known:true,q:[0,0,0,0,0,0]},tcp:{xyz_mm:[400,0,450],rpy_deg:[180,0,0]},exec:{current:null}}})
 world.call=async(url,payload)=>{toolCalls.push({url,payload});world.snap.tools={visual:{kind:'pen'},mounted:{tool_id:'pen',revision:2}};return {ok:true}}
 let controller,phase='ready',traceOffset=[0,0,0],saveFailure=false
 world.setSettings=async patch=>{settingsCalls.push(patch);if(saveFailure)return {ok:false,why:'保存失败'};world.snap.settings={...world.snap.settings,...patch};return {ok:true}}
 const center=[400,0,450,180,0,0],trace=[[405,0,460,180,0,0],[410,0,450,180,0,0],[400,0,450,180,0,0],[405,0,460,180,0,0]]
 const full=[center,[400,0,460,180,0,0],...trace,center]
 const snapshot=()=>({ok:true,plan_id:'visual-plan',phase,mode,session_id:'s1',config:{shape:'heart',plane:'xz',center},bounds:{frame:'base',min:[290,-40,340],max:[510,40,560],margin_mm:2},trace_tcp:{valid:true,offset_in_snapshot_tcp_mm:[...traceOffset]},path:full,trace_path:trace,program:'WAIT .1',expires_in_s:120,can_run:phase==='ready',progress:{completed:phase==='completed'?2:0,total:2}})
 const runtime={...Vue,onMounted:()=>{},onActivated:()=>{},onDeactivated:fn=>hooks.push(fn),onBeforeUnmount:fn=>hooks.push(fn)}
 const imports={'../numberDisplay.js':numberDisplay,vue:runtime,'../interactionRange.js':interactionRanges,'../store':{useWorld:()=>world},'../drawingPreview.js':drawingPreview,'../simTaskPen.js':simTaskPen,'../drawingTask.js':{...drawing,createDrawingTask:(w,options)=>controller=drawing.createDrawingTask(w,{...options,request:async(action,payload)=>{requests.push(action);requestPayloads.push({action,payload});if(action==='run')phase='running';if(action==='pause')phase='paused';if(action==='resume')phase='running';if(action==='cancel')phase='cancelled';return snapshot()}})}}
 const code=compileScript(descriptor,{id:'drawing-live',inlineTemplate:mounted}).content.replace(/^import (.+?) from ['"]([^'"]+)['"];?\s*$/gm,(_,bindings,path)=>`const ${bindings.replace(/\bas\b/g,':')}=imports[${JSON.stringify(path)}];`).replace('export default','return')
 const scene={setPlanPath(){throw Error('generic plan projection must not add approach lines')},clearPlanPath(){},setDrawingBounds:value=>bounds.push(value),setView:value=>views.push(value),setDrawingState:value=>calls.push(value),clearDrawing:owner=>clears.push(owner)}
 const component=new Function('imports','globalThis',code)(imports,{window:{__elfinScene:scene},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)}}),scope=Vue.effectScope()
 const api=mounted?null:scope.run(()=>component.setup({},{expose(){}}))
 return {component,api,world,calls,clears,hooks,views,requests,requestPayloads,bounds,storage,settingsCalls,toolCalls,trace,full,setSaveFailure(value){saveFailure=value},setTraceOffset(value){traceOffset=[...value]},setPhase(value){phase=value},get controller(){return controller},close(){controller.dispose();scope.stop()}}
}

test('actual component uses closed trace contour while retaining full checked motion path and samples only same-plan trace instructions immediately',async()=>{
 const h=liveFixture(),{api,world,calls,requests,trace,full,controller}=h
 try{
  assert.equal(api.form.plane,'xz')
  await api.preview();assert.equal(requests.length,0,'local preview does not submit backend motion planning')
  await controller.preview(api.draftConfig.value);await Vue.nextTick();assert.equal(calls.at(-1).plan_id,'visual-plan');assert.deepEqual(calls.at(-1).tcp,[400,0,450,180,0,0])
  assert.deepEqual(calls.at(-1).path,trace);assert.deepEqual(calls.at(-1).path[0],calls.at(-1).path.at(-1),'display contour is complete and closed')
  assert.deepEqual(controller.state.plan.path,full,'approach and return remain in actual motion precheck')
  assert.notDeepEqual(calls.at(-1).path,full,'center approach must not draw a diagonal through the contour')
  await controller.act('run');world.snap.tcp.xyz_mm=[405,0,450];await Vue.nextTick()
  assert.equal(calls.at(-1).phase,'running');assert.deepEqual(calls.at(-1).tcp,[405,0,450,180,0,0]);assert.equal(calls.at(-1).progress.completed,0);assert.equal(calls.at(-1).trace_active,false)
  const beforeRequests=requests.length
  world.snap.exec.current={drawing_plan_id:'visual-plan',drawing_trace:true};await Vue.nextTick()
  assert.equal(calls.at(-1).trace_active,true);assert.equal(requests.length,beforeRequests,'trace gate follows live state, without waiting for 500ms task polling')
  world.snap.exec.current={drawing_plan_id:'foreign',drawing_trace:true};await Vue.nextTick();assert.equal(calls.at(-1).trace_active,false)
  world.snap.exec.current={drawing_plan_id:'visual-plan',drawing_trace:false};await Vue.nextTick();assert.equal(calls.at(-1).trace_active,false,'approach/return instructions cannot add a stroke')
  await controller.act('pause');world.snap.tcp.xyz_mm=[406,0,450];await Vue.nextTick();assert.equal(calls.at(-1).phase,'paused')
  const clearCountBeforeCancel=h.clears.length
  await controller.cancel();await Vue.nextTick();assert.equal(h.clears.length,clearCountBeforeCancel,'cancelling must preserve the actual stroke instead of clearing it');assert.equal(calls.at(-1).phase,'cancelled');assert.equal(controller.visualSnapshot().plan_id,'visual-plan')
  assert.equal(controller.owned(),false);assert.equal(calls.at(-1).valid,true,'retained terminal measurement can render without restoring motion ownership')
  const boundsBeforeLeave=h.bounds.length;h.hooks[0]();await Vue.nextTick();assert.equal(h.bounds.length,boundsBeforeLeave,'deactivated page must not repaint its draft range');assert.ok(h.clears.every(owner=>owner===controller.owner));assert.equal(controller.visualSnapshot(),null)
 }finally{h.close()}
})

test('view drawing changes only finite camera view parameters and never q, task progress or API authority',async()=>{
 const h=liveFixture()
 try{
  h.views.length=0;h.api.focusDrawing();assert.equal(h.views.length,1,'valid draft range is visible without a motion ticket')
  h.views.length=0
  await h.controller.preview(h.api.draftConfig.value);await Vue.nextTick()
  const q=[...h.world.snap.joints.q],before=h.requests.length,progress={...h.controller.state.snapshot.progress}
  h.api.focusDrawing();assert.equal(h.views.length,1)
  const view=h.views[0];assert.ok(['theta','phi','r'].every(key=>Number.isFinite(view[key])));assert.ok(view.r>0)
  assert.ok(Array.isArray(view.target)&&view.target.length===3&&view.target.every(Number.isFinite))
  assert.deepEqual(view.target,[.255,.28,0]);assert.ok(view.r>=1.1,'framing includes robot and the drawing range');assert.equal(h.requests.length,before);assert.deepEqual(h.world.snap.joints.q,q);assert.deepEqual(h.controller.state.snapshot.progress,progress)
 }finally{h.close()}
})

test('real mode never emits simulated drawing visuals before or after an explicitly adopted base preview',async()=>{
 const h=liveFixture('real')
 try{
  await h.api.preview();assert.equal(h.requests.length,0);assert.equal(h.calls.length,0)
  await h.controller.read();h.api.takeBase();await h.api.preview();await Vue.nextTick();assert.equal(h.controller.owned(),true)
  assert.equal(h.calls.length,0);assert.equal(h.controller.state.confirmedReal,false)
  await h.controller.act('run');assert.equal(h.requests.includes('run'),false)
  assert.deepEqual(h.world.snap.joints.q,[0,0,0,0,0,0])
 }finally{h.close()}
})


test('mounted idle controls react through checking, ready, running, paused, resumed and completed',async()=>{
 const h=liveFixture('sim',true),errors=[]
 const node=(type,text='')=>({type,text,children:[],parent:null,props:{},value:'',options:[],addEventListener(){},removeEventListener(){}})
 const renderer=Vue.createRenderer({createElement:type=>node(type),createText:text=>node('#text',text),createComment:text=>node('#comment',text),
 insert(child,parent,anchor){if(child.parent)child.parent.children.splice(child.parent.children.indexOf(child),1);child.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;if(at<0)parent.children.push(child);else parent.children.splice(at,0,child)},
 remove(child){if(child.parent)child.parent.children.splice(child.parent.children.indexOf(child),1);child.parent=null},setText(el,text){el.text=text},setElementText(el,text){el.text=text;el.children=[]},parentNode:el=>el.parent,nextSibling:el=>el.parent?.children[el.parent.children.indexOf(el)+1]||null,patchProp(el,key,old,value){el.props[key]=value;if(key==='value'){el.value=value;el._value=value}}})
 const root=node('root'),app=renderer.createApp(h.component);app.config.errorHandler=error=>errors.push(error)
 const all=el=>[el,...el.children.flatMap(all)],text=el=>el.text+el.children.map(text).join('')
 const button=label=>{const result=all(root).find(el=>el.type==='button'&&text(el).trim()===label);assert.ok(result,`button ${label} exists`);return result}
 const settle=async()=>{for(let i=0;i<6;i++){await Promise.resolve();await Vue.nextTick()}}
 const click=async label=>{const el=button(label);assert.equal(!!el.props.disabled,false,`${label} enabled`);await el.props.onClick();await settle()}
 try{
 app.mount(root);await settle();assert.ok(!all(root).some(el=>el.type==='button'&&text(el).trim()==='读取当前TCP'),'simulation has no real base controls');assert.equal(!!button('执行').props.disabled,false)
 h.setPhase('idle');await h.controller.read();h.world.meta={model:{variant:'elfin5'}};h.world.snap.settings={fence:{enabled:true},self_collision:true};h.world.stateFresh=false;h.world.stateFresh=true;await settle();assert.equal(h.controller.state.error,'');assert.equal(all(root).filter(el=>el.props.role==='alert').length,0,'idle metadata settling has no false environment alarm')
  h.setPhase('checking');await click('执行');assert.equal(!!button('停止').props.disabled,false)
 h.setPhase('ready');await h.controller.read();await settle();assert.equal(!!button('停止').props.disabled,false)
 assert.ok(all(root).filter(el=>el.type==='input').every(el=>{for(let p=el;p;p=p.parent)if(p.props.disabled)return true;return false}),'running disables editing including fieldset controls')
 await h.controller.act('pause');await settle();assert.equal(!!button('停止').props.disabled,false)
 await h.controller.act('resume');await settle()
 h.setPhase('completed');await h.controller.read();await settle();assert.equal(!!button('执行').props.disabled,false)
 assert.deepEqual(h.requests.filter(action=>action!=='state'),['preview','run','pause','resume']);assert.deepEqual(errors,[])
 }finally{h.close();app.unmount()}
})


test('entering and editing the sim drawing range is immediate and creates no motion authority or request',async()=>{
 const h=liveFixture()
 try{
 assert.equal(h.api.form.width_mm,280);assert.equal(h.api.form.height_mm,240)
 assert.equal(h.api.form.speed_pct,100)
 for(const shape of ['square','circle','heart']){h.api.form.shape=shape;await Vue.nextTick();assert.equal(h.api.inputError.value,'',`default ${shape} fits the range including margin`)}
 assert.equal(h.requests.length,0);assert.equal(h.controller.owned(),false);assert.equal(h.controller.state.plan,null)
 assert.deepEqual(h.bounds.at(-1).bounds,{min:[-650,-450,450],max:[-350,450,950]});assert.equal(h.bounds.at(-1).plan_id,undefined)
 const q=[...h.world.snap.joints.q]
 h.api.rangeSize.value=[1000,400,600];await Vue.nextTick();assert.deepEqual(h.bounds.at(-1).bounds,{min:[-700,-500,400],max:[-300,500,1000]})
 h.api.form.shape='circle';await Vue.nextTick();assert.equal(h.api.draftConfig.value.range_size_mm[2],600)
 const count=h.bounds.length;h.api.form.width_mm='';await Vue.nextTick();assert.equal(h.bounds.length,count,'invalid empty numbers never publish a fabricated range')
 assert.equal(h.requests.length,0);assert.deepEqual(h.world.snap.joints.q,q);assert.equal(h.controller.state.plan,null)
 }finally{h.close()}
})


test('drawing range apply/default/reset consumes backend defaults and preserves drafts on save failure',async()=>{
 const h=liveFixture()
 try{
 h.api.rangeSize.value=[1000,400,600];h.api.form.center[0]=-520;await Vue.nextTick()
 h.api.applyRange();await h.api.saveRangeDefault();await Vue.nextTick()
 assert.equal(h.requests.length,0);assert.equal(h.controller.owned(),false)
 const saved=h.world.snap.settings.interaction_range_default
 assert.deepEqual(saved,{center:[-520,0,700],size:[1000,400,600],direction:270,depthRange:[.06,.3]})
 assert.equal(h.storage.has('elfin.drawing.range-default.v1'),false,'no second active local default')
 const reopened=liveFixture('sim',false,null,saved)
 try{assert.deepEqual(reopened.api.form.center,[-520,0,700,90,0,0]);assert.deepEqual(reopened.api.rangeSize.value,[1000,400,600]);assert.equal(reopened.controller.state.plan,null);assert.equal(reopened.controller.owned(),false);assert.equal(reopened.requests.length,0)}finally{reopened.close()}
 const real=liveFixture('real',false,null,saved)
 try{assert.deepEqual(real.api.form.center,Array(6).fill(null));assert.equal(real.calls.length,0);assert.equal(real.bounds.length,0)}finally{real.close()}
 h.api.resetRange();await Vue.nextTick();assert.deepEqual(h.api.form.center,[-500,0,700,90,0,0]);assert.deepEqual(h.api.rangeSize.value,[900,300,500]);assert.equal(h.requests.length,0)
 h.setSaveFailure(true);h.api.form.center[0]=-550;await h.api.saveRangeDefault();assert.equal(h.api.message.value,'保存失败');assert.equal(h.api.form.center[0],-550);assert.deepEqual(h.world.snap.settings.interaction_range_default,saved)
 const count=h.settingsCalls.length;h.api.rangeSize.value[0]='';await Vue.nextTick();await h.api.saveRangeDefault();assert.equal(h.settingsCalls.length,count,'invalid input cannot save')
 }finally{h.close()}
})

test('drawing directions rotate the same world center and local dimensions as following without issuing motion',async()=>{
 const h=liveFixture()
 try{
  const original=[...h.api.form.center],size=[...h.api.rangeSize.value]
  for(const direction of [0,90,180,270]){
   h.api.changeDirection(direction);await Vue.nextTick()
   const expected=interactionRanges.rotateInteractionCenter(original.slice(0,3),270,direction)
   assert.deepEqual(h.api.form.center.slice(0,3),expected)
   assert.deepEqual(h.api.rangeSize.value,size)
   assert.deepEqual(h.bounds.at(-1).bounds,drawing.drawingRange(expected,size,'sim',direction))
   assert.equal(h.api.draftConfig.value.direction,direction)
   assert.equal(h.requests.length,0)
  }
  assert.deepEqual(h.api.form.center,original)
 }finally{h.close()}
})

test('visible drawing samples the immutable tool contact offset from each live TCP frame without waiting for polling',async()=>{
 const h=liveFixture()
 try{
 h.setTraceOffset([-40,0,30]);await h.controller.preview(h.api.draftConfig.value);await h.controller.act('run');await Vue.nextTick()
 const first=h.calls.at(-1);assert.ok(Math.abs(first.tcp[0]-360)<1e-9);assert.ok(Math.abs(first.tcp[2]-420)<1e-9)
 const requests=h.requests.length
 h.world.snap.tcp={xyz_mm:[410,0,450],rpy_deg:[0,0,90]};await Vue.nextTick()
 const last=h.calls.at(-1);assert.ok(Math.abs(last.tcp[0]-410)<1e-9);assert.ok(Math.abs(last.tcp[1]+40)<1e-9);assert.ok(Math.abs(last.tcp[2]-480)<1e-9)
 assert.equal(h.requests.length,requests,'contact follows live feedback, not 500ms state samples')
 assert.deepEqual(h.world.snap.joints.q,[0,0,0,0,0,0])
 h.controller.state.snapshot.trace_tcp=null;await Vue.nextTick();assert.equal(h.calls.at(-1).valid,false,'unknown tip cannot publish a valid flange stroke')
 }finally{h.close()}
})


test('adopting the current drawing base requires a current tool descriptor and uses its contact point',async()=>{
 const h=liveFixture()
 try{
 h.setTraceOffset([-40,0,30]);const initial=[...h.api.form.center]
 h.api.takeBase();assert.deepEqual(h.api.form.center,initial);assert.equal(h.controller.state.baseAccepted,false)
 await h.controller.read();h.api.takeBase()
 assert.ok(Math.abs(h.api.form.center[0]-360)<1e-9);assert.ok(Math.abs(h.api.form.center[2]-420)<1e-9);assert.deepEqual(h.api.form.center.slice(3),[180,0,0]);assert.equal(h.controller.state.baseAccepted,true)
 assert.deepEqual(h.requests,['state'],'adopting a measured point does not preview or move')
 h.world.snap.session_id='replacement';await Vue.nextTick();const accepted=[...h.api.form.center];h.api.takeBase();assert.deepEqual(h.api.form.center,accepted);assert.equal(h.controller.state.baseAccepted,false,'old session descriptor cannot restore base permission')
 }finally{h.close()}
})


test('mounted ready/completed edits replace old plan visuals with the new draft bounds while cancel retains its stroke',async()=>{
 for(const phase of ['ready','completed']){
 const h=liveFixture('sim',true),errors=[]
 const node=(type,text='')=>({type,text,children:[],parent:null,props:{},value:'',options:[],listeners:{},getRootNode:()=>({activeElement:null}),addEventListener(type,fn){this.listeners[type]=fn},removeEventListener(type){delete this.listeners[type]}})
 const renderer=Vue.createRenderer({createElement:type=>node(type),createText:text=>node('#text',text),createComment:text=>node('#comment',text),
 insert(child,parent,anchor){if(child.parent)child.parent.children.splice(child.parent.children.indexOf(child),1);child.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;if(at<0)parent.children.push(child);else parent.children.splice(at,0,child)},
 remove(child){if(child.parent)child.parent.children.splice(child.parent.children.indexOf(child),1);child.parent=null},setText(el,text){el.text=text},setElementText(el,text){el.text=text;el.children=[]},parentNode:el=>el.parent,nextSibling:el=>el.parent?.children[el.parent.children.indexOf(el)+1]||null,patchProp(el,key,old,value){el.props[key]=value;if(key==='value'){el.value=value;el._value=value}}})
 const root=node('root'),app=renderer.createApp(h.component);app.config.errorHandler=error=>errors.push(error)
 const all=el=>[el,...el.children.flatMap(all)],text=el=>el.text+el.children.map(text).join('')
 const settle=async()=>{for(let i=0;i<6;i++){await Promise.resolve();await Vue.nextTick()}}
 const edit=async(label,value)=>{const el=all(root).find(el=>el.props['aria-label']===label);assert.ok(el);assert.ok(el.listeners.input);el.value=String(value);el.listeners.input({target:el});await settle()}
 try{
 app.mount(root);await settle()
 const preview=all(root).find(el=>el.type==='button'&&text(el).trim()==='预览');await preview.props.onClick();await settle()
 assert.equal(h.requests.length,0,'preview remains local and cannot start planning')
 await h.controller.preview({shape:'heart',center:[-500,0,700,90,0,0],plane:'xz',width_mm:280,height_mm:240,angle_deg:5,speed_pct:100,repeats:1,direction:270,center_policy:'range_lower',range_center_mm:[-500,0,700],range_size_mm:[900,300,500]});await settle()
 h.setPhase(phase);await h.controller.read();await settle();assert.equal(h.calls.at(-1).plan_id,'visual-plan')
 const oldCount=h.calls.length,requestCount=h.requests.length
 await edit('X · 宽中心坐标',420)
 assert.equal(h.controller.visualSnapshot(),null);assert.ok(h.calls.length>oldCount,'editing immediately previews the new local shape');assert.match(h.calls.at(-1).plan_id,/^draft:/)
 assert.deepEqual(h.bounds.at(-1).bounds,{min:[270,-450,450],max:[570,450,950]})
 await edit('X · 宽范围尺寸',240)
 assert.deepEqual(h.bounds.at(-1).bounds,{min:[270,-120,450],max:[570,120,950]})
 assert.match(h.calls.at(-1).plan_id,/^draft:/);assert.equal(h.calls.at(-1).valid,false,'draft has no motion or measured-ink qualification')
 const actions=h.requests.slice(requestCount)
 assert.deepEqual(actions,phase==='ready'?['cancel']:[],'edit only revokes an owned ready ticket; it never starts a new motion')
 assert.deepEqual(errors,[])
 }finally{h.close();app.unmount()}
 }
})
