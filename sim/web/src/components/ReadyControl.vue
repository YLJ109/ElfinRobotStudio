<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'

const w = useWorld()
const { snap } = storeToRefs(w)
const ctrl = computed(() => (snap.value ? snap.value.ctrl : null))
const primary = computed(() => (ctrl.value || {}).primary || { label: '连接中…', sub: 'WAIT', enabled: false })
const running = computed(() => !!(ctrl.value || {}).ready_running)
const showAck = ref(false)
const ackBox = ref({ nobody: false, estop: false })
const busy = ref(false)
const denied = ref('')

async function press() {
  denied.value = ''
  if (!primary.value.enabled || busy.value) return
  if (ctrl.value.need_ack) { showAck.value = true; return }
  await run(primary.value.key)
}
async function run(key, challenge) {
  busy.value = true
  try {
    const r = await w.control(key, challenge)
    if (r && r.changed) denied.value = r.why
    else if (r && r.ok === false) denied.value = r.why || '被拒绝'
    else denied.value = ''
    if (r && r.need_ack) showAck.value = true
  } finally {
    busy.value = false
    showAck.value = false
    ackBox.value = { nobody: false, estop: false }
  }
}
async function confirmAck() {
  const a = await w.ackMotion(ackBox.value)
  if (!a.ok) { denied.value = a.why || '确认凭据签发失败'; showAck.value = false; return }
  await run(primary.value.key, a.challenge)
}
</script>

<template>
  <div class="ready-control">
    <button class="btn ready-primary" :class="'btn--' + (primary.tone || 'acc')"
            :disabled="!primary.enabled || busy" :title="primary.why" @click="press">
      <span>{{ running ? '推进中…' : primary.label }}</span>
      <small class="mono">{{ running ? 'WORKING' : primary.sub }}</small>
    </button>
    <span v-if="ctrl && ctrl.ack_left_s" class="arm mono tiny">
      场地确认 {{ Math.floor(ctrl.ack_left_s / 60) }}分{{ (ctrl.ack_left_s % 60) + '秒' }}
    </span>
    <div v-if="denied" class="note bad" role="alert">{{ denied }}</div>

    <div v-if="showAck" class="mask">
      <div class="dlg panel corner">
        <div class="panel-hd">
          <span class="panel-title">{{ primary.label }}</span>
          <span class="panel-sub mono">{{ primary.sub }} · 两项确认</span>
        </div>
        <div class="panel-bd">
          <div class="note tiny">{{ primary.why }}</div>
          <label class="chk"><input type="checkbox" v-model="ackBox.nobody" />
            <span>工作范围内<b>无人、无障碍物</b>，机器人运动不会伤及人员或设备。</span></label>
          <label class="chk"><input type="checkbox" v-model="ackBox.estop" />
            <span>我能够到<b>物理急停按钮</b>，可在异常时立即停止设备。</span></label>
          <div class="row">
            <button class="btn btn--primary grow" :disabled="!(ackBox.nobody && ackBox.estop) || busy"
                    @click="confirmAck">确认并{{ primary.label }}</button>
            <button class="btn" @click="showAck = false">取消</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ready-control { display:flex; flex-direction:column; gap:6px; }
.ready-primary { width:100%; min-height:46px; flex-direction:column; gap:1px; line-height:1.15;
  clip-path:polygon(0 0,calc(100% - 9px) 0,100% 9px,100% 100%,9px 100%,0 calc(100% - 9px)); }
.ready-primary > span { font-family:var(--font-display); font-size:13.5px; font-weight:700; letter-spacing:.06em; }
.ready-primary small { font-size:9px; letter-spacing:.16em; opacity:.72; }
.arm { color:var(--tx-3); text-align:right; }
.mask { position:fixed; inset:0; z-index:60; background:rgba(2,5,10,.74); display:flex; align-items:center;
  justify-content:center; backdrop-filter:blur(3px); }
.dlg { width:440px; max-width:92vw; }
.chk { display:flex; gap:8px; align-items:flex-start; line-height:1.55; font-size:12.5px; cursor:pointer; }
</style>
