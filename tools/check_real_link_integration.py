"""Real inherited ASCII roundtrip + staged adapters, entirely fake wire IO."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import time
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from check_real_servo import Clock, profile
from backend.core.real_servo import RealServoSession
from backend.core.real_follow_link import ServoCapableRealLink
from backend.core.real_servo_transport import TransportError
from backend.core.real_follow_finite import FiniteCenterExecutor
from backend.core.real_follow_host import RealPalmFollowHost


class Writer:
    def __init__(self,link,feedback):
        self.link,self.feedback=link,feedback
        self.packets=[];self.closed=False;self.hang=False;self.negative=None
    def write(self,payload):
        if self.closed:raise ConnectionError('closed original writer')
        self.packets.append(payload)
    async def drain(self):
        if self.hang:await asyncio.Event().wait()
        fields=self.packets[-1].decode().split(',');name=fields[0]
        if name=='MoveRelJ':
            axis=int(fields[2]);direction=int(fields[3]);distance=float(fields[4])
            self.feedback['q'][axis]+=(1 if direction else -1)*distance
        if name=='PushServoJ':self.feedback['q']=[float(v) for v in fields[2:8]]
        self.feedback.update(idle=True,velocity=[0.]*6)
        self.link.ascii=(name+(',Fail,20018,;' if self.negative==name else ',OK,;')).encode()
        self.link._data.set()
    def close(self):self.closed=True


class Model:
    def pose_vec(self,q):return [q[0],q[1],500.+q[2],q[3],q[4],q[5]]


class Integration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock=Clock()
        self.feedback=dict(connection=1,context='SYNTHETIC-ONLY',stamp=10.,frame_seq=1,
            q=[0.,0.,0.,0.,0.,180.],velocity=[0.]*6,enabled=True,safety_ok=True,
            motion_allowed=True,idle=True,override=.05)
        self.link=ServoCapableRealLink('OFFLINE-NEVER-CONNECT')
        self.writer=Writer(self.link,self.feedback)
        self.link._writer=self.writer;self.link.cmd_up=True;self.link._command_connection=1
        self.transport=self.link.servo_transport()
        async def sleep(seconds):
            await self.clock.sleep(seconds)
            self.feedback.update(stamp=self.clock(),frame_seq=self.feedback['frame_seq']+1)
        self.sleep=sleep
        self.read=lambda:deepcopy(self.feedback)
        self.validations=[]
        def valid(a,b):self.validations.append((list(a),list(b)));return True
        self.valid=valid
        # Debug-mode asyncio + nested real parser/writer tasks is not a real-
        # time scheduler. These longer SYNTHETIC fixture deadlines test flow,
        # NOT a hardware cycle recommendation or a latency qualification.
        fixture=replace(profile(),period_s=.2,reply_timeout_s=.1,stop_timeout_s=.2)
        self.finite=FiniteCenterExecutor(self.transport,self.read,valid,fixture,
                                         clock=self.clock,sleep=sleep)
        self.driver=RealServoSession(self.transport,self.read,valid,fixture,clock=self.clock,sleep=sleep)
        context=lambda:dict(camera_session='camera-test',connection=1,tool_context='TEST-tool:r1')
        def solve(model,q,target,lo,hi,**kw):return {'q':[1.,0.,0.,0.,0.,0.]}
        self.host=RealPalmFollowHost(Model(),self.driver,self.finite,self.read,context,valid,
            clock=self.clock,sleep=sleep,solve=solve)
        self.network=patch('socket.socket.connect',side_effect=AssertionError('NO DEVICES'))
        self.network.start();self.addCleanup(self.network.stop)

    async def test_full_center_servo_stop_uses_one_original_ascii_channel(self):
        self.assertTrue(await self.host.begin(center=[0.,0.,500.],size=[100.,80.,60.]),self.host.reason)
        names=[p.decode().split(',')[0] for p in self.writer.packets]
        self.assertEqual(names,['MoveRelJ']*18)
        self.assertEqual(self.feedback['q'][5],0.)
        self.assertEqual(self.transport.owner,self.driver.owner)
        p=self.host.pipeline
        self.assertTrue(self.host.submit(token=p.token,session='camera-test',seq=1,frame_seq=1,
            captured_at=self.clock(),u=.6,v=.5,opening=.5))
        self.assertTrue(await self.host.pump(),self.host.reason)
        self.assertEqual(self.writer.packets[18],b'StartServo,0,0.2,0.1,;')
        self.assertEqual(self.writer.packets[19],b'PushServoJ,0,0.0,0.0,0.0,0.0,0.0,0.0,;')
        stopped=await self.link.estop()
        self.assertTrue(stopped.ok)
        self.assertEqual(self.writer.packets[-1],b'GrpStop,0,;')
        self.assertEqual(self.host.phase,'stopped')
        self.assertFalse(self.writer.closed)
        self.assertIsNone(self.transport.owner)

    async def test_exclusive_blocks_normal_ask_and_identity_but_keeps_stop(self):
        self.assertTrue(self.transport.acquire(self.driver.owner))
        self.assertFalse((await self.link.ask('SetOverride',0,.05)).ok)
        self.assertFalse(await self.link._probe_controller_identity())
        self.assertEqual(self.writer.packets,[])
        self.transport.release(self.driver.owner)

    async def test_normal_roundtrip_also_uses_same_arbiter(self):
        reply=await self.link._roundtrip('ReadVersion,0,;','ReadVersion',reply_timeout=.1)
        self.assertTrue(reply.ok)
        self.assertEqual(self.writer.packets,[b'ReadVersion,0,;'])

    async def test_original_queue_or_jog_intent_blocks_exclusive_acquisition(self):
        self.link._jog_intent=(0,1)
        self.assertFalse(self.transport.acquire(self.driver.owner))
        self.link._jog_intent=None
        self.link.q.put_nowait((1,1,'ReadVersion,0,;','ReadVersion'))
        self.assertFalse(self.transport.acquire(self.driver.owner))
        self.assertEqual(self.writer.packets,[])

    async def test_inflight_timeout_closes_original_and_disables_reconnect(self):
        self.assertTrue(self.transport.acquire(self.driver.owner))
        self.writer.hang=True
        with self.assertRaises(TransportError) as caught:
            await self.transport.exchange(b'StartServo,0,0.02,0.1,;',owner=self.driver.owner,timeout=.02)
        self.assertEqual(caught.exception.outcome,'unknown')
        self.assertTrue(self.writer.closed)
        self.assertTrue(self.link._closing)
        self.assertTrue(self.transport.quarantined)
        self.assertFalse((await self.link.ask('ReadVersion',0)).ok)
        self.assertEqual(len(self.writer.packets),1)

    async def test_replacement_writer_never_receives_old_stop_or_abort(self):
        self.assertTrue(self.transport.acquire(self.driver.owner))
        replacement=Writer(self.link,self.feedback)
        self.link._writer=replacement;self.link._command_connection=2
        with self.assertRaises(TransportError):
            await self.transport.exchange(b'GrpStop,0,;',owner=self.driver.owner,timeout=.05)
        self.assertEqual(replacement.packets,[])
        self.assertFalse(replacement.closed)
        self.assertTrue(self.transport.quarantined)

    async def test_axiswise_collision_blocks_center_before_any_packet(self):
        self.assertTrue(self.transport.acquire(self.driver.owner))
        self.finite.validate_segment=lambda a,b:False
        accepted=await self.finite.start([0.]*6,owner=self.driver.owner)
        self.assertTrue(accepted['accepted'])
        await self.finite.task
        self.assertEqual(self.finite.state,'fault')
        self.assertEqual(self.writer.packets,[])
        self.transport.release(self.driver.owner)

    async def test_stop_revokes_scheduled_center_before_first_write(self):
        self.assertTrue(self.transport.acquire(self.driver.owner))
        accepted=await self.finite.start([0.]*6,owner=self.driver.owner)
        self.assertTrue(accepted['accepted'])
        self.finite.invalidate(self.driver.owner)
        self.assertTrue(await self.finite.stop(owner=self.driver.owner))
        self.assertEqual(self.writer.packets,[b'GrpStop,0,;'])
        self.transport.release(self.driver.owner)

    async def test_shared_stop_revokes_host_intent_synchronously(self):
        self.assertTrue(self.transport.acquire(self.driver.owner))
        old_generation=self.host.generation
        old_driver_generation=self.driver._generation
        self.driver.target=((1.,)*6,self.clock())
        task=self.link._begin_stop(self.link.command_session)
        self.assertGreater(self.host.generation,old_generation)
        self.assertGreater(self.driver._generation,old_driver_generation)
        self.assertIsNone(self.driver.target)
        reply=await task
        self.assertTrue(reply.ok)

    async def test_stop_handler_exception_quarantines_instead_of_acknowledging(self):
        async def failed(reason):raise RuntimeError('stop adapter failed')
        self.assertTrue(self.transport.set_stop_handler('test-owner',failed,invalidate=lambda reason:None))
        self.assertTrue(self.transport.acquire('test-owner'))
        reply=await self.link.estop()
        self.assertFalse(reply.ok)
        self.assertEqual(reply.outcome,'unknown')
        self.assertTrue(self.transport.quarantined)
        self.assertTrue(self.writer.closed)

    async def test_disconnect_while_waiting_for_hand_latches_before_reconnect(self):
        self.assertTrue(await self.host.begin(center=[0.,0.,500.],size=[100.,80.,60.]))
        self.link.cmd_up=False  # inherited disconnect-loop boundary
        self.assertTrue(self.link._closing)
        self.assertTrue(self.writer.closed)
        self.assertTrue(self.transport.quarantined)
        self.assertFalse(self.transport.acquire('replacement-owner'))

    async def test_normal_queued_deadline_bounds_raw_wait_too(self):
        from backend.core.robot_real import Desync
        self.writer.hang=True
        self.link._inflight=1
        self.link._pending[1]=(time.monotonic()+.02,self.link.epoch)
        before=time.monotonic()
        with self.assertRaises(Desync):
            await self.link._roundtrip('ReadVersion,0,;','ReadVersion',reply_timeout=1.)
        self.assertLess(time.monotonic()-before,.5)
        self.assertTrue(self.writer.closed)
        self.assertTrue(self.transport.quarantined)

    async def test_cancel_original_ask_inflight_quarantines_immediately(self):
        self.writer.hang=True
        request=asyncio.create_task(self.link.ask('MoveRelJ',0,0,1,1.,timeout=1.))
        await asyncio.sleep(0)
        _,seq,text,name=self.link.q.get_nowait()
        self.link._inflight=seq
        dispatch=asyncio.create_task(self.link._roundtrip(text,name))
        while not self.writer.packets:await asyncio.sleep(0)
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):await request
        self.assertTrue(self.transport.quarantined)
        self.assertTrue(self.writer.closed)
        await asyncio.gather(dispatch,return_exceptions=True)


if __name__=='__main__':unittest.main(verbosity=2)
