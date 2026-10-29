import { createInteractionTarget } from './interactionCore.js'

// Compatibility frame for the shared HandFollowClient; never a camera producer.
export function createMouseFollowInput(session, saved) {
  const target = createInteractionTarget(saved)
  let sequence = 0
  return {
    move:target.move,
    nudge:target.nudge,
    advance:target.advance,
    depth:target.depth,
    center:target.center,
    target:target.read,
    position() { const {u:x, v:y, depth} = target.read(); return {x, y, depth} },
    frame(now = performance.now()) {
      const {u:x, v:y, depth:opening} = target.read()
      return {session, source:'interaction', stream:'interaction', width:640, height:480, fresh:true,
        frameSeq:++sequence, timestamp:now, trackingState:'tracking',
        point:{x, y, palm_scale:1, dx:1, dy:0, hand_open:opening}}
    },
  }
}
