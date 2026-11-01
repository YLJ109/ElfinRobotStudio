const test=require('node:test'),assert=require('node:assert/strict')
const {manualArrived}=require('../renewalSmoke.cjs')
const target=[1,2,3,4,5,6],binding={epoch:4,started:123.4}
const state=()=>({joints:{q:[...target]},moving:false,exec:{state:'aborted',error:'previous program stopped',execution:{...binding,state:'running',busy:true,error:''}},target_evidence:{plan_id:'this-plan',reached:false}})
test('manual acceptance ignores old Runner terminal and requires current same-plan settled proof',()=>{
 const s=state(),accepted={execution:binding}
 assert.equal(manualArrived(s,accepted,'this-plan',target),false)
 s.exec.execution.state='completed';s.exec.execution.busy=false
 assert.equal(manualArrived(s,accepted,'this-plan',target),false)
 s.target_evidence.reached=true
 assert.equal(manualArrived(s,accepted,'this-plan',target),true)
 s.target_evidence.plan_id='old-plan';assert.equal(manualArrived(s,accepted,'this-plan',target),false)
 s.target_evidence.plan_id='this-plan';s.moving=true;assert.equal(manualArrived(s,accepted,'this-plan',target),false)
 s.moving=false;s.joints.q[0]+=.2;assert.equal(manualArrived(s,accepted,'this-plan',target),false)
})
test('current manual failure, cancellation or replacement cannot pass arrival driver',()=>{
 for(const phase of ['failed','cancelled','aborted']){const s=state();s.exec.execution.state=phase;s.exec.execution.error='this motion failed';assert.throws(()=>manualArrived(s,{execution:binding},'this-plan',target),/this motion failed/)}
 for(const key of ['epoch','started']){const s=state();s.exec.execution[key]++;assert.throws(()=>manualArrived(s,{execution:binding},'this-plan',target),/替换或撤销/)}
})
