<script setup>
import { ref, reactive, computed, defineAsyncComponent, onBeforeUnmount } from 'vue'
import TaskTabs from '../components/TaskTabs.vue'
import CameraSettings from '../components/CameraSettings.vue'
import { visionWorkspace as v, visionCall, COLOR_NAMES, SHAPE_NAMES } from '../visionWorkspace.js'
import { readMarkerFile } from '../visionMarkers.js'
import { HEAD_CAMERA_MOUNT,headCameraSettings } from '../virtualCameraMount.js'
import { useWorld } from '../store'
const world=useWorld()
const realMode=computed(()=>(world.snap?.mode || world.mode)==='real')
const MarkerPanel=defineAsyncComponent(()=>import('./VisionMarkerPanel.vue'))
const tab=ref('camera'),tabs=[{id:'camera',label:'摄像头'},{id:'objects',label:'工件识别'},{id:'position',label:'尺寸定位'},{id:'calibration',label:'标定'},{id:'recipe',label:'方案'}]
const busy=ref(false),message=ref(''),fileInput=ref(null),markerTools=ref(false),selectedPoint=ref(0)
const rows=reactive(Array.from({length:6},()=>['','','','']))
const planeZ=ref(0),maxError=ref(2),height=ref(20),minArea=ref(200),roi=ref([.02,.02,.96,.96])
const isStation=location.pathname.endsWith('/vision.html')
const virtualDraft=reactive({...JSON.parse(JSON.stringify(HEAD_CAMERA_MOUNT)),fov:55,width:640,fps:15})
function loadVirtual(){if(v.config?.virtual)Object.assign(virtualDraft,JSON.parse(JSON.stringify(headCameraSettings(v.config.virtual))))}
function applyVirtual(){return change({virtual:JSON.parse(JSON.stringify(virtualDraft))})}
function openStation(){if(window.elfinDesktop?.openVisionWorkspace)window.elfinDesktop.openVisionWorkspace();else message.value='独立视觉窗口请在桌面端打开。'}
const result=computed(()=>v.result),objects=computed(()=>result.value?.objects || [])
const imageSrc=computed(()=>result.value?'data:'+result.value.image.mime+';base64,'+result.value.image.base64:'')
const preview=computed(()=>result.value?`0 0 ${result.value.image.width} ${result.value.image.height}`:'0 0 640 480')
let request=null,disposed=false
async function run(action){
  if(busy.value)return
  busy.value=true;message.value='';request=new AbortController()
  try{await action(request.signal)}catch(error){if(!disposed && error.name!=='AbortError')message.value=error.message}
  finally{if(!disposed)busy.value=false;request=null}
}
function change(patch){return run(signal=>visionCall('config',patch,signal))}
function toggleColor(color,checked){const colors=(v.config?.colors || []).filter(value=>value!==color);if(checked)colors.push(color);return change({colors})}
function upload(event){const file=event.target.files?.[0];event.target.value='';if(!file)return;return run(async signal=>{await visionCall('image',{image_base64:await readMarkerFile(file,signal)},signal);message.value='图片已导入，结果仅用于本张图像。'})}
function pickPoint(event){
  if(tab.value!=='calibration' || !result.value)return
  const svg=event.currentTarget,point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY
  const matrix=svg.getScreenCTM();if(!matrix)return
  const pixel=point.matrixTransform(matrix.inverse())
  if(pixel.x<0 || pixel.y<0 || pixel.x>=result.value.image.width || pixel.y>=result.value.image.height)return
  rows[selectedPoint.value][0]=Math.round(pixel.x*100)/100;rows[selectedPoint.value][1]=Math.round(pixel.y*100)/100
  selectedPoint.value=Math.min(5,selectedPoint.value+1)
}
function calibrate(){return run(async signal=>{
  if(rows.some(row=>row.some(value=>value==='' || !Number.isFinite(Number(value)))))throw new Error('请填写全部6组对应点')
  const response=await visionCall('calibrate',{points:rows.map(row=>row.map(Number)),plane_z:Number(planeZ.value),max_error_mm:Number(maxError.value)},signal)
  message.value='复核通过：'+response.calibration.check_errors_mm.map(value=>value.toFixed(2)+'mm').join(' / ')
})}
function loadConfig(){height.value=v.config?.height_mm ?? 20;minArea.value=v.config?.min_area ?? 200;roi.value=(v.config?.roi || [.02,.02,.96,.96]).slice()}
onBeforeUnmount(()=>{disposed=true;request?.abort()})
const f=value=>Number.isFinite(value)?value.toFixed(1):'—'
</script>
<template>
  <div class="col vision-workspace">
    <button v-if="!isStation" class="btn btn--primary" @click="openStation">打开视觉工作窗口</button>
    <TaskTabs v-model="tab" :tabs="tabs" id="industrial-vision" label="视觉分类" />
    <div id="industrial-vision-panel" class="col" role="tabpanel" :aria-labelledby="`industrial-vision-tab-${tab}`">
      <template v-if="tab==='camera'">
        <CameraSettings />
        <section v-if="!realMode && v.config?.input==='virtual'" class="panel"><div class="panel-hd"><span class="panel-title">末端安装与成像</span><span class="tag">仅仿真</span></div><div class="panel-bd">
          <button class="btn btn--xs" @click="loadVirtual">读取当前参数</button>
          <label v-for="(name,index) in ['X','Y','Z']" :key="name" class="row">法兰 {{ name }}/mm<input v-model.number="virtualDraft.offset_mm[index]" class="input grow" type="number" min="-1000" max="1000" /></label>
          <label v-for="(name,index) in ['X','Y','Z']" :key="'dir'+name" class="row">朝向 {{ name }}<input v-model.number="virtualDraft.direction[index]" class="input grow" type="number" min="-10000" max="10000" /></label>
          <label class="row">视场角/°<input v-model.number="virtualDraft.fov" class="input grow" type="number" min="20" max="100" /></label><label class="row">分辨率<select v-model.number="virtualDraft.width" class="select grow"><option :value="320">320×240</option><option :value="640">640×480</option><option :value="960">960×720</option></select></label>
          <label class="row">采样fps<input v-model.number="virtualDraft.fps" class="input grow" type="number" min="1" max="30" /></label><button class="btn btn--primary" :disabled="busy" @click="applyVirtual">应用安装与成像</button>
          <p class="dim tiny">位置和朝向以法兰坐标为准。末端移动会使当前平面标定失效，防止套用旧视角的坐标；工件从「场景」添加。</p>
        </div></section>
      </template>
      <section v-if="tab!=='camera'" class="panel">
        <div class="panel-hd"><span class="panel-title">{{ tabs.find(item=>item.id===tab)?.label }}</span><span class="tag" :class="result?.calibrated?'tag--ok':'tag--warn'">{{ result?.calibrated?'平面标定有效':'像素结果' }}</span></div>
        <div class="panel-bd">
          <div class="row"><button class="btn" :disabled="busy" @click="tab='camera'">选择摄像头来源</button><button class="btn" :disabled="busy" @click="fileInput?.click()">导入图片</button><input ref="fileInput" type="file" accept="image/png,image/jpeg" hidden @change="upload" /></div>
          <div v-if="result" class="vision-image">
            <svg :viewBox="preview" role="img" aria-label="同帧工件轮廓及标定取点" @pointerdown="pickPoint">
              <image :href="imageSrc" :width="result.image.width" :height="result.image.height" />
              <g v-for="object in objects" :key="object.id" :class="{held:object.status!=='observed'}"><polygon :points="object.contour.map(point=>point.join(',')).join(' ')" /><circle :cx="object.center_px[0]" :cy="object.center_px[1]" r="4" /><text :x="object.center_px[0]+6" :y="object.center_px[1]-8" :font-size="Math.max(12,result.image.width/45)">{{ object.id }}</text></g>
              <g v-if="tab==='calibration'"><template v-for="(row,index) in rows" :key="index"><circle v-if="row[0]!=='' && row[1]!==''" :cx="row[0]" :cy="row[1]" r="6" class="cal-point" /></template></g>
            </svg>
          </div>
          <p v-else class="dim tiny">开启彩色相机后自动识别，或导入 JPEG / PNG（≤1MiB）离线调试。</p>
          <p v-if="result" class="dim tiny">{{ result.source==='camera'?'真实相机':result.source==='virtual'?'末端仿真相机':'离线图片' }} · 帧 {{ result.frame_seq }} · {{ result.image.width }}×{{ result.image.height }} · CPU {{ f(result.elapsed_ms) }}ms{{ v.stale?' · 结果过期':'' }}</p>
          <template v-if="tab==='objects'">
            <div class="color-options"><label v-for="(label,color) in COLOR_NAMES" :key="color" class="sw"><input type="checkbox" :checked="v.config?.colors.includes(color)" :disabled="busy" @change="toggleColor(color,$event.target.checked)" />{{ label }}</label></div>
            <label class="row">最小面积<input v-model.number="minArea" class="input grow" type="number" min="25" @focus="minArea=v.config?.min_area || 200" /><span>px²</span><button class="btn btn--xs" :disabled="busy" @click="change({min_area:Number(minArea)})">应用</button></label>
            <details @toggle="$event.target.open && loadConfig()"><summary>检测区域 ROI</summary><div class="roi-inputs"><label v-for="(name,index) in ['X','Y','宽','高']" :key="name">{{ name }}<input v-model.number="roi[index]" class="input" type="number" min="0" max="1" step="0.01" /></label></div><button class="btn btn--xs" :disabled="busy" @click="change({roi:roi.map(Number)})">应用区域</button></details>
          </template>
          <template v-if="tab==='position'">
            <label class="sw"><input type="checkbox" :checked="v.config?.mapping" :disabled="busy || !result?.calibrated" @change="change({mapping:$event.target.checked})" />映射到三维仿真</label>
            <label class="row">代理高度<input v-model.number="height" class="input grow" type="number" min="1" max="1000" @focus="height=v.config?.height_mm || 20" /><span>mm</span><button class="btn btn--xs" :disabled="busy" @click="change({height_mm:Number(height)})">应用</button></label>
            <p class="note">XY与长宽为同一水平面的投影估测；高度由你指定，未测量。识别代理不参与机器人运动、碰撞许可或抓取执行。</p>
          </template>
          <template v-if="tab==='calibration'">
            <p class="dim tiny">固定相机。先选点号，再点击图像填写像素XY；手工输入实测基座XY。前4点用于拟合，后2点独立复核，不能重复拟合点。</p>
            <div class="cal-table"><table><thead><tr><th>点</th><th>像素X</th><th>像素Y</th><th>基座X/mm</th><th>基座Y/mm</th></tr></thead><tbody><tr v-for="(row,index) in rows" :key="index" :class="{selected:selectedPoint===index}"><td><button class="btn btn--xs" :aria-pressed="selectedPoint===index" @click="selectedPoint=index">{{ index+1 }}{{ index>=4?'验':'' }}</button></td><td v-for="(_,column) in row" :key="column"><input v-model.number="row[column]" class="input mono" type="number" :aria-label="`点${index+1}字段${column+1}`" /></td></tr></tbody></table></div>
            <label class="row">平面Z/mm<input v-model.number="planeZ" class="input grow" type="number" min="0" max="3000" /></label><label class="row">复核上限/mm<input v-model.number="maxError" class="input grow" type="number" min="0.1" max="10" step="0.1" /></label>
            <button class="btn btn--primary" :disabled="busy || !result" @click="calibrate">计算并复核标定</button>
            <p class="dim tiny">只在标定四边形内映射；更换相机会话、分辨率或重启后重新标定。相机位置改变也必须重做；本批不包含畸变校正与手眼标定。</p>
            <details @toggle="markerTools=$event.target.open"><summary>ArUco辅助工具</summary><MarkerPanel v-if="markerTools" /></details>
          </template>
          <template v-if="tab==='recipe'">
            <label class="sw"><input type="checkbox" :checked="v.config?.enabled" :disabled="busy" @change="change({enabled:$event.target.checked})" />开启相机后自动执行当前方案</label>
            <p class="dim tiny">当前颜色、ROI、面积和代理高度自动保存为一个默认方案；标定不跨重启静默恢复。多方案库将在后续增加。</p>
            <div class="row"><button class="btn" :disabled="busy" @click="change({enabled:false,mapping:false})">暂停识别与映射</button></div>
          </template>
          <p v-if="message || v.error" class="note" role="status">{{ message || v.error }}</p>
        </div>
      </section>
      <section v-if="tab==='objects' || tab==='position'" class="panel"><div class="panel-hd"><span class="panel-title">工件结果</span><span class="tag">{{ objects.length }} 个</span></div><div class="panel-bd flush"><div class="tscroll"><table><thead><tr><th>目标</th><th>颜色/形状</th><th>{{ tab==='position'?'位置/尺寸':'中心像素' }}</th></tr></thead><tbody><tr v-for="object in objects" :key="object.id"><td>{{ object.id }}<small class="dim">{{ object.status==='holding'?' 暂失':object.status==='expired'?' 过期':'' }}</small></td><td>{{ COLOR_NAMES[object.color] }}<br />{{ SHAPE_NAMES[object.shape] }}</td><td class="mono tiny"><template v-if="tab==='position' && object.mapped">XY {{ object.mapped.xyz_mm.slice(0,2).map(f).join(', ') }} mm<br />{{ object.mapped.size_mm.slice(0,2).map(f).join(' × ') }} mm</template><template v-else>{{ object.center_px.map(f).join(', ') }} px<br />{{ object.size_px.map(f).join(' × ') }} px</template></td></tr><tr v-if="!objects.length"><td colspan="3" class="dim tiny">暂无符合当前颜色和区域条件的工件。</td></tr></tbody></table></div></div></section>
    </div>
  </div>
</template>
<style scoped>
.vision-workspace{min-width:0}.vision-image{border:1px solid var(--line);background:var(--bg-inset)}.vision-image svg{display:block;width:100%;max-height:300px;touch-action:none}.vision-image polygon,.vision-image circle{fill:none;stroke:var(--acc);stroke-width:2;vector-effect:non-scaling-stroke}.vision-image text{fill:var(--acc);paint-order:stroke;stroke:#000;stroke-width:2px}.vision-image .held{opacity:.4;stroke-dasharray:5 4}.vision-image .cal-point{stroke:#ffcf65}.color-options{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;font-size:11px}.cal-table{overflow:auto}.cal-table input{min-width:50px;width:100%;font-size:10px;padding:4px}.cal-table td{padding:3px}.cal-table .selected{background:var(--acc-soft)}.roi-inputs{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}.roi-inputs input{width:100%;min-width:0}
</style>
