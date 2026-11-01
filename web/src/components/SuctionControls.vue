<script setup>
import { computed, ref, watch } from 'vue'
import { useWorld } from '../store'
import { suctionFeedback, suctionBlockReason, validateSuctionMapping } from '../suctionControls.js'
import { toolActions } from '../shared/toolActions.ts'
const props = defineProps({ configuration: Boolean })
const w = useWorld(), message = ref(''), failed = ref(false)
const sides = [{id:'left',name:'左侧'},{id:'right',name:'右侧'}]
const channels = [{id:'vacuum',name:'吸气'},{id:'blow',name:'放气'}]
const pending = ref({})
const actions = toolActions.filter(action => action.toolTypes.includes('suction'))
const feedback = computed(() => Object.fromEntries(sides.map(side => [side.id,suctionFeedback(w,side.id)])))
const label = value => value === true ? '开' : value === false ? '关' : '未知'
const reason = (side,action) => pending.value[side] ? '正在处理该侧动作' : suctionBlockReason(w,side,action)
const toggleAction = (side,channel) => `${channel}_${feedback.value[side][channel] === true ? 'off' : 'on'}`
const toggleLabel = (side,channel) => `${feedback.value[side][channel] === true ? '停止' : ''}${channel === 'vacuum' ? '吸气' : '放气'}`
const toggleReason = (side,channel) => {
  const blocked = reason(side,toggleAction(side,channel))
  if (blocked) return blocked
  return feedback.value[side][channel] === null ? '气路状态未知，等待有效回读' : ''
}
async function toggle(side,channel) {
  if (!['vacuum','blow'].includes(channel) || !['left','right'].includes(side) || toggleReason(side,channel)) return
  await act(side,toggleAction(side,channel))
}
async function act(side, action) {
  if (reason(side,action)) return
  if (w.snap?.mode === 'real' && !window.confirm(`${side==='left'?'左侧':'右侧'}${actions.find(item=>item.id===action)?.zh || action}将操作实际气路，确认现场安全？`)) return
  message.value = ''; failed.value = false
  pending.value[side] = action
  try {
    const response = await w.toolAction(action,0,side)
    if (!response?.ok) throw Error(response?.why || '动作未完成')
    message.value = response.sim ? '仿真动作已执行' : '请求已处理，请核对有效回读；不代表已吸牢或释放'
  } catch(error) { failed.value = true; message.value = error.message }
  finally { delete pending.value[side] }
}
const aliases = ref({}), draft = ref(null), original = ref(null), boundTool = ref(null), loading = ref(false), saving = ref(false)
const outputs = computed(() => Object.entries(aliases.value).filter(([,value]) => value.direction==='output' && value.confirmed_by_user===true))
const dirty = computed(() => draft.value && JSON.stringify(draft.value)!==JSON.stringify(original.value))
const mappingIssue = computed(() => {
  if (!boundTool.value || boundTool.value.id!==w.tools?.tool?.id || boundTool.value.revision!==w.tools?.tool?.revision) return '工具档案已变化，请重新载入'
  return validateSuctionMapping(draft.value,aliases.value)
})
function loadProfile() {
  if (dirty.value && !window.confirm('放弃尚未保存的吸盘映射？')) return
  const tool = w.tools?.tool
  boundTool.value = tool ? JSON.parse(JSON.stringify(tool)) : null
  draft.value = Object.fromEntries(sides.map(side=>[side.id,Object.fromEntries(channels.map(channel=>[channel.id,{alias:tool?.suction_io?.sides?.[side.id]?.[channel.id]?.alias || '',on:tool?.suction_io?.sides?.[side.id]?.[channel.id]?.on ?? 1}]))]))
  original.value = JSON.parse(JSON.stringify(draft.value))
}
watch(() => [w.tools?.tool?.id,w.tools?.tool?.revision],()=>{ if(!dirty.value)loadProfile() },{immediate:true})
async function loadAliases() {
  if (loading.value) return
  loading.value=true;failed.value=false
  try {
    const response=await fetch('/api/calibrations'),body=await response.json()
    if(!response.ok || body.ok===false)throw Error(body.why || '读取输出别名失败')
    aliases.value=body.io || {};message.value=outputs.value.length?'输出别名已更新':'暂无已标定输出，请先在 IO 页添加别名'
  } catch(error) { failed.value=true;message.value=error.message }
  finally { loading.value=false }
}
async function save() {
  if (saving.value || mappingIssue.value) return
  saving.value=true;failed.value=false
  try {
    const response=await w.call('/api/tools/profile',{...boundTool.value,suction_io:{version:1,sides:JSON.parse(JSON.stringify(draft.value))}})
    if(!response?.ok)throw Error(response?.why || '档案保存失败')
    await w.refreshTools();original.value=JSON.parse(JSON.stringify(draft.value));loadProfile();message.value='映射已保存，尚未确认实际 IO'
  } catch(error) { failed.value=true;message.value=error.message }
  finally { saving.value=false }
}
async function confirmMapping() {
  if (saving.value || dirty.value || mappingIssue.value) return
  if(!window.confirm('确认四路别名、接线和有效电平已人工核对？此次仅核对档案与连接，不试输出。'))return
  saving.value=true;failed.value=false
  try {
    const response=await w.call('/api/tools/io-confirm',{tool_id:w.tools.tool.id,revision:w.tools.tool.revision})
    if(!response?.ok)throw Error(response?.why || 'IO 确认失败')
    await w.refreshTools();message.value='IO 档案已确认，未试输出'
  } catch(error) { failed.value=true;message.value=error.message }
  finally { saving.value=false }
}
</script>
<template>
  <section v-if="w.tools?.tool?.type==='suction'" class="panel suction-controls">
    <div class="panel-hd"><span class="panel-title">双吸盘</span><span class="panel-sub">{{ w.snap?.mode==='sim'?'仅仿真':'实际气路' }}</span></div>
    <div class="panel-bd">
      <div class="suction-sides">
        <div v-for="side in sides" :key="side.id" class="suction-side">
          <b>{{side.name}}</b>
          <span class="tiny">吸气 {{label(feedback[side.id].vacuum)}} · 放气 {{label(feedback[side.id].blow)}}</span>
          <span class="tiny dim">持件：{{feedback[side.id].holding || '未确认'}}</span>
          <div class="row"><button v-for="channel in channels" :key="channel.id" class="btn" :class="{'btn--primary':feedback[side.id][channel.id]===true}" :disabled="!!toggleReason(side.id,channel.id)" :title="toggleReason(side.id,channel.id)" :aria-label="side.name+toggleLabel(side.id,channel.id)" @click="toggle(side.id,channel.id)">{{toggleLabel(side.id,channel.id)}}</button></div>
          <p v-if="toggleReason(side.id,'vacuum') || toggleReason(side.id,'blow')" class="tiny dim">{{toggleReason(side.id,'vacuum') || toggleReason(side.id,'blow')}}</p>
        </div>
      </div>
      <details v-if="configuration"><summary>气路映射</summary>
        <div class="col mapping">
          <div class="row"><button class="btn btn--xs" :disabled="loading" @click="loadAliases">{{loading?'读取中…':'读取已标定输出'}}</button><button class="btn btn--xs" :disabled="saving" @click="loadProfile">重新载入档案</button></div>
          <template v-if="draft"><fieldset v-for="side in sides" :key="side.id" :disabled="saving"><legend>{{side.name}}</legend>
            <div v-for="channel in channels" :key="channel.id" class="mapping-row"><label>{{channel.name}}<select v-model="draft[side.id][channel.id].alias" class="select"><option value="">选择输出别名</option><option v-for="[name,entry] in outputs" :key="name" :value="name">{{name}}（{{entry.group==='end'?'末端':'电箱'}} DO{{entry.bit+1}}）</option></select></label><label>有效电平<select v-model.number="draft[side.id][channel.id].on" class="select"><option :value="0">低电平</option><option :value="1">高电平</option></select></label></div>
          </fieldset></template>
          <p v-if="mappingIssue" class="tiny dim">{{mappingIssue}}</p>
          <div class="row"><button class="btn" :disabled="saving || !dirty || !!mappingIssue" @click="save">保存映射</button><button class="btn" :disabled="saving || dirty || !!mappingIssue" @click="confirmMapping">确认 IO 档案</button></div>
        </div>
      </details>
      <p v-if="message" class="note tiny" :class="{bad:failed}" role="status">{{message}}</p>
    </div>
  </section>
</template>
<style scoped>
.suction-sides{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.suction-side{display:flex;flex-direction:column;gap:8px;min-width:0;border:1px solid var(--line);padding:8px}.suction-side p{margin:0}.suction-side span{overflow-wrap:anywhere}.suction-side .row{flex-wrap:wrap}.mapping{padding-top:8px}.mapping fieldset{min-width:0;border:1px solid var(--line);padding:8px}.mapping-row{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:8px}.mapping label{display:flex;flex-direction:column;gap:4px;font-size:11px;min-width:0}.mapping select{max-width:100%}.suction-side summary{font-size:11px;cursor:pointer}
</style>
