"""真机状态机全流程验证：按前端会走的那条路一步步走，不许绕。

走的是 /api/control 这个唯一入口（不是直接调 power），所以它同时验证了
"状态推导 → 按钮标签 → 凭据闸 → 指令下发 → FSM 落定"整条链。

用法：
    .venv/Scripts/python.exe tools/ctrl_cycle.py            # 完整循环 + J6 微动
    .venv/Scripts/python.exe tools/ctrl_cycle.py --no-move  # 只走到就绪，不动机器
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8710"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hy import Guard          # noqa: E402  我自己的 5% 硬闸，不碰网页侧

G = Guard()


def call(path, payload=None, timeout=40):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def say(step, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {step}: {msg}", flush=True)


def primary(d):
    c = (d.get("ctrl") or {}).get("primary") or {}
    return c.get("key"), c.get("label"), (d.get("real") or {})


def wait_settle(want=None, limit=25.0):
    """等 FSM 连续稳定（或到指定位）；返回最后读到的 FSM。"""
    end = time.monotonic() + limit
    last, stable, seen = None, 0.0, None
    while time.monotonic() < end:
        d = call("/api/state")
        fsm = (d.get("real") or {}).get("fsm")
        now = time.monotonic()
        if fsm != last:
            last, stable = fsm, now
        if want is not None and fsm == want and now - stable >= 1.5:
            return fsm
        if want is None and last is not None and now - stable >= 2.0:
            return last
        time.sleep(0.4)
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-move", action="store_true")
    ap.add_argument("--j6-deg", type=float, default=2.0)
    a = ap.parse_args()

    d = call("/api/state")
    key, label, real = primary(d)
    say("起点", f"FSM={real.get('fsm')} enabled={real.get('enabled')} "
               f"48V={real.get('volt48_in')} 主按钮={label}({key})")
    if key != "connect":
        say("起点", "链路已在，开始循环")
    else:
        r = call("/api/control", {"key": "connect"})
        say("连接", json.dumps(r, ensure_ascii=False)[:200])
        time.sleep(2.5)

    # ---- 1. 收回：取消就绪（去使能 → 下电）
    d = call("/api/state")
    key = (d.get("ctrl") or {}).get("primary", {}).get("key")
    if key == "cancel_ready":
        r = call("/api/control", {"key": "cancel_ready"})
        say("取消就绪", ("成功 " if r.get("ok") else "失败 ") +
            json.dumps(r.get("steps") or r.get("why"), ensure_ascii=False)[:220])
        fsm = wait_settle()
        say("落定", f"FSM={fsm}")
    else:
        say("取消就绪", f"当前主按钮是 {key}，跳过这一步")

    # ---- 2. 换确认凭据（真机侧推进状态要它）
    ack = call("/api/perm/ack", {"acks": {"nobody": True, "estop": True}})
    if not ack.get("ok"):
        say("确认", "被拒：" + str(ack.get("why")))
        return 1
    chal = ack["challenge"]
    say("确认", f"凭据已签发（{ack['ttl_s']}s 一次性）")

    # ---- 3. 上电
    r = call("/api/control", {"key": "electrify", "challenge": chal})
    say("上电", json.dumps(r, ensure_ascii=False)[:260])
    fsm = wait_settle(14)
    say("上电后", f"FSM={fsm} 48V={ (call('/api/state').get('real') or {}).get('volt48_in') }")

    # ---- 4. 启动主控（14→24，本轮新发现的那一步）
    r = call("/api/control", {"key": "start_master"})
    say("启动主控", json.dumps(r, ensure_ascii=False)[:260])
    fsm = wait_settle(24, 30)
    say("主控后", f"FSM={fsm}")

    # ---- 5. 一键就绪（走泵）
    r = call("/api/control", {"key": "ready"})
    say("就绪", json.dumps(r, ensure_ascii=False)[:200])
    for _ in range(30):
        d = call("/api/state")
        rd = d.get("ready") or {}
        if rd.get("running"):
            say("泵", " → ".join(f"{s['label']}:{s['at']}" for s in rd.get("steps", [])))
        if not rd.get("running") and rd.get("steps"):
            break
        if (d.get("real") or {}).get("fsm") == 33:
            break
        time.sleep(1.0)
    d = call("/api/state")
    real = d.get("real") or {}
    key, label, _ = primary(d)
    say("就绪后", f"FSM={real.get('fsm')} enabled={real.get('enabled')} "
                 f"perm={d.get('perm_name')} 主按钮={label}")

    if a.no_move:
        say("结束", "--no-move：不下发运动指令")
        return 0

    # ---- 6. J6 微动（授权范围内：5% 速度、±2°）
    if not d.get("motion_granted"):
        say("运动", "没有运动许可（未就绪+使能），按规则不发运动指令")
        return 1
    G.set_speed(5)      # 只允许 5%：超过就抛，不发 SetOverride
    q0 = list((d.get("joints") or {}).get("q") or [])
    say("运动前", f"J6={q0[5]:.3f}° override={real.get('override')}")
    r = G.ask("MoveRelJ", [0, 5, 1, a.j6_deg])
    say("J6 +" + str(a.j6_deg), ("OK " if r.get("ok") else "Fail ") +
        f"rtt={r.get('rtt_ms')}ms code={r.get('err_code')}")
    time.sleep(3.0)
    q1 = list((call("/api/state").get("joints") or {}).get("q") or [])
    say("到位", f"J6={q1[5]:.3f}° 实际移动 {q1[5] - q0[5]:+.3f}° 其余轴漂移 "
               f"{[round(b - c, 3) for b, c in zip(q1, q0)]}")
    r = G.ask("MoveRelJ", [0, 5, 0, a.j6_deg])
    say("J6 退回", ("OK " if r.get("ok") else "Fail ") + f"rtt={r.get('rtt_ms')}ms")
    time.sleep(3.0)
    q2 = list((call("/api/state").get("joints") or {}).get("q") or [])
    say("回到原位", f"J6={q2[5]:.3f}° 与起点差 {q2[5] - q0[5]:+.3f}°")
    return 0


if __name__ == "__main__":
    sys.exit(main())
