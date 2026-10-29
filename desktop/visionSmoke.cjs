// Opt-in only. The caller owns launching and shutting down its isolated smoke app.
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { randomUUID } = require('node:crypto')

async function verifyVision({ app, BrowserWindow, window, directory, report, wait }) {
  assert.ok(process.argv.includes('--smoke-vision'), '视觉验收需要显式 --smoke-vision')
  assert.equal(process.env.HUYAN_NO_REAL, '1', '视觉验收仅允许禁用真机的隔离进程')
  const relative = path.relative(path.resolve(directory), path.resolve(app.getPath('userData')))
  assert.ok(relative && relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative), '验收 userData 必须位于输出目录内')
  assert.ok(window && !window.isDestroyed(), '缺少主工作窗口')

  const began = Date.now(), deadline = began + 20000, workDeadline = deadline - 6000
  const checks = report.vision = { completed: false, createdIds: [], cleanupErrors: [] }
  const created = [], initialWindows = new Set(BrowserWindow.getAllWindows())
  let child, safe = false, failure
  function bounded(task, until, label) {
    const remaining = until - Date.now()
    if (remaining <= 0) return Promise.reject(new Error('视觉验收超时：' + label))
    let timer
    return Promise.race([Promise.resolve().then(task), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('视觉验收超时：' + label)), remaining)
    })]).finally(() => clearTimeout(timer))
  }
  const evaluate = (target, script, until = workDeadline) => bounded(() => target.webContents.executeJavaScript(script), until, '页面脚本')
  async function until(check, label) {
    const run = async () => {
      if (wait) return wait(async () => {
        if (Date.now() >= workDeadline) throw new Error('视觉验收超时：' + label)
        return check()
      })
      while (Date.now() < workDeadline) {
        if (await check()) return
        await new Promise(resolve => setTimeout(resolve, 60))
      }
      throw new Error('视觉验收超时：' + label)
    }
    await bounded(run, workDeadline, label)
  }
  // Fetch only fixed same-origin routes in this smoke module; no scene ground truth is read.
  function request(route, payload, end = workDeadline) {
    const timeout = Math.max(1, Math.min(1800, end - Date.now() - 50))
    return evaluate(window, `(async () => {
      const response = await fetch(${JSON.stringify(route)}, {
        cache:'no-store', signal:AbortSignal.timeout(${timeout}),
        ${payload === undefined ? '' : `method:'POST',headers:{'content-type':'application/json'},body:${JSON.stringify(JSON.stringify(payload))},`}
      });
      const value = await response.json();
      if (!response.ok || value.ok === false) throw new Error(value.why || '视觉验收 HTTP '+response.status);
      return value;
    })()`, end)
  }
  function visionWindows() {
    return BrowserWindow.getAllWindows().filter(candidate => !candidate.isDestroyed()
      && new URL(candidate.webContents.getURL() || 'about:blank').pathname === '/vision.html')
  }
  try {
    const state = await request('/api/state')
    assert.ok(state.mode === 'sim' && !state.real, '拒绝修改非纯仿真会话')
    assert.equal(visionWindows().length, 0, '隔离验收开始前不应已有视觉窗口')
    safe = true; checks.simOnly = true
    await evaluate(window, `(() => {
      const button=document.querySelector('nav button[title="视觉"]');
      if(!button)throw new Error('主窗缺少视觉导航');button.click();
    })()`)
    await until(() => evaluate(window, `!!document.querySelector('#industrial-vision-tab-camera')`), '进入 VisionPanel')
    await evaluate(window, `document.querySelector('#industrial-vision-tab-camera').click()`)

    const prefix = 'vision-smoke-' + randomUUID().slice(0, 8)
    const fixtures = [
      { kind: 'box', xyz: [500, 0, 40], size: [80, 80, 80], color: '#ff2020' },
      { kind: 'cylinder', xyz: [650, 100, 30], size: [30, 30, 60], color: '#20ff20' },
      { kind: 'box', xyz: [500, -140, 40], size: [80, 80, 80], color: '#2020ff' },
    ]
    for (const [index, fixture] of fixtures.entries()) {
      const id = prefix + '-' + index
      // Remember the ID before sending, so a lost response cannot orphan a created object.
      created.push(id)
      const value = await request('/api/objects', { ...fixture, id, name: id, collide: false, gravity: false })
      assert.equal(value.object?.id, id)
      checks.createdIds.push(id)
    }
    await evaluate(window, `(() => {
      const select=document.querySelector('#preview-camera-source');
      if(!select || select.disabled)throw new Error('摄像头来源下拉不可用');
      select.value='virtual:end-effector';select.dispatchEvent(new Event('change',{bubbles:true}));
    })()`)
    // 默认镜头沿工具前方，零位朝天不应伪造地面画面；先核验其真实挂载。
    await until(async()=>{const value=await request('/api/vision/workspace/state');return value.result?.source==='virtual' && !!value.result.image?.base64},'默认末端视角出图')
    await until(()=>evaluate(window,`!!document.querySelector('img.virtual-preview')?.getAttribute('src')?.startsWith('data:image/')`),'默认右侧取景出图')
    await fs.writeFile(path.join(directory,'camera-mounted.png'),(await window.webContents.capturePage()).toPNG())
    // Close-up in the actual renderer, not a stand-alone geometry preview.
    // Restore the user's isolated-test view before the remaining workflow.
    const originalView=await evaluate(window,'window.__elfinScene.getView()')
    await evaluate(window,`window.__elfinScene.setView({theta:.8,phi:1.15,r:1,target:[0,1.12,0]})`)
    await evaluate(window,'new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    await fs.writeFile(path.join(directory,'camera-mounted-closeup.png'),(await window.webContents.capturePage()).toPNG())
    await evaluate(window,`window.__elfinScene.setView(${JSON.stringify(originalView)})`)
    // 显式调整实验安装位与方向，使静止零位的测试工件落在视野内。
    await request('/api/vision/workspace/config',{input:'virtual',enabled:true,virtual:{offset_mm:[100,0,50],direction:[400,0,-1000],fov:55,width:640,fps:15}})
    let detected
    await until(async () => {
      const value = await request('/api/vision/workspace/state')
      const result = value.result
      if (value.config?.input !== 'virtual' || !value.config.enabled || value.stale !== false
        || result?.source !== 'virtual' || !result.image?.base64 || !(result.image.width > 0 && result.image.height > 0)) return false
      // Targets must come from the backend image detector, never /api/objects or scene meshes.
      if (!result.objects?.some(object => object.status === 'observed' && object.contour?.length >= 3)) return false
      detected = value; return true
    }, 'GPU 仿真图像经 CPU 识别出目标')
    checks.detection = { source: detected.result.source, stale: detected.stale,
      frame: detected.result.frame_seq, width: detected.result.image.width, height: detected.result.image.height,
      count: detected.result.objects.length, elapsedMs: detected.result.elapsed_ms, virtual: detected.config.virtual }
    await until(()=>evaluate(window,`!!document.querySelector('img.virtual-preview')?.getAttribute('src')?.startsWith('data:image/')`),'右侧共用仿真预览')
    checks.sharedPreview=true

    await evaluate(window, 'window.elfinDesktop.openVisionWorkspace()')
    await until(() => { child = visionWindows()[0]; return !!child }, '打开视觉子窗')
    assert.ok(!initialWindows.has(child), '应新建隔离视觉子窗')
    const preferences = child.webContents.getLastWebPreferences()
    assert.equal(preferences.sandbox, true); assert.equal(preferences.contextIsolation, true)
    assert.equal(preferences.nodeIntegration, false)
    await until(() => evaluate(child, `(() => {
      const svg=document.querySelector('.station-view .image-stage svg'),image=svg?.querySelector('image');
      return !!image?.getAttribute('href')?.startsWith('data:image/') && svg.getBoundingClientRect().width>=500
        && svg.getBoundingClientRect().height>=300 && !!svg.querySelector('polygon');
    })()`), '子窗大图和识别轮廓')
    const renderer = await evaluate(child, `({require:typeof require,process:typeof process,scene:!!window.__elfinScene,controls:document.querySelectorAll('.window-controls button').length})`)
    assert.equal(renderer.require, 'undefined'); assert.equal(renderer.process, 'undefined')
    assert.equal(renderer.scene, false); assert.equal(renderer.controls, 3)
    checks.renderer = renderer
    await evaluate(window, 'window.elfinDesktop.openVisionWorkspace()')
    assert.equal(visionWindows().length, 1); assert.equal(visionWindows()[0], child)
    checks.singleton = true
    for (const [target, filename] of [[window, 'virtual-main.png'], [child, 'vision.png']]) {
      await bounded(async () => {
        const image = await target.webContents.capturePage()
        await fs.writeFile(path.join(directory, filename), image.toPNG())
      }, workDeadline, filename)
    }
    checks.screenshots = ['virtual-main.png', 'vision.png']
    await evaluate(child, 'window.elfinDesktop.close()')
    await until(() => child.isDestroyed(), '子窗独立关闭')
    assert.equal(window.isDestroyed(), false)
    assert.equal(await evaluate(window, 'document.readyState'), 'complete')
    checks.childCloseIndependent = true
  } catch (error) {
    failure = error instanceof Error ? error : new Error(String(error))
    try { checks.diagnostic=await request('/api/vision/workspace/state',undefined,deadline) }catch{}
    try { await fs.writeFile(path.join(directory,'vision-failure.png'),(await window.webContents.capturePage()).toPNG()) }catch{}
  }
  finally {
    // Reserve six seconds for cleanup, including failure and deadline paths.
    if (safe) {
      const cleanup = async (label, action) => {
        try { await action() } catch (error) { checks.cleanupErrors.push(label + ': ' + error.message) }
      }
      await cleanup('停用仿真输入', () => request('/api/vision/workspace/config', { input: 'camera', enabled: false, mapping: false }, deadline))
      for (const id of created) await cleanup('删除 ' + id, () => request('/api/objects/remove', { id }, deadline))
      for (const candidate of visionWindows().filter(candidate => !initialWindows.has(candidate))) {
        await cleanup('关闭验收子窗', () => bounded(() => { candidate.close() }, deadline, '关闭子窗'))
      }
      await cleanup('核对配置复位', async () => {
        const value = await request('/api/vision/workspace/state', undefined, deadline)
        assert.equal(value.config?.input, 'camera'); assert.equal(value.config?.enabled, false)
      })
      checks.cleaned = checks.cleanupErrors.length === 0
    }
    checks.elapsedMs = Date.now() - began
  }
  if (checks.cleanupErrors.length) failure = new AggregateError([...(failure ? [failure] : []), ...checks.cleanupErrors.map(message => new Error(message))], '视觉验收清理失败')
  if (failure) { checks.failure = failure.stack; throw failure }
  checks.completed = true
  return checks
}

module.exports = { verifyVision }
