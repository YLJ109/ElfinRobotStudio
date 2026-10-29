import test from 'node:test'
import assert from 'node:assert/strict'
import { GROUND_DIRECTIONS } from '../src/groundDirections.js'

test('four names match the fixed workspace floor directions',()=>{
  assert.deepEqual(GROUND_DIRECTIONS.map(d=>d.name),['右侧','背面','左侧','正面'])
  assert.deepEqual(GROUND_DIRECTIONS.map(d=>d.angle),[0,90,180,270])
  assert.ok(GROUND_DIRECTIONS.every(d=>d.xyz[2]===0))
  assert.equal(GROUND_DIRECTIONS[0].xyz[1],-1150)
  assert.equal(GROUND_DIRECTIONS[1].xyz[0],1150)
  assert.equal(GROUND_DIRECTIONS[2].xyz[1],1150)
  assert.equal(GROUND_DIRECTIONS[3].xyz[0],-1150)
})
