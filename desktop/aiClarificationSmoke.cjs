// Real assistant UI, local rules only. Never clicks execution or changes IO.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
async function verifyAiClarification({window,directory,report,wait}){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  const result=report.aiClarification={completed:false,steps:[],executed:false},evaluate=code=>window.webContents.executeJavaScript(code)
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms))
  const request=(url,body)=>evaluate(`(async()=>{const r=await fetch(${JSON.stringify(url)},{cache:'no-store',...(${JSON.stringify(body)}===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(body)})})});return {status:r.status,value:await r.json()}})()`)
  async function button(label,selector='.ai'){
    await evaluate(`(()=>{const b=[...document.querySelectorAll(${JSON.stringify(selector+' button')})].find(b=>b.textContent.trim()===${JSON.stringify(label)});if(!b||b.disabled)throw Error('AI按钮不可用：'+${JSON.stringify(label)});b.click()})()`)
  }
  async function send(text){
    const count=await evaluate(`document.querySelectorAll('.ai .msg.assistant').length`)
    await evaluate(`(()=>{const e=document.querySelector('.ai .composer textarea');if(!e||e.disabled)throw Error('AI输入不可用');e.value=${JSON.stringify(text)};e.dispatchEvent(new Event('input',{bubbles:true}))})()`)
    await button('发送','.ai .composer')
    await wait(()=>evaluate(`(()=>{const messages=[...document.querySelectorAll('.ai .msg.assistant')];return messages.length>${count}&&!messages.at(-1).textContent.includes('思考中…')&&!document.querySelector('.ai .composer textarea')?.disabled})()`))
  }
  const ticketScope=t=>{assert.match(t.id,/^[A-Za-z0-9_-]+$/);assert.ok(Number.isInteger(t.revision));return `.clarification-ticket[data-clarification-id="${t.id}"][data-revision="${t.revision}"]`}
  const ticket=()=>evaluate(`(()=>{const e=[...document.querySelectorAll('.clarification-ticket')].at(-1);return e?{id:e.dataset.clarificationId,revision:Number(e.dataset.revision),request_id:e.dataset.requestId,expires_at:Number(e.dataset.expiresAt),text:e.textContent}:null})()`)
  async function capture(name){await pause(100);fs.writeFileSync(path.join(directory,name),(await window.webContents.capturePage()).toPNG())}
  const before=(await request('/api/state',null)).value
  assert.equal(before.mode,'sim');assert.equal(before.real,null);assert.notEqual(before.exec.state,'running')
  await evaluate(`document.querySelector('nav button[title="AI"]').click()`)
  await wait(()=>evaluate(`!!document.querySelector('.ai .composer')`))
  assert.equal(await evaluate(`!!document.querySelector('[data-ai-model-consent]:checked')`),false)
  await send('速度10%，等待多久')
  await wait(()=>evaluate(`!!document.querySelector('.clarification-ticket')`))
  const first=await ticket();assert.equal(first.revision,1);assert.ok(first.id);assert.match(first.text,/待补充/)
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(ticketScope(first))}).closest('.bub').querySelector('.ai-source').dataset.sourceKind`),'local_rules')
  result.steps.push({kind:'clarify',source_kind:'local_rules',...first})
  await button('补充本次要求',ticketScope(first))
  await send('等待0.2秒')
  await wait(()=>evaluate(`!!document.querySelector('.message-plan[data-plan-id]')`))
  const plan=await evaluate(`(()=>{const e=[...document.querySelectorAll('.message-plan')].at(-1);return {plan_id:e.dataset.planId,request_id:e.dataset.requestId,code:e.querySelector('code').textContent,confirmationChecked:!!e.querySelector('input:checked')}})()`)
  assert.equal(await evaluate(`document.querySelector('.message-plan[data-plan-id]').closest('.bub').querySelector('.ai-source').textContent.trim()`),'本地规则 · 待确认草稿')
  assert.equal(await evaluate(`!!document.querySelector('[data-ai-model-consent]:checked')`),false)
  assert.ok(plan.plan_id);assert.notEqual(plan.request_id,first.request_id);assert.match(plan.code,/SPEED 10/);assert.match(plan.code,/WAIT 0.2/);assert.equal(plan.confirmationChecked,false)
  result.steps.push({kind:'new_complete_plan',source_kind:'local_rules',...plan,executed:false})
  await capture('ai-clarification.png')
  assert.match(plan.plan_id,/^[A-Za-z0-9_-]+$/);await button('丢弃',`.message-plan[data-plan-id="${plan.plan_id}"]`)
  const old=(await request('/api/ai/chat',{text:'确认',request_id:'smoke-ai-old-pending',clarification:{id:first.id,revision:first.revision}})).value
  assert.equal(old.ok,false);assert.ok(!old.plan_id);result.steps.push({kind:'consumed_pending_rejected',result:old})
  await send('等待多久');const cancelled=await ticket()
  await button('取消澄清',ticketScope(cancelled))
  await wait(()=>evaluate(`document.querySelector('.ai').textContent.includes('本次计划或澄清已取消')`))
  const denied=(await request('/api/ai/chat',{text:'等待0.2秒',request_id:'smoke-ai-cancelled',clarification:{id:cancelled.id,revision:cancelled.revision}})).value
  assert.equal(denied.ok,false);assert.ok(!denied.plan_id);result.steps.push({kind:'cancelled_pending_rejected',result:denied})
  await send('等待多久');const expiring=await ticket();assert.ok(expiring.expires_at>Date.now())
  // Exercise the actual production TTL. No shortened clock or test-only flag.
  await pause(Math.max(0,expiring.expires_at-Date.now())+300)
  await wait(()=>evaluate(`document.querySelector('.ai').textContent.includes('澄清已过期')`))
  const expired=(await request('/api/ai/chat',{text:'等待0.2秒',request_id:'smoke-ai-expired',clarification:{id:expiring.id,revision:expiring.revision}})).value
  assert.equal(expired.ok,false);assert.ok(!expired.plan_id);result.steps.push({kind:'production_ttl_expired_rejected',result:expired})
  await evaluate(`document.querySelector(${JSON.stringify(ticketScope(expiring))}).scrollIntoView({block:'end'})`)
  await wait(()=>evaluate(`(()=>{const ticket=document.querySelector(${JSON.stringify(ticketScope(expiring))}),r=ticket.getBoundingClientRect(),messages=ticket.closest('.messages')?.getBoundingClientRect();return ticket.textContent.includes('澄清已过期')&&r.top>=0&&r.bottom<=innerHeight&&(!messages||r.bottom<=messages.bottom)})()`))
  await capture('ai-clarification-expired.png')
  const after=(await request('/api/state',null)).value
  assert.equal(after.exec.state,before.exec.state);assert.deepEqual(after.joints.q,before.joints.q);assert.equal(after.real,null)
  result.completed=true
}
module.exports={verifyAiClarification}
