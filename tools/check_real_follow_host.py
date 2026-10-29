"""Executable full lifecycle against synthetic finite/Servo adapters only."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
from copy import deepcopy
import unittest

from check_real_servo import Clock, FakeTransport, profile
from backend.core.real_servo import RealServoSession
from backend.core.real_follow_host import RealPalmFollowHost


class Model:
    def pose_vec(self,q):return [q[0],q[1],500.+q[2],q[3],q[4],q[5]]


class Transport(FakeTransport):
    def acquire(self,owner):
        if self.owner==owner and not self.quarantined:return True
        return super().acquire(owner)


class Finite:
    def __init__(self,feedback,transport):
        self.feedback,self.transport=feedback,transport
        self.calls=[];self.epoch=0;self.reject=False;self.before_write=None
    def invalidate(self,owner):self.epoch+=1
    async def start(self,q,*,owner):
        epoch=self.epoch
        if self.before_write:await self.before_write()
        if epoch!=self.epoch or self.reject:return dict(accepted=False,outcome='not_sent')
        assert self.transport.owner==owner
        self.calls.append(('center',list(q)))
        self.feedback.update(q=list(q),idle=True,velocity=[0.]*6)
        return dict(accepted=True,outcome='accepted')
    async def stop(self,*,owner):
        self.invalidate(owner)
        self.calls.append(('stop',))
        self.feedback.update(idle=True,velocity=[0.]*6)
        return True


class Host(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock=Clock()
        self.context=dict(camera_session='cam1',connection=1,tool_context='TEST-ONLY-tool:r1')
        self.feedback=dict(connection=1,context='TEST-ONLY-cell',stamp=10.,frame_seq=1,
            q=[0.,0.,0.,0.,0.,180.],velocity=[0.]*6,enabled=True,safety_ok=True,
            motion_allowed=True,idle=True,override=.05)
        self.transport=Transport(self.feedback,self.clock)
        async def sleep(seconds):
            await self.clock.sleep(seconds)
            self.feedback.update(stamp=self.clock(),frame_seq=self.feedback['frame_seq']+1)
        self.sleep=sleep
        self.driver=RealServoSession(self.transport,self.transport.read,lambda a,b:True,
                                     profile(),clock=self.clock,sleep=sleep)
        self.finite=Finite(self.feedback,self.transport)
        def solve(model,q,target,lo,hi,**kw):
            return {'q':[q[0]+1.,q[1],q[2],q[3],q[4],0.]}
        self.host=RealPalmFollowHost(Model(),self.driver,self.finite,lambda:deepcopy(self.feedback),
            lambda:deepcopy(self.context),lambda a,b:True,clock=self.clock,sleep=sleep,solve=solve)

    async def begin(self):return await self.host.begin(center=[0.,0.,500.],size=[100.,80.,60.])
    def sample(self):
        p=self.host.pipeline
        return self.host.submit(token=p.token,session='cam1',seq=p.seq+1,frame_seq=p.frame_seq+1,
            captured_at=self.clock(),u=.6,v=.5,opening=.5)

    async def test_center_first_no_servo_until_hand_then_zero_wrist_hold(self):
        self.assertTrue(await self.begin(),self.host.reason)
        self.assertEqual(self.finite.calls,[('center',[0.]*6)])
        self.assertEqual(self.transport.packets,[])
        self.assertEqual(self.host.phase,'waiting_hand')
        self.assertFalse(await self.host.pump())
        self.assertTrue(self.sample())
        self.assertTrue(await self.host.pump(),self.host.reason)
        self.assertEqual(self.host.phase,'following')
        self.assertEqual(self.transport.packets[:2],
            [b'StartServo,0,0.02,0.1,;',b'PushServoJ,0,0.0,0.0,0.0,0.0,0.0,0.0,;'])
        self.assertEqual(self.driver.target[0][5],0.)
        stopped=await self.host.stop()
        self.assertTrue(stopped['stop_confirmed'])
        self.assertFalse(self.sample())
        self.assertFalse(await self.begin())

    async def test_stopping_waiting_for_hand_confirms_new_actual_frames(self):
        self.assertTrue(await self.begin())
        result=await self.host.stop()
        self.assertTrue(result['stop_confirmed'])
        self.assertEqual(self.finite.calls[-1],('stop',))
        self.assertIsNone(self.transport.owner)
        self.assertEqual(self.transport.packets,[])

    async def test_changed_camera_or_tool_before_hand_cannot_start_servo(self):
        self.assertTrue(await self.begin())
        self.context['tool_context']='changed'
        self.assertFalse(self.sample())
        self.assertFalse(await self.host.pump())
        self.assertEqual(self.transport.packets,[])
        await self.host.stop()

    async def test_rejected_center_does_not_launch_servo_or_leave_owner(self):
        self.finite.reject=True
        self.assertFalse(await self.begin())
        self.assertIsNone(self.transport.owner)
        self.assertEqual(self.transport.packets,[])

    async def test_busy_or_quarantined_transport_never_dispatches_center(self):
        self.transport.owner='another-program'
        self.assertFalse(await self.begin())
        self.assertEqual(self.finite.calls,[])
        self.assertEqual(self.transport.owner,'another-program')

    async def test_stop_during_awaited_center_start_revokes_before_write(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def wait():entered.set();await release.wait()
        self.finite.before_write=wait
        pending=asyncio.create_task(self.begin())
        await asyncio.wait_for(entered.wait(),1.)
        result=await self.host.stop()
        release.set()
        self.assertFalse(await pending)
        self.assertTrue(result['stop_confirmed'])
        self.assertFalse(any(call[0]=='center' for call in self.finite.calls))
        self.assertEqual(self.transport.packets,[])

    async def test_no_cross_connection_stop_or_restart(self):
        self.assertTrue(await self.begin())
        self.feedback['connection']=2
        stopped=await self.host.stop()
        self.assertFalse(stopped['stop_confirmed'])
        self.assertTrue(self.transport.quarantined)
        self.assertEqual(self.finite.calls,[('center',[0.]*6)])
        self.assertFalse(await self.begin())

    async def test_actual_feedback_failure_before_servo_sends_no_push(self):
        self.assertTrue(await self.begin());self.assertTrue(self.sample())
        self.feedback['enabled']=False
        self.assertFalse(await self.host.pump())
        self.assertEqual(self.transport.packets,[])
        self.assertTrue(self.transport.quarantined)

    async def test_missing_or_high_actual_override_blocks_before_center(self):
        for override in (None,True,.051,0.,float('nan')):
            self.setUp();self.feedback['override']=override
            self.assertFalse(await self.begin())
            self.assertEqual(self.finite.calls,[])
            self.assertEqual(self.transport.packets,[])

    async def test_tool_change_during_servo_start_does_not_publish_hand_goal(self):
        self.assertTrue(await self.begin());self.assertTrue(self.sample())
        def changed(name):
            if name=='StartServo':self.context['tool_context']='new-tool'
        self.transport.hook=changed
        self.assertFalse(await self.host.pump())
        self.assertIsNone(self.driver.target)
        self.assertNotEqual(self.host.phase,'following')

    async def test_unexited_center_task_cannot_handoff_to_servo(self):
        self.assertTrue(await self.begin());self.assertTrue(self.sample())
        pending=asyncio.get_running_loop().create_future()
        self.finite.task=pending
        self.finite.state='settled'
        try:
            self.assertFalse(await self.host.pump())
            self.assertEqual(self.transport.packets,[])
        finally:pending.cancel()


if __name__=='__main__':unittest.main(verbosity=2)
