"""Requires run_offline_checks.py: only copied data and simulation are touched."""
import os
import sys
import time
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
assert os.environ.get('HUYAN_NO_REAL') == '1', 'Use isolated offline runner'
assert 'huayan-fix-checks-' in str(Path(__file__).resolve()), 'Use isolated source copy'
from fastapi.testclient import TestClient
from sim.backend.main import app, world
from sim.backend.core import config
from sim.backend.core.points import PointStore

def main():
    with TestClient(app, base_url='http://127.0.0.1:8710') as client:
        assert client.get('/api/state').json()['mode'] == 'sim'
        point = client.post('/api/points', json={'name':'取料点','joints':[0,0,0,0,0,2]}).json()['point']
        code = point['code']
        renamed = client.patch('/api/points/'+str(point['id']), json={'name':'取料工位'}).json()
        assert renamed['ok'] and renamed['point']['code'] == code and renamed['point']['tcp'] == point['tcp']
        assert not client.patch('/api/points/'+str(point['id']), json={'joints':[0]*6}).json()['ok']
        assert not client.patch('/api/points/'+str(point['id']), json={'name':''}).json()['ok']
        assert not client.post('/api/points', json={'name':'取料工位','joints':[0]*6}).json()['ok']
        assert not client.post('/api/points', json={'name':'坏点','joints':[0]*5}).json()['ok']
        source = f'配置 = {{"速度": 15}}\n路径 = ["取料工位", [0,0,0,0,0,4]]\n定义 去点(目标):\n    关节移动(目标)\n    等待(0.02)\n速度(配置["速度"])\n循环 点 在 路径:\n    去点(点)\n'
        parsed = client.post('/api/program/load', json={'src':source}).json()
        assert parsed['ok'], parsed
        assert client.post('/api/program/start',json={}).json()['ok']
        for _ in range(120):
            time.sleep(.05)
            state = client.get('/api/state').json()
            if state['exec']['state'] in ('finished','aborted'): break
        assert state['exec']['state'] == 'finished', state['exec']
        assert abs(state['joints']['q'][5] - 4) < .01, state['joints']
        assert state['exec']['completed_index'] == 5 and state['exec']['dynamic']
        # A pure infinite loop yields each tick, so stop stays responsive.
        assert client.post('/api/program/load',json={'src':'当 真:\n    略过'}).json()['ok']
        assert client.post('/api/program/start',json={}).json()['ok']
        time.sleep(.05)
        started = time.monotonic(); assert client.post('/api/program/stop',json={}).json()['ok']
        assert time.monotonic()-started < 1
        assert client.get('/api/state').json()['exec']['state'] == 'aborted'
        world._persist_exec(time.monotonic())
        checkpoint = json.loads(world.exec_file.read_text(encoding='utf-8'))
        assert checkpoint['dynamic'] and checkpoint['steps'] == [] and checkpoint['source'] == '当 真:\n    略过'
        world.resume = checkpoint
        assert not world.resume_last()['ok']
        assert not client.post('/api/program/load',json={'src':'速度(10)\nopen("file")'}).json()['ok']
        assert client.get('/api/cameras/frame.jpg',params={'session':'missing','stream':'color'}).status_code == 503
        assert not client.get('/api/cameras/pointcloud',params={'session':'missing'}).json()['ok']
        assert not client.get('/api/cameras/measure',params={'session':'missing','x':0,'y':0}).json()['ok']
        client.delete('/api/points/'+str(point['id']))
        new = client.post('/api/points',json={'joints':[0]*6}).json()['point']
        assert int(new['code'][1:]) > int(code[1:]), 'Deleted references must not be recycled'
    # Migration must preserve legacy named P1 even when a custom named point precedes it.
    (config.DATA_DIR/'points.json').write_text(json.dumps([{'id':1,'name':'custom','joints':[0]*6},{'id':2,'name':'P1','joints':[1]*6}]),encoding='utf-8')
    store = PointStore(); assert store.resolve('P1')['id'] == 2 and store.get(1)['code'] != 'P1'
    print('program-integration: passed (simulation only, including script checkpoints)')
if __name__ == '__main__': main()
