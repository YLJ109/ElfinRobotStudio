"""Real production Servo driver against a deterministic FAKE transport only."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.real_servo import RealServoSession, ServoProfile, real_follow_readiness


class Clock:
    def __init__(self): self.now = 10.
    def __call__(self): return self.now
    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


def profile():
    # Deliberately synthetic; these are NOT qualified settings for an E05.
    return ServoProfile(.02, .1, .009, .08, .3, .05, 2., .01,
        (-180.,)*6, (180.,)*6, (5.,)*6, (10.,)*6,
        ("fake-firmware", "fake-tool-load", "fake-stopping", "fake-path-timing"))


class FakeTransport:
    def __init__(self, feedback, clock):
        self.feedback, self.clock = feedback, clock
        self.owner, self.packets = None, []
        self.packet_times = []
        self.aborts = 0
        self.fail = self.hang = self.hook = None
        self.confirm_stop = True
        self.quarantined = False
        self.stop_requested = False
    def acquire(self, owner):
        if self.owner is not None or self.quarantined: return False
        self.owner = owner
        return True
    def release(self, owner):
        if self.owner == owner: self.owner = None
    async def abort(self, owner):
        assert self.owner == owner
        self.aborts += 1
    def quarantine(self,owner,reason):
        assert self.owner == owner
        self.quarantined = True
    async def exchange(self, packet, *, owner, timeout):
        assert self.owner == owner
        assert timeout > 0
        self.packets.append(packet)
        self.packet_times.append(self.clock())
        name = packet.decode().split(',')[0]
        if self.hook: self.hook(name)
        if self.hang == name: await asyncio.Event().wait()
        if self.fail == name: return {"name":name,"ok":False}
        if name == 'PushServoJ':
            self.feedback['q'] = [float(v) for v in packet.decode().split(',')[2:8]]
        if name == 'GrpStop' and self.confirm_stop:
            self.stop_requested = True
        return {"name":name,"ok":True}

    def read(self):
        if self.stop_requested and self.confirm_stop:
            self.clock.now += .001
            self.feedback.update(idle=True,velocity=[0.]*6,stamp=self.clock(),
                                 frame_seq=self.feedback['frame_seq']+1)
        return dict(self.feedback)


class Servo(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.feedback = dict(connection=1,context='cell-tool-v1',stamp=10.,frame_seq=1,
            q=[0.]*6,velocity=[0.]*6,enabled=True,safety_ok=True,idle=True,motion_allowed=True)
        self.transport = FakeTransport(self.feedback,self.clock)
        self.validations = []
        def valid(a,b):
            self.validations.append((tuple(a),tuple(b)))
            return True
        self.driver = RealServoSession(self.transport,self.transport.read,valid,profile(),
                                       clock=self.clock,sleep=self.clock.sleep)
        self.no_network = patch('socket.socket.connect',side_effect=AssertionError('No hardware/network allowed'))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def fresh(self,dt=.02):
        self.clock.now += dt
        self.feedback.update(stamp=self.clock(),frame_seq=self.feedback['frame_seq']+1)

    def publish(self,goal=None,seq=1,**kw):
        return self.driver.publish(goal or [10.]*6,token=kw.get('token',self.driver.owner),
            seq=seq,captured_at=kw.get('captured_at',self.clock()))

    def names(self): return [p.decode().split(',')[0] for p in self.transport.packets]

    async def test_start_uses_documented_wire_order_and_measured_hold(self):
        self.feedback['q']=[1.,2.,3.,4.,5.,6.]
        self.assertTrue(await self.driver.start())
        self.assertEqual(self.transport.packets,[b'StartServo,0,0.02,0.1,;',b'PushServoJ,0,1.0,2.0,3.0,4.0,5.0,6.0,;'])
        self.assertEqual(self.driver.snapshot()['q_command'],self.feedback['q'])
        await self.driver.stop()

    async def test_no_hardware_traffic_for_invalid_or_busy_start(self):
        for key,value in [('enabled',False),('safety_ok',False),('motion_allowed',False),('idle',False),
                          ('stamp',9.),('q',[float('nan')]*6),('velocity',[1.]*6)]:
            with self.subTest(key=key):
                old=self.feedback[key]; self.feedback[key]=value
                d=RealServoSession(self.transport,self.transport.read,lambda a,b:True,profile(),clock=self.clock,sleep=self.clock.sleep)
                self.assertFalse(await d.start());self.feedback[key]=old
        self.transport.owner='other-operation'
        self.assertFalse(await self.driver.start())
        self.assertFalse(self.transport.packets)
        self.assertEqual(self.transport.owner,'other-operation')

    async def test_mailbox_rejects_old_future_nonfinite_and_wrong_owner(self):
        self.assertTrue(await self.driver.start())
        self.assertFalse(self.publish(token='old'))
        self.assertFalse(self.publish(captured_at=9.))
        self.assertFalse(self.publish(captured_at=11.))
        self.assertFalse(self.publish(goal=[float('nan')]*6))
        self.assertFalse(self.publish(goal=[181.]*6))
        self.assertTrue(self.publish(seq=2))
        self.assertFalse(self.publish(seq=1))
        self.assertFalse(self.publish(seq=3,captured_at=9.99))
        await self.driver.stop()

    async def test_latest_only_fixed_period_and_velocity_acceleration_bounds(self):
        self.assertTrue(await self.driver.start())
        for i in range(100): self.assertTrue(self.publish([i/10.]*6,seq=i))
        self.assertEqual(len(self.transport.packets),2)
        self.assertFalse(await self.driver.tick())
        oldq=self.driver.last_q;oldv=self.driver.velocity
        for i in range(100,130):
            self.fresh();self.assertTrue(self.publish([10.]*6,seq=i))
            self.assertTrue(await self.driver.tick(),self.driver.reason)
            for a,b,v,ov in zip(self.driver.last_q,oldq,self.driver.velocity,oldv):
                self.assertLessEqual(abs(a-b),5*.02+1e-9)
                self.assertLessEqual(abs(v-ov),10*.02+1e-9)
            oldq=self.driver.last_q;oldv=self.driver.velocity
        self.assertGreater(len(self.validations),60)
        await self.driver.stop()

    async def test_target_expiry_stops_no_return_to_center_no_restart(self):
        self.assertTrue(await self.driver.start());self.assertTrue(self.publish())
        self.fresh(.301)
        # A regular scheduler would already stop on its missed deadline too.
        self.driver.next_due=self.clock()
        self.assertFalse(await self.driver.tick())
        self.assertEqual(self.names(),['StartServo','PushServoJ','GrpStop'])
        self.assertTrue(self.driver.stop_confirmed)
        self.assertFalse(await self.driver.start());self.assertFalse(self.publish(seq=2))
        self.assertFalse(await self.driver.tick())

    async def test_missing_target_stops_instead_of_automatic_motion(self):
        self.assertTrue(await self.driver.start());self.fresh()
        self.assertFalse(await self.driver.tick())
        self.assertIn('丢掌',self.driver.reason)
        self.assertEqual(self.names()[-1],'GrpStop')

    async def test_missed_cycle_does_not_catch_up(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh(.061)
        self.assertFalse(await self.driver.tick())
        self.assertIn('周期超时',self.driver.reason)
        self.assertEqual(self.names().count('PushServoJ'),1)

    async def test_stale_feedback_cannot_be_refreshed_by_heartbeat(self):
        self.assertTrue(await self.driver.start());self.publish()
        self.clock.now+=.02;self.feedback['stamp']=self.clock()
        self.assertFalse(await self.driver.tick())
        self.assertIn('旧帧',self.driver.reason)

    async def test_expired_feedback_stops(self):
        self.assertTrue(await self.driver.start());self.publish()
        self.clock.now+=.081;self.driver.next_due=self.clock()
        self.assertFalse(await self.driver.tick());self.assertIn('反馈过期',self.driver.reason)

    async def test_context_change_stops_and_reconnect_never_sends_to_new_connection(self):
        for key,value in [('context','new-tool'),('connection',2)]:
            with self.subTest(key=key):
                self.setUp()
                self.assertTrue(await self.driver.start());self.publish();self.fresh()
                self.feedback[key]=value
                self.assertFalse(await self.driver.tick())
                if key=='connection':
                    self.assertNotIn('GrpStop',self.names());self.assertFalse(self.driver.stop_confirmed)
                else:self.assertIn('GrpStop',self.names())

    async def test_tracking_error_or_unsafe_path_stops_before_next_push(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh()
        self.feedback['q']=[3.]*6
        self.assertFalse(await self.driver.tick());self.assertIn('偏差',self.driver.reason)
        self.assertEqual(self.names().count('PushServoJ'),1)

    async def test_validator_checks_transition_not_just_target(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh()
        self.driver.validate_path=lambda a,b:False
        self.assertFalse(await self.driver.tick())
        self.assertEqual(self.names().count('PushServoJ'),1)
        self.assertIn('路径',self.driver.reason)

    async def test_slow_validator_must_not_send_late(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh()
        def slow(a,b): self.clock.now+=.021;return True
        self.driver.validate_path=slow
        self.assertFalse(await self.driver.tick())
        self.assertEqual(self.names().count('PushServoJ'),1)

    async def test_slow_valid_check_does_not_compress_next_packet_interval(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh()
        def slow(a,b):self.clock.now+=.008;return True
        self.driver.validate_path=slow
        self.assertTrue(await self.driver.tick(),self.driver.reason)
        first_sent=self.transport.packet_times[-1]
        self.driver.validate_path=lambda a,b:True
        self.clock.now=self.driver.next_due
        self.feedback.update(stamp=self.clock(),frame_seq=self.feedback['frame_seq']+1)
        self.publish(seq=2)
        self.assertTrue(await self.driver.tick(),self.driver.reason)
        self.assertGreaterEqual(self.transport.packet_times[-1]-first_sent,.02-1e-9)
        await self.driver.stop()

    async def test_feedback_progress_during_path_check_rejects_old_start(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh()
        def changed(a,b):
            self.feedback['q']=[1.]*6
            self.feedback['frame_seq']+=1
            return True
        self.driver.validate_path=changed
        self.assertFalse(await self.driver.tick())
        self.assertEqual(self.names().count('PushServoJ'),1)
        self.assertIn('旧起点',self.driver.reason)

    async def test_start_failure_attempts_stop_but_never_pushes(self):
        self.transport.fail='StartServo'
        self.assertFalse(await self.driver.start())
        self.assertEqual(self.names(),['StartServo','GrpStop'])
        self.assertTrue(self.driver.stop_confirmed)
        self.assertIsNone(self.transport.owner)

    async def test_connection_change_during_start_never_pushes_old_pose(self):
        def reconnect(name):
            if name=='StartServo':self.feedback['connection']=2
        self.transport.hook=reconnect
        self.assertFalse(await self.driver.start())
        self.assertEqual(self.names(),['StartServo'])
        self.assertTrue(self.transport.quarantined)
        self.assertFalse(self.driver.stop_confirmed)

    async def test_late_ok_reply_is_not_accepted_as_timely_success(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh()
        def late(name):
            if name=='PushServoJ':self.clock.now+=.01
        self.transport.hook=late
        self.assertFalse(await self.driver.tick())
        self.assertIn('回执已超期',self.driver.reason)
        self.assertEqual(self.names()[-1],'GrpStop')

    async def test_reply_timeout_does_not_retry_unknown_command(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh()
        self.transport.hang='PushServoJ'
        self.assertFalse(await self.driver.tick())
        self.assertEqual(self.names().count('PushServoJ'),2)
        self.assertEqual(self.names()[-1],'GrpStop')
        self.assertEqual(self.transport.aborts,1)

    async def test_ok_stop_reply_without_new_settled_frame_is_not_stopped(self):
        self.assertTrue(await self.driver.start());self.transport.confirm_stop=False
        result=await self.driver.stop()
        self.assertFalse(result['stop_confirmed']);self.assertEqual(result['state'],'fault')
        self.assertIn('未收到新的停稳反馈',result['reason'])
        self.assertTrue(self.transport.quarantined)
        self.assertFalse(self.transport.acquire('new-session'))

    async def test_pre_ack_captured_frame_cannot_confirm_stop(self):
        self.assertTrue(await self.driver.start());self.transport.confirm_stop=False
        def old_frame(name):
            if name=='GrpStop':self.feedback['frame_seq']+=1
        self.transport.hook=old_frame
        result=await self.driver.stop()
        self.assertFalse(result['stop_confirmed']);self.assertTrue(self.transport.quarantined)

    async def test_cancelling_stop_task_does_not_cancel_bounded_cleanup(self):
        self.assertTrue(await self.driver.start());self.transport.confirm_stop=False
        task=asyncio.create_task(self.driver.stop())
        await asyncio.sleep(0);await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.names()[-1],'GrpStop')
        self.assertEqual(self.driver.state,'fault')
        self.assertTrue(self.transport.quarantined)
        self.assertIsNone(self.transport.owner)

    async def test_stop_racing_start_cannot_activate_after_stop_request(self):
        stopped=[]
        def interrupt(name):
            if name=='StartServo':stopped.append(asyncio.create_task(self.driver.stop()))
        self.transport.hook=interrupt
        self.assertFalse(await self.driver.start())
        await asyncio.gather(*stopped)
        self.assertEqual(self.names(),['StartServo','GrpStop'])
        self.assertNotEqual(self.driver.state,'running')

    async def test_task_cancel_attempts_stop_and_releases_owner(self):
        self.assertTrue(await self.driver.start());self.publish();self.fresh()
        self.transport.hang='PushServoJ'
        task=asyncio.create_task(self.driver.tick())
        await asyncio.sleep(0);await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.names()[-1],'GrpStop')
        self.assertIsNone(self.transport.owner)


class Profiles(unittest.TestCase):
    def test_no_default_parameters_and_no_missing_commissioning_evidence(self):
        for update in ({'evidence':()}, {'period_s':2}, {'reply_timeout_s':.03},
                       {'target_max_age_s':1}, {'acceleration':(0,)*6},
                       {'velocity':(float('inf'),)*6}):
            with self.subTest(update=update),self.assertRaises(ValueError):replace(profile(),**update)

    def test_readiness_never_unlocks_motion_just_because_connected(self):
        for connected in (False,True):
            state=real_follow_readiness(connected=connected)
            self.assertFalse(state['available']);self.assertFalse(state['motion_enabled'])
            self.assertEqual(len(state['missing']),3)


class Api(unittest.IsolatedAsyncioTestCase):
    async def test_readonly_status_cannot_be_unlocked_by_query_post_or_connection(self):
        import httpx
        from types import SimpleNamespace
        from backend import main
        attached=SimpleNamespace(cmd_up=True,stream_up=True)
        with patch.object(main.world,'real',attached):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://127.0.0.1:8710') as c:
                response=await c.get('/api/real/hand-follow/status?enabled=true&qualified=true')
                self.assertEqual(response.status_code,200)
                self.assertTrue(response.json()['connected'])
                self.assertFalse(response.json()['motion_enabled'])
                self.assertFalse(response.json()['available'])
                status=await c.post('/api/real/hand-follow/status',json={'enabled':True})
                self.assertEqual(status.status_code,405)
        # A real finite-jog compatibility route now exists. It must reject a
        # forged qualification flag without configuration/token/confirmation;
        # it does not expose the unqualified Servo driver.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://127.0.0.1:8710') as c:
            denied=await c.post('/api/real/hand-follow/start',json={'qualified':True})
            self.assertEqual(denied.status_code,200)
            self.assertFalse(denied.json()['ok'])
            self.assertFalse(denied.json()['state']['active'])

    async def test_generic_command_path_cannot_send_servo_commands(self):
        from backend.core import protocol
        from backend.core.robot_real import RealLink
        link=RealLink('offline-never-connected')
        with patch('asyncio.open_connection',side_effect=AssertionError('No actual connection')):
            for name,params in [('StartServo',(0,.02,.1)),('PushServoJ',(0,0,0,0,0,0,0))]:
                self.assertNotIn(name,protocol.CMDS)
                reply=await link.ask(name,*params)
                self.assertFalse(reply.ok)
                self.assertEqual(reply.outcome,'not_sent')
        self.assertFalse(link.command_trace)


if __name__=='__main__':unittest.main()
