// 正确：只记录与画面验收有关的指定偏好，不遍历或导出浏览器存储。
globalThis.reviewBaseline = await page.evaluate(() => {
  const keys = ['huayan.view.workspace.v1', 'huayan.side.width', 'huayan.side.open', 'huayan.visualModel'];
  return {hash: location.hash, preferences: Object.fromEntries(keys.map(key =>
    [key, sessionStorage.getItem(key) ?? localStorage.getItem(key)]))};
});
return globalThis.reviewBaseline;
