import { defineStore } from 'pinia'

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

async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}),
  })
  if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status)
  return r.json()
}
async function get(path) {
  const r = await fetch(path)
  if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status)
  return r.json()
}
async function del(path) {
  const r = await fetch(path, { method: 'DELETE' })
  return r.json()
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
    view: 'sim',
    workTab: 'points',          // points | program | ai | jog —— 作业页顶部 tab
    preview: null,              // 非 null = 六轴滑块预设的目标角，三维按它摆并留残影
    connected: false,
    error: '',
    aiHistory: [],
    aiPlan: null,
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
    canPrimary: (s) => !!((s.snap && s.snap.ctrl && s.snap.ctrl.primary) || {}).enabled,
    freeOn: (s) => !!(s.snap && s.snap.free && s.snap.free.on),
  },
  actions: {
    async boot() {
      try {
        this.meta = await get('/api/meta')
        this.points = (await get('/api/points')).items
        this.programs = (await get('/api/programs')).items
        this.tools = await get('/api/tools')
        this.cameras = await get('/api/cameras')
        this.scenes = (await get('/api/scenes')).items
      } catch (e) {
        this.error = '后端未就绪：' + e.message
      }
      this.connectWs()
    },
    connectWs() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws`)
      ws.onopen = () => { this.connected = true; this.error = ''; lastLogSeq = -1; teleSeq = -1; this.logs = []; latest = null }
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data)
        if (m.type !== 'state') return
        if (!m.data.settings && latest) m.data.settings = latest.settings
        if (m.data.log_delta) {
          const d = m.data.log_delta
          const rows = d.gap ? [] : this.logs
          const known = new Set(rows.map((row) => row.seq))
          this.logs = [...rows, ...d.rows.filter((row) => !known.has(row.seq))].slice(-2000)
          lastLogSeq = d.seq
        }
        const prev = latest && latest.safety.level
        latest = m.data
        if (frameCb) frameCb(m.data)          // 3D 每帧都吃，不经响应式
        if (m.data.safety.level === 'alarm' && prev !== 'alarm') {
          this.announce('安全告警：' + (m.data.safety.reasons.join('；') || '发生碰撞'))
        }
        if (m.data.log && m.data.log_seq !== lastLogSeq) {  // 首帧完整初始化
          lastLogSeq = m.data.log_seq
          this.logs = m.data.log
        }
        // 只有真带了 telemetry 的那一帧才换引用：20Hz 里其余 19 帧这个字段根本不存在，
        // 拿 undefined 去比 seq 会把已经收到的遥测覆盖成空（实测踩过）。
        if (m.data.telemetry && m.data.telemetry_seq !== teleSeq) {
          teleSeq = m.data.telemetry_seq
          this.tele = m.data.telemetry
        }
        const now = performance.now()
        if (now >= uiDue) { uiDue = now + UI_INTERVAL_MS; this.snap = m.data }
      }
      ws.onclose = () => {
        this.connected = false
        setTimeout(() => this.connectWs(), 1500)
      }
      ws.onerror = () => { this.error = '与后端的连接异常，正在重连' }
    },
    async call(path, body) {
      try {
        const r = await post(path, body)
        if (r && r.ok === false && r.why) this.error = r.why
        else this.error = ''
        return r
      } catch (e) {
        this.error = e.message
        return { ok: false, why: e.message }
      }
    },
    async setSpeed(pct) { return await post('/api/speed', { pct: Number(pct) }) },
    async setSettings(patch) { return await post('/api/settings', patch) },
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
    async moveJ(joints) { return await post('/api/movej', { joints }) },
    async ik(target, options = {}) { return await post('/api/ik', { target, ...options }) },
    async fk(joints) { return await post('/api/fk', { joints }) },
    setPreview(q) {
      this.preview = q ? q.slice() : null
      // 残影的收掉时机在后端判（要等真机真的站上去），前端只做"用户主动取消"
      if (this.preview) post('/api/preview', { joints: this.preview }).catch(() => {})
      else post('/api/preview', { joints: null }).catch(() => {})
    },
    cancelPreview() {
      this.preview = null
      post('/api/preview', { joints: null }).catch(() => {})
    },
    setPlaybackGhost(joints) { this.playbackGhost = joints ? [...joints] : null },
    async estop() { return await post('/api/estop', {}) },
    async teach(name) {
      const r = await post('/api/points/teach', { name })
      if (r.ok) this.points = (await get('/api/points')).items
      return r
    },
    async addPoint(name, joints) {
      const r = await post('/api/points', { name, joints })
      if (r.ok) this.points = (await get('/api/points')).items
      return r
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
    async stopExec() { await post('/api/points/stop', {}); await post('/api/program/stop', {}) },
    async resume() { await post('/api/points/resume', {}); await post('/api/program/resume', {}) },
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
    async connectReal(ip) { return await post('/api/real/connect', { ip }) },
    async disconnectReal() { return await post('/api/real/disconnect', {}) },
    async askReal(name, params) { return await post('/api/real/ask', { name, params }) },
    async refreshTools() { this.tools = await get('/api/tools'); return this.tools },
    async refreshCameras() {
      const revision = ++cameraRevision
      const r = await post('/api/cameras/scan', {})
      if (revision === cameraRevision) this.cameras = r
      return r
    },
    async openCamera(camera_id, streams) {
      const revision = ++cameraRevision
      const r = await post('/api/cameras/open', { camera_id, streams })
      if (revision === cameraRevision) this.cameras = { ...this.cameras, ...r }
      if (r.ok === false) this.error = r.why || '摄像头开启失败'
      return r
    },
    async closeCamera() {
      const revision = ++cameraRevision
      const r = await post('/api/cameras/close', {})
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
      this.aiHistory.push({ role: 'user', content: text })
      const r = await post('/api/ai/chat', { text, history: this.aiHistory.slice(-8) })
      this.aiHistory.push({ role: 'assistant', content: r.ok ? r.summary : (r.error || '失败') })
      if (r.ok) this.aiPlan = r
      else this.error = r.error || 'AI 调用失败'
      return r
    },
    async aiRun() {
      const r = await post('/api/ai/run', { plan: this.aiPlan.plan, plan_id: this.aiPlan.plan_id })
      if (!r.ok) this.error = r.why || '执行被拒'
      return r
    },
    announce(text) {
      if (!this.snap || !this.snap.settings.tts) return
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
