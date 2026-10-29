// 正确：只读页面，不点击控制按钮、不打开相机，不读取凭据存储。
globalThis.reviewErrors = globalThis.reviewErrors || [];
page.on('pageerror', error => globalThis.reviewErrors.push(error.message));
return await tabbit.observe({frames: 'none', maxChars: 6500});
