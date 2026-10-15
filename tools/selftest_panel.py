"""面板端到端自测（只打仿真控制器 127.0.0.1，不碰真机）。

跑法：
    先  python tools/mock_server.py
    再  set QT_QPA_PLATFORM=offscreen && python tools/selftest_panel.py

判定项：链路通、Data Sheet 有帧率、就绪流程把 FSM 推到 33 并使能、
点动 1° 后关节角真的变了、急停回执 OK、IsSimulation=1 被面板识别为仿真。
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from huayan_panel.core.robot import Perm  # noqa: E402
from huayan_panel.ui import lamps  # noqa: E402
from huayan_panel.ui.main_window import MainWindow  # noqa: E402

import app as APP  # noqa: E402  用和正式入口一样的主题，截图才是真实长相

HOST = "127.0.0.1"
results: list[tuple[str, bool, str]] = []


def spin(app, ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(app, pred, timeout_s, step=0.05):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(step)
    return False


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def main():
    app = QApplication(sys.argv)
    APP.apply_theme(app)
    APP.apply_theme(app)
    win = MainWindow()
    win.show()
    link = win.link

    win.ip.setText(HOST)
    win._connect()
    spin(app, 500)

    ok = wait_until(app, lambda: link.snap.stream_up, 8)
    check("Data Sheet 链路 (10004)", ok, f"rate={link.snap.rate_hz:.1f}Hz")
    ok = wait_until(app, lambda: link.snap.cmd_up, 8)
    check("命令链路 (10003)", ok)

    ok = wait_until(app, lambda: link.snap.is_sim == "仿真", 6)
    check("IsSimulation 被识别为仿真（面板必须标注）", ok, f"is_sim={link.snap.is_sim!r}")
    check("窗口标题已标注仿真", "仿真" in win.windowTitle(), win.windowTitle())
    check("型号来自控制器自报", bool(link.snap.model), f"model={link.snap.model}")

    # 上一轮可能把仿真留在已使能状态，先归零，保证"初始 FSM 未上电"这条判据有意义
    link.grant(Perm.POWER)
    if link.snap.robot_state != 7:
        link.ask("GrpDisable", 0, need=Perm.POWER)
        spin(app, 400)
        link.ask("BlackOut", need=Perm.POWER)
        wait_until(app, lambda: link.snap.robot_state == 7, 8)

    link.grant(Perm.MOTION)
    spin(app, 200)
    check("运动许可已授予", link.perm == Perm.MOTION, f"perm={link.perm.name}")

    s = link.snap
    j_before = list(s.joints)
    fsm_before = s.robot_state
    check("初始 FSM 为未上电", fsm_before == 7, f"fsm={fsm_before}")

    link.begin_ready(then_enable=True)
    ok = wait_until(app, lambda: link.snap.robot_state >= 33 and link.snap.enabled == 1, 25)
    s = link.snap
    check("就绪流程 Electrify→ConnectToBox→GrpEnable", ok,
          f"fsm={s.robot_state} enabled={s.enabled}")

    check("使能后抱闸状态非全 0", any(s.brake), f"brake={s.brake}")

    axis = 0
    link.jog_step(axis, 1, 1.0)
    ok = wait_until(app, lambda: abs((link.snap.joints[axis] or 0) - j_before[axis]) > 0.5, 10)
    check("点动 J1 +1.0° 真的动了", ok,
          f"{j_before[axis]:.3f} -> {link.snap.joints[axis]}")

    link.jog_hold(axis, 0, True)
    spin(app, 400)
    held = link.snap.joints[axis]
    link.jog_hold(axis, 0, False)
    spin(app, 400)
    stopped = link.snap.joints[axis]
    check("长点动松开后停住", abs((held or 0) - (stopped or 0)) < 0.6,
          f"{held} -> {stopped}")

    link.set_override(0.05)
    ok = wait_until(app, lambda: abs((link.snap.override or 0) - 0.05) < 0.01, 6)
    check("速度比下发 5%", ok, f"override={link.snap.override}")

    link.emergency_stop()
    ok = wait_until(app, lambda: link.snap.moving == 0, 6)
    check("急停后 moving=0", ok)

    logs = []
    link.log.connect(lambda k, m: logs.append(m))

    link.ask("SetEndDO", 0, 0, 1, need=Perm.MOTION, why="仿真验证 IO 写入")
    ok = wait_until(app, lambda: (link.snap.end_do or [0] * 4)[0] == 1, 5)
    check("SetEndDO 置1 生效（仿真）", ok, f"end_do={link.snap.end_do}")
    link.ask("SetEndDO", 0, 0, 0, need=Perm.MOTION, why="仿真验证 IO 复位")
    ok = wait_until(app, lambda: (link.snap.end_do or [1] * 4)[0] == 0, 5)
    check("SetEndDO 置0 生效（仿真）", ok, f"end_do={link.snap.end_do}")

    win._io_refresh_read()
    spin(app, 1500)
    ok = any("ReadEO,OK" in m for m in logs)
    check("ReadEO 即时复核有回执", ok, next((m for m in logs if "ReadEO" in m), "--"))

    bad_before = len([m for m in logs if "已拒绝" in m])
    link.ask("SetEndDO", 0, 7, 1, need=Perm.MOTION, why="越界参数应被拒")
    spin(app, 300)
    ok = len([m for m in logs if "已拒绝" in m]) > bad_before
    check("参数越界在本地被拒（不发给控制器）", ok,
          next((m for m in logs if "已拒绝 SetEndDO" in m), "没拦住"))

    rules = lamps.rules(link.snap, link.perm, False)
    reds = [n for n, lv, d, src in rules if lv == lamps.RED]
    check("灯带出规则且无空值", len(rules) >= 8, f"{len(rules)} 条, 红灯={reds}")

    win.grab().save("tools/selftest_panel.png")
    print("screenshot -> tools/selftest_panel.png")

    win.close()
    link.disconnect()
    spin(app, 300)

    failed = [n for n, ok, _ in results if not ok]
    print("=" * 60)
    print(f"{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("未通过：", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
