<script setup>
import { ref, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import ProgramRules from '../components/ProgramRules.vue'
import { OPERATION_NOTES } from '../operationNotes'

const w = useWorld()
const { meta, snap } = storeToRefs(w)
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
  out.value[name] = '…'
  const r = await w.askReal(name, params)
  out.value[name] = r.ok ? (r.fields.join(', ') || 'OK') : `Fail ${r.err_code} ${r.hint || ''}`
}
</script>

<template>
  <div class="col">
    <section class="panel corner">
      <div class="panel-hd"><span class="panel-title">ElfinRobotStudio</span><span class="panel-sub">ABOUT / PROVENANCE</span></div>
      <div class="panel-bd">
        <div class="p">ElfinRobotStudio 是华沿 Elfin E05（真机自报 <b class="mono acc">E05-1195_Pro</b>）的点位仿真与监控台，中文名称为“华沿 Elfin 机器人仿真与监控台”。
          三维模型来自华沿官方 ROS 包
          <span class="mono">huayan-robotics/elfin_robot → elfin_description/meshes/elfin5</span>
          的每轴 STL 与 URDF，用 <span class="mono">tools/build_robot_model.py</span> 转成前端层级，不是手工建模。</div>
        <div class="note">型号判定：机械说明书 ver3.0 写 E05 = 5kg / 臂展 800mm / 法兰 Ø156mm；
          官方 elfin5 网格底座实测 156.0mm、零位法兰高度 <b class="mono">1195.0mm</b> —— 正好是型号串里的 1195。</div>
        <div class="note">正运动学反验：两组真机（关节角 → PCS_Base）实测比对，姿态差 0.001°、
          位置差 <b class="mono">0.09mm</b>（补 20mm 法兰参考点偏置后）。所以三维姿态与真机一致。</div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-hd"><span class="panel-title">操作指南</span></div>
      <div class="panel-bd">
        <ol class="steps">
          <li><b>模拟仿真</b>：纯软件机器人，与真机零耦合；「驱动与许可」页决定当前驱动谁。</li>
          <li><b>点位（含点动）</b>：拖滑块预设目标角、轻点 ± 走一个步距、按住 0.32s 以上连续点动，松手即停。点位库用于保存、改名和残影预览。</li>
          <li><b>残影 / 实时</b>：作业栏第二行那个开关。<b>残影</b>模式下拖预设会留下半透明余像，点「执行目标」才下发，点「取消」清余像并把滑块回到真实数据；<b>实时</b>模式不留余像，只跟着当前数据动。</li>
          <li><b>程序执行</b>：编辑 → 解析 → 启动。右下角日志显示执行回执；修改代码或参数后需要重新解析。程序可命名保存、载入和删除。</li>
          <li><b>AI 执行</b>：整栏对话，自然语言生成计划，必须点"人工确认"才会动作。</li>
          <li><b>摆放</b>：添加长方体、球或圆柱，点击物体编辑属性。现场可新建、命名保存、载入、导入/导出 JSON。</li>
          <li><b>真实监控</b>：在「驱动与许可」填 IP 连接后三维与真机同步（本地 120ms 插值缓冲）。</li>
          <li><b>日志</b>：右下角常驻执行控制台 + 独立日志页，可导出 CSV。</li>
          <li><b>设置</b>：围栏、阈值、两路速度、速度口径、播报、IP。</li>
          <li><b>顶栏 / 底栏</b>：顶部保留速度和急停；一键就绪在驱动页操作，底部汇总就绪、安全、链路、许可和摄像头状态。</li>
          <li><b>左侧栏</b>：导航底部可收起/展开工作面板；拖动面板右边缘调整宽度。收起后再次选择工作区会自动展开。</li>
          <li><b>摄像头</b>：三维右上角打开设备面板，可扫描、选择、开启和关闭相机；RealSense 的可选流取决于实际设备能力。</li>
          <li><b>三维操作</b>：左键旋转、滚轮缩放、点击选择物体；1–9 切页，F 切换 TCP 口径，R 复位视角。编辑器内这些快捷键不会触发机器人操作。</li>
          <li><b>卡片折叠</b>：点任意卡片标题栏收起/展开（点按钮和输入框不会触发）。</li>
        </ol>
      </div>
    </section>

    <ProgramRules />

    <section class="panel">
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

    <section class="panel">
      <div class="panel-hd"><span class="panel-title">安全口径与已知矛盾</span><span class="panel-sub">不粉饰</span></div>
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
          <li>软件急停只发 GrpStop；协议 p38 / 手册 p183 写明手动模式下急停响应为 Stop0 不受控停止，
            <b>不能替代物理急停按钮</b>。</li>
          <li class="warn">这台机器人<b>没有摄像头</b>：三份官方 PDF 里"相机/摄像头/视觉/图像"命中数为 0，
            Data Sheet 十个顶层键无图像字段，554/8554/9000/8000 等视频端口全部关闭；
            8080 是"控制器监控诊断系统"（运维用，无视频流）。要看末端视觉只能外接相机。</li>
        </ul>
      </div>
    </section>

    <section class="panel" v-if="meta">
      <div class="panel-hd"><span class="panel-title">已接入指令</span><span class="panel-sub">{{ cmds.length }} 条，页码为协议文档</span></div>
      <div class="panel-bd">
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

    <section class="panel">
      <div class="panel-hd">
        <span class="panel-title">调试台</span>
        <span class="panel-sub">同步查询复核 · 只在真机模式可用</span>
      </div>
      <div class="panel-bd">
        <div class="cmd" v-for="[n, p, why] in READS" :key="n">
          <button class="btn btn--xs" :disabled="!isReal" @click="read(n, p)">{{ n }}</button>
          <span class="dim tiny ellip">{{ why }}</span>
          <span class="grow"></span>
          <span class="mono tiny res ellip">{{ out[n] || '' }}</span>
        </div>
        <div v-if="!isReal" class="note warn">当前是仿真模式，只读指令不会发到任何硬件。去「驱动与许可」连接真机后才能复核。</div>
        <div class="note dim tiny">
          这些是<b>同步查询</b>，用来对账 20Hz 推送的 Data Sheet。协议里 <span class="mono">ReadCurFSM</span>
          一类必须带 <span class="mono">nRbtID</span>，漏了真机会回 20007。
        </div>
      </div>
    </section>

    <section class="panel" v-if="meta">
      <div class="panel-hd"><span class="panel-title">电源指令对照</span><span class="panel-sub">按钮背后发的是什么</span></div>
      <div class="panel-bd">
        <dl class="kv">
          <dt v-for="(v, k) in meta.power_actions" :key="k">{{ k }}</dt>
          <dd v-for="(v, k) in meta.power_actions" :key="'d' + k" class="tiny">{{ v }}</dd>
        </dl>
      </div>
    </section>

    <section class="panel">
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

    <section class="panel">
      <div class="panel-hd"><span class="panel-title">读数诚实性约定</span></div>
      <div class="panel-bd">
        <div class="p">未知一律显示 <b class="mono">--</b>，不填假设值；仿真数据永远带"仿真"徽标；
          超限只标红不把角度折回限位内；报错一定带错误码；汇总数值随筛选条件重算。</div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.program-example { font: 12px/1.7 var(--font-mono); background: var(--bg-inset); padding: 8px; overflow: auto; }
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
</style>
