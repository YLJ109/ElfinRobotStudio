"""用 Tabbit(Chromium) 的 CDP 做真实页面验收：截图 + 点击 + 读取状态。

只读驱动，不改项目源码；不连真机、不开相机采集。
用法：
  python tools/qa_cdp.py shot <name> [url]     截图
  python tools/qa_cdp.py eval "<js>"           在页面求值并打印结果
  python tools/qa_cdp.py click "<selector>"    点击元素（先滚动到可见）
  python tools/qa_cdp.py texts                列出可见按钮/标签文本
"""
import asyncio
import base64
import json
import sys
import urllib.request
from pathlib import Path

import websockets

CDP = "http://127.0.0.1:9333"
OUT = Path(__file__).resolve().parent.parent / "output" / "qa-shots"


def page_ws_url():
    data = json.load(urllib.request.urlopen(CDP + "/json/list", timeout=5))
    pages = [p for p in data if p.get("type") == "page"]
    if not pages:
        raise SystemExit("没有可用的页面标签")
    return pages[0]["webSocketDebuggerUrl"]


class Session:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method, params=None, timeout=60):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr, await_promise=False):
        r = await self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True,
            "awaitPromise": await_promise, "userGesture": True,
        })
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError(d.get("text") or json.dumps(d, ensure_ascii=False)[:300])
        return r.get("result", {}).get("value")

    async def shot(self, name):
        OUT.mkdir(parents=True, exist_ok=True)
        r = await self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        p = OUT / f"{name}.png"
        p.write_bytes(base64.b64decode(r["data"]))
        return p


async def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "shot"
    async with websockets.connect(page_ws_url(), max_size=64 * 1024 * 1024) as ws:
        s = Session(ws)
        await s.call("Page.enable")
        await s.call("Runtime.enable")
        if cmd == "shot":
            name = sys.argv[2] if len(sys.argv) > 2 else "page"
            url = sys.argv[3] if len(sys.argv) > 3 else None
            if url:
                await s.call("Page.navigate", {"url": url})
                await asyncio.sleep(4)
            p = await s.shot(name)
            print(p)
        elif cmd == "eval":
            print(json.dumps(await s.eval(sys.argv[2], await_promise=True), ensure_ascii=False, indent=1))
        elif cmd == "click":
            sel = sys.argv[2]
            js = """(() => {
              const sel = %s;
              const els = [...document.querySelectorAll(sel)];
              const el = els.find(e => e.offsetParent !== null) || els[0];
              if (!el) return {ok:false, why:'not found'};
              el.scrollIntoView({block:'center'});
              el.click();
              return {ok:true, text:(el.innerText||el.value||'').trim().slice(0,40)};
            })()""" % json.dumps(sel)
            print(json.dumps(await s.eval(js), ensure_ascii=False))
            await asyncio.sleep(0.6)
        elif cmd == "tour":
            titles = await s.eval("""[...document.querySelectorAll('nav.rail button.ri[title]')]
                .map(b => b.getAttribute('title'))""")
            print("导航项:", titles)
            for i, t in enumerate(titles, 1):
                sel = 'nav.rail button.ri[title="%s"]' % t
                r = await s.eval("""(() => { const b=document.querySelector(%s); if(!b) return null;
                    b.click(); return true; })()""" % json.dumps(sel))
                if not r:
                    print("跳过", t)
                    continue
                await asyncio.sleep(1.8)
                p = await s.shot(f"tour-{i:02d}-{t}")
                print(f"{t} -> {p.name}")
        elif cmd == "textclick":
            text = sys.argv[2]
            name = sys.argv[3] if len(sys.argv) > 3 else None
            js = """(() => {
              const t = %s;
              const vis = e => e.offsetParent !== null;
              const cands = [...document.querySelectorAll('button,[role=tab],.tab,a,div,span')]
                 .filter(e => vis(e) && (e.innerText||'').trim().includes(t));
              const el = cands.sort((a,b)=>a.children.length-b.children.length)[0];
              if (!el) return {ok:false, why:'text not found'};
              el.scrollIntoView({block:'center'});
              el.click();
              return {ok:true, tag:el.tagName, cls:String(el.className).slice(0,40)};
            })()""" % json.dumps(text, ensure_ascii=False)
            print(json.dumps(await s.eval(js), ensure_ascii=False))
            await asyncio.sleep(1.6)
            if name:
                print(await s.shot(name))
        elif cmd == "texts":
            js = """(() => {
              const vis = e => e.offsetParent !== null && e.getBoundingClientRect().width > 0;
              const pick = sel => [...document.querySelectorAll(sel)].filter(vis)
                 .map(e => (e.innerText || '').trim().replace(/\\s+/g,' ')).filter(t => t && t.length < 30);
              return {buttons: [...new Set(pick('button'))], tabs: [...new Set(pick('[role=tab], .tab, .tab-item'))]};
            })()"""
            print(json.dumps(await s.eval(js), ensure_ascii=False, indent=1))
        else:
            raise SystemExit("未知命令: " + cmd)


asyncio.run(main())
