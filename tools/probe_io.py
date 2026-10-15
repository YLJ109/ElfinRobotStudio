"""只读探测末端/电箱 IO：全部是协议 4.4 章的 Read* 指令，不含任何 Set*/cdsSetIO 写操作。

跑法： python tools/probe_io.py [IP]
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from huayan_panel.core import protocol as P  # noqa: E402
from huayan_panel.core.robot import Link, Perm  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.10"

READS = [
    ("ReadEO", ("0", "0", "1", "2"), "末端输出 EndDO1~3（真空/气阀常接这里）"),
    ("ReadEI", ("0", "0", "1", "2"), "末端输入 EndDI1~3"),
    ("ReadEAI", ("0", "0"), "末端模拟量通道1"),
    ("ReadEAI", ("0", "1"), "末端模拟量通道2"),
    ("ReadBoxDI", tuple(str(i) for i in range(8)), "电箱数字输入 DI0~7"),
    ("ReadBoxDO", tuple(str(i) for i in range(8)), "电箱数字输出 DO0~7"),
    ("ReadBoxCI", tuple(str(i) for i in range(8)), "电箱编码器输入 CI0~7"),
    ("ReadBoxCO", tuple(str(i) for i in range(8)), "电箱编码器输出 CO0~7"),
    ("ReadBoxInfo", ("0",), "电箱信息"),
    ("ReadBrakeStatus", ("0",), "抱闸状态"),
]


def main():
    app = QApplication(sys.argv)
    replies = {}
    link = Link()
    link.grant(Perm.READONLY)
    link.log.connect(lambda k, m: print(f"[{k}] {m}", flush=True))

    def on_reply(r):
        replies.setdefault(r.name, []).append(r)

    link.connect_to(HOST)
    link._cmd.reply.connect(on_reply)

    def spin(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    spin(3)
    print("=" * 74, flush=True)
    ok_n = fail_n = skip_n = 0
    for name, prm, note in READS:
        before = len(replies.get(name, []))
        if not link.ask(name, *prm, why=f"{name} 只读探测"):
            skip_n += 1
            print(f"SKIP    {name:15} {note}（本地联锁拒绝）", flush=True)
            continue
        deadline = time.monotonic() + 3
        got = None
        while time.monotonic() < deadline:
            app.processEvents()
            if len(replies.get(name, [])) > before:
                got = replies[name][-1]
                break
            time.sleep(0.01)
        if got is None:
            fail_n += 1
            print(f"TIMEOUT {name:15} {note}", flush=True)
        elif got.ok:
            ok_n += 1
            print(f"OK      {name:15} -> {got.raw}   # {note}", flush=True)
        else:
            fail_n += 1
            print(f"FAIL    {name:15} -> code={got.err_code} {','.join(got.fields[1:])} "
                  f"[{P.ERROR_HINT.get(got.err_code or 0, '')}]   # {note}", flush=True)
    print("=" * 74, f"\n成功 {ok_n} / 失败 {fail_n} / 本地拒绝 {skip_n}", flush=True)

    s = link.snap
    print(f"\nData Sheet 里的 IO 现值（20Hz 推送，非查询快照）：")
    print(f"  EndDI={s.end_di}  EndDO={s.end_do}  EndButton={s.end_button} "
          f"EnableEndBTN={s.enable_end_btn}  EndAI={s.end_ai}")
    print(f"  BoxDI={s.box_di}  BoxDO={s.box_do}  BoxCI={s.box_ci}  BoxCO={s.box_co} "
          f"Conveyor={s.conveyor}  Encode={s.encode}")

    link.disconnect()
    spin(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
