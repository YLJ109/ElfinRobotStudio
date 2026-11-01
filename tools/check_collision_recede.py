"""本轮临时复现脚本：仿真近距不拦截 + 相交后沿脱离方向退离。

只读仓库、只写临时目录（World(data_dir=...) 指向 tempfile），不连真机、不起服务。
运行：python tools/check_collision_recede.py
退出码 0 表示全部断言通过；1 表示有断言失败（改动前预期会失败）。
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="elfin-recede-check-"))
os.environ["ELFIN_DATA_DIR"] = str(TMP)
os.environ["HUYAN_NO_REAL"] = "1"

from backend.core import planning                     # noqa: E402
from backend.core.objects import capsule_object_gap   # noqa: E402
from backend.core.safety import evaluate, tool_capsules, _object_signed_gap  # noqa: E402
from backend.core.world import World                  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + ("   | " + detail if detail else ""))


def new_world(mode="sim"):
    w = World(data_dir=TMP / ("w-" + mode))
    w.settings["mode"] = mode
    return w


def fence_near(x, *, ahead=61.0, behind=2000.0):
    """只在 x 上很薄的一片围栏：+x 墙在法兰原点 x+ahead，-x 墙在 x-behind。

    工具包络在 +x 方向比法兰原点外伸约 55mm，所以 ahead=61 → 最小间隙约 6mm（只是接近）。
    """
    return {"x": [x - behind, x + ahead], "y": [-3000.0, 3000.0],
            "z": [0.0, 3000.0], "enabled": True}


def setup_fence(w, q, **kw):
    w.settings["fence"] = fence_near(w.motion_model().flange_pose(q)["xyz_mm"][0], **kw)
    w.settings["ground_enabled"] = False
    w.sim.q = list(q)


def gap_of(model, q, settings, objects=()):
    """该姿态下已建模接触源的最小带符号间隙（围栏/地面/摆放物，末端口径）。"""
    caps = model.capsules(q, conservative=True)
    st = evaluate(model, q, settings, model.lower, model.upper, caps=caps)
    values = [v for v in (st.get("fence_gap_mm"), st.get("ground_gap_mm")) if v is not None]
    for cap in tool_capsules(model, caps):
        for obj in objects:
            values.append(_object_signed_gap(cap, obj))
    return min(values) if values else float("inf")


def chord_worst_gap(model, start, target, settings, objects=(), lo=None, up=None, steps=8):
    """沿关节弦采样，返回 (最差最小间隙, 末点最小间隙, 末点法兰x)。"""
    lo = lo or list(model.lower)
    up = up or list(model.upper)
    worst = final = None
    for i in range(steps + 1):
        q = [a + (b - a) * i / steps for a, b in zip(start, target)]
        if any(not l <= v <= u for v, l, u in zip(q, lo, up)):
            return None
        g = gap_of(model, q, settings, objects)
        worst = g if worst is None else min(worst, g)
        final = g
    return worst, final, model.flange_pose(list(target))["xyz_mm"][0]


def profile_min(model, q, settings, objects=()):
    return gap_of(model, q, settings, objects)


def far_wall_gap(model, q, settings):
    """-x 墙（正面墙）的带符号间隙；<0 表示已经从另一侧穿出去。"""
    caps = model.capsules(q, conservative=True)
    st = evaluate(model, q, settings, model.lower, model.upper, caps=caps)
    wall = st.get("nearest_wall") or {}
    if wall.get("surface") == "正面墙":
        return float(wall.get("gap_mm", 0.0))
    return float("inf")


# ---------------------------------------------------------------- A 近距不拦截
def case_proximity():
    w = new_world("sim")
    q = [0.0, 0.0, 90.0, 0.0, 0.0, 0.0]
    # 工具包络在 +x 方向比法兰原点外伸约 55mm；front=59 时最小间隙约 4mm：
    # 只是「接近」，没有任何穿透。
    setup_fence(w, q, ahead=59.0)
    model, lo, up = w.motion_model(), w.limits()[0], w.limits()[1]
    settings = dict(w.settings)
    st = evaluate(model, q, settings, lo, up)
    print("  sim 近距(2mm) evaluate:", st["level"], "fence_gap", st["fence_gap_mm"],
          "collisions", st["collisions"], "reasons", st["reasons"])
    check("A1 仿真 evaluate 不因接近余量阻断（level 非 red/alarm 且无碰撞）",
          st["level"] not in ("red", "alarm") and not st["collisions"], "level=" + st["level"])
    rep = planning.inspect_path(model, q, q, settings, lo, up, [])
    print("  sim 近距 inspect_path: risk_detected", rep["risk_detected"],
          "issues", len(rep["issues"]), "min_clearance", rep["min_clearance_mm"])
    check("A2 仿真 inspect_path 不因接近余量阻断", rep["risk_detected"] is False)

    rs = dict(settings, mode="real")
    rst = evaluate(model, q, rs, lo, up)
    rrep = planning.inspect_path(model, q, q, rs, lo, up, [])
    print("  real 近距(2mm) evaluate:", rst["level"], rst["reasons"])
    print("  real 近距 inspect_path: risk_detected", rrep["risk_detected"])
    check("A3 真机 evaluate 仍按余量拦截（红线）", rst["level"] in ("red", "alarm"),
          "level=" + rst["level"])
    check("A4 真机 inspect_path 仍拦截", rrep["risk_detected"] is True)


# ------------------------------------------------------- B 相交后沿脱离方向退离
def case_recede():
    w = new_world("sim")
    model, lo, up = w.motion_model(), w.limits()[0], w.limits()[1]
    q0 = [0.0, 0.0, 90.0, 0.0, 0.0, 0.0]
    setup_fence(w, q0, ahead=50.0, behind=200.0)
    settings = dict(w.settings)
    st = evaluate(model, q0, settings, lo, up)
    print("  起点 evaluate:", st["level"], "fence_gap", st["fence_gap_mm"], "collisions", st["collisions"])
    check("B1 起点已穿出围栏 5mm（仿真中仍是 ALARM）", st["level"] == "alarm" and st["collisions"])

    # 用关节搜索找出「退离 / 加深 / 穿到另一侧」三个方向
    cands = []
    for j in range(6):
        for d in (0.5, 1, 2, 4, 8, 16, 27, 40, 55):
            for s in (-1, 1):
                t = list(q0)
                t[j] += s * d
                if not (lo[j] <= t[j] <= up[j]):
                    continue
                got = chord_worst_gap(model, q0, t, settings, (), lo, up)
                if got is None:
                    continue
                cands.append({"target": t, "joint": j, "delta": s * d,
                              "worst": got[0], "final": got[1], "x": got[2]})
    if not cands:
        check("B2 退离放行（仿真）", False, "contact_profile 尚未实现，无法搜索候选目标")
        return w, model, lo, up, q0, settings
    start_gap = profile_min(model, q0, settings)
    recede = max(cands, key=lambda c: c["final"])
    deeper = min(cands, key=lambda c: c["final"])
    crossed = [c for c in cands if far_wall_gap(model, c["target"], settings) < 0]
    cross = min(crossed, key=lambda c: far_wall_gap(model, c["target"], settings), default=None)
    print("  起点最小间隙 %.2fmm，穿出 +x 墙 %.1fmm"
          % (start_gap, -(evaluate(model, q0, settings, lo, up)["fence_gap_mm"])))
    print("  退离候选 J%d%+.1f° → 端点最小间隙 %.2fmm" % (recede["joint"] + 1, recede["delta"], recede["final"]))
    print("  加深候选 J%d%+.1f° → 端点最小间隙 %.2fmm" % (deeper["joint"] + 1, deeper["delta"], deeper["final"]))

    rep = planning.inspect_path(model, q0, recede["target"], settings, lo, up, [])
    print("  退离 inspect_path: risk_detected", rep["risk_detected"], "issues", len(rep["issues"]),
          "recede_exempted", rep.get("recede_exempted"))
    check("B2 相交后沿脱离方向退离被放行（仿真）", rep["risk_detected"] is False,
          "issues=" + json.dumps(rep["issues"][:1], ensure_ascii=False)[:160])
    check("B3 界面预检不再报「红色安全预检，禁止执行」",
          (w.precheck(recede["target"]) or "").find("红色安全预检") < 0,
          str(w.precheck(recede["target"]))[:120])

    drep = planning.inspect_path(model, q0, deeper["target"], settings, lo, up, [])
    check("B4 负例：加深碰撞仍被拒绝", drep["risk_detected"] is True)

    if cross is None:
        check("B5 负例：穿到另一侧仍被拒绝", False, "没搜到穿过围栏的候选")
    else:
        print("  穿到另一侧候选 J%d%+.1f° → 正面墙间隙 %.1fmm（穿出另一侧）"
              % (cross["joint"] + 1, cross["delta"], far_wall_gap(model, cross["target"], settings)))
        crep = planning.inspect_path(model, q0, cross["target"], settings, lo, up, [])
        check("B5 负例：穿到另一侧仍被拒绝", crep["risk_detected"] is True)

    rrep = planning.inspect_path(model, q0, recede["target"], dict(settings, mode="real"), lo, up, [])
    check("B6 负例：真机模式同一条退离路线仍被拒绝（门槛未放松）",
          rrep["risk_detected"] is True)

    # 手掌微步互锁（World 内部逐小步检查）
    step = getattr(w, "_hand_step_interlock", None)
    if step is None:
        check("B7 手掌微步：退离放行", False, "World._hand_step_interlock 尚未实现")
        check("B8 手掌微步：加深拒绝", False, "同上")
        check("B9 手掌微步：穿到另一侧拒绝", False, "同上")
    else:
        base = gap_of(model, q0, settings)
        micro = []
        for j in range(6):
            for s in (-1, 1):
                t = list(q0)
                t[j] += s * 0.5
                micro.append({"target": t, "joint": j, "delta": s * 0.5,
                              "gap": gap_of(model, t, settings)})
        near = max(micro, key=lambda c: c["gap"])
        deep = min(micro, key=lambda c: c["gap"])
        ok1, _ = step(q0, near["target"], model, [], lo, up)
        ok2, blocked2 = step(q0, deep["target"], model, [], lo, up)
        print("  手掌微步 J%d%+.1f° 间隙 %.2f→%.2f：放行 %s"
              % (near["joint"] + 1, near["delta"], base, near["gap"], ok1))
        print("  手掌微步 J%d%+.1f° 间隙 %.2f→%.2f：放行 %s"
              % (deep["joint"] + 1, deep["delta"], base, deep["gap"], ok2))
        check("B7 手掌微步沿脱离方向被放行", ok1 is True and near["gap"] > base)
        check("B8 手掌微步反向加深被拒绝",
              ok2 is False and deep["gap"] < base, (blocked2 or {}).get("reasons", [""])[0][:60])
        if cross is not None:
            ok3, _ = step(q0, cross["target"], model, [], lo, up)
            check("B9 手掌微步穿到另一侧被拒绝", ok3 is False)
        real_w = new_world("real")
        real_w.settings["fence"] = dict(w.settings["fence"])
        real_w.settings["ground_enabled"] = False
        real_w.sim.q = list(q0)
        real_ok, _ = real_w._hand_step_interlock(q0, near["target"], model, [], lo, up)
        check("B10 负例：真机模式手掌微步退离仍被拒绝（门槛未放松）", real_ok is False)
    return w, model, lo, up, q0, settings


# ------------------------------------------------------------- C 摆放物（球/盒）
def case_objects():
    w = new_world("sim")
    model, lo, up = w.motion_model(), w.limits()[0], w.limits()[1]
    q0 = [0.0, 0.0, 90.0, 0.0, 0.0, 0.0]
    w.settings["ground_enabled"] = False
    w.settings["fence"] = {"x": [-3000.0, 3000.0], "y": [-3000.0, 3000.0],
                           "z": [0.0, 3000.0], "enabled": True}
    pose = model.flange_pose(q0)["xyz_mm"]
    settings = dict(w.settings)
    sphere = {"name": "球", "kind": "sphere", "xyz": [pose[0] + 8.0, pose[1], pose[2]],
              "size": [60.0, 60.0, 60.0], "scale": 1, "collide": True}
    box = {"name": "盒", "kind": "box", "xyz": [pose[0] + 8.0, pose[1], pose[2]],
           "size": [60.0, 60.0, 60.0], "scale": 1, "collide": True}
    cap = tool_capsules(model, model.capsules(q0, conservative=True))[0]
    print("  球间隙 %.2fmm  盒间隙 %.2fmm" % (capsule_object_gap(cap, sphere), capsule_object_gap(cap, box)))
    # 盒体穿透时 evaluate 必须能算（原实现缺 import math 会 NameError）
    try:
        st = evaluate(model, q0, settings, lo, up,
                      obj_hits=[{"link": cap["link"], "name": "盒", "gap_mm": -3.0, "penetrating": True}])
        check("C1 盒体穿透时 evaluate 不抛 NameError", st["level"] == "alarm", st["level"])
    except Exception as exc:
        check("C1 盒体穿透时 evaluate 不抛 NameError", False, f"{type(exc).__name__}: {exc}")

    cands = []
    for j in range(6):
        for d in (1, 2, 4):
            for s in (-1, 1):
                t = list(q0)
                t[j] += s * d
                got = chord_worst_gap(model, q0, t, settings, [sphere], lo, up)
                if got is None:
                    continue
                cands.append({"target": t, "joint": j, "delta": s * d, "final": got[1]})
    if not cands:
        check("C2 摆放物：退离放行", False, "contact_profile 尚未实现")
        check("C3 摆放物：加深拒绝", False, "contact_profile 尚未实现")
        return
    recede = max(cands, key=lambda c: c["final"])
    deeper = min(cands, key=lambda c: c["final"])
    rep = planning.inspect_path(model, q0, recede["target"], settings, lo, up, [sphere])
    drep = planning.inspect_path(model, q0, deeper["target"], settings, lo, up, [sphere])
    brep = planning.inspect_path(model, q0, recede["target"], settings, lo, up, [box])
    print("  球 退离 J%d%+.1f° risk=%s / 加深 J%d%+.1f° risk=%s"
          % (recede["joint"] + 1, recede["delta"], rep["risk_detected"],
             deeper["joint"] + 1, deeper["delta"], drep["risk_detected"]))
    check("C2 摆放物：沿脱离方向退离被放行", rep["risk_detected"] is False)
    check("C3 负例：摆放物加深碰撞仍被拒绝", drep["risk_detected"] is True)
    check("C4 盒体相交后同一条退离路线仍放行（SDF 不饱和）", brep["risk_detected"] is False)

    # 负例：从厚板近侧穿到远侧。仿真工具包络是一段 300mm 长、半径 25mm 的胶囊，
    # 所以板要在 x 上有厚度，才能出现"端点已经穿出去、中途却更深"的穿越段。
    thin = {"name": "厚板", "kind": "box", "xyz": [pose[0] + 20.0, pose[1], pose[2]],
            "size": [80.0, 300.0, 90.0], "scale": 1, "collide": True}
    start_box = gap_of(model, q0, settings, [thin])
    through = []
    for j in range(6):
        for d in (0.5, 1, 2, 4, 8, 16):
            for s in (-1, 1):
                t = list(q0)
                t[j] += s * d
                if not (lo[j] <= t[j] <= up[j]):
                    continue
                got = chord_worst_gap(model, q0, t, settings, [thin], lo, up, steps=16)
                if got is None:
                    continue
                end = gap_of(model, t, settings, [thin])
                if end > 0 and got[0] < start_box - 1:
                    through.append({"target": t, "joint": j, "delta": s * d, "worst": got[0],
                                    "end": end})
    if not through:
        check("C5 负例：盒体穿到另一侧仍被拒绝", False, "没搜到穿过盒体的候选")
    else:
        pick = max(through, key=lambda c: c["end"])
        trep = planning.inspect_path(model, q0, pick["target"], settings, lo, up, [thin])
        print("  穿薄板候选 J%d%+.1f°：起点 %.1f → 最深处 %.1f → 端点 %.1fmm，risk=%s"
              % (pick["joint"] + 1, pick["delta"], start_box, pick["worst"], pick["end"],
                 trep["risk_detected"]))
        check("C5 负例：盒体穿到另一侧仍被拒绝", trep["risk_detected"] is True)


# ------------------------------------------------------------- D 解除仿真暂停
def case_unpause(q0, model, lo, up, settings):
    w = new_world("sim")
    fn = getattr(w, "_sim_recede_unpause", None)
    if fn is None:
        check("D1 退离放行后解除仿真告警暂停", False, "_sim_recede_unpause 尚未实现")
        return
    w.sim.paused = True
    ok1 = fn() is True and w.sim.paused is False
    w.settings["mode"] = "real"
    w.sim.paused = True
    ok2 = fn() is False and w.sim.paused is True
    check("D1 退离放行后解除仿真暂停，真机模式不动", ok1 and ok2, f"{ok1}/{ok2}")


def main():
    print("隔离数据目录:", TMP)
    print("contact_profile 是否已实现:", hasattr(planning, "contact_profile"))
    if "--baseline" in sys.argv:
        # 把新判据短路成"永不放行"，等价于改动前"有碰撞就拦"的语义，
        # 用来复现这两条门槛在改动前的行为。
        import backend.core.world as world_module

        def deny(*args, **kwargs):
            return {"ok": False, "why": "baseline: 退离判据停用", "touched": 0,
                    "start_min": None, "target_min": None, "deeper": [], "new": []}
        planning.recede_verdict = deny
        world_module.recede_verdict = deny
        print("模式: baseline（退离判据短路为永不放行）")
    print("\n[A] 仿真近距 / 真机门槛")
    case_proximity()
    print("\n[B] 相交后沿脱离方向退离（围栏）")
    w, model, lo, up, q0, settings = case_recede()
    print("\n[C] 摆放物")
    case_objects()
    print("\n[D] 告警暂停")
    case_unpause(q0, model, lo, up, settings)
    bad = [n for n, ok in RESULTS if not ok]
    print("\n共 %d 项，失败 %d 项" % (len(RESULTS), len(bad)))
    for n in bad:
        print("  FAIL:", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
