// Camera access belongs only to our two top-level production viewers.
function createCameraPermission({ getOrigin, getViewers, trace = () => {} }) {
  function sameOrigin(value) {
    try { return new URL(value).origin === getOrigin() } catch { return false }
  }
  function owns(web, url, mainFrame = true) {
    if (!web || web.isDestroyed() || !mainFrame || !getViewers().includes(web)) return false
    try {
      const expected = new URL(getOrigin()), actual = new URL(url)
      return expected.protocol === 'http:' && expected.hostname === '127.0.0.1'
        && actual.origin === expected.origin && ['/', '/index.html', '/vision.html'].includes(actual.pathname)
        && new URL(web.getURL()).origin === expected.origin
    } catch { return false }
  }
  return {
    check(web, permission, requestingOrigin, details = {}) {
      const granted = permission === 'media' && details.mediaType === 'video'
        && owns(web, details.requestingUrl || requestingOrigin, details.isMainFrame)
        && (!details.embeddingOrigin || sameOrigin(details.embeddingOrigin))
        && (!details.securityOrigin || sameOrigin(details.securityOrigin))
      trace({phase:'check',permission,requestingOrigin,details,granted})
      return granted
    },
    request(web, permission, callback, details = {}) {
      const videoOnly = Array.isArray(details.mediaTypes) && details.mediaTypes.length > 0
        && details.mediaTypes.every(type => type === 'video')
      const granted = permission === 'media' && videoOnly && owns(web, details.requestingUrl, details.isMainFrame)
        && (!details.securityOrigin || sameOrigin(details.securityOrigin))
      trace({phase:'request',permission,details,granted})
      callback(granted)
    },
  }
}
module.exports = { createCameraPermission }
