const test=require('node:test'),assert=require('node:assert/strict')
const {waitForPrecheck}=require('../robotPlaySmoke.cjs')
test('precheck driver waits for the real enabled projection and keeps the 15-second deadline',async()=>{
 let reads=0;const statuses=[{available:false,title:'',guide:'尚未完成标定'},{available:true,title:'可预检',guide:'当前识别有效'}]
 const result=await waitForPrecheck(async()=>statuses[reads++],async(check,label,seconds)=>{assert.equal(seconds,15);assert.match(label,/正式预检/);assert.equal(await check(),false);assert.equal(await check(),true)})
 assert.equal(reads,2);assert.equal(result.available,true)
})
test('timeout preserves actual button title and repair guide instead of bypassing disabled',async()=>{
 await assert.rejects(waitForPrecheck(async()=>({available:false,title:'禁止预检',guide:'视觉结果已过期，请重新采集'}),async check=>{assert.equal(await check(),false);throw Error('等待超时')}),error=>error.message.includes('禁止预检')&&error.message.includes('视觉结果已过期')&&error.message.includes('等待超时'))
})
