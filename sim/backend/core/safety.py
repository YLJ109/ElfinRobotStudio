"""安全评估：限位占用比 80/90/100 分级 + 胶囊体碰撞（围栏越界 / 地面 / 自碰撞）。

分级口径按用户 2026-10-11 明确要求：80% 黄、90% 红、100% 视为碰撞级（红闪 + 播报 + 自动暂停）。

围栏语义 = **允许工作范围**（keep-in），连杆穿出盒子才算越界；不是放在那里的障碍物。
碰撞包络是连杆胶囊体近似，不是逐三角形；宁可略保守也不漏报。
"""

from __future__ import annotations

from .kinematics import capsule_distance

GREEN, YELLOW, RED, ALARM = "green", "yellow", "red", "alarm"
RANK = {GREEN: 0, YELLOW: 1, RED: 2, ALARM: 3}
_SAMPLES = 14


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


def _signed_box_dist(p, box):
    """盒内为正（到最近面的余量），盒外为负（穿出多少 mm）。"""
    inside = True
    worst = float("inf")
    for k, (lo, hi) in enumerate(((box["x"][0], box["x"][1]),
                                  (box["y"][0], box["y"][1]),
                                  (box["z"][0], box["z"][1]))):
        d = min(p[k] - lo, hi - p[k])
        if d < 0:
            inside = False
            worst = min(worst, d)
        else:
            worst = min(worst, d)
    if inside:
        return worst
    # 外面：用各轴超出量合成距离，取负
    out = 0.0
    for k, (lo, hi) in enumerate(((box["x"][0], box["x"][1]),
                                  (box["y"][0], box["y"][1]),
                                  (box["z"][0], box["z"][1]))):
        d = (lo - p[k]) if p[k] < lo else ((p[k] - hi) if p[k] > hi else 0.0)
        out += d * d
    return -(out ** 0.5)


def _fence_clearance(cap, box):
    """胶囊表面到围栏边界的最小带符号距离；<0 表示已经穿出去。"""
    best = float("inf")
    for i in range(_SAMPLES + 1):
        t = i / _SAMPLES
        p = [cap["a"][k] + (cap["b"][k] - cap["a"][k]) * t for k in range(3)]
        best = min(best, _signed_box_dist(p, box) - cap["r_mm"])
    return best


def _ground_clearance(cap):
    return min(cap["a"][2], cap["b"][2]) - cap["r_mm"]


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
    reasons = []
    if lvl != GREEN:
        worst = ratios.index(max(ratios))
        reasons.append(f"J{worst + 1} 已到限位的 {max_pct:.1f}%")

    if caps is None:
        caps = model.capsules(q)
    collisions = []
    fence = settings["fence"]
    if fence.get("enabled", True):
        for c in caps:
            g = _fence_clearance(c, fence)
            if g < 0:
                collisions.append({"type": "fence", "link": c["link"], "gap_mm": round(g, 2)})
    if settings.get("ground_enabled", True):
        for c in caps:
            if c["link"] == "elfin_base":
                continue
            g = _ground_clearance(c)
            if g < 0:
                collisions.append({"type": "ground", "link": c["link"], "gap_mm": round(g, 2)})
    if settings.get("self_collision", True):
        idx = {f"elfin_link{i + 1}": i for i in range(len(model.names))}
        idx["elfin_base"] = -1
        for i, c1 in enumerate(caps):
            for c2 in caps[i + 1:]:
                k1, k2 = idx.get(c1["link"], 99), idx.get(c2["link"], 99)
                if abs(k1 - k2) <= 1:
                    continue          # 相邻连杆本来就连着，不算自碰撞
                g = capsule_distance(c1, c2, n=8)
                if g < 0:
                    collisions.append({"type": "self", "a": c1["link"], "b": c2["link"],
                                       "gap_mm": round(g, 2)})
    for h in (obj_hits or []):
        collisions.append({"type": "object", "link": h["link"], "obj": h["name"],
                           "gap_mm": h["gap_mm"]})
    if collisions:
        lvl = ALARM
        kinds = sorted({c["type"] for c in collisions})
        reasons.append("检测到越界/碰撞：" + "/".join(kinds) + f"（{len(collisions)} 处）")
        for c in collisions:
            if c["type"] == "object":
                reasons.append(f"摆放物「{c['obj']}」压到 {c['link']}")
                break
    return {"level": lvl, "max_pct": round(max_pct, 2), "per_joint": ratios,
            "collisions": collisions, "reasons": reasons,
            "capsules": [[round(v, 1) for v in c["a"]] + [round(v, 1) for v in c["b"]]
                         + [round(c["r_mm"], 1)] for c in caps]}
