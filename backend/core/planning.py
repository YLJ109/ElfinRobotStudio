"""Deterministic preflight of the actual execution strategy. No hardware access."""
import hashlib
import json
import math
from .kinematics import capsule_distance, interpolate_pose, pose_rotation_distance
from .safety import evaluate, tool_capsules, _fence_clearance, _ground_clearance
from .trajectory import JointTrajectory, PreparedMotion, relative_route, vector
from .objects import radius as object_radius, capsule_object_gap


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
    spacing = max(1., travel / 480.)
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
        samples = trajectory.inspection_samples()
        spacing, seconds = 1., trajectory.duration
    elif settings.get("mode") == "sim" and strategy == "legacy_relj":
        seconds = JointTrajectory.build(route, vmax, speed*100, stop_at_knots=True).duration
    poses = []
    for index, joints in enumerate(samples):
        check()
        caps = model.capsules(joints, conservative=True)
        state = evaluate(model, joints, settings, lower, upper, caps=caps)
        monitored = tool_capsules(model, caps)
        max_pct = max(max_pct, state["max_pct"])
        hits = state["collisions"][:]
        for aidx, cap in enumerate(monitored):
            if settings["fence"].get("enabled"):
                closest = min(closest, _fence_clearance(cap, settings["fence"]))
            if settings.get("ground_enabled") and cap["link"] != "elfin_base":
                closest = min(closest, _ground_clearance(cap))
            for obj in objects:
                check()
                if not obj.get("collide", True):
                    continue
                gap = capsule_object_gap(cap,obj)
                closest = min(closest, gap)
                if gap < 0:
                    hits.append({"type": "object", "link": cap["link"], "object": obj["name"], "gap_mm": gap})
        if hits or state["level"] in ("red", "alarm"):
            if len(issues) < 40:
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
            "path_contract": "synchronized" if settings.get("mode") == "sim" and strategy == "movejto" else strategy,
            "envelope": "conservative_visual_bbox_capsules",
            "notes": ["保守包络覆盖视觉模型边界盒，可能误报；尚未包含实际工具与线缆",
                      "关节采样预警，不是连续碰撞证明或硬件安全保护",
                      "估计耗时不包含真实加减速、通信及控制器停稳时间",
                      "仿真时间律未经控制器标定；C1关节曲线不保证精确笛卡尔直线" if trajectory is not None else
                      "兼容逐轴几何预演，反馈、停稳及控制器动力学未标定" if strategy == "legacy_relj" else
                      "仿真六轴同步、平滑加减速；控制器实际加速度尚未标定" if settings.get("mode") == "sim" else
                      "MoveJTo 协调轨迹尚待本机验证" if strategy == "movejto" else "按实际逐轴分段策略预演"]}


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
    steps = min(120, max(2, math.ceil(distance/8), math.ceil(angle/2)))
    path = [q[:]]
    for index in range(1, steps+1):
        check()
        pose = interpolate_pose(start, target, index/steps)
        options = {} if deadline is None and cancelled is None else {'deadline': deadline, 'cancelled': cancelled}
        q, emm, edeg, converged = model.ik(q, pose, lower, upper, iters=80, max_restarts=2, **options)
        check()
        vector(q)
        if not converged:
            raise RuntimeError(f"MOVEL 第 {index}/{steps} 段逆解未收敛：{emm:.2f}mm/{edeg:.2f}°")
        if any(not lo <= v <= hi for v, lo, hi in zip(q, lower, upper)):
            raise RuntimeError("MOVEL 逆解超出关节限位")
        risk = evaluate(model, q, settings, lower, upper, caps=model.capsules(q, conservative=True))
        if risk["level"] in ("red", "alarm"):
            raise RuntimeError("MOVEL 安全预检为红色，已阻止执行：" + "；".join(risk["reasons"]))
        path.append(list(q))
    vmax = model.vmax if settings["vel_source"] == "urdf" else [180, 180, 180, 180, 200, 200]
    curve = JointTrajectory.build(path, vmax, settings.get("sim_speed_pct", 10))
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
