"""首动延迟实测 v2：区分「点击→有反馈」与「点击→真实运动」。

用持久 HTTP 连接高频轮询，避免测量本身引入延迟。
"""
import asyncio
import http.client
import json
import sys
import time
import urllib.request

import websockets

CDP_PORT = 9333
API = ("127.0.0.1", 8710)

_conn = None


def state():
    global _conn
    if _conn is None:
        _conn = http.client.HTTPConnection(*API, timeout=5)
    try:
        _conn.request("GET", "/api/state")
        return json.loads(_conn.getresponse().read())
    except Exception:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
        raise


def q_of(s):
    j = s.get("joints") or {}
    return [round(float(v), 3) for v in (j.get("q") if isinstance(j, dict) else j or [])]


def page_ws():
    data = json.load(urllib.request.urlopen(
        f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5))
    return [p for p in data if p.get("type") == "page"][0]["webSocketDebuggerUrl"]


async def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "执行"
    async with websockets.connect(page_ws(), max_size=32 * 1024 * 1024) as ws:
        n = 0

        async def call(method, params=None):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == n:
                    return msg.get("result", {})

        await call("Runtime.enable")
        before_state = state()
        q0 = q_of(before_state)
        run0 = (before_state.get("exec") or {}).get("run_id")
        js = """(() => {
          const t=%s; const b=[...document.querySelectorAll('button')]
            .filter(e=>e.offsetParent && (e.innerText||'').trim()===t)[0];
          if(!b) return {ok:false, why:'missing'};
          if(b.disabled) return {ok:false, why:'disabled'};
          b.click(); return {ok:true};
        })()""" % json.dumps(label, ensure_ascii=False)
        r = await call("Runtime.evaluate", {"expression": js, "returnByValue": True, "userGesture": True})
        if not (r.get("result", {}).get("value") or {}).get("ok"):
            print("点击失败:", r.get("result", {}).get("value"))
            return
        t0 = time.monotonic()

        t_feedback = None
        t_move = None
        probe = []
        deadline = t0 + 45
        while time.monotonic() < deadline:
            s = state()
            dt = (time.monotonic() - t0) * 1000
            e = s.get("exec") or {}
            q = q_of(s)
            submitted = bool(e.get("run_id")) and e.get("run_id") != run0
            if t_feedback is None and submitted:
                t_feedback = dt
            moved = q0 and any(abs(a - b) > 0.05 for a, b in zip(q, q0))
            if len(probe) < 40:
                probe.append([round(dt), e.get("state"), round(max(
                    [abs(a - b) for a, b in zip(q, q0)] or [0]), 3)])
            if moved:
                t_move = dt
                break
            if submitted and e.get("state") in ("finished", "failed", "aborted"):
                break
            await asyncio.sleep(0.02)

        print(json.dumps({
            "button": label,
            "feedback_ms": None if t_feedback is None else round(t_feedback),
            "first_move_ms": None if t_move is None else round(t_move),
            "limit_deg_at_first_move": round(max([abs(a - b) for a, b in zip(q_of(state()), q0)] or [0]), 3),
            "probe_head": probe[:12],
        }, ensure_ascii=False, indent=1))


asyncio.run(main())
