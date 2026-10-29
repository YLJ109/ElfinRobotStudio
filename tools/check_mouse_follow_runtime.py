"""临时副本内跑真实ASGI/仿真循环，验证采样响应与丢输入回中。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import math
import os
import cProfile
import pstats
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from backend.core import config
config.save_settings({'mode':'sim','auto_connect':False,'sim_tool':None,'sim_speed_pct':100})
import backend.main as main

if os.environ.get('ELFIN_PROFILE_FOLLOW') == '1':
    profile=cProfile.Profile()
    original_tick=main.world._tick
    def profiled_tick(dt,now):
        return profile.runcall(original_tick,dt,now)
    main.world._tick=profiled_tick


class MouseRuntime(unittest.TestCase):
    def test_latest_mouse_samples_stay_responsive_while_ik_moves_and_absence_returns_center(self):
        with TestClient(main.app,base_url='http://127.0.0.1:8710') as client:
            session='mouse-offline-runtime'
            result=client.post('/api/sim/mouse-follow/configure',json={
                'session':session,'aspect':4/3,'depth':True,'rotation':False,
                'direction':270,'depth_mode':'gesture',
                'enclosure':{'min':[-950,-75,475],'max':[-250,75,825]}}).json()
            self.assertTrue(result['ok'],result)
            result=client.post('/api/sim/mouse-follow/start',json={
                'session':session,'token':result['state']['token']}).json()
            self.assertTrue(result['ok'],result)
            token=result['state']['token']
            deadline=time.monotonic()+15
            while main.world._hand_centering and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertFalse(main.world._hand_centering)
            self.assertTrue(main.world.hand_follow.active,main.world.hand_follow.why)
            initial=list(main.world.sim.q)
            elapsed=[]
            for seq in range(1,41):
                began=time.monotonic()
                result=client.post('/api/sim/mouse-follow/sample',json={
                    'session':session,'token':token,'seq':seq,'u':.65,'v':.35,
                    'hand_open':.5,'palm_scale':1,'angle':0,'hand_present':True}).json()
                elapsed.append(time.monotonic()-began)
                self.assertTrue(result['ok'],result)
                time.sleep(.025)
            self.assertNotEqual(main.world.sim.q,initial,'后台逆解必须实际驱动共用仿真关节')
            p95=sorted(elapsed)[math.ceil(len(elapsed)*.95)-1]
            print(f'鼠标采样响应 p95={p95*1000:.1f}ms max={max(elapsed)*1000:.1f}ms',flush=True)
            self.assertLess(p95,.5,'正常采样不应接近前端750ms超时')
            result=client.post('/api/sim/mouse-follow/sample',json={
                'session':session,'token':token,'seq':41,'u':.5,'v':.5,
                'hand_present':False,'palm_scale':1,'angle':0}).json()
            self.assertTrue(result['ok'],result)
            center=main.world.hand_follow._base[:3]
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if math.dist(main.world.hand_follow.actual[:3],center)<1:
                    break
                time.sleep(.02)
            self.assertLess(math.dist(main.world.hand_follow.actual[:3],center),1)
            self.assertTrue(main.world.hand_follow.active)
            self.assertTrue(main.world.hand_follow.waiting)
            client.post('/api/sim/mouse-follow/stop',json={'token':token})


if __name__=='__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        if os.environ.get('ELFIN_PROFILE_FOLLOW') == '1':
            pstats.Stats(profile).sort_stats('cumtime').print_stats(18)
