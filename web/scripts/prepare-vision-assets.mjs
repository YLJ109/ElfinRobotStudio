import { mkdirSync, copyFileSync, readdirSync, existsSync } from 'node:fs'
import path from 'node:path'
const source = path.resolve('node_modules/@mediapipe/tasks-vision')
const dest = path.resolve('public/vision')
mkdirSync(path.join(dest, 'wasm'), { recursive: true })
copyFileSync(path.join(source, 'vision_bundle.js'), path.join(dest, 'vision_bundle.js'))
for (const name of readdirSync(path.join(source, 'wasm'))) {
  if (/\.(wasm|js)$/.test(name)) copyFileSync(path.join(source, 'wasm', name), path.join(dest, 'wasm', name))
}
if (!existsSync(path.join(dest, 'hand_landmarker.task'))) throw new Error('Missing local hand model; see public/vision/sources.json')
console.log('Local MediaPipe runtime and model ready (no CDN needed at runtime)')
