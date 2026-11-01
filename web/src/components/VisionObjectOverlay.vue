<script setup>
import { computed } from 'vue'
import { layoutVisionLabels } from '../visionLabelLayout.js'
import { COLOR_NAMES,SHAPE_NAMES } from '../visionWorkspace.js'
const props=defineProps({objects:{type:Array,default:()=>[]},width:{type:Number,required:true},height:{type:Number,required:true},detailed:Boolean})
const labels=computed(()=>layoutVisionLabels(props.objects,props.width,props.height,object=>props.detailed?`${object.id} · ${COLOR_NAMES[object.color] || ''} ${SHAPE_NAMES[object.shape] || ''}`:object.id))
</script>
<template>
  <g v-for="object in objects" :key="object.id" :class="{held:object.status!=='observed'}"><title>{{object.id}} · {{COLOR_NAMES[object.color]}} {{SHAPE_NAMES[object.shape]}}</title><polygon class="vision-contour" :points="object.contour.map(point=>point.join(',')).join(' ')"/><circle class="vision-center" :cx="object.center_px[0]" :cy="object.center_px[1]" r="4"/></g>
  <g v-for="label in labels" :key="'label-'+label.id" class="vision-label"><line :x1="label.cx" :y1="label.cy" :x2="label.lineX" :y2="label.lineY"/><rect :x="label.x" :y="label.y" :width="label.width" :height="label.height"/><text :x="label.x+3" :y="label.textY" :font-size="label.font">{{label.text}}</text></g>
</template>
<style scoped>.vision-contour,.vision-center{fill:none;stroke:var(--acc);stroke-width:2;vector-effect:non-scaling-stroke}.held{opacity:.4;stroke-dasharray:5 4}.vision-label line{stroke:#62dcec;stroke-width:1}.vision-label rect{fill:rgba(5,12,18,.85);stroke:none}.vision-label text{fill:#8aebf5;stroke:none!important;paint-order:normal;font-family:system-ui,sans-serif}</style>
