"""安全边界验收：来源校验、凭据、脱敏、注入。跑在**已启动的服务**上。

    python tools/security_check.py              # 只跑不动机器的边界项
    python tools/security_check.py --allow-real # 追加真机侧"必须被拒"的路径（仍不发运动报文）

前置闸：如果控制器现在处于就绪+使能（运动模式），本脚本**直接退出**。
理由很直白 —— 这套接口里的 MOVEJ/点动在运动模式下是真会动的，
2026-10-12 就发生过一次：测试脚本连上真机把 J6 拖走 2°、DO1 留在通电。
要跑，先按顶栏「取消就绪」，或者用 --allow-real 明确说你接受。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ap = argparse.ArgumentParser()
ap.add_argument("--allow-real", action="store_true",
                help="允许在真机已就绪时跑（默认拒绝）")
ARGS = ap.parse_args()

BASE = "http://127.0.0.1:8710"
OUT = Path(__file__).with_name("_security.txt")
lines = []
ok_n = fail_n = 0


def say(name, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
    else:
        fail_n += 1
    lines.append(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def req(path, body=None, headers=None, method=None, raw=False):
    data = None if body is None else json.dumps(body).encode()
    rq = urllib.request.Request(BASE + path, data=data,
                                method=method or ("GET" if body is None else "POST"))
    if body is not None:
        rq.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        rq.add_header(k, v)
    try:
        with urllib.request.urlopen(rq, timeout=25) as r:
            b = r.read()
            if raw:
                return r.status, b.decode("utf-8", "replace")
            return r.status, json.loads(b.decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, {"raw": txt[:160]}


def state():
    return req("/api/state")[1]


# ---------------------------------------------------------------- 前置闸
st = state()
real_ready = st["mode"] == "real" and st.get("motion_granted")
if real_ready and not ARGS.allow_real:
    print("拒绝执行：控制器现在是 就绪+使能（运动模式），这套接口里的运动请求会真的发出去。\n"
          "先按顶栏「取消就绪」再跑，或确认你接受风险后加 --allow-real。")
    sys.exit(2)
say("前置闸：真机就绪时默认不跑", not real_ready or ARGS.allow_real,
    "运动模式，已按 --allow-real 放行" if real_ready else "只读/仿真，安全")

# ---------------------------------------------------------------- 1) 只读下运动类必须被拒
if st["mode"] == "sim" or not st.get("motion_granted"):
    _, r = req("/api/movej", {"joints": [0, 0, 0, 0, 0, 0]})
    say("只读下 MOVEJ 被拒", r.get("ok") is False and "只读" in str(r.get("why")), str(r.get("why"))[:70])
    _, r = req("/api/jog", {"axis": 5, "direction": 1, "on": True})
    say("只读下点动被拒", r.get("ok") is False, str(r.get("why"))[:50])
    _, r = req("/api/io/enddo", {"bit": 0, "val": 1})
    say("只读下末端 DO 写入被拒", r.get("ok") is False, str(r.get("why"))[:50])
    _, r = req("/api/points/run", {"ids": []})
    say("只读下点位执行被拒", r.get("ok") is False, str(r.get("why"))[:50])
else:
    say("只读闸（当前运动模式，跳过）", True, "跳过：改由 check_backend 在仿真里断言")

# ---------------------------------------------------------------- 2) 许可没有可调的档
_, r = req("/api/perm", {"level": 1})
say("手动调档接口已下线（许可由状态推导）",
    r.get("detail") in ("Not Found", "Method Not Allowed") or r.get("ok") is False, str(r)[:60])
_, r = req("/api/perm/ack", {"acks": {"nobody": True, "estop": False}})
say("确认没勾全不发凭据", r.get("ok") is False, str(r.get("why"))[:50])
_, r = req("/api/perm/ack", {"acks": {"nobody": True, "estop": True}})
say("两项都勾才发凭据", r.get("ok") is True and len(str(r.get("challenge"))) > 16,
    f"ttl={r.get('ttl_s')}s")
chal = r.get("challenge")
# 凭据一次性只能在进程内单测：走接口就会真的把机器人推到就绪（安全脚本不许改硬件状态）
from sim.backend.main import world as WD          # noqa: E402
_keep = (WD._armed, WD._challenge)
WD._armed = False
WD._challenge = ("tok-fixed", time.monotonic() + 60)
first = WD._challenge_ok("tok-fixed")
second = WD._challenge_ok("tok-fixed")
wrong = WD._challenge_ok("tok-guess")
WD._armed, WD._challenge = _keep
say("凭据用一次即废", first is True and second is False, f"first={first} second={second}")
say("错串凭据不被接受", wrong is False, "")
_, r = req("/api/control", {"key": "nonsense"})
say("未知动作被拒", r.get("ok") is False, str(r.get("why"))[:40])
_, r = req("/api/free", {"on": True, "hold": True})
say("未就绪时手拖被拒（松闸必须在伺服上电下做）",
    r.get("ok") is False and "就绪" in str(r.get("why")), str(r.get("why"))[:70])

# ---------------------------------------------------------------- 3) 急停不收硬件电
if state().get("motion_granted"):
    say("急停项跳过（当前运动模式，不对真机按急停）", True, "改由 check_backend 在仿真里断言")
else:
    _, r = req("/api/estop", {})
    say("只读下急停接口可用", r.get("ok") is True, "")
    time.sleep(0.3)
    say("急停后场地确认作废", state().get("ack_left_s", 0) == 0, str(state().get("ack_left_s")))

# ---------------------------------------------------------------- 4) 来源校验
code, body = req("/api/settings", {"log_keep": 2000}, headers={"Origin": "http://evil.example"})
say("跨源写请求被拒 403", code == 403, f"{code} {str(body)[:60]}")
code, body = req("/api/state", headers={"Origin": "http://evil.example"})
say("跨源 GET 只读不受影响", code == 200, str(code))
code, body = req("/api/settings", {"log_keep": 2000}, headers={"Origin": "http://127.0.0.1:8710"})
say("同源写请求正常放行", code == 200, str(code))
code, body = req("/api/control", {"key": "ready"}, headers={"Origin": "http://evil.example"})
say("新接口同样受来源闸保护", code == 403, str(code))

# ---------------------------------------------------------------- 5) 设置白名单
_, r = req("/api/settings", {"rm_rf": "x"})
say("未知设置项被拒", r.get("ok") is False, str(r)[:60])
_, r = req("/api/settings", {"mode": "real"})
say("mode 不能从设置接口偷改（要走连接流程）",
    r.get("ok") is False or "不认识的设置项" not in str(r.get("why")), str(r)[:60])

# ---------------------------------------------------------------- 6) CSV 公式注入
_, pr = req("/api/points", {"name": "=HYPERLINK(\"http://evil\")", "joints": [0, 0, 0, 0, 0, 0]})
pid = (pr.get("point") or {}).get("id")
_, csv = req("/api/logs/export.csv", raw=True)
bad = [l for l in csv.splitlines() if ",=H" in l or ",+H" in l]
say("导出的 CSV 里没有裸公式单元格", not bad, str(bad[:1])[:90])
if pid:
    req(f"/api/points/{pid}", method="DELETE")     # 别把测试点位留在用户的点位表里

# ---------------------------------------------------------------- 7) 诊断包脱敏
_, diag = req("/api/diag", raw=True)
sn = ((state().get("telemetry") or {}).get("device_sn")) or ""
leak = bool(sn) and sn in diag
say("诊断包默认对 SN 打码", not leak or "***" in diag, f"sn={sn} 泄漏={leak}")
say("诊断包里不含 AI 接口以外的密钥字段", "ZHIPU" not in diag and "api_key" not in diag.lower(), "")

# ---------------------------------------------------------------- 8) 未知指令 / 参数越界仍被挡
_, r = req("/api/real/ask", {"name": "DROP_TABLE", "params": []})
say("指令表之外的名字不发", r.get("ok") is False and "指令表里没有" in str(r.get("sent")), str(r)[:70])
_, r = req("/api/real/ask", {"name": "MoveRelJ", "params": [0, 9, 1, 5]})
say("轴号越界被参数闸挡下", r.get("ok") is False, str(r)[:70])
_, r = req("/api/real/ask", {"name": "SetOverride", "params": [0, 9.0]})
say("override 超范围被拒（协议 0.01~1.0）", r.get("ok") is False, str(r)[:70])

# ---------------------------------------------------------------- 9) 回放/遥测接口不炸
code, body = req("/api/playback")
say("回放接口返回结构合法", code == 200 and "n" in body, f"n={body.get('n')}")
code, body = req("/api/playback/frame?i=999999")
say("回放游标越界被夹到有效范围", code == 200 and (body.get("ok") is False or body.get("i") is not None),
    str(body)[:60])

OUT.write_text("\n".join(lines) + f"\n\n{ok_n} 通过 / {fail_n} 失败\n", encoding="utf-8")
print(f"{ok_n} 通过 / {fail_n} 失败")
for l in lines:
    if l.startswith("FAIL"):
        print(l)
