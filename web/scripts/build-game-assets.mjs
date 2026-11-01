// 从 CC-BY-4.0 的 Khronos「A Beautiful Game」模型裁剪出 6 种棋子的轻量 GLB。
// 运行：node scripts/build-game-assets.mjs <ABeautifulGame.glb> [输出路径]
// 只保留棋子网格，去除棋盘/重复棋子/贴图，并做网格简化；颜色在运行时按阵营设置。
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { NodeIO } from '@gltf-transform/core'
import { ALL_EXTENSIONS } from '@gltf-transform/extensions'
import { dedup, prune, simplify, weld } from '@gltf-transform/functions'
import { MeshoptSimplifier } from 'meshoptimizer'

const webRoot = fileURLToPath(new URL('../', import.meta.url))
const source = process.argv[2]
if (!source) throw new Error('usage: node scripts/build-game-assets.mjs <ABeautifulGame.glb> [out]')
const out = process.argv[3] || path.join(webRoot, 'public/models/games/chess_pieces.glb')

// 每种棋子只保留一个节点；白/黑各一套，兵由 Body+Top 两个网格组成。
const KEEP = [
  'King_W', 'King_B', 'Queen_W', 'Queen_B', 'Castle_W1', 'Castle_B1',
  'Knight_W1', 'Knight_B1', 'Bishop_W1', 'Bishop_B1',
  'Pawn_Body_W1', 'Pawn_Top_W1', 'Pawn_Body_B1', 'Pawn_Top_B1',
]

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
const doc = await io.read(source)
const documentRoot = doc.getRoot()

const meshes = []
for (const node of documentRoot.listNodes()) {
  if (KEEP.includes(node.getName()) && node.getMesh()) {
    meshes.push({ name: node.getName(), mesh: node.getMesh() })
  }
}
if (!meshes.length) throw new Error('没有找到可裁剪的棋子节点')

// 保留原节点层级与局部变换：骑士朝向、兵身与兵帽的相对位置都在变换里。
// 只删除不需要的根节点，不再重建坐标，避免把棋子压回原点。
const scenes = documentRoot.listScenes()
if (scenes.length !== 1) throw new Error('需要单一场景的源模型')
const scene = scenes[0]
for (const node of documentRoot.listNodes()) {
  const parent = node.getParentNode()
  if (!KEEP.includes(node.getName()) && (!parent || !KEEP.includes(parent.getName()))) node.dispose()
}
for (const mesh of documentRoot.listMeshes()) {
  for (const primitive of mesh.listPrimitives()) primitive.setMaterial(null)
}
for (const material of documentRoot.listMaterials()) material.dispose()
for (const texture of documentRoot.listTextures()) texture.dispose()

if (!scene.listChildren().length) throw new Error('裁剪后没有棋子节点')

await MeshoptSimplifier.ready
await doc.transform(
  weld(),
  simplify({ simplifier: MeshoptSimplifier, ratio: 0.35, error: 0.001 }),
  prune(),
  dedup(),
)
await io.write(out, doc)
console.log('wrote', out, 'bytes', (await import('node:fs')).statSync(out).size,
  'nodes', documentRoot.listNodes().map(node => node.getName()).join(','))
