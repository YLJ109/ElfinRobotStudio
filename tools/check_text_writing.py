"""Ground-contact pen, text contract and actual shared Runner; disposable data only."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import copy
import math
import time
import unittest
from unittest.mock import patch
from backend.core import config,sim_tool
from backend.core.world import World
from backend.core.safety import evaluate
from backend.application.drawing import Drawing
from backend.application.drawing_geometry import configuration,poses,trace_metadata
from backend.application.text_writing import paths,sequence

def options(**changes):
    value=dict(shape='text',text='A!',strokes=[[[-.4,-.4],[0,.4],[.4,-.4],[-.4,-.4]],[[-.1,0],[.1,0],[.1,.1],[-.1,.1],[-.1,0]]],
        font_size_mm=50,center=[-500,0,16,180,0,0],plane='xy',direction=0,
        width_mm=50,height_mm=50,range_center_mm=[-500,0,16],range_size_mm=[90,90,36],
        center_policy='fixed',angle_deg=0,speed_pct=100,repeats=1)
    return {**value,**changes}

class TextGeometry(unittest.TestCase):
    def test_writing_height_moves_real_ink_and_lifts_inside_box(self):
        for z in (1,120,800):
            p=configuration(options(writing_z_mm=z,center=[-500,0,z+15,180,0,0],
                range_center_mm=[-500,0,z+40],range_size_mm=[90,90,86]),'sim')
            rows,flags,_=sequence(p)
            self.assertEqual(rows[0][2],z+15);self.assertEqual(rows[-1][2],z+60)
            self.assertEqual(rows[-2][2],z+60)
            self.assertTrue(all(point[2]==z for stroke in paths(p) for point in stroke))
            self.assertTrue(all(point[2]==z for point,ink in zip(rows,flags) if ink))
        for z in (0,801,True,float('nan')):
            with self.assertRaises(ValueError):configuration(options(writing_z_mm=z),'sim')

    def test_glyph_geometry_preserves_disconnected_contours_and_lifts(self):
        p=configuration(options(),'sim');rows,flags,ids=sequence(p)
        self.assertEqual(rows,poses(p));self.assertEqual(rows[0][:2],rows[-1][:2]);self.assertGreater(rows[-1][2],rows[0][2]);self.assertFalse(flags[-1]);self.assertEqual(trace_metadata(p,rows)[1],flags)
        self.assertEqual(len(paths(p)),2)
        for pose,ink in zip(rows,flags):
            if ink:self.assertEqual(pose[2],1)
        self.assertTrue(any(not ink and pose[2]==16 for pose,ink in zip(rows,flags)))
        self.assertEqual({i for i in ids if i is not None},{0,1})
    def test_bad_text_and_geometry_do_not_produce_motion(self):
        for change in ({'text':''},{'text':'123456'},{'text':'a\nb'},{'strokes':[]},{'strokes':[[[float('nan'),0],[0,0]]]},{'font_size_mm':181},{'plane':'xz'},{'center':[0,0,100,180,0,0]},{'repeats':2}):
            with self.assertRaises(ValueError):configuration(options(**change),'sim')
        for text in ('你好','A123!','中A1。?','12345'):
            self.assertEqual(configuration(options(text=text),'sim')['text'],text)
        with self.assertRaises(ValueError):configuration(options(),'real')

class ActualWriting(unittest.IsolatedAsyncioTestCase):
    async def test_elevated_writing_completes_at_requested_z_and_lifts_60mm(self):
        with patch.object(config,'_settings',copy.deepcopy(config.DEFAULTS)):
            w=World();w.settings['mode']='sim';w.set_sim_tool({'kind':'pen'});d=Drawing(w)
            try:
                state=d.preview(options(writing_z_mm=80,center=[-500,0,95,180,0,0],
                    range_center_mm=[-500,0,120],range_size_mm=[90,90,86],
                    strokes=[[[0,0],[.1,0],[.1,.1],[0,.1],[0,0]]]))
                await d.worker;self.assertEqual(d.phase,'ready',d.why)
                d.run({'plan_id':state['plan_id']});deadline=time.monotonic()+30
                while d.state()['phase'] not in ('completed','failed','cancelled'):
                    w._tick(.025,time.monotonic());await asyncio.sleep(.005)
                    self.assertLess(time.monotonic(),deadline)
                self.assertEqual(d.state()['phase'],'completed',d.why)
                ink=d.state()['actual_samples'];self.assertTrue(ink)
                self.assertTrue(all(abs(sample['point'][2]-80)<.1 for sample in ink))
                tip=w.motion_model().pose_vec(w.current_joints())
                self.assertAlmostEqual(tip[2],140,delta=.1)
                self.assertAlmostEqual(tip[0],-500,delta=.1);self.assertAlmostEqual(tip[1],0,delta=.1)
            finally:await d.close();await w.stop_execution('elevated finish check end')

    async def test_cancel_continuous_stroke_revokes_same_runner_and_no_late_motion(self):
        with patch.object(config,'_settings',copy.deepcopy(config.DEFAULTS)):
            w=World();w.settings['mode']='sim';w.set_sim_tool({'kind':'pen'});d=Drawing(w)
            try:
                state=d.preview(options(writing_z_mm=80,center=[-500,0,95,180,0,0],
                    range_center_mm=[-500,0,120],range_size_mm=[90,90,86],
                    strokes=[[[0,0],[.1,0],[.1,.1],[0,.1],[0,0]]]))
                await d.worker;self.assertEqual(d.phase,'ready',d.why)
                self.assertTrue(d.plan['prepared_strokes'])
                d.run({'plan_id':state['plan_id']});deadline=time.monotonic()+30
                while not (w.runner.current() and 'drawing_continuous' in w.runner.current() and w.sim.traj):
                    w._tick(.025,time.monotonic());await asyncio.sleep(.005)
                    self.assertLess(time.monotonic(),deadline)
                self.assertAlmostEqual(w.motion_model().pose_vec(w.current_joints())[2],80,delta=.1)
                result=await d.cancel({'plan_id':state['plan_id']})
                self.assertEqual(result['phase'],'cancelled');self.assertIsNone(w.sim.traj)
                frozen=w.current_joints().copy()
                for _ in range(5):w._tick(.025,time.monotonic());await asyncio.sleep(.005)
                self.assertEqual(w.current_joints(),frozen);self.assertFalse(w.executor.busy)
            finally:await d.close();await w.stop_execution('continuous cancel check end')

    async def test_preview_reuse_never_survives_origin_scene_or_expiration_changes(self):
        with patch.object(config,'_settings',copy.deepcopy(config.DEFAULTS)):
            w=World();w.settings['mode']='sim';w.set_sim_tool({'kind':'pen'})
            offset=[0.];d=Drawing(w,clock=lambda:time.monotonic()+offset[0])
            try:
                d.preview(options());await d.worker
                self.assertEqual(d.phase,'ready',d.why)
                for change in ('origin','scene','expiry'):
                    old=d.plan['id']
                    if change=='origin':w.sim.q[0]+=.001
                    elif change=='scene':w.settings['ground_enabled']=not w.settings['ground_enabled']
                    else:offset[0]+=121
                    current=d.preview(options())
                    self.assertNotEqual(current['plan_id'],old,change)
                    self.assertEqual(current['phase'],'checking',change)
                    await d.worker
                    self.assertEqual(d.phase,'ready',d.why)
            finally:await d.close();await w.stop_execution('preview cache check end')

    async def test_chess_auto_tool_preserves_dimensions_and_rejects_late_pen_cleanup(self):
        with patch.object(config,'_settings',copy.deepcopy(config.DEFAULTS)):
            w=World();w.settings['mode']='sim'
            custom=copy.deepcopy(sim_tool.DEFAULT_TOOL);custom['dimensions']['length']=340
            try:
                self.assertTrue(w.set_sim_tool(custom)['ok'])
                before=w.current_joints().copy()
                self.assertTrue(w.ensure_sim_task_tool('writing')['ok'])
                self.assertTrue(w.ensure_sim_task_tool('drawing')['ok'])
                pen_revision=w._cfg_rev
                self.assertEqual(w.tool_snapshot()['visual']['kind'],'pen')
                self.assertTrue(w.ensure_sim_task_tool('chess')['ok'])
                self.assertEqual(w.tool_snapshot()['visual'],sim_tool.normalize(custom))
                self.assertEqual(w.current_joints(),before)
                stale=w.set_sim_tool({'kind':'pen'},expected_revision=pen_revision)
                self.assertFalse(stale['ok']);self.assertEqual(stale['code'],'tool_changed')
                revision=w._cfg_rev
                self.assertTrue(w.ensure_sim_task_tool('chess')['ok'])
                self.assertEqual(w._cfg_rev,revision)
                w.settings['mode']='real'
                self.assertFalse(w.ensure_sim_task_tool('chess')['ok'])
                self.assertFalse(w.ensure_sim_task_tool('drawing')['ok'])
            finally:await w.stop_execution('auto tool check end')

    async def test_actual_ground_writing_keeps_original_range_and_real_feedback_gates(self):
        with patch.object(config,'_settings',copy.deepcopy(config.DEFAULTS)):
            w=World();w.settings['mode']='sim';before=copy.deepcopy(w.settings.get('interaction_range_default'))
            d=Drawing(w)
            try:
                with self.assertRaises(ValueError):d.preview(options())
                result=w.set_sim_tool({'kind':'pen'});self.assertTrue(result['ok']);self.assertEqual(result['tool']['name'],'书写笔')
                self.assertEqual(w.current_joints(),[0]*6)
                state=d.preview(options());await asyncio.wait_for(d.worker,95)
                self.assertEqual(d.phase,'ready',d.why);self.assertEqual(d.plan['trace_tcp']['kind'],'pen_tip')
                self.assertLessEqual(d.plan['max_tcp_deviation_mm'],2)
                self.assertTrue(d.plan['prepared_strokes'])
                self.assertLess(len(d.plan['steps']),d.plan['unmerged_step_count'])
                self.assertEqual(len(d.plan['program'].splitlines()),d.plan['unmerged_step_count'])
                print('TEXT_CONTINUOUS',d.plan['continuous_metrics'],'STEPS',d.plan['unmerged_step_count'],'->',len(d.plan['steps']),flush=True)
                # Repeated UI previews reuse the exact checked plan, without
                # launching another worker or extending the ticket lifetime.
                plan=d.plan;worker=d.worker;expires=plan['expires']
                started=time.perf_counter()
                for _ in range(10):
                    reused=d.preview(options())
                    self.assertEqual(reused['plan_id'],state['plan_id'])
                    self.assertIs(d.plan,plan);self.assertIs(d.worker,worker)
                    self.assertEqual(d.plan['expires'],expires)
                print('TEXT_PREVIEW_REUSE_MS',round((time.perf_counter()-started)*100,3),flush=True)
                d.run({'plan_id':state['plan_id']});deadline=asyncio.get_running_loop().time()+40
                checked_pause=False
                while d.state()['phase'] not in ('completed','failed','cancelled'):
                    w._tick(.025,asyncio.get_running_loop().time());await asyncio.sleep(.005)
                    step=w.runner.current()
                    if not checked_pause and step and 'drawing_continuous' in step and w.sim.traj:
                        self.assertTrue((await d.pause({'plan_id':state['plan_id']}))['ok'])
                        frozen=w.current_joints().copy();owned=w.sim.traj
                        for _ in range(4):w._tick(.025,asyncio.get_running_loop().time());await asyncio.sleep(.005)
                        self.assertEqual(w.current_joints(),frozen);self.assertIs(w.sim.traj,owned)
                        self.assertTrue((await d.resume({'plan_id':state['plan_id']}))['ok'])
                        checked_pause=True
                    self.assertLess(asyncio.get_running_loop().time(),deadline)
                self.assertEqual(d.state()['phase'],'completed',d.why)
                self.assertTrue(checked_pause)
                self.assertEqual(w.runner.completed_index,len(w.runner.steps));self.assertIsNone(w.sim.traj)
                self.assertEqual(w.settings.get('interaction_range_default'),before)
                history=d.state()['actual_samples'];self.assertTrue(history)
                self.assertEqual({p['stroke'] for p in history},{0,1})
                self.assertTrue(all(abs(p['point'][2]-1)<2 for p in history))
                # Only a nib's intended contact is exempted. Penetration and
                # real-mode ground checks keep their existing protections.
                model=w.motion_model();q=w.current_joints();lo,hi=w.limits()
                target=model.pose_vec(q);target[2]=-.5
                bad,_,_,ok=model.ik(q,target,lo,hi,iters=160,max_restarts=2)
                self.assertTrue(ok)
                risk=evaluate(model,bad,w.settings,lo,hi)
                self.assertTrue(any(p['type']=='ground' for p in risk['collisions']))
                self.assertTrue(w.set_sim_tool(sim_tool.DEFAULT_TOOL)['ok'])
            finally:await d.close();await w.stop_execution('text check end')

if __name__=='__main__':unittest.main()

