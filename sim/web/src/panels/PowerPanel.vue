<script setup>
import { ref, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import ReadyControl from '../components/ReadyControl.vue'

const w = useWorld()
const { snap, tele } = storeToRefs(w)
const busy = ref('')
const msg = ref({})
const ip = ref((snap.value && snap.value.settings.robot_ip) || '192.168.0.10')

const st = computed(() => {
  const s = snap.value
  if (!s) return {}
  return s.mode === 'real' && s.real ? s.real : s.sim_state
})
const isReal = computed(() => snap.value && snap.value.mode === 'real')
const real = computed(() => (snap.value ? snap.value.real : null))
const readyInfo = computed(() => (snap.value ? snap.value.ready : null))
const ctrl = computed(() => (snap.value ? snap.value.ctrl : null))
const readySteps = computed(() => (readyInfo.value ? readyInfo.value.steps : []))
const running = computed(() => !!(readyInfo.value && readyInfo.value.running))
const ack = computed(() => (snap.value ? snap.value.speed_ack : null))
const realBad = computed(() => isReal.value && ack.value && ack.value.ok === false)
const f = (v, d = 2) => (v === null || v === undefined || v === '' ? '--' : Number(v).toFixed(d))

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
  busy.value = a
  msg.value[a] = '…'
  const r = await w.power(a)
  if (r.ok) msg.value[a] = r.sim ? '【仿真】已执行' : `OK ${r.reply ? r.reply.rtt_ms.toFixed(0) + 'ms' : ''}`
  else msg.value[a] = r.why || (r.reply ? `Fail ${r.reply.err_code} ${r.reply.hint || ''}` : 'Fail')
  busy.value = ''
}
const SECOND = [
  ['disable', '去使能', 'p31 GrpDisable'],
  ['blackout', '下电', 'p22 BlackOut 断 48V；已使能时会被拒（实测 20018）'],
  ['init', '连接电箱', 'p20 ConnectToBox。实测 14→24 是 StartMaster 做的，这条留作手动可选'],
  ['reset', '清错', 'p32 GrpReset'],
  ['stop', '停止', 'p33 GrpStop（停动，不抱闸）'],
]
</script>

<template>
  <div class="col" v-if="snap">
    <section class="panel corner">
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

    <section class="panel corner">
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
            <span v-if="realBad" class="bad tiny">（SetOverride 被拒：{{ ack.why }}）</span>
            <span class="dim tiny">软件侧设的 {{ snap.real_speed_pct }}%</span>
          </dd>
          <dt v-if="isReal">48V 输入</dt><dd v-if="isReal">{{ f(volt, 2) }} V</dd>
          <dt v-if="isReal">关节温度</dt><dd v-if="isReal">
            {{ temps && temps.length ? temps.map((t) => f(t, 1)).join(' ') + ' ℃' : '--' }}</dd>
        </dl>
        
      </div>
    </section>

    <section class="panel">
      <div class="panel-hd">
        <span class="panel-title">就绪流程</span>
        <span class="panel-sub mono">FSM PUMP</span>
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

    <section class="panel">
      <div class="panel-hd">
        <span class="panel-title">手动状态指令</span>
      </div>
      <div class="panel-bd">
        <div class="grid2 no-fold">
          <div v-for="[a, label, sub] in SECOND" :key="a" class="act">
            <button class="btn" :class="{ 'btn--danger': a === 'blackout' }"
                    :disabled="busy === a" @click="act(a)" :title="sub">{{ label }}</button>
            <div v-if="msg[a]" class="r mono" :class="/^(Fail|需要|未知|仿真：|命令通道)/.test(msg[a]) ? 'bad' : 'ok'">
              {{ msg[a] }}</div>
          </div>
        </div>
      </div>
    </section>
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
</style>
