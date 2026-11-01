<script setup>
import {computed,watch} from 'vue'
import {useWorld} from '../store'
import TaskTabs from '../components/TaskTabs.vue'
import SimPanel from './SimPanel.vue'
import PowerPanel from './PowerPanel.vue'
const w=useWorld()
const tabs=computed(()=>[{id:'run',label:'运行'},{id:'connection',label:'连接'},{id:'kinematics',label:'运动学'},...(w.snap?.mode==='real'?[{id:'control',label:'使能控制'}]:[]),{id:'diagnostics',label:'诊断'}])
const tab=computed({get:()=>w.controlTab || 'run',set:value=>{w.controlTab=value}})
watch(tabs,items=>{if(!items.some(item=>item.id===tab.value))tab.value='run'},{immediate:true,flush:'sync'})
const driveVisible=computed(()=>['connection','control','diagnostics'].includes(tab.value))
</script>
<template>
  <div class="col control-panel">
    <TaskTabs v-model="tab" :tabs="tabs" id="control" label="控制分类" />
    <div id="control-panel" class="col" role="tabpanel" :aria-labelledby="`control-tab-${tab}`">
      <SimPanel v-show="!driveVisible" :section="tab==='kinematics'?'kinematics':'run'" />
      <PowerPanel v-show="driveVisible" :section="driveVisible?tab:'connection'" />
    </div>
  </div>
</template>
