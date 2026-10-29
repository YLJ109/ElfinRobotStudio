// Geometry-only sidecar. No camera, browser, backend, or real robot is opened.
// Run: node web/scripts/check-hand-follow.mjs
// This proves the shared mirror/letterbox math, NOT that the future sample sender
// uses it. End-to-end sending and private-arm page rendering need integration QA.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { imageBoundsInView, fingertipInView, imagePixelAt, fingerMarkerDiameter } from '../src/handGeometry.js'

const near = (a, b) => assert.ok(Math.abs(a-b) < 1e-9, `${a} != ${b}`)

test('single finger circle grows near, shrinks far and has finite limits', () => {
  const sizes=[.01,.1,.2,.3,1].map(fingerMarkerDiameter)
  assert.deepEqual(sizes,[10,10,18,27,30])
  for (const value of [NaN,Infinity,-1,0,undefined]) assert.equal(fingerMarkerDiameter(value),14)
})

test('camera content corners exclude horizontal letterbox padding', () => {
  assert.deepEqual(imageBoundsInView(400, 400, 640, 480),
    { left: 0, top: 50, width: 400, height: 300 })
  assert.equal(imagePixelAt(200, 49, 400, 400, 640, 480, true), null)
  assert.equal(imagePixelAt(200, 350, 400, 400, 640, 480, true), null)
})

test('portrait image in wide view excludes vertical letterbox padding', () => {
  assert.deepEqual(imageBoundsInView(400, 300, 480, 640),
    { left: 87.5, top: 0, width: 225, height: 300 })
  assert.equal(imagePixelAt(87, 150, 400, 300, 480, 640, true), null)
  assert.equal(imagePixelAt(313, 150, 400, 300, 480, 640, true), null)
})

test('index tip mirrors exactly once into displayed u; v is unchanged', () => {
  const bounds = imageBoundsInView(400, 400, 640, 480)
  for (const [x, y] of [[0, 0], [1, 0], [1, 1], [0, 1], [.5, .5], [.17, .83]]) {
    const p = fingertipInView({ x, y }, 400, 400, 640, 480, true)
    const u = (p.x-bounds.left)/bounds.width
    const v = (p.y-bounds.top)/bounds.height
    near(u, 1-x)
    near(v, y)
    if (x !== .5) assert.notEqual(u, x, 'Mirror was lost or applied twice')
  }
})

test('display tip maps back to the same raw depth pixel, within pixel quantization', () => {
  for (const mirrored of [false, true]) {
    for (const [x, y] of [[.1, .2], [.5, .5], [.9, .8]]) {
      const p = fingertipInView({ x, y }, 400, 400, 640, 480, mirrored)
      const raw = imagePixelAt(p.x, p.y, 400, 400, 640, 480, mirrored)
      assert.ok(Math.abs(raw.x-x*640) <= 1)
      assert.ok(Math.abs(raw.y-y*480) <= 1)
    }
  }
})

test('unmirrored content coordinates preserve raw u/v', () => {
  const p = fingertipInView({ x: .25, y: .75 }, 400, 400, 640, 480, false)
  near(p.x, 100)
  near(p.y, 275)
})

test('nonfinite, zero-size, and out-of-image inputs never produce a sample position', () => {
  assert.equal(imageBoundsInView(0, 100, 640, 480), null)
  assert.equal(imageBoundsInView(NaN, 100, 640, 480), null)
  for (const point of [null, { x: NaN, y: .5 }, { x: Infinity, y: .5 },
    { x: -.01, y: .5 }, { x: .5, y: 1.01 }]) {
    assert.equal(fingertipInView(point, 400, 400, 640, 480, true), null)
  }
})
