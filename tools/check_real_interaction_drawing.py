"""Real-mode drawing/writing dispatch against synthetic, disposable devices."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import copy
import math
import os
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
import uuid
from unittest.mock import patch
from backend.application.drawing import Drawing
from backend.application.drawing_geometry import configuration,poses,check_bounds,bounds
from backend.core import config
from backend.core.world import World
from check_real_follow_tool import fixture,certify


def text_options(center):
    center=list(center[:3])+[180.,0.,0.]
    return dict(shape='text',text='一零九',strokes=[[[-.2,-.2],[.2,.2]],[[-.2,.2],[.2,-.2]]],
        font_size_mm=5,writing_z_mm=center[2]-15,center=list(center),plane='xy',direction=0,
        width_mm=6,height_mm=6,range_center_mm=list(center[:3]),range_size_mm=[32,32,50],
        center_policy='fixed',angle_deg=0,speed_pct=5,repeats=1)


class RealDrawing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings=patch.object(config,'_settings',copy.deepcopy(config.DEFAULTS))
        settings.start();self.addCleanup(settings.stop)
        self.world=World(data_dir=Path(os.environ['ELFIN_DATA_DIR'])/uuid.uuid4().hex)
        self.world.settings.update(mode='real',real_move_cmd='relj',real_speed_pct=5)
        self.drawing=Drawing(self.world)

    async def asyncTearDown(self):
        await self.drawing.close()
        self.world.real=None;self.world.settings['mode']='sim'
        await self.world.stop_execution('disposable real-writing test end')

    def install_pen(self):
        mounted,profile=fixture()
        profile.update(id='SYNTHETIC-pen',type='pen',name='Synthetic pen only')
        mounted['tool_id']=profile['id']
        mounted['revision']=int(self.world.tools.mounted.get('revision',0))+1
        profile['mount'].update(translation_mm=[0,0,0])
        profile['tcp'].update(translation_mm=[0,0,160],rpy_deg=[0,0,0])
        profile['geometry']['capsules']=[{'a':[0,0,0],'b':[0,0,150],'r_mm':6},
                                       {'a':[0,0,145],'b':[0,0,158],'r_mm':2}]
        certify(profile,mounted)
        self.world.tools.add_or_update(profile);self.world.tools.select(profile['id'])
        return profile

    def fake_feedback(self):
        model=self.drawing._tool()[0]
        q,_,_,ok=model.ik([0,-45,90,0,45,0],[400,0,450,180,0,0],*self.world.limits())
        self.assertTrue(ok)
        calls=[]
        link=SimpleNamespace(cmd_up=True,stream_up=True,epoch=4,command_session='synthetic-writing-only',
            frame_seq=1,jogging=None,snap={'joints':q[:],'override':.05,'moving':0,'inpos':1,
                'free_drive':0,'fsm':33,'enabled':1,'stamp':time.monotonic()},
            state_valid=lambda:True,pose_valid=lambda:True,controller_info=lambda:{'motion_qualified':True})
        async def ask(command,*args):
            calls.append((command,args))
            if command=='MoveRelJ':
                _,axis,sign,amount=args
                link.snap['joints'][axis]+=(1 if sign else -1)*float(amount)
                link.frame_seq+=1;link.snap['stamp']=time.monotonic()
            return SimpleNamespace(ok=True)
        link.ask=ask;self.world.real=link;self.world.speed_ack={'ok':True}
        return model,model.pose_vec(q),calls

    async def ready(self,payload):
        result=self.drawing.preview(payload)
        await asyncio.wait_for(self.drawing.worker,95)
        self.assertEqual(self.drawing.phase,'ready',str({'why':self.drawing.why,'progress':self.drawing.plan.get('progress')}))
        return result['plan_id']

    async def execute(self,plan_id,calls):
        with self.assertRaisesRegex(ValueError,'本轮明确确认'):
            self.drawing.run({'plan_id':plan_id})
        self.assertEqual(calls,[])
        self.drawing.run({'plan_id':plan_id,'confirm_real':True})
        deadline=time.monotonic()+30
        while self.drawing.state()['phase'] not in ('completed','failed','cancelled'):
            self.world._drive_executor(time.monotonic(),False)
            await asyncio.sleep(.003)
            self.assertLess(time.monotonic(),deadline)
        self.assertEqual(self.drawing.phase,'completed',self.drawing.why)
        self.assertTrue(calls)
        self.assertTrue(all(command=='MoveRelJ' for command,_ in calls))

    async def test_real_text_uses_verified_pen_dispatches_both_strokes_and_lifts(self):
        self.install_pen();model,center,calls=self.fake_feedback()
        payload=text_options(center)
        ticket=await self.ready(payload)
        self.assertEqual(calls,[],'preview must never send a robot command')
        self.assertEqual(self.drawing.state()['trace_tcp']['kind'],'verified_pen_tip')
        self.assertEqual({s.get('drawing_stroke_id') for s in self.drawing.plan['steps'] if s.get('drawing_trace')},{0,1})
        await self.execute(ticket,calls)
        actual=model.pose_vec(self.world.current_joints())
        self.assertGreater(actual[2],payload['writing_z_mm']+15)
        self.assertLess(math.dist(actual[:3],self.drawing.plan['path'][-1][:3]),2)
        self.assertIsNone(self.world.runner.execution_guard)

    async def test_real_graphic_reuses_same_verified_pen_and_runner(self):
        self.install_pen();model,center,calls=self.fake_feedback()
        payload=dict(shape='square',center=center,plane='xy',width_mm=4,height_mm=4,
                     range_size_mm=[32,32,32],angle_deg=0,speed_pct=5,repeats=1)
        ticket=await self.ready(payload)
        await self.execute(ticket,calls)
        self.assertLess(math.dist(model.pose_vec(self.world.current_joints())[:3],center[:3]),2)

    async def test_missing_pen_expired_feedback_and_modified_envelope_never_dispatch(self):
        self.world.tools.clear()
        model,center,calls=self.fake_feedback()
        with self.assertRaisesRegex(ValueError,'实际书写笔'):
            self.drawing.preview(text_options(center))
        self.assertEqual(calls,[])
        self.install_pen();model,center,calls=self.fake_feedback()
        self.world.real.state_valid=lambda:False
        with self.assertRaisesRegex(ValueError,'反馈'):
            self.drawing.preview(text_options(center))
        self.assertEqual(calls,[])
        self.world.real.state_valid=lambda:True
        self.world.tools.items[-1]['geometry']['capsules'][0]['r_mm']+=1
        with self.assertRaisesRegex(ValueError,'工具验证'):
            self.drawing.preview(text_options(center))
        self.assertEqual(calls,[])

    def test_real_text_contract_keeps_existing_range_speed_and_all_path_bounds(self):
        p=configuration(text_options([400,0,450,180,0,0]),'real')
        for point in poses(p):check_bounds(point[:3],bounds(p))
        for change in ({'speed_pct':6},{'range_size_mm':[51,32,50]},
                       {'font_size_mm':1},{'font_size_mm':41},{'text':'123456'}):
            with self.assertRaises(ValueError):configuration({**p,**change},'real')


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(RealDrawing)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(
        'check_drawing.DrawingTests.test_real_unknown_tool_feedback_controller_and_remote_center_refused'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
