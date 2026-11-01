"""Convex proxy contacts shared by scene edits and gravity, in millimetres."""
import itertools
import math

CONTACT_MM = .05

def _dot(a,b):return sum(x*y for x,y in zip(a,b))
def _sub(a,b):return [x-y for x,y in zip(a,b)]
def _length(a):return math.sqrt(_dot(a,a))

def support(item,direction,shrink=0.):
    if item.get('convex_proxy') is not None:return support(item['convex_proxy'],direction,shrink)
    length=_length(direction)
    if length<1e-14:return list(item['xyz'])
    unit=[v/length for v in direction]
    if item.get('kind')=='capsule':
        end=max((item['a'],item['b']),key=lambda p:_dot(p,direction))
        return [end[i]+unit[i]*max(0.,item['r_mm']-shrink) for i in range(3)]
    from ..core.objects import rotation_matrix
    rotation=rotation_matrix(item);size=[v*item.get('scale',1.) for v in item['size']]
    local=[sum(rotation[j][i]*direction[j] for j in range(3)) for i in range(3)]
    if item['kind']=='sphere':offset=[unit[i]*size[0] for i in range(3)]
    else:
        if item['kind']=='cylinder':
            radial=math.hypot(local[0],local[1]);point=[local[i]*size[0]/radial if radial>1e-14 else 0. for i in range(2)]+[math.copysign(size[2]/2,local[2])]
        else:point=[math.copysign(size[i]/2,local[i]) for i in range(3)]
        offset=[sum(rotation[i][j]*point[j] for j in range(3)) for i in range(3)]
    return [item['xyz'][i]+offset[i]-unit[i]*shrink for i in range(3)]

def bounds(item):
    if item.get('convex_proxy') is not None:return bounds(item['convex_proxy'])
    if item.get('kind')=='capsule':return [(min(item['a'][i],item['b'][i])-item['r_mm'],max(item['a'][i],item['b'][i])+item['r_mm']) for i in range(3)]
    from .scene_bounds import object_bounds
    return object_bounds(item)

def _solve(matrix,values):
    n=len(values);rows=[list(row)+[value] for row,value in zip(matrix,values)]
    for col in range(n):
        pivot=max(range(col,n),key=lambda i:abs(rows[i][col]))
        if abs(rows[pivot][col])<1e-12:return None
        rows[col],rows[pivot]=rows[pivot],rows[col]
        factor=rows[col][col];rows[col]=[v/factor for v in rows[col]]
        for i in range(n):
            if i!=col:
                factor=rows[i][col];rows[i]=[a-factor*b for a,b in zip(rows[i],rows[col])]
    return [row[-1] for row in rows]

def _closest(points):
    best=None
    for count in range(1,min(4,len(points))+1):
        for indices in itertools.combinations(range(len(points)),count):
            subset=[points[i] for i in indices]
            matrix=[[_dot(a,b) for b in subset]+[1.] for a in subset]+[[1.]*count+[0.]]
            solved=_solve(matrix,[0.]*count+[1.])
            if solved is None or any(v < -1e-8 for v in solved[:count]):continue
            weights=solved[:count];point=[sum(w*p[i] for w,p in zip(weights,subset)) for i in range(3)]
            norm=_dot(point,point)
            if best is None or norm<best[0]:best=(norm,point,[p for w,p in zip(weights,subset) if w>1e-9])
    if best is None:raise ValueError('物体接触计算未收敛')
    return best[1],best[2]

def distance(first,second,shrink=0.):
    """GJK distance for boxes, spheres, cylinders and existing robot capsules."""
    kinds=(first['kind'],second['kind'])
    if kinds==('sphere','sphere'):
        return max(0.,math.dist(first['xyz'],second['xyz'])-first['size'][0]*first.get('scale',1)-second['size'][0]*second.get('scale',1)+2*shrink)
    if 'box' in kinds and ('capsule' in kinds or 'sphere' in kinds):
        from ..core.objects import capsule_object_gap
        box=first if first['kind']=='box' else second
        other=second if first['kind']=='box' else first
        cap=other if other['kind']=='capsule' else {'a':other['xyz'],'b':other['xyz'],'r_mm':other['size'][0]*other.get('scale',1)}
        return max(0.,capsule_object_gap(cap,box)+2*shrink)
    if kinds==('box','box') and not any(first.get('rotation_deg',(0,0,0))) and not any(second.get('rotation_deg',(0,0,0))):
        return math.sqrt(sum(max(0.,a[0]-b[1]+2*shrink,b[0]-a[1]+2*shrink)**2 for a,b in zip(bounds(first),bounds(second))))
    def point(direction):return _sub(support(first,direction,shrink),support(second,[-v for v in direction],shrink))
    direction=_sub(second.get('xyz',second.get('a')),first.get('xyz',first.get('a')))
    if _length(direction)<1e-9:direction=[1.,0.,0.]
    simplex=[point(direction)];closest=simplex[0]
    for _ in range(48):
        norm=_length(closest)
        if norm<1e-7:return 0.
        direction=[-v for v in closest];next_point=point(direction)
        if _dot(next_point,direction)-_dot(closest,direction)<=1e-8*max(1.,norm):return norm
        if any(_length(_sub(next_point,p))<1e-8 for p in simplex):return norm
        closest,simplex=_closest([*simplex,next_point])
    # A conservative distance is required for swept advancement: if the
    # iteration budget is exhausted, stop at this position instead of tunnelling.
    return 0.

def penetrates(first,second):
    a,b=bounds(first),bounds(second)
    if any(min(x[1],y[1])-max(x[0],y[0])<=CONTACT_MM for x,y in zip(a,b)):return False
    return distance(first,second,CONTACT_MM/2)<1e-6

def physical(item):
    # Board pieces and supports are physical; only visual task zones are exempt.
    task = item.get('task') or {}
    return task.get('role') not in ('zone_a','zone_b')

def validate_contacts(rows,capsules=(),changed=None,contact_ids=()):
    solids=[row for row in rows if physical(row)]
    boxes={row['id']:bounds(row) for row in solids}
    ordered=sorted(solids,key=lambda row:boxes[row['id']][0][0])
    for i,item in enumerate(ordered):
        a=boxes[item['id']]
        for other in ordered[i+1:]:
            b=boxes[other['id']]
            if b[0][0]>=a[0][1]-CONTACT_MM:break
            if changed is not None and item['id'] not in changed and other['id'] not in changed:continue
            if any(min(x[1],y[1])-max(x[0],y[0])<=CONTACT_MM for x,y in zip(a,b)):continue
            if penetrates(item,other):raise ValueError('物体相互重叠：'+item['name']+' / '+other['name'])
        if changed is not None and item['id'] not in changed:continue
        for capsule in capsules:
            if item['id'] in contact_ids and capsule.get('contact_allowed'):continue
            cap={**capsule,'kind':'capsule'}
            if penetrates(item,cap):raise ValueError('物体与机器人相交：'+item['name']+' / '+capsule['link'])

def swept_drop(item,target_z,obstacles):
    """Earliest vertical contact by conservative advancement, including thin tops."""
    start=item['xyz'][2];best=target_z;hit=None
    a=bounds(item)
    for obstacle in obstacles:
        b=bounds(obstacle)
        if any(min(a[i][1],b[i][1])-max(a[i][0],b[i][0])<=1e-7 for i in (0,1)):continue
        if a[2][0]>b[2][1]+start-best+CONTACT_MM or a[2][1]<b[2][0]-CONTACT_MM:continue
        position=start
        for _ in range(64):
            probe={**item,'xyz':[item['xyz'][0],item['xyz'][1],position]}
            gap=distance(probe,obstacle)
            if gap<=CONTACT_MM:
                if position>best:best,hit=position,obstacle
                break
            move=max(.0001,gap-CONTACT_MM/2)
            if position-move<best:break
            position-=move
        else:
            if position>best:best,hit=position,obstacle
    return best,hit
