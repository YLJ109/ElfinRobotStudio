const test=require('node:test'),assert=require('node:assert/strict')
const {createCameraPermission}=require('../cameraPermission.cjs')
const origin='http://127.0.0.1:12345', web={isDestroyed:()=>false,getURL:()=>origin+'/'}
const permission=createCameraPermission({getOrigin:()=>origin,getViewers:()=>[web]})
function request(details={},kind='media',viewer=web){let answer;permission.request(viewer,kind,value=>answer=value,{requestingUrl:origin+'/',isMainFrame:true,securityOrigin:origin,mediaTypes:['video'],...details});return answer}
test('own top-level preview can request video, without authorizing audio or other capabilities',()=>{
 assert.equal(request(),true);assert.equal(request({mediaTypes:['audio']}),false)
 assert.equal(request({securityOrigin:origin+'/'}),true)
 assert.equal(request({mediaTypes:['video','audio']}),false);assert.equal(request({mediaTypes:[]}),false)
 assert.equal(request({},'display-capture'),false);assert.equal(request({},'geolocation'),false)
})
test('foreign windows, origins and subframes cannot inherit the camera permission',()=>{
 assert.equal(request({},'media',{...web}),false)
 assert.equal(request({requestingUrl:'https://example.test/'}),false)
 assert.equal(request({requestingUrl:origin+'/untrusted.html'}),false)
 assert.equal(request({securityOrigin:'http://127.0.0.1:9999'}),false)
 assert.equal(request({isMainFrame:false}),false)
})
test('video permission checks agree with requests; microphone and unknown media remain denied',()=>{
 const details={requestingUrl:origin+'/',securityOrigin:origin,mediaType:'video'}
 assert.equal(permission.check(web,'media',origin,details),true)
 assert.equal(permission.check(web,'media',origin+'/',{...details,securityOrigin:origin+'/',embeddingOrigin:origin+'/'}),true)
 assert.equal(permission.check(web,'media',origin,{...details,isMainFrame:false}),false)
 for(const mediaType of ['audio','unknown',undefined])assert.equal(permission.check(web,'media',origin,{...details,mediaType}),false)
 assert.equal(permission.check(web,'media',origin,{...details,embeddingOrigin:'https://example.test'}),false)
})
