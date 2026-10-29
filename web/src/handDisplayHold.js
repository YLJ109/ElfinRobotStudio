import { cleanHandLandmarks, palmFromLandmarks } from './handGeometry.js'

// Display only. Never use this cache as a camera observation or motion input.
export function createHandDisplayHold() {
  let last = null, lastSequence = 0, lastTimestamp = -Infinity
  const empty = Object.freeze({ point: null, landmarks: Object.freeze([]), frameSeq: 0, expiresAt: 0 })
  function read(now) {
    if (!Number.isFinite(now) || !last || now < last.receivedAt || now >= last.expiresAt) {
      last = null
      return empty
    }
    return last
  }
  function update(observation, now) {
    const receivedAt = observation?.receivedAt
    if (observation?.point && Number.isSafeInteger(observation.frameSeq) && observation.frameSeq > lastSequence
      && Number.isFinite(observation.timestamp) && observation.timestamp > lastTimestamp
      && Number.isFinite(receivedAt) && receivedAt >= observation.timestamp && receivedAt <= now
      && now < receivedAt + 1000) {
      lastSequence = observation.frameSeq; lastTimestamp = observation.timestamp
      last = { point: { ...observation.point }, landmarks: observation.landmarks.map(p => p ? { ...p } : null),
        frameSeq: observation.frameSeq, timestamp: observation.timestamp, receivedAt, expiresAt: receivedAt + 1000 }
    }
    return read(now)
  }
  function observe(result, now) {
    // A valid but slow inference may be too old for motion. It can still draw
    // the measured skeleton; never copy it back into the motion tracker/bus.
    if (!Number.isSafeInteger(result?.frameSeq) || result.frameSeq <= lastSequence
      || !Number.isFinite(result.timestamp) || result.timestamp <= lastTimestamp
      || result.timestamp > now || now - result.timestamp >= 1000) return read(now)
    const landmarks = cleanHandLandmarks(result.landmarks), point = palmFromLandmarks(landmarks)
    return point ? update({ point, landmarks, frameSeq: result.frameSeq, timestamp: result.timestamp, receivedAt: now }, now) : read(now)
  }
  return { read, update, observe, reset() { last = null; lastSequence = 0; lastTimestamp = -Infinity } }
}
