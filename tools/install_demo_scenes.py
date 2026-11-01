"""Generate fixtures in the owned isolated server; save layouts without activating them."""
import datetime
import json
from pathlib import Path
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]

def call(port,path,payload=None):
    body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf-8')
    req=urllib.request.Request(f'http://127.0.0.1:{port}/api/'+path,data=body,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as response:result=json.load(response)
    if result.get('ok') is False:raise ValueError(result.get('why') or result.get('error'))
    return result

def main():
    iso=18766
    assert call(iso,'state')['mode']=='sim'
    call(iso,'vision/workspace/config',dict(input='camera',enabled=False,detection_enabled=False,mapping=False))
    recipes=[
        ('01 三色分拣','play/setup',dict(mode='sorting',counts=dict(red=3,green=3,blue=3))),
        ('02 形状分拣','play/setup',dict(mode='sorting',criterion='shape',shape_counts=dict(rectangle=3,circle=3))),
        ('03 井字棋','play/setup',dict(mode='chess')),
        ('04 三色装盒','assembly/setup',dict(mode='kit',columns=3,rows=1,cells=['red','green','blue'],perception='scene')),
        ('05 数字拼摆','assembly/setup',dict(mode='pattern',columns=3,rows=5,cells=[None,'red',None,'red','red',None,None,'red',None,None,'red',None,'red','red','red'],perception='scene')),
        ('06 爱心拼摆','assembly/setup',dict(mode='pattern',columns=5,rows=5,cells=[None,'red',None,'red',None,'red','red','red','red','red','red','red','red','red','red',None,'red','red','red',None,None,None,'red',None,None],perception='scene')),
        ('07 基础搬运','challenge/handling/setup',dict(level=1)),
        ('08 单障碍搬运','challenge/handling/setup',dict(level=2)),
        ('09 双障碍搬运','challenge/handling/setup',dict(level=3)),
        ('10 三件分拣','play/setup',dict(mode='sorting',counts=dict(red=1,green=1,blue=1))),
    ]
    bundle=[]
    for name,route,payload in recipes:
        setup=call(iso,route,payload)
        group=setup['group']
        objects=[o for o in call(iso,'objects')['items'] if o.get('task',{}).get('group')==group]
        assert objects and sum(o['task']['role']=='zone_a' for o in objects)==1
        assert sum(o['task']['role']=='zone_b' for o in objects)==1
        assert all(o['task']['slot']==-1 for o in objects)
        name='演示 · '+name
        call(iso,'scenes/import',dict(name=name,objects=objects))
        exported=call(iso,'scenes/export/'+urllib.parse.quote(name))
        assert exported['objects']==objects
        bundle.append(dict(name=name,objects=objects,setup=dict(route=route,payload=payload)))
    existing=call(8710,'scenes')['items']
    names={row['name'] for row in existing}
    assert not names.intersection(row['name'] for row in bundle), 'Existing scene names conflict; no workstation scene changed'
    before=call(8710,'objects')
    folder=ROOT/'output/demo-scenes-20261031'/datetime.datetime.now().strftime('%H%M%S')
    folder.mkdir(parents=True,exist_ok=False)
    backup=[call(8710,'scenes/export/'+urllib.parse.quote(row['name'])) for row in existing]
    (folder/'before-install.json').write_text(json.dumps(dict(scenes=backup,active_objects=before),ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'scenes.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in bundle:
        call(8710,'scenes/import',dict(name=row['name'],objects=row['objects']))
        saved=call(8710,'scenes/export/'+urllib.parse.quote(row['name']))
        assert saved['objects']==row['objects']
    for scene in backup:assert call(8710,'scenes/export/'+urllib.parse.quote(scene['name']))==scene
    assert call(8710,'objects')==before, 'Current objects changed concurrently; inspect without rollback'
    disk=json.loads((ROOT/'backend/data/scenes.json').read_text(encoding='utf-8'))
    for row in bundle:assert disk['items'][row['name']]['objects']==row['objects']
    report=dict(scenes=[dict(name=row['name'],objects=len(row['objects'])) for row in bundle],
                previous_scenes_preserved=True,active_scene_unchanged=True,persisted_verified=True,
                physical_robot_verified=False)
    (folder/'installed.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(folder=str(folder),**report),ensure_ascii=False))

if __name__=='__main__':main()
