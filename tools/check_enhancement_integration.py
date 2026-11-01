"""新增工作区端到端API回归；只在硬件禁用的临时副本运行。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from backend.main import app,world

with TestClient(app,base_url='http://127.0.0.1:8710') as client:
    def post(path,body=None):
        response=client.post(path,json=body or {})
        value=response.json()
        assert response.is_success and value.get('ok'),(path,response.status_code,value)
        return value
    assert world.real is None
    q=list(world.sim.q)
    before=world.runner.snapshot()
    # Recording/trajectory execution was explicitly retired; stale clients
    # must receive a failure and cannot acquire motion through legacy routes.
    for path,body in [('/record/start',{'name':'离线录制'}),('/record/stop',{}),
                      ('/inspect',{'joints':[q],'speed':10}),('/run',{'plan_id':'retired'})]:
        response=client.post('/api/studio'+path,json=body)
        assert response.status_code==409 and '工艺入口已移除' in response.json()['detail']
        assert world.runner.snapshot()==before and list(world.sim.q)==q
    legacy=client.get('/api/studio').json()
    assert legacy['readonly'] and legacy['deprecated'] and legacy['templates']==[]
    post('/api/teaching/batch',{'points':[{'name':'批量示教1','joints':q}]})
    assert world.points.list()[-1]['source']=='sim'
    post('/api/program/load',{'src':'SPEED 100\nMOVEJ '+','.join(map(str,q))})
    assert world.runner.state!='running'
    post('/api/program/start')
    for _ in range(100):
        if client.get('/api/state').json()['exec']['state']=='finished':break
        time.sleep(.02)
    else:raise AssertionError('程序仿真未完成')
    assert list(world.sim.q)==q
    profile=post('/api/workspace/profiles',{'name':'偏好档案'})['profile']
    compared=post('/api/workspace/profiles/'+profile['id']+'/compare')
    post('/api/workspace/profiles/'+profile['id']+'/apply',{'revision':compared['profile']['revision']})
    scene=client.get('/api/workspace/scene').json()
    scene=post('/api/workspace/scene/template',{'revision':scene['revision'],'template':'pallet'})
    assert len(scene['objects'])==1
    scene=post('/api/workspace/scene/undo',{'revision':scene['revision']})
    assert scene['objects']==[]
    debug=post('/api/program/debug/load',{'src':'SPEED 12\nWAIT 0.01'})
    token=debug['session']
    post('/api/program/debug/step',{'session':token})
    for _ in range(100):
        debug=client.get('/api/program/debug').json()
        if debug['state']=='paused':break
        time.sleep(.02)
    assert debug['state']=='paused'
    post('/api/program/debug/step',{'session':token})
    time.sleep(.1)
    debug=client.get('/api/program/debug').json()
    assert debug['state'] in ('paused','finished'),debug
    post('/api/program/debug/stop',{'session':token})
    response=client.get('/api/vision/markers/capabilities')
    assert response.status_code in (200,503) and 'ok' in response.json()
    assert world.real is None
    print('PASS: 退役工艺拒绝写入且不运动、程序显式仿真执行、批量示教、档案比较应用、场景撤销、调试与视觉能力路由；未接硬件')
