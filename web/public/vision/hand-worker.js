// Classic worker: MediaPipe's WASM bootstrap uses importScripts.
let detector = null, ready = false, lastFrameSeq = 0, busy = false
self.onmessage = async ({ data }) => {
  if (data.type === 'init') {
    try {
      importScripts('/vision/vision_bundle.js')
      const files = await Vision.FilesetResolver.forVisionTasks('/vision/wasm')
      const options = {
        baseOptions: { modelAssetPath: '/vision/hand_landmarker.task', delegate: 'CPU' },
        // Official defaults: the previous .6 thresholds needlessly rejected
        // otherwise usable observations, especially at a low camera frame rate.
        runningMode: 'VIDEO', numHands: 1, minHandDetectionConfidence: .5,
        minHandPresenceConfidence: .5, minTrackingConfidence: .5,
      }
      // 用户指定CPU识别，避免与三维场景争用GPU；保持独立工作线程。
      const delegate = 'CPU'
      detector = await Vision.HandLandmarker.createFromOptions(files,options)
      ready = true
      self.postMessage({ type: 'ready', delegate,
        frameInput:typeof createImageBitmap === 'function' ? 'jpeg' : 'bitmap' })
    } catch (e) { self.postMessage({type:'error', message: e.message || String(e)}) }
  } else if (data.type === 'frame') {
    let bitmap = data.bitmap, ownsBusy = false
    try {
      if (!ready || busy || !Number.isSafeInteger(data.frameSeq) || data.frameSeq <= lastFrameSeq) return
      busy = ownsBusy = true
      lastFrameSeq = data.frameSeq
      // Main and worker performance.now() have different time origins. This
      // deadline uses the camera's host epoch stamp, never a renewed receipt.
      const expired = () => Number.isFinite(data.expiresAt) && Date.now() >= data.expiresAt
      const skip = (stage,decodeMs=0) => self.postMessage({type:'skipped',stage,
        frameSeq:data.frameSeq,timestamp:data.timestamp,decodeMs})
      if (expired()) { skip('before-decode'); return }
      const decodeStarted = performance.now()
      // Blob cloning passes immutable encoded data, not a full decoded RGBA
      // image. Decode/resize off the UI thread; keep the preview independent.
      if (!bitmap && data.blob) {
        const width = data.width, height = data.height
        if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0 ||
            Math.max(width,height) > 640) throw new Error('Invalid inference dimensions')
        bitmap = await createImageBitmap(data.blob,{resizeWidth:width,resizeHeight:height})
      }
      if (!bitmap) throw new Error('Missing camera frame')
      const decodeMs = performance.now() - decodeStarted
      // Decode can yield while a slow frame expires. Avoid an expensive palm
      // relocalization call whose result cannot be used for motion anyway.
      if (expired()) { skip('after-decode',decodeMs); return }
      const started = performance.now()
      const result = detector.detectForVideo(bitmap, data.timestamp)
      const inferenceMs = performance.now() - started
      const landmarks = result.landmarks?.[0]
      // 21 scalar points allow a local, lightweight hand overlay. Images never
      // leave this worker pipeline; invalid points cannot leak NaN into the UI.
      const hand = landmarks ? Array.from({length:21}, (_, i) => {
        const p = landmarks[i]
          return p && [p.x,p.y].every(Number.isFinite)
            ? {x:p.x,y:p.y,...(Number.isFinite(p.z) ? {z:p.z} : {})} : null
      }) : null
      self.postMessage({ type:'result', landmarks:hand, timestamp:data.timestamp, frameSeq:data.frameSeq,
        inferenceMs,decodeMs,inputWidth:bitmap.width,inputHeight:bitmap.height })
    } catch (e) { self.postMessage({type:'error', message:e.message || String(e)}) }
    finally { bitmap?.close(); if (ownsBusy) busy = false }
  }
}
