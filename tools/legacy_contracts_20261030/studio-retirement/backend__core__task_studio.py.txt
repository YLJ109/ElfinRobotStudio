"""工艺几何与实际关节录制；不拥有运动或真机传输。"""
from __future__ import annotations
import copy
import math
import time
import uuid
from . import config
from .storage import write_json

MAX_WAYPOINTS = 256


def number(value, low=-10000., high=10000.):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('参数必须是范围内的有限数值')
    return float(value)


def vector(value, n=6):
    if not isinstance(value, list) or len(value) != n:
        raise ValueError(f'需要{n}个数值')
    return [number(v) for v in value]


def name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
        raise ValueError('名称需为1～80个可见字符')
    return value.strip()


def integer(value, low=1, high=64):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'数量需为{low}～{high}整数')
    return value


def generate(payload):
    kind = payload.get('kind')
    options = payload.get('options', {})
    if not isinstance(options, dict):
        raise ValueError('工艺参数需为对象')
    center = vector(options.get('center', [0, -400, 700]), 3)
    rpy = vector(options.get('rpy', [90, 0, 0]), 3)
    speed = number(options.get('speed', 10), 1, 100)
    plane = options.get('plane', 'xz')
    if plane not in ('xz', 'xy', 'yz'):
        raise ValueError('平面应为xy/xz/yz')
    axes = {'xz': (0, 2), 'xy': (0, 1), 'yz': (1, 2)}[plane]
    poses = []
    def append(pose):
        if len(poses) >= MAX_WAYPOINTS:
            raise ValueError('工艺最多256个路点，请增大间距或减少数量')
        poses.append(vector(pose))
    def plane_point(x, y):
        xyz = list(center)
        xyz[axes[0]] += x
        xyz[axes[1]] += y
        append(xyz + rpy)
    if kind == 'raster':
        width = number(options.get('width', 200), 1, 3000)
        height = number(options.get('height', 150), 1, 3000)
        spacing = number(options.get('spacing', 30), 1, 3000)
        rows = math.ceil(height / spacing) + 1
        if rows * 2 > MAX_WAYPOINTS:
            raise ValueError('扫描行数过多，请增大行距')
        for row in range(rows):
            y = -height / 2 + height * row / (rows - 1)
            for x in ((-width/2, width/2) if row % 2 == 0 else (width/2, -width/2)):
                plane_point(x, y)
    elif kind == 'circle':
        radius = number(options.get('radius', 80), 1, 1500)
        count = integer(options.get('segments', 32), 8, 128)
        for i in range(count + 1):
            plane_point(radius*math.cos(2*math.pi*i/count), radius*math.sin(2*math.pi*i/count))
    elif kind in ('pick-place', 'pallet'):
        pick = vector(options.get('pick', center + rpy))
        place = vector(options.get('place', [center[0]+150, *center[1:], *rpy]))
        lift = number(options.get('lift', 80), 1, 1000)
        rows = integer(options.get('rows', 1)) if kind == 'pallet' else 1
        cols = integer(options.get('cols', 1)) if kind == 'pallet' else 1
        layers = integer(options.get('layers', 1), 1, 32) if kind == 'pallet' else 1
        dx, dy, dz = (number(options.get(k, default), 1, 2000) for k, default in
                      [('step_x', 100), ('step_y', 100), ('step_z', 80)])
        if rows*cols*layers*6 > MAX_WAYPOINTS:
            raise ValueError('物料数量过多，每件6个路点，最多42件')
        for layer in range(layers):
            for row in range(rows):
                for col in range(cols):
                    target = list(place)
                    target[0] += col*dx; target[1] += row*dy; target[2] += layer*dz
                    high_pick, high_place = list(pick), list(target)
                    high_pick[2] += lift; high_place[2] += lift
                    for pose in (high_pick, pick, high_pick, high_place, target, high_place):
                        append(pose)
    else:
        raise ValueError('未知工艺类型')
    text = 'SPEED ' + format(speed, '.6g') + '\n' + '\n'.join('MOVEL '+','.join(format(v,'.8g') for v in p) for p in poses)
    return {'name': name(payload.get('name') or {'raster':'擦拭扫描','circle':'圆形扫描','pick-place':'搬运路径','pallet':'码垛路径'}[kind]),
            'kind':kind,'poses':poses,'count':len(poses),'program':text,'speed':speed,
            'note':'几何轨迹仿真；不包含接触力控或真实抓取。执行前需检查完整路径。'}


class RecordingStore:
    def __init__(self):
        self.path = config.DATA_DIR / 'studio_recordings.json'
        self.items = []
        self.active = None
        self.last_at = 0.
        if self.path.exists():
            import json
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value,list) or len(value)>100:
                raise ValueError('录制库格式无效')
            self.items = value

    def list(self):
        # 正确：目录查询不复制整库采样数组，活动页轮询成本只随录制条数增长。
        return copy.deepcopy([{k:v for k,v in item.items() if k not in ('points','joints','poses')} for item in self.items])

    def get(self, key):
        found = next((item for item in self.items if item['id']==key),None)
        if found is None:
            raise ValueError('录制轨迹不存在')
        return copy.deepcopy(found)

    def status(self):
        return {'active':self.active is not None,'name':(self.active or {}).get('name',''),
                'count':len((self.active or {}).get('points',[])),
                'interrupted':(self.active or {}).get('interrupted',''),
                'full':len((self.active or {}).get('points',[]))>=6000}

    def start(self, title):
        if self.active is not None:
            raise ValueError('已有录制，请先停止并保存')
        self.active = {'id':uuid.uuid4().hex,'name':name(title),'points':[],'created':time.time(),'revision':1}
        self.last_at = 0.
        return self.status()

    def observe(self, world):
        if self.active is None:
            return
        if world.settings.get('mode')!='sim' or world.real is not None:
            self.active['interrupted']='模式变化，录制已中断，等待保存'
            return
        now = time.monotonic()
        points = self.active['points']
        if len(points)>=6000 or now-self.last_at < .05 or self.active.get('interrupted'):
            return
        q = list(world.sim.q)
        if points and max(abs(a-b) for a,b in zip(q,points[-1]['q'])) < .01:
            return
        self.last_at = now
        self.active.setdefault('started',now)
        points.append({'t':round(now-self.active['started'],4),'q':vector(q),'tcp':vector(world.motion_model().pose_vec(q))})

    def stop(self):
        if self.active is None:
            raise ValueError('当前没有录制')
        item = copy.deepcopy(self.active)
        item.pop('started',None)
        item.update(joints=[p['q'] for p in item['points']],poses=[p['tcp'] for p in item['points']],
                    count=len(item['points']),duration=item['points'][-1]['t'] if item['points'] else 0.)
        if len(self.items)>=100:
            raise ValueError('录制库已满100条，请先导出或删除旧轨迹')
        candidate=[*self.items,item]
        write_json(self.path,candidate)
        self.items,self.active=candidate,None
        return copy.deepcopy(item)

    def edit(self,key,payload):
        item=self.get(key)
        if type(payload.get('revision')) is not int or payload['revision']!=item['revision']:
            raise ValueError('轨迹已被修改，请重新读取后再保存')
        joint_mode='joints' in payload
        field='joints' if joint_mode else 'poses'
        rows=payload.get(field,item.get(field,[]))
        if not isinstance(rows,list) or not 1<=len(rows)<=6000:
            raise ValueError('轨迹需有1～6000个点')
        rows=[vector(row) for row in rows]
        item['name']=name(payload.get('name',item['name']))
        item[field]=rows
        item['path_mode']='joints' if joint_mode else 'poses'
        item['points']=[]
        # 正确：编辑后原采样时序不再代表新路径的执行时长。
        item['duration']=None
        if joint_mode:item['poses']=[]
        else:item['joints']=[]
        item['count']=len(rows); item['revision']+=1
        candidate=[item if row['id']==key else row for row in self.items]
        write_json(self.path,candidate);self.items=candidate
        return copy.deepcopy(item)

    def remove(self,key):
        self.get(key)
        candidate=[row for row in self.items if row['id']!=key]
        write_json(self.path,candidate);self.items=candidate
