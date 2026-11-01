"""Portable data only. Restores publish a new directory, never mutate a live World."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
from ..core.objects import ObjectStore
from ..core.scenes import SceneStore
from ..core.scene_assets import SceneAssetStore, inspect_glb
from ..core.tools import ToolStore
from ..core.storage import write_json
from ..workspace_routes import profile_settings, PROFILE_KEYS

MAX_BYTES=32*1024*1024
SECTIONS={'points','objects','scenes','tools','io','settings','programs','assets'}
SECRET_KEYS={'token','password','secret','api_key','apikey','authorization','access_token','refresh_token'}

def digest(value):
    # JSON has one numeric type. Browser parse/stringify removes a trailing .0,
    # so integral floats and integers must share the same portable identity.
    def canonical(item):
        if isinstance(item,dict):return {k:canonical(v) for k,v in item.items()}
        if isinstance(item,list):return [canonical(v) for v in item]
        if type(item) is float and math.isfinite(item) and item.is_integer():return int(item)
        return item
    return hashlib.sha256(json.dumps(canonical(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def text(value,limit):
    if not isinstance(value,str) or not 1<=len(value)<=limit or any(ord(c)<32 for c in value):
        raise ValueError('备份名称或标识无效')
    return value

def vector(value,n):
    if not isinstance(value,list) or len(value)!=n or any(type(x) not in (int,float) or not math.isfinite(x) for x in value):
        raise ValueError('备份坐标需要有限数值')

def guard_tree(value,depth=0):
    if depth>24:raise ValueError('备份结构过深')
    if isinstance(value,dict):
        if any(not isinstance(k,str) or k.lower() in SECRET_KEYS or re.sub(r'[^a-z0-9]','',k.lower()).endswith(('apikey','token','password','secret','authorization')) for k in value):raise ValueError('备份不能包含凭据字段')
        for child in value.values():guard_tree(child,depth+1)
    elif isinstance(value,list):
        if len(value)>10000:raise ValueError('备份列表超过容量')
        for child in value:guard_tree(child,depth+1)
    elif isinstance(value,str) and re.search(r'(?i)(?:[A-Z_]*(?:API[_-]?KEY|TOKEN|PASSWORD|SECRET)|AUTHORIZATION)\s*[=:]\s*\S|Bearer\s+[A-Za-z0-9._-]{8,}',value):
        raise ValueError('备份文本含疑似凭据，请先移除后备份')
    elif isinstance(value,float) and not math.isfinite(value):raise ValueError('备份包含非有限数值')

def portable_tool(value):
    value=copy.deepcopy(value)
    # Installation, calibration and controller evidence are not portable grants.
    value['identity']={'source':'imported','hardware_id':None}
    value['verification']='unvalidated'
    for field in ('tcp','payload'):
        if isinstance(value.get(field),dict):value[field]['status']='unvalidated'
    return value

def validate_bundle(bundle):
    if not isinstance(bundle,dict) or set(bundle)!={'format','version','created_at','data','sha256'} or bundle['format']!='elfin-data-backup' or type(bundle['version']) is not int or bundle['version']!=1:
        raise ValueError('备份格式或版本不受支持')
    if type(bundle['created_at']) not in (int,float) or not math.isfinite(bundle['created_at']) or bundle['created_at']<0:raise ValueError('备份时间无效')
    encoded=json.dumps(bundle,ensure_ascii=False,allow_nan=False).encode()
    if len(encoded)>MAX_BYTES:raise ValueError('备份不能超过32MiB')
    guard_tree(bundle)
    raw=bundle['data']
    if not isinstance(raw,dict) or set(raw)!=SECTIONS or digest(raw)!=bundle['sha256']:raise ValueError('备份内容或校验值损坏')
    out=copy.deepcopy(raw)
    if not isinstance(out['assets'],dict) or len(out['assets'])>128:raise ValueError('资源数量超限')
    assets={}
    for asset_id,doc in out['assets'].items():
        if not isinstance(doc,dict) or set(doc)!={'version','asset_id','metadata','glb'} or type(doc['version']) is not int or doc['version']!=1 or doc['asset_id']!=asset_id:raise ValueError('备份资源身份无效')
        data=base64.b64decode(doc['glb'],validate=True)
        meta=doc['metadata'];info=inspect_glb(data,{k:meta[k] for k in ('name','units','source','license','role','size_mm')})
        expected=hashlib.sha256(data+json.dumps(info,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if expected!=asset_id or info!=meta:raise ValueError('备份资源内容不符')
        assets[asset_id]={'asset_id':asset_id,**info}
    def objects(rows):
        if not isinstance(rows,list) or len(rows)>1000:raise ValueError('备份场景最多1000个物体')
        for row in rows:
            if not isinstance(row,dict) or set(row)-{'id','name','kind','xyz','size','gravity','floating','collide','floor','color','preset','surface','rotation_deg','scale','locked','asset_id','task'}:raise ValueError('备份物体字段无效')
            text(row.get('id'),128);text(row.get('name'),24)
            vector(row.get('xyz'),3);vector(row.get('size'),3)
            if any(v<1 for v in row['size']):raise ValueError('备份物体尺寸无效')
            if row.get('asset_id') and row['asset_id'] not in assets:raise ValueError('场景引用了缺失资源')
        normalized=[ObjectStore._sanitize(row,asset_lookup=lambda key:assets[key]) for row in rows]
        if any(row['xyz']!=fixed['xyz'] or row['size']!=fixed['size'] for row,fixed in zip(rows,normalized)):raise ValueError('备份物体穿地或尺寸超限，未静默修改')
        if len({row['id'] for row in normalized})!=len(rows):raise ValueError('备份物体标识重复')
        return normalized
    out['objects']=objects(out['objects'])
    if not isinstance(out['scenes'],dict) or len(out['scenes'])>100:raise ValueError('备份现场数量超限')
    for name,item in out['scenes'].items():
        if SceneStore._name(name)!=name or not isinstance(item,dict) or set(item)-{'name','objects','updated_at','created_at'} or item.get('name',name)!=name:raise ValueError('备份现场格式无效')
        item['objects']=objects(item.get('objects'))
        for key in ('updated_at','created_at'):
            if type(item.get(key)) not in (int,float) or not math.isfinite(item[key]):raise ValueError('现场时间无效')
    if not isinstance(out['points'],list) or len(out['points'])>10000:raise ValueError('备份点位数量超限')
    ids=set();codes=set();names=set()
    for point in out['points']:
        if not isinstance(point,dict) or set(point)-{'id','code','name','joints','tcp','source','note','created','imported_source'}:raise ValueError('备份点位字段无效')
        if type(point.get('id')) is not int or not 0<=point['id']<=2**53-1 or point['id'] in ids:raise ValueError('点位ID无效或重复')
        code=point.get('code');name=text(point.get('name'),80)
        if not isinstance(code,str) or not re.fullmatch(r'P[1-9]\d*',code) or code in codes or name in names:raise ValueError('点位编号或名称重复')
        ids.add(point['id']);codes.add(code);names.add(name);vector(point.get('joints'),6)
        tcp=point.get('tcp')
        if isinstance(tcp,dict):
            if set(tcp)-{'xyz_mm','rpy_deg','link'}:raise ValueError('点位TCP字段无效')
            vector(tcp.get('xyz_mm'),3);vector(tcp.get('rpy_deg'),3)
        elif tcp is not None:vector(tcp,6)
        # Sim points remain sim. Hardware points lose executable provenance.
        for key,limit in (('note',2000),('imported_source',80)):
            if key in point and (not isinstance(point[key],str) or len(point[key])>limit):raise ValueError('点位说明或来源格式无效')
        if type(point.get('created')) not in (int,float) or not math.isfinite(point['created']) or point['created']<0:raise ValueError('点位时间无效')
        source=point.get('source')
        if source is not None and (not isinstance(source,str) or len(source)>80):raise ValueError('点位来源格式无效')
        if source not in ('sim','imported'):point['imported_source']=source or 'unknown';point['source']='imported'
    if any(point['name'] in codes and point['name']!=point['code'] for point in out['points']):raise ValueError('点位名称与稳定编号冲突')
    if not isinstance(out['tools'],list) or len(out['tools'])>100 or len({t.get('id') for t in out['tools'] if isinstance(t,dict)})!=len(out['tools']):raise ValueError('工具档案无效或重复')
    out['tools']=[portable_tool(t) for t in out['tools']]
    # Reuse the production validator in a disposable domain. No World/devices.
    with tempfile.TemporaryDirectory(prefix='elfin-backup-tool-') as folder:
        store=ToolStore(Path(folder)/'tools.json');store.items=[]
        for tool in out['tools']:store.add_or_update(tool)
    if not isinstance(out['io'],dict) or len(out['io'])>1000:raise ValueError('IO档案数量超限')
    for name,channel in out['io'].items():
        text(name,64)
        if not isinstance(channel,dict) or set(channel)!={'group','direction','bit'}:raise ValueError('IO档案字段无效')
        group,direction,bit=channel['group'],channel['direction'],channel['bit']
        maximum=4 if group=='end' and direction=='input' else 3 if group=='end' else 8
        if group not in ('end','box') or direction not in ('input','output') or type(bit) is not int or not 0<=bit<maximum:raise ValueError('IO通道无效')
    if out['settings']:out['settings']=profile_settings(out['settings'])
    elif not isinstance(out['settings'],dict):raise ValueError('设置格式无效')
    if not isinstance(out['programs'],dict) or len(out['programs'])>1000:raise ValueError('程序数量超限')
    for name,item in out['programs'].items():
        text(name,40)
        if not isinstance(item,dict) or set(item)-{'src','updated'} or not isinstance(item.get('src'),str) or len(item['src'])>200000:raise ValueError('程序格式无效')
        if type(item.get('updated')) not in (int,float) or not math.isfinite(item['updated']) or item['updated']<0:raise ValueError('程序时间无效')
        # Credentials in user scripts are deliberately not portable either.
        if re.search(r'(?i)(api[_-]?key|access[_-]?token|password|authorization)\s*[=:]',item['src']):raise ValueError('程序含疑似凭据，请先移除后备份')
    return out

def preview_changes(current,incoming):
    """Bounded, stable-ID diffs. Raw asset bytes never enter UI responses."""
    budget=100;output=[]
    def indexed(section,value):
        return {str(item['id']):item for item in value} if isinstance(value,list) else value
    def changed_fields(before,after,prefix=''):
        if isinstance(before,dict) and isinstance(after,dict):
            values=[]
            for key in sorted(set(before)|set(after)):
                if before.get(key)!=after.get(key):
                    path=prefix+'.'+key if prefix else key
                    values.extend(changed_fields(before.get(key),after.get(key),path))
            return values
        return [prefix or 'value']
    def summarize(section,value,other,fields):
        if value is None:return '无'
        if section=='assets':value=value['metadata']
        elif section=='programs':
            lines=value['src'].splitlines();other_lines=(other or {}).get('src','').splitlines()
            index=next((i for i,(a,b) in enumerate(zip(lines,other_lines)) if a!=b),min(len(lines),len(other_lines)))
            value={'lines':len(lines),'first_changed_line':index+1,'text':lines[index][:120] if index<len(lines) else '已删除/无后续行'}
        elif section=='scenes':value={'name':value['name'],'object_count':len(value['objects']),'objects':[v['name'] for v in value['objects'][:5]]}
        elif isinstance(value,dict) and other is not None:
            subset={}
            for field in fields[:8]:
                item=value
                for key in field.split('.'):
                    item=item.get(key) if isinstance(item,dict) else None
                subset[field]=item
            value=subset
        encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
        return encoded if len(encoded)<=240 else encoded[:237]+'…'
    for section in sorted(SECTIONS):
        before=indexed(section,current[section]);after=indexed(section,incoming[section]);rows=[];count=0
        for key in sorted(set(before)|set(after)):
            left,right=before.get(key),after.get(key)
            if left==right:continue
            count+=1
            if budget<=0:continue
            fields=changed_fields(left,right)
            if section=='assets':fields=[v for v in fields if not v.startswith('glb')]
            rows.append(dict(id=key,change='added' if key not in before else 'removed' if key not in after else 'changed',
                             fields=fields[:16],before=summarize(section,left,right,fields),after=summarize(section,right,left,fields)))
            budget-=1
        output.append(dict(section=section,before=len(before),after=len(after),changed=count>0,changed_count=count,rows=rows,truncated=count>len(rows)))
    return output


class DataBackup:
    def __init__(self,resources):
        self.resources=resources
        self.root=Path(resources.data_dir)
        self.previews={}
        self.published_manifest_digests={}

    def capture(self):
        r=self.resources;w=r.world
        scenes={row['name']:r.scenes.get(row['name']) for row in r.scenes.list()}
        data={'points':w.points.list(),'objects':w.objects.list(),'scenes':scenes,
              'tools':[portable_tool(t) for t in w.tools.list()],
              'io':{name:{k:row[k] for k in ('group','direction','bit')} for name,row in w.calibrations.items.get('io',{}).items()},
              'settings':{k:copy.deepcopy(w.settings[k]) for k in PROFILE_KEYS if k in w.settings},
              'programs':copy.deepcopy(r.programs.items),'assets':{}}
        referenced={o['asset_id'] for rows in [data['objects'],*[s['objects'] for s in scenes.values()]] for o in rows if o.get('asset_id')}
        assets=SceneAssetStore(self.root/'scene_assets')
        for key in referenced:data['assets'][key]=assets._read(key)[0]
        bundle={'format':'elfin-data-backup','version':1,'created_at':time.time(),'data':data,'sha256':digest(data)}
        return bundle

    @staticmethod
    def normalize_export(bundle):
        # Pure validation may run outside the device/event loop.
        data=validate_bundle(bundle);bundle['data']=data;bundle['sha256']=digest(data)
        return bundle

    def export(self):
        return self.normalize_export(self.capture())

    def preview(self,bundle,*,current_data=None,checked_data=None):
        data=validate_bundle(bundle) if checked_data is None else checked_data
        current=self.export()['data'] if current_data is None else current_data
        token=uuid.uuid4().hex
        now=time.monotonic()
        self.previews={k:v for k,v in self.previews.items() if now-v['at']<300}
        if len(self.previews)>=4:raise ValueError('恢复预览过多，请稍后重试')
        self.previews[token]={'at':now,'data':data,'current':digest(current),'bundle':copy.deepcopy(bundle)}
        return {'ok':True,'token':token,'expires_in':300,'changes':preview_changes(current,data),
                'requires_reconfirmation':['工具安装','IO接线','真机点位来源'],'restores_running_tasks':False}

    def restore(self,token,*,current_bundle=None):
        item=self.previews.pop(token,None)
        if not item or time.monotonic()-item['at']>=300:raise ValueError('恢复预览已失效，请重新检查')
        current=self.export() if current_bundle is None else current_bundle
        if digest(current['data'])!=item['current']:raise ValueError('当前数据已变化，请重新预览恢复差异')
        root=self.root.resolve()
        parent=root/'recovery'
        if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)() or parent.resolve()!=parent:raise ValueError('恢复父目录不能是跳转链接')
        parent.mkdir(parents=True,exist_ok=True)
        if len(list(parent.iterdir()))>=16:raise ValueError('恢复副本已达16份，请先备份并手动整理旧副本')
        identifier=uuid.uuid4().hex
        destination=parent/identifier
        stage=Path(tempfile.mkdtemp(prefix='.preparing-',dir=parent))
        try:
            data=item['data']
            documents={'points.json':data['points'],'points_meta.json':{'next_number':max([int(p['code'][1:]) for p in data['points']] or [0])+1},
                'objects.json':data['objects'],'scenes.json':{'version':1,'items':data['scenes']},
                'tools.json':{'version':1,'items':data['tools']},'mounted_tool.json':ToolStore._unknown(),
                'settings.json':{**data['settings'],'mode':'sim','auto_connect':False},'programs.json':data['programs'],
                'pending-io.json':{'version':1,'items':data['io'],'confirmed':False},
                'calibrations.json':{'version':0,'frames':{},'tools':{},'io':{}}}
            for name,value in documents.items():write_json(stage/name,value)
            for key,doc in data['assets'].items():write_json(stage/'scene_assets'/(key+'.json'),doc)
            # Keep a portable pre-restore backup as the recovery point.
            write_json(stage/'before-restore.backup.json',current)
            write_json(stage/'restore-manifest.json',{'version':1,'copy_id':identifier,'created_at':time.time(),'source_sha256':digest(data),'files':{str(p.relative_to(stage)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.rglob('*') if p.is_file()}})
            manifest_digest=hashlib.sha256((stage/'restore-manifest.json').read_bytes()).hexdigest()
            os.rename(stage,destination) # same volume, publishes only a complete new directory
        except BaseException:
            shutil.rmtree(stage)
            raise
        self.published_manifest_digests[identifier]=manifest_digest
        return {'ok':True,'copy_id':identifier,'manifest_digest':manifest_digest,'data_dir':str(destination),'active':False}

    def recovery_digest(self,identifier):
        value=self.published_manifest_digests.get(identifier)
        if not value:raise ValueError('恢复副本不属于本次检查，请重新导入')
        return value

    def recovery_path(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('恢复副本标识无效')
        root=self.root.resolve();parent=root/'recovery';target=parent/identifier
        def linked(path):return path.is_symlink() or getattr(path,'is_junction',lambda:False)()
        if linked(parent) or linked(target) or parent.resolve()!=parent or target.resolve()!=target:raise ValueError('恢复路径无效')
        manifest_path=target/'restore-manifest.json'
        if linked(manifest_path) or hashlib.sha256(manifest_path.read_bytes()).hexdigest()!=self.recovery_digest(identifier):raise ValueError('恢复清单已修改，请重新导入')
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        if (not isinstance(manifest,dict) or set(manifest)!={'version','copy_id','created_at','source_sha256','files'}
                or manifest['version']!=1 or manifest['copy_id']!=identifier or not isinstance(manifest['files'],dict)):
            raise ValueError('恢复副本清单损坏')
        required={'points.json','points_meta.json','objects.json','scenes.json','tools.json','mounted_tool.json',
                  'settings.json','programs.json','pending-io.json','calibrations.json','before-restore.backup.json'}
        names=set(manifest['files'])
        if not required<=names or any(name not in required and not re.fullmatch(r'scene_assets/[a-f0-9]{64}\.json',name) for name in names):raise ValueError('恢复清单文件不完整')
        actual=set()
        for path in target.rglob('*'):
            if linked(path):raise ValueError('恢复副本不能包含跳转链接')
            if path.is_file():actual.add(path.relative_to(target).as_posix())
            elif path.relative_to(target).as_posix()!='scene_assets':raise ValueError('恢复副本包含额外目录')
        if actual!=names|{'restore-manifest.json'}:raise ValueError('恢复副本文件与清单不一致')
        for name,expected in manifest['files'].items():
            path=target/name
            if (not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected)
                    or path.stat().st_size>MAX_BYTES or hashlib.sha256(path.read_bytes()).hexdigest()!=expected):
                raise ValueError('恢复副本内容已改变，请重新导入')
        def read(name):return json.loads((target/name).read_text(encoding='utf-8'))
        settings=read('settings.json');mounted=read('mounted_tool.json');calibrations=read('calibrations.json');pending=read('pending-io.json')
        if (settings.get('mode')!='sim' or settings.get('auto_connect') is not False
                or set(settings)-PROFILE_KEYS-{'mode','auto_connect'} or mounted!=ToolStore._unknown()
                or calibrations!={'version':0,'frames':{},'tools':{},'io':{}}
                or set(pending)!={'version','items','confirmed'} or pending['version']!=1 or pending['confirmed'] is not False):
            raise ValueError('恢复副本包含未撤销的设备权限')
        data=dict(points=read('points.json'),objects=read('objects.json'),scenes=read('scenes.json')['items'],
                  tools=read('tools.json')['items'],io=pending['items'],settings={k:v for k,v in settings.items() if k in PROFILE_KEYS},
                  programs=read('programs.json'),assets={Path(name).stem:read(name) for name in names if name.startswith('scene_assets/')})
        checked=validate_bundle(dict(format='elfin-data-backup',version=1,created_at=manifest['created_at'],data=data,sha256=digest(data)))
        if checked!=data or digest(data)!=manifest['source_sha256']:raise ValueError('恢复副本语义或来源已改变')
        validate_bundle(read('before-restore.backup.json'))
        return target
