// One draft per stable object ID; telemetry updates never replace typed text.
export function createTextDraft({state,save,maxLength=64}) {
  state.entries={};state.key=null
  const entry=()=>state.entries[state.key]
  function sync(key,value){
    state.key=key
    if(key==null)return
    const current=state.entries[key]
    if(!current)state.entries[key]={text:String(value),base:String(value),focused:false,saving:false,error:''}
    else if(current.awaiting){
      if(String(value)===current.awaiting){current.awaiting=null;current.error=''}
      else if(Date.now()>current.deadline)current.error='名称保存已应答，但回读未确认；请核对后重试'
    }
    else if(!current.focused && !current.saving && current.text===current.base){current.text=String(value);current.base=String(value)}
  }
  function cancel(value){const current=entry();if(!current||current.saving)return;current.text=String(value);current.base=String(value);current.error='';current.focused=false;current.awaiting=null}
  async function commit(latest){
    const key=state.key,current=entry()
    if(!current||current.saving)return false
    current.focused=false
    if(current.text===current.base)return true
    const text=current.text.trim()
    if(!text||text.length>maxLength){current.error=`名称需要1～${maxLength}个字符`;return false}
    if(String(latest)!==current.base){current.error='名称已在别处修改，请取消后核对；草稿已保留';return false}
    current.saving=true;current.error=''
    try{
      const response=await save(key,text)
      if(response?.ok!==true)throw new Error(response?.why||'保存失败')
      current.text=text;current.base=text;current.awaiting=text;current.deadline=Date.now()+3000
      return true
    }catch(error){current.error=error.message;return false}
    finally{current.saving=false}
  }
  return {sync,cancel,commit,entry}
}
