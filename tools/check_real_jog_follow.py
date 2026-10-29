"""Finite real jog follower with deterministic fake feedback/IO, no sockets."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.real_jog_follow import RealJogFollow, MAX_STEP_DEG


class Device:
    def __init__(self):
        self.now=10.;self.seq=1;self.q=[0.]*6
        self.flags=dict(mode='real',connection=1,context='cell-tool-1',enabled=True,motion_allowed=True,
            safety_ok=True,idle=True,override=.05,confirmed_tool=True,camera_ok=True,
            camera_session='fake-camera',camera_aspect=4/3,busy_external=False)
        self.commands=[];self.stops=[];self.checks=[]
        self.pending=None;self.arrive_at=0
        self.freeze=False;self.ack=True;self.stop_ok=True;self.safe=True
        self.solve_gate=self.move_gate=None
        self.solve_seen=asyncio.Event();self.move_seen=asyncio.Event()

    def clock(self):return self.now
    async def sleep(self,seconds):self.now+=seconds;await asyncio.sleep(0)
    def read(self):
        if not self.freeze:self.seq+=1
        if self.pending is not None and self.seq>=self.arrive_at:
            self.q=self.pending;self.pending=None;self.flags['idle']=True
        return dict(self.flags,stamp=9. if self.freeze else self.now,frame_seq=self.seq,q=self.q[:],
                    pose=[self.q[0]*10,self.q[1]*10,700+self.q[2]*10,90.,0.,0.])
    async def solve(self,seed,pose):
        self.solve_seen.set()
        if self.solve_gate is not None:await self.solve_gate.wait()
        return {'ok':True,'q':[pose[0]/10,pose[1]/10,(pose[2]-700)/10,0.,0.,0.]}
    async def check(self,start,end,box):
        self.checks.append((start[:],end[:],box))
        return self.safe
    async def move(self,axis,delta,expected):
        assert self.pending is None,'A second move was sent before the first was confirmed'
        assert abs(delta)<=MAX_STEP_DEG+1e-9
        assert sum(abs(a-b)>1e-9 for a,b in zip(self.q,expected))==1
        self.commands.append((axis,delta,expected[:],self.seq,self.now))
        self.move_seen.set()
        if self.move_gate is not None:await self.move_gate.wait()
        if self.ack:
            self.pending=expected[:];self.arrive_at=self.seq+2;self.flags['idle']=False
        return self.ack
    async def stop(self,reason):
        self.stops.append(reason);self.pending=None;self.flags['idle']=True
        return self.stop_ok


class Jog(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.device=Device()
        d=self.device
        self.follow=RealJogFollow(d.read,d.solve,d.check,d.move,d.stop,clock=d.clock,sleep=d.sleep)
        self.no_network=patch('socket.socket.connect',side_effect=AssertionError('No hardware allowed'))
        self.no_network.start();self.addCleanup(self.no_network.stop)

    async def asyncTearDown(self):
        self.device.stop_ok=True
        await self.follow.stop('test cleanup')

    def config(self,direction=0):
        return dict(session='fake-camera',aspect=4/3,depth=True,depth_mode='gesture',rotation=False,
                    direction=direction,enclosure={'min':[-400,-150,450],'max':[400,150,950]})
    async def begin(self):
        self.assertTrue(self.follow.configure(self.config())['ok'])
        result=await self.follow.start(dict(token=self.follow.token,session='fake-camera',confirm_real=True))
        self.assertTrue(result['ok'],result)
    def sample(self,seq,u=.6,v=.4,opening=.5,**extra):
        return self.follow.sample(dict(token=self.follow.token,session='fake-camera',seq=seq,
            u=u,v=v,hand_open=opening,hand_present=True,palm_scale=.2,angle=0,age_ms=0,**extra))
    async def pump(self,condition,*,u=.6,v=.4,limit=100):
        for _ in range(limit):
            if condition():return
            if self.follow.active:self.sample(self.follow.seq+1,u=u,v=v)
            await asyncio.sleep(0)
        self.fail('Fake-device condition did not complete: '+repr(self.follow.snapshot()))

    async def test_configure_and_start_do_not_move_or_automatically_center(self):
        await self.begin()
        for _ in range(10):await asyncio.sleep(0)
        self.assertTrue(self.follow.active);self.assertTrue(self.follow.waiting)
        self.assertFalse(self.device.commands)
        self.assertEqual(self.follow.actual[:3],[0.,0.,700.])

    async def test_explicit_confirmation_and_controller_preconditions_are_required(self):
        self.assertTrue(self.follow.configure(self.config())['ok'])
        self.assertFalse((await self.follow.start(dict(token=self.follow.token,session='fake-camera')))['ok'])
        for flag,value in [('enabled',False),('motion_allowed',False),('override',.051),('override',0),
                           ('safety_ok',False),('confirmed_tool',False),('idle',False),('busy_external',True)]:
            with self.subTest(flag=flag):
                old=self.device.flags[flag];self.device.flags[flag]=value
                reply=await self.follow.start(dict(token=self.follow.token,session='fake-camera',confirm_real=True))
                self.assertFalse(reply['ok'],reply);self.device.flags[flag]=old
        self.assertFalse(self.device.commands)

    async def test_only_one_bounded_axis_step_until_new_settled_feedback(self):
        await self.begin()
        await self.pump(lambda:len(self.device.commands)>=5)
        self.assertGreaterEqual(len(self.device.checks),5)
        for first,second in zip(self.device.commands,self.device.commands[1:]):
            self.assertGreater(second[3],first[3]+2)
            self.assertGreater(second[4],first[4])
        for axis,delta,expected,_,_ in self.device.commands:
            self.assertIn(axis,range(6));self.assertLessEqual(abs(delta),MAX_STEP_DEG)

    async def test_mailbox_overwrites_old_goal_before_send(self):
        await self.begin();self.device.solve_gate=asyncio.Event()
        self.sample(1,u=.9,v=.5)
        await asyncio.wait_for(self.device.solve_seen.wait(),.2)
        self.sample(2,u=.1,v=.5)
        self.device.solve_gate.set()
        await self.pump(lambda:bool(self.device.commands),u=.1,v=.5)
        self.assertTrue(all(delta<0 for _,delta,_,_,_ in self.device.commands))

    async def test_absence_before_first_palm_waits_but_loss_after_detection_stops(self):
        await self.begin()
        self.assertTrue(self.follow.sample(dict(token=self.follow.token,session='fake-camera',seq=1,age_ms=0,hand_present=False))['ok'])
        self.assertTrue(self.follow.active)
        self.sample(2)
        self.follow.sample(dict(token=self.follow.token,session='fake-camera',seq=3,age_ms=0,hand_present=False))
        await self.follow.stop()
        self.assertFalse(self.follow.active);self.assertTrue(self.device.stops)
        self.assertFalse(self.device.commands)

    async def test_lost_tracking_stops_and_never_automatically_restarts(self):
        await self.begin()
        await self.pump(lambda:bool(self.device.commands))
        old_token=self.follow.token
        self.device.now+=.301
        for _ in range(20):await asyncio.sleep(0)
        await self.follow.stop()
        count=len(self.device.commands)
        result=self.follow.sample(dict(token=old_token,session='fake-camera',seq=99,age_ms=0,u=.9,v=.9,hand_open=1))
        self.assertFalse(result['ok']);self.assertFalse(self.follow.active)
        for _ in range(10):await asyncio.sleep(0)
        self.assertEqual(len(self.device.commands),count)

    async def test_unconfirmed_stop_keeps_motion_ownership_locked(self):
        await self.begin();self.device.stop_ok=False
        reply=await self.follow.stop()
        self.assertFalse(reply['ok']);self.assertTrue(self.follow.busy)
        self.assertFalse(self.follow.configure(self.config())['ok'])
        self.device.stop_ok=True
        self.assertTrue((await self.follow.stop())['ok']);self.assertFalse(self.follow.busy)

    async def test_rejects_invalid_old_and_future_samples_without_motion(self):
        await self.begin()
        self.assertTrue(self.sample(2)['ok'])
        result=self.sample(1)
        self.assertFalse(result['ok']);self.assertFalse(self.follow.active)
        await self.follow.stop()
        self.assertFalse(self.device.commands)

    async def test_stale_feedback_stops_before_next_move(self):
        await self.begin();self.device.freeze=True
        for _ in range(10):await asyncio.sleep(0)
        await self.follow.stop()
        self.assertFalse(self.follow.active);self.assertFalse(self.device.commands)

    async def test_wrong_camera_or_changed_context_stops(self):
        await self.begin();self.device.flags['context']='different-tool'
        self.assertFalse(self.sample(1)['ok'])
        await self.follow.stop();self.assertFalse(self.device.commands)

    async def test_unsafe_transition_does_not_send_any_command(self):
        await self.begin();self.device.safe=False
        await self.pump(lambda:len(self.device.checks)>3)
        self.assertFalse(self.device.commands)

    async def test_refused_ack_is_not_retried(self):
        await self.begin();self.device.ack=False
        await self.pump(lambda:not self.follow.active)
        await self.follow.stop()
        self.assertEqual(len(self.device.commands),1)
        self.assertTrue(self.device.stops)

    async def test_stop_during_inflight_ack_does_not_send_next_target(self):
        await self.begin();self.device.move_gate=asyncio.Event()
        self.sample(1)
        await asyncio.wait_for(self.device.move_seen.wait(),.2)
        await self.follow.stop()
        self.assertEqual(len(self.device.commands),1)
        self.assertTrue(self.device.stops)
        self.assertFalse(self.follow.active)

    async def test_independent_watchdog_expires_target_during_ack_wait(self):
        await self.begin();self.device.move_gate=asyncio.Event()
        self.sample(1)
        await asyncio.wait_for(self.device.move_seen.wait(),.2)
        self.device.now+=.301
        for _ in range(30):await asyncio.sleep(0)
        await self.follow.stop()
        self.assertFalse(self.follow.active)
        self.assertTrue(self.device.stops)
        self.assertEqual(len(self.device.commands),1)

    async def test_slow_cancellation_cannot_delay_stop_or_unlock_motion(self):
        release=asyncio.Event()
        seen=asyncio.Event()
        async def ignores_first_cancel(axis,delta,expected):
            seen.set()
            try:await release.wait()
            except asyncio.CancelledError:await release.wait()
            return True
        self.follow.move=ignores_first_cancel
        await self.begin();self.sample(1)
        await asyncio.wait_for(seen.wait(),.2)
        try:
            result=await asyncio.wait_for(self.follow.stop(),.5)
            self.assertTrue(self.device.stops)
            self.assertFalse(result['ok'])
            self.assertTrue(self.follow.busy)
        finally:
            release.set()
            for _ in range(10):await asyncio.sleep(0)
        self.assertTrue((await self.follow.stop())['ok'])

    async def test_sub_millimeter_new_target_invalidates_old_direction(self):
        await self.begin()
        original=self.device.check
        changed=False
        async def reverse(start,end,box):
            nonlocal changed
            if not changed:
                changed=True
                self.sample(self.follow.seq+1,u=.4995,v=.5)
            return await original(start,end,box)
        # Exaggerated fake IK ensures both tiny cartesian targets create a step.
        async def solve(seed,pose):return {'ok':True,'q':[pose[0]*10,0.,0.,0.,0.,0.]}
        self.follow.solve=solve;self.follow.check_step=reverse
        self.sample(1,u=.5005,v=.5)
        await self.pump(lambda:bool(self.device.commands),u=.4995,v=.5)
        self.assertLess(self.device.commands[0][1],0)

    async def test_continuously_updated_target_does_not_starve_safe_fresh_microsteps(self):
        self.follow.pose_for_joints=lambda q:[q[0]*10,q[1]*10,700+q[2]*10,90.,0.,0.]
        await self.begin()
        count=0
        async def changing_check(start,end,box):
            nonlocal count
            count+=1
            self.sample(self.follow.seq+1,u=.6+count*.0001,v=.5)
            return True
        self.follow.check_step=changing_check
        await self.pump(lambda:len(self.device.commands)>=3,u=.6,v=.5)
        self.assertTrue(all(delta>0 for _,delta,_,_,_ in self.device.commands))

    async def test_late_cancelled_callback_exit_does_not_unlock_after_prior_stop(self):
        entered=asyncio.Event();late_effect=[]
        async def late(axis,delta,expected):
            entered.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(.01)
                late_effect.append('late transport side effect')
                return True
        self.follow.move=late
        await self.begin();self.sample(1)
        await asyncio.wait_for(entered.wait(),.2)
        result=await self.follow.stop()
        self.assertTrue(self.device.stops);self.assertTrue(late_effect)
        self.assertFalse(result['ok']);self.assertTrue(self.follow.busy)
        self.assertTrue((await self.follow.stop())['ok'])

    async def test_cancelling_watchdog_stops_instead_of_removing_protection(self):
        await self.begin();self.device.move_gate=asyncio.Event()
        self.sample(1)
        await asyncio.wait_for(self.device.move_seen.wait(),.2)
        self.follow._watch_task.cancel()
        await asyncio.gather(self.follow._watch_task,return_exceptions=True)
        await self.follow.stop()
        self.assertFalse(self.follow.active);self.assertTrue(self.device.stops)

    async def test_stale_stop_token_does_not_stop_new_session(self):
        await self.begin()
        self.assertFalse((await self.follow.stop(token='foreign'))['ok'])
        self.assertTrue(self.follow.active);self.assertFalse(self.device.stops)

    async def test_mapping_and_actual_orientation_use_same_world_axes_on_all_sides(self):
        for direction,target in [(0,[399.5,-149.5,949.5]),(90,[149.5,399.5,949.5]),
                                 (180,[-399.5,149.5,949.5]),(270,[-149.5,-399.5,949.5])]:
            with self.subTest(direction=direction):
                self.assertTrue(self.follow.configure(self.config(direction))['ok'])
                self.assertTrue((await self.follow.start(dict(token=self.follow.token,session='fake-camera',confirm_real=True)))['ok'])
                self.assertTrue(self.sample(1,u=1,v=0,opening=1)['ok'])
                self.assertEqual(self.follow.target[:3],target)
                self.assertEqual(self.follow.target[3:],[90.,0.,0.])
                snap=self.follow.snapshot()
                self.assertEqual(snap['bounds']['max'][0]-snap['bounds']['min'][0],800)
                self.assertEqual(snap['world_bounds']['max'][0]-snap['world_bounds']['min'][0],
                                 300 if direction in (90,270) else 800)
                await self.follow.stop()

    async def test_actual_tcp_must_be_inside_box_before_start(self):
        self.assertTrue(self.follow.configure(self.config())['ok'])
        self.device.q[0]=50.
        result=await self.follow.start(dict(token=self.follow.token,session='fake-camera',confirm_real=True))
        self.assertFalse(result['ok']);self.assertIn('不在跟随框',result['why'])
        self.assertFalse(self.device.commands)


if __name__=='__main__':unittest.main()
