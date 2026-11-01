import * as THREE from 'three'
import { disposeAsset } from './toolVisual.js'

// Both preview and the live scene use one centering/scaling path. The server
// accepts only self-contained static GLB; a URL modifier adds a second barrier.
export async function parseSceneAsset(buffer, sizeMm) {
  const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js')
  const manager = new THREE.LoadingManager()
  manager.setURLModifier(() => { throw new Error('场景资源不允许加载外部文件') })
  const loaded = await new Promise((resolve, reject) => new GLTFLoader(manager).parse(buffer, '', resolve, reject))
  const model = loaded.scene
  try {
    model.updateMatrixWorld(true)
    const bounds = new THREE.Box3().setFromObject(model)
    if (bounds.isEmpty()) throw new Error('资源没有可显示的几何')
    const actual = bounds.getSize(new THREE.Vector3())
    const center = bounds.getCenter(new THREE.Vector3())
    model.position.sub(center)
    const root = new THREE.Group()
    root.add(model)
    const target = [sizeMm[0], sizeMm[2], sizeMm[1]].map(value => value / 1000)
    root.scale.set(...target.map((value, index) => value / (actual.getComponent(index) || 1)))
    root.userData.sceneAsset = true
    return root
  } catch (error) { disposeAsset(model); throw error }
}
