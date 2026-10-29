"""Pure bridge tests, not hardware qualification or actual driver integration."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import threading
from types import SimpleNamespace as NS
import unittest

from backend.core.real_palm_pipeline import RealPalmTargetPipeline


class LinearModel:
    def pose_vec(self,q):
        return [q[0],q[1],500.+q[2],q[3],q[4],q[5]]


class Driver:
    def __init__(self):
        self.owner='internal-driver-owner'
        self.profile=NS(lower=[-180.]*6,upper=[180.]*6)
        self.published=[]
    def publish(self,q,**metadata):
        self.published.append((q,metadata));return True


class FakeStartup:
    def cancel(self,reason):self.phase='blocked'


class Bridge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now=10.
        self.context=dict(camera_session='camera-1',connection=('same-arm',4),tool_context='measured-tool:r8')
        self.driver=Driver();self.paths=[]
        def solve(model,q,target,lo,hi,**kw):
            delta=[b-a for a,b in zip(model.pose_vec(q)[:3],target)]
            return {'q':[q[i]+max(-2.,min(2.,delta[i])) if i<3 else q[i] for i in range(6)]}
        self.solve=solve
        self.pipeline=RealPalmTargetPipeline(LinearModel(),self.driver,lambda:dict(self.context),
            lambda a,b:self.paths.append((a,b)) or True,startup=FakeStartup(),
            solve=solve,clock=lambda:self.now)
        self.bind()
    def bind(self,direction=0):
        token=self.pipeline.configure(**self.context,center=[0.,0.,500.],size=[100.,80.,60.],direction=direction)
        # Pure bridge fixture only. Integrated test below uses REAL startup
        # state and fresh measured frames instead of this test-only ready flag.
        self.pipeline.startup.phase='ready'
        self.pipeline.startup.context=dict(self.context)
        self.pipeline.startup.center=[0.,0.,500.]
        return token
    def sample(self,**changes):
        args=dict(token=self.pipeline.token,session='camera-1',seq=self.pipeline.seq+1,
            frame_seq=self.pipeline.frame_seq+1,captured_at=self.now,u=.6,v=.4,opening=.6)
        args.update(changes)
        return self.pipeline.submit(**args)

    async def test_configure_never_starts_driver_or_publishes_a_center(self):
        self.assertEqual(self.driver.published,[])
        self.assertFalse(await self.pipeline.publish_latest([0.]*6))
        self.assertIn('不自动居中',self.pipeline.reason)

    async def test_four_directions_share_the_same_position_mapping_once(self):
        for direction,xy in [(0,(10.,-8.)),(90,(8.,10.)),(180,(-10.,8.)),(270,(-8.,-10.))]:
            self.bind(direction);self.assertTrue(self.sample())
            target=self.pipeline.latest['target']
            for actual,wanted in zip(target,[*xy,506.]):self.assertAlmostEqual(actual,wanted)

    async def test_only_improving_checked_backend_ik_is_published_with_original_capture(self):
        self.assertTrue(self.sample(captured_at=9.9))
        self.assertTrue(await self.pipeline.publish_latest([0.]*6))
        q,metadata=self.driver.published[-1]
        self.assertEqual(q,[2.,-2.,2.,0.,0.,0.])
        self.assertEqual(metadata['captured_at'],9.9)
        self.assertEqual(metadata['token'],self.driver.owner)
        self.assertEqual(len(self.paths),1)

    async def test_replay_missing_credentials_bad_age_and_nonfinite_rejected(self):
        self.assertTrue(self.sample())
        for changes in ({'seq':0},{'frame_seq':0},{'token':'foreign'},{'session':'other'},
                        {'captured_at':9.6},{'captured_at':10.1},{'u':float('nan')},
                        {'opening':2},{'v':True},{'frame_seq':True}):
            self.assertFalse(self.sample(**changes),changes)
        self.assertEqual(self.driver.published,[])

    async def test_invalidation_and_tool_camera_connection_changes_reject(self):
        old=self.pipeline.token;self.pipeline.invalidate('已停止')
        self.assertFalse(self.sample(token=old))
        for field,value in [('camera_session','new'),('connection',('new-arm',4)),('tool_context','r9')]:
            previous=self.context[field];self.context[field]=value
            self.assertFalse(self.sample());self.context[field]=previous

    async def test_path_rejection_exception_and_non_true_never_publish(self):
        for value in (False,None,1):
            self.now+=.01
            self.pipeline.validate_path=lambda *_:value
            self.assertTrue(self.sample());self.assertFalse(await self.pipeline.publish_latest([0.]*6))
        def crash(*_):raise RuntimeError('geometry missing')
        self.pipeline.validate_path=crash
        self.now+=.01
        self.assertTrue(self.sample());self.assertFalse(await self.pipeline.publish_latest([0.]*6))
        self.assertEqual(self.driver.published,[])

    async def test_measured_180_degree_wrist_cannot_jump_to_zero(self):
        self.assertTrue(self.sample())
        self.assertFalse(await self.pipeline.publish_latest([0.,0.,0.,0.,0.,180.]))
        self.assertEqual(self.driver.published,[])
        self.assertIn('J6',self.pipeline.reason)

    async def test_solver_receives_zero_wrist_bounds_and_cannot_override_them(self):
        def rogue(model,q,target,lo,hi,**kw):
            self.assertEqual(q[5],0.)
            self.assertEqual((lo[5],hi[5]),(0.,0.))
            return {'q':[2.,-2.,2.,0.,0.,1.]}
        self.pipeline.solve=rogue
        self.assertTrue(self.sample())
        self.assertFalse(await self.pipeline.publish_latest([0.,0.,0.,0.,0.,.01]))
        self.assertEqual(self.driver.published,[])

    async def test_missing_context_fails_closed(self):
        def missing():raise RuntimeError('unavailable')
        self.pipeline.read_context=missing
        self.assertFalse(self.sample())
        self.assertFalse(await self.pipeline.publish_latest([0.]*6))

    async def test_absent_or_revoked_startup_never_accepts_hand_input(self):
        self.pipeline.startup=None
        self.assertFalse(self.sample())
        self.assertFalse(await self.pipeline.publish_latest([0.]*6))

    async def test_centering_then_actual_arrival_then_hand_follow(self):
        from backend.core.real_follow_startup import RealFollowStartup
        startup=RealFollowStartup(LinearModel(),lambda:dict(self.context),lambda a,b:True,
                                 clock=lambda:self.now)
        self.pipeline.startup=startup
        self.pipeline.configure(**self.context,center=[0.,0.,500.],size=[100.,80.,60.])
        self.assertFalse(self.sample())
        feedback=dict(q=[0.,0.,0.,0.,0.,180.],stamp=self.now,frame_seq=1,
            connection=self.context['connection'],enabled=True,safety_ok=True,motion_allowed=True,idle=True)
        plan=startup.prepare(feedback,[0.,0.,500.],[-180.]*6,[180.]*6)
        self.assertIsNotNone(plan)
        self.assertFalse(self.sample())
        self.assertTrue(startup.mark_dispatched(plan['token'],feedback))
        self.assertFalse(self.sample())
        for i in range(3):
            self.now+=.02
            feedback.update(q=[0.]*6,stamp=self.now,frame_seq=i+2)
            self.assertEqual(startup.observe_arrival(plan['token'],feedback),i==2)
        self.assertTrue(self.sample())
        self.assertTrue(await self.pipeline.publish_latest([0.]*6))
        self.assertEqual(self.driver.published[-1][0][5],0.)
        startup.cancel('用户停止')
        self.now+=.01
        self.assertFalse(self.sample())
        self.assertFalse(await self.pipeline.publish_latest([0.]*6))

    async def test_slow_validation_cannot_renew_observation_age(self):
        def slow(*_):self.now+=.31;return True
        self.pipeline.validate_path=slow
        self.assertTrue(self.sample());self.assertFalse(await self.pipeline.publish_latest([0.]*6))
        self.assertEqual(self.driver.published,[])

    async def test_bad_solver_output_never_reaches_driver(self):
        for q in ([float('nan')]*6,[9.]*6,[-2.,2.,-2.,0.,0.,0.],None):
            self.pipeline.solve=lambda *a,_q=q,**kw:{'q':_q}
            self.now+=.01;self.assertTrue(self.sample())
            self.assertFalse(await self.pipeline.publish_latest([0.]*6))
        self.assertEqual(self.driver.published,[])

    async def test_one_worker_and_newer_sample_invalidate_late_result(self):
        entered=threading.Event();release=threading.Event()
        def slow(*a,**kw):
            entered.set();release.wait(2);return self.solve(*a,**kw)
        self.pipeline.solve=slow;self.assertTrue(self.sample())
        pending=asyncio.create_task(self.pipeline.publish_latest([0.]*6))
        try:
            await asyncio.to_thread(entered.wait,1)
            self.assertFalse(await self.pipeline.publish_latest([0.]*6))
            self.now+=.01;self.assertTrue(self.sample(u=.7))
        finally:release.set()
        self.assertFalse(await pending)
        self.assertEqual(self.driver.published,[])
        self.assertTrue(await self.pipeline.publish_latest([0.]*6))

    async def test_cancelled_await_keeps_worker_slot_until_it_really_exits(self):
        entered=threading.Event();release=threading.Event()
        def slow(*a,**kw):
            entered.set();release.wait(2);return self.solve(*a,**kw)
        self.pipeline.solve=slow;self.assertTrue(self.sample())
        pending=asyncio.create_task(self.pipeline.publish_latest([0.]*6))
        try:
            await asyncio.to_thread(entered.wait,1);pending.cancel()
            with self.assertRaises(asyncio.CancelledError):await pending
            self.assertIsNotNone(self.pipeline.worker)
            self.assertFalse(self.pipeline.worker.done())
        finally:release.set()
        await self.pipeline.worker
        self.assertEqual(self.driver.published,[])


if __name__=='__main__':unittest.main(verbosity=2)
