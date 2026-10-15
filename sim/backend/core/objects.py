"""场景摆放物：三维里放的箱体 / 球 / 圆柱，可开重力、落地、漂浮、与机器人碰。

这是本软件的**仿真道具**，和真机无关，任何一条都不会变成发给控制器的报文。
碰撞用外接球近似（盒子取最远顶点），落地用几何最低点，宁可略保守也不漏报。
"""

from __future__ import annotations
from .storage import write_json

import json
import math
import threading
import uuid
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
G_MM_S2 = 9810.0                     # 毫米/秒²，9.81 m/s²
FALL_FLOOR_MM = 3000.0               # 关掉落地后的数值硬底
KINDS = ("box", "sphere", "cylinder")
COLORS = ("#4fd8cf", "#e0a33b", "#e4553f", "#7ad2ff", "#9b7bff", "#7ee08a")

FIELDS = ("name", "kind", "xyz", "size", "gravity", "floating", "collide", "floor", "color")


def _num(v, d=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else d
    except (TypeError, ValueError):
        return d


def half_height(o):
    """几何最低点到中心的距离：决定它落在地面上时球心该停在哪。"""
    k, s = o["kind"], o["size"]
    if k == "sphere":
        return max(s[0], 0.1)
    return max(s[2], 0.1) / 2.0


def radius(o):
    """外接球半径：盒子取最远顶点，圆柱取底面圆到顶面圆心的斜边。"""
    s = o["size"]
    if o["kind"] == "sphere":
        return max(s[0], 0.1)
    if o["kind"] == "cylinder":
        return math.hypot(max(s[0], 0.1), max(s[2], 0.1) / 2.0)
    return math.sqrt(sum((max(v, 0.1) / 2.0) ** 2 for v in s))


class ObjectStore:
    def __init__(self, path=None):
        self.path = path or DATA / "objects.json"
        self._lock = threading.RLock()
        self.items = []
        self._vel = {}               # id -> vz，只在内存里，不写盘
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        for o in raw if isinstance(raw, list) else []:
            fixed = self._sanitize(o)
            if fixed:
                self.items.append(fixed)

    def _save(self):
        DATA.mkdir(parents=True, exist_ok=True)
        write_json(self.path, self.items)

    @staticmethod
    def _sanitize(o):
        kind = o.get("kind") if o.get("kind") in KINDS else "box"

        def triple(v, d):
            raw = list(v or [])
            if not raw:
                raw = [d]
            if len(raw) == 1:                       # 球只给一个半径，补齐成三项
                raw = raw * 3
            raw = [_num(x) for x in raw[:3]]
            while len(raw) < 3:
                raw.append(raw[-1])
            return raw

        xyz = triple(o.get("xyz"), 0.0)
        size = [max(v, 1.0) for v in triple(o.get("size"), 100.0)]
        return {"id": str(o.get("id") or uuid.uuid4().hex[:12]),
                "name": str(o.get("name") or "物体")[:24],
                "kind": kind, "xyz": xyz, "size": size,
                "gravity": bool(o.get("gravity", False)),
                "floating": bool(o.get("floating", False)),
                "collide": bool(o.get("collide", True)),
                "floor": bool(o.get("floor", True)),
                "color": str(o.get("color") or "#4fd8cf")[:16]}

    # ------------------------------------------------------------------ CRUD
    def list(self):
        with self._lock:
            return [dict(o) for o in self.items]

    def add(self, spec):
        with self._lock:
            o = self._sanitize(dict(spec or {}))
            if not o:
                raise ValueError("摆放物字段不完整")
            o["name"] = o["name"] if o["name"] != "物体" else f"{o['kind']}{len(self.items) + 1}"
            self.items.append(o)
            self._save()
            return dict(o)

    def update(self, oid, patch):
        with self._lock:
            for o in self.items:
                if o["id"] != oid:
                    continue
                for k in FIELDS:
                    if k not in (patch or {}):
                        continue
                    v = patch[k]
                    if k in ("gravity", "floating", "collide", "floor"):
                        o[k] = bool(v)
                    elif k in ("xyz", "size"):
                        v = [_num(x) for x in (v or [])]
                        if len(v) != 3:
                            continue
                        o[k] = [max(x, 1.0) for x in v] if k == "size" else v
                        if k == "xyz":
                            self._vel.pop(oid, None)     # 挪了位置就把下落速度清零
                    else:
                        o[k] = str(v)[:24] if k == "name" else str(v)[:16]
                self._save()
                return dict(o)
            return None

    def remove(self, oid):
        with self._lock:
            n = len(self.items)
            self.items = [o for o in self.items if o["id"] != oid]
            self._vel.pop(oid, None)
            self._save()
            return len(self.items) < n

    def clear(self):
        with self._lock:
            n = len(self.items)
            self.items, self._vel = [], {}
            self._save()
            return n

    def replace(self, items):
        """载入一个现场文件，统一经过同一套字段清洗和限幅逻辑。"""
        if not isinstance(items, list):
            raise ValueError("摆放现场必须是数组")
        with self._lock:
            next_items = []
            for raw in items[:1000]:
                if not isinstance(raw, dict):
                    continue
                fixed = self._sanitize(raw)
                if fixed:
                    next_items.append(fixed)
            self.items, self._vel = next_items, {}
            self._save()
            return self.list()

    # ------------------------------------------------------------------ 物理
    def step(self, dt, capsules):
        """推进一格。返回与机器人接触/穿透的物体列表。

        落地：几何最低点碰到 z=0 就停住并把速度归零（不反弹）。
        碰撞：外接球和连杆胶囊体重叠就停在接触前那一帧，不穿过去。
        """
        hits = []
        with self._lock:
            dt = max(0.0, min(0.1, float(dt)))
            for o in self.items:
                r = radius(o)
                hh = half_height(o)
                if o["gravity"] and not o["floating"]:
                    vz = self._vel.get(o["id"], 0.0) - G_MM_S2 * dt
                    z0 = o["xyz"][2]
                    z1 = z0 + vz * dt
                    blocked = None
                    if o["floor"] and z1 - hh <= 0.0:
                        blocked = "floor"
                    if o["collide"]:
                        for c in capsules:
                            if c["link"] == "elfin_base":
                                continue
                            if _capsule_sphere_dist(c, o["xyz"], r) < 0.0:
                                blocked = c["link"]
                                break
                    if blocked:
                        vz = 0.0
                        self._vel[o["id"]] = 0.0
                        if blocked == "floor":
                            o["xyz"][2] = hh
                        else:
                            hits.append({"id": o["id"], "name": o["name"], "link": blocked,
                                         "gap_mm": 0.0})
                        continue
                    o["xyz"][2] = z1
                    # 关了落地就会一直掉，给个硬底把数值圈住，别让坐标跑到无穷
                    if o["xyz"][2] < -FALL_FLOOR_MM:
                        o["xyz"][2] = -FALL_FLOOR_MM
                        self._vel[o["id"]] = 0.0
                    else:
                        self._vel[o["id"]] = vz
                elif not o["gravity"] or o["floating"]:
                    self._vel[o["id"]] = 0.0
                # 静态/漂浮物也要每帧判一次是否压在机器人上
                if o["collide"] and not any(h["id"] == o["id"] for h in hits):
                    for c in capsules:
                        if c["link"] == "elfin_base":
                            continue
                        g = _capsule_sphere_dist(c, o["xyz"], r)
                        if g < 0.0:
                            hits.append({"id": o["id"], "name": o["name"], "link": c["link"],
                                         "gap_mm": round(g, 2)})
                            break
        return hits

    def any_active(self):
        """有没有摆放物还在动（开着重力、没漂浮、要么有速度要么还没落到地面上）。

        主循环靠这个决定能不能复用上一帧的安全结果 —— 机器人不动但箱子还在掉，
        一样得继续算，否则下落的物体会被缓存冻在半空。
        """
        # 只看"有没有东西还在掉"。机器人姿态变了要不要重判，由调用方把姿态放进
        # 缓存键里管 —— 把它也算进这里会让这个标志恒为真，缓存就白做了。
        with self._lock:
            for o in self.items:
                if o["gravity"] and not o["floating"]:
                    if self._vel.get(o["id"], 0.0) != 0.0 or o["xyz"][2] > half_height(o) + 1e-6:
                        return True
            return False

    def with_vel(self):
        with self._lock:
            return [{**o, "vz": round(self._vel.get(o["id"], 0.0), 1)} for o in self.items]


def _capsule_sphere_dist(cap, center, r):
    """胶囊表面到球心的距离减去两者半径；<0 就是重叠。"""
    a, b = cap["a"], cap["b"]
    ab = [b[i] - a[i] for i in range(3)]
    len2 = sum(v * v for v in ab) or 1e-9
    ap = [center[i] - a[i] for i in range(3)]
    t = max(0.0, min(1.0, sum(ap[i] * ab[i] for i in range(3)) / len2))
    closest = [a[i] + ab[i] * t for i in range(3)]
    d = math.sqrt(sum((center[i] - closest[i]) ** 2 for i in range(3)))
    return d - cap["r_mm"] - r
