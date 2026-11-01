"""Bounded drawing geometry and the actual adapter's sampled TCP path."""
import math
from ..core.kinematics import interpolate_pose

SHAPES=('heart','square','rectangle','circle','nod','shake','text')
KEYS={'shape','center','plane','width_mm','height_mm','angle_deg','speed_pct','repeats'}

def number(value,lo,hi):
    if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
        raise ValueError('绘制参数必须是范围内的有限数值')
    return float(value)

def configuration(payload,mode):
    if not isinstance(payload,dict) or set(payload)-KEYS-{'range_size_mm','range_center_mm','center_policy','direction','text','strokes','font_size_mm','writing_z_mm'} or not KEYS<=set(payload):raise ValueError('绘制参数字段不完整或无效')
    p=dict(payload)
    direction=p.get('direction',0)
    if type(direction) not in (int,float) or direction not in (0,90,180,270) or (mode=='real' and direction!=0):raise ValueError('绘制朝向无效')
    p['direction']=direction
    if p['shape'] not in SHAPES or p['plane'] not in ('xy','xz','yz'):raise ValueError('绘制形状或平面无效')
    if not isinstance(p['center'],list) or len(p['center'])!=6:raise ValueError('中心需要完整六维TCP位姿')
    p['center']=[number(v,-10000,10000) for v in p['center']]
    if p['shape']=='text':
        from .text_writing import checked_text
        p.update(checked_text(payload,mode))
        if p['plane']!='xy' or p['direction']!=0 or p['center'][2:]!=[p['writing_z_mm']+15,180,0,0] or p.get('center_policy','fixed')!='fixed' or p['repeats']!=1:
            raise ValueError('文字采用地面书写姿态与一次书写')
    elif set(payload)&{'text','strokes','font_size_mm','writing_z_mm'}:raise ValueError('非文字绘制不接受文字笔画')
    for k in ('width_mm','height_mm'):p[k]=number(p[k],1,50 if mode=='real' else 500)
    p['speed_pct']=round(number(p['speed_pct'],1,5 if mode=='real' else 100),1)
    cap=5 if mode=='real' else 15
    p['angle_deg']=abs(number(p['angle_deg'],-cap,cap))
    if type(p['repeats']) is not int or not 1<=p['repeats']<=3:raise ValueError('重复次数需要1～3的整数')
    if p['shape'] in ('square','circle'):p['height_mm']=p['width_mm']
    if p['shape'] not in ('nod','shake') and p['angle_deg']!=0:raise ValueError('平面绘制不接受姿态摆动角度')
    if p['shape'] in ('nod','shake'):
        if p['angle_deg']<.5:raise ValueError('点头和摇头幅度至少0.5度')
        p.update(width_mm=20.,height_mm=20.,plane='xy')
    size=p.get('range_size_mm',[50,50,50] if mode=='real' else [220,80,220])
    if not isinstance(size,list) or len(size)!=3:raise ValueError('绘制范围需要宽、深、高三个尺寸')
    p['range_size_mm']=[number(v,5,50 if mode=='real' else 5000) for v in size]
    policy=p.get('center_policy','fixed')
    if policy not in ('fixed','range_lower'):raise ValueError('绘制落点策略无效')
    if policy=='range_lower':
        if mode!='sim':raise ValueError('真机必须采用当前有效TCP，不允许自动选择绘制落点')
        p.setdefault('range_center_mm',p['center'][:3])
    if 'range_center_mm' in p:
        if not isinstance(p['range_center_mm'],list) or len(p['range_center_mm'])!=3:raise ValueError('绘制范围中心需要三个坐标')
        p['range_center_mm']=[number(v,-10000,10000) for v in p['range_center_mm']]
        if mode=='real' and math.dist(p['range_center_mm'],p['center'][:3])>.001:raise ValueError('真机范围中心必须采用当前有效TCP')
        if mode=='sim' and policy=='range_lower':p['center']=p['range_center_mm'][:]+p['center'][3:]
    return p

def bounds(config):
    center=config.get('range_center_mm',config['center'][:3])
    size=config['range_size_mm']
    if config.get('direction',0) in (90,270):size=[size[1],size[0],size[2]]
    return {'frame':'base','min':[c-s/2 for c,s in zip(center,size)],
            'max':[c+s/2 for c,s in zip(center,size)],'margin_mm':2.}

class DrawingBoundsError(ValueError):
    """A sampled path leaves its checked workspace; never permission to execute."""


def check_bounds(point,box,margin=2.):
    if any(not a+margin<=v<=b-margin for a,v,b in zip(box['min'],point,box['max'])):
        raise DrawingBoundsError('工具作用点超出绘制范围或不足2毫米余量，请调整图形或范围')

def poses(config):
    if config['shape']=='text':
        from .text_writing import sequence
        return sequence(config)[0]
    c=config['center'];kind=config['shape'];width=config['width_mm'];height=config['height_mm']
    if kind in ('nod','shake'):
        rows=[];axis=3 if kind=='nod' else 5
        for sign in (1,-1,0):
            p=c[:];p[axis]+=sign*config['angle_deg'];rows.append(p)
    else:
        if kind in ('square','rectangle'):xy=[(-.5,-.5),(.5,-.5),(.5,.5),(-.5,.5),(-.5,-.5)]
        elif kind=='circle':xy=[(.5*math.cos(2*math.pi*i/32),.5*math.sin(2*math.pi*i/32)) for i in range(33)]
        else:
            points=[(16*math.sin(t)**3,13*math.cos(t)-5*math.cos(2*t)-2*math.cos(3*t)-math.cos(4*t)) for t in [2*math.pi*i/32 for i in range(33)]]
            xmin,xmax=min(x for x,y in points),max(x for x,y in points);ymin,ymax=min(y for x,y in points),max(y for x,y in points)
            xy=[((x-(xmin+xmax)/2)/(xmax-xmin),(y-(ymin+ymax)/2)/(ymax-ymin)) for x,y in points]
        a,b={'xy':(0,1),'xz':(0,2),'yz':(1,2)}[config['plane']];rows=[]
        for x,y in xy:
            p=c[:];p[a]+=x*width;p[b]+=y*height;rows.append(p)
        rows.append(c[:])
    angle=math.radians(config.get('direction',0));co,si=round(math.cos(angle)),round(math.sin(angle))
    for p in rows:
        x,y=p[0]-c[0],p[1]-c[1]
        p[0],p[1]=c[0]+co*x-si*y,c[1]+si*x+co*y
    return [c[:]]+[p[:] for _ in range(config['repeats']) for p in rows]

def line_distance(point,start,end):
    d=[b-a for a,b in zip(start,end)];den=sum(v*v for v in d)
    t=max(0,min(1,sum((p-a)*v for p,a,v in zip(point,start,d))/den)) if den else 0
    return math.dist(point,[a+t*v for a,v in zip(start,d)])

def trace_metadata(config,path):
    if config['shape']=='text':
        from .text_writing import sequence,paths
        return [point for stroke in paths(config) for point in stroke],sequence(config)[1]
    period=(len(path)-1)//config['repeats']
    if config['shape'] in ('nod','shake'):return path[:period+1],[False]+[True]*(len(path)-1)
    flags=[False]+[i not in (0,period-1) for _ in range(config['repeats']) for i in range(period)]
    return path[1:period],flags

def tcp_samples(model,route,start_pose,end_pose,check,*,box=None):
    maximum=0.;count=0
    for a,b in zip(route,route[1:]):
        n=max(2,math.ceil(max(abs(x-y) for x,y in zip(a,b))/.1))
        if n>2048:raise ValueError('分段路径采样超出上限')
        for i in range(n+1):
            check();q=[x+(y-x)*i/n for x,y in zip(a,b)];p=model.pose_vec(q)
            if box is not None:check_bounds(p[:3],box)
            maximum=max(maximum,line_distance(p[:3],start_pose[:3],end_pose[:3]));count+=1
    return maximum,count
