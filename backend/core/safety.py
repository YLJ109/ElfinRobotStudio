"""安全评估：接近碰撞90黄/95红/接触红闪；关节限位单独说明。

默认90% 黄、95% 红、100% 接触（红闪 + 自动停止）。接近带为100mm。
上述接近阈值针对空间碰撞；仿真关节接近限位仅提示，实际越限才拦截。
围栏是四面 keep-in 墙；地面独立检测，顶部开放。
四墙/物体检查末端工具；地面检查可动连杆原始网格，排除接地安装座。
"""

from __future__ import annotations

GREEN, YELLOW, RED, ALARM = "green", "yellow", "red", "alarm"
RANK = {GREEN: 0, YELLOW: 1, RED: 2, ALARM: 3}
_SAMPLES = 14
_PROXIMITY_BAND_MM = 100.0


def limit_ratios(q, lower, upper):
    """每轴 |角| / 同向限位幅值。>1 表示越限。"""
    out = []
    for v, lo, up in zip(q, lower, upper):
        bound = abs(up) if v >= 0 else abs(lo)
        out.append(round(abs(v) / bound, 4) if bound else 0.0)
    return out


def classify(ratio_pct, yellow, red, alarm):
    if ratio_pct >= alarm:
        return ALARM
    if ratio_pct >= red:
        return RED
    if ratio_pct >= yellow:
        return YELLOW
    return GREEN


def _wall_clearance(p, box):
    """到四面墙的带符号余量；地板单独按真实地面检测，围栏顶部开放。"""
    return min(p[0] - box["x"][0], box["x"][1] - p[0],
               p[1] - box["y"][0], box["y"][1] - p[1])


def _fence_clearance(cap, box):
    """胶囊表面到围栏边界的最小带符号距离；<0 表示已经穿出去。"""
    best = float("inf")
    for i in range(_SAMPLES + 1):
        t = i / _SAMPLES
        p = [cap["a"][k] + (cap["b"][k] - cap["a"][k]) * t for k in range(3)]
        best = min(best, _wall_clearance(p, box) - cap["r_mm"])
    return best


def _ground_clearance(cap):
    return min(cap["a"][2], cap["b"][2]) - cap["r_mm"]


def tool_capsules(model, caps):
    """Only the terminal flange/tool envelope participates in collision alarms."""
    link = getattr(model, "flange", "elfin_end_link")
    return [cap for cap in caps if cap.get("link") == link]


def evaluate(model, q, settings, lower=None, upper=None, caps=None, obj_hits=None):
    """返回 {level, max_pct, per_joint, collisions[], reasons[], capsules}。

    lower/upper 由调用方给（控制器软限位 vs URDF 建模限位是两套数，见 config.LIMIT_SOURCES）。
    caps / obj_hits 由 World 每帧算一次复用，避免同一份胶囊体算两遍。
    """
    lo = lower if lower is not None else model.lower
    up = upper if upper is not None else model.upper
    ratios = limit_ratios(q, lo, up)
    max_pct = max(ratios) * 100.0
    lvl = classify(max_pct, settings["safety_yellow_pct"], settings["safety_red_pct"],
                   settings["safety_alarm_pct"])
    joint_level = lvl
    reasons = []
    if lvl != GREEN:
        worst = ratios.index(max(ratios))
        reasons.append(f"J{worst + 1} 已到限位的 {max_pct:.1f}%")

    if caps is None:
        caps = model.capsules(q)
    collisions = []
    # Keep all link envelopes available for display; only monitor the terminal
    # flange/tool against the fence, ground, and placed objects.
    monitored = tool_capsules(model, caps)
    fence_pct = ground_pct = 0.0
    fence_gap = ground_gap = float("inf")
    fence = settings["fence"]
    tool_gaps=model.tool_plane_gaps(q,fence) if hasattr(model,'tool_plane_gaps') else []
    nearest_ground=nearest_fence=None
    if fence.get("enabled", True):
        wall_samples=[s for s in tool_gaps if s['type']=='fence'] if tool_gaps else [
            {'link':c['link'],'gap_mm':_fence_clearance(c,fence),'surface':'围栏','geometry':'capsule'} for c in monitored]
        for sample in wall_samples:
            g = sample['gap_mm']
            if g<fence_gap:nearest_fence=sample;fence_gap=g
            fence_pct = max(fence_pct, max(0.0, min(100.0,
                100.0 * (1.0 - max(0.0, g) / _PROXIMITY_BAND_MM))))
            if g <= 0:
                collisions.append({"type": "fence", "link": sample["link"], "gap_mm": round(g, 2)})
    if settings.get("ground_enabled", True):
        ground_samples=[s for s in tool_gaps if s['type']=='ground'] if tool_gaps else [
            {'link':c['link'],'gap_mm':_ground_clearance(c),'geometry':'capsule'} for c in monitored]
        if hasattr(model,'moving_ground_clearances'):
            ground_samples.extend(model.moving_ground_clearances(q))
        for sample in ground_samples:
            g = sample['gap_mm']
            if g<ground_gap:nearest_ground=sample;ground_gap=g
            ground_pct = max(ground_pct, max(0.0, min(100.0,
                100.0 * (1.0 - max(0.0, g) / _PROXIMITY_BAND_MM))))
            if g <= 0:
                collisions.append({"type": "ground", "link": sample['link'], "gap_mm": round(g, 2)})
    for h in (obj_hits or []):
        if h.get("link") == getattr(model, "flange", "elfin_end_link"):
            collisions.append({"type": "object", "link": h["link"], "obj": h["name"],
                               "gap_mm": h["gap_mm"]})
    clearance_pct = max(fence_pct, ground_pct)
    fence_level = classify(fence_pct, settings["safety_yellow_pct"], settings["safety_red_pct"],
                           settings["safety_alarm_pct"])
    ground_level = classify(ground_pct, settings["safety_yellow_pct"], settings["safety_red_pct"],
                            settings["safety_alarm_pct"])
    workspace_level = max((fence_level, ground_level), key=RANK.get)
    max_pct = max(max_pct, clearance_pct)
    lvl = classify(max_pct, settings["safety_yellow_pct"], settings["safety_red_pct"],
                   settings["safety_alarm_pct"])
    joint_out_of_bounds = any(not (a <= v <= b) for v, a, b in zip(q, lo, up))
    if settings.get("mode") == "sim":
        # 仿真中关节接近限位只是提示，不等于碰撞或实际越限。
        # 保留 joint_level 的诊断信息；执行级别只阻止真实越限与空间风险。
        joint_motion_level = ALARM if joint_out_of_bounds else GREEN
        lvl = max((joint_motion_level, workspace_level), key=RANK.get)
        if joint_level != GREEN and not joint_out_of_bounds and reasons:
            reasons[0] += "（仿真提示，不拦截）"
    if collisions:
        lvl = ALARM
        kinds = sorted({c["type"] for c in collisions})
        reasons.append("检测到越界/碰撞：" + "/".join(kinds) + f"（{len(collisions)} 处）")
        for c in collisions:
            if c["type"] == "object":
                reasons.append(f"摆放物「{c['obj']}」压到 {c['link']}")
                break
    for label, pct, gap, nearest in (("围栏", fence_pct, fence_gap,nearest_fence), ("地面", ground_pct, ground_gap,nearest_ground)):
        if pct >= settings["safety_yellow_pct"]:
            part=(nearest or {}).get('link','末端')
            surface=(nearest or {}).get('surface',label)
            reasons.append(f"接近碰撞 {pct:.1f}%：{part} → {surface}，距离 {gap:.1f}mm")
    return {"level": lvl, "max_pct": round(max_pct, 2), "clearance_pct": round(clearance_pct, 2), "per_joint": ratios,
            "joint_level": joint_level, "joint_out_of_bounds": joint_out_of_bounds,
            "fence_level": fence_level, "ground_level": ground_level,
            "workspace_level": workspace_level, "proximity_band_mm": _PROXIMITY_BAND_MM,
            "fence_gap_mm": round(fence_gap, 2) if fence_gap != float("inf") else None,
            "ground_gap_mm": round(ground_gap, 2) if ground_gap != float("inf") else None,
            "collision_proximity_pct":round(clearance_pct,2),
            "nearest_ground":nearest_ground,"nearest_wall":nearest_fence,
            "collisions": collisions, "reasons": reasons,
            "capsules": [[round(v, 1) for v in c["a"]] + [round(v, 1) for v in c["b"]]
                         + [round(c["r_mm"], 1)] for c in caps]}
