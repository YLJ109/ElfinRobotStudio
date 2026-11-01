<script setup>
import {ref,onMounted,onBeforeUnmount} from 'vue'
import {parseResponse} from '../connectionState.js'
import {displayNumericText,displayNumericJson} from '../numberDisplay.js'
const busy=ref(false),message=ref(''),preview=ref(null),restored=ref(null),input=ref(null)
const labels={points:'点位',objects:'当前场景',scenes:'现场文件',tools:'工具档案',io:'IO映射',settings:'显示与仿真设置',programs:'程序',assets:'三维资源'}
const fieldNames={name:'名称',xyz:'位置',size:'尺寸',rotation_deg:'旋转',scale:'缩放',locked:'锁定',color:'颜色',surface:'材质',src:'程序内容',value:'内容',settings:'设置',objects:'物体',metadata:'资源信息',gravity:'重力',floating:'悬浮',tcp:'工具坐标',suction_io:'吸盘接线'}
function fieldSummary(fields){return fields.map(field=>field.split('.').map(part=>fieldNames[part] || part).join(' / ')).join('、')}
function displayValue(value){
  if(typeof value==='string')try{const parsed=JSON.parse(value);if(parsed && typeof parsed==='object')value=parsed}catch{}
  if(value && typeof value==='object' && Number.isInteger(value.first_changed_line) && Number.isInteger(value.lines) && typeof value.text==='string')return displayNumericText(`第${value.first_changed_line}行起 · ${value.lines}行\n${value.text}`)
  return value && typeof value==='object' ? displayNumericJson(value) : displayNumericText(value)
}
let request=null,generation=0
const desktop=globalThis.elfinDesktop
const recoveryState=ref({canActivate:false,reason:''})
onMounted(async()=>{if(desktop?.recoveryState)try{recoveryState.value=await desktop.recoveryState()}catch(error){message.value=error.message}})
async function run(work){
  if(busy.value)return
  busy.value=true;message.value='';const current=++generation,controller=new AbortController();request=controller
  const timer=setTimeout(()=>controller.abort(),15000)
  try{await work(controller.signal,current)}catch(error){if(current===generation)message.value=error.name==='AbortError'?'操作已取消或超时，请重试':error.message}
  finally{clearTimeout(timer);if(current===generation){busy.value=false;request=null}}
}
async function api(route,body,signal){return parseResponse(await fetch('/api/backup/'+route,{cache:'no-store',signal,...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}),'/api/backup/'+route)}
function download(){return run(async(signal,current)=>{
  const value=await api('export',undefined,signal);if(current!==generation)return
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}))
  const link=document.createElement('a');link.href=url;link.download='工作站数据备份.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  message.value='备份已导出'
})}
function inspect(event){const file=event.target.files?.[0];event.target.value='';preview.value=null;restored.value=null;if(!file)return
  return run(async(signal,current)=>{if(file.size>32*1024*1024)throw Error('备份不能超过32MiB');const value=JSON.parse(await file.text());const result=await api('preview',value,signal);if(current===generation)preview.value=result})
}
function restore(){return run(async(signal,current)=>{const value=await api('restore-copy',{token:preview.value.token},signal);if(current!==generation)return;restored.value=value;preview.value=null;message.value='恢复副本已创建，当前工作区未替换'})}
async function activate(){if(busy.value||!restored.value)return;if(!confirm('停止当前仿真并重新启动到恢复副本？工具安装、IO和真机点位仍需重新确认。'))return
  busy.value=true;message.value='正在停止并重启…'
  try{await desktop.useRecovery(restored.value.copy_id)}catch(error){message.value=error.message;busy.value=false}
}
function cancel(){generation++;request?.abort();request=null;busy.value=false;preview.value=null;message.value='已取消'}
onBeforeUnmount(()=>{generation++;request?.abort()})
</script>
<template>
  <section class="panel backup-panel"><div class="panel-hd"><span class="panel-title">数据备份与恢复</span></div><div class="panel-bd col">
    <p class="tiny dim">备份点位、场景、程序、工具与IO档案。凭据、设备资格和运行中任务不导出。</p>
    <div class="row"><button class="btn" :disabled="busy" @click="download">导出数据备份</button><button class="btn" :disabled="busy" @click="input.click()">选择恢复文件</button><button v-if="busy||preview" class="btn" @click="cancel">取消</button></div>
    <input ref="input" type="file" accept=".json,application/json" hidden @change="inspect" />
    <template v-if="preview"><table class="backup-diff"><thead><tr><th>数据</th><th>当前</th><th>导入</th><th>变化</th></tr></thead><tbody><tr v-for="row in preview.changes" :key="row.section"><td>{{labels[row.section]}}</td><td>{{row.before}}</td><td>{{row.after}}</td><td>{{row.changed_count ?? (row.changed?'有':'无')}}</td></tr></tbody></table>
      <details v-for="section in preview.changes.filter(row=>row.changed)" :key="section.section"><summary>{{labels[section.section] || section.section}}差异</summary><div v-for="row in section.rows" :key="row.id" class="backup-change"><b>{{row.id}} · {{({added:'新增',removed:'移除',changed:'修改'})[row.change]}}</b><span class="tiny dim" :title="row.fields.join('、')">{{fieldSummary(row.fields)}}</span><dl class="backup-values"><dt>当前</dt><dd><pre>{{displayValue(row.before)}}</pre></dd><dt>导入</dt><dd><pre>{{displayValue(row.after)}}</pre></dd></dl></div><p v-if="section.truncated" class="tiny dim">变化较多，此处只展示部分摘要；完整内容以所选备份文件为准。</p></details>
      <p class="tiny dim">导入为独立恢复副本；工具安装、IO接线与真机点位来源需重新确认。</p><button class="btn btn--primary" :disabled="busy" @click="restore">确认创建恢复副本</button>
    </template>
    <template v-if="restored"><button v-if="desktop?.useRecovery" class="btn btn--primary" :disabled="busy||!recoveryState.canActivate" @click="activate">使用恢复副本并重启</button><p v-if="recoveryState.reason" class="tiny dim">{{recoveryState.reason}}</p><div v-if="!desktop?.useRecovery||!recoveryState.canActivate" class="tiny dim">恢复目录：<span class="mono">{{restored.data_dir}}</span><br />退出后将 ELFIN_DATA_DIR 指向此目录启动，可重新打开副本。</div></template>
    <p v-if="message" class="note" role="status">{{message}}</p>
  </div></section>
</template>
<style scoped>.backup-panel p{margin:0;line-height:1.5}.backup-panel .panel-bd{gap:10px}.backup-diff{width:100%;table-layout:fixed;font-size:12px;text-align:right;border-collapse:collapse}.backup-diff td,.backup-diff th{padding:5px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.backup-diff td:first-child,.backup-diff th:first-child{width:40%;text-align:left}.backup-change{display:flex;flex-direction:column;gap:3px;border-bottom:1px solid var(--line);padding:7px 0;min-width:0;overflow-wrap:anywhere}.backup-values{display:grid;grid-template-columns:30px minmax(0,1fr);gap:4px;margin:3px 0;font-size:11px}.backup-values dt{color:var(--tx-3)}.backup-values dd{margin:0;min-width:0}.backup-change pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;margin:0}</style>
