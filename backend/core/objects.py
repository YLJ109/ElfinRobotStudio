"""场景摆放物：三维里放的箱体 / 球 / 圆柱，可开重力、落地、漂浮、与机器人碰。

这是本软件的**仿真道具**，和真机无关，任何一条都不会变成发给控制器的报文。
编辑和重力接触共用旋转缩放后的凸体代理；辅助任务区域不作为实体。
"""

from __future__ import annotations
from .storage import write_json
from ..schemas import validate_object_appearance, validate_object_transform
from ..paths import DATA_DIR as DATA

import json
import hashlib
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

FIELDS = ("name", "kind", "xyz", "size", "gravity", "floating", "collide", "floor", "color", "preset", "surface", "rotation_deg", "scale", "locked")


def _num(v, d=0.0):
    if type(v) not in (int, float) or not -1e6 <= v <= 1e6 or not math.isfinite(v):
        raise ValueError("场景尺寸和坐标需要 ±1000000mm 内的有限数值")
    return float(v)


def rotation_matrix(o):
    x, y, z = [math.radians(v) for v in o.get('rotation_deg', (0, 0, 0))]
    sx,cx,sy,cy,sz,cz = math.sin(x),math.cos(x),math.sin(y),math.cos(y),math.sin(z),math.cos(z)
    return [[cz*cy,cz*sy*sx-sz*cx,cz*sy*cx+sz*sx],
            [sz*cy,sz*sy*sx+cz*cx,sz*sy*cx-cz*sx],[-sy,cy*sx,cy*cx]]


def half_height(o):
    """Rotated proxy support in base Z; same transform as the rendered object."""
    s = [v*o.get('scale',1) for v in o['size']]
    if o['kind']=='sphere': return max(s[0],.1)
    row = rotation_matrix(o)[2]
    if o['kind']=='cylinder': return math.hypot(row[0],row[1])*s[0]+abs(row[2])*s[2]/2
    return sum(abs(row[i])*s[i]/2 for i in range(3))


def radius(o):
    s = [v*o.get('scale',1) for v in o['size']]
    if o['kind']=='sphere': return max(s[0],.1)
    if o['kind']=='cylinder': return math.hypot(max(s[0],.1),max(s[2],.1)/2)
    return math.hypot(*(max(v,.1)/2 for v in s))


class ObjectStore:
    def __init__(self, path=None, *, fence_getter=None, capsule_getter=None, contact_getter=None):
        self.path = path or DATA / "objects.json"
        self._lock = threading.RLock()
        self._fence_getter = fence_getter
        self._capsule_getter = capsule_getter
        self._contact_getter = contact_getter
        self._resting = set()
        self.items = []
        self._vel = {}               # id -> vz，只在内存里，不写盘
        from .scene_assets import SceneAssetStore
        self._asset_lookup = SceneAssetStore(self.path.parent/'scene_assets').get
        self._load()
        self.persisted_generation = 0
        self.persisted_disk = self.disk_signature()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        for o in raw if isinstance(raw, list) else []:
            try:
                fixed = self.sanitize(o)
            except ValueError:
                continue  # 历史坏记录不阻止启动；读取时不改写源文件。
            if fixed and not any(row['id']==fixed['id'] for row in self.items):
                self.items.append(fixed)

    def disk_signature(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None

    def _persist(self, rows, *, validate_all=False):
        """One durable write boundary for all internal scene producers.

        Physics may legitimately differ from the last saved geometry. Compare
        file identity with the last committed file, never with live positions.
        A successful write publishes its identity only after atomic replace.
        """
        with self._lock:
            if self.disk_signature() != self.persisted_disk:
                raise ValueError('检测到外部场景文件修改，未覆盖文件；请先重新载入')
            previous={item['id']:item for item in self.items}
            geometry=('xyz','size','rotation_deg','scale','kind','asset_id','preset','collide')
            changed={item['id'] for item in rows if item['id'] not in previous or any(previous[item['id']].get(key)!=item.get(key) for key in geometry)}
            # 围栏（安全边界）先于摆放质量（接触）校验：物体越界是更根本的
            # 安全问题，若先报"重叠"会误导用户去挪动一台根本不该在那里的物体。
            # 围栏只看真实外形，collide/preset 不参与外形判定。
            if self._fence_getter is not None:
                from ..domain.scene_bounds import validate_placement
                shape=('xyz','size','rotation_deg','scale','kind','asset_id','preset')
                for item in rows:
                    old=previous.get(item['id'])
                    if old is None or any(old.get(key)!=item.get(key) for key in shape):
                        validate_placement(item,self._fence_getter())
            from ..domain.object_contacts import validate_contacts
            capsules=self._capsule_getter() if self._capsule_getter is not None else ()
            if capsules is None and (changed or validate_all):raise ValueError('当前机器人几何未知，未修改场景')
            contacts=self._contact_getter() if self._contact_getter else ()
            validate_contacts(rows,capsules or (),None if validate_all else changed,contacts)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            write_json(self.path,rows)
            self.persisted_disk=self.disk_signature()
            self.persisted_generation+=1
            if changed or set(previous)!={item['id'] for item in rows}:self._resting.clear()

    def _save(self):
        self._persist(self.items)

    @staticmethod
    def _sanitize(o, *, asset_lookup=None):
        if not isinstance(o, dict):
            raise ValueError("摆放物必须是对象")
        for key in ("gravity", "floating", "collide", "floor"):
            if key in o and type(o[key]) is not bool:
                raise ValueError(key + "需要布尔值")
        kind = o.get("kind", "box")
        if not isinstance(kind,str) or kind not in KINDS:
            raise ValueError('物体碰撞代理需为box/sphere/cylinder')
        appearance = validate_object_appearance(o,kind)
        transform = validate_object_transform(o)
        task = o.get('task')
        if task is not None:
            required = {'group','mode','role','symbol','slot','home'}
            if (not isinstance(task,dict) or set(task)!=required
                    or not isinstance(task['group'],str) or not re.fullmatch(r'[a-f0-9]{32}',task['group'])
                    or task['mode'] not in ('chess','sorting','handling') or task['role'] not in ('piece','zone_a','zone_b','obstacle')
                    or task['symbol'] not in ('','O','X') or type(task['slot']) is not int or not -1<=task['slot']<=26
                    or type(task['home']) is not int or not 0<=task['home']<=26):
                raise ValueError('任务物体标记无效')
            task = deepcopy(task)
            if task['role']!='obstacle' and (transform['scale']!=1 or any(transform['rotation_deg'])):
                raise ValueError('任务工件和区域不支持独立旋转或缩放，请重新布置任务')

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
        if any(v*transform['scale']>1e6 for v in size):
            raise ValueError('缩放后尺寸不能超过1000000毫米')
        if appearance.get('asset_id'):
            from .scene_assets import SceneAssetStore
            asset=(asset_lookup or SceneAssetStore().get)(appearance['asset_id'])
            if any(v*transform['scale']>500 for v in size) or task is not None:
                raise ValueError('导入资源各边不能超过500毫米，且不能直接作为可抓取任务物料')
            if asset['role']=='obstacle' and o.get('collide',True) is not True:
                raise ValueError('障碍资源必须保留碰撞检查')
        # 正确：创建、修改尺寸、载入旧现场也不能把物体底部放到地面以下。
        support=half_height(dict(kind=kind,size=size,**transform))
        if support>1e6:raise ValueError('旋转后物体包络超出坐标范围')
        xyz[2] = max(xyz[2], support)
        return {"id": str(o.get("id") or uuid.uuid4().hex[:12]),
                "name": str(o.get("name") or "物体")[:24],
                "kind": kind, "xyz": xyz, "size": size,
                "gravity": bool(o.get("gravity", False)),
                "floating": bool(o.get("floating", False)),
                "collide": not (task is not None and task.get("role") in ("zone_a","zone_b")),
                "floor": True,
                **transform, **appearance, **({'task':task} if task is not None else {})}

    # ------------------------------------------------------------------ CRUD
    def list(self):
        with self._lock:
            return deepcopy(self.items)

    def sanitize(self, value):
        return self._sanitize(value,asset_lookup=self._asset_lookup)

    def add(self, spec):
        with self._lock:
            o = self.sanitize(dict(spec or {}))
            if not o:
                raise ValueError("摆放物字段不完整")
            o["name"] = o["name"] if o["name"] != "物体" else f"{o['kind']}{len(self.items) + 1}"
            if any(row['id']==o['id'] for row in self.items):raise ValueError('物体ID已存在')
            candidate = [*self.items, o]
            self._persist(candidate)
            self.items = candidate
            return deepcopy(o)

    @staticmethod
    def patched(original, patch, *, sanitizer=None):
        if not isinstance(patch, dict): raise ValueError('摆放物修改必须是对象')
        if set(patch)-set(FIELDS): raise ValueError('物体修改包含未知字段')
        if original.get('locked') and patch != {'locked': False}: raise ValueError('物体已锁定，请先解锁')
        o=deepcopy(original)
        for key,value in patch.items():
            if key in ('gravity','floating','collide','floor','locked'):
                if type(value) is not bool: raise ValueError(key+'需要布尔值')
                o[key]=value
            elif key in ('xyz','size','rotation_deg'):
                if not isinstance(value,(list,tuple)) or len(value)!=3: raise ValueError('坐标、尺寸和旋转需要三个数值')
                o[key]=[_num(v) for v in value]
                if key=='size':o[key]=[max(v,1) for v in o[key]]
            elif key=='scale': o[key]=value
            elif key in ('kind','preset','surface','color'): o[key]=value
            else:o[key]=str(value)[:24]
        if 'kind' in patch and 'preset' not in patch and o.get('preset') in KINDS:o['preset']=o['kind']
        checked=(sanitizer or ObjectStore._sanitize)(o)
        if {'rotation_deg','scale'} & set(patch) and checked['xyz'][2]>o['xyz'][2]+1e-6:
            raise ValueError('变换后物体将穿过地面，请先抬高位置')
        return checked

    def update(self, oid, patch):
        with self._lock:
            candidate=deepcopy(self.items)
            for index,original in enumerate(candidate):
                if original['id']!=oid:continue
                changed=self.patched(original,patch,sanitizer=self.sanitize);candidate[index]=changed
                self._persist(candidate);self.items=candidate
                if 'xyz' in patch:self._vel.pop(oid,None)
                return deepcopy(changed)
            return None

    def remove(self, oid):
        with self._lock:
            if any(o['id']==oid and o.get('locked') for o in self.items):raise ValueError('物体已锁定，请先解锁')
            n = len(self.items)
            candidate = [o for o in self.items if o["id"] != oid]
            self._persist(candidate)
            self.items = candidate
            self._vel.pop(oid, None)
            return len(self.items) < n

    def clear(self):
        with self._lock:
            if any(o.get('locked') for o in self.items):raise ValueError('现场含锁定物体，请先解锁')
            n = len(self.items)
            self._persist([])
            self.items, self._vel = [], {}
            return n

    def replace(self, items, *, validate_all=True):
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
                fixed = self.sanitize(raw)
                if fixed:
                    if any(row['id']==fixed['id'] for row in next_items):raise ValueError('现场含重复物体ID，未载入任何物体')
                    next_items.append(fixed)
            self._persist(next_items,validate_all=validate_all)
            self.items, self._vel = next_items, {}
            return self.list()

    # ------------------------------------------------------------------ 物理
    def step(self, dt, capsules, contact_ids=(), hit_capsules=None):
        """Gravity uses earliest convex contact, not the preceding frame's Z.

        Solid object tops support stacks. Contacts stop descent without creating
        robot-collision alarms; only actual penetration is reported as a hit.
        `capsules` is the support/physics envelope (slightly conservative);
        `hit_capsules` is the reporting envelope and must match the safety and
        planning caliber, otherwise an inflated capsule brushing an object
        would be reported as a real penetration.
        """
        from ..domain.object_contacts import physical,swept_drop,penetrates,bounds,CONTACT_MM
        hits=[]
        with self._lock:
            dt=max(0.,min(.1,float(dt)))
            resting=set()
            # Lower supports settle before the objects above them in this step.
            ordered=sorted(self.items,key=lambda o:o['xyz'][2]-half_height(o))
            robot=[{**cap,'kind':'capsule'} for cap in capsules]
            robot_bounds=[bounds(cap) for cap in robot]
            check=[{**cap,'kind':'capsule'} for cap in (hit_capsules if hit_capsules is not None else capsules)]
            check_bounds=[bounds(cap) for cap in check]
            for item in ordered:
                oid=item['id']
                held=oid in contact_ids
                if not held and item['gravity'] and not item['floating'] and not item.get('locked'):
                    velocity=self._vel.get(oid,0.)-G_MM_S2*dt
                    before=item['xyz'][2]
                    target=max(half_height(item),before+velocity*dt) if item['floor'] else max(-FALL_FLOOR_MM,before+velocity*dt)
                    obstacles=[other for other in self.items if other['id']!=oid and physical(other)] if physical(item) else []
                    position,contact=swept_drop(item,target,[*obstacles,*robot] if physical(item) else [])
                    item['xyz'][2]=position
                    settled=contact is not None or (item['floor'] and position<=half_height(item)+.05)
                    self._vel[oid]=0. if settled else velocity
                    if settled:resting.add(oid)
                else:self._vel[oid]=0.
                if physical(item):
                    item_bounds=bounds(item)
                    for cap,cap_bounds in zip(check,check_bounds):
                        if any(min(a[1],b[1])-max(a[0],b[0])<=CONTACT_MM for a,b in zip(item_bounds,cap_bounds)):continue
                        if held and cap.get('contact_allowed'):continue
                        if penetrates(item,cap):
                            gap=capsule_object_gap(cap,item) if item['kind']=='box' else None
                            hits.append({'id':oid,'name':item['name'],'link':cap['link'],'gap_mm':round(gap,3) if gap is not None else None,'penetrating':True})
                            break
            self._resting=resting
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
                if o["gravity"] and not o["floating"] and not o.get("locked"):
                    if self._vel.get(o["id"], 0.0) != 0.0 or o["xyz"][2] > half_height(o) + 1e-6 and o["id"] not in self._resting:
                        return True
            return False

    def with_vel(self):
        with self._lock:
            return [{**deepcopy(o), "vz": round(self._vel.get(o["id"], 0.0), 1), "supported":o["id"] in self._resting} for o in self.items]


def capsule_object_gap(cap,obj):
    """盒体用精确线段-AABB距离，避免薄棋子被外接球膨胀成高障碍。"""
    if obj['kind']!='box':return _capsule_sphere_dist(cap,obj['xyz'],radius(obj))
    matrix=rotation_matrix(obj)
    def local(point):return [sum(matrix[j][i]*(point[j]-obj['xyz'][j]) for j in range(3)) for i in range(3)]
    a,b=local(cap['a']),local(cap['b'])
    low=[-obj['size'][i]*obj.get('scale',1)/2 for i in range(3)]
    high=[-v for v in low]
    delta=[b[i]-a[i] for i in range(3)]
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
