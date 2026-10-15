// Run with: playwright-cli -s=camera-ui run-code --filename tools/check_camera_layout.js
// Browser-only regression: checks geometry and hit testing; never opens a camera or moves the robot.
async (page) => {
  const errors = []
  const motionRequests = []
  const onError = error => errors.push(error.message)
  const onRequest = request => {
    if (/\/api\/(estop|movej|jog|control|power|ready)(\/|$)/.test(new URL(request.url()).pathname)) {
      motionRequests.push(request.url())
    }
  }
  page.on('pageerror', onError)
  page.on('request', onRequest)
  const results = []
  try {
    const origin = new URL(page.url()).origin
    if (!/^http:\/\/127\.0\.0\.1:(8710|8711)$/.test(origin)) throw new Error('Open a local validation page first')
    await page.goto(origin + '/#/sim')
    await page.reload()
    await page.getByRole('button', { name: '摄像头', exact: true }).click()
    await page.locator('.camera-pop').waitFor({ state: 'visible' })
    await page.waitForFunction(() => !document.querySelector('.camera-head .btn')?.disabled)
    for (const [width, height] of [[1920, 1080], [1366, 768], [1280, 400], [1024, 768], [768, 768], [390, 844]]) {
      await page.setViewportSize({ width, height })
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
      const geometry = await page.evaluate(() => {
        const popup = document.querySelector('.camera-pop')
        const rect = popup.getBoundingClientRect()
        const stage = document.querySelector('.stage').getBoundingClientRect()
        const header = document.querySelector('.top').getBoundingClientRect()
        const footer = document.querySelector('.statusbar').getBoundingClientRect()
        const hits = [40, rect.height - 16].every(offset =>
          popup.contains(document.elementFromPoint(rect.left + rect.width / 2, rect.top + offset)))
        return {
          pass: hits && rect.top > header.bottom && rect.left >= stage.left &&
            rect.right < stage.right && rect.bottom <= Math.min(stage.bottom, footer.top) && rect.height > 70,
          hitTest: hits, panel: rect.toJSON(), stage: stage.toJSON(),
          scrollable: popup.scrollHeight > popup.clientHeight,
        }
      })
      results.push({ width, height, ...geometry })
      if (!geometry.pass) throw new Error('Camera obscured: ' + JSON.stringify(results))
    }
    await page.getByRole('button', { name: '收起摄像头面板' }).click()
    if (await page.locator('.camera-pop').count()) throw new Error('Close button did not close camera panel')
    await page.getByRole('button', { name: '摄像头', exact: true }).click()
    await page.keyboard.press('Escape')
    if (await page.locator('.camera-pop').count()) throw new Error('Escape did not close camera panel')
    if (motionRequests.length) throw new Error('Camera controls sent a robot command: ' + motionRequests.join(', '))
    if (errors.length) throw new Error('Application page errors: ' + errors.join(', '))
    return { results, pageErrors: errors, motionRequests, closeControls: 'passed' }
  } finally {
    page.off('pageerror', onError)
    page.off('request', onRequest)
  }
}
