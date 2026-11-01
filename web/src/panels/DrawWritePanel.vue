<script setup>
import { defineAsyncComponent, ref } from 'vue'
import TaskTabs from '../components/TaskTabs.vue'

/* 绘制与写字合并为一个工作区：同一套笔、范围、预览、执行与进度，
   只把“图形 / 文字”作为二级栏内的模式切换。 */
const DrawingTask = defineAsyncComponent(() => import('../components/DrawingTask.vue'))
const TextWritingTask = defineAsyncComponent(() => import('../components/TextWritingTask.vue'))
const tab = ref('drawing')
const tabs = [{ id: 'drawing', label: '图形' }, { id: 'writing', label: '文字' }]
</script>

<template>
  <div class="col draw-write-panel">
    <TaskTabs v-model="tab" :tabs="tabs" id="drawwrite" label="绘制与写字" />
    <KeepAlive>
      <DrawingTask v-if="tab === 'drawing'" />
    </KeepAlive>
    <TextWritingTask v-if="tab === 'writing'" />
  </div>
</template>

<style scoped>
.draw-write-panel { min-width: 0; }
</style>
