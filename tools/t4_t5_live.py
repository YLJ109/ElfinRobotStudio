"""真机受控试验（用户 2026-10-11 授权）：可通电/下电；运动只允许 J6、速度 5%、幅度 1~2°。

白名单之外的指令本脚本一条都不会发。每一步都用 10004 的 Data Sheet 回读校验，
并且核对"只有 J6 动了，其余五轴没动"。

跑法： python tools/t4_t5_live.py [IP]
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from huayan_panel.core import protocol as P  # noqa: E402
from huayan_panel.core.robot import Link, Perm  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.10"
OVERRIDE = 0.05          # 用户授权：5%
MOVE_AXIS = 5            # 只允许 J6（轴号 5）
MOVE_DIST = 1.5          # 1~2 度之间
OTHER_AXIS_TOL = 0.05    # 其余五轴允许漂移上限（度）

# 指令白名单：名称 -> 允许的参数约束校验函数
ALLOW = {
    "IsSimulation": lambda a: True,
    "ReadRobotModel": lambda a: True,
    "PackageVersion": lambda a: True,
    "ReadCurFSM": lambda a: a == ["0"],
    "ReadActPos": lambda a: a == ["0"],
    "ReadOverride": lambda a: a == ["0"],
    "ReadBrakeStatus": lambda a: True,
    "GetErrorCodeStr": lambda a: True,
    "Electrify": lambda a: True,
    "BlackOut": lambda a: True,
    "ConnectToBox": lambda a: True,
    "GrpEnable": lambda a: a == ["0"],
    "GrpDisable": lambda a: a == ["0"],
    "GrpStop": lambda a: a == ["0"],
    "GrpReset": lambda a: True,
    "SetOverride": lambda a: a[0] == "0" and abs(float(a[1]) - OVERRIDE) < 1e-6,
    "MoveRelJ": lambda a: (a[0] == "0" and int(a[1]) == MOVE_AXIS and float(a[3]) == MOVE_DIST),
}


def install_guard(link: Link):
    """把白名单挂到 Link.ask 上：本脚本走的是产品自己的发送路径，
    但任何一条超授权的指令在出口处被拦下，根本不进socket。"""
    origin = link.ask

    def ask(name, *params, need=None, why=None, urgent=False):
        prm = [str(x) for x in params]
        chk = ALLOW.get(name)
        if chk is None:
            print(f"!! 拒发 {name}：不在本次授权白名单内", flush=True)
            return False
        try:
            ok = chk(prm)
        except (ValueError, IndexError):
            ok = False
        if not ok:
            print(f"!! 拒发 {name},{','.join(prm)},;：超出授权"
                  f"（只允许 J6 / {MOVE_DIST}° / {OVERRIDE:.0%}）", flush=True)
            return False
        # 未显式给权限的，一律按 POWER 门槛走，不会因为漏传而降成只读
        kw = {"need": need if need is not None else Perm.POWER}
        if why:
            kw["why"] = why
        if urgent:
            kw["urgent"] = urgent
        return origin(name, *prm, **kw)

    link.ask = ask


log_lines = []


def main():
    app = QApplication(sys.argv)
    link = Link()
    link.log.connect(lambda k, m: (log_lines.append((k, m)),
                                  print(f"[{k}] {m}", flush=True)))
    link.connect_to(HOST)

    def spin(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def wait_state(pred, timeout, what):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if pred(link.snap):
                return True
            time.sleep(0.05)
        print(f"?? 等待超时：{what}（当前 {brief(link.snap)}）", flush=True)
        return False

    spin(3)
    s = link.snap
    print("=" * 70, f"\n起点：{brief(s)}", flush=True)
    if s.is_sim != "真机":
        print("!! 对端不是真机（IsSimulation 未确认），中止", flush=True)
        return 2

    install_guard(link)
    link.grant(Perm.POWER)

    # ---- 1. 下电 → 上电 往返（用户授权：可通电可关电）---------------------
    link.ask("BlackOut")
    ok = wait_state(lambda x: (x.robot_state or 0) < 14, 15, "下电后 FSM<14")
    print(f"下电 BlackOut：{'OK' if ok else '未确认'} FSM={link.snap.robot_state}", flush=True)

    link.ask("Electrify")
    ok = wait_state(lambda x: (x.robot_state or 0) >= 14, 20, "上电后 FSM>=14")
    print(f"上电 Electrify：{'OK' if ok else '未确认'} FSM={link.snap.robot_state}", flush=True)
    if not ok:
        print("!! 上电没成功，后续使能/运动全部取消", flush=True)
        return 1

    # ---- 2. 初始化 + 使能 --------------------------------------------------
    link.ask("ConnectToBox")
    ok = wait_state(lambda x: (x.robot_state or 0) >= 24, 25, "ConnectToBox 后 FSM>=24")
    print(f"初始化：{'OK' if ok else '超时'} FSM={link.snap.robot_state}", flush=True)
    if not ok:
        print("!! 未到 FSM24，不尝试使能，中止", flush=True)
        return 1

    link.grant(Perm.MOTION)
    link.set_override(OVERRIDE)
    ok = wait_state(lambda x: abs((x.override or 0) - OVERRIDE) < 0.005, 8, "速度比=5%")
    print(f"速度比：{'OK' if ok else '未生效'} override={link.snap.override}", flush=True)

    link.ask("GrpEnable", 0)
    ok = wait_state(lambda x: (x.robot_state or 0) >= 33 and x.enabled == 1, 20, "使能 FSM>=33")
    print(f"使能：{'OK' if ok else '超时'} FSM={link.snap.robot_state} "
          f"enabled={link.snap.enabled} brake={link.snap.brake}", flush=True)
    if not ok:
        print("!! 使能未成功，绝不发运动指令，中止", flush=True)
        return 1

    # ---- 3. 只允许 J6 微动 -------------------------------------------------
    before = list(link.snap.joints)
    print(f"\n运动前关节角 {fmt(before)}", flush=True)
    j6_idx = MOVE_AXIS
    allowed_target = before[j6_idx] + MOVE_DIST
    if not in_limits(j6_idx, allowed_target):
        print(f"!! J6 目标 {allowed_target:.3f} 超出限位，取消运动", flush=True)
        return 1

    link.jog_step(MOVE_AXIS, 1, MOVE_DIST)
    started = wait_state(lambda x: x.moving == 1 or x.inpos == 0, 5, "J6 起动")
    print(f"起动确认：{'看到 moving/inpos 变化' if started else '没抓到起动瞬间（可能太快），继续等到位'}",
          flush=True)
    ok = wait_state(lambda x: x.moving == 0 and x.inpos == 1, 25, "运动结束（moving=0 且到位）")
    after = list(link.snap.joints)
    d6 = after[j6_idx] - before[j6_idx]
    drift = [abs(after[i] - before[i]) for i in range(6) if i != j6_idx]
    print(f"运动后关节角 {fmt(after)}", flush=True)
    print(f"J6 实际变化 {d6:+.3f}°（授权 {MOVE_DIST}°）；其余轴最大漂移 {max(drift):.3f}°", flush=True)
    verdict = []
    verdict.append(("J6 到位", abs(abs(d6) - MOVE_DIST) < 0.3))
    verdict.append(("其余轴未动", max(drift) < OTHER_AXIS_TOL))
    for name, v in verdict:
        print(f"{'PASS' if v else 'FAIL'}  {name}", flush=True)

    # ---- 4. 去使能 → 下电，回到安全态 --------------------------------------
    link.grant(Perm.POWER)
    link.ask("GrpDisable", 0, need=Perm.POWER)
    ok = wait_state(lambda x: x.enabled == 0, 15, "去使能")
    print(f"去使能：{'OK' if ok else '超时'} enabled={link.snap.enabled} brake={link.snap.brake}",
          flush=True)

    link.ask("BlackOut")
    ok = wait_state(lambda x: (x.robot_state or 0) < 14, 15, "下电后 FSM<14")
    print(f"下电：{'OK' if ok else '超时'} FSM={link.snap.robot_state}", flush=True)

    print("=" * 70, f"\n终点：{brief(link.snap)}", flush=True)
    all_pass = all(v for _, v in verdict) and ok
    link.disconnect()
    spin(1)
    print("结论：", "受控往返全部通过" if all_pass else "有项未通过，见上方 FAIL/超时", flush=True)
    return 0 if all_pass else 1


def in_limits(axis, target):
    lo, hi = P.JOINT_LIMITS[axis]
    return lo <= target <= hi


def fmt(v):
    return "[" + ", ".join("--" if x is None else f"{x:.3f}" for x in v) + "]"


def brief(s):
    return (f"FSM={s.robot_state} enabled={s.enabled} moving={s.moving} inpos={s.inpos} "
            f"override={s.override} err=({s.err_axis},{s.err_code}) brake={s.brake} "
            f"joints={fmt(s.joints)}")


if __name__ == "__main__":
    sys.exit(main())
