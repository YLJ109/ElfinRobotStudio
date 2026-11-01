"""Conservative per-link OBBs from the exact installed 2F-85 GLB (metres)."""
import hashlib
import json
import math
import struct
from functools import lru_cache

import numpy as np

from . import tool_library


def rig_matrices(rig, closure):
    matrices = {}
    for link in rig['links']:
        origin = np.asarray(link['origin']).reshape(4, 4, order='F')
        q = link['open'] + closure*(link['closed']-link['open'])
        axis = np.asarray(link['axis'], dtype=float)
        motion = np.eye(4)
        if link['type'] == 'revolute':
            x,y,z = axis
            skew = np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
            motion[:3,:3] += math.sin(q)*skew + (1-math.cos(q))*(skew@skew)
        elif link['type'] == 'prismatic':
            motion[:3,3] = axis*q
        local = origin@motion
        matrices[link['name']] = matrices[link['parent']]@local if link['parent'] else local
    return matrices


def inspect_asset(blob, entry):
    """Fail closed on a changed, malformed, externally referenced or merged asset."""
    if len(blob)<28 or len(blob)>25*1024*1024 or hashlib.sha256(blob).hexdigest()!=entry['sha256']:
        raise ValueError('工具碰撞资产校验失败')
    magic,version,length,jlen,jtype=struct.unpack_from('<IIIII',blob)
    if (magic,version,length,jtype)!=(0x46546c67,2,len(blob),0x4e4f534a):
        raise ValueError('工具碰撞 GLB 格式错误')
    doc=json.loads(blob[20:20+jlen])
    blen,btype=struct.unpack_from('<II',blob,20+jlen)
    if btype!=0x004e4942 or 28+jlen+blen!=len(blob):raise ValueError('工具碰撞数据不完整')
    if doc.get('extensionsUsed') or doc.get('extensionsRequired') or doc.get('images') or len(doc.get('buffers',[]))!=1 or doc['buffers'][0].get('uri'):
        raise ValueError('工具碰撞资产不能引用外部资源')
    rig=entry['animation']
    if doc.get('asset',{}).get('extras',{}).get('animation')!=rig:raise ValueError('工具碰撞关节目录不一致')
    binary=memoryview(blob)[28+jlen:]
    bind=rig_matrices(rig,0.)
    expected={p['name']:p['link'] for p in rig['parts']}
    parts=[]
    for node in doc['nodes']:
        if 'mesh' not in node:continue
        name=node.get('name');link=node.get('extras',{}).get('original_link')
        if name not in expected or expected.pop(name)!=link or any(k in node for k in ('matrix','translation','rotation','scale')):
            raise ValueError('工具碰撞缺少正式独立部件')
        chunks=[]
        for primitive in doc['meshes'][node['mesh']]['primitives']:
            accessor=doc['accessors'][primitive['attributes']['POSITION']]
            view=doc['bufferViews'][accessor['bufferView']]
            if accessor['componentType']!=5126 or accessor['type']!='VEC3' or accessor.get('sparse') or view.get('byteStride',12)!=12 or view.get('buffer',0)!=0:
                raise ValueError('工具碰撞顶点布局无效')
            count=accessor['count'];offset=view.get('byteOffset',0)+accessor.get('byteOffset',0)
            if not 0<count<1000000 or offset+count*12>len(binary):raise ValueError('工具碰撞顶点越界')
            chunks.append(np.frombuffer(binary,dtype='<f4',count=count*3,offset=offset).reshape(-1,3).astype(float))
        vertices=np.concatenate(chunks)
        if not np.isfinite(vertices).all():raise ValueError('工具碰撞顶点无效')
        inverse=np.linalg.inv(bind[link])
        local=vertices@inverse[:3,:3].T+inverse[:3,3]
        low,high=local.min(0),local.max(0)
        if np.any(high-low<=0):raise ValueError('工具碰撞包络为空')
        parts.append((name,link,(low+high)/2,high-low))
    if expected or len(parts)!=len(rig['parts']):raise ValueError('工具碰撞部件不完整')
    return rig,parts


@lru_cache(maxsize=1)
def asset():
    entry=tool_library.get('robotiq-2f85')
    path=tool_library.CATALOG.parent/'robotiq-2f85.glb'
    return inspect_asset(path.read_bytes(),entry)


def capsules(flange, mount_rotation, closure, link):
    """Broad spheres carry an OBB narrow phase; never treat broad radii as fingers."""
    from .kinematics import mat_to_xyzrpy
    rig,parts=asset()
    transforms=rig_matrices(rig,closure)
    base=np.asarray(flange)@np.asarray(mount_rotation)
    result=[]
    for name,source,center,size in parts:
        matrix=base@transforms[source]
        xyz=(matrix[:3,:3]@center+matrix[:3,3])*1000
        _,rpy=mat_to_xyzrpy(matrix.tolist())
        proxy={'kind':'box','xyz':xyz.tolist(),'size':(size*1000).tolist(),
               'rotation_deg':[math.degrees(v) for v in rpy]}
        result.append({'link':link,'part':name,'a':xyz.tolist(),'b':xyz.tolist(),
                       'r_mm':float(np.linalg.norm(size)*500),'convex_proxy':proxy,
                       'contact_allowed':source.endswith('finger_pad')})
    return result
