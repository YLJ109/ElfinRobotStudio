import test from 'node:test'
import assert from 'node:assert/strict'
import { createJointPresentation } from '../src/jointPresentation.js'

const q = value => Array(6).fill(value)
test('显示插帧仅在真实反馈端点内，断流不外推', () => {
  const view = createJointPresentation()
  view.reset(q(0), 0); view.push(q(10), .1); view.push(q(20), .2)
  for (let time = .12; time < .32; time += .001) {
    const value = view.sample(time)[0]
    assert.ok(value >= 0 && value <= 20)
  }
  assert.deepEqual([...view.sample(50)], q(20))
})
test('跟随缓冲覆盖两个快照间隔，三次补帧不产生超调或关节绕圈', () => {
  const view = createJointPresentation(); view.setFollowing(true)
  view.reset(q(170), 0)
  for (let i = 1; i <= 12; i++) view.push(q(170+i), i/30)
  let previous = 170, changed = 0
  for (let time = .08; time < .46; time += 1/120) {
    const value = view.sample(time)[0]
    assert.ok(value >= previous-1e-9 && value <= 182)
    if (value > previous) changed++
    previous = value
  }
  assert.ok(changed > 30, '显示补帧数应大于后端快照数')
})
test('变速与反向用例全轴不超调；无效、重复、乱序反馈不污染缓冲', () => {
  const view = createJointPresentation()
  const values = [0, 1, 12, 12, -2, -3, 4]
  values.forEach((value,i) => view.push(q(value), i*.1))
  for (let i = 0; i < values.length-1; i++) {
    for (let j = 0; j <= 20; j++) {
      const value = view.sample(.12+i*.1+j*.005)[0]
      assert.ok(value >= Math.min(values[i],values[i+1])-1e-8)
      assert.ok(value <= Math.max(values[i],values[i+1])+1e-8)
    }
  }
  const count = view.info().samples
  for (const [value,stamp] of [[q(NaN),1],[[0],1],[q(2),NaN],[q(2),.5]]) assert.equal(view.push(value,stamp),false)
  assert.equal(view.info().samples,count)
})
test('增加缓冲和切换跟随模式不倒放；重置隔离会话，样本数量有界', () => {
  const view = createJointPresentation(); view.setFollowing(true)
  for (let i = 0; i < 1000; i++) view.push(q(i), i/30)
  view.sample(33.3); const before = view.info().playback
  view.setFollowing(false); view.sample(33.3)
  assert.ok(view.info().playback >= before); assert.ok(view.info().samples <= 32)
  assert.equal(view.reset(q(-10),34),true); assert.deepEqual([...view.sample(34)],q(-10))
  assert.equal(view.info().samples,1)
})
