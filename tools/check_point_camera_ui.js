// Run only against the isolated UI server. Camera frames remain in browser memory.
async (page) => {
  if (!page.url().startsWith('http://127.0.0.1:8711/')) throw new Error('Requires isolated UI server')
  const results = [], errors = [], motions = []
  const api = 'http://127.0.0.1:8711'
  let createdId = null
  const assert = (ok, text) => { if (!ok) throw new Error(text); results.push(text) }
  const onError = e => errors.push(e.message)
  const onRequest = r => { if (/\/api\/(estop|movej|jog|control|power|ready|program\/start)(\/|$)/.test(new URL(r.url()).pathname)) motions.push(r.url()) }
  page.on('pageerror', onError); page.on('request', onRequest)
  try {
    const state = await (await page.request.get(api + '/api/state')).json(); assert(state.mode === 'sim', 'Offline simulation verified')
    const saved = await (await page.request.get(api + '/api/points')).json()
    for (const point of saved.items.filter(p => ['取料点','取料工位'].includes(p.name))) await page.request.delete(api + '/api/points/' + point.id)
    const q = [10,-20,30,40,-50,60]
    const create = await (await page.request.post(api + '/api/points', { data: { name: '取料点', joints: q, source: 'sim' } })).json()
    assert(create.ok, 'Saved point created in isolated data')
    const p = create.point
    createdId = p.id
    await page.setViewportSize({width:1366,height:900})
    await page.goto('http://127.0.0.1:8711/#/work/program'); await page.reload()
    const editor = page.getByRole('textbox', {name:'程序代码',exact:true})
    await editor.waitFor()
    await editor.fill(`直线运动(${p.code})`)
    const ghost = page.getByRole('button', {name:`预览点位 ${p.code} 残影`,exact:true})
    await ghost.waitFor(); assert(await ghost.count() === 1, 'Inline ghost widget appears for point')
    assert((await ghost.getAttribute('title')).includes('取料点') && (await ghost.getAttribute('title')).includes('J6=60.000°'), 'Inline tooltip uses stored name and all joints')
    await ghost.click()
    await page.waitForFunction(code => document.querySelector('.point-detail')?.textContent.includes(code), p.code)
    const detail = await page.locator('.point-detail').innerText()
    assert(detail.includes('取料点') && q.every((v,i) => detail.includes(`J${i+1} ${v.toFixed(3)}°`)), 'Selected point displays name and six stored joints')
    assert(p.tcp.xyz_mm.every((v,i) => detail.includes(`${['X','Y','Z'][i]} ${Number(v).toFixed(3)}mm`)), 'Selected point displays stored TCP coordinates')
    await page.waitForFunction(async q => { const s = await (await fetch('/api/state')).json(); return JSON.stringify(s.preview) === JSON.stringify(q) }, q)
    const preview = await (await page.request.get(api + '/api/state')).json(); assert(JSON.stringify(preview.joints.q) === JSON.stringify(state.joints.q), 'Ghost preview does not move robot')
    await editor.fill(`关节移动(${p.code.slice(0,1)}`); await page.keyboard.press('Control+Space')
    await page.locator('.cm-completionLabel').filter({hasText:'取料点'}).waitFor()
    assert((await page.locator('.cm-completionDetail').allTextContents()).some(t => t.includes('60.000')), 'Point completion displays real stored joint values')
    await page.keyboard.press('Tab'); assert((await editor.innerText()).includes(`关节移动(${p.code}`), 'Point completion inserts stable reference')
    await page.keyboard.press('Escape')
    await editor.fill('# P1\n字符串 = "不是点P1"')
    assert(await page.locator('.point-ghost-button').count() === 0, 'No ghost controls inside comments or unrelated strings')
    await editor.fill('定义 作业(目标):\n    关节移动(目标)\n次数 = {"n": 2}\n循环 i 在 范围(次数["n"]):\n    作业("取料点")')
    await page.locator('.program-panel').getByRole('button',{name:'解析',exact:true}).click()
    await page.getByText('语法检查通过 · 步数由运行时控制流决定',{exact:true}).waitFor()
    assert(true, 'Nested Chinese script reaches backend parser')
    const rename = await (await page.request.patch(api + '/api/points/' + p.id, {data:{name:'取料工位'}})).json()
    assert(rename.ok && rename.point.code === p.code && JSON.stringify(rename.point.joints) === JSON.stringify(q), 'Rename preserves number and stored coordinates')
    await page.reload(); await editor.waitFor(); await editor.fill(`关节移动("取料工位")`)
    await page.getByRole('button',{name:`预览点位 ${p.code} 残影`,exact:true}).waitFor()
    assert(true, 'Renamed point reference has inline preview')
    await page.locator('.camera-trigger').click()
    await page.locator('.device').first().waitFor()
    const devices = await (await page.request.get(api + '/api/cameras')).json()
    assert(devices.devices.every(d => d.number > 0 && d.name), 'Camera list contains actual device numbers and names')
    await page.locator('.device').first().click(); await page.getByRole('button',{name:'开启摄像头',exact:true}).click()
    await page.waitForFunction(() => {const img = document.querySelector('.camera-frame img'); return img?.naturalWidth > 0}, null, {timeout:12000})
    assert(await page.locator('.camera-frame img').evaluate(img => img.naturalWidth === 640 && img.naturalHeight === 480), 'Browser decodes actual Chicony camera frame')
    await page.getByRole('button',{name:'关闭摄像头',exact:true}).click()
    await page.waitForFunction(() => !document.querySelector('.camera-frame img'))
    assert((await (await page.request.get(api + '/api/cameras')).json()).active === null, 'Close releases device and removes stale preview')
    assert(errors.length === 0, 'No browser exceptions: ' + errors.join(', '))
    assert(motions.length === 0, 'No robot motion requests')
    return {checks: results.length, results}
  } finally {
    if (createdId) await page.request.delete(api + '/api/points/' + createdId)
    await page.request.post(api + '/api/cameras/close', {data:{}})
    page.off('pageerror',onError); page.off('request',onRequest)
  }
}
