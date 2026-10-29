// 正确：只查看本地界面，不启动跟随、摄像头或真机。
await page.goto('http://127.0.0.1:8710/#/hand-follow', {waitUntil:'domcontentloaded'});
return await tabbit.observe({frames:'none',maxChars:5000});
