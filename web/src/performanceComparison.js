// Explicit local sampling; values never contain credentials, logs or program text.
export const COMPARISON_LIMIT=120
export function performanceIdentity(s={},build={},viewport={}) {
  if(build.verified!==true || ![build.backend_sha256,build.frontend_sha256].every(v=>typeof v==='string' && /^[a-f0-9]{64}$/.test(v)))throw Error('缺少当前已验证版本，不能开始同版采样')
  if(typeof s.session_id!=='string' || !s.session_id || !s.tools?.mounted)throw Error('等待当前会话与工具状态后再采样')
  if(!['width','height','dpr'].every(k=>typeof viewport[k]==='number' && Number.isFinite(viewport[k]) && viewport[k]>0))throw Error('窗口尺寸信息不可用，未开始采样')
  const mounted=s.tools.mounted,profile=s.tools.tool
  return JSON.stringify({session_id:s.session_id,mode:s.mode,viewport:{width:viewport.width,height:viewport.height,dpr:viewport.dpr},
    tool:{mounted:Object.fromEntries(['tool_id','state','scope','source','revision'].map(k=>[k,mounted[k] ?? null])),
      profile:profile?Object.fromEntries(['id','revision','tcp','payload','envelope','visual'].map(k=>[k,profile[k] ?? null])):null,
      visual:s.tools.visual ?? null,sim_tool:s.settings?.sim_tool ?? null},
    build:{backend_sha256:build.backend_sha256,frontend_sha256:build.frontend_sha256},
    objects:(s.objects || []).map(o=>({id:o.id,kind:o.kind,xyz:o.xyz,size:o.size,rotation_deg:o.rotation_deg,locked:o.locked})).sort((a,b)=>String(a.id).localeCompare(String(b.id)))})
}
const finite=v=>typeof v==='number' && Number.isFinite(v) && v>=0?v:null
const clone=v=>JSON.parse(JSON.stringify(v))
export function createPerformanceComparison() {
  let current=null,before=null,after=null
  return {
    start(identity,now=Date.now()){after=null;current={identity,started_at:now,samples:[],truncated:false}},
    sample(identity,metrics,vision=null,now=Date.now()) {
      if(!current)return false
      if(identity!==current.identity){current=null;throw Error('场景、工具或版本已变化，请重新采样')}
      const sourceIdentity=JSON.stringify([vision?.source,vision?.session,vision?.epoch,vision?.enabled])
      if(current.sourceIdentity!==undefined && current.sourceIdentity!==sourceIdentity){current=null;throw Error('检测来源或配置已变化，请重新采样')}
      current.sourceIdentity=sourceIdentity
      const stamp=finite(vision?.result?.captured_at),server=finite(vision?.server_now),previous=current.samples.at(-1)
      const v={detector:'hand_landmarker_pipeline',submitted:finite(vision?.stats?.submitted),inferred:finite(vision?.stats?.inferred),replaced:finite(vision?.stats?.replaced),
        frame_age_ms:vision?.result?.clock_domain==='server' && stamp!==null && server!==null && server>=stamp?(server-stamp)*1000:null,input_hz:null,detection_hz:null,
        capture_hz:null,display_hz:null,gpu_percent:null}
      const dt=previous?(now-previous.time)/1000:0
      for(const [counter,rate] of [['submitted','input_hz'],['inferred','detection_hz']])if(dt>0 && v[counter]!==null && previous.vision[counter]!==null && v[counter]>=previous.vision[counter])v[rate]=(v[counter]-previous.vision[counter])/dt
      current.samples.push({time:now,metrics:clone(metrics),vision:v})
      if(current.samples.length>COMPARISON_LIMIT){current.samples.shift();current.truncated=true}return true
    },
    finish(slot,now=Date.now()) {
      if(!current?.samples.length)throw Error('尚未采集有效样本')
      if(!['before','after'].includes(slot))throw Error('采样位置无效')
      const record={...current,finished_at:now};current=null
      if(slot==='before'){before=record;after=null}else{if(!before || before.identity!==record.identity || before.sourceIdentity!==record.sourceIdentity)throw Error('前后场景或检测来源不一致，请重新采样');after=record}
      return record
    },
    stop(){current=null},
    export(){if(!before || !after)throw Error('请先保存同一场景的前后样本');return {version:1,before:clone(before),after:clone(after),
      unmeasured:['工件颜色轮廓检测速率','真实显示帧率','物理采集帧率','GPU利用率','进程CPU与内存'],note:'输入率与推理率仅为手掌检测进程计数，不代表工件检测或物理采集；CPU渲染提交不等于GPU显示。'}},
    get latest(){return current?.samples.at(-1)?.vision || after?.samples.at(-1)?.vision || before?.samples.at(-1)?.vision || null},
    get active(){return !!current},get before(){return before},get after(){return after}
  }
}
