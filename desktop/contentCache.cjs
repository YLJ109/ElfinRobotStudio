async function clearContentCache(session) {
  await session.clearCache()
  // Preserve local preferences, editor drafts, cookies and all project data.
  await session.clearStorageData({storages:['serviceworkers','cachestorage']})
}
module.exports={clearContentCache}
