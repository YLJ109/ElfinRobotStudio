import { defineStore } from 'pinia'
import { createStateConnection, parseResponse } from './connectionState.js'
import { targetFor, createTargetState } from './targetState.js'
import { recordMetric } from './runtimeMetrics.js'
import { aiFor } from './aiConversation.js'

const metricNow = () => { try { return performance.now() } catch { return Date.now() } }
function metric(name, start) { try { recordMetric(name, Math.max(0, metricNow() - start)) } catch { /* Metrics never control the robot. */ } }

export const SYSTEM_TABS = ['engineering', 'logs', 'tele', 'settings', 'about']
export const WORK_TABS = ['points', 'program', 'ai']
export const NAV_VIEWS = ['sim', 'power', 'work', 'objects', 'engineering', 'hand-follow', 'fence', 'studio', 'vision']

// Old bookmarks and saved page names remain usable after the five-page merge.
export function normalizeNavigation({ view = 'sim', workTab = 'points', systemTab = 'engineering' } = {}) {
  const oldWorkEngineering = view === 'work' && workTab === 'engineering'
  const oldSystemPage = SYSTEM_TABS.includes(view)
  return {
    view: oldSystemPage || oldWorkEngineering ? 'engineering' : NAV_VIEWS.includes(view) ? view : 'sim',
    workTab: WORK_TABS.includes(workTab) ? workTab : 'points',
    systemTab: oldWorkEngineering ? 'engineering' : oldSystemPage && view !== 'engineering' ? view
      : SYSTEM_TABS.includes(systemTab) ? systemTab : 'engineering',
  }
}

function savedNavigation() {
  const read = (key) => {
    try { return globalThis.sessionStorage?.getItem(key) || globalThis.localStorage?.getItem(key) || undefined } catch { return undefined }
  }
  return normalizeNavigation({
    view: read('huayan.view') || read('view'),
    workTab: read('huayan.workTab') || read('workTab'),
    systemTab: read('huayan.systemTab'),
  })
}

/* 双通道：3D 走这个模块级的普通变量（不进 Vue 响应式，省掉每帧遍历整棵对象图），
   UI 走 20Hz 的响应式快照。真机 20Hz、仿真 50Hz 的原始帧都先落在这里。 */
let latest = null
let frameCb = null
export function setFrameHandler(fn) { frameCb = fn }
export function latestFrame() { return latest }
let lastLogSeq = -1
let uiDue = 0
const UI_INTERVAL_MS = 50          // UI 20Hz 足够；3D 的流畅度由场景内插值负责
/* 遥测（温度/力/IO/脚本）后端只 1Hz 发一次，前端没必要跟着 20Hz 重建响应式对象 */
let teleSeq = -1
let cameraRevision = 0
const connections = new WeakMap()
const bootVersions = new WeakMap()

async function post(path, body) {
  const start = metricNow()
  try {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}),
    })
    return await parseResponse(r, path)
  } finally {
    metric('http.request', start)
    if (path === '/api/ik') metric('ik.roundtrip', start)
    if (path === '/api/preview') metric('preview.roundtrip', start)
  }
}
async function get(path) {
  const start = metricNow()
  try { return await parseResponse(await fetch(path), path) }
  finally { metric('http.request', start) }
}
async function del(path) {
  const start = metricNow()
  try { return await parseResponse(await fetch(path, { method: 'DELETE' }), path) }
  finally { metric('http.request', start) }
}

export const useWorld = defineStore('world', {
  state: () => ({
    snap: null,
    tele: null,                   // 1Hz 遥测通道（独立于 snap，避免 20Hz 重建）
    meta: null,
    points: [],
    programs: [],
    scenes: [],
    activeScene: '',
    tools: { items: [], mounted: null, tool: null },
    cameras: { devices: [], active: null, sdk: { realsense: false, opencv: false }, error: '' },
    logs: [],
    ...savedNavigation(),       // Independent work entry + remembered engineering-workspace tab.
    preview: null,              // 非 null = 六轴滑块预设的目标角，三维按它摆并留残影
    targetState: createTargetState(), // Ephemeral; never restored from preferences.
    connected: false,
    connectionError: '',
    frameError: '',
    fenceVisible: true,
    receivedFrames: 0,
    error: '',
    aiHistory: [],
    aiPlan: null,
    aiPending: false,
    aiExecuting: false,
    selObj: null,               // 三维里选中的摆放物 id
    playbackGhost: null,
    /* 时间轴回放：数据一次取到浏览器本地（≤1500 帧），拖游标不再打网络。
       q 用列存（6 个数组）是为了按轴画曲线时不用逐帧解对象。 */
    play: { on: false, loaded: false, n: 0, dur: 0, idx: 0, t: [], cols: [],
            levels: [], marks: [] },
    // 默认示例必须包含真实可执行的运动指令；以 # 开头的行只是注释。
    programSrc: '# 编程规则见「关于」\n速度(15)\n关节移动(P1)\n等待(0.2)\n',
    lastAnnounced: '',
  }),
  getters: {
    speed: (s) => (s.snap ? s.snap.speed_pct : 10),
    mode: (s) => (s.snap ? s.snap.mode : 'sim'),
    perm: (s) => (s.snap ? s.snap.perm : 0),
    level: (s) => (s.snap ? s.snap.safety.level : 'green'),
    joints: (s) => (s.snap ? s.snap.joints : { q: [0, 0, 0, 0, 0, 0], names: [] }),
    real: (s) => (s.snap ? s.snap.real : null),
    ctrl: (s) => (s.snap ? s.snap.ctrl : null),
    /* 主按钮标签完全跟机器人状态走：没就绪就是"一键就绪"，就绪了就是"取消就绪"。 */
    primaryLabel: (s) => ((s.snap && s.snap.ctrl && s.snap.ctrl.primary) || {}).label || '连接中',
    canPrimary: (s) => s.connected && !!((s.snap && s.snap.ctrl && s.snap.ctrl.primary) || {}).enabled,
    freeOn: (s) => !!(s.snap && s.snap.free && s.snap.free.on),
  },
  actions: {
    async boot({ parallel = false } = {}) {
      const version = (bootVersions.get(this) || 0) + 1
      bootVersions.set(this, version)
      const current = () => bootVersions.get(this) === version
      try {
        // 每次等待后再核对归属，卸载或后一次启动后不允许旧请求写状态。
        const resources = [
          ['meta', '/api/meta'], ['points', '/api/points', true],
          ['programs', '/api/programs', true], ['tools', '/api/tools'],
          ['cameras', '/api/cameras'], ['scenes', '/api/scenes', true],
        ]
        if (parallel) {
          const results = await Promise.all(resources.map(async ([field, path, items]) => {
            const value = await get(path)
            return [field, items ? value.items : value]
          }))
          if (!current()) return { ok: false }
          for (const [field, value] of results) this[field] = value
        } else for (const [field, path, items] of resources) {
          const value = await get(path)
          if (!current()) return
          this[field] = items ? value.items : value
        }
      } catch (e) {
        if (!current()) return
        this.error = '后端未就绪：' + e.message
        if (parallel) return { ok: false, why: this.error }
      }
      if (current()) this.connectWs()
      return { ok: current() }
    },
    connectWs() {
      let connection = connections.get(this)
      if (connection) { connection.connect(); return }
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      let lastArrival = null
      connection = createStateConnection({
        url: `${proto}://${location.host}/ws`,
        onOpen: () => {
          lastArrival = null
          targetFor(this).invalidate('连接已更新，请重新预览目标。')
          this.connected = true
          this.connectionError = ''; this.frameError = ''
          lastLogSeq = -1; teleSeq = -1; uiDue = 0
          this.logs = []; this.snap = null; this.tele = null; this.receivedFrames = 0; latest = null
        },
        onMessage: (ev) => {
          const arrived = metricNow()
          if (lastArrival !== null) metric('state.interval', lastArrival)
          lastArrival = arrived
          let m
          try {
            const start = metricNow()
            try { m = JSON.parse(ev.data) } finally { metric('state.decode', start) }
            if (m.type !== 'state') return
            if (!m.data?.safety || !m.data?.joints) throw new Error('缺少 safety / joints 字段')
          } catch (e) { this.error = '状态消息无效：' + e.message; return }
          if (!m.data.settings && latest) m.data.settings = latest.settings
          if (m.data.log_delta) {
            const d = m.data.log_delta
            // 正确：空增量保留同一响应式数组，避免每个状态帧都重算日志与滚动位置。
            if (d.gap || d.rows.length) {
              const rows = d.gap ? [] : this.logs
              const known = new Set(rows.map((row) => row.seq))
              const added = d.rows.filter(row => {
                if (known.has(row.seq)) return false
                known.add(row.seq)
                return true
              })
              if (d.gap || added.length) this.logs = [...rows, ...added].slice(-2000)
            }
            lastLogSeq = d.seq
          }
          const prev = latest && latest.safety.level
          latest = m.data
          this.receivedFrames++
          if (m.data.log && m.data.log_seq !== lastLogSeq) {  // 首帧完整初始化
            lastLogSeq = m.data.log_seq
            this.logs = m.data.log
          }
          // 只有携带 telemetry 的帧才替换遥测，其他帧保留上一份读数。
          if (m.data.telemetry && m.data.telemetry_seq !== teleSeq) {
            teleSeq = m.data.telemetry_seq
            this.tele = m.data.telemetry
          }
          const now = performance.now()
          if (now >= uiDue) { uiDue = now + UI_INTERVAL_MS; this.snap = m.data }
          targetFor(this).observe(m.data, true)
          // An initial/reconnected snapshot is not a newly occurring alarm.
          // Keep the real safety indication, but do not replay it on every visit.
          if (prev != null && m.data.safety.level === 'alarm' && prev !== 'alarm') {
            this.announce('安全告警：' + ((m.data.safety.reasons || []).join('；') || '发生碰撞'))
          }
          // Rendering failures must never block state/telemetry delivery to the controls.
          try { if (frameCb) frameCb(m.data); this.frameError = '' }
          catch (e) { this.frameError = '三维更新失败：' + e.message }
        },
        onClose: () => {
          this.connected = false
          targetFor(this).invalidate('连接已断开，目标已失效；重新连接后请重新预览。')
          this.connectionError = '与后端的连接已断开，正在重连；显示的是最后一次状态'
        },
        onError: () => { this.connectionError = '与后端的连接异常，正在重连' },
      })
      connections.set(this, connection)
      connection.connect()
    },
    disconnectWs() {
      bootVersions.set(this, (bootVersions.get(this) || 0) + 1)
      connections.get(this)?.stop()
      this.connected = false
      targetFor(this).invalidate('连接已断开，目标已失效。')
    },
    async call(path, body) {
      try {
        const r = await post(path, body)
        if (r && r.ok === false) this.error = r.why || r.error || '请求被拒绝'
        else this.error = ''
        return r
      } catch (e) {
        this.error = e.message
        return { ok: false, why: e.message }
      }
    },
    async setSpeed(pct) { return await this.call('/api/speed', { pct: Number(pct) }) },
    async setSettings(patch) { return await this.call('/api/settings', patch) },
    async stopHandFollow() { return await post('/api/sim/hand-follow/stop', {}) },
    async ackMotion(acks) { return await post('/api/perm/ack', { acks }) },
    async power(action) { return await post('/api/power', { action }) },
    /* 主按钮：动作 key 由后端按当前状态推导，前端只负责"按下当前该按的那个" */
    async control(key, challenge) { return await post('/api/control', { key, challenge }) },
    async free(on, hold) { return await post('/api/free', { on, hold, source: '网页按住' }) },
    async playLoad() {
      const d = await get('/api/playback')
      const p = this.play
      p.n = d.n; p.dur = d.dur_s; p.t = d.t; p.cols = d.q || []
      p.levels = d.levels || []; p.marks = d.marks || []
      p.loaded = true
      if (!p.n) p.idx = 0
      p.idx = Math.min(p.idx || 0, Math.max(0, p.n - 1))
      return d
    },
    playSeek(frac) {
      const p = this.play
      if (!p.n) return
      p.idx = Math.max(0, Math.min(p.n - 1, Math.round(frac * (p.n - 1))))
    },
    playFrame() {
      const p = this.play
      if (!p.n || !p.cols.length) return null
      return [0, 1, 2, 3, 4, 5].map((j) => p.cols[j][p.idx])
    },
    async readySequence(thenEnable = true) {
      // 时序泵在后端：先看 FSM，已过就跳过；每步等状态真到位再发下一步。
      const r = await post('/api/ready', { then_enable: thenEnable })
      if (!r.ok) this.error = r.why || '就绪流程未启动'
      return r
    },
    async jog(axis, direction, on) { return await post('/api/jog', { axis, direction, on }) },
    async moveJ(joints) { return await targetFor(this).execute(joints) },
    async moveTargetRequest(joints) { return await post('/api/movej', { joints }) },
    async previewRequest(joints) { return await post('/api/preview', { joints }) },
    async ik(target, options = {}) { return await post('/api/ik', { target, ...options }) },
    async fk(joints) { return await post('/api/fk', { joints }) },
    setPreview(q, options) { return targetFor(this).preview(q, options) },
    cancelPreview() { return targetFor(this).preview(null) },
    setPlaybackGhost(joints) { this.playbackGhost = joints ? [...joints] : null },
    async estop() { return await this.call('/api/estop', {}) },
    async teach(name) {
      const r = await post('/api/points/teach', { name })
      if (r.ok) this.points = (await get('/api/points')).items
      return r
    },
    async refreshPoints() { this.points = (await get('/api/points')).items; return this.points },
    async addPoint(name, joints) {
      const target = targetFor(this), request = { revision: target.state.revision, context: target.state.context }
      try {
        const r = await post('/api/points', { name, joints })
        if (!r.ok) { this.error = r.why || '点位存储失败'; return r }
        const points = await get('/api/points')
        if (!target.current(request)) return { ok: false, why: '保存后的上下文已变化，名称已保留，请核对点位列表。' }
        this.points = points.items
        return r
      } catch (error) { this.error = error.message; return { ok: false, why: error.message } }
    },
    async removePoint(id) {
      await del('/api/points/' + id)
      this.points = (await get('/api/points')).items
    },
    async renamePoint(id, name) {
      const response = await fetch('/api/points/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
      const r = await response.json()
      if (r.ok) this.points = (await get('/api/points')).items
      else this.error = r.why || '点位改名失败'
      return r
    },
    async cameraState() { const revision = cameraRevision; const r = await get('/api/cameras'); if (revision === cameraRevision) this.cameras = r; return r },
    async runPoints(ids) { return await post('/api/points/run', { ids }) },
    async stopExec() { return await this.call('/api/motion/cancel', {}) },
    async resume() { return await this.call('/api/motion/resume', {}) },
    async loadProgram(src, params = {}) { return await post('/api/program/load', { src, params }) },
    async startProgram() { return await post('/api/program/start', {}) },
    async saveProgram(name, src) {
      const r = await post('/api/programs', { name, src })
      if (r.ok) this.programs = r.items
      return r
    },
    async deleteProgram(name) {
      const r = await del('/api/programs/' + encodeURIComponent(name))
      if (r.ok) this.programs = r.items
      return r
    },
    async openProgram(name) {
      const r = await get('/api/programs/' + encodeURIComponent(name))
      if (r.ok) this.programSrc = r.src
      return r
    },
    async addObject(spec) { return await post('/api/objects', spec) },
    async patchObject(id, patch) { return await post('/api/objects/update', { id, patch }) },
    async removeObject(id) { return await post('/api/objects/remove', { id }) },
    async clearObjects() { return await post('/api/objects/clear', {}) },
    async refreshScenes() {
      const r = await get('/api/scenes')
      this.scenes = r.items || []
      return this.scenes
    },
    async saveScene(name, objects) {
      const body = { name }
      if (objects) body.objects = objects
      const r = await post('/api/scenes', body)
      if (r.ok) { this.scenes = r.items || []; this.activeScene = name }
      else this.error = r.why || '现场保存失败'
      return r
    },
    async loadScene(name) {
      const r = await post('/api/scenes/load', { name })
      if (r.ok) { this.scenes = r.items || []; this.activeScene = r.name; this.selObj = null }
      else this.error = r.why || '现场载入失败'
      return r
    },
    async importScene(name, objects) {
      const r = await post('/api/scenes/import', { name, objects })
      if (r.ok) this.scenes = r.items || []
      else this.error = r.why || '现场导入失败'
      return r
    },
    async deleteScene(name) {
      const r = await del('/api/scenes/' + encodeURIComponent(name))
      if (r.ok) { this.scenes = r.items || []; if (this.activeScene === name) this.activeScene = '' }
      else this.error = r.why || '现场删除失败'
      return r
    },
    async exportScene(name) {
      const r = await get('/api/scenes/export/' + encodeURIComponent(name))
      if (!r.ok) { this.error = r.why || '现场导出失败'; return r }
      const blob = new Blob([JSON.stringify(r, null, 2)], { type: 'application/json;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = `${name}.scene.json`; a.click()
      URL.revokeObjectURL(url)
      return r
    },
    async connectReal(ip) { return await this.call('/api/real/connect', { ip }) },
    async disconnectReal() { return await this.call('/api/real/disconnect', {}) },
    async askReal(name, params) { return await post('/api/real/ask', { name, params }) },
    async refreshTools() { this.tools = await get('/api/tools'); return this.tools },
    async refreshCameras() {
      const revision = ++cameraRevision
      const r = await post('/api/cameras/scan', {})
      if (revision === cameraRevision) this.cameras = r
      return r
    },
    async openCamera(camera_id, streams, fps = 60) {
      const revision = ++cameraRevision
      const r = await post('/api/cameras/open', { camera_id, streams, fps })
      if (revision === cameraRevision) {
        this.cameras = { ...this.cameras, ...r }
        if (r.ok === false) {
          this.cameras.error = r.why || '摄像头开启失败'
        }
      }
      return r
    },
    async closeCamera(session) {
      const revision = ++cameraRevision
      const r = await post('/api/cameras/close', session===undefined?{}:{session})
      if (revision === cameraRevision) this.cameras = r
      return r
    },
    async selectTool(tool_id, source = 'manual', hardware_id = null, reason = '') {
      const r = await post('/api/tools/select', { tool_id, source, hardware_id, reason })
      if (r.ok) this.tools = { items: r.items, mounted: r.mounted, tool: r.tool }
      else this.error = r.why || '工具切换失败'
      return r
    },
    async clearTool(reason = '用户确认无工具') {
      const r = await post('/api/tools/clear', { reason })
      if (r.ok) this.tools = { items: r.items, mounted: r.mounted, tool: r.tool }
      else this.error = r.why || '确认无工具失败'
      return r
    },
    async toolAction(action, duration_ms = 0) {
      const r = await post('/api/tools/action', { action, duration_ms })
      if (r.ok) this.tools = { items: r.items, mounted: r.mounted, tool: r.tool }
      else this.error = r.why || '工具动作失败'
      return r
    },
    async endDo(bit, val) { return await post('/api/io/enddo', { bit, val }) },
    async aiSay(text) {
      return await aiFor(this, post).say(text)
    },
    async aiRun(message) {
      return await aiFor(this, post).run(message)
    },
    aiReason(message) { return aiFor(this, post).reason(message) },
    aiDiscard(why) { return aiFor(this, post).discard(why) },
    announce(text) {
      if (!this.snap?.settings?.tts) return
      if (text === this.lastAnnounced) return
      this.lastAnnounced = text
      try {
        const u = new SpeechSynthesisUtterance(text)
        u.lang = 'zh-CN'
        u.rate = this.snap.settings.tts_rate || 1
        speechSynthesis.cancel()
        speechSynthesis.speak(u)
      } catch (e) { /* 浏览器不支持语音就不播报，不影响告警显示 */ }
    },
  },
})
