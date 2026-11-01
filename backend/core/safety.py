"""安全评估：接近碰撞90黄/95红/接触红闪；关节限位单独说明。

默认90% 黄、95% 红、100% 接触（红闪 + 自动停止）。接近带为100mm。
上述接近阈值针对空间碰撞；仿真关节接近限位仅提示，实际越限才拦截。
围栏是四面 keep-in 墙；地面独立检测，顶部开放。
四墙/物体检查末端工具；地面检查可动连杆原始网格，排除接地安装座。
"""

from __future__ import annotations

import math

GREEN, YELLOW, RED, ALARM = "green", "yellow", "red", "alarm"
RANK = {GREEN: 0, YELLOW: 1, RED: 2, ALARM: 3}
_SAMPLES = 14
_PROXIMITY_BAND_MM = 100.0
_RECEDE_EPS_MM = 1e-6          # 退离要求最小间距严格变大，至少跨过浮点噪声
_RECEDE_TOUCH_MM = .05         # 与仿真穿透判据同一量级：>此值才算"已相交"


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
    """Terminal envelope for workspace proximity; object contacts use all links."""
    link = getattr(model, "flange", "elfin_end_link")
    return [cap for cap in caps if cap.get("link") == link]


def object_capsules(model, caps):
    """摆放物的接触口径：全部可动连杆，排除固定安装座（SIM04）。

    底座与地面固定、路径无法改变它的位置，把"物体本来就贴着底座"算成本次运动
    的碰撞只会制造误拦；地面检查里同样排除固定安装座。围栏/地面的接近余量口径
    仍是末端（tool_capsules），这里只影响摆放物。
    """
    base = getattr(model, "base", "elfin_base")
    return [cap for cap in caps if cap.get("link") != base]


def _fence_samples(model, q, fence, monitored, tool_gaps=None):
    """围栏间隙样本（带墙面身份 surface），evaluate 与 contact_profile 共用。"""
    if tool_gaps is None:
        tool_gaps = model.tool_plane_gaps(q, fence) if hasattr(model, 'tool_plane_gaps') else []
    if tool_gaps:
        return [s for s in tool_gaps if s.get('type') == 'fence']
    return [{'link': c['link'], 'gap_mm': _fence_clearance(c, fence), 'surface': '围栏',
             'geometry': 'capsule'} for c in monitored]


def _ground_samples(model, q, settings, monitored, tool_gaps=None):
    """地面间隙样本（含全部可动连杆与笔尖特例），evaluate 与 contact_profile 共用。"""
    if tool_gaps is None:
        fence = settings.get('fence', {})
        tool_gaps = model.tool_plane_gaps(q, fence) if hasattr(model, 'tool_plane_gaps') else []
    if tool_gaps:
        samples = [s for s in tool_gaps if s.get('type') == 'ground']
    else:
        samples = [{'link': c['link'], 'gap_mm': _ground_clearance(c),
                    'geometry': 'capsule'} for c in monitored]
    if hasattr(model, 'moving_ground_clearances'):
        samples = samples + list(model.moving_ground_clearances(q))
    if settings.get('mode') == 'sim' and getattr(model, 'pen_tool', False):
        # Explicit simulation contact tool; never grants real motion,
        # body contact, or permission to penetrate below the floor.
        samples = [{**s, 'gap_mm': min(s['pen_body_gap_mm'],
                    100. if s['pen_contact_gap_mm'] >= -.05 else s['pen_contact_gap_mm'])}
                   if 'pen_contact_gap_mm' in s else s for s in samples]
    return samples


def object_envelope(obj):
    """摆放物的外接球 (中心, 半径)；供球-球下界剪枝预计算一次。"""
    from ..domain.object_contacts import bounds
    lo_hi = bounds(obj)
    center = [(a + b) * .5 for a, b in lo_hi]
    radius = .5 * math.sqrt(sum((b - a) ** 2 for a, b in lo_hi))
    return center, radius


def _object_signed_gap(cap, obj, envelope=None, ceiling=None):
    """摆放物带符号间隙：盒体内部给出真实穿透深度，而不是 0 饱和。

    objects.capsule_object_gap 对盒体只算"线段到盒体外观距离"，端点落在盒内时恒为 -r，
    分不出 1mm 和 30mm 的穿透。这里在盒体局部系补一个点-盒 SDF，取更保守的那个。

    envelope/ceiling 是纯剪枝：胶囊外接球与物体外接球的最小可能距离若已经大于当前
    已记录的最小间隙，这个组合不可能改变 min_clearance，也不可能产生负值命中，于是
    跳过精确求距。判据本身一个字没改，只是不再算注定被 min() 吞掉的那一项。
    """
    if envelope is not None and ceiling is not None:
        a, b = cap['a'], cap['b']
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        half = .5 * math.sqrt(dx * dx + dy * dy + dz * dz)
        cx, cy, cz = envelope[0]
        ex, ey, ez = (a[0] + b[0]) * .5 - cx, (a[1] + b[1]) * .5 - cy, (a[2] + b[2]) * .5 - cz
        lower = math.sqrt(ex * ex + ey * ey + ez * ez) - half - float(cap.get('r_mm', 0.0)) - envelope[1]
        if lower > ceiling:
            return lower
    from .objects import capsule_object_gap, rotation_matrix
    gap = capsule_object_gap(cap, obj)
    if cap.get('convex_proxy') is not None:return gap
    if obj.get('kind') != 'box':
        return gap
    try:
        matrix = rotation_matrix(obj)
        half = [abs(float(v)) * float(obj.get('scale', 1)) / 2 for v in obj.get('size', ())]
        if len(half) != 3 or not all(math.isfinite(v) for v in half):
            return gap
        radius = float(cap.get('r_mm', 0.0))
        a, b = cap['a'], cap['b']
        worst = gap
        for point in (a, b, [(a[i] + b[i]) / 2 for i in range(3)]):
            local = [sum(matrix[j][i] * (point[j] - obj['xyz'][j]) for j in range(3)) for i in range(3)]
            edge = [abs(local[i]) - half[i] for i in range(3)]
            outside = math.sqrt(sum(max(v, 0.) ** 2 for v in edge))
            worst = min(worst, outside + min(max(edge), 0.) - radius)
        return worst
    except (KeyError, TypeError, ValueError):
        return gap


def contact_profile(model, q, settings, caps=None, objects=(), monitored=None, object_monitored=None):
    """{几何身份: 带符号间隙 mm}；负值即已相交，越负越深。

    身份 = (类型, 面/连杆/摆放物)。同一身份取最小值，保证两处样本不会被平均值掩盖。
    这是"退离"判据的唯一几何依据，不含任何告警布尔量或历史采样锁。
    monitored 是围栏/地面的末端口径；object_monitored 是摆放物的全连杆口径，
    缺省时退回 monitored。两边必须用同一口径，否则"新增相交面"会被口径差异伪造。
    """
    if caps is None:
        caps = model.capsules(q)
    if monitored is None:
        monitored = tool_capsules(model, caps)
    if object_monitored is None:
        object_monitored = monitored
    fence = settings.get('fence', {})
    tool_gaps = model.tool_plane_gaps(q, fence) if hasattr(model, 'tool_plane_gaps') else []
    gaps = {}

    def put(key, value):
        if value is None:
            return
        gaps[key] = value if key not in gaps else min(gaps[key], value)

    if fence.get('enabled', True):
        for s in _fence_samples(model, q, fence, monitored, tool_gaps):
            put(('fence', s.get('surface') or '围栏', s.get('link') or ''), s.get('gap_mm'))
    if settings.get('ground_enabled', True):
        for s in _ground_samples(model, q, settings, monitored, tool_gaps):
            put(('ground', s.get('link') or '', s.get('surface') or ''), s.get('gap_mm'))
    for obj in objects:
        if not obj.get('collide', True):
            continue
        for cap in object_monitored:
            put(('object', obj.get('name') or '', cap.get('link') or ''),
                _object_signed_gap(cap, obj))
    return gaps


def recede_verdict(before, after, *, require_progress=True, slack_mm=0.0):
    """起点已相交时，判断 after 是否只是"沿脱离方向退出"。

    判据全是几何量：
      1) 起点必须真的有相交（gap < -_RECEDE_TOUCH_MM），否则不放行；
      2) after 不允许出现起点没有的相交身份（穿到另一侧 / 换到别处相交）；
      3) 已相交身份只允许变大；slack_mm 是关节弦线的几何瞬时抖动余量，
         不改变"端点必须严格变大"这一条（4）；
      4) require_progress 时最小间距必须严格变大（纯切向平移不算退离）。
    不使用"当前是否 alert"之类的布尔量，也不缓存起点采样。
    """
    active = {k: g for k, g in before.items()
              if type(g) in (int, float) and g < -_RECEDE_TOUCH_MM}
    verdict = {"ok": False, "why": "", "touched": len(active),
               "start_min": min(active.values()) if active else None,
               "target_min": None, "deeper": [], "new": []}
    if not active:
        verdict["why"] = "起点未处于相交状态，退离放行不适用"
        return verdict
    verdict["target_min"] = min(after.get(k, float("inf")) for k in active)
    verdict["new"] = [k for k, g in after.items()
                      if type(g) in (int, float) and g < -_RECEDE_TOUCH_MM and k not in active]
    verdict["deeper"] = [k for k in active if k in after and after[k] < before[k] - slack_mm]
    if verdict["new"]:
        verdict["why"] = "出现新的相交面：" + "；".join(str(k) for k in verdict["new"][:3])
        return verdict
    if verdict["deeper"]:
        verdict["why"] = ("穿透加深：" + "；".join(
            "%s %.2f→%.2fmm" % (k, before[k], after[k]) for k in verdict["deeper"][:3]))
        return verdict
    if require_progress and not verdict["target_min"] > verdict["start_min"] + _RECEDE_EPS_MM:
        verdict["why"] = ("最小间距没有变大（%.2f→%.2fmm），不按退离放行"
                          % (verdict["start_min"], verdict["target_min"]))
        return verdict
    verdict["ok"] = True
    verdict["why"] = ("沿脱离方向退出：最小间距 %.2f→%.2fmm"
                      % (verdict["start_min"], verdict["target_min"]))
    return verdict


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
    # flange/tool against the fence and ground. Objects monitor every link.
    monitored = tool_capsules(model, caps)
    fence_pct = ground_pct = 0.0
    fence_gap = ground_gap = float("inf")
    fence = settings["fence"]
    tool_gaps=model.tool_plane_gaps(q,fence) if hasattr(model,'tool_plane_gaps') else []
    nearest_ground=nearest_fence=None
    if fence.get("enabled", True):
        wall_samples=_fence_samples(model, q, fence, monitored, tool_gaps)
        for sample in wall_samples:
            g = sample['gap_mm']
            if g<fence_gap:nearest_fence=sample;fence_gap=g
            fence_pct = max(fence_pct, max(0.0, min(100.0,
                100.0 * (1.0 - max(0.0, g) / _PROXIMITY_BAND_MM))))
            if g < -.05 if settings.get('mode')=='sim' else g <= 0:
                collisions.append({"type": "fence", "link": sample["link"], "gap_mm": round(g, 2)})
    if settings.get("ground_enabled", True):
        # 逐顶点求最低点是热路径里最贵的一步（一台机器人约有 8 万个源网格顶点）。
        # 胶囊包络本身包住源网格，所以"全部可动连杆的胶囊离地都超过接近带宽度"
        # 时，源网格只可能更远：ground_pct 必为 0、等级必为绿、不可能产生地面碰撞。
        # 这种情况下跳过求最低顶点，判定结果与精确计算完全一致。
        capsule_floor = min((_ground_clearance(c) for c in caps
                             if c.get("link") != "elfin_base"), default=float("inf"))
        # 笔尖/工具平面不在连杆胶囊里，必须一起看过才能跳过。
        tool_floor = min((s["gap_mm"] for s in (tool_gaps or []) if s.get("type") == "ground"),
                         default=float("inf"))
        ground_samples = ([] if min(capsule_floor, tool_floor) > _PROXIMITY_BAND_MM
                          else _ground_samples(model, q, settings, monitored, tool_gaps))
        for sample in ground_samples:
            g = sample['gap_mm']
            if g<ground_gap:nearest_ground=sample;ground_gap=g
            ground_pct = max(ground_pct, max(0.0, min(100.0,
                100.0 * (1.0 - max(0.0, g) / _PROXIMITY_BAND_MM))))
            if g < -.05 if settings.get('mode')=='sim' else g <= 0:
                collisions.append({"type": "ground", "link": sample['link'], "gap_mm": round(g, 2)})
    links={cap.get('link') for cap in caps}
    for h in (obj_hits or []):
        gap=h.get('gap_mm')
        negative=type(gap) in (int,float) and math.isfinite(gap) and gap<0
        if h.get('link') in links and (h.get('penetrating') is True or negative):
            collisions.append({"type": "object", "link": h["link"], "obj": h["name"],
                               "gap_mm": gap,"penetrating":True})
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
        # Workspace margins remain diagnostic in simulation. Only confirmed
        # geometric penetration below and actual joint-bound violations stop it.
        # SIM01：这里覆盖的是"接近余量"（warning/near），真机分支不经过这一段，
        # 真机 90/95/100 门槛与下面 collisions 的 ALARM 判定保持原样。
        lvl = joint_motion_level
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
