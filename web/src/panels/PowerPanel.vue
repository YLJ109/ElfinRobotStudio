<script setup>
import { ref, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import ReadyControl from '../components/ReadyControl.vue'
import { isSixAxis } from '../connectionState.js'
import TaskTabs from '../components/TaskTabs.vue'
import { driveDiagnostics } from '../workspaceInsights.js'

const w = useWorld()
const { snap, tele } = storeToRefs(w)
const tab = ref('connection')
const tabs = [{ id: 'connection', label: '连接' }, { id: 'control', label: '控制' }, { id: 'diagnostics', label: '诊断' }]
const diagnostics = computed(() => driveDiagnostics({ ...snap.value, telemetry: tele.value }, w.connected))
const busy = ref('')
const msg = ref({})
const failed = ref({})
const ip = ref((snap.value && snap.value.settings.robot_ip) || '192.168.0.10')

const st = computed(() => {
  const s = snap.value
  if (!s) return {}
  return (s.mode === 'real' ? s.real : s.sim_state) || {}
})
const isReal = computed(() => snap.value && snap.value.mode === 'real')
const real = computed(() => (snap.value ? snap.value.real : null))
const readyInfo = computed(() => (snap.value ? snap.value.ready : null))
const ctrl = computed(() => (snap.value ? snap.value.ctrl : null))
const readySteps = computed(() => (readyInfo.value ? readyInfo.value.steps : []))
const running = computed(() => !!(readyInfo.value && readyInfo.value.running))
const ack = computed(() => (snap.value ? snap.value.speed_ack : null))
const realBad = computed(() => isReal.value && ack.value && ack.value.ok === false)
const f = (v, d = 2) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? '--' : Number(v).toFixed(d))
// In real mode never substitute FK, preview angles or simulation zeros for a report.
const reportedAngles = computed(() => isReal.value ? real.value?.joints : snap.value?.joints?.q)
const reportAvailable = computed(() => w.connected && isSixAxis(reportedAngles.value)
  && snap.value?.joints?.known !== false && (!isReal.value || real.value?.stream_up))
const reportSource = computed(() => isReal.value ? '控制器 Data Sheet · PosAndVel.Actual_Position（前六轴）' : '仿真关节状态')
const lastCommand = computed(() => isReal.value ? real.value?.last_command : null)
const commandReceipt = computed(() => {
  const outcome = lastCommand.value?.outcome
  return ['acknowledged', 'rejected'].includes(outcome) ? '已收到控制器回执'
    : ['not_sent', 'expired', 'cancelled'].includes(outcome) ? '未发送或已取消'
    : '执行结果需核对'
})
const commandFields = computed(() => {
  const fields = lastCommand.value?.fields
  return Array.isArray(fields) ? fields.join(', ') : fields == null ? '--'
    : typeof fields === 'object' ? JSON.stringify(fields) : String(fields)
})
const emergencyBusy = ref(false)
const emergencyReply = ref(null)
const emergencyError = ref('')
const emergencyReadAt = ref('')
const EMERGENCY_FIELDS = ['nESTO_Error', 'nESTO', 'nSafeGuard_Error', 'nSafeGuard']
const emergencyFields = computed(() => Array.isArray(emergencyReply.value?.fields) ? emergencyReply.value.fields : [])
async function readEmergencyInfo() {
  if (emergencyBusy.value || !w.connected || !isReal.value || !real.value?.cmd_up) return
  emergencyBusy.value = true
  emergencyReply.value = null
  emergencyError.value = ''
  try {
    const r = await w.askReal('ReadEmergencyInfo', [0])
    emergencyReply.value = r
    emergencyReadAt.value = new Date().toLocaleTimeString()
    if (!r?.ok) emergencyError.value = r?.why || r?.hint || `读取失败，错误码 ${r?.err_code ?? '--'}`
  } catch (e) { emergencyError.value = e.message || '急停信息读取失败' }
  finally { emergencyBusy.value = false }
}

/* 48V/温度这类是 1Hz 遥测通道的数，不再从 20Hz 快照里找 */
const volt = computed(() => (tele.value || {}).volt48_in)
const temps = computed(() => (tele.value || {}).temps)

/* FSM 档位表：完整官方表（HuayanRobot_ErrorCode.docx）仓库内缺失，
   这里的含义是 SDK 注释推断 + 本机逐条实测，界面上必须写明这一点。 */
const FSM = { 6: ['下电过渡', 'warn'], 7: ['未上电', 'bad'], 14: ['已上电', 'warn'],
              15: ['主控启动中', 'warn'], 24: ['初始化完成', 'warn'], 33: ['已就绪', 'ok'] }

/* 次要动作：主按钮只管"当前唯一合法的那一步"，剩下的收在这里。
   电源类动作一律不设闸 —— 让它停下来、把电断掉，不该被确认框挡住。 */
async function act(a) {
  if (busy.value) return
  busy.value = a
  msg.value[a] = '…'
  failed.value[a] = false
  try {
    const r = await w.power(a)
    failed.value[a] = !r?.ok
    if (r?.ok) msg.value[a] = r.sim ? '【仿真】已执行' : r.outcome === 'confirmed'
      ? '状态回读已确认' : `OK 控制器已应答，状态以回读为准 ${r.reply?.rtt_ms != null ? f(r.reply.rtt_ms, 0) + 'ms' : ''}`
    else msg.value[a] = r?.why || (r?.reply ? `Fail ${r.reply.err_code} ${r.reply.hint || ''}` : '操作被拒绝')
  } catch (e) {
    failed.value[a] = true
    msg.value[a] = e.message || '操作请求失败'
  } finally { busy.value = '' }
}
const SECOND = [
  ['disable', '去使能', 'p31 GrpDisable'],
  ['blackout', '下电', 'p22 BlackOut 断 48V；已使能时会被拒（实测 20018）'],
  ['init', '连接电箱', 'ConnectToBox 只连接电箱，不等于上电或启动主控；初始化使用 StartMaster'],
  ['reset', '清错', 'p32 GrpReset'],
  ['stop', '停止', 'p33 GrpStop（停动，不抱闸）'],
]
</script>

<template>
  <div class="col" v-if="snap">
    <TaskTabs v-model="tab" :tabs="tabs" id="drive" label="驱动分类" storage-key="huayan.tabs.drive.v1" />
    <div id="drive-panel" class="col" role="tabpanel" :aria-labelledby="`drive-tab-${tab}`" tabindex="0">
    <div class="note" :class="w.connected ? '' : 'warn'" role="status">{{ !w.connected ? '后端断开 · 现态未知' : isReal ? '真机 · 状态以有效回读为准' : '仿真模式 · 未连接真机' }}</div>
    <section v-if="tab === 'connection'" class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">驱动链路</span>
        <span class="panel-sub mono">{{ (real && real.host) || '未连接真机' }}</span>
        <span class="grow"></span>
        <button v-if="!isReal" class="btn btn--xs btn--primary" @click="w.connectReal(ip)">连接真机</button>
        <button v-else class="btn btn--xs btn--danger" @click="w.disconnectReal()">断开回仿真</button>
      </div>
      <div class="panel-bd">
        <div class="row nowrap">
          <button class="btn" :class="{ 'btn--on': !isReal }" @click="w.disconnectReal()">仿真</button>
          <button class="btn" :class="{ 'btn--on': isReal }" @click="w.connectReal(ip)">真机</button>
          <input class="input grow mono no-fold" v-model="ip" placeholder="192.168.0.10" :disabled="isReal"
                 aria-label="控制器 IP" />
        </div>
        <label class="row nowrap no-fold" style="gap:6px">
          <input type="checkbox" :checked="snap.settings.auto_connect"
                 @change="w.setSettings({ auto_connect: $event.target.checked })" />
          <span class="tiny">开机自动连接真机</span>
        </label>
        <div class="row nowrap">
          <span class="tag" :class="real && real.stream_up ? 'tag--ok' : 'tag--mute'">
            <i class="dot" :class="real && real.stream_up ? 'ok' : 'bad'"></i>取流 10004
          </span>
          <span class="tag" :class="real && real.cmd_up ? 'tag--ok' : 'tag--mute'">
            <i class="dot" :class="real && real.cmd_up ? 'ok' : 'bad'"></i>命令 10003
          </span>
          <span class="grow"></span>
          <span class="dim tiny mono">{{ f(real && real.rate_hz, 1) }}Hz · 滞后 {{ f(real && real.lag_ms, 0) }}ms</span>
        </div>
        <div v-if="isReal && (!real || !real.stream_up)" class="note warn">
          链路未连接，请检查电源、网线和 IP。
        </div>
      </div>
    </section>

    <section v-if="tab === 'diagnostics'" class="panel">
      <div class="panel-hd"><span class="panel-title">现态与恢复步骤</span></div>
      <div class="panel-bd"><div v-for="(row, i) in diagnostics" :key="i" class="note"><b>{{ row.state }}</b><p>{{ row.why }}</p><p>下一步：{{ row.fix }}</p></div></div>
    </section>
    <section v-if="isReal && tab === 'diagnostics'" class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">控制器状态</span>
        <span class="panel-sub">{{ isReal ? '真机 Data Sheet' : '仿真状态机' }}</span>
        <span class="grow"></span>
        <span class="tag" :class="'tag--' + (FSM[st.fsm] ? FSM[st.fsm][1] : 'mute')">
          FSM {{ st.fsm === null || st.fsm === undefined ? '--' : st.fsm }}
          {{ FSM[st.fsm] ? FSM[st.fsm][0] : '' }}
        </span>
        <span class="tag" :class="ctrl && ctrl.motion_granted ? 'tag--warn' : 'tag--mute'">
          {{ ctrl && ctrl.motion_granted ? '运动模式' : '只读模式' }}
        </span>
      </div>
      <div class="panel-bd">
        <dl class="kv">
          <dt class="reported-label">六轴上报角度</dt><dd class="reported-joints mono">
            <span v-for="i in 6" :key="i"><small>J{{ i }}</small><b>{{ reportAvailable ? f(reportedAngles[i - 1], 3) + '°' : '--' }}</b></span>
          </dd>
          <dt>角度来源</dt><dd class="report-source">{{ reportSource }}</dd>
          <dt>帧计数</dt><dd>后端解析 {{ isReal ? (real?.frame_seq ?? '--') : '不适用' }} / 页面接收 {{ w.receivedFrames }}</dd>
          <dt>使能 / 抱闸</dt>
          <dd>{{ st.enabled === null || st.enabled === undefined ? '--' : (st.enabled ? '已使能' : '未使能') }}
            / {{ st.brake ? st.brake.join(' ') : '--' }}</dd>
          <dt>运动 / 到位</dt>
          <dd>{{ st.moving ?? '--' }} / {{ st.inpos ?? '--' }}
            <span v-if="st.paused" class="warn tiny">· 暂停中</span>
            <span v-if="st.free_drive" class="warn tiny">· 手拖中</span></dd>
          <dt>错误码</dt><dd :class="st.err_code ? 'bad' : ''">{{ st.err_code ?? '--' }}
            <span class="dim tiny">轴 {{ st.err_axis ?? '--' }}</span></dd>
          <dt v-if="isReal">控制器 override</dt><dd v-if="isReal">
            {{ st.override === null || st.override === undefined ? '--' : (st.override * 100).toFixed(1) + ' %' }}
            <span v-if="realBad" class="bad tiny">（倍率设置未确认：{{ ack.why }}）</span>
            <span class="dim tiny">软件侧设的 {{ snap.real_speed_pct }}%</span>
          </dd>
          <dt v-if="isReal">48V 输入</dt><dd v-if="isReal">{{ f(volt, 2) }} V</dd>
          <dt v-if="isReal">关节温度</dt><dd v-if="isReal">
            {{ temps && temps.length ? temps.map((t) => f(t, 1)).join(' ') + ' ℃' : '--' }}</dd>
        </dl>
        <div v-if="!w.connected" class="note warn">后端已断开，以上控制器状态为最后一次快照，不能代表当前状态。</div>
        <div v-else-if="!reportAvailable" class="note warn">尚无有效六轴上报，不能确认实际姿态。</div>
        <div v-if="isReal && ![24, 33].includes(st.fsm)" class="note warn tiny">主控状态尚未确认完成初始化，上报角度可能仍为初始化值；帧计数增加不代表已经获得实际姿态。</div>
        <div v-if="lastCommand" class="command-reply note" :class="{ bad: lastCommand.ok === false }" :role="lastCommand.ok === false ? 'alert' : 'status'">
          <b>最近操作结果 · {{ lastCommand.name || '--' }}</b>
          <span>结果 {{ lastCommand.ok === true ? '成功' : lastCommand.ok === false ? '失败' : '未知' }} · {{ commandReceipt }}</span>
          <span>错误码 {{ lastCommand.err_code ?? '--' }} · 回执时间 {{ lastCommand.t ?? '--' }}</span>
          <span v-if="lastCommand.hint">{{ lastCommand.hint }}</span>
          <span class="mono">原始字段 {{ commandFields }}</span>
        </div>
        <template v-if="isReal">
          <button class="btn" :disabled="emergencyBusy || !w.connected || !real?.cmd_up" @click="readEmergencyInfo">{{ emergencyBusy ? '读取中…' : '读取急停信息' }}</button>
          <div v-if="emergencyError" class="note bad" role="alert">{{ emergencyError }}</div>
          <dl v-if="emergencyReply" class="kv mono">
            <template v-for="(label, i) in EMERGENCY_FIELDS" :key="label">
              <dt>{{ label }}</dt><dd>{{ emergencyFields[i] ?? '--' }}</dd>
            </template>
          </dl>
          <div class="dim tiny">ReadEmergencyInfo(0) · 只读原始字段，0/1 不解释为按钮按下或释放。{{ emergencyReply ? '最近手动读取 ' + emergencyReadAt + '，不随状态流更新。' : '' }}</div>
        </template>
      </div>
    </section>

    <div v-if="!isReal && tab === 'control'" class="note">当前为仿真。真机就绪与电源指令仅在主动连接真机后显示。</div>
    <section v-if="isReal && tab === 'control'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">就绪流程</span>
        <span class="grow"></span>
        <span class="tag" :class="running ? 'tag--acc' : 'tag--mute'">{{ running ? '推进中' : '空闲' }}</span>
      </div>
      <div class="panel-bd">
        <ReadyControl />
        <div v-if="readySteps.length" class="rsteps">
          <span v-for="x in readySteps" :key="x.cmd" class="rs">
            <b class="mono">{{ x.cmd }}</b><span class="dim">{{ x.at }}</span>
          </span>
        </div>
        <div v-else class="dim tiny">暂无流程记录</div>
        <div v-if="readyInfo && readyInfo.why" class="note bad">{{ readyInfo.why }}</div>
        
      </div>
    </section>

    <section v-if="isReal && tab === 'control'" class="panel">
      <div class="panel-hd">
        <span class="panel-title">手动状态指令</span>
      </div>
      <div class="panel-bd">
        <div class="grid2 no-fold">
          <div v-for="[a, label, sub] in SECOND" :key="a" class="act">
            <button class="btn" :class="{ 'btn--danger': a === 'blackout' }"
                    :disabled="!!busy" @click="act(a)" :title="sub">{{ label }}</button>
            <div v-if="msg[a]" class="r mono" :class="failed[a] ? 'bad' : 'ok'" :role="failed[a] ? 'alert' : 'status'">
              {{ msg[a] }}</div>
          </div>
        </div>
      </div>
    </section>
    </div>
  </div>
</template>

<style scoped>
.act { display: flex; flex-direction: column; gap: 2px; min-width: 0;
       padding: 3px 4px; border: 1px solid transparent; border-radius: 7px; }
.act .btn { width: 100%; }
.sub { font-size: 10.5px; }
.r { font-size: 10.5px; }
.rsteps { display: flex; flex-wrap: wrap; gap: 4px; }
.rs { display: flex; flex-direction: column; gap: 1px; padding: 3px 8px; font-size: 10.5px;
      border: 1px solid var(--line); border-radius: 10px; background: var(--bg-inset); }
.rs b { color: var(--acc); font-size: 11px; }
.dot { display: inline-block; width: 7px; height: 7px; margin-right: 4px; }
.reported-label, .reported-joints { grid-column: 1 / -1; }
.reported-joints { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px 8px; }
.reported-joints > span { display:flex; align-items:baseline; flex-wrap:wrap; gap:2px 4px; min-width:0; }
.reported-joints small { color:var(--tx-3); font-size:10px; }
.reported-joints b { font-size:11px; font-weight:600; white-space:nowrap; }
.report-source, .command-reply { overflow-wrap:anywhere; }
.command-reply { display:flex; flex-direction:column; gap:4px; }
</style>
