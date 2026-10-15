"""自动模式下的受控点动验证（用户 2026-10-11 授权：只允许 J6、5% 速度、1~2 度）。

白名单外的指令一条都不发；不改动电源/使能以外的状态，结束后保持现场原状。

跑法： python tools/t_auto_j6.py [IP]
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from huayan_panel.core.robot import Link, Perm  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.10"
OVERRIDE = 0.05
AXIS = 5              # J6
DIST = 1.5            # 1~2 度之间
OTHER_TOL = 0.05

ALLOW = {
    "IsSimulation": lambda a: not a,
    "ReadRobotModel": lambda a: not a,
    "PackageVersion": lambda a: not a,
    "ReadCurFSM": lambda a: a == ["0"],
    "ReadActPos": lambda a: a == ["0"],
    "ReadOverride": lambda a: a == ["0"],
    "ReadBrakeStatus": lambda a: not a,
    "GetErrorCodeStr": lambda a: True,
    "SetOverride": lambda a: a[0] == "0" and abs(float(a[1]) - OVERRIDE) < 1e-6,
    "MoveRelJ": lambda a: (a[0] == "0" and int(a[1]) == AXIS
                           and float(a[3]) == DIST and int(a[2]) in (0, 1)),
    # 只有在“没就绪/没使能”时才用得上；下电/去使能本次不允许发，现场状态由用户掌握
    "Electrify": lambda a: not a,
    "ConnectToBox": lambda a: not a,
    "GrpEnable": lambda a: a == ["0"],
    "GrpStop": lambda a: a == ["0"],
}
FORBIDDEN_NOTE = "本次授权只放开 J6/5%/1~2°，且不许下电、不许去使能"


def install_guard(link: Link):
    origin = link.ask

    def ask(name, *params, need=None, why=None, urgent=False):
        prm = [str(x) for x in params]
        chk = ALLOW.get(name)
        ok = False
        try:
            ok = chk is not None and chk(prm)
        except (ValueError, IndexError, TypeError):
            ok = False
        if not ok:
            print(f"!! 拒发 {name},{','.join(prm)},; —— {FORBIDDEN_NOTE}", flush=True)
            return False
        return origin(name, *prm, need=need if need is not None else Perm.MOTION,
                      why=why or name, urgent=urgent)

    link.ask = ask
    # 产品里的 jog_step/set_override 最终都走 ask，所以它们也被同一道白名单管着


def main():
    app = QApplication(sys.argv)
    link = Link()
    link.log.connect(lambda k, m: print(f"[{k}] {m}", flush=True))
    install_guard(link)
    link.grant(Perm.MOTION)
    link.connect_to(HOST)

    def spin(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def wait_for(pred, timeout, what):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if pred(link.snap):
                return True
            time.sleep(0.03)
        print(f"?? 超时：{what}", flush=True)
        return False

    spin(3)
    s = link.snap
    mode = {"0": "自动", "1": "手动"}.get(str(s.auto_mode), "未知")
    print(f"对端 {s.model} / {s.version} / {s.is_sim}；AutoMode={s.auto_mode}（实测口径={mode}模式）",
          flush=True)
    print(f"起点 FSM={s.robot_state} enabled={s.enabled} moving={s.moving} inpos={s.inpos} "
          f"override={s.override} err=({s.err_axis},{s.err_code})", flush=True)

    if s.is_sim != "真机":
        print("!! 未确认真机，中止", flush=True)
        return 2
    if mode != "自动":
        print(f"!! 现场说是自动，实测 AutoMode={s.auto_mode}，口径不符，先停手核对", flush=True)
        return 2
    if s.err_code or s.moving:
        print("!! 有错误码或在运动中，不发运动指令", flush=True)
        return 1

    # 需要就绪/使能时才推进，且只走白名单
    if (s.robot_state or 0) < 24:
        link.ask("ConnectToBox")
        wait_for(lambda x: (x.robot_state or 0) >= 24, 25, "FSM>=24")
    if not s.enabled:
        link.ask("GrpEnable", 0)
        ok = wait_for(lambda x: x.enabled == 1 and (x.robot_state or 0) >= 33, 20, "已使能")
        print(f"使能：{'OK' if ok else '失败'} FSM={link.snap.robot_state} "
              f"enabled={link.snap.enabled}", flush=True)
        if not ok:
            print("!! 未使能，绝不发运动指令", flush=True)
            return 1

    link.set_override(OVERRIDE)
    ok = wait_for(lambda x: abs((x.override or 0) - OVERRIDE) < 0.005, 8, "速度比=5%")
    print(f"速度比：{'OK' if ok else '未生效'} override={link.snap.override}", flush=True)
    if not ok:
        print("!! 速度比没到 5%，不发运动指令", flush=True)
        return 1

    before = list(link.snap.joints)
    print(f"运动前 {fmt(before)}", flush=True)
    link.jog_step(AXIS, 1, DIST)
    started = wait_for(lambda x: x.moving == 1 or x.inpos == 0, 6, "J6 起动")
    done = wait_for(lambda x: x.moving == 0 and x.inpos == 1, 25, "J6 到位")
    after = list(link.snap.joints)
    d = after[AXIS] - before[AXIS]
    drift = max(abs(after[i] - before[i]) for i in range(6) if i != AXIS)
    print(f"运动后 {fmt(after)}", flush=True)
    print(f"起动={seen(started)} 到位={seen(done)}  J6 变化 {d:+.3f}°（授权 {DIST}°） "
          f"其余五轴最大漂移 {drift:.3f}°", flush=True)

    checks = [
        ("J6 动了且幅度对", abs(abs(d) - DIST) < 0.3),
        ("其余五轴没动", drift < OTHER_TOL),
        ("运动后无错误码", (link.snap.err_code or 0) == 0),
        ("速度比仍为 5%", abs((link.snap.override or 0) - OVERRIDE) < 0.005),
    ]
    for name, v in checks:
        print(f"{'PASS' if v else 'FAIL'}  {name}", flush=True)
    if not all(v for _, v in checks):
        link.ask("GrpStop", 0, need=Perm.MOTION, urgent=True)
        spin(1)

    print(f"终点 FSM={link.snap.robot_state} enabled={link.snap.enabled} "
          f"AutoMode={link.snap.auto_mode} override={link.snap.override}", flush=True)
    link.disconnect()
    spin(1)
    return 0 if all(v for _, v in checks) else 1


def seen(b):
    return "已确认" if b else "未抓到"


def fmt(v):
    return "[" + ", ".join("--" if x is None else f"{x:.3f}" for x in v) + "]"


if __name__ == "__main__":
    sys.exit(main())
