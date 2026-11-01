"""Deterministic preflight of the actual execution strategy. No hardware access."""
import hashlib
import json
import math
from .kinematics import capsule_distance, interpolate_pose, pose_rotation_distance
from .safety import (evaluate, tool_capsules, object_capsules, _fence_clearance, _ground_clearance,
                     contact_profile, recede_verdict, _object_signed_gap, object_envelope)
from .trajectory import JointTrajectory, PreparedMotion, relative_route, vector
from .objects import radius as object_radius, capsule_object_gap

# 路径预检里"全连杆 × 摆放物 × 采样点"的求距次数上限。约合 240 个采样点 ×
# 10 根连杆 × 3 个物体；超出后放宽角采样间距，不退回只查末端。
_OBJECT_EVAL_BUDGET = 7200


def _canonical_json(value):
    """Normalize JSON numbers the same way a browser round-trip does.

    JavaScript has one Number type, so JSON.stringify(JSON.parse("0.0")) emits
    ``0``.  Plan signatures must compare the numeric meaning rather than the
    Python int/float spelling, while leaving every key, list item and non-number
    value exact.
    """
    if isinstance(value, dict):
        return {key: _canonical_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json(item) for item in value]
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(_canonical_json(value), sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def waypoints(start, target, strategy="relj", chunk=10., max_chunks=240):
    if len(start) != 6 or len(target) != 6 or not all(math.isfinite(v) for v in start + target):
        raise ValueError("起点和目标需要六个有限关节角")
    if strategy in ("movejto", "synchronized"):
        return [list(start), list(target)]
    if strategy not in ("relj", "legacy_relj"):
        raise ValueError("未知路径策略；不能冒充已验证控制器路径")
    return relative_route(start, target, chunk, max_chunks)


def eigenvalues_symmetric(matrix):
    a = [row[:] for row in matrix]
    n = len(a)
    for _ in range(100):
        p, q = max(((i, j) for i in range(n) for j in range(i + 1, n)), key=lambda ij: abs(a[ij[0]][ij[1]]))
        if abs(a[p][q]) < 1e-10:
            break
        angle = .5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
        c, s = math.cos(angle), math.sin(angle)
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        a[p][p] = c*c*app - 2*s*c*apq + s*s*aqq
        a[q][q] = s*s*app + 2*s*c*apq + c*c*aqq
        a[p][q] = a[q][p] = 0.
        for k in range(n):
            if k not in (p, q):
                akp, akq = a[k][p], a[k][q]
                a[k][p] = a[p][k] = c*akp - s*akq
                a[k][q] = a[q][k] = s*akp + c*akq
    return sorted(max(0., a[i][i]) for i in range(n))


def inspect_path(model, start, target, settings, lower, upper, objects, *, trajectory=None, check=lambda: None):
    # Preserve the legacy report spelling for the existing World/UI callers.
    # "movejto" here describes the old direct preflight chord, not permission
    # to send the (still blocked) controller long-press command.
    strategy = settings.get("real_move_cmd", "relj") if settings.get("mode") == "real" else settings.get("sim_path_contract", "movejto")
    if trajectory is not None:
        if settings.get("mode") != "sim":
            raise ValueError("预生成轨迹仅用于仿真")
        if tuple(start) != trajectory.points[0] or tuple(target) != trajectory.points[-1]:
            raise ValueError("预检起终点与执行曲线不一致")
        route = [list(p) for p in trajectory.points]
        strategy = "joint_cubic_simulation"
    else:
        route = waypoints(start, target, strategy)
    # Bound work, and disclose when angular sample spacing is relaxed.
    travel = sum(max(abs(b[i] - a[i]) for i in range(6)) for a, b in zip(route, route[1:]))
    # 仿真按 2° 采样：关节 2° 在臂展中段约合末端 10mm 步进，40mm 工件不会被整段跨过；
    # 真机的 1° 门槛保持不变（真机是安全边界，不跟着仿真一起松）。
    spacing = max(2. if settings.get("mode") == "sim" else 1., travel / 480.)
    # 全连杆×物体×采样点的求距次数预算（SIM04）。超出时放宽角采样间距，
    # 而不是退回"只查末端"：宁可稀一点，也不能对小臂/手腕视而不见。
    links = max(1, len(object_capsules(model, model.capsules(list(start), conservative=True))))
    objects = [obj for obj in objects if obj.get("collide", True)]
    # 每个物体的外接球只算一次，供内层求距做球-球下界剪枝（纯剪枝，不改进判据）。
    envelopes = [object_envelope(obj) for obj in objects]
    if objects:
        spacing = max(spacing, travel * links * len(objects) / _OBJECT_EVAL_BUDGET)
    samples, seconds, issues, closest, max_pct, condition = [], 0., [], float("inf"), 0., 0.
    vmax = model.vmax if settings["vel_source"] == "urdf" else [180, 180, 180, 180, 200, 200]
    speed = settings.get("real_speed_pct" if settings["mode"] == "real" else "sim_speed_pct", 10) / 100.
    for a, b in zip(route, route[1:]):
        check()
        distance = max(abs(b[i] - a[i]) for i in range(6))
        n = max(1, math.ceil(distance / spacing))
        seconds += max(abs(b[i] - a[i]) / max(vmax[i] * speed, .001) for i in range(6)) * (1.875 if settings.get("mode") == "sim" else 1)
        samples.extend([[a[i] + (b[i] - a[i]) * k / n for i in range(6)] for k in range(n)])
    samples.append(list(target))
    if trajectory is not None:
        # Inspect the actual C1 curve, not only its IK knots or chord segments.
        # 这里过去固定用 1°，把上面按仿真 2° 算好的 spacing 丢掉了；曲线分支才是
        # MOVEL/绘制真正走的那条路，所以采样密度要以 spacing 为准。
        samples = trajectory.inspection_samples(spacing)
        seconds = trajectory.duration
    elif settings.get("mode") == "sim" and strategy == "legacy_relj":
        seconds = JointTrajectory.build(route, vmax, speed*100, stop_at_knots=True).duration
    poses = []
    # 仿真里若起点已经相交，只放行"沿脱离方向退出"的采样点；真机不进入这段逻辑。
    recede_start = None
    receded = 0
    recede_note = ""
    for index, joints in enumerate(samples):
        check()
        caps = model.capsules(joints, conservative=True)
        state = evaluate(model, joints, settings, lower, upper, caps=caps)
        # 围栏/地面仍是末端口径；摆放物按全连杆（含手腕、小臂），
        # 否则"末端没碰到、小臂扫过物体"会被整条预检漏掉（SIM04）。
        monitored = tool_capsules(model, caps)
        contact_links = object_capsules(model, caps)
        max_pct = max(max_pct, state["max_pct"])
        hits = state["collisions"][:]
        for aidx, cap in enumerate(monitored):
            if settings["fence"].get("enabled"):
                closest = min(closest, _fence_clearance(cap, settings["fence"]))
            if settings.get("ground_enabled") and cap["link"] != "elfin_base":
                closest = min(closest, _ground_clearance(cap))
        for cap in contact_links:
            # 球-球下界在循环外先把这根连杆的球参数算一次；命中下界就直接跳过，
            # 连一次函数调用都不付。判据与 _object_signed_gap 内部完全同一套。
            ca, cb = cap['a'], cap['b']
            cdx, cdy, cdz = cb[0] - ca[0], cb[1] - ca[1], cb[2] - ca[2]
            chalf = .5 * math.sqrt(cdx * cdx + cdy * cdy + cdz * cdz)
            cmx, cmy, cmz = (ca[0] + cb[0]) * .5, (ca[1] + cb[1]) * .5, (ca[2] + cb[2]) * .5
            cr = float(cap.get('r_mm', 0.0))
            for obj, envelope in zip(objects, envelopes):
                check()
                ox, oy, oz = envelope[0]
                ex, ey, ez = cmx - ox, cmy - oy, cmz - oz
                # 只有下界超过"当前最小间隙（负值时取 0）"才可能影响结果；
                # 此时真实间隙既不可能更小，也不可能为负，跳过是安全的。
                if math.sqrt(ex * ex + ey * ey + ez * ez) - chalf - cr - envelope[1] > (closest if closest > 0 else 0.):
                    continue
                # 用带符号间隙：盒体内部的端点过去会在 0 处饱和，等于看不见穿透。
                gap = _object_signed_gap(cap, obj)
                closest = min(closest, gap)
                if gap < 0:
                    hits.append({"type": "object", "link": cap["link"], "object": obj["name"], "gap_mm": gap})
        if hits or state["level"] in ("red", "alarm"):
            exempt = False
            if hits and settings.get("mode") == "sim":
                if recede_start is None:
                    start_caps = model.capsules(samples[0], conservative=True)
                    recede_start = contact_profile(model, samples[0], settings, start_caps, objects,
                                                   object_monitored=object_capsules(model, start_caps))
                verdict = recede_verdict(recede_start,
                                         contact_profile(model, joints, settings, caps, objects,
                                                         monitored=monitored,
                                                         object_monitored=contact_links),
                                         require_progress=index == len(samples) - 1,
                                         # 关节弦线不是笛卡尔直线，允许 0.5mm 的
                                         # 几何瞬时抖动；端点仍必须严格走远。
                                         slack_mm=.5)
                exempt = verdict["ok"]
                if exempt:
                    receded += 1
                    recede_note = verdict["why"]
            if not exempt and len(issues) < 40:
                issues.append({"sample": index, "joints": joints, "collisions": hits, "reasons": state["reasons"]})
        if index % 12 == 0 or index == len(samples) - 1:
            j = model.jacobian(joints)
            eig = eigenvalues_symmetric([[sum(j[k][r] * j[k][c] for k in range(6)) for c in range(6)] for r in range(6)])
            condition = max(condition, math.sqrt(eig[-1] / max(eig[0], 1e-12)))
        poses.append(model.flange_pose(joints)["xyz_mm"])
    return {"strategy": strategy, "waypoints": route, "samples": len(samples),
            "sample_spacing_deg": round(spacing, 3), "tcp_path": poses,
            "duration_estimate_s": round(seconds, 2), "max_limit_pct": max_pct,
            "min_clearance_mm": round(closest, 2) if math.isfinite(closest) else None,
            "condition_estimate": round(condition, 1), "issues": issues,
            "risk_detected": bool(issues), "validated_hardware": False,
            "controller_path_equivalent": False,
            # 仿真退离放行：显式披露多少采样点是因为"沿脱离方向退出"而被放过的。
            "recede_exempted": bool(receded), "recede_samples": receded,
            "recede_note": recede_note,
            "path_contract": "synchronized" if settings.get("mode") == "sim" and strategy == "movejto" else strategy,
            "envelope": "conservative_visual_bbox_capsules",
            "notes": ["保守包络覆盖视觉模型边界盒，可能误报；尚未包含实际工具与线缆",
                      "关节采样预警，不是连续碰撞证明或硬件安全保护",
                      "估计耗时不包含真实加减速、通信及控制器停稳时间",
                      "仿真时间律未经控制器标定；C1关节曲线不保证精确笛卡尔直线" if trajectory is not None else
                      "兼容逐轴几何预演，反馈、停稳及控制器动力学未标定" if strategy == "legacy_relj" else
                      "仿真六轴同步、平滑加减速；控制器实际加速度尚未标定" if settings.get("mode") == "sim" else
                      "MoveJTo 协调轨迹尚待本机验证" if strategy == "movejto" else "按实际逐轴分段策略预演"]
                     + (["退离放行只依据几何间距变化（不加深/不新增相交面），不是连续扫掠证明"]
                        if receded else [])}


def prepare_movel(model, start_q, target_pose, settings, lower, upper, objects, *, context, check=lambda: None,
                  deadline=None, cancelled=None):
    """Build and inspect ONE sim trajectory before any execution/state write.

    Context must capture revision/model/TCP/UCS/tool/object identities. World must
    revalidate it after this potentially threaded call and throughout execution.
    Existing IK-knot red checks are retained; actual cubic and placed-object
    sampling are additional checks, not a replacement for runtime interlocks.
    Returns immutable PreparedMotion; raises on any failed/unsafe sample.
    """
    if settings.get("mode") != "sim" or context.get("mode") != "sim" or context.get("real_connected") is not False:
        raise ValueError("连续MOVEL仅限无真机连接的仿真")
    q, target = list(vector(start_q)), list(vector(target_pose))
    lower, upper = vector(lower), vector(upper)
    start = model.pose_vec(q)
    distance = math.dist(start[:3], target[:3])
    angle = pose_rotation_distance(start, target)
    # 结节点间距：仿真取 15mm / 3°，真机保持 8mm / 2°。结节点只决定曲线拟合的
    # 粒度，执行时 PreparedMotion 仍按原速度重放整条曲线，不会跳点或瞬移。
    step_mm, step_deg = (15., 3.) if settings.get("mode") == "sim" else (8., 2.)
    steps = min(120, max(2, math.ceil(distance / step_mm), math.ceil(angle / step_deg)))
    path = [q[:]]
    for index in range(1, steps+1):
        check()
        pose = interpolate_pose(start, target, index/steps)
        options = {} if deadline is None and cancelled is None else {'deadline': deadline, 'cancelled': cancelled}
        # 容差必须保持 IK 默认的 0.05mm/0.05°：节点残差直接就是路径偏离量，
        # 放松它换来的是"能跑但走歪"（实测放宽容差会让直线偏离翻三倍）。
        # 想少失败只能多算，不能降精度。
        q, emm, edeg, converged = model.ik(q, pose, lower, upper, iters=200, max_restarts=4, **options)
        check()
        vector(q)
        if not converged:
            # 起点附近的奇异位形（最典型的是零位：TCP 正好落在 J1 轴线上，
            # 转 J1 不动位置）会让直线插值根本解不出来。原来的报错只说
            # "逆解未收敛：1.54mm"，看不出是姿态的问题还是代码的问题。
            if index <= 2 or index == steps:
                hint = ("起点或终点接近奇异位形（例如零位时 TCP 落在 J1 轴线上），"
                        "先用 MOVEJ 移到工作姿态再走直线")
            else:
                hint = "直线路径中途解不出来，多为超出可达范围或要穿过关节限位"
            raise RuntimeError(f"MOVEL 第 {index}/{steps} 段逆解未收敛：{emm:.2f}mm/{edeg:.2f}°；{hint}")
        if any(not lo <= v <= hi for v, lo, hi in zip(q, lower, upper)):
            raise RuntimeError("MOVEL 逆解超出关节限位")
        # 逐节点单独 evaluate 是重复劳动：下面用整条曲线调 inspect_path，
        # 而 JointTrajectory.inspection_samples() 对每个结节都取了终点样本，
        # 覆盖这里能看到的每一个姿态，并且还多查了摆放物。这里的 per-step
        # 全量评估（含逐顶点地面间隙）只会让 MOVEL 预检翻倍，不增加任何保障。
        path.append(list(q))
    vmax = model.vmax if settings["vel_source"] == "urdf" else [180, 180, 180, 180, 200, 200]
    # 用加速-匀速-减速的时钟，而不是全程变速的平滑时钟：控制器 MoveL 走的是
    # 前一种节拍，同样的倍率下中间有一段稳定速度，观感和实测都更接近真机。
    curve = JointTrajectory.build(path, vmax, settings.get("sim_speed_pct", 10), profile='trapezoid')
    check()
    report = inspect_path(model, path[0], path[-1], settings, lower, upper, objects, trajectory=curve, check=check)
    check()
    if report["risk_detected"]:
        raise RuntimeError("MOVEL 连续曲线安全预检为红色，已阻止执行")
    # Disclose approximation; not an assertion of controller/cartesian truth.
    report["cartesian_ik_samples"] = steps
    report["continuous_simulation"] = True
    report["acceleration_jerk_calibrated"] = False
    report["speed_limit_units"] = "joint_degrees_per_second"
    report["tcp_speed_bounded"] = False
    return PreparedMotion(curve, digest(context), lower, upper,
                          json.dumps(report, ensure_ascii=False, allow_nan=False))
