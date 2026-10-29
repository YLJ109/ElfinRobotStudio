const { app, BrowserWindow, ipcMain, session, dialog, nativeImage, screen } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const { pathToFileURL } = require('node:url')
const { BackendSession } = require('./backend.cjs')

app.setName('JiuShuRobotStudio')
app.setAppUserModelId('com.elfinrobotstudio.desktop')
const smokeArgument = process.argv.find(value => value.startsWith('--smoke-output='))
if (smokeArgument) {
  const directory = path.resolve(smokeArgument.slice('--smoke-output='.length))
  fs.mkdirSync(path.join(directory, 'profile'), { recursive: true })
  app.setPath('userData', path.join(directory, 'profile'))
  process.env.HUYAN_NO_REAL = '1'
  require('./smoke.cjs').install({ app, ipcMain, BrowserWindow, directory })
}
let mainWindow, visionWindow, splash, backend, origin = '', quitting = false, closing = false, starting = false, bootTimer
const root = path.resolve(__dirname, '..')
const preload = path.join(__dirname, 'preload.cjs')
const splashFile = path.join(__dirname, 'splash', 'index.html')
const splashURL = pathToFileURL(splashFile).href
const iconFile = app.isPackaged ? path.join(process.resourcesPath, 'branding', 'logo.png')
  : path.join(root, 'web', 'public', 'elfin-logo-arm-v1.png')
const icon = nativeImage.createFromPath(iconFile)
const state = window => ({ maximized: !!window?.isMaximized(), focused: !!window?.isFocused() })
function senderWindow(event) {
  if (!event.senderFrame || event.senderFrame !== event.sender.mainFrame) return null
  try {
    const url = new URL(event.senderFrame.url)
    if (event.sender === splash?.webContents && url.href === splashURL) return splash
    if (url.origin !== origin) return null
    if (event.sender === mainWindow?.webContents && ['/', '/index.html'].includes(url.pathname)) return mainWindow
    if (event.sender === visionWindow?.webContents && url.pathname === '/vision.html') return visionWindow
  } catch { /* Malformed or detached frames are never trusted. */ }
  return null
}
const trusted = event => !!senderWindow(event)
function guard(event) {
  const window = senderWindow(event)
  if (!window) throw new Error('非法桌面调用来源')
  return window
}
function progress(phase, message, error = false) {
  if (splash && !splash.isDestroyed()) splash.webContents.send('desktop:progress', { phase, message, error })
}
function protect(window) {
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => {
    try {
      const target = new URL(url)
      const allowed = window === splash ? url === splashURL : target.origin === origin
        && (window === visionWindow ? target.pathname === '/vision.html' : ['/', '/index.html'].includes(target.pathname))
      if (!allowed) event.preventDefault()
    } catch { event.preventDefault() }
  })
  window.webContents.on('will-attach-webview', event => event.preventDefault())
  window.on('close', event => {
    if (quitting || window === visionWindow) return
    event.preventDefault(); void closeApplication()
  })
}
function bindWindowState(window) {
  for (const event of ['maximize', 'unmaximize', 'focus', 'blur']) {
    window.on(event, () => {
      if (!window.isDestroyed() && !window.webContents.isDestroyed()) window.webContents.send('window:state-changed', state(window))
    })
  }
}
async function openVisionWorkspace() {
  if (closing || quitting) throw new Error('工作站正在退出')
  if (visionWindow && !visionWindow.isDestroyed()) {
    if (visionWindow.isMinimized()) visionWindow.restore()
    visionWindow.show(); visionWindow.focus(); return
  }
  const display = screen.getDisplayMatching(mainWindow.getBounds()).workAreaSize
  const window = new BrowserWindow({ width: Math.min(1760, display.width), height: Math.min(1080, display.height),
    minWidth: 1100, minHeight: 700, frame: false, thickFrame: true, roundedCorners: true,
    hasShadow: true, resizable: true, maximizable: true, modal: false, backgroundColor: '#070c13',
    title: '玖枢 · 视觉工作站', icon, show: false, autoHideMenuBar: true,
    webPreferences: { preload, contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false } })
  visionWindow = window
  protect(window); bindWindowState(window)
  window.once('closed', () => { if (visionWindow === window) visionWindow = null })
  window.once('ready-to-show', () => { if (!window.isDestroyed() && !closing && !quitting) { window.show(); window.focus() } })
  try { await window.loadURL(origin + '/vision.html') }
  catch (error) { if (!window.isDestroyed()) window.destroy(); throw error }
}
async function closeApplication() {
  if (closing || quitting) return
  closing = true
  try {
    await backend?.close()
    if (visionWindow && !visionWindow.isDestroyed()) visionWindow.destroy()
    quitting = true; clearTimeout(bootTimer); app.quit()
  } catch (error) {
    progress('error', error.message, true)
    await dialog.showMessageBox(mainWindow || splash, { type: 'error', title: '暂时无法退出', message: error.message,
      detail: '窗口保持打开，请确认停止状态后重试。', buttons: ['返回应用'] })
  } finally { closing = false }
}
async function start() {
  if (starting) return
  starting = true
  try {
    const base = await backend.start()
    if (closing || quitting) return
    origin = new URL(base).origin
    if (mainWindow && !mainWindow.isDestroyed()) { mainWindow.destroy(); mainWindow = null }
    const display = screen.getPrimaryDisplay().workAreaSize
    mainWindow = new BrowserWindow({ width: Math.min(1600, display.width), height: Math.min(1000, display.height),
      minWidth: 1024, minHeight: 680, frame: false, thickFrame: true, roundedCorners: true,
      hasShadow: true, resizable: true, maximizable: true, backgroundColor: '#070c13',
      title: '玖枢机器人工作站', icon, show: false, autoHideMenuBar: true,
      webPreferences: { preload, contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false } })
    protect(mainWindow)
    bindWindowState(mainWindow)
    mainWindow.webContents.on('render-process-gone', () => { splash?.show(); progress('error', '界面进程意外退出，请重试', true) })
    mainWindow.webContents.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
      if (isMainFrame) progress('error', `界面加载失败：${description}（${code}）`, true)
    })
    progress('frontend', '正在加载工作区')
    clearTimeout(bootTimer)
    bootTimer = setTimeout(() => progress('error', '工作区初始化超时，请重试或检查日志', true), 30000)
    await mainWindow.loadURL(base + '/#/sim')
  } catch (error) { progress('error', error.message, true) }
  finally { starting = false }
}

if (!app.requestSingleInstanceLock() || process.argv.includes('--quit')) app.quit()
else {
  app.on('second-instance', (_event, args) => {
    if (args.includes('--quit')) { void closeApplication(); return }
    const window = mainWindow?.isVisible() ? mainWindow : splash
    if (window?.isMinimized()) window.restore()
    window?.show(); window?.focus()
  })
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); void closeApplication() } })
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_web, _permission, callback) => callback(false))
    session.defaultSession.setPermissionCheckHandler(() => false)
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      const headers = { ...details.responseHeaders }
      if (origin && details.url.startsWith(origin + '/') && details.resourceType === 'mainFrame') {
        headers['Content-Security-Policy'] = [`default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; worker-src 'self' blob:; connect-src 'self' ws://${new URL(origin).host}; media-src 'self' blob:; object-src 'none'; frame-src 'none'; base-uri 'self'`]
      }
      callback({ responseHeaders: headers })
    })
    ipcMain.handle('window:minimize', event => { guard(event).minimize() })
    ipcMain.handle('window:maximize', event => { const window = guard(event); if (window.isMaximized()) window.unmaximize(); else window.maximize(); return state(window) })
    ipcMain.handle('window:state', event => state(guard(event)))
    ipcMain.handle('window:close', event => {
      const window = guard(event)
      // 先交付IPC回执再销毁子窗，避免关闭调用成为已销毁frame的未处理拒绝。
      if (window === visionWindow) setTimeout(() => { if (!window.isDestroyed()) window.close() }, 50)
      else void closeApplication()
    })
    ipcMain.handle('vision:open', event => { if (guard(event) !== mainWindow) throw new Error('仅主工作站可打开视觉窗口'); return openVisionWorkspace() })
    ipcMain.handle('desktop:retry', event => { if (guard(event) !== splash) throw new Error('仅启动窗口可重试'); if (!starting) void start() })
    ipcMain.on('desktop:boot', (event, value) => {
      if (!trusted(event) || event.sender !== mainWindow?.webContents || !value || typeof value !== 'object') return
      if (typeof value.message === 'string') progress(String(value.phase).slice(0, 40), value.message.slice(0, 240), value.error === true)
      if (value.ready === true && value.phase === 'ready') {
        clearTimeout(bootTimer); mainWindow.show(); mainWindow.focus(); splash?.destroy(); splash = null
      }
    })
    splash = new BrowserWindow({ width: 520, height: 330, frame: false, thickFrame: true,
      roundedCorners: true, hasShadow: true, resizable: false, maximizable: false, icon,
      backgroundColor: '#080f19', show: false, webPreferences: { preload, contextIsolation: true, nodeIntegration: false, sandbox: true } })
    protect(splash)
    splash.once('ready-to-show', () => splash.show())
    backend = new BackendSession({ root, resources: process.resourcesPath, userData: app.getPath('userData'), packaged: app.isPackaged, onStage: progress })
    await splash.loadFile(splashFile)
    await start()
  }).catch(error => { console.error(error); quitting = true; app.exit(1) })
}
