import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { DEFAULT_VACUUM } from '../src/visualAssets.js'
import { buildVacuumReference, disposeAsset, applyToolSkin } from '../src/toolVisual.js'

const sizeOf = object => new THREE.Box3().setFromObject(object).getSize(new THREE.Vector3())
test('实拍板：圆角倒边、八个实际孔与两颗沉头螺钉，吸盘为半透明空心杯口', () => {
  const root=buildVacuumReference({})
  try {
    root.updateMatrixWorld(true)
    const plate=root.getObjectByName('dual-vacuum-mounting-plate')
    assert.equal(plate.geometry.type,'ExtrudeGeometry')
    assert.equal(plate.geometry.parameters.shapes.holes.length,8)
    assert.equal(plate.userData.photoReference.cornerRadiusMm,3)
    const cast=(x,y)=>new THREE.Raycaster(new THREE.Vector3(x,y,.2),new THREE.Vector3(0,0,-1)).intersectObject(plate).length
    assert.equal(cast(-.012,.009),0,'孔必须贯通，不是画黑色圆片')
    const inner=root.getObjectByName('black-hole-inner-wall-0')
    assert.equal(inner.material.userData.surface,'black-metal')
    const holeRay=new THREE.Raycaster(new THREE.Vector3(-.012,.009,.2),new THREE.Vector3(0,0,-1))
    const recess=holeRay.intersectObject(root.getObjectByName('black-hole-recess-base-0'))[0]
    assert.ok(recess,'孔内有黑色暗底，不能看到后方银色组件')
    assert.ok(recess.point.z<.05-.001,'暗底必须低于板表面而非平面贴片')
    assert.equal(cast(.1495,.0245),0,'板端角应切圆，不能仍是完整方角')
    assert.ok(cast(.07,0)>0,'安装板实体区域保留')
    for(const side of [-1,1]){
      const cup=root.getObjectByName('open-suction-bell-'+side)
      assert.equal(cup.geometry.type,'LatheGeometry')
      assert.equal(cup.material.transparent,true)
      assert.equal(cup.material.depthWrite,false)
      assert.equal(root.getObjectByName('stem-thread-ridges-'+side).count,6)
    }
    assert.ok(root.getObjectByName('recessed-brass-screw-0'))
    assert.ok(root.getObjectByName('screw-hex-socket-1'))
  } finally { disposeAsset(root) }
})
test('实拍预设：连接40mm，两个完整吸盘各30mm，不重复增加腕部50mm', () => {
  const root = buildVacuumReference({})
  try {
    assert.equal(DEFAULT_VACUUM.mountHeight, 40)
    assert.equal(DEFAULT_VACUUM.cupHeight, 30)
    assert.ok(Math.abs(sizeOf(root).z - .08) < 1e-8)
    for (const side of [-1, 1]) {
      assert.ok(Math.abs(sizeOf(root.getObjectByName('suction-assembly-' + side)).z - .03) < 1e-8)
      assert.equal(root.getObjectByName('hex-locknut-' + side).geometry.parameters.radialSegments, 6)
      assert.equal(root.getObjectByName('blue-air-hose-' + side).geometry.type, 'TubeGeometry')
    }
    assert.equal(root.userData.provenance.controllerCalibrated, false)
    assert.equal(root.getObjectByName('upper-wrist-cylinder'), undefined)
  } finally { disposeAsset(root) }
})

test('不擅自覆盖自定义尺寸：保留旧0mm连接与50mm吸盘，微小有效尺寸也无非有限几何', () => {
  for (const dimensions of [{mountHeight:0,cupHeight:50}, {mountHeight:80,cupHeight:12},
    {length:1,width:1,thickness:.1,edge:.2,cupDiameter:.1,cupHeight:.1,mountHeight:.1}]) {
    const root=buildVacuumReference(dimensions)
    try {
      const p=root.userData.dimensions
      assert.ok(Math.abs(sizeOf(root).z - (p.mountHeight+p.thickness+p.cupHeight)/1000)<1e-7)
      root.traverse(part=>{
        if(part.geometry)assert.ok([...part.geometry.attributes.position.array].every(Number.isFinite))
      })
    } finally { disposeAsset(root) }
  }
})

test('气管与接头皮肤保持本色；重复创建释放后每个自有几何和材质仅释放一次', () => {
  for(let i=0;i<5;i++) {
    const root=buildVacuumReference({}), geometry=new Map(), materials=new Map()
    root.traverse(part=>{
      if(part.geometry)geometry.set(part.geometry,0)
      if(part.material)materials.set(part.material,0)
    })
    for(const map of [geometry,materials])for(const resource of map.keys())resource.addEventListener('dispose',()=>map.set(resource,map.get(resource)+1))
    const hose=root.getObjectByName('blue-air-hose-1').material
    applyToolSkin(root,'cyber');assert.equal(hose.color.getHexString(),'158fc4')
    applyToolSkin(root,'factory');assert.equal(hose.color.getHexString(),'158fc4')
    disposeAsset(root)
    for(const map of [geometry,materials])assert.ok([...map.values()].every(count=>count===1))
  }
})
