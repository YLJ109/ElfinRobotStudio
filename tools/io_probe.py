"""末端 IO 逐路探棒：写一路 DO，同时回读 EO / EI / 两路模拟量，看有没有反馈跟着翻。

用途是**取证接线映射**：只有当某路 DO 通的时候 DI 或模拟量跟着变，
才有资格说"这路是吸气阀、那路是真空反馈"。没变化就说明这路上什么都没接。
跑法： python tools/io_probe.py            # 三路各通断一次
      python tools/io_probe.py 1          # 只试 bit1
"""

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8710"
HOLD_S = 2.0


def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode())


def ask(name, params):
    return post("/api/real/ask", {"name": name, "params": params})


def snapshot(tag):
    st = get("/api/state")
    r = st.get("real") or {}
    eo = ask("ReadEO", [0, 0, 1, 2]).get("fields")
    ei = ask("ReadEI", [0, 0, 1, 2]).get("fields")
    a0 = ask("ReadEAI", [0, 0]).get("fields")
    a1 = ask("ReadEAI", [0, 1]).get("fields")
    print(f"  [{tag}] EO={eo}  EI={ei}  AI0={a0}  AI1={a1}  "
          f"| sheet do={r.get('end_do')} di={r.get('end_di')} ai={r.get('end_ai')}")
    return {"eo": eo, "ei": ei, "ai": [a0, a1]}


def main():
    bits = [int(x) for x in sys.argv[1:]] or [0, 1, 2]
    st = get("/api/state")
    r = st.get("real") or {}
    if st.get("mode") != "real" or not r.get("cmd_up"):
        print("没连真机，不做任何写入。")
        return 1
    if st.get("perm", 0) < 2:
        print("许可不足（要运动许可才能写 IO），不做了。")
        return 1
    print(f"FSM={r.get('fsm')} enabled={r.get('enabled')} err={r.get('err_code')}")
    base = snapshot("基线")

    for bit in bits:
        print(f"\n--- bit{bit}（界面上标 DO{bit + 1}）---")
        on = post("/api/io/enddo", {"bit": bit, "val": 1})
        rep = on.get("reply") or {}
        print(f"  SetEndDO,0,{bit},1 -> ok={rep.get('ok')} fields={rep.get('fields')} "
              f"err={rep.get('err_code')} rtt={rep.get('rtt_ms')}ms")
        if not rep.get("ok"):
            snapshot("被拒")
            continue
        time.sleep(HOLD_S)
        during = snapshot(f"通 {HOLD_S}s 后")
        off = post("/api/io/enddo", {"bit": bit, "val": 0})
        rep2 = off.get("reply") or {}
        print(f"  SetEndDO,0,{bit},0 -> ok={rep2.get('ok')} err={rep2.get('err_code')}")
        time.sleep(0.6)
        after = snapshot("断后")

        flips = [i for i, (a, b) in enumerate(zip(base["ei"] or [], during["ei"] or []))
                 if a != b]
        ai_delta = []
        for i, (a, b) in enumerate(zip(base["ai"], during["ai"])):
            try:
                ai_delta.append(round(float((b or ["0"])[0]) - float((a or ["0"])[0]), 3))
            except (TypeError, ValueError):
                ai_delta.append(None)
        print(f"  => 反馈判定：DO 回读={during['eo']}  DI 翻转位={flips or '无'}  "
              f"模拟量变化={ai_delta}")
        if not flips and all(abs(v or 0) < 0.15 for v in ai_delta):
            print("     这一路通了之后**没有任何可读反馈** —— 不能凭它下结论说接了什么，"
                  "只能靠现场听/摸。")

    print("\n收尾复核：", snapshot("全部归零"))


if __name__ == "__main__":
    sys.exit(main())
