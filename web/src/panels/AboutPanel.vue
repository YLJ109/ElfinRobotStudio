<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import ProgramRules from '../components/ProgramRules.vue'
import { OPERATION_NOTES } from '../operationNotes'
import TaskTabs from '../components/TaskTabs.vue'
import { parseResponse } from '../connectionState.js'
import { acceptanceView } from '../acceptanceView.js'

const w = useWorld()
const { meta, snap } = storeToRefs(w)
const tab = ref('overview')
const tabs = [{id:'overview',label:'概览'},{id:'guide',label:'指南 / 快捷键'},{id:'diagnostics',label:'字段 / 复核'},{id:'validation',label:'更新 / 验证'}]
const acceptance=ref(null),acceptanceError=ref(''),acceptanceLoading=ref(false)
const acceptanceStatus=computed(()=>acceptanceView(acceptance.value))
let acceptanceAbort=null
async function loadAcceptance(){
  acceptanceAbort?.abort();const request=new AbortController();acceptanceAbort=request;acceptanceLoading.value=true;acceptanceError.value=''
  try{const value=await parseResponse(await fetch('/api/acceptance',{cache:'no-store',signal:AbortSignal.any([request.signal,AbortSignal.timeout(10000)])}),'/api/acceptance');if(value?.ok!==true)throw Error(value?.why||'验收记录读取失败');if(!request.signal.aborted)acceptance.value=value}
  catch(error){if(!request.signal.aborted){acceptanceError.value=error.message;acceptance.value=null}}
  finally{if(acceptanceAbort===request){acceptanceLoading.value=false;acceptanceAbort=null}}
}
watch(tab,value=>{if(value==='validation')loadAcceptance();else{acceptanceAbort?.abort();acceptanceLoading.value=false}},{immediate:true})
onBeforeUnmount(()=>acceptanceAbort?.abort())
const fields = ref(null), onlyMiss = ref(false), fieldsError = ref(''), readBusy = ref('')
const frows = computed(() => (fields.value?.rows || []).filter(row => !onlyMiss.value || !row.got))
let fieldsAbort = null
watch(tab, async value => {
  fieldsAbort?.abort(); fieldsAbort = null
  if (value !== 'diagnostics') return
  const request = new AbortController(); fieldsAbort = request; fieldsError.value = ''
  try { const response = await parseResponse(await fetch('/api/fields', { signal:request.signal }), '/api/fields'); if (!request.signal.aborted) fields.value = response }
  catch(e) { if (!request.signal.aborted) fieldsError.value = e.message }
  finally { if (fieldsAbort === request) fieldsAbort = null }
})
onBeforeUnmount(() => fieldsAbort?.abort())
const cmds = computed(() => (meta.value ? Object.entries(meta.value.commands) : []))
const isReal = computed(() => snap.value && snap.value.mode === 'real')
const noteSections = [...new Set(OPERATION_NOTES.map(note => note.section))]

/* 只读指令复核：从驱动页挪过来的，用来对账 Data Sheet 推送 */
const READS = [
  ['IsSimulation', [], '对端是不是仿真'],
  ['ReadRobotModel', [], '控制器自报型号'],
  ['PackageVersion', [], '固件版本'],
  ['ReadCurFSM', [0], '状态机码'],
  ['ReadActPos', [0], '当前关节角 + 笛卡尔'],
  ['ReadOverride', [0], '速度比'],
  ['ReadBrakeStatus', [0], '抱闸（极性与 SDK 文档冲突）'],
  ['ReadEO', [0, 0, 1, 2], '末端 3 路输出'],
  ['ReadEI', [0, 0, 1, 2], '末端 3 路输入'],
  ['ReadEAI', [0, 0], '末端模拟量通道 1'],
  ['ReadBoxDI', [0, 1, 2, 3, 4, 5, 6, 7], '电箱数字输入'],
  ['ReadBoxDO', [0, 1, 2, 3, 4, 5, 6, 7], '电箱数字输出'],
]
const out = ref({})
async function read(name, params) {
  if (readBusy.value || !w.connected || !isReal.value || !snap.value?.real?.cmd_up) return
  readBusy.value = name
  out.value[name] = '…'
  try {
    const r = await w.askReal(name, params)
    out.value[name] = r.ok ? (r.fields?.join(', ') || 'OK') : `Fail ${r.err_code} ${r.hint || r.why || ''}`
  } catch(e) { out.value[name] = e.message }
  finally { readBusy.value = '' }
}
</script>

<template>
  <div class="col">
    <TaskTabs v-model="tab" :tabs="tabs" id="about" label="关于分类" storage-key="huayan.tabs.about.v1" />
    <div id="about-panel" class="col" role="tabpanel" :aria-labelledby="`about-tab-${tab}`" tabindex="0">
    <section v-if="tab === 'overview'" class="panel corner">
      <div class="panel-hd"><span class="panel-title">玖枢机器人工作站</span></div>
      <div class="panel-bd">
        <div class="p">玖枢（JiuShuRobotStudio）是华沿 Elfin E05（真机自报 <b class="mono acc">E05-1195_Pro</b>）的示教、仿真与监控工作站。
          三维模型来自华沿官方 ROS 包
          <span class="mono">huayan-robotics/elfin_robot → elfin_description/meshes/elfin5</span>
          的每轴 STL 与 URDF，用 <span class="mono">tools/build_robot_model.py</span> 转成前端层级，不是手工建模。</div>
        <div class="note">型号判定：机械说明书 ver3.0 写 E05 = 5kg / 臂展 800mm / 法兰 Ø156mm；
          官方 elfin5 网格底座实测 156.0mm、零位法兰高度 <b class="mono">1195.0mm</b> —— 正好是型号串里的 1195。</div>
        <div class="note">棋类 3D 国际象棋棋子使用 Khronos glTF Sample Assets「A Beautiful Game」
          <span class="mono">CC-BY-4.0</span>，裁剪与许可见
          <span class="mono">web/public/models/games/LICENSE.md</span>；围棋/五子棋/中国象棋/井字棋棋子为程序生成。</div>
        <div class="note">正运动学反验：两组真机采样（关节角 → PCS_Base）姿态差 0.001°、位置差 <b class="mono">0.09mm</b>（补 20mm 法兰偏置后）。本轮未复测，不能外推为全部姿态、工具与负载一致。</div>
      </div>
    </section>

    <section v-if="tab === 'overview'" class="panel">
      <div class="panel-hd"><span class="panel-title">项目概览</span></div>
      <div class="panel-bd">
        <div class="about-grid">
          <div><b>作者</b><span>伊木</span></div>
          <div><b>学校</b><span>福州理工学院</span></div>
          <div><b>用途</b><span>六轴工业机器人示教、仿真、程序编排与真机监控</span></div>
          <div><b>目标型号</b><span class="mono">Elfin E05-1195_Pro</span></div>
          <div><b>前端</b><span>Vue 3 · Three.js · CodeMirror</span></div>
          <div><b>后端</b><span>Python · FastAPI · WebSocket</span></div>
          <div><b>运动学</b><span>URDF · FK · IK · 笛卡尔插值</span></div>
          <div><b>运行方式</b><span>本地优先，仿真与真机通道隔离</span></div>
        </div>
        <details><summary>一次运动指令经过什么</summary>
          <p>输入目标 → 解析单位与坐标系 → 正/逆运动学求解 → 关节与工作空间检查 → 碰撞与围栏评估 → 仿真插值或控制器指令 → 等待到位 → 记录执行回执。</p>
        </details>
        <details><summary>仿真和真机的边界</summary>
          <p>仿真使用同一套模型、限位和安全评估逻辑，但不代表真实负载、摩擦、线缆、控制器内部规划和现场风险。真机通道只使用已验证的通信路径；未验证的指令会在界面中标记。</p>
        </details>
      </div>
    </section>

    <section v-if="tab === 'overview'" class="panel">
      <div class="panel-hd"><span class="panel-title">模块能力</span></div>
      <div class="panel-bd capability-list">
        <div><b>点位</b><span>关节角、笛卡尔 TCP、命名、保存、改名、残影和到点执行。</span></div>
        <div><b>程序</b><span>中英文指令、Python 风格变量/循环/判断/函数、解析、保存、暂停和回执。</span></div>
        <div><b>AI</b><span>自然语言生成可审阅计划，必须人工确认后才进入运动执行链。</span></div>
        <div><b>场景</b><span>多场景文件、物体属性、重力/落地、碰撞检测、导入导出。</span></div>
        <div><b>视觉</b><span>枚举普通 USB 摄像头和 RealSense 能力，支持彩色/深度流状态展示。</span></div>
        <div><b>工具</b><span>无工具、两指夹爪、吸盘模板，可扩展 TCP、负载和 IO 映射。</span></div>
      </div>
    </section>

    <section v-if="tab === 'guide'" class="panel">
      <div class="panel-hd"><span class="panel-title">操作指南</span></div>
      <div class="panel-bd">
        <ol class="steps">
          <li><b>仿真</b>：纯软件机器人，与真机零耦合；由「控制 · 连接」页决定当前驱动谁。</li>
          <li><b>点位</b>：滑块、± 和笛卡尔输入只调整预览；长按连续调整，松手停止调整。确认预览后点「执行目标」才移动机器人。</li>
          <li><b>目标与残影</b>：残影显示待执行目标，到达以有效后端反馈为准。已执行目标不能再次提交；修改模式、工具或连接后需要重新预览。</li>
          <li><b>程序执行</b>：编辑 → 解析 → 启动。右下角日志显示执行回执；修改代码或参数后需要重新解析。程序可命名保存、载入和删除。</li>
          <li><b>AI 执行</b>：整栏对话，自然语言生成计划，必须点"人工确认"才会动作。</li>
          <li><b>场景</b>：点击或拖放资源到地面，确认前可按 Esc 取消；物体支持改名、移动、旋转、缩放、锁定及共享撤销。静态 GLB 需检查单位、来源、许可和尺寸；现场可保存重开。</li>
          <li><b>真实监控</b>：在「控制 · 连接」填 IP 连接真机后三维与真机同步（本地 120ms 插值缓冲）。</li>
          <li><b>日志</b>：右下角常驻执行控制台 + 独立日志页，可导出 CSV。</li>
          <li><b>设置</b>：连接与真机项、显示参数、速度口径与两路原始速度、日志与回放。</li>
          <li><b>顶栏 / 底栏</b>：顶部保留速度与急停；就绪在「控制 · 连接」操作，底部汇总就绪、安全、链路、许可和摄像头状态。</li>
          <li><b>左侧栏</b>：导航底部可收起/展开工作面板；拖动面板右边缘调整宽度。收起后再次选择工作区会自动展开。</li>
          <li><b>摄像头</b>：普通浏览器预览需授权，默认不上传、不检测。工作站相机检测由后端采集，远程来源需配对；仿真画面在本地生成。关闭或换源不会自动恢复跟随。</li>
          <li><b>三维操作</b>：左键旋转、滚轮缩放、点击选择物体；1–7 按主导航顺序切页，F 切换 TCP 口径，R 复位视角。</li>
          <li><b>停止与预览</b>：Esc 在输入框和浮窗内也会请求软件停止；空格取消预览，在文字编辑和下拉选择时保留正常输入。软件停止不能替代物理急停。</li>
          <li><b>分类 Tab</b>：左右方向键、Home / End 移动焦点，Enter / 空格选择。分类选择只更改视图，不启动设备或动作；刷新后恢复上次分类。</li>
          <li><b>卡片折叠</b>：点任意卡片标题栏收起/展开（点按钮和输入框不会触发）。</li>
        </ol>
      </div>
    </section>

    <ProgramRules v-if="tab === 'guide'" />

    <section v-if="tab === 'guide'" class="panel">
      <div class="panel-hd"><span class="panel-title">功能说明与实现口径</span></div>
      <div class="panel-bd">
        <details v-for="section in noteSections" :key="section">
          <summary>{{ section }}</summary>
          <p v-for="note in OPERATION_NOTES.filter(item => item.section === section)" :key="note.text">{{ note.text }}</p>
        </details>
        <details><summary>工程与工具</summary>
          <p>轨迹检查只预演，不移动机器人；包络采样不能代替连续碰撞证明或硬件安全保护。更改现场、工具或标定后须重新检查轨迹。</p>
          <p>工具身份读不到时，可人工确认实际安装档案。仿真工具动作只更新虚拟状态；真机动作需要核对 IO 映射。黑匣子回放不会重放硬件命令。</p>
        </details>
      </div>
    </section>

    <section v-if="tab === 'validation'" class="panel">
      <div class="panel-hd"><span class="panel-title">安全与限制</span></div>
      <div class="panel-bd" v-if="meta">
        <ul class="li">
          <li>告警分级按"六轴最大 |当前角|/同向限位"：黄 {{ meta.safety.yellow_pct }}%、红 {{ meta.safety.red_pct }}%、
            告警 {{ meta.safety.alarm_pct }}%（含碰撞）。阈值是<b>本软件自设</b>，文档没有规定。</li>
          <li>碰撞用连杆<b>胶囊体近似</b>（半径取网格包围盒最短边一半 ×0.9），不是逐三角形：
            可能漏检细物，也可能对法兰附近偏保守。</li>
          <li class="warn">关节限位两套官方数据不一致：URDF 是 J3 ±149.5°、J5 ±146.7°；手册 p54 是 J3 ±153°、J5 ±180°。当前生效的是 URDF。</li>
          <li class="warn">关节速度也不一致：URDF 全轴 90°/s；手册 E05 表 J1-4 180°/s、J5-6 200°/s。在"设置"里选，选完仿真立刻按新值走。</li>
          <li class="warn">抱闸极性别信：真机<b>已使能</b>时 BrakeState 仍上报全 0，
            所以"0=抱闸吸合"（Data Sheet p272）与 SDK 的相反说法都没被证实，不能拿它判断掉轴。</li>
          <li>软件停止走统一停止与状态确认；ACK 不等于停稳，停止结果未知保持闭锁，<b>不能替代物理急停按钮</b>。</li>
          <li class="warn">这台机器人<b>没有摄像头</b>：三份官方 PDF 里"相机/摄像头/视觉/图像"命中数为 0，
            Data Sheet 十个顶层键无图像字段，554/8554/9000/8000 等视频端口全部关闭；
            8080 是"控制器监控诊断系统"（运维用，无视频流）。要看末端视觉只能外接相机。</li>
        </ul>
      </div>
    </section>

    <section class="panel" v-if="meta && tab === 'diagnostics'">
      <div class="panel-hd"><span class="panel-title">协议参考目录</span><span class="panel-sub">{{ cmds.length }} 条收录，页码为协议文档</span></div>
      <div class="panel-bd">
        <p class="note">协议收录不代表当前支持。软件接入、离线验证与当前资格分别核验；诊断写保护和运动门禁仍以当前操作入口为准。</p>
        <details>
          <summary class="dim tiny">展开指令表与参数</summary>
          <div class="tscroll">
            <table>
              <thead><tr><th>指令</th><th>参数</th><th class="w50">协议</th></tr></thead>
              <tbody>
                <tr v-for="[n, c] in cmds" :key="n">
                  <td class="mono">{{ n }}</td>
                  <td class="mono dim tiny">{{ c.params || '（无）' }}</td>
                  <td class="mono tiny">p{{ c.doc_page }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
        <div class="note">真机模式的点位执行只用<b>已实测验证的</b> MoveRelJ 逐轴逼近；绝对点 MoveJ 的报文格式未取证，不猜、不发。</div>
      </div>
    </section>

    <section v-if="tab === 'diagnostics'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">调试台</span>
        <span class="panel-sub">同步查询复核</span>
      </div>
      <div class="panel-bd">
        <template v-if="isReal">
          <div class="cmd" v-for="[n, p, why] in READS" :key="n">
            <button class="btn btn--xs" :disabled="!w.connected || !snap?.real?.cmd_up || !!readBusy" @click="read(n, p)">{{ n }}</button>
            <span class="dim tiny ellip">{{ why }}</span>
            <span class="grow"></span>
            <span class="mono tiny res ellip">{{ out[n] || '' }}</span>
          </div>
          <div class="note dim tiny">
            用于对账 20Hz 推送的 Data Sheet。<span class="mono">ReadCurFSM</span> 一类必须带
            <span class="mono">nRbtID</span>，漏了真机会回 20007。
          </div>
        </template>
        <div v-else class="note warn">仿真模式不下发只读指令；连接真机后才能复核。</div>
      </div>
    </section>

    <section class="panel" v-if="meta && tab === 'diagnostics'">
      <div class="panel-hd"><span class="panel-title">电源指令对照</span><span class="panel-sub">按钮背后发的是什么</span></div>
      <div class="panel-bd">
        <dl class="kv">
          <dt v-for="(v, k) in meta.power_actions" :key="k">{{ k }}</dt>
          <dd v-for="(v, k) in meta.power_actions" :key="'d' + k" class="tiny">{{ v }}</dd>
        </dl>
      </div>
    </section>

    <section v-if="tab === 'diagnostics'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">Data Sheet 字段覆盖</span>
        <span class="panel-sub mono">
          {{ fields ? fields.rows.length + ' 个字段 · ' + (fields.linked ? '按当前真机帧标注' : '未连真机，只列表') : '载入中…' }}
        </span>
        <span class="grow"></span>
        <label class="row nowrap no-fold" style="gap:4px">
          <input type="checkbox" v-model="onlyMiss" /><span class="tiny">只看没取到的</span>
        </label>
      </div>
      <div class="panel-bd flush">
        <div v-if="fieldsError" class="note warn" role="status">字段覆盖读取失败：{{ fieldsError }}；已有表格如存在为历史数据，重新进入此分类可重试。</div>
        <div class="tscroll">
          <table>
            <thead><tr><th>节</th><th>官方字段</th><th>快照键</th><th>说明</th><th class="w50">本帧</th></tr></thead>
            <tbody>
              <tr v-for="r in frows" :key="r.sec + r.field">
                <td class="mono tiny dim">{{ r.sec }}</td>
                <td class="mono tiny">{{ r.field }}</td>
                <td class="mono tiny acc">{{ r.key }}</td>
                <td class="tiny">{{ r.note }}</td>
                <td><span v-if="!fields || !fields.linked" class="tag tag--mute">--</span>
                    <span v-else class="tag" :class="r.got ? 'tag--ok' : 'tag--warn'">
                      {{ r.got ? '有' : '空' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="note tiny" style="margin:6px 10px">
          "空"不一定是 bug：未上电时温度/电流本来就没值。对齐规则写在
          <span class="mono">tools/check_datasheet_fields.py</span> ——
          帧里出现新字段而这张表没登记，测试直接失败。
        </div>
      </div>
    </section>

    <section v-if="tab === 'validation'" class="panel">
      <div class="panel-hd"><span class="panel-title">读数规则</span></div>
      <div class="panel-bd">
        <div class="p">未知一律显示 <b class="mono">--</b>，不填假设值；仿真数据永远带"仿真"徽标；
          超限只标红不把角度折回限位内；报错一定带错误码；汇总数值随筛选条件重算。</div>
      </div>
    </section>
    <section v-if="tab === 'validation'" class="panel">
      <div class="panel-hd"><span class="panel-title">更新记录与验收边界</span></div>
      <div class="panel-bd">
        <p class="p">仿真、离线检查、界面运行与现场资格分别验收。当前版本和证据以本次交付记录为准；历史采样不授予当前设备操作资格。</p>
        <p class="note">硬件待验证：实际停止距离、末端工具与负载、IO 接线需现场核验，不能由仿真或界面测试推定通过。</p>
        <button class="btn" :disabled="acceptanceLoading" @click="loadAcceptance">{{acceptanceLoading?'读取中…':'刷新验收记录'}}</button>
        <p v-if="acceptanceError" class="note" role="alert">{{acceptanceError}}</p>
        <template v-if="acceptance">
          <p class="note" role="status">{{!acceptanceStatus.hasEvidence?'当前没有绑定版本的验收记录，不能据此认定已通过。':acceptanceStatus.current?'以下证据与当前构建一致。':'以下为历史证据；当前版本的源码、离线与界面验收待核验。'}}</p>
          <dl class="acceptance-version"><dt>后端 SHA256</dt><dd>{{acceptance.build?.backend_sha256||'未提供'}}</dd><dt>前端 SHA256</dt><dd>{{acceptance.build?.frontend_sha256||'未提供'}}</dd></dl>
          <details v-if="acceptanceStatus.hasEvidence&&!acceptanceStatus.current"><summary>历史证据版本</summary><dl class="acceptance-version"><dt>后端 SHA256</dt><dd>{{acceptance.evidence.backend_sha256||'未提供'}}</dd><dt>前端 SHA256</dt><dd>{{acceptance.evidence.frontend_sha256||'未提供'}}</dd></dl></details>
          <div v-if="acceptanceStatus.rows.length" class="acceptance-features">
            <article v-for="feature in acceptanceStatus.rows" :key="feature.id" class="acceptance-feature">
              <strong>{{feature.id}} · {{feature.title}}</strong>
              <table><thead><tr><th>源码</th><th>离线</th><th>界面</th><th>现场</th></tr></thead><tbody><tr><td>{{feature.source}}</td><td>{{feature.offline}}</td><td>{{feature.ui}}</td><td>{{feature.field}}</td></tr></tbody></table>
              <p v-if="feature.history" class="dim tiny">历史：{{feature.history}}</p>
              <p v-if="feature.unverified.length" class="note">未验范围：{{feature.unverified.join('；')}}</p>
              <details v-if="feature.evidence.length"><summary>证据位置</summary><ul><li v-for="(item,index) in feature.evidence" :key="index">{{item}}</li></ul></details>
            </article>
          </div>
        </template>
      </div>
    </section>
    </div>
  </div>
</template>

<style scoped>
.program-example { font: 12px/1.7 var(--font-mono); background: var(--bg-inset); padding: 8px; overflow: auto; }
.acceptance-version{font-size:11px;display:grid;gap:4px}.acceptance-version dd{margin:0;overflow-wrap:anywhere;font-family:var(--font-mono)}.acceptance-features{display:grid;gap:12px}.acceptance-feature{border-top:1px solid var(--line-2);padding-top:8px;display:grid;gap:6px}.acceptance-feature p{margin:0}.acceptance-feature table{table-layout:fixed;width:100%}.acceptance-feature td,.acceptance-feature th{font-size:11px;padding:5px 2px;overflow-wrap:anywhere}.acceptance-feature li{overflow-wrap:anywhere;font-size:11px}
.p { font-size: 12.5px; line-height: 1.7; }
.steps, .li { font-size: 12.5px; line-height: 1.75; }
.steps { padding-left: 10px; }
.li { list-style: none; padding-left: 0; display: flex; flex-direction: column; gap: 4px; }
.li li { padding-left: 10px; border-left: 2px solid var(--line-2); }
.nowrap { white-space: nowrap; }
.w50 { width: 50px; }
.tscroll { max-height: 34vh; overflow: auto; }
summary { cursor: pointer; }
table { font-size: 12px; }
.cmd { display: flex; align-items: center; gap: 6px; min-width: 0; }
.cmd button { min-width: 118px; font-family: var(--font-mono); font-size: 11px; }
.res { color: var(--acc); max-width: 42%; }
.about-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; margin-bottom: 8px; }
.about-grid > div, .capability-list > div { display: grid; gap: 2px; padding: 7px 8px; border: 1px solid var(--line-2); border-radius: 5px; background: rgba(10,22,35,.45); }
.about-grid b, .capability-list b { color: var(--acc); font-size: 11px; }
.about-grid span, .capability-list span { color: var(--tx-2); font-size: 11px; line-height: 1.45; }
.capability-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
@media (max-width: 420px) { .about-grid, .capability-list { grid-template-columns: 1fr; } }
</style>
