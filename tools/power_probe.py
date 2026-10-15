"""取证：这台控制器到底接受哪一步状态迁移。
每次只发一条状态指令，发之前/之后各读一次 FSM，被拒就停下换下一条。
不发任何运动指令。结果写 tools/_power_probe.txt。
跑法： python tools/power_probe.py
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8710"
OUT = Path(__file__).with_name("_power_probe.txt")
log_lines = []


def log(s):
    log_lines.append(str(s))


def post(path, body=None):
    if body is None:
        with urllib.request.urlopen(BASE + path, timeout=25) as r:
            return json.loads(r.read().decode())
    rq = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=60) as r:
        return json.loads(r.read().decode())


def rd():
    r = post("/api/real/ask", {"name": "ReadCurFSM", "params": [0]})
    return r.get("fields")


def fsm_now():
    s = post("/api/state")
    r = s.get("real") or {}
    return r, s


r, s = fsm_now()
log(f"起点：DataSheet FSM={r.get('fsm')} enabled={r.get('enabled')} 48V={r.get('volt48_in')} "
    f"cmd_up={r.get('cmd_up')} stream_up={r.get('stream_up')}")
log(f"      ReadCurFSM 同步查询 = {rd()}")

# 只读指令先确认链路真的活着（被拒时能区分"状态不允许"和"链路死了"）
for name in ("IsSimulation", "ReadRobotModel", "PackageVersion"):
    x = post("/api/real/ask", {"name": name, "params": []})
    log(f"  只读 {name}: ok={x.get('ok')} fields={x.get('fields')} err={x.get('err_code')}")

CANDIDATES = [
    ("ConnectToBox", [], "初始化/连电箱"),
    ("Electrify", [], "上电"),
]
for name, params, label in CANDIDATES:
    r, s = fsm_now()
    log(f"\n试 {name}（{label}）@ FSM={r.get('fsm')} cmd_up={r.get('cmd_up')}")
    if not r.get("cmd_up"):
        log("  命令通道没起来，跳过（不算被拒）")
        continue
    x = post("/api/real/ask", {"name": name, "params": params})
    log(f"  回执 ok={x.get('ok')} fields={x.get('fields')} err={x.get('err_code')} "
        f"hint={x.get('hint')} timeout={x.get('timeout')} sent={x.get('sent')}")
    for wait in (1, 3, 6):
        time.sleep(wait if wait == 1 else 2.5)
        r, s = fsm_now()
        log(f"  +{wait}s FSM={r.get('fsm')} enabled={r.get('enabled')} 48V={r.get('volt48_in')} "
            f"cmd_up={r.get('cmd_up')} err={r.get('err_code')}")
        if (r.get("fsm") or 0) >= 24:
            break

r, s = fsm_now()
log(f"\n终点：FSM={r.get('fsm')} enabled={r.get('enabled')} 48V={r.get('volt48_in')} "
    f"cmd_up={r.get('cmd_up')}")
log("未发送任何运动/使能指令。")
OUT.write_text("\n".join(log_lines), encoding="utf-8")
print("\n".join(log_lines).encode("unicode_escape").decode()[:200])
