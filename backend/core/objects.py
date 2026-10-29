"""场景摆放物：三维里放的箱体 / 球 / 圆柱，可开重力、落地、漂浮、与机器人碰。

这是本软件的**仿真道具**，和真机无关，任何一条都不会变成发给控制器的报文。
碰撞用外接球近似（盒子取最远顶点），落地用几何最低点，宁可略保守也不漏报。
"""

from __future__ import annotations
from .storage import write_json
from ..schemas import validate_object_appearance
from ..paths import DATA_DIR as DATA

import json
import math
import threading
import uuid
import re
from copy import deepcopy
from pathlib import Path

G_MM_S2 = 9810.0                     # 毫米/秒²，9.81 m/s²
FALL_FLOOR_MM = 3000.0               # 关掉落地后的数值硬底
KINDS = ("box", "sphere", "cylinder")
COLORS = ("#4fd8cf", "#e0a33b", "#e4553f", "#7ad2ff", "#9b7bff", "#7ee08a")

FIELDS = ("name", "kind", "xyz", "size", "gravity", "floating", "collide", "floor", "color", "preset", "surface")


def _num(v, d=0.0):
    if type(v) not in (int, float) or not -1e6 <= v <= 1e6 or not math.isfinite(v):
        raise ValueError("场景尺寸和坐标需要 ±1000000mm 内的有限数值")
    return float(v)


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
    return math.hypot(*(max(v, 0.1) / 2.0 for v in s))


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
            try:
                fixed = self._sanitize(o)
            except ValueError:
                continue  # 历史坏记录不阻止启动；读取时不改写源文件。
            if fixed:
                self.items.append(fixed)

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, self.items)

    @staticmethod
    def _sanitize(o):
        if not isinstance(o, dict):
            raise ValueError("摆放物必须是对象")
        for key in ("gravity", "floating", "collide", "floor"):
            if key in o and type(o[key]) is not bool:
                raise ValueError(key + "需要布尔值")
        kind = o.get("kind", "box")
        if not isinstance(kind,str) or kind not in KINDS:
            raise ValueError('物体碰撞代理需为box/sphere/cylinder')
        appearance = validate_object_appearance(o,kind)
        task = o.get('task')
        if task is not None:
            required = {'group','mode','role','symbol','slot','home'}
            if (not isinstance(task,dict) or set(task)!=required
                    or not isinstance(task['group'],str) or not re.fullmatch(r'[a-f0-9]{32}',task['group'])
                    or task['mode'] not in ('chess','sorting') or task['role'] not in ('piece','zone_a','zone_b')
                    or task['symbol'] not in ('','O','X') or type(task['slot']) is not int or not -1<=task['slot']<=26
                    or type(task['home']) is not int or not 0<=task['home']<=26):
                raise ValueError('任务物体标记无效')
            task = deepcopy(task)

        def triple(v, d):
            if v is not None and not isinstance(v, (list, tuple)):
                raise ValueError("场景坐标和尺寸必须是数值数组")
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
        # 正确：创建、修改尺寸、载入旧现场也不能把物体底部放到地面以下。
        xyz[2] = max(xyz[2], size[0] if kind == 'sphere' else size[2] / 2.0)
        return {"id": str(o.get("id") or uuid.uuid4().hex[:12]),
                "name": str(o.get("name") or "物体")[:24],
                "kind": kind, "xyz": xyz, "size": size,
                "gravity": bool(o.get("gravity", False)),
                "floating": bool(o.get("floating", False)),
                "collide": bool(o.get("collide", True)),
                "floor": True,
                **appearance, **({'task':task} if task is not None else {})}

    # ------------------------------------------------------------------ CRUD
    def list(self):
        with self._lock:
            return deepcopy(self.items)

    def add(self, spec):
        with self._lock:
            o = self._sanitize(dict(spec or {}))
            if not o:
                raise ValueError("摆放物字段不完整")
            o["name"] = o["name"] if o["name"] != "物体" else f"{o['kind']}{len(self.items) + 1}"
            candidate = [*self.items, o]
            write_json(self.path, candidate)
            self.items = candidate
            return deepcopy(o)

    def update(self, oid, patch):
        if not isinstance(patch, dict):
            raise ValueError("摆放物修改必须是对象")
        with self._lock:
            candidate = deepcopy(self.items)
            for o in candidate:
                if o["id"] != oid:
                    continue
                for k in FIELDS:
                    if k not in (patch or {}):
                        continue
                    v = patch[k]
                    if k in ("gravity", "floating", "collide", "floor"):
                        if type(v) is not bool:
                            raise ValueError(k + "需要布尔值")
                        o[k] = v
                    elif k in ("xyz", "size"):
                        if not isinstance(v, (list, tuple)) or len(v) != 3:
                            raise ValueError("修改坐标和尺寸需要三个数值")
                        v = [_num(x) for x in (v or [])]
                        o[k] = [max(x, 1.0) for x in v] if k == "size" else v
                    else:
                        if k in ('kind','preset','surface','color'):
                            o[k] = v  # Validate exact types/whitelists below; never truncate bad input into a valid token.
                        else: o[k] = str(v)[:24]
                if 'kind' in patch and 'preset' not in patch and o.get('preset') in KINDS:
                    o['preset']=o['kind']
                checked=self._sanitize(o)
                o.clear();o.update(checked)
                write_json(self.path, candidate)
                self.items = candidate
                if 'xyz' in (patch or {}):
                    self._vel.pop(oid, None)
                return deepcopy(o)
            return None

    def remove(self, oid):
        with self._lock:
            n = len(self.items)
            candidate = [o for o in self.items if o["id"] != oid]
            write_json(self.path, candidate)
            self.items = candidate
            self._vel.pop(oid, None)
            return len(self.items) < n

    def clear(self):
        with self._lock:
            n = len(self.items)
            write_json(self.path, [])
            self.items, self._vel = [], {}
            return n

    def replace(self, items):
        """载入一个现场文件，统一经过同一套字段清洗和限幅逻辑。"""
        if not isinstance(items, list):
            raise ValueError("摆放现场必须是数组")
        if len(items) > 1000:
            raise ValueError("现场最多1000个物体，不能截断后丢失物体")
        with self._lock:
            next_items = []
            for raw in items[:1000]:
                if not isinstance(raw, dict):
                    raise ValueError("摆放物必须是对象，已拒绝整个现场")
                fixed = self._sanitize(raw)
                if fixed:
                    next_items.append(fixed)
            write_json(self.path, next_items)
            self.items, self._vel = next_items, {}
            return self.list()

    # ------------------------------------------------------------------ 物理
    def step(self, dt, capsules, contact_ids=()):
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
                            if capsule_object_gap(c,o) < 0.0:
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
                if o["collide"] and o['id'] not in contact_ids and not any(h["id"] == o["id"] for h in hits):
                    for c in capsules:
                        if c["link"] == "elfin_base":
                            continue
                        g = capsule_object_gap(c,o)
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
            return [{**deepcopy(o), "vz": round(self._vel.get(o["id"], 0.0), 1)} for o in self.items]


def capsule_object_gap(cap,obj):
    """盒体用精确线段-AABB距离，避免薄棋子被外接球膨胀成高障碍。"""
    if obj['kind']!='box':return _capsule_sphere_dist(cap,obj['xyz'],radius(obj))
    low=[obj['xyz'][i]-obj['size'][i]/2 for i in range(3)]
    high=[obj['xyz'][i]+obj['size'][i]/2 for i in range(3)]
    a=cap['a'];delta=[cap['b'][i]-a[i] for i in range(3)]
    cuts={0.,1.}
    for i in range(3):
        if abs(delta[i])>1e-12:
            for bound in (low[i],high[i]):
                t=(bound-a[i])/delta[i]
                if 0<t<1:cuts.add(t)
    cuts=sorted(cuts)
    def square(t):return sum((a[i]+delta[i]*t-min(high[i],max(low[i],a[i]+delta[i]*t)))**2 for i in range(3))
    closest=min(square(t) for t in cuts)
    for start,end in zip(cuts,cuts[1:]):
        middle=(start+end)/2;linear=quadratic=0.
        for i in range(3):
            value=a[i]+delta[i]*middle
            if low[i]<=value<=high[i]:continue
            bound=low[i] if value<low[i] else high[i]
            linear+=delta[i]*(a[i]-bound);quadratic+=delta[i]**2
        if quadratic:closest=min(closest,square(max(start,min(end,-linear/quadratic))))
    return math.sqrt(closest)-cap['r_mm']


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
