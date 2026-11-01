// Real Three CPU geometry/lifecycle tests. Does not claim GPU or screenshot validation.
import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import {createDrawingVisual} from '../src/drawingVisual.js'
function fixture(options={}){let time=1000;const scene=new THREE.Scene(),v=createDrawingVisual({scene,now:()=>time,...options});const preview={owner:'ui-a',plan_id:'plan-a',session:'backend-a',mode:'sim',shape:'heart',path:[[0,0,100],[10,0,100],[0,0,100]]};v.setPreview(preview);const sample=(patch={})=>v.sample({...preview,phase:'running',valid:true,tcp:[0,0,100,0,0,0],...patch});return {scene,v,preview,sample,time:value=>{time=value}}}

test('text renders a thin technological cyan outline continuously along measured ink only',()=>{
 const {v,scene,preview}=fixture()
 const text={...preview,shape:'text',plan_id:'purple-text'}
 v.setPreview(text)
 const group=scene.children[0],colors=group.children[0].geometry.attributes.color
 const expected=new THREE.Color(0x25e6ff)
 for(let j=0;j<3;j++)assert.ok(Math.abs(colors.array[j]-[expected.r,expected.g,expected.b][j])<1e-6)
 for(const [seq,x] of [[1,0],[2,5],[3,10]])v.sample({...text,phase:'running',valid:true,trace_active:true,frame_seq:seq,tcp:[x,0,1]})
 assert.equal(v.info().segments,2)
 const ink=group.children[2];assert.equal(ink.material.linewidth,1.8)
 const positions=ink.geometry.attributes.instanceStart.data.array
 assert.deepEqual(Array.from(positions.slice(3,6)),Array.from(positions.slice(6,9)),'adjacent measured segments meet exactly')
 v.sample({...text,phase:'running',valid:true,trace_active:false,frame_seq:4,tcp:[30,0,16]})
 assert.equal(v.info().segments,2,'lifting the pen never connects characters')
 v.dispose()
})

test('execution hides only the planned contour while the range and completed measured ink remain visible',()=>{
 const {scene,v,preview,sample}=fixture()
 v.setBounds({owner:preview.owner,session:preview.session,mode:'sim',bounds:{min:[-20,-10,80],max:[20,10,120]}})
 const frameCount=v.info().frameSegments*2,planned=scene.children[0].children[0].geometry
 assert.ok(planned.drawRange.count>frameCount)
 v.setPreview({...preview,preview_visible:false,phase:'planning'})
 assert.equal(planned.drawRange.count,frameCount,'clicking execute hides the planned curve, not the frame')
 sample({frame_seq:1});sample({frame_seq:2,tcp:[10,0,100]})
 const ink=scene.children[0].children[2]
 assert.equal(ink.visible,true);assert.equal(v.info().segments,1)
 v.setPreview({...preview,preview_visible:false,phase:'completed'})
 sample({phase:'completed',progress:{completed:2,total:2}})
 assert.equal(ink.visible,true);assert.equal(v.info().segments,1)
 v.setPreview({...preview,preview_visible:true,phase:'preview'})
 assert.ok(planned.drawRange.count>frameCount);assert.equal(v.info().segments,1,'re-showing intent does not discard measured ink')
 v.dispose()
})

test('cancelled writing retains measured ink and fill, with depth-independent visible materials',()=>{
 const scene=new THREE.Scene(),v=createDrawingVisual({scene}),path=[[0,0,1],[16,0,1],[16,8,1],[0,8,1],[0,0,1]]
 const identity={owner:'text',plan_id:'partial',session:'s',mode:'sim',shape:'text',path,trace_paths:[path],preview_visible:false}
 v.setPreview(identity)
 const actual_samples=[{seq:1,stroke:0,point:[0,0,1]},{seq:2,stroke:0,point:[5,0,1]}]
 v.sample({...identity,phase:'running',valid:true,tcp:[5,0,1],actual_samples})
 const count=v.info().filledTriangles,segments=v.info().segments,group=scene.children[0],fill=group.getObjectByName('writing-fill')
 assert.ok(count>0);assert.ok(segments>0)
 v.setPreview({...identity,phase:'cancelled'})
 v.sample({...identity,phase:'cancelled',valid:true,tcp:[5,0,1],actual_samples})
 for(const index of [1,2]){assert.equal(group.children[index].visible,true);assert.equal(group.children[index].material.depthTest,false)}
 assert.equal(fill.visible,true);assert.equal(fill.material.depthTest,false);assert.equal(fill.material.depthWrite,false)
 for(let i=0;i<20;i++)v.tick(i*1000)
 assert.equal(v.info().filledTriangles,count);assert.equal(v.info().segments,segments);assert.equal(v.info().completedEffect,false)
 v.dispose()
})

test('dark purple fill follows real pen progress, preserves holes, and never advances with time or completion',()=>{
 const scene=new THREE.Scene(),v=createDrawingVisual({scene})
 const outer=[[0,0,1],[40,0,1],[40,20,1],[0,20,1],[0,0,1]],hole=[[10,5,1],[10,15,1],[30,15,1],[30,5,1],[10,5,1]]
 const identity={owner:'writing',plan_id:'ring',session:'s',mode:'sim',shape:'text',path:outer.concat(hole),trace_paths:[outer,hole]}
 v.setPreview(identity)
 const mesh=scene.children[0].getObjectByName('writing-fill'),geometry=mesh.geometry
 const purple=new THREE.Color(0x542278),color=mesh.geometry.attributes.color.array
 for(let j=0;j<3;j++)assert.ok(Math.abs(color[j]-[purple.r,purple.g,purple.b][j])<1e-6)
 assert.equal(v.info().filledTriangles,0)
 assert.ok(v.info().fillTriangles>0&&v.info().fillTriangles<=16384)
 const history=[{seq:1,stroke:0,point:[0,0,1]},{seq:2,stroke:0,point:[5,0,1]}]
 const state={...identity,phase:'running',valid:true,tcp:[5,0,1],actual_samples:history}
 v.sample(state)
 const partial=v.info().filledTriangles
 assert.ok(partial>0&&partial<v.info().fillTriangles,'fill is partial while the pen is near the first corner')
 for(let i=0;i<100;i++)v.tick(i*100)
 v.sample(state);assert.equal(v.info().filledTriangles,partial,'time and duplicate polls do not invent progress')
 v.sample({...state,phase:'completed',progress:{completed:2,total:2}})
 assert.equal(v.info().filledTriangles,partial,'completion cannot fill untraced regions')
 const array=geometry.attributes.position.array
 for(let i=0;i<geometry.drawRange.count;i+=3){
  const cx=(array[i*3]+array[(i+1)*3]+array[(i+2)*3])*1000/3,cy=-(array[i*3+2]+array[(i+1)*3+2]+array[(i+2)*3+2])*1000/3
  assert.ok(!(cx>10.001&&cx<29.999&&cy>5.001&&cy<14.999),'letter hole remains empty')
 }
 // Complete the actual contours to check the triangulation itself, rather than
 // only checking a partial reveal that has not reached the hole yet.
 const all=outer.map((point,i)=>({seq:i+10,stroke:0,point})).concat(hole.map((point,i)=>({seq:i+20,stroke:1,point})))
 v.sample({...identity,phase:'running',valid:true,tcp:hole.at(-1),actual_samples:all})
 let area=0
 for(let i=0;i<geometry.drawRange.count;i+=3){
  const a=i*3,b=(i+1)*3,c=(i+2)*3
  area+=Math.abs((array[b]-array[a])*(array[c+2]-array[a+2])-(array[c]-array[a])*(array[b+2]-array[a+2]))*500000
 }
 assert.ok(Math.abs(area-600)<.02,'filled area excludes the full 200mm² hole')
 assert.equal(mesh.geometry,geometry)
 v.clear();assert.equal(v.info().filledTriangles,0);assert.equal(mesh.visible,false);v.dispose()
})

test('progressive fill never bridges a pen lift between distant letters and releases buffers',()=>{
 const scene=new THREE.Scene(),v=createDrawingVisual({scene})
 const box=x=>[[x,0,1],[x+8,0,1],[x+8,8,1],[x,8,1],[x,0,1]],paths=[box(0),box(100)]
 const identity={owner:'writing',plan_id:'two',session:'s',mode:'sim',shape:'text',path:paths.flat(),trace_paths:paths}
 v.setPreview(identity)
 const mesh=scene.children[0].getObjectByName('writing-fill');let disposed=0;mesh.geometry.addEventListener('dispose',()=>disposed++)
 v.sample({...identity,phase:'running',valid:true,tcp:[100,0,1],actual_samples:[{seq:1,stroke:0,point:[0,0,1]},{seq:2,stroke:1,point:[100,0,1]}]})
 assert.equal(v.info().segments,0)
 const array=mesh.geometry.attributes.position.array
 for(let i=0;i<mesh.geometry.drawRange.count;i++)assert.ok(array[i*3]<.009||array[i*3]>.099)
 const before=v.info().filledTriangles
 v.sample({...identity,phase:'running',valid:false,tcp:[108,8,1]});assert.equal(v.info().filledTriangles,before)
 v.dispose();v.dispose();assert.equal(disposed,1)
})

test('only written 2D regions smoothly rise into bounded shallow 3D relief',()=>{
 let time=1000
 const scene=new THREE.Scene(),v=createDrawingVisual({scene,now:()=>time})
 const path=[[0,0,1],[30,0,1],[30,12,1],[0,12,1],[0,0,1]]
 const identity={owner:'relief',plan_id:'p',session:'s',mode:'sim',shape:'text',path,trace_paths:[path]}
 v.setPreview(identity)
 const mesh=scene.children[0].getObjectByName('writing-fill'),positions=mesh.geometry.attributes.position.array
 v.tick(99999);assert.equal(mesh.geometry.drawRange.count,0,'no 3D before measured pen contact')
 v.sample({...identity,phase:'running',valid:true,tcp:[0,0,1],frame_seq:1})
 const count=v.info().filledTriangles,base=positions[1]
 assert.ok(count>0);assert.ok(Math.abs(base-.0008)<1e-8,'new ink starts flat')
 assert.equal(v.info().animating,true)
 v.tick(1250);assert.ok(positions[1]>base&&positions[1]<.0088)
 v.tick(1500);assert.ok(Math.abs(positions[1]-.0088)<1e-8)
 assert.equal(v.info().filledTriangles,count,'height animation cannot reveal unwritten triangles')
 assert.equal(v.info().animating,false)
 assert.ok(v.info().reliefVertices<=16384*21);assert.ok(v.info().reliefBytes<=9*1024*1024)
 const normals=mesh.geometry.attributes.normal
 assert.equal(normals.normalized,true);assert.deepEqual(Array.from(normals.array.slice(0,3)),[0,127,0])
 assert.ok(Math.abs(positions[3*3+1]-base)<1e-8,'side bases stay anchored to the floor')
 const top=mesh.geometry.attributes.color.array,side=top.slice(9,12)
 assert.ok(side[0]<top[0]&&side[2]<top[2],'side walls are a restrained darker purple')
 v.clear();assert.equal(v.info().animating,false);assert.equal(v.info().reliefVertices,0);v.dispose()
})

test('raised writing plane moves both relief and cyan ink without changing reported TCP',()=>{
 let time=1000
 const scene=new THREE.Scene(),v=createDrawingVisual({scene,now:()=>time}),path=[[0,0,120],[8,0,120],[8,8,120],[0,8,120],[0,0,120]]
 const identity={owner:'raised',plan_id:'p',session:'s',mode:'sim',shape:'text',path,trace_paths:[path]}
 v.setPreview(identity)
 v.sample({...identity,phase:'running',valid:true,tcp:path[0],frame_seq:1})
 v.sample({...identity,phase:'running',valid:true,tcp:path[1],frame_seq:2})
 const group=scene.children[0],mesh=group.getObjectByName('writing-fill'),positions=mesh.geometry.attributes.position.array
 assert.ok(Math.abs(positions[1]-.1198)<1e-8)
 v.tick(1500);assert.ok(Math.abs(positions[1]-.1278)<1e-8)
 const actual=group.children[2].geometry.attributes.instanceStart.data.array
 assert.ok(Math.abs(actual[1]-.129)<1e-8,'cyan ink stays above the 8mm relief')
 assert.equal(v.info().lastTcp[2],120,'presentation height does not alter measured TCP')
 v.dispose()
})

test('ink style recolors the relief without changing cyan outline or motion progress',()=>{
 const {v,scene,preview}=fixture(),path=[[0,0,1],[8,0,1],[8,8,1],[0,8,1],[0,0,1]]
 const identity={...preview,shape:'text',plan_id:'style',path,trace_paths:[path]}
 v.setPreview(identity);v.sample({...identity,phase:'running',valid:true,tcp:[0,0,1],frame_seq:1})
 const before=v.info().filledTriangles,mesh=scene.children[0].getObjectByName('writing-fill')
 for(const [ink_style,hex] of [['cyan',0x25e6ff],['blue',0x489dff],['pink',0xff58c7],['purple',0x542278]]){
  v.setPreview({...identity,ink_style})
  const expected=new THREE.Color(hex),color=mesh.geometry.attributes.color.array
  for(let j=0;j<3;j++)assert.ok(Math.abs(color[j]-[expected.r,expected.g,expected.b][j])<1e-6)
  assert.equal(v.info().filledTriangles,before)
 }
 v.dispose()
})

test('text previews and measured stroke histories never join separate letters or ink pen lifts',()=>{
 const scene=new THREE.Scene(),v=createDrawingVisual({scene})
 const path=[[0,0,1],[10,0,1],[40,0,1],[50,0,1]],identity={owner:'text',plan_id:'letters',session:'s',mode:'sim',shape:'text',path,trace_paths:[path.slice(0,2),path.slice(2)]}
 v.setPreview(identity)
 assert.equal(scene.children[0].children[0].geometry.drawRange.count,4,'only two disconnected preview segments')
 const actual_samples=path.map((point,seq)=>({point,seq,stroke:seq<2?0:1}))
 v.sample({...identity,phase:'running',valid:true,tcp:[50,0,16],actual_samples})
 assert.equal(v.info().segments,2,'no connecting segment during pen lift')
 v.sample({...identity,phase:'running',valid:true,tcp:[50,0,16],actual_samples})
 assert.equal(v.info().segments,2,'polling the same measured history cannot duplicate ink')
 v.sample({...identity,phase:'completed',valid:true,tcp:[50,0,16],actual_samples,progress:{completed:4,total:4}})
 assert.equal(v.info().completedEffect,true);v.dispose()
})
test('thin complete preview is independent of measured prefix; no elapsed-time fake motion',()=>{
 const {v,sample}=fixture();assert.equal(v.info().previewPoints,3);for(let i=0;i<100;i++)v.tick(i*100)
 assert.equal(v.info().segments,0);sample();sample({tcp:[10,0,100]});assert.equal(v.info().segments,1)
 sample({phase:'paused',tcp:[20,0,100]});assert.equal(v.info().segments,1)
 sample({owner:'old',tcp:[30,0,100]});sample({plan_id:'old',tcp:[30,0,100]});sample({session:'old',tcp:[30,0,100]});assert.equal(v.info().segments,1);v.dispose()
})
test('failure, stale samples and partial completion cannot produce a celebration',()=>{
 for(const phase of ['failed','cancelled','aborted','stopped']){const {v,sample}=fixture();sample({frame_seq:10});sample({frame_seq:11,tcp:[10,0,100]});sample({frame_seq:9,tcp:[20,0,100]});assert.equal(v.info().segments,1);sample({phase});sample({phase:'completed',progress:{completed:2,total:2}});assert.equal(v.info().completedEffect,false);v.dispose()}
 const {v,sample}=fixture();sample();sample({tcp:[10,0,100]});sample({phase:'completed',progress:{completed:1,total:2}});assert.equal(v.info().completedEffect,false);sample({phase:'completed',valid:false,progress:{completed:2,total:2}});assert.equal(v.tick(),false);v.dispose()
})

test('positioning and return-to-center motion do not ink extra strokes across the shape',()=>{
 const {v,sample}=fixture()
 sample({trace_active:false,tcp:[300,0,100]});sample({trace_active:false,tcp:[100,0,100]})
 assert.equal(v.info().samples,0)
 sample({trace_active:true,tcp:[0,0,100]});sample({trace_active:true,tcp:[10,0,100]})
 assert.equal(v.info().segments,1)
 sample({trace_active:false,tcp:[50,0,100]});assert.equal(v.info().segments,1)
 sample({phase:'completed',progress:{completed:3,total:3}})
 assert.equal(v.info().completedEffect,true);v.dispose()
})
test('confirmed same-plan terminal is one-shot and ends within three seconds',()=>{
 const {v,sample,time}=fixture();sample();sample({tcp:[10,0,100]});sample({phase:'completed',progress:{completed:2,total:2}});assert.equal(v.info().completedEffect,true);assert.equal(v.tick(2000),true);assert.ok(v.info().drawCalls<=5);assert.equal(v.tick(3800),false)
 time(5000);sample({phase:'completed',progress:{completed:2,total:2}});assert.equal(v.tick(5000),false);v.dispose()
})
test('buffers and render resources remain identical over bounded steady-state sampling',()=>{
 const {v,scene,sample}=fixture({maxPoints:16,particleCount:1000});const resources=[];scene.traverse(o=>{if(o.geometry)resources.push(o.geometry);if(o.material)resources.push(o.material)})
 const arrays=resources.filter(o=>o.attributes).flatMap(g=>Object.values(g.attributes).map(a=>a.array||a.data.array))
 for(let i=0;i<1000;i++){sample({tcp:[i,0,100],frame_seq:i});v.tick(1000+i);v.resize(1280,720)}
 assert.equal(v.info().capacity,16);assert.equal(v.info().segments,15);assert.equal(v.info().particles,128);assert.equal(v.info().truncated,true)
 const after=[];scene.traverse(o=>{if(o.geometry)after.push(o.geometry);if(o.material)after.push(o.material)});assert.deepEqual(after,resources)
 assert.deepEqual(after.filter(o=>o.attributes).flatMap(g=>Object.values(g.attributes).map(a=>a.array||a.data.array)),arrays);assert.ok(v.info().drawCalls<=5);v.dispose()
})
test('ownership/session/mode changes clear old visual qualification; late clear cannot erase new owner',()=>{
 const {v,preview,sample}=fixture();sample();sample({tcp:[10,0,100]});v.setPreview({...preview,owner:'ui-b',plan_id:'plan-b'});assert.equal(v.info().samples,0);assert.equal(v.clear('ui-a'),false);assert.equal(v.info().owner,'ui-b')
 v.sample({...preview,owner:'ui-b',plan_id:'plan-b',mode:'real',phase:'running',tcp:[0,0,100],valid:true});assert.equal(v.info().owner,null);v.dispose()
})
test('nod/shake use actual measured pose direction rather than fabricated circular paths',()=>{
 const {v,scene,preview}=fixture();v.setPreview({...preview,plan_id:'nod',shape:'nod',path:[[0,0,100],[0,0,100]]})
 const s={owner:preview.owner,plan_id:'nod',session:preview.session,phase:'running',valid:true}
 v.sample({...s,tcp:[0,0,100,0,0,0]});v.sample({...s,tcp:[0,0,100,10,0,0]});assert.equal(v.info().segments,0);assert.equal(v.info().samples,2)
 const direction=scene.children[0].children.find(o=>o.isLine&&o.geometry.attributes.position.array.length===6);assert.ok(direction.visible);const p=direction.geometry.attributes.position.array;assert.notEqual(p[5],p[2]);v.dispose()
})
test('each shared resource is disposed exactly once, including reentrant disposal and late events',()=>{
 const {v,scene,preview}=fixture(),unique=new Set();scene.traverse(o=>{if(o.geometry)unique.add(o.geometry);if(o.material)unique.add(o.material)})
 const counts=new Map();for(const r of unique)r.addEventListener('dispose',()=>counts.set(r,(counts.get(r)||0)+1))
 v.dispose();v.dispose();v.setPreview(preview);v.sample({...preview,tcp:[0,0,0],valid:true,phase:'running'});assert.equal(v.tick(),false);assert.equal(scene.children.length,0);for(const r of unique)assert.equal(counts.get(r),1)
})

test('nonfinite requested budgets cannot escape allocation bounds',()=>{const {v}=fixture({maxPoints:Infinity,particleCount:NaN});assert.equal(v.info().capacity,4096);assert.equal(v.info().particles,96);v.dispose()})


test('draft range immediately renders a bounded numbered cyan box without a plan or trace permission',()=>{
 const scene=new THREE.Scene(),v=createDrawingVisual({scene})
 assert.equal(v.setBounds({owner:'draft',session:'s',mode:'sim',center:[400,0,450],size_mm:[220,80,180],plane:'xz'}),true)
 const info=v.info();assert.deepEqual(info.bounds,{min:[290,-40,360],max:[510,40,540]});assert.ok(info.frameSegments>12);assert.equal(info.plan_id,null);assert.equal(info.samples,0);assert.equal(info.drawCalls,1)
 v.sample({owner:'draft',session:'s',mode:'sim',plan_id:'fake',phase:'running',valid:true,tcp:[400,0,450]});assert.equal(v.info().samples,0)
 assert.equal(v.clear('old'),false);assert.equal(v.clear('draft'),true);assert.equal(v.info().owner,null);assert.equal(v.info().drawCalls,0);v.dispose()
})
test('bounds repetition preserves measured strokes and all five-pass/resource budgets',()=>{
 const {v,scene,preview,sample}=fixture();const bounds={owner:preview.owner,session:preview.session,mode:'sim',bounds:{min:[-20,-10,80],max:[20,10,120]},plane:'xz'}
 v.setBounds(bounds);sample();sample({tcp:[10,0,100],progress:{completed:1,total:2}})
 const count=v.info().segments,resources=[];scene.traverse(o=>{if(o.geometry)resources.push(o.geometry);if(o.material)resources.push(o.material)})
 for(let i=0;i<1000;i++)v.setBounds(bounds)
 assert.equal(v.info().segments,count);assert.ok(v.info().drawCalls<=5)
 const after=[];scene.traverse(o=>{if(o.geometry)after.push(o.geometry);if(o.material)after.push(o.material)});assert.deepEqual(after,resources)
 const materials=scene.children[0].children.map(o=>o.material);assert.ok(materials.some(m=>m.linewidth===6));assert.ok(materials.some(m=>m.linewidth===16));for(const m of materials){assert.equal(m.depthTest,false);assert.equal(m.toneMapped,false)}
 v.dispose();assert.equal(v.info().owner,null)
})
test('explicit bounds reject bad dimensions without replacing the last valid range',()=>{
 const {v,preview}=fixture(),valid={owner:preview.owner,mode:'sim',bounds:{min:[0,0,0],max:[100,80,180]}}
 v.setBounds(valid);const before=v.info().bounds
 for(const bad of [{min:[0,0,0],max:[NaN,2,3]},{min:[0,0,0],max:[-1,2,3]},{min:[0,0,0],max:[999999,2,3]}])assert.equal(v.setBounds({...valid,bounds:bad}),false)
 assert.deepEqual(v.info().bounds,before);v.dispose()
})
