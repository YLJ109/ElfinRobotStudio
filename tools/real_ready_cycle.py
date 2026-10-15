"""真机闭环实测：一键就绪 → 取消就绪(去使能+下电) → 再从当前状态一键就绪。
只发状态指令，不发任何运动指令。结果写到 tools/_real_cycle.txt（避开控制台转码）。
跑法： python tools/real_ready_cycle.py
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
BASE = "http://127.0.0.1:8710"
OUT = Path(__file__).with_name("_real_cycle.txt")
lines = []


def log(s):
    lines.append(str(s))
    print(s)


def req(path, body=None):
    if body is None:
        with urllib.request.urlopen(BASE + path, timeout=25) as r:
            return json.loads(r.read().decode())
    rq = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=60) as r:
        return json.loads(r.read().decode())


def state():
    return req("/api/state")


def snap(tag):
    s = state()
    r = s.get("real") or {}
    log(f"  [{tag}] FSM={r.get('fsm')} enabled={r.get('enabled')} err={r.get('err_code')} "
        f"48V={r.get('volt48_in')} override={r.get('override')} "
        f"stream={r.get('stream_up')} cmd={r.get('cmd_up')}")
    return s


log("=== 连接 ===")
req("/api/real/connect", {"ip": "192.168.0.10"})
time.sleep(3.5)
s = snap("连上")
if not (s["real"] or {}).get("cmd_up"):
    log("命令通道没起来，停手，不发任何写指令")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    sys.exit(1)

req("/api/perm", {"level": 2})
log("\n=== 1) 一键就绪（上电+初始化+使能）===")
log("  POST /api/ready -> " + json.dumps(req("/api/ready", {"then_enable": True}), ensure_ascii=False))
for _ in range(30):
    time.sleep(1.0)
    s = state()
    r = s["real"] or {}
    if not s["ready"]["running"]:
        break
log(f"  步骤判定：{json.dumps(s['ready']['steps'], ensure_ascii=False)}")
if s["ready"]["why"]:
    log(f"  why: {s['ready']['why']}")
snap("就绪后")

log("\n=== 2) 取消就绪（应自动去使能 + 下电）===")
r = req("/api/ready/cancel", {})
log("  -> " + json.dumps(r, ensure_ascii=False))
time.sleep(2.0)
snap("收回后")

log("\n=== 3) 下电之后再来一次一键就绪（用户报失败的那一步）===")
req("/api/perm", {"level": 2})
log("  POST /api/ready -> " + json.dumps(req("/api/ready", {"then_enable": True}), ensure_ascii=False))
for _ in range(45):
    time.sleep(1.0)
    s = state()
    if not s["ready"]["running"]:
        break
log(f"  步骤判定：{json.dumps(s['ready']['steps'], ensure_ascii=False)}")
if s["ready"]["why"]:
    log(f"  why: {s['ready']['why']}")
snap("再就绪后")

log("\n=== 4) 收回并降回只读（不把机器留在使能态）===")
r = req("/api/ready/cancel", {})
log("  -> " + json.dumps(r, ensure_ascii=False))
req("/api/perm", {"level": 0})
time.sleep(2.0)
snap("收尾")
log("  许可已降回只读；未发送任何运动指令")
OUT.write_text("\n".join(lines), encoding="utf-8")
