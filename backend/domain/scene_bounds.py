"""Editor camera/placement envelope; never changes robot motion qualification."""
import json
import math
from ..paths import PROJECT_ROOT

EXPANSION_SCALE=json.loads((PROJECT_ROOT/'contracts/scene_bounds.json').read_text(encoding='utf-8'))['expansion_scale']

def expanded_fence(fence):
    """110 percent of each full dimension, about the configured fence centre."""
    bounds=[]
    if not isinstance(fence,dict):raise ValueError('安全围栏范围不可用')
    for axis in ('x','y','z'):
        row=fence.get(axis)
        if (not isinstance(row,(list,tuple)) or len(row)!=2
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in row)
                or row[1]<=row[0]):raise ValueError('安全围栏范围不可用')
        center=(row[0]+row[1])/2;half=(row[1]-row[0])*EXPANSION_SCALE/2
        bounds.append((center-half,center+half))
    return bounds

def object_bounds(item):
    from ..core.objects import rotation_matrix
    rotation=rotation_matrix(item);size=[v*item.get('scale',1) for v in item['size']]
    kind=item['kind']
    if kind=='sphere':half=[size[0]]*3
    elif kind=='cylinder':half=[math.hypot(row[0],row[1])*size[0]+abs(row[2])*size[2]/2 for row in rotation]
    else:half=[sum(abs(row[i])*size[i]/2 for i in range(3)) for row in rotation]
    return [(position-radius,position+radius) for position,radius in zip(item['xyz'],half)]

def validate_placement(item,fence):
    limits=expanded_fence(fence);bounds=object_bounds(item)
    if any(low<limit_low-1e-7 or high>limit_high+1e-7 for (low,high),(limit_low,limit_high) in zip(bounds,limits)):
        raise ValueError('物体外形超出围栏允许范围，请移回或缩小物体')
    return item
