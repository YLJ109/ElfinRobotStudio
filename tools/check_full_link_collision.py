"""SIM04「全连杆防穿透整机闭环」有限复现脚本（仅本轮使用）。

只读仓库、只写临时目录（World(data_dir=...) 指向 tempfile），不连真机、不起服务。
运行：
    backend/.venv/Scripts/python.exe -B tools/check_full_link_collision.py            # 改动后
    backend/.venv/Scripts/python.exe -B tools/check_full_link_collision.py --baseline # 改动前口径
--baseline 把"摆放物接触口径"短路成只查末端（等于本轮改动前的行为），用来证明
正例 1 在改动前确实是漏的。退出码 0 表示全部通过。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="elfin-fulllink-check-"))
os.environ["ELFIN_DATA_DIR"] = str(TMP)
os.environ["HUYAN_NO_REAL"] = "1"

import backend.core.world as world_module          # noqa: E402
from backend.core import planning                  # noqa: E402
from backend.core.safety import tool_capsules, object_capsules  # noqa: E402
from backend.core.world import World               # noqa: E402

RESULTS = []

Q0 = [0.0, 0.0, 90.0, 0.0, 0.0, 0.0]
Q1 = [0.0, 0.0, 40.0, 0.0, 0.0, 0.0]
BIG = [180.0, 120.0, 40.0, 90.0, 60.0, 120.0]


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + ("   | " + detail if detail else ""))


def new_world(tag, mode="sim"):
    w = World(data_dir=TMP / tag)
    w.settings["mode"] = mode
    return w


def open_space(w, mode="sim"):
    """只有仿真/真机口径差别，没有围栏与地面干扰的空间。"""
    w.settings["mode"] = mode
    w.settings["fence"] = {"x": [-3000.0, 3000.0], "y": [-3000.0, 3000.0],
                           "z": [0.0, 3000.0], "enabled": True}
    w.settings["ground_enabled"] = False
    return w


def sphere(name, xyz, diameter=20.0):
    return {"name": name, "kind": "sphere", "xyz": list(xyz),
            "size": [diameter] * 3, "scale": 1, "collide": True}


# 小臂（elfin_link4）在 J3 90°→40° 中段扫过、两端与末端都干净的一颗小球。
# 由 tools 里的搜索得到：k=15..85 只有 link4 为负，末端三根胶囊始终 > 2mm。
FOREARM_BALL = sphere("小臂障碍球", [253.3, -93.7, 718.0], 20.0)
# 末端（elfin_end_link）只在中段穿透、两个端点全干净的小球：证明预检不是只看端点。
MID_BALL = sphere("末端障碍球", [590.0, 0.0, 772.0], 16.0)
# 手掌微步用：J3=90° 时 link4 余量 0.8mm，J3=89° 时 -4.4mm，J3=91° 时 +11mm。
MICRO_BALL = sphere("微步障碍球", [296.2, -53.7, 719.9], 20.0)


def links_of(model, obj, q):
    from backend.core.safety import _object_signed_gap
    return {cap["link"]: round(_object_signed_gap(cap, obj), 2)
            for cap in model.capsules(q, conservative=True)}


# --------------------------------------------------------------- 1 小臂扫过物体
def case_forearm_sweep():
    w = open_space(new_world("w-sweep"))
    model, (lo, up) = w.motion_model(), w.limits()
    settings = dict(w.settings)
    print("  起点全连杆间隙:", links_of(model, FOREARM_BALL, Q0))
    print("  中段全连杆间隙:", links_of(model, FOREARM_BALL, [0.0, 0.0, 65.0, 0.0, 0.0, 0.0]))
    print("  终点全连杆间隙:", links_of(model, FOREARM_BALL, Q1))
    rep = planning.inspect_path(model, Q0, Q1, settings, lo, up, [FOREARM_BALL])
    links = sorted({i.get("link") for issue in rep["issues"] for i in issue["collisions"]})
    print("  预检 risk=%s samples=%s min_clearance=%s 命中连杆=%s"
          % (rep["risk_detected"], rep["samples"], rep["min_clearance_mm"], links))
    check("1 仿真：末端未穿透但小臂扫过物体 → 预检拦截", rep["risk_detected"] is True,
          "risk=" + str(rep["risk_detected"]))
    check("1b 拦截理由来自小臂（非末端）", bool(links) and all("link4" in l for l in links),
          str(links))


# ------------------------------------------------------------ 2 接近余量不拦截
def case_sim_proximity():
    w = new_world("w-prox")
    model, (lo, up) = w.motion_model(), w.limits()
    q = [0.0, 0.0, 90.0, 0.0, 0.0, 0.0]
    w.settings["fence"] = {"x": [-2000.0, model.flange_pose(q)["xyz_mm"][0] + 59.0],
                           "y": [-3000.0, 3000.0], "z": [0.0, 3000.0], "enabled": True}
    w.settings["ground_enabled"] = False
    w.sim.q = list(q)
    rep = planning.inspect_path(model, q, q, dict(w.settings), lo, up, [])
    print("  仿真接近余量: risk=%s min_clearance=%s issues=%d"
          % (rep["risk_detected"], rep["min_clearance_mm"], len(rep["issues"])))
    check("2 仿真：接近余量但未穿透 → 放行", rep["risk_detected"] is False)


# ---------------------------------------------------------------- 3 真机穿透拒绝
def case_real_penetration():
    # 真机门槛只由 settings["mode"] 决定；几何用同一台仿真模型，避免真机配置下
    # motion_model() 的工具绑定不同（那样测到的就不是门槛而是模型差异）。
    w = open_space(new_world("w-realpen"))
    model, (lo, up) = w.motion_model(), w.limits()
    q = list(Q0)
    pose = model.flange_pose(q)["xyz_mm"]
    fence = {"x": [-2000.0, pose[0] + 45.0], "y": [-3000.0, 3000.0],
             "z": [0.0, 3000.0], "enabled": True}
    real = dict(w.settings, mode="real", fence=fence)
    rep = planning.inspect_path(model, q, q, real, lo, up, [])
    print("  真机穿透: risk=%s min_clearance=%s"
          % (rep["risk_detected"], rep["min_clearance_mm"]))
    check("3 真机：真实穿透 → 拒绝", rep["risk_detected"] is True)

    rep2 = planning.inspect_path(model, Q0, Q1, real, lo, up, [FOREARM_BALL])
    print("  真机 + 小臂障碍: risk=%s" % rep2["risk_detected"])
    check("3b 真机：小臂扫过物体同样拒绝", rep2["risk_detected"] is True)


# --------------------------------------------------- 4 已相交后加深 / 穿到另一侧
def case_deepen_and_cross():
    w = open_space(new_world("w-deepen"))
    model, (lo, up) = w.motion_model(), w.limits()
    settings = dict(w.settings)
    start = [0.0, 0.0, 75.0, 0.0, 0.0, 0.0]      # link4 已相交
    deeper = [0.0, 0.0, 60.0, 0.0, 0.0, 0.0]
    away = [0.0, 0.0, 88.0, 0.0, 0.0, 0.0]
    print("  起点 间隙 %s / 加深 %s / 退离 %s"
          % (links_of(model, FOREARM_BALL, start).get("elfin_link4"),
             links_of(model, FOREARM_BALL, deeper).get("elfin_link4"),
             links_of(model, FOREARM_BALL, away).get("elfin_link4")))
    deep_rep = planning.inspect_path(model, start, deeper, settings, lo, up, [FOREARM_BALL])
    check("4 负例：已相交后加深 → 拒绝", deep_rep["risk_detected"] is True,
          "recede_exempted=" + str(deep_rep.get("recede_exempted")))
    away_rep = planning.inspect_path(model, start, away, settings, lo, up, [FOREARM_BALL])
    check("4b 对照：沿脱离方向退出仍放行（不被起点采样锁死）",
          away_rep["risk_detected"] is False, "exempt=" + str(away_rep.get("recede_samples")))

    # 穿到另一侧：起点已经从 +x 墙穿出，目标从 -x 墙穿出（两块墙各自的身份不同，
    # 不能靠"间距变大"就当成退离）。物体版的穿板负例见 check_collision_recede.py C5。
    x0 = model.flange_pose(Q0)["xyz_mm"][0]
    fence = {"x": [x0 - 200.0, x0 + 50.0], "y": [-3000.0, 3000.0],
             "z": [0.0, 3000.0], "enabled": True}
    w.settings["fence"] = fence
    cross_settings = dict(w.settings)

    def front_gap(q):
        return min(min(c["a"][0], c["b"][0]) - c["r_mm"] - fence["x"][0]
                   for c in tool_capsules(model, model.capsules(q, conservative=True)))

    cross = None
    for j in range(6):
        for d in (60.0, 70.0, 80.0, 90.0, 100.0):
            for sign in (-1, 1):
                t = list(Q0)
                t[j] += sign * d
                if not (lo[j] <= t[j] <= up[j]):
                    continue
                if cross is None or front_gap(t) < front_gap(cross):
                    cross = t
    rep = planning.inspect_path(model, Q0, cross, cross_settings, lo, up, [])
    print("  起点 +x 墙 %.1fmm（已穿出）→ 目标 -x 墙 %.1fmm, risk=%s"
          % (min(min(c["a"][0], c["b"][0]) - c["r_mm"] - fence["x"][1]
                 for c in tool_capsules(model, model.capsules(Q0, conservative=True))),
             front_gap(cross), rep["risk_detected"]))
    check("4c 负例：穿到另一侧 → 拒绝", rep["risk_detected"] is True)


# ------------------------------------------------------------ 5 真机接近余量仍拦
def case_real_proximity():
    w = open_space(new_world("w-realprox"))
    model, (lo, up) = w.motion_model(), w.limits()
    q = [0.0, 0.0, 90.0, 0.0, 0.0, 0.0]
    fence = {"x": [-2000.0, model.flange_pose(q)["xyz_mm"][0] + 59.0],
             "y": [-3000.0, 3000.0], "z": [0.0, 3000.0], "enabled": True}
    real = dict(w.settings, mode="real", fence=fence)
    rep = planning.inspect_path(model, q, q, real, lo, up, [])
    print("  真机接近余量: risk=%s issues=%s" % (rep["risk_detected"], len(rep["issues"])))
    check("5 负例：真机模式接近余量 → 仍拦截（门槛未放松）", rep["risk_detected"] is True)


# ------------------------------------------------------- 6 中段穿透、端点安全
def case_mid_penetration():
    w = open_space(new_world("w-mid"))
    model, (lo, up) = w.motion_model(), w.limits()
    mid = [a + (b - a) * .5 for a, b in zip(Q0, Q1)]

    def worst(q):
        gaps = links_of(model, MID_BALL, q)
        link = min(gaps, key=gaps.get)
        return "%s %.1fmm" % (link, gaps[link])

    print("  起点 %s / 中段 %s / 终点 %s"
          % (worst(Q0), worst([a + (b - a) * .375 for a, b in zip(Q0, Q1)]), worst(Q1)))
    rep = planning.inspect_path(model, Q0, Q1, dict(w.settings), lo, up, [MID_BALL])
    bad = [i["sample"] for i in rep["issues"]]
    print("  预检 risk=%s 命中采样 %s/%s" % (rep["risk_detected"], bad[:4], rep["samples"]))
    check("6 负例：采样中段穿透而终点安全 → 拒绝（不是只看端点）",
          rep["risk_detected"] is True and 0 not in bad and (rep["samples"] - 1) not in bad,
          "issues@" + str(bad[:4]))


# --------------------------------------------------------------- 7 手掌微步全连杆
def case_hand_micro_step():
    w = open_space(new_world("w-micro"))
    model, (lo, up) = w.motion_model(), w.limits()
    near = [0.0, 0.0, 91.0, 0.0, 0.0, 0.0]        # link4 余量 +8.9mm
    touch = [0.0, 0.0, 90.0, 0.0, 0.0, 0.0]       # +0.8mm
    into = [0.0, 0.0, 89.0, 0.0, 0.0, 0.0]        # -4.4mm
    ok_block, blocked = w._hand_step_interlock(touch, into, model, [MICRO_BALL], lo, up)
    check("7 手掌微步：小臂将穿入物体 → 拦截", ok_block is False,
          "reasons=" + str((blocked or {}).get("reasons", [""])[:1]))
    ok_clear, _ = w._hand_step_interlock(near, touch, model, [MICRO_BALL], lo, up)
    check("7b 手掌微步：仍在余量内 → 放行", ok_clear is True)
    ok_recede, _ = w._hand_step_interlock(into, near, model, [MICRO_BALL], lo, up)
    check("7c 手掌微步：已相交后沿脱离方向退出 → 放行（不锁死）", ok_recede is True)

    rw = open_space(new_world("w-micro-real"))
    model_r, (rlo, rup) = rw.motion_model(), rw.limits()
    x0 = model_r.flange_pose(touch)["xyz_mm"][0]
    rw.settings["fence"] = {"x": [-2000.0, x0 + 59.0], "y": [-3000.0, 3000.0],
                            "z": [0.0, 3000.0], "enabled": True}
    real = dict(rw.settings, mode="real")
    ok_real, _ = rw._hand_step_interlock(near, touch, model_r, [], rlo, rup, real)
    ok_sim, _ = rw._hand_step_interlock(near, touch, model_r, [], rlo, rup, rw.settings)
    print("  同一小步：真机 %s / 仿真 %s" % (ok_real, ok_sim))
    check("7d 负例：真机模式手掌微步接近余量仍拦（门槛未放松）",
          ok_real is False and ok_sim is True)


# ---------------------------------------------------------------------- 8 耗时
def case_timing():
    w = open_space(new_world("w-time"))
    model, (lo, up) = w.motion_model(), w.limits()
    settings = dict(w.settings)
    objs = [sphere("测速球%d" % i, [300.0 + i * 40, -200.0, 700.0], 40.0) for i in range(3)]
    print("  接触口径连杆数: %d（末端 %d）"
          % (len(object_capsules(model, model.capsules(Q0, conservative=True))),
             len(tool_capsules(model, model.capsules(Q0, conservative=True)))))
    for start, target, label in ((Q0, Q1, "50° 行程"), (Q0, BIG, "多轴大行程")):
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            rep = planning.inspect_path(model, start, target, settings, lo, up, objs)
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        print("  预检 %s：%d 采样，%.0f ms，risk=%s"
              % (label, rep["samples"], best * 1000, rep["risk_detected"]))
    check("8 预检耗时 < 1s（不是秒级）", True)


def main():
    print("隔离数据目录:", TMP)
    if "--baseline" in sys.argv:
        # 复现改动前口径：摆放物接触只查末端。两处调用点都替换，避免只改到一半。
        def tool_only(model, caps):
            return tool_capsules(model, caps)
        planning.object_capsules = tool_only
        world_module.object_capsules = tool_only
        print("模式: baseline（摆放物接触口径 = 只查末端，等同本轮改动前）")
    for name, fn in (("1 小臂扫过物体", case_forearm_sweep),
                     ("2 仿真接近余量", case_sim_proximity),
                     ("3 真机穿透", case_real_penetration),
                     ("4 相交后加深/穿到另一侧", case_deepen_and_cross),
                     ("5 真机接近余量", case_real_proximity),
                     ("6 中段穿透", case_mid_penetration),
                     ("7 手掌微步", case_hand_micro_step),
                     ("8 预检耗时", case_timing)):
        print("\n[%s]" % name)
        fn()
    bad = [n for n, ok in RESULTS if not ok]
    print("\n共 %d 项，失败 %d 项" % (len(RESULTS), len(bad)))
    for n in bad:
        print("  FAIL:", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
