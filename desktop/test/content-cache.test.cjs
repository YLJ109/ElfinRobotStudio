const test=require('node:test'),assert=require('node:assert/strict')
const {clearContentCache}=require('../contentCache.cjs')
test('startup drops old HTTP and service-worker content while preserving project preferences and drafts',async()=>{
 const calls=[]
 await clearContentCache({async clearCache(){calls.push('http')},async clearStorageData(options){calls.push(options)}})
 assert.deepEqual(calls,['http',{storages:['serviceworkers','cachestorage']}])
 assert.ok(!calls[1].storages.includes('localstorage'))
})
test('cache reset failure propagates to startup rather than claiming the new version is ready',async()=>{
 await assert.rejects(clearContentCache({async clearCache(){throw Error('cache locked')}}),/cache locked/)
})
