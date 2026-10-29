const { spawn } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')
const crypto = require('node:crypto')
const { BoundedLog } = require('./logSink.cjs')

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function requestJson(base, route, { timeout = 2000, method = 'GET', key } = {}) {
  const response = await fetch(base + route, { method, signal: AbortSignal.timeout(timeout),
    headers: key ? { 'x-elfin-desktop-key': key } : {} })
  const value = await response.json()
  if (!response.ok) throw new Error(value.why || `本地服务响应 ${response.status}`)
  return value
}

class BackendSession {
  constructor({ root, resources, userData, packaged, onStage = () => {} }) {
    Object.assign(this, { root, resources, userData, packaged, onStage })
    this.child = null; this.base = null; this.startingBase = null; this.owned = false; this.failure = ''; this.key = crypto.randomBytes(32).toString('hex')
  }
  async start() {
    if (this.base) return this.base
    this.onStage('backend', '正在启动本地后端')
    // 正确：上一次等待超时不代表子进程已退出，重试复用同一启动事务。
    if (this.child && this.child.exitCode === null) return this.waitReady()
    this.failure = ''; this.startingBase = null; this.childClosed = false; this.lastWaitError = ''
    const isolatedSmoke = process.argv.some(value => value.startsWith('--smoke-output='))
    if (!this.packaged && !isolatedSmoke) {
      try {
        const state = await requestJson('http://127.0.0.1:8710', '/api/state', { timeout: 700 })
        // 正确：源码开发可复用兼容服务，关闭时不结束非本窗口启动的进程。
        if (state.session_id && state.joints?.names?.length === 6 && state.hand_follow) {
          this.base = 'http://127.0.0.1:8710'
          this.onStage('backend', '本地后端已就绪')
          return this.base
        }
      } catch {}
    }
    const executable = this.packaged ? path.join(this.resources, 'backend', 'ElfinBackend.exe')
      : path.join(this.root, '.venv', 'Scripts', 'python.exe')
    if (!fs.existsSync(executable)) throw new Error('缺少后端运行文件，请重新构建桌面应用')
    const args = this.packaged ? [] : ['-B', path.join(this.root, 'desktop', 'backend_entry.py')]
    const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8',
      ELFIN_DESKTOP_KEY: this.key, ELFIN_DESKTOP: '1' }
    if (this.packaged || isolatedSmoke) env.ELFIN_DATA_DIR = path.join(this.userData, 'data')
    fs.mkdirSync(path.join(this.userData, 'logs'), { recursive: true })
    const log = new BoundedLog(path.join(this.userData, 'logs', 'backend.log'), {
      onError:error=>{this.logFailure=error.message}
    })
    this.child = spawn(executable, args, { cwd: this.packaged ? this.resources : this.root,
      env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
    this.owned = true
    let pending = ''
    this.child.stdout.on('data', chunk => {
      log.write(chunk); pending += chunk.toString('utf8')
      let end
      while ((end = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, end); pending = pending.slice(end + 1)
        try {
          const event = JSON.parse(line)
          if (event.type === 'desktop-listening' && Number.isInteger(event.port) && event.port > 0 && event.port < 65536)
            this.startingBase = `http://127.0.0.1:${event.port}`
        } catch { /* 后端普通日志不属于启动协议。 */ }
      }
      if (pending.length > 65536) pending = pending.slice(-4096)
    })
    this.child.stderr.on('data', chunk => log.write(chunk))
    this.child.on('error', error => { this.failure = error.message })
    this.child.once('close', () => { this.childClosed = true;void log.end() })
    this.child.once('exit', code => { this.failure = `后端已退出（${code}），请查看本地日志` })
    return this.waitReady()
  }
  async waitReady() {
    const deadline = Date.now() + 30000
    while (Date.now() < deadline) {
      if (this.failure) throw new Error(this.failure)
      if (this.startingBase) {
        try {
          const status = await requestJson(this.startingBase, '/api/desktop/status', { key: this.key, timeout: 500 })
          // 正确：PyInstaller引导器PID与Python工作进程PID可不同。
          // 私有凭据、子进程输出的端口及就绪响应共同绑定本次服务。
          if (status.ready === true && Number.isInteger(status.pid) && status.pid > 0) {
            this.backendPid = status.pid
            this.base = this.startingBase; this.onStage('backend', '本地后端已就绪'); return this.base
          }
        } catch (error) { this.lastWaitError = error.message }
      }
      await delay(40)
    }
    throw new Error('后端启动超时：' + (this.lastWaitError || '尚未收到就绪状态'))
  }
  async close() {
    // 正确：启动阶段尚未接受界面命令；已有监听端口则走同一受认证停止流程。
    if (!this.base && this.owned && this.startingBase) this.base = this.startingBase
    if (!this.base) {
      if (this.owned && this.child && this.child.exitCode === null) {
        // 正确：spawn失败没有PID；kill返回false也可能是进程已退出，不能把窗口锁死。
        if (this.childClosed || !Number.isInteger(this.child.pid)) return
        const signalled = this.child.kill()
        if (!signalled) {
          try { process.kill(this.child.pid, 0) }
          catch (error) { if (error.code === 'ESRCH') return; throw error }
        }
        const deadline = Date.now() + 5000
        while (!this.childClosed && this.child.exitCode === null && Date.now() < deadline) await delay(40)
        if (!this.childClosed && this.child.exitCode === null) throw new Error('后端启动正在取消，请稍后重试')
      }
      return
    }
    const result = await requestJson(this.base, this.owned ? '/api/desktop/shutdown' : '/api/points/stop',
      { method: 'POST', key: this.owned ? this.key : undefined, timeout: 10000 })
    if (result.ok !== true || result.settled === false) throw new Error(result.why || '尚未确认停止，请检查机器人状态')
    if (this.owned && this.child?.exitCode === null) {
      const deadline = Date.now() + 10000
      while (this.child.exitCode === null && Date.now() < deadline) await delay(40)
      if (this.child.exitCode === null) throw new Error('后端正在退出，请稍后重试关闭')
    }
    this.base = null
  }
}
module.exports = { BackendSession, requestJson }
