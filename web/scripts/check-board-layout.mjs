import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import {baseToGameScene,readBoardLayout,boardCellBase} from '../src/gameBoardLayout.js'
import {createGameLayer} from '../src/gameSceneLayer.js'

const layout=(game='go')=>({game,center_mm:[-500,game==='chess'||game==='xiangqi'?0:260,180],axis_col:[0,-1,0],axis_row:[-1,0,0],span_mm:game==='xiangqi'?400:game==='go'||game==='gomoku'?380:360,rows:game==='ttt'?3:game==='chess'?8:game==='xiangqi'?10:15,cols:game==='ttt'?3:game==='chess'?8:game==='xiangqi'?9:15,board_top_mm:180,board_size_mm:[410,410,180],tray_center_mm:[-500,-260,160],tray_top_mm:160,tray_size_mm:[380,380,160],tray_rows:15,tray_cols:15,tray_pitch_mm:23,rim_height_mm:8,rim_width_mm:6})
for(const game of ['chess','xiangqi','ttt','go','gomoku'])test(game+' uses a right-handed front-facing base frame and exact 2D cell locations',()=>{
 const l=layout(game),t=readBoardLayout(l,game)
 assert.ok(t);assert.equal(readBoardLayout(l,'other'),null)
 const row0=boardCellBase(l,0,0),rowLast=boardCellBase(l,l.rows-1,0),colLast=boardCellBase(l,0,l.cols-1)
 assert.ok(row0[0]>rowLast[0]);assert.ok(row0[1]>colLast[1])
 const pitch=l.span_mm/Math.max(l.rows,l.cols),local=new THREE.Vector3(-(l.cols-1)*pitch/2000,0,-(l.rows-1)*pitch/2000)
 local.applyMatrix4(t.basis).add(t.center)
 assert.ok(local.distanceTo(baseToGameScene(row0))<1e-10)
})
test('mirrored/degenerate/missing layout cannot silently render a guessed chessboard',()=>{
 assert.equal(readBoardLayout(null,'go'),null)
 assert.equal(readBoardLayout({...layout(),axis_row:[1,0,0]},'go'),null)
 assert.equal(readBoardLayout({...layout(),axis_col:[0,0,0]},'go'),null)
})
test('physical pieces, tray and held material share the same scene/base transform',()=>{
 const ctx=new Proxy({createLinearGradient:()=>({addColorStop(){}})},{get:(target,key)=>target[key]||(()=>{})})
 const old=globalThis.document;globalThis.document={createElement:()=>({getContext:()=>ctx})}
 try{
  const parent=new THREE.Group(),layer=createGameLayer({parent}),l=layout('go')
  const s={game:'go',rows:15,cols:15,layout:'points',cells:[],physical_layout:l,physical_group:'group'}
  assert.equal(layer.setState(s).ok,true)
  const p=boardCellBase(l,2,5);p[2]+=4
  layer.setSceneObjects([{id:'stone',kind:'cylinder',xyz:p,size:[10,10,8],task:{mode:'board',role:'piece',game:'go',group:'group',code:'black',side:'human'}}])
  const root=parent.getObjectByName('game-layer'),piece=root.children[1].children[0]
  parent.updateMatrixWorld(true)
  assert.ok(piece.getWorldPosition(new THREE.Vector3()).distanceTo(baseToGameScene([p[0],p[1],180]))<1e-10)
  const flange=new THREE.Matrix4().makeTranslation(.15,.42,.3)
  layer.presentAttachment({id:'stone',local_m:[.01,0,0]},flange)
  parent.updateMatrixWorld(true)
  assert.ok(piece.getWorldPosition(new THREE.Vector3()).distanceTo(new THREE.Vector3(.16,.416,.3))<1e-10)
  assert.equal(layer.info().physicalPieces,1)
  layer.dispose();assert.equal(parent.children.length,0)
 }finally{globalThis.document=old}
})

test('moves, selected positions and game switches never call camera focus implicitly',async()=>{
 const {readFileSync}=await import('node:fs')
 const source=readFileSync(new URL('../src/scene.js',import.meta.url),'utf8')
 const body=source.slice(source.indexOf('    setGameState(state)'),source.indexOf('    setGamePhysical('))
 let focused=0,seen=[]
 const invoke=new Function('gameLayer','api','return ({'+body+'})')({setState(s){seen.push(s);return {ok:true}}},{focusGame(){focused++}})
 for(const game of ['chess','go','ttt',null,'xiangqi'])invoke.setGameState(game?{game,selected:[1,1]}:null)
 assert.equal(seen.length,5);assert.equal(focused,0)
})

test('a full 225-piece tray updates coordinates without quadratic scene-tree walks',()=>{
 const ctx=new Proxy({createLinearGradient:()=>({addColorStop(){}})},{get:(t,k)=>t[k]||(()=>{})})
 const previous=globalThis.document;globalThis.document={createElement:()=>({getContext:()=>ctx})}
 const parent=new THREE.Group(),layer=createGameLayer({parent}),l=layout('go')
 const old=THREE.Object3D.prototype.updateMatrixWorld
 try{
  layer.setState({game:'go',rows:15,cols:15,layout:'points',cells:[],physical_layout:l,physical_group:'group'})
  const objects=Array.from({length:225},(_,i)=>({id:'p'+i,kind:'cylinder',xyz:[-650+i%15*23,-420+Math.floor(i/15)*23,164],size:[10,10,8],task:{mode:'board',role:'piece',game:'go',group:'group',code:'black',side:'human'}}))
  layer.setSceneObjects(objects)
  let walks=0
  THREE.Object3D.prototype.updateMatrixWorld=function(...args){walks++;return old.apply(this,args)}
  parent.position.set(.05,.02,-.03)
  objects[224].xyz[0]+=7
  layer.setSceneObjects(objects)
  assert.ok(walks<objects.length*4,'one object update must not revisit the full tray for each piece')
  THREE.Object3D.prototype.updateMatrixWorld=old
  const root=parent.getObjectByName('game-layer'),last=root.children[1].children[224]
  parent.updateMatrixWorld(true)
  const expected=baseToGameScene([...objects[224].xyz.slice(0,2),160])
  assert.ok(last.getWorldPosition(new THREE.Vector3()).distanceTo(expected)<1e-10)
  assert.equal(layer.info().physicalPieces,225)
  layer.setSceneObjects([])
  assert.equal(layer.info().visible,false,'clearing physical objects must not resurrect the old logical board')
  assert.equal(layer.info().physicalPieces,0)
 }finally{THREE.Object3D.prototype.updateMatrixWorld=old;layer.dispose();globalThis.document=previous}
})


test('one board-state update synchronizes the full physical tray exactly once',()=>{
 const ctx=new Proxy({createLinearGradient:()=>({addColorStop(){}})},{get:(t,k)=>t[k]||(()=>{})})
 const previous=globalThis.document;globalThis.document={createElement:()=>({getContext:()=>ctx})}
 const parent=new THREE.Group(),layer=createGameLayer({parent}),l=layout('go')
 const old=THREE.Object3D.prototype.updateWorldMatrix
 let rootSyncs=0
 try{
  const state={game:'go',rows:15,cols:15,layout:'points',cells:[],physical_layout:l}
  layer.setState(state)
  THREE.Object3D.prototype.updateWorldMatrix=function(...args){if(this.name==='game-layer')rootSyncs++;return old.apply(this,args)}
  for(let i=0;i<100;i++)layer.setState({...state,selected:[i%15,0]})
  console.log(JSON.stringify({fixture:'board-state-physical-sync',states:100,rootSyncs}))
  assert.equal(rootSyncs,100)
 }finally{THREE.Object3D.prototype.updateWorldMatrix=old;layer.dispose();globalThis.document=previous}
})
