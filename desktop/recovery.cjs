const {selectData}=require('./dataSelection.cjs')
async function activateRecovery({backend,userData,copyId,relaunch,fetcher=fetch,select=selectData}){
  if(!backend?.owned || !backend.base)throw Error('当前后端由其他窗口启动，不能切换数据目录')
  if(typeof copyId!=='string' || !/^[a-f0-9]{32}$/.test(copyId))throw Error('恢复副本标识无效')
  const response=await fetcher(backend.base+'/api/backup/activation-plan',{method:'POST',headers:{'content-type':'application/json','x-elfin-desktop-key':backend.key},body:JSON.stringify({copy_id:copyId}),signal:AbortSignal.timeout(10000)})
  const plan=await response.json()
  if(!response.ok || plan.ok!==true || plan.copy_id!==copyId || plan.requires_restart!==true)throw Error(plan.why||plan.detail||'恢复副本检查失败')
  // A stop request alone does not permit switching. BackendSession.close must
  // confirm settled and owned-process exit before the pointer can change.
  await backend.close()
  select(userData,plan.data_dir,copyId,plan.manifest_digest)
  relaunch()
  return {ok:true}
}
module.exports={activateRecovery}
