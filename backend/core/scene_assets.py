"""Bounded, immutable static GLB cache. No file extraction, URLs or device IO."""
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import struct
from .storage import write_json
from ..paths import DATA_DIR

MAX_BYTES=1024*1024
MAX_TRIANGLES=50000
ID=re.compile(r'^[a-f0-9]{64}$')


def finite(values,count):
    return isinstance(values,list) and len(values)==count and all(type(v) in (int,float) and math.isfinite(v) for v in values)


def _inspect_glb(data, metadata):
    if not isinstance(data,bytes) or not 28<=len(data)<=MAX_BYTES:
        raise ValueError('模型需要有效GLB，大小不能超过1MiB')
    if not isinstance(metadata,dict) or set(metadata)-{'name','units','source','license','role','size_mm'}:
        raise ValueError('模型元数据字段无效')
    for field,limit in (('name',24),('source',300),('license',160)):
        value=metadata.get(field)
        if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):
            raise ValueError('请明确填写模型名称、来源和许可；未知许可不能导入')
        if field in ('source','license') and (value.strip().lower() in ('unknown','none','n/a','na','未知','无','待定')
                or any(token in value for token in ('待确认','未确认','待核实','未授权','来源不明','许可不明'))):
            raise ValueError('来源或许可尚未确认，不能导入')
    if metadata.get('units') not in ('m','cm','mm') or metadata.get('role') not in ('decoration','obstacle'):
        raise ValueError('请确认原始单位和装饰/障碍用途')
    if struct.unpack_from('<III',data)!=(0x46546c67,2,len(data)):
        raise ValueError('GLB头或长度无效')
    offset=12;chunks=[]
    while offset<len(data):
        if offset+8>len(data): raise ValueError('GLB块头损坏')
        length,kind=struct.unpack_from('<II',data,offset);offset+=8
        if length%4 or offset+length>len(data): raise ValueError('GLB块长度无效')
        chunks.append((kind,data[offset:offset+length]));offset+=length
    if len(chunks)!=2 or chunks[0][0]!=0x4e4f534a or chunks[1][0]!=0x004e4942 or len(chunks[0][1])>256*1024:
        raise ValueError('仅支持JSON和内嵌BIN组成的GLB')
    try:
        doc=json.loads(chunks[0][1].decode('utf-8'))
    except (ValueError,UnicodeError,RecursionError) as exc: raise ValueError('GLB结构无法解析') from exc
    binary=chunks[1][1]
    def visit(value,depth=0):
        if depth>24: raise ValueError('模型结构层级过深')
        if isinstance(value,dict):
            if 'uri' in value: raise ValueError('模型不能引用外链或附属文件')
            if value.get('extensions') or any(key.endswith('Texture') for key in value):
                raise ValueError('当前不支持模型扩展或贴图')
            for child in value.values():visit(child,depth+1)
        elif isinstance(value,list):
            if len(value)>100000: raise ValueError('模型数组超过预算')
            for child in value:visit(child,depth+1)
        elif isinstance(value,float) and not math.isfinite(value): raise ValueError('模型含非有限数值')
    visit(doc)
    if (not isinstance(doc,dict) or doc.get('asset',{}).get('version')!='2.0'
            or any(doc.get(key) for key in ('images','textures','animations','skins','extensionsUsed','extensionsRequired'))):
        raise ValueError('当前只支持无贴图、扩展、骨骼与动画的静态GLB')
    if doc.get('extensions'):raise ValueError('当前不支持GLB扩展')
    buffers=doc.get('buffers',[])
    if len(buffers)!=1 or type(buffers[0].get('byteLength')) is not int or not len(binary)-3<=buffers[0]['byteLength']<=len(binary):
        raise ValueError('内嵌缓冲区长度不一致')
    views=doc.get('bufferViews',[]);accessors=doc.get('accessors',[])
    if not 1<=len(views)<=512 or not 1<=len(accessors)<=512:
        raise ValueError('模型缓冲区数量超过预算')
    decoded=[];decoded_components=0
    components={5121:('B',1),5123:('H',2),5125:('I',4),5126:('f',4)}
    dimensions={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}
    for accessor in accessors:
        if not isinstance(accessor,dict) or 'sparse' in accessor: raise ValueError('不支持稀疏或损坏的访问器')
        index=accessor.get('bufferView');count=accessor.get('count');component=accessor.get('componentType')
        width=dimensions.get(accessor.get('type'))
        if type(index) is not int or not 0<=index<len(views) or type(count) is not int or not 1<=count<=100000 or component not in components or width is None:
            raise ValueError('顶点/索引访问器超预算或无效')
        decoded_components+=count*width
        if decoded_components>250000:raise ValueError('顶点解码内存超过预算')
        view=views[index]
        if not isinstance(view,dict) or view.get('buffer')!=0:raise ValueError('缓冲区引用无效')
        fmt,unit=components[component];item_size=width*unit
        start=view.get('byteOffset',0);length=view.get('byteLength');relative=accessor.get('byteOffset',0);stride=view.get('byteStride',item_size)
        if any(type(v) is not int or v<0 for v in (start,length,relative,stride)) or not item_size<=stride<=252:
            raise ValueError('顶点跨度无效')
        if start+length>buffers[0]['byteLength'] or relative+(count-1)*stride+item_size>length:
            raise ValueError('顶点缓冲区越界')
        rows=[struct.unpack_from('<'+fmt*width,binary,start+relative+i*stride) for i in range(count)]
        if any(not math.isfinite(v) for row in rows for v in row):raise ValueError('顶点含无效数值')
        decoded.append(rows)
    meshes=doc.get('meshes',[]);nodes=doc.get('nodes',[]);scenes=doc.get('scenes',[])
    materials=doc.get('materials',[])
    if not isinstance(materials,list) or len(materials)>32:raise ValueError('模型材质数量超过预算')
    for material in materials:
        if not isinstance(material,dict):raise ValueError('模型材质结构无效')
        pbr=material.get('pbrMetallicRoughness',{})
        if not isinstance(pbr,dict):raise ValueError('模型材质参数无效')
        for key,count,container in (('baseColorFactor',4,pbr),('emissiveFactor',3,material)):
            if key in container and (not finite(container[key],count) or any(not 0<=v<=1 for v in container[key])):raise ValueError('模型颜色参数无效')
        for key in ('metallicFactor','roughnessFactor'):
            if key in pbr and (type(pbr[key]) not in (int,float) or not 0<=pbr[key]<=1):raise ValueError('模型材质数值无效')
        if material.get('alphaMode','OPAQUE') not in ('OPAQUE','MASK','BLEND') or ('doubleSided' in material and type(material['doubleSided']) is not bool):raise ValueError('模型材质显示参数无效')
    if not 1<=len(meshes)<=64 or not 1<=len(nodes)<=128 or len(scenes)!=1 or doc.get('scene',0)!=0:
        raise ValueError('仅支持一个场景，最多64个网格与128个节点')
    triangles=0;mesh_positions=[];mesh_triangles=[];vertex_budget=0
    for mesh in meshes:
        previous_triangles=triangles
        primitives=mesh.get('primitives',[])
        if not 1<=len(primitives)<=64 or mesh.get('weights'):raise ValueError('网格结构超预算或含变形')
        positions=[]
        for primitive in primitives:
            attrs=primitive.get('attributes',{})
            if primitive.get('mode',4)!=4 or primitive.get('targets') or primitive.get('extensions') or set(attrs)-{'POSITION','NORMAL','TEXCOORD_0','COLOR_0'}:
                raise ValueError('仅支持静态三角网格和基础顶点属性')
            for semantic,accessor_id in attrs.items():
                if type(accessor_id) is not int or not 0<=accessor_id<len(decoded):raise ValueError('顶点属性引用无效')
            position_id=attrs.get('POSITION')
            if type(position_id) is not int or not 0<=position_id<len(decoded) or accessors[position_id].get('type')!='VEC3' or accessors[position_id].get('componentType')!=5126:
                raise ValueError('缺少浮点三维顶点')
            points=decoded[position_id];indices=primitive.get('indices')
            for semantic,accessor_id in attrs.items():
                expected={'POSITION':('VEC3',),'NORMAL':('VEC3',),'TEXCOORD_0':('VEC2',),'COLOR_0':('VEC3','VEC4')}[semantic]
                field=accessors[accessor_id]
                if field.get('type') not in expected or field.get('componentType')!=5126 or field.get('normalized',False) or len(decoded[accessor_id])!=len(points):
                    raise ValueError('模型顶点属性类型或数量不一致')
            material=primitive.get('material')
            if material is not None and (type(material) is not int or not 0<=material<len(materials)):
                raise ValueError('模型材质引用无效')
            vertex_budget+=len(points)
            if vertex_budget>150000:raise ValueError('网格顶点实例超过预算')
            if indices is not None:
                if type(indices) is not int or not 0<=indices<len(decoded) or accessors[indices].get('type')!='SCALAR' or accessors[indices].get('componentType') not in (5121,5123,5125):
                    raise ValueError('三角索引无效')
                index_values=[row[0] for row in decoded[indices]]
                if any(i>=len(points) for i in index_values):raise ValueError('三角索引越界')
                count=len(index_values)
            else:count=len(points)
            if count%3:raise ValueError('三角面顶点数量无效')
            triangles+=count//3
            if triangles>MAX_TRIANGLES:raise ValueError('模型超过5万三角面预算')
            positions.extend(points)
        mesh_positions.append(positions)
        mesh_triangles.append(triangles-previous_triangles)
    bounds=[];seen=set();instance_triangles=0
    identity=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]
    def multiply(a,b):return [sum(a[k*4+r]*b[c*4+k] for k in range(4)) for c in range(4) for r in range(4)]
    def node_matrix(node):
        if 'matrix' in node:
            if not finite(node['matrix'],16):raise ValueError('节点矩阵无效')
            if any(abs(node['matrix'][i]-v)>1e-8 for i,v in ((3,0),(7,0),(11,0),(15,1))):raise ValueError('仅支持仿射节点矩阵')
            return node['matrix']
        t=node.get('translation',[0,0,0]);q=node.get('rotation',[0,0,0,1]);s=node.get('scale',[1,1,1])
        if not finite(t,3) or not finite(q,4) or not finite(s,3) or abs(sum(v*v for v in q)-1)>.001 or min(map(abs,s))<1e-9:raise ValueError('节点变换无效')
        x,y,z,w=q
        result=[1-2*y*y-2*z*z,2*x*y+2*w*z,2*x*z-2*w*y,0,
                2*x*y-2*w*z,1-2*x*x-2*z*z,2*y*z+2*w*x,0,
                2*x*z+2*w*y,2*y*z-2*w*x,1-2*x*x-2*y*y,0,*t,1]
        for col in range(3):
            for row in range(3):result[col*4+row]*=s[col]
        return result
    def walk(index,parent,depth=0):
        nonlocal instance_triangles
        if type(index) is not int or not 0<=index<len(nodes) or index in seen or depth>24:raise ValueError('节点循环、重复父节点或越界')
        seen.add(index);node=nodes[index]
        if not isinstance(node,dict) or node.get('skin') is not None or node.get('extensions'):raise ValueError('节点结构无效')
        matrix=multiply(parent,node_matrix(node))
        if 'mesh' in node:
            mesh=node['mesh']
            if type(mesh) is not int or not 0<=mesh<len(mesh_positions):raise ValueError('节点网格引用无效')
            instance_triangles+=mesh_triangles[mesh]
            if instance_triangles>MAX_TRIANGLES:raise ValueError('场景实例总三角面超过5万预算')
            for point in mesh_positions[mesh]:
                if len(bounds)>=150000:raise ValueError('场景顶点实例超过预算')
                bounds.append([sum(matrix[c*4+r]*point[c] for c in range(3))+matrix[12+r] for r in range(3)])
        for child in node.get('children',[]):walk(child,matrix,depth+1)
    for root in scenes[0].get('nodes',[]):walk(root,identity)
    if not bounds or len(bounds)>150000 or any(not math.isfinite(v) or abs(v)>1e6 for row in bounds for v in row):raise ValueError('模型包围盒无效或超预算')
    low=[min(row[i] for row in bounds) for i in range(3)];high=[max(row[i] for row in bounds) for i in range(3)]
    unit={'m':1000,'cm':10,'mm':1}[metadata['units']]
    natural=[max(1.,(high[i]-low[i])*unit) for i in (0,2,1)]
    size=metadata.get('size_mm',natural)
    if not finite(size,3) or not all(1<=v<=500 for v in size):raise ValueError('工件实际各边需要1至500毫米，请适配尺寸后重试')
    return {**metadata,'size_mm':list(size),'natural_size_mm':natural,'bounds':{'min':low,'max':high},
            'triangles':instance_triangles,'bytes':len(data),'proxy':'box','graspable':False,'format':'glb-static-v1'}


def inspect_glb(data,metadata):
    try:return _inspect_glb(data,metadata)
    except (TypeError,KeyError,IndexError,AttributeError,struct.error,OverflowError,RecursionError) as exc:
        raise ValueError('GLB结构损坏或包含不支持的字段') from exc


class SceneAssetStore:
    def __init__(self,path=None):self.path=Path(path) if path is not None else DATA_DIR/'scene_assets'
    def _file(self,asset_id):
        if not isinstance(asset_id,str) or not ID.fullmatch(asset_id):raise ValueError('资源标识无效')
        root=self.path.resolve();target=(root/(asset_id+'.json')).resolve()
        if target.parent!=root:raise ValueError('资源路径越界')
        return target
    def commit(self,data,info,*,checked=False):
        # Revalidate untrusted callers; only one atomic document contains both
        # source bytes and metadata, so a failed save cannot leave half an asset.
        validated=info if checked else inspect_glb(data,{key:info[key] for key in ('name','units','source','license','role','size_mm')})
        identity=hashlib.sha256(data+json.dumps(validated,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        target=self._file(identity)
        if not target.exists():
            existing=list(self.path.glob('*.json'))
            if len(existing)>=128 or sum(p.stat().st_size for p in existing)+len(data)*1.4>32*1024*1024:raise ValueError('资源缓存已到预算上限，请整理后重试')
            self.path.mkdir(parents=True,exist_ok=True)
            write_json(target,{'version':1,'asset_id':identity,'metadata':validated,'glb':base64.b64encode(data).decode('ascii')})
        return {'asset_id':identity,**validated}
    def _read(self,asset_id):
        path=self._file(asset_id)
        if not path.is_file() or path.stat().st_size>2*MAX_BYTES:raise ValueError('资源不存在或缓存损坏')
        try:
            doc=json.loads(path.read_text(encoding='utf-8'))
            data=base64.b64decode(doc['glb'],validate=True)
            if doc['asset_id']!=asset_id or len(data)>MAX_BYTES:raise ValueError()
            expected=hashlib.sha256(data+json.dumps(doc['metadata'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if expected!=asset_id:raise ValueError()
            return doc,data
        except (ValueError,KeyError,TypeError) as exc:raise ValueError('资源缓存损坏') from exc
    def get(self,asset_id):
        doc,data=self._read(asset_id)
        return {'asset_id':asset_id,**doc['metadata']}
    def read_glb(self,asset_id):return self._read(asset_id)[1]
