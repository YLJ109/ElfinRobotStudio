"""工艺与录制边界回归，只在隔离副本执行。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import sys
import tempfile
import unittest
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core import config, task_studio
from backend.core.task_studio import generate, RecordingStore
from backend.task_routes import TaskStudio
import backend.task_routes as routes
from backend.core.program import parse
from backend.core.points import PointStore


class StudioChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=patch.object(config,'DATA_DIR',Path(self.tmp.name));self.data.start()
    def tearDown(self):
        self.data.stop();self.tmp.cleanup()
    def test_templates_have_bounded_parseable_paths(self):
        for kind in ('raster','circle','pick-place','pallet'):
            with self.subTest(kind=kind):
                draft=generate({'kind':kind,'options':{'speed':12}})
                self.assertTrue(1<=draft['count']<=256)
                self.assertEqual(draft['speed'],12)
                self.assertTrue(parse(draft['program'],lambda key:None))
    def test_raster_alternates_and_circle_closes(self):
        pts=generate({'kind':'raster'})['poses']
        self.assertEqual(pts[0][0],pts[3][0])
        circle=generate({'kind':'circle'})['poses']
        for a,b in zip(circle[0],circle[-1]):self.assertAlmostEqual(a,b)
    def test_bad_and_excessive_inputs(self):
        for payload in ({'kind':'bad'},{'kind':'circle','options':{'radius':float('nan')}},
                        {'kind':'pallet','options':{'rows':10,'cols':10}},
                        {'kind':'raster','options':{'spacing':True}},
                        {'kind':'raster','options':{'width':float('inf')}},
                        {'kind':'circle','options':{'segments':False}}):
            with self.subTest(payload=payload),self.assertRaises(ValueError):generate(payload)
    def world(self):
        return NS(settings={'mode':'sim'},real=None,sim=NS(q=[0.]*6,traj=None,jog=None),
                  motion_model=lambda:NS(pose_vec=lambda q:[*q]),executor=NS(busy=False),
                  runner=NS(state='idle'),hand_follow=NS(active=False),queue_state='idle')
    def test_record_is_actual_motion_and_detached(self):
        store=RecordingStore();w=self.world();store.start('轨迹')
        store.observe(w);self.assertEqual(store.status()['count'],1)
        store.last_at=0;store.observe(w);self.assertEqual(store.status()['count'],1)
        w.sim.q[0]=3;store.last_at=0;store.observe(w)
        saved=store.stop();self.assertEqual(saved['joints'],[[0.]*6,[3,0,0,0,0,0]])
        saved['joints'][0][0]=99
        self.assertEqual(store.get(saved['id'])['joints'][0][0],0)
        self.assertEqual(RecordingStore().get(saved['id'])['count'],2)
    def test_stop_io_failure_preserves_active_recording(self):
        store=RecordingStore();store.start('保留');store.observe(self.world())
        with patch.object(task_studio,'write_json',side_effect=OSError('磁盘满')):
            with self.assertRaises(OSError):store.stop()
        self.assertTrue(store.status()['active']);self.assertEqual(store.list(),[])
    def test_revision_and_mode_boundaries(self):
        store=RecordingStore();store.start('修订');store.observe(self.world());saved=store.stop()
        with self.assertRaises(ValueError):store.edit(saved['id'],{'revision':0,'joints':[[0.]*6]})
        result=store.edit(saved['id'],{'revision':1,'joints':[[1.]*6]})
        self.assertEqual(result['revision'],2);self.assertEqual(result['poses'],[])
        studio=TaskStudio(self.world());studio.require_sim(idle=True)
        studio.world.queue_state='paused'
        with self.assertRaises(ValueError):studio.require_sim(idle=True)
        studio.world.queue_state='idle';studio.world.real=object()
        with self.assertRaises(ValueError):studio.require_sim()
    def test_mode_change_interrupts_not_records_real(self):
        store=RecordingStore();w=self.world();store.start('隔离');w.real=object();store.observe(w)
        self.assertEqual(store.status()['count'],0);self.assertTrue(store.status()['interrupted'])
    def test_batch_points_all_or_nothing_and_unique_codes(self):
        store=PointStore();store.add('已有',[0.]*6)
        for rows in ([{'name':'已有','joints':[0.]*6}],
                     [{'name':'新','joints':[0.]*6},{'name':'新','joints':[1.]*6}],
                     [{'name':'新','joints':[float('nan')]*6}]):
            with self.assertRaises(ValueError):store.batch_add(rows)
        self.assertEqual(len(store.list()),1)
        result=store.batch_add([{'name':'新1','joints':[0.]*6},{'name':'新2','joints':[1.]*6}])
        self.assertEqual([row['code'] for row in result],['P2','P3'])
        result[0]['joints'][0]=99
        self.assertEqual(store.list()[1]['joints'][0],0)

class InspectionChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=patch.object(config,'DATA_DIR',Path(self.tmp.name));self.data.start()
        self.context={'revision':1};self.programs=[]
        async def load(source):self.programs.append(source);return {'ok':True}
        self.world=NS(settings={'mode':'sim','sim_speed_pct':15},real=None,sim=NS(q=[0.]*6,traj=None,jog=None),
            motion_model=lambda:NS(_fk_key=None,_fk_value=None),executor=NS(busy=False),runner=NS(state='idle'),
            hand_follow=NS(active=False),queue_state='idle',plan_context=lambda:dict(self.context),
            current_joints=lambda:[0.]*6,objects=NS(list=lambda:[]),limits=lambda:([-360.]*6,[360.]*6),
            load_program=load,start_program=lambda:{'ok':True})
        self.studio=TaskStudio(self.world)
        self.report={'risk_detected':False,'issues':[],'tcp_path':[[0,0,0]],'duration_estimate_s':1.}
    async def asyncTearDown(self):self.data.stop();self.tmp.cleanup()
    async def test_checked_speed_and_single_use_plan(self):
        with patch.object(routes,'inspect_path',return_value=self.report) as inspected:
            result=await self.studio.inspect({'joints':[[0.]*6],'speed':23})
        self.assertEqual(inspected.call_args.args[3]['sim_speed_pct'],23)
        self.assertTrue((await self.studio.run(result['plan_id']))['ok'])
        self.assertTrue(self.programs[0].startswith('SPEED 23\nMOVEJ'))
        with self.assertRaises(ValueError):await self.studio.run(result['plan_id'])
        with self.assertRaises(ValueError):await self.studio.run([])
    async def test_context_changes_invalidate_result_and_run(self):
        with patch.object(routes,'inspect_path',return_value=self.report):
            result=await self.studio.inspect({'joints':[[0.]*6]})
        self.context['revision']=2
        with self.assertRaises(ValueError):await self.studio.run(result['plan_id'])
        self.assertEqual(self.programs,[])
        def changed(*args):self.context['revision']+=1;return self.report
        with patch.object(routes,'inspect_path',side_effect=changed):
            with self.assertRaises(ValueError):await self.studio.inspect({'joints':[[0.]*6]})
    async def test_risky_plan_never_executes(self):
        with patch.object(routes,'inspect_path',return_value={**self.report,'risk_detected':True}):
            result=await self.studio.inspect({'joints':[[0.]*6]})
        with self.assertRaises(ValueError):await self.studio.run(result['plan_id'])
        self.assertEqual(self.programs,[])
    async def test_cancelled_http_keeps_worker_slot_until_exit(self):
        entered=threading.Event();release=threading.Event()
        def blocked(*args):entered.set();release.wait(2);return self.report
        try:
            with patch.object(routes,'inspect_path',side_effect=blocked):
                task=asyncio.create_task(self.studio.inspect({'joints':[[0.]*6]}))
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertTrue(self.studio.inspection_busy)
                with self.assertRaises(ValueError):await self.studio.inspect({'joints':[[0.]*6]})
                release.set()
                for _ in range(100):
                    if not self.studio.inspection_busy:break
                    await asyncio.sleep(.01)
                self.assertFalse(self.studio.inspection_busy);self.assertEqual(self.studio.plans,{})
        finally:release.set()

if __name__=='__main__':unittest.main()
