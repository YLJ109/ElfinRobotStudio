<script setup>
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
const w = useWorld(), tab = ref('path'), busy = ref(false), message = ref(''), failed = ref(false)
const target = ref([0,0,0,0,0,0]), inspection = ref(null), samples = ref(''), name = ref('工位1'), method = ref('frame'), fit = ref(null)
const registry = ref({ frames:{}, tools:{}, io:{} }), selectedFrame = ref(''), selectedTool = ref(''), localTarget = ref('0,0,0,0,0,0')
const { tools } = storeToRefs(w)
const toolChoice = ref(''), toolMessage = ref('')
const toolActionBusy = ref(false)
const ioName = ref('到位信号'), group = ref('end'), direction = ref('input'), bit = ref(0), confirmed = ref(false), level = ref(0)
const diagnostics = ref(null), cursor = ref(0), quality = ref(localStorage.getItem('huayan.modelQuality') || 'full')
const frames = computed(() => diagnostics.value?.frames || []), frame = computed(() => frames.value[cursor.value])
async function task(fn) { busy.value=true; failed.value=false; message.value=''; try { await fn() } catch(e) { failed.value=true; message.value=e.message } finally { busy.value=false } }
async function post(url, body) { const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const v=await r.json(); if(!r.ok || v.ok===false) throw new Error(v.why || v.detail?.[0]?.msg || '请求失败'); return v }
async function refresh() { registry.value=await (await fetch('/api/calibrations')).json() }
onMounted(() => { target.value=[...(w.preview || w.snap?.joints.q || target.value)]; task(refresh) })
function captureTarget() { target.value=[...(w.preview || w.snap?.joints.q || target.value)]; inspection.value=null }
function inspect() { task(async()=> { inspection.value=await post('/api/plans/inspect',{joints:target.value.map(Number)}); window.__elfinScene?.setPlanPath(inspection.value.report.tcp_path); message.value='轨迹检查完成，请核对现场。' }) }
function execute() { task(async()=> { const r=await post('/api/plans/execute',{plan_id:inspection.value.plan_id}); inspection.value=null; message.value=r.warn || '作业已提交，完成以实时回读为准。' }) }
function capturePose() { if(!w.snap?.joints.known) { message.value='姿态未知，无法采样'; failed.value=true; return } const p=w.snap.real?.pcs_base || [...w.snap.tcp.xyz_mm,...w.snap.tcp.rpy_deg]; samples.value+=(samples.value.trim()?'\n':'')+p.slice(0,method.value==='frame'?3:6).join(',') }
function calibrate(save=false) { task(async()=> { const rows=samples.value.trim().split('\n').map(r=>r.split(/[,，\s]+/).map(Number)); fit.value=await post(`/api/calibrations/${method.value==='frame'?'frame':'tcp'}`,{name:name.value,save,[method.value==='frame'?'points':'poses']:rows}); await refresh(); message.value=save?'已保存标定，旧轨迹检查已失效。':'计算完成，请核对后保存。' }) }
function resolveTarget() { task(async()=> { const r=await post('/api/calibrations/target',{frame:selectedFrame.value,tool:selectedTool.value || null,target:localTarget.value.split(/[,，\s]+/).map(Number)}); w.setPreview(r.joints); target.value=r.joints; tab.value='path'; inspection.value=null; message.value=r.warn || '已载入关节预览，请检查轨迹。' }) }
function saveIO() { task(async()=> { await post('/api/calibrations/io',{name:ioName.value,group:group.value,direction:direction.value,bit:Number(bit.value),confirmed:confirmed.value}); await refresh(); message.value='别名已保存。' }) }
function simulate() { task(async()=> { await post('/api/sim/input',{group:group.value,bit:Number(bit.value),val:Number(level.value)}); message.value='虚拟输入已更新，没有向硬件写入。' }) }
function loadDiagnostics() { task(async()=> { diagnostics.value=await (await fetch('/api/blackbox')).json(); cursor.value=Math.max(0,frames.value.length-1); message.value=`读取 ${frames.value.length} 帧、${diagnostics.value.events.length} 条事件。` }) }
function seek() { if(frame.value?.valid) w.setPlaybackGhost(frame.value.joints) }
function exportDiagnostics() { const url=URL.createObjectURL(new Blob([JSON.stringify(diagnostics.value,null,2)],{type:'application/json'})); const a=document.createElement('a'); a.href=url; a.download='机器人诊断回放.json'; a.click(); URL.revokeObjectURL(url) }
function changeQuality() { localStorage.setItem('huayan.modelQuality',quality.value); location.reload() }
const toolActions = computed(() => {
  const type = tools.value.tool?.type
  if (type === 'parallel_gripper') return [['open','打开'],['close','关闭']]
  if (type === 'suction') return [['vacuum_on','吸附'],['vacuum_off','释放']]
  return []
})
async function runToolAction(action) {
  toolActionBusy.value = true
  try { const r = await w.toolAction(action); toolMessage.value = r.sim ? `仿真动作已执行：${action}（未向硬件发送）` : `工具动作已执行：${action}` }
  catch (e) { toolMessage.value = e.message }
  finally { toolActionBusy.value = false }
}
</script>

<template>
  <div class="engineering col">
    <nav class="actions" aria-label="工程工具"><button v-for="[k,label] in [['path','轨迹'],['calibration','标定'],['io','IO'],['tooling','工具'],['diagnostics','诊断']]" :key="k" class="btn" :class="{'btn--primary':tab===k}" @click="tab=k">{{label}}</button></nav>
    <p v-if="message" :class="['feedback',{bad:failed}]" role="status">{{message}}</p>
    <section v-if="tab==='path'" class="tool">
      <h3>轨迹检查</h3>
      <div class="angles"><label v-for="(_,i) in target" :key="i">J{{i+1}}（°）<input v-model.number="target[i]" type="number" step="0.1" @input="inspection=null" /></label></div>
      <div class="actions"><button class="btn" @click="captureTarget">取残影／当前位置</button><button class="btn btn--primary" :disabled="busy || !w.snap?.joints.known" @click="inspect">{{busy?'检查中…':'检查轨迹'}}</button></div>
      <template v-if="inspection"><dl><dt>执行策略</dt><dd>{{inspection.report.strategy==='relj'?'逐轴分段':'协调运动（待验证）'}}</dd><dt>估计耗时</dt><dd>{{inspection.report.duration_estimate_s}} s</dd><dt>近似最小间隙</dt><dd>{{inspection.report.min_clearance_mm ?? '--'}} mm</dd><dt>限位占用</dt><dd>{{inspection.report.max_limit_pct}}%</dd><dt>雅可比条件估计</dt><dd>{{inspection.report.condition_estimate}}</dd></dl>
        <p :class="{bad:inspection.report.risk_detected}">{{inspection.report.risk_detected?`发现 ${inspection.report.issues.length} 个风险采样点`:'本次采样未发现几何风险'}}</p>
        <button class="btn btn--primary" :disabled="busy || !w.snap?.motion_granted" @click="execute">人工确认，执行已检查目标</button></template>
    </section>
    <section v-else-if="tab==='calibration'" class="tool">
      <h3>坐标与工具标定</h3><label>方式<select v-model="method" @change="samples='';fit=null"><option value="frame">工位三点：原点、X方向、XY平面</option><option value="tcp">TCP平移：至少四个姿态</option></select></label>
      <label>名称<input v-model="name" maxlength="64" /></label><p class="dim">{{method==='frame'?'每行 X,Y,Z（毫米）。':'保持工具尖端固定，改变姿态；每行参考点 X,Y,Z,A,B,C（毫米／度）。工具方向另行标定。'}}</p>
      <label>采样数据<textarea v-model="samples" rows="5" spellcheck="false" /></label><div class="actions"><button class="btn" :disabled="!w.snap?.joints.known" @click="capturePose">采集当前参考点</button><button class="btn" :disabled="busy" @click="calibrate(false)">计算</button><button class="btn btn--primary" :disabled="busy || !fit" @click="calibrate(true)">计算并保存</button></div>
      <template v-if="fit"><p v-if="fit.tcp_offset_mm">TCP偏移：{{fit.tcp_offset_mm.map(v=>v.toFixed(3)).join(', ')}} mm</p><p v-if="fit.rms_mm!==undefined">RMS {{fit.rms_mm.toFixed(3)}} mm；最大残差 {{fit.max_mm.toFixed(3)}} mm</p><p class="dim">{{fit.note}}</p></template>
      <h3>工位目标预览</h3><label>工位<select v-model="selectedFrame"><option value="">请选择</option><option v-for="(_,k) in registry.frames" :key="k">{{k}}</option></select></label><label>工具<select v-model="selectedTool"><option value="">控制器参考点</option><option v-for="(_,k) in registry.tools" :key="k">{{k}}</option></select></label>
      <label>工位内 X,Y,Z,A,B,C<input v-model="localTarget" /></label><button class="btn" :disabled="busy || !selectedFrame" @click="resolveTarget">反解并载入预览</button>
    </section>
    <section v-else-if="tab==='io'" class="tool"><h3>IO别名与握手</h3><label>别名<input v-model="ioName" maxlength="64" /></label>
      <div class="angles"><label>分组<select v-model="group"><option value="end">末端</option><option value="box">电箱</option></select></label><label>方向<select v-model="direction"><option value="input">输入</option><option value="output">输出</option></select></label></div>
      <label>协议通道（从0开始）<input v-model.number="bit" type="number" min="0" max="7" /></label><label class="check"><input v-model="confirmed" type="checkbox" />已核对接线；仿真别名仅用于演练</label><button class="btn" :disabled="busy || !confirmed" @click="saveIO">保存别名</button>
      <p class="dim">程序示例：WAIT_DI 到位信号 1 TIMEOUT 3 DEBOUNCE 0.1</p><template v-if="w.mode==='sim'"><label>虚拟输入<select v-model.number="level"><option :value="0">0</option><option :value="1">1</option></select></label><button class="btn" :disabled="busy" @click="simulate">更新仿真输入</button></template>
      <dl><template v-for="(c,k) in registry.io" :key="k"><dt>{{k}}</dt><dd>{{c.group}} {{c.direction}} [{{c.bit}}]</dd></template></dl>
    </section>
    <section v-else-if="tab==='tooling'" class="tool">
      <h3>J6 末端工具</h3>
      <div class="tool-state" :class="tools.mounted?.state || 'unknown'">
        <span class="state-dot"></span><b>{{ tools.mounted?.state || 'unknown' }}</b>
        <span class="dim">{{ tools.tool?.name || '未确认实际工具' }}</span>
      </div>
      <dl><dt>来源</dt><dd>{{ tools.mounted?.source || '--' }}</dd><dt>工具版本</dt><dd>{{ tools.tool?.revision || '--' }}</dd><dt>TCP</dt><dd>{{ tools.tool?.tcp?.name || '--' }}</dd><dt>安装修订</dt><dd>{{ tools.mounted?.revision ?? '--' }}</dd></dl>
      <label>选择实际安装工具<select v-model="toolChoice"><option value="">请选择</option><option v-for="t in tools.items" :key="t.id" :value="t.id">{{t.name}} · {{t.type}}</option></select></label>
      <div class="actions"><button class="btn btn--primary" :disabled="busy || !toolChoice" @click="task(async()=>{ const r=await w.selectTool(toolChoice,'manual',null,'用户确认实际已安装'); toolMessage.value=r.ok?'已确认工具；请重新检查轨迹':'切换失败：'+(r.why||'未知原因') })">确认实际已安装</button><button class="btn" :disabled="busy" @click="task(async()=>{ const r=await w.clearTool(); toolMessage.value=r.ok?'已确认法兰无工具':'操作失败：'+(r.why||'未知原因') })">确认无工具</button><button class="btn" :disabled="busy" @click="task(async()=>{ await w.refreshTools(); toolMessage.value='已读取当前工具状态（只读）' })">读取状态</button></div>
      <div v-if="toolActions.length" class="tool-actions"><b>动作测试</b><span class="dim">仿真只改变虚拟状态；真机必须先完成 IO 别名映射。</span><div class="actions"><button v-for="[action,label] in toolActions" :key="action" class="btn btn--primary" :disabled="busy || toolActionBusy" @click="runToolAction(action)">{{label}}</button></div><p class="dim">当前状态：{{tools.mounted?.action_state || 'idle'}} · 最后动作：{{tools.mounted?.last_action?.action || '--'}}</p></div>
      <p v-if="toolMessage" class="feedback" role="status">{{toolMessage}}</p>
      <div class="tool-card" v-for="t in tools.items" :key="t.id"><b>{{t.name}}</b><span class="dim">{{t.type}} · TCP {{t.tcp?.translation_mm?.join(', ') || '--'}} mm · 负载 {{t.payload?.mass_kg ?? '未知'}} kg</span></div>
    </section>
    <section v-else class="tool"><h3>故障黑匣子</h3><div class="actions"><button class="btn" :disabled="busy" @click="loadDiagnostics">读取回放</button><button class="btn" :disabled="!diagnostics" @click="exportDiagnostics">导出诊断包</button></div>
      <label v-if="frames.length">回放帧<input v-model.number="cursor" type="range" min="0" :max="frames.length-1" @input="seek" /></label><template v-if="frame"><p>{{new Date(frame.t*1000).toLocaleTimeString()}} · {{frame.valid?'有效姿态':'数据失效'}}</p><p>FSM {{frame.fsm ?? '--'}} · 故障码 {{frame.err_code ?? '--'}} · 步骤 {{frame.exec.completed_index}}/{{frame.exec.total}}</p></template>
      <button class="btn" @click="w.setPlaybackGhost(null)">收回回放假手</button><div v-if="diagnostics" class="events"><p v-for="row in diagnostics.events.slice(-20)" :key="row.seq">{{row.text}}</p></div>
      <label>模型质量<select v-model="quality" @change="changeQuality"><option value="full">完整模型</option><option value="low">轻量模型（刷新后生效）</option></select></label>
    </section>
  </div>
</template>

<style scoped>
.engineering,.tool{display:flex;flex-direction:column;gap:8px;min-width:0}.actions{display:flex;gap:6px;flex-wrap:wrap}.tool h3,.tool p,.feedback{margin:0}.tool h3{font-size:13px;color:var(--tx-1)}.tool p,.feedback{font-size:12px;line-height:1.5;overflow-wrap:anywhere}.tool label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--tx-2)}.tool input,.tool textarea,.tool select{min-width:0;width:100%;box-sizing:border-box;background:var(--bg-inset);color:var(--tx-1);border:1px solid var(--line);border-radius:5px;padding:6px;font:inherit}.angles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.tool .check{flex-direction:row;align-items:center}.check input{width:auto}dl{display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;margin:0}dd{margin:0;text-align:right;overflow-wrap:anywhere}dt{color:var(--tx-3)}.events{max-height:180px;overflow:auto;display:flex;flex-direction:column;gap:4px}.tool-state{display:flex;align-items:center;gap:7px;padding:8px;border:1px solid var(--line-2);border-radius:6px;background:var(--bg-inset)}.tool-state.detected_verified{border-color:var(--ok)}.tool-state.conflict,.tool-state.unknown{border-color:var(--warn)}.state-dot{width:8px;height:8px;border-radius:50%;background:var(--warn)}.tool-state.detected_verified .state-dot{background:var(--ok)}.tool-card{display:flex;justify-content:space-between;gap:8px;padding:7px 8px;border:1px solid var(--line);border-radius:5px;background:rgba(255,255,255,.02);font-size:12px}.tool-card span{overflow-wrap:anywhere;text-align:right}:focus-visible{outline:2px solid var(--acc);outline-offset:2px}.tool-actions{display:flex;flex-direction:column;gap:6px;padding:8px;border:1px solid var(--line-2);border-radius:6px;background:rgba(26,199,222,.04)}
</style>
