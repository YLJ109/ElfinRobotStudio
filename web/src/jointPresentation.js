// 仅用于三维显示：对已收到的关节反馈补帧，不预测、不发布机器人指令。
export function createJointPresentation({ now = () => performance.now() / 1000 } = {}) {
  const samples = [], rendered = Array(6).fill(0), output = Array(6).fill(0)
  let following = false, interval = 1 / 30, playback = -Infinity
  const valid = q => Array.isArray(q) && q.length === 6 && q.every(Number.isFinite)
  function push(q, stamp = now()) {
    if (!valid(q) || !Number.isFinite(stamp)) return false
    const previous = samples.at(-1)
    if (previous && stamp <= previous.t) return false
    if (previous) {
      const elapsed = stamp - previous.t
      // 慢帧影响显示缓冲，不无限增加延迟；断流交给连接状态，不靠外推隐藏。
      if (elapsed > .005 && elapsed < .25) interval += (elapsed - interval) * .15
    }
    samples.push({ q: q.slice(), t: stamp })
    while (samples.length > 32 || (samples.length > 3 && samples[1].t < stamp - .5)) samples.shift()
    return true
  }
  function reset(q, stamp = now()) {
    if (!valid(q) || !Number.isFinite(stamp)) return false
    samples.length = 0; playback = -Infinity; interval = 1 / 30
    for (let axis = 0; axis < 6; axis++) rendered[axis] = q[axis]
    return push(q, stamp)
  }
  function tangent(left, center, right, axis) {
    const h0 = center.t-left.t, h1 = right.t-center.t
    const d0 = (center.q[axis]-left.q[axis])/h0, d1 = (right.q[axis]-center.q[axis])/h1
    if (d0*d1 <= 0) return 0
    const w0 = 2*h1+h0, w1 = h1+2*h0
    return (w0+w1)/(w0/d0+w1/d1)
  }
  function sample(stamp = now()) {
    if (!samples.length || !Number.isFinite(stamp)) return rendered
    // 跟随只保留一个实测反馈间隔；普通播放仍用原缓冲，任何模式都不外推。
    const delay = following ? Math.min(.05, Math.max(1 / 120, interval)) : .12
    // 调整延迟不能倒放：展示时钟只前进，换模式也不往回拉机械臂。
    playback = Math.max(playback, stamp-delay)
    let index = samples.findIndex(point => point.t >= playback), value
    if (index < 0) value = samples.at(-1).q
    else if (index === 0) value = samples[0].q
    else {
      const a = samples[index-1], b = samples[index], h = b.t-a.t
      const t = Math.max(0, Math.min(1, (playback-a.t)/h)), t2 = t*t, t3 = t2*t
      for (let axis = 0; axis < 6; axis++) {
        const secant = (b.q[axis]-a.q[axis])/h
        const m0 = index > 1 ? tangent(samples[index-2], a, b, axis) : secant
        const m1 = index+1 < samples.length ? tangent(a, b, samples[index+1], axis) : secant
        const q = (2*t3-3*t2+1)*a.q[axis] + (t3-2*t2+t)*h*m0
          + (-2*t3+3*t2)*b.q[axis] + (t3-t2)*h*m1
        // 单调三次Hermite只在两帧反馈之间补帧，不越过端点或跨360度绕圈。
        output[axis] = Math.max(Math.min(a.q[axis],b.q[axis]), Math.min(Math.max(a.q[axis],b.q[axis]),q))
      }
      value = output
    }
    for (let axis = 0; axis < 6; axis++) rendered[axis] = value[axis]
    return rendered
  }
  return { push, reset, sample, setFollowing: value => { following = !!value },
    moving: () => samples.length >= 2 && samples.at(-1).q.some((v,axis) => Math.abs(v-samples.at(-2).q[axis]) > .004),
    info: () => ({ samples:samples.length, interval, playback }) }
}
