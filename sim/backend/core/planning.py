"""Deterministic preflight of the actual execution strategy. No hardware access."""
import hashlib
import json
import math
from .kinematics import capsule_distance
from .safety import evaluate, _fence_clearance, _ground_clearance


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
    if strategy == "movejto":
        return [list(start), list(target)]
    path, q = [list(start)], list(start)
    for _ in range(max_chunks):
        axis = max(range(6), key=lambda i: abs(target[i] - q[i]))
        delta = target[axis] - q[axis]
        if abs(delta) <= .05:
            return path
        q = list(q)
        q[axis] += max(-chunk, min(chunk, delta))
        path.append(q)
    raise ValueError("所需分段超过执行器当前能力，请分成多个作业点位")


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


def inspect_path(model, start, target, settings, lower, upper, objects):
    strategy = settings.get("real_move_cmd", "relj") if settings.get("mode") == "real" else "movejto"
    route = waypoints(start, target, strategy)
    # Bound work, and disclose when angular sample spacing is relaxed.
    travel = sum(max(abs(b[i] - a[i]) for i in range(6)) for a, b in zip(route, route[1:]))
    spacing = max(1., travel / 480.)
    samples, seconds, issues, closest, max_pct, condition = [], 0., [], float("inf"), 0., 0.
    vmax = model.vmax if settings["vel_source"] == "urdf" else [180, 180, 180, 180, 200, 200]
    speed = settings.get("real_speed_pct" if settings["mode"] == "real" else "sim_speed_pct", 10) / 100.
    for a, b in zip(route, route[1:]):
        distance = max(abs(b[i] - a[i]) for i in range(6))
        n = max(1, math.ceil(distance / spacing))
        seconds += max(abs(b[i] - a[i]) / max(vmax[i] * speed, .001) for i in range(6)) * (1.875 if settings.get("mode") == "sim" else 1)
        samples.extend([[a[i] + (b[i] - a[i]) * k / n for i in range(6)] for k in range(n)])
    samples.append(list(target))
    poses = []
    for index, joints in enumerate(samples):
        caps = model.capsules(joints, conservative=True)
        state = evaluate(model, joints, settings, lower, upper, caps=caps)
        max_pct = max(max_pct, state["max_pct"])
        hits = state["collisions"][:]
        for aidx, cap in enumerate(caps):
            if settings["fence"].get("enabled"):
                closest = min(closest, _fence_clearance(cap, settings["fence"]))
            if settings.get("ground_enabled") and cap["link"] != "elfin_base":
                closest = min(closest, _ground_clearance(cap))
            if settings.get("self_collision"):
                indices = {"elfin_base": -1, **{f"elfin_link{i+1}":i for i in range(6)}}
                for other in caps[aidx + 1:]:
                    if abs(indices.get(cap["link"],99) - indices.get(other["link"],99)) > 1:
                        closest = min(closest, capsule_distance(cap, other))
            for obj in objects:
                if not obj.get("collide", True):
                    continue
                radius = obj["size"][0] if obj["kind"] == "sphere" else math.sqrt(sum(v*v for v in obj["size"])) / 2
                proxy = {"a": obj["xyz"], "b": obj["xyz"], "r_mm": radius}
                gap = capsule_distance(cap, proxy)
                closest = min(closest, gap)
                if gap < 0:
                    hits.append({"type": "object", "link": cap["link"], "object": obj["name"], "gap_mm": gap})
        if hits or state["max_pct"] >= 100:
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
            "envelope": "conservative_visual_bbox_capsules",
            "notes": ["保守包络覆盖视觉模型边界盒，可能误报；尚未包含实际工具与线缆",
                      "关节采样预警，不是连续碰撞证明或硬件安全保护",
                      "估计耗时不包含真实加减速、通信及控制器停稳时间",
                      "仿真六轴同步、平滑加减速；控制器实际加速度尚未标定" if settings.get("mode") == "sim" else
                      "MoveJTo 协调轨迹尚待本机验证" if strategy == "movejto" else "按实际逐轴分段策略预演"]}
