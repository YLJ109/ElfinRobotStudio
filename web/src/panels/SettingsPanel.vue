<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorld } from '../store'
import TaskTabs from '../components/TaskTabs.vue'
import { settingChanges } from '../workspaceInsights.js'
import { parseResponse } from '../connectionState.js'

const w = useWorld()
const { snap: worldSnap } = storeToRefs(w)
const remote = ref({}), draft = ref({}), before = ref({}), busy = ref(false), failed = ref(false)
const base = computed(() => ({ ...remote.value, ...worldSnap.value?.settings }))
const snap = computed(() => worldSnap.value ? { ...worldSnap.value, settings: { ...base.value, ...draft.value } } : null)
const saved = ref('')
const tab = ref('connection'), search = ref('')
const tabs = [{id:'connection',label:'连接'},{id:'display',label:'显示'},{id:'advanced',label:'高级'},{id:'changes',label:'改动预览'},{id:'profiles',label:'配置档案'}]
const changes = computed(() => settingChanges(before.value, draft.value))
const groups = {
  connection: '连接 驱动模式 仿真 真机 控制器 IP robot_ip 开机自动连接真机 auto_connect 真机运动指令 real_move_cmd',
  display: '显示 残影模式 ghost_mode 假手透明度 ghost_opacity 轨迹拖尾 trail_len 残影重合判据 ghost_align_tol_deg ghost_align_hold_s',
  advanced: '高级 速度口径 vel_source 手拖按住上限 力保护阈值 free_hold_max_s free_force_max_n 回放缓冲 日志保留 play_len_s log_keep 仿真速度原始值 真机速度原始值 sim_speed_pct real_speed_pct AI 模型 接口地址 ai_model ai_base_url',
}
function matchField(text) { return !search.value.trim() || text.toLowerCase().includes(search.value.trim().toLowerCase()) }
function matches(group) { return matchField(groups[group]) }
watch(search, () => { if (search.value.trim()) { const group = Object.keys(groups).find(matches); if (group) tab.value = group } })
async function get(url) { return parseResponse(await fetch(url), url) }
onMounted(async () => { try { remote.value = await get('/api/settings') } catch(e) { failed.value = true; saved.value = `完整设置读取失败：${e.message}` } })

function patch(obj) {
  if (busy.value) return
  for (const [key, value] of Object.entries(obj)) {
    if (!(key in before.value)) before.value[key] = base.value[key]
    if (Object.is(before.value[key], value)) { delete draft.value[key]; delete before.value[key] }
    else draft.value[key] = value
  }
  saved.value = '已暂存，去「改动预览」核对后保存。'; failed.value = false
}
function restore(key) { delete draft.value[key]; delete before.value[key] }
async function applyChanges() {
  if (busy.value || !changes.value.length) return
  busy.value = true; failed.value = false
  try {
    const current = await get('/api/settings')
    const conflicts = changes.value.filter(row => !Object.is(current[row.key], row.before))
    if (conflicts.length) throw new Error(`设置已被其他操作修改：${conflicts.map(row => row.key).join('、')}；请撤销这些项后重新编辑。`)
    const response = await w.setSettings({ ...draft.value })
    if (!response?.ok) throw new Error(response?.why || '保存失败')
    remote.value = response.settings || current
    draft.value = {}; before.value = {}; saved.value = '设置已保存；当前运行状态以回读为准。'
  } catch(e) { failed.value = true; saved.value = e.message }
  finally { busy.value = false }
}

const profiles = ref(null), profileStatus = ref(''), profileBusy = ref(false), profileName = ref(''), importText = ref(''), comparison = ref(null)
const profilePrefix = '/api/workspace/profiles'
const profileKeys = ['sim_speed_pct', 'ghost_mode', 'ghost_opacity', 'ghost_align_tol_deg', 'ghost_align_hold_s', 'tts', 'tts_rate', 'log_keep', 'play_len_s', 'trail_len']
const offlineIdle = computed(() => w.connected && worldSnap.value?.mode === 'sim' && !worldSnap.value?.real?.stream_up && !worldSnap.value?.real?.cmd_up
  && worldSnap.value?.moving === false && !worldSnap.value?.exec?.execution?.busy
  && !['running','waiting','paused'].includes(worldSnap.value?.exec?.state) && !worldSnap.value?.ready?.running
  && !worldSnap.value?.hand_follow?.active && !worldSnap.value?.real_hand_follow?.active && !worldSnap.value?.free?.on
  && !worldSnap.value?.paused && !['running','paused'].includes(worldSnap.value?.exec?.queue_state)
  && worldSnap.value?.exec?.stop_result?.settled !== false)
function profileError(response, fallback) {
  const detail = response?.why || response?.detail || response?.error
  return typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => item?.msg || String(item)).join('；') : fallback
}
async function profileTask(fn) {
  if (profileBusy.value) return
  profileBusy.value = true; profileStatus.value = ''
  try { await fn() } catch(e) { profileStatus.value = e.message }
  finally { profileBusy.value = false }
}
async function loadProfiles() {
  const response = await get(profilePrefix)
  if (!response?.ok || !Array.isArray(response.items)) throw new Error('配置档案接口尚不可用')
  profiles.value = response.items
  comparison.value = null
}
watch(tab, value => { if (value === 'profiles' && profiles.value === null) profileTask(loadProfiles) })
async function profilePost(path, body) {
  const response = await w.call(profilePrefix + path, body)
  if (!response?.ok) throw new Error(profileError(response, '档案请求失败'))
  return response
}
function saveProfile() { profileTask(async () => { await profilePost('', { name: profileName.value.trim() }); await loadProfiles(); profileStatus.value = '已保存服务端当前白名单设置（不含未提交草稿）。' }) }
function compareProfile(id) { profileTask(async () => {
  comparison.value = null
  const result = await profilePost(`/${encodeURIComponent(id)}/compare`, {})
  if (!result.profile?.id || typeof result.profile.revision !== 'string' || !result.profile.revision || !Array.isArray(result.changes)) throw new Error('档案比较响应不完整，请刷新后重试。')
  comparison.value = result; profileStatus.value = '比较完成，已绑定本次当前配置；档案或配置发生变化后需重新比较。'
}) }
function applyProfile() {
  if (!offlineIdle.value || changes.value.length || !comparison.value) return
  profileTask(async () => {
    // Only the compare response authorizes this attempt. Never substitute the
    // list item's revision or coerce the opaque concurrency token to a number.
    const compared = comparison.value.profile
    comparison.value = null
    await profilePost(`/${encodeURIComponent(compared.id)}/apply`, { revision:compared.revision })
    remote.value = await get('/api/settings'); await loadProfiles(); profileStatus.value = '配置档案已应用，等待快照回读。'
  })
}
function deleteProfile(id) { profileTask(async () => { const url = `${profilePrefix}/${encodeURIComponent(id)}`; const response = await parseResponse(await fetch(url, { method:'DELETE' }), url); if (!response?.ok) throw new Error(profileError(response, '删除失败')); comparison.value = null; await loadProfiles(); profileStatus.value = '档案已删除。' }) }
function importProfile() { profileTask(async () => { const value = JSON.parse(importText.value); const settings = value.settings || value; await profilePost('/import', { name: profileName.value.trim(), settings }); importText.value = ''; comparison.value = null; await loadProfiles(); profileStatus.value = '已导入档案，尚未应用。' }) }

const speedKeys = ['sim_speed_pct', 'real_speed_pct']
</script>

<template>
  <div class="col" v-if="snap" @keydown.space.stop>
    <TaskTabs v-model="tab" :tabs="tabs" id="settings" label="设置分类" storage-key="huayan.tabs.settings.v1" />
    <input v-model="search" class="input" type="search" aria-label="搜索设置" placeholder="搜索设置名称或键名" />
    <div v-if="saved" class="note tiny" :class="failed ? 'bad' : ''" role="status">{{ saved }}</div>
    <div v-if="search.trim() && !Object.keys(groups).some(matches)" class="note">没有匹配的设置。</div>
    <div id="settings-panel" class="col" role="tabpanel" :aria-labelledby="`settings-tab-${tab}`" tabindex="0">
    <fieldset :disabled="busy" class="settings-fields" v-if="['connection','display','advanced'].includes(tab)">

    <section v-if="tab === 'connection' && matches('connection')" class="panel corner">
      <div class="panel-hd">
        <span class="panel-title">连接与模式</span>
      </div>
      <div class="panel-bd">
        <div v-if="matchField('驱动模式 仿真 真机')" class="row nowrap">
          <span class="lbl">驱动模式</span>
          <button class="btn" :class="{ 'btn--on': snap.mode === 'sim' }" @click="w.disconnectReal()">仿真</button>
          <button class="btn" :class="{ 'btn--on': snap.mode === 'real' }" :disabled="'robot_ip' in draft" @click="w.connectReal()">真机</button>
        </div>
        <div v-if="'robot_ip' in draft" class="note warn tiny">控制器 IP 尚为草稿，请先在改动预览保存或恢复此项，再主动连接。</div>
        <div v-if="matchField('控制器 IP robot_ip')" class="row nowrap">
          <span class="lbl">控制器 IP</span>
          <input class="input mono grow" :value="snap.settings.robot_ip"
                 @change="patch({ robot_ip: $event.target.value.trim() })" />
        </div>
        <label v-if="matchField('开机自动连接真机 auto_connect')" class="row no-fold" style="gap:6px">
          <input type="checkbox" :checked="snap.settings.auto_connect"
                 @change="patch({ auto_connect: $event.target.checked })" />
          <span class="tiny">开机自动连接真机</span>
        </label>
        <div v-if="matchField('真机运动指令 real_move_cmd')" class="row nowrap">
          <span class="lbl">真机运动指令</span>
          <select class="select grow" :value="snap.settings.real_move_cmd"
                  @change="patch({ real_move_cmd: $event.target.value })">
            <option value="relj">MoveRelJ 逐轴逼近（本机已实测）</option>
            <option value="movejto" disabled>MoveJTo 长按到点（心跳未验证，暂不可用）</option>
          </select>
        </div>
        
      </div>
    </section>

    <section v-if="tab === 'display' && matches('display')" class="panel">
      <div class="panel-hd">
        <span class="panel-title">显示</span>
      </div>
      <div class="panel-bd">
        <div v-if="matchField('残影模式 ghost_mode')" class="row nowrap">
          <span class="lbl">残影模式</span>
          <button class="btn" :class="{ 'btn--on': snap.settings.ghost_mode === 'ghost' }"
                  @click="patch({ ghost_mode: 'ghost' })">残影预览</button>
          <button class="btn" :class="{ 'btn--on': snap.settings.ghost_mode === 'live' }"
                  @click="patch({ ghost_mode: 'live' })">实时跟随</button>
        </div>
        <div v-if="matchField('假手透明度 ghost_opacity')" class="row nowrap">
          <span class="lbl">假手透明度</span>
          <input type="range" min="0.1" max="0.7" step="0.02" :value="snap.settings.ghost_opacity"
                 :style="{ '--fill': ((snap.settings.ghost_opacity - 0.1) / 0.6 * 100) + '%' }"
                 @change="patch({ ghost_opacity: Number($event.target.value) })" />
          <b class="mono tiny">{{ Number(snap.settings.ghost_opacity).toFixed(2) }}</b>
        </div>
        <div v-if="matchField('轨迹拖尾 trail_len')" class="row nowrap">
          <span class="lbl">轨迹拖尾</span>
          <input type="range" min="0" max="100" step="5" :value="snap.settings.trail_len ?? 40"
                 :style="{ '--fill': (snap.settings.trail_len ?? 40) + '%' }"
                 @change="patch({ trail_len: Number($event.target.value) })" />
          <b class="mono tiny">{{ snap.settings.trail_len ?? 40 }} 帧</b>
        </div>
        <div v-if="matchField('残影重合判据 ghost_align_tol_deg ghost_align_hold_s')" class="row nowrap">
          <span class="lbl">残影重合判据</span>
          ±<input class="input input--num" type="number" step="0.1" :value="snap.settings.ghost_align_tol_deg"
                 @change="patch({ ghost_align_tol_deg: Number($event.target.value) })" />°
          站住
          <input class="input input--num" type="number" step="0.1" :value="snap.settings.ghost_align_hold_s"
                 @change="patch({ ghost_align_hold_s: Number($event.target.value) })" />s
        </div>
      </div>
    </section>

    <section v-if="tab === 'advanced' && matches('advanced')" class="panel">
      <div class="panel-hd">
        <span class="panel-title">高级</span>
        <span class="grow"></span>
      </div>
      <div class="panel-bd">
        <div v-if="matchField('速度口径 vel_source')" class="row nowrap">
          <span class="lbl">速度口径</span>
          <select class="select grow" :value="snap.settings.vel_source"
                  @change="patch({ vel_source: $event.target.value })">
            <option value="urdf">URDF（六轴 90°/s）</option>
            <option value="manual">手册 E05（J1-4 180，J5-6 200°/s）</option>
          </select>
        </div>
        

        <div v-if="matchField('手拖按住上限 力保护阈值 free_hold_max_s free_force_max_n')" class="row nowrap">
          <span class="lbl">手拖按住上限</span>
          <input class="input input--num" type="number" step="1" :value="snap.settings.free_hold_max_s"
                 @change="patch({ free_hold_max_s: Number($event.target.value) })" />s
          <span class="lbl">力保护阈值</span>
          <input class="input input--num" type="number" step="5" :value="snap.settings.free_force_max_n"
                 @change="patch({ free_force_max_n: Number($event.target.value) })" />N
          <span class="dim tiny grow">0 = 不设力闸（本机 FTData 是否可用需先实测）</span>
        </div>
        <div v-if="matchField('回放缓冲 日志保留 play_len_s log_keep')" class="row nowrap">
          <span class="lbl">回放缓冲</span>
          <input class="input input--num" type="number" step="30" :value="snap.settings.play_len_s"
                 @change="patch({ play_len_s: Number($event.target.value) })" />s
          <span class="lbl">日志保留</span>
          <input class="input input--num" type="number" step="200" :value="snap.settings.log_keep"
                 @change="patch({ log_keep: Number($event.target.value) })" />条
        </div>
        <div class="row nowrap" v-for="k in speedKeys.filter(key => matchField(key + (key === 'sim_speed_pct' ? ' 仿真速度原始值' : ' 真机速度原始值')))" :key="k">
          <span class="lbl">{{ k === 'sim_speed_pct' ? '仿真速度原始值' : '真机速度原始值' }}</span>
          <input class="input input--num" type="number" step="1" :value="snap.settings[k]"
                 @change="patch({ [k]: Number($event.target.value) })" />%
        </div>
        <div v-if="matchField('AI 模型 接口地址 ai_model ai_base_url')" class="field">
          <label>AI 模型 / 接口地址（密钥只放项目根 .env，界面上看不到也不写入设置）</label>
          <div class="row nowrap">
            <input class="input grow mono" :value="snap.settings.ai_model"
                   @change="patch({ ai_model: $event.target.value.trim() })" />
          </div>
          <input class="input mono" :value="snap.settings.ai_base_url"
                 @change="patch({ ai_base_url: $event.target.value.trim() })" />
        </div>
      </div>
    </section>
    </fieldset>
    <section v-if="tab === 'changes'" class="panel">
      <div class="panel-hd"><span class="panel-title">改动预览 · {{ changes.length }} 项</span></div>
      <div class="panel-bd"><div class="note tiny">表单改动先暂存。连接/断开模式按钮仍为即时动作。单项恢复只撤销草稿，不向机器人发送命令。</div>
        <div v-for="row in changes" :key="row.key" class="change-row"><b>{{ row.key }}</b><span>{{ row.before ?? '未读取' }} → {{ row.after }}</span><button class="btn btn--xs" :disabled="busy" @click="restore(row.key)">恢复此项</button></div>
        <div v-if="!changes.length" class="note">没有待保存的改动。</div><button v-else class="btn btn--primary" :disabled="busy" @click="applyChanges">{{ busy ? '保存中…' : '确认保存改动' }}</button>
      </div>
    </section>
    <section v-if="tab === 'profiles'" class="panel">
      <div class="panel-hd"><span class="panel-title">配置档案</span></div>
      <div class="panel-bd">
        <p class="note tiny">档案仅保存仿真速度、残影显示与重合判据、语音播报、日志保留、回放长度及轨迹拖尾。不含围栏、工具、限位、真机速度、IP、Token、自动连接或运行权限；不是完整工位备份。导入不应用；应用仅限离线仿真空闲，服务端最终校验。</p>
        <details><summary class="tiny">允许的设置键</summary><p class="tiny mono">{{ profileKeys.join('、') }}</p></details>
        <div v-if="profileStatus" class="note" role="status">{{ profileStatus }}</div>
        <div v-if="profiles === null" class="note">{{ profileBusy ? '正在检查配置档案接口…' : '尚未获取可用档案接口，未开放操作。重新选择此 Tab 可重试。' }}</div>
        <fieldset v-else class="settings-fields" :disabled="profileBusy">
          <label class="field">档案名称<input class="input" v-model="profileName" maxlength="64" /></label>
          <div class="row"><button class="btn" :disabled="!profileName.trim() || changes.length > 0" @click="saveProfile">保存当前配置</button><button class="btn" @click="profileTask(loadProfiles)">刷新档案</button></div>
          <div v-if="!profiles.length" class="note">尚无配置档案。</div>
          <div v-for="profile in profiles" :key="profile.id" class="profile-row"><b>{{ profile.name }}</b><span class="tiny">档案版本 {{ profile.version ?? '--' }} · {{ profile.created }}</span><div class="row"><button class="btn btn--xs" @click="compareProfile(profile.id)">比较</button><a class="btn btn--xs" :href="`${profilePrefix}/${encodeURIComponent(profile.id)}`" download>导出</a><button class="btn btn--xs" @click="deleteProfile(profile.id)">删除档案</button></div></div>
          <label class="field">导入 JSON（settings 对象或导出的档案）<textarea class="input" v-model="importText" rows="4" /></label><button class="btn" :disabled="!profileName.trim() || !importText.trim()" @click="importProfile">导入为新档案</button>
          <div v-if="comparison" class="profile-row"><b>比较：{{ comparison.profile.name }} · 档案版本 {{ comparison.profile.version ?? '--' }}</b>
            <span class="tiny dim">本次比较已绑定当前配置；配置或档案变化后必须重新比较。</span>
            <div v-for="row in comparison.changes" :key="row.key" class="change-row"><b>{{ row.key }}</b><span>{{ row.current }} → {{ row.saved }}</span></div>
            <span v-if="!comparison.changes.length" class="note">当前配置与档案一致。</span>
            <button v-else class="btn btn--primary" :disabled="!offlineIdle || changes.length > 0" @click="applyProfile">确认应用本次比较</button>
            <span v-if="!offlineIdle || changes.length" class="note warn">应用需状态流在线、仿真模式、真机两通道断开、任务空闲，且没有设置草稿。</span>
          </div>
        </fieldset>
      </div>
    </section>
    </div>
  </div>
</template>

<style scoped>
.lbl { font-size: 11.5px; color: var(--tx-2); white-space: nowrap; }
.saved { color: var(--ok); padding: 3px 8px; background: var(--ok-soft);
         border-radius: 6px; border: 1px solid rgba(46, 230, 168, .3); }
.col.s4 { gap: 4px; }
.field .row + .row { margin-top: 2px; }
.settings-fields { border:0; padding:0; margin:0; min-width:0; display:flex; flex-direction:column; gap:8px; }
.change-row, .profile-row { display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:8px 0; border-bottom:1px solid var(--line); font-size:12px; overflow-wrap:anywhere; }
.profile-row { flex-direction:column; align-items:stretch; }
.change-row span { flex:1; min-width:80px; }
</style>
