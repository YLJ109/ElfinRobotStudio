"""统一运动暂停/继续/终结回归；仅假连接与离线副本，不导入World。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from backend.core.motion import MotionExecutor
from backend.core.program import Runner
from backend.core.robot_sim import SimRobot
from backend.motion_session_routes import MotionSession, create_motion_session_router


class FakeLink:
    def __init__(self):
        self.epoch=4;self.command_session='command-a';self.cmd_up=True;self.valid=True;self.pose_fresh=True
        self.frame_seq=1;self.snap={'stamp':time.monotonic(),'moving':1,'paused':0,'enabled':1,'err_code':0}
        self.calls=[];self.reply_ok=True;self.auto_update=True;self.before_ack=False;self.still_moving=False
        self.ack_gate=None;self.started=asyncio.Event();self.publishers=[]
        self.report_name='GrpContinue'

    def state_valid(self):return self.valid
    def pose_valid(self):return self.pose_fresh

    def publish(self,name):
        self.frame_seq+=1
        self.snap.update(stamp=time.monotonic(),paused=int(name=='GrpInterrupt'),
                         moving=int(name!='GrpInterrupt' or self.still_moving))

    async def ask(self,name,*args):
        self.calls.append((name,args));self.started.set()
        if self.ack_gate is not None:await self.ack_gate.wait()
        if self.auto_update:
            if self.before_ack:self.publish(name)
            else:
                self.report_name=name
                async def later():
                    # Real readback is a stream. A single 3ms fake frame may
                    # share ACK's coarse Windows monotonic tick and is correctly
                    # rejected as not newer; keep publishing actual later frames.
                    for _ in range(20):
                        await asyncio.sleep(.004);self.publish(self.report_name)
                self.publishers.append(asyncio.create_task(later()))
        return NS(ok=self.reply_ok)


class FakeWorld:
    MOTION=1
    def __init__(self):
        self.session_id='world-a';self.settings={'mode':'sim'};self.real=None
        self.executor=MotionExecutor(lambda *_args:None);self.runner=Runner()
        model=NS(names=list(range(6)),lower=[-180.]*6,upper=[180.]*6)
        self.sim=SimRobot(model,[90.]*6)
        self.safety={'level':'green'};self.permission=True;self.last_deny='';self._stop_unknown=False
        self.context='scene-a';self.forgotten=0;self.saved_resume={'old':True};self.stops=0
        self.stop_ok=True;self.stop_settled=True;self.stop_gate=None;self.stop_started=asyncio.Event()
        self.hand_follow=NS(active=False);self.real_jog_follow=NS(busy=False);self.robot_play=None

    def plan_context(self):return self.context
    def _stop_interlocked(self):return self._stop_unknown
    def _need(self,_level,_why):
        self.last_deny='' if self.permission else '未获得运动许可'
        return self.permission
    def forget_resume(self):self.forgotten+=1;self.saved_resume=None
    async def stop_execution(self,_reason):
        self.stops+=1;self.stop_started.set()
        if self.stop_gate is not None:await self.stop_gate.wait()
        await self.executor.cancel('fake stop')
        self.sim.stop();self.runner.stop('fake stop')
        self.saved_resume={'rewritten_during_stop':True}
        self._stop_unknown=not (self.stop_ok and self.stop_settled)
        return {'ok':self.stop_ok,'settled':self.stop_settled,'why':'fake stop'}


class MotionSessionChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w=FakeWorld()
        self.s=MotionSession(self.w,confirmation_timeout=.2,command_timeout=.2,poll_interval=.001)
        self.w.motion_session=self.s

    async def asyncTearDown(self):
        if self.w.stop_gate:self.w.stop_gate.set()
        if self.w.real and self.w.real.ack_gate:self.w.real.ack_gate.set()
        if self.w.executor.task and not self.w.executor.task.done():await self.w.executor.cancel('test teardown')
        for task in (self.s._task,self.s._cancel_task):
            if task and not task.done():
                try:await asyncio.wait_for(asyncio.shield(task),.2)
                except (Exception,asyncio.CancelledError):
                    task.cancel();await asyncio.gather(task,return_exceptions=True)
        if self.w.real:await asyncio.gather(*self.w.real.publishers,return_exceptions=True)

    async def motion(self,real=False,program=False):
        if real:self.w.settings['mode']='real';self.w.real=FakeLink()
        else:self.w.sim.move_j([20.,0,0,0,0,0])
        if program:
            self.w.runner.load([{'kind':'wait','sec':2,'src':'WAIT 2','line':1}]);self.w.runner.start()
        async def job(epoch):
            while True:
                self.w.executor.check(epoch);await asyncio.sleep(.001)
        self.w.executor.submit('offline manual motion',job)
        await asyncio.sleep(0)

    async def test_idle_state_is_passive_and_has_compatible_alias_and_no_token(self):
        self.assertIsNone(self.s.token)
        state=self.s.state();self.assertEqual(state['status'],state['state'])
        self.assertFalse(state['can_pause']);self.assertFalse(state['can_resume'])
        self.assertFalse((await self.s.pause())['ok']);self.assertFalse((await self.s.resume())['ok'])
        self.assertFalse(self.s.hold);self.assertEqual(self.w.stops,0)
        self.assertNotIn('backend.core.world',sys.modules);self.assertNotIn('backend.main',sys.modules)

    async def test_manual_sim_executor_is_paused_without_starting_or_changing_runner(self):
        await self.motion();epoch=self.w.executor.epoch;task=self.w.executor.task;steps=self.w.runner.steps
        paused=await self.s.pause();self.assertTrue(paused['ok']);self.assertTrue(paused['can_resume'])
        self.assertTrue(self.w.sim.paused);self.assertEqual(self.w.runner.state,'idle')
        start=self.w.sim.q[:]
        for _ in range(10):self.w.sim.tick(.2)
        self.assertEqual(self.w.sim.q,start)
        resumed=await self.s.resume(paused['token']);self.assertTrue(resumed['ok'])
        self.assertFalse(self.w.sim.paused);self.w.sim.tick(.2);self.assertGreater(self.w.sim.q[0],0)
        self.assertIs(self.w.executor.task,task);self.assertIs(self.w.runner.steps,steps)
        self.assertEqual(self.w.executor.epoch,epoch);self.assertEqual(self.w.runner.state,'idle')

    async def test_program_wait_preserves_remaining_time_and_never_restarts_at_zero(self):
        self.w.runner.load([{'kind':'wait','sec':2,'src':'WAIT 2','line':1}]);self.w.runner.start()
        self.w.runner.advance(time.monotonic());index=self.w.runner.i
        self.assertTrue((await self.s.pause())['ok'])
        remaining=self.w.runner._wait_remaining;await asyncio.sleep(.005)
        self.assertTrue((await self.s.resume())['ok'])
        self.assertEqual(self.w.runner.i,index)
        self.assertAlmostEqual(self.w.runner.wait_until-time.monotonic(),remaining,delta=.02)

    async def test_sim_freezes_before_capturing_context_and_robot_play_uses_controlled_context(self):
        await self.motion(program=True)
        frozen=[]
        def context():frozen.append(self.w.sim.paused);return self.w.context
        self.w.plan_context=context
        self.assertTrue((await self.s.pause())['ok']);self.assertTrue(all(frozen))
        self.assertTrue((await self.s.resume())['ok'])
        guarded=[]
        play=NS(active={'controlled':{'held-piece'}},context=lambda controlled:('stable',tuple(sorted(controlled))),
                guard=lambda:guarded.append(True))
        self.w.robot_play=play
        self.assertTrue((await self.s.pause())['ok'])
        self.w.context='held xyz updated during tick'
        self.assertTrue((await self.s.resume())['ok']);self.assertEqual(guarded,[True])

    async def test_real_manual_requires_ack_and_post_ack_paused_readback(self):
        await self.motion(real=True)
        paused=await self.s.pause()
        self.assertTrue(paused['ok'],paused);self.assertEqual(paused['state'],'paused')
        self.assertEqual(self.w.real.calls,[('GrpInterrupt',(0,))])
        self.assertEqual(self.w.runner.state,'idle','manual pause must not invent a program')
        resumed=await self.s.resume(paused['token'])
        self.assertTrue(resumed['ok'],(resumed,self.w.real.snap,self.w.real.frame_seq,time.monotonic()))
        self.assertEqual(self.w.real.calls[-1],('GrpContinue',(0,)))

    async def test_real_ack_without_new_frame_does_not_report_fake_pause(self):
        await self.motion(real=True);self.w.real.auto_update=False
        result=await self.s.pause()
        self.assertFalse(result['ok']);self.assertFalse(result['paused'])
        self.assertFalse(result['can_resume']);self.assertTrue(self.s.hold);self.assertTrue(self.w._stop_unknown)
        self.assertFalse((await self.s.resume())['ok'])
        self.assertEqual(len(self.w.real.calls),1)

    async def test_pre_ack_frame_even_if_paused_is_not_pause_confirmation(self):
        await self.motion(real=True);self.w.real.before_ack=True
        result=await self.s.pause()
        self.assertFalse(result['ok']);self.assertFalse(result['paused'])

    async def test_paused_flag_while_still_moving_is_not_pause_confirmation(self):
        await self.motion(real=True);self.w.real.still_moving=True
        self.assertFalse((await self.s.pause())['ok'])

    async def test_refused_interrupt_and_refused_continue_keep_resume_closed(self):
        await self.motion(real=True);self.w.real.reply_ok=False
        self.assertFalse((await self.s.pause())['ok']);self.assertTrue(self.s.hold)
        await self.s.cancel()
        await self.motion(real=True)
        self.assertTrue((await self.s.pause())['ok']);self.w.real.reply_ok=False
        self.assertFalse((await self.s.resume())['ok']);self.assertTrue(self.s.hold)
        self.assertFalse(self.s.state()['can_resume'])

    async def test_resume_ack_without_new_frame_keeps_service_hold(self):
        await self.motion(real=True);self.assertTrue((await self.s.pause())['ok'])
        self.w.real.auto_update=False
        result=await self.s.resume()
        self.assertFalse(result['ok']);self.assertTrue(self.s.hold)
        self.assertFalse(result['can_resume'])

    async def test_changed_session_epoch_task_steps_context_or_link_forbids_continue(self):
        mutations=[lambda w:setattr(w,'session_id','world-b'),lambda w:setattr(w.executor,'epoch',99),
                   lambda w:setattr(w.runner,'steps',[]),lambda w:setattr(w,'context','other'),
                   lambda w:setattr(w.real,'command_session','command-b'),lambda w:setattr(w.real,'epoch',99),
                   lambda w:setattr(w.real,'valid',False)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.w=FakeWorld();self.s=MotionSession(self.w,confirmation_timeout=.2,poll_interval=.001)
                await self.motion(real=True);paused=await self.s.pause();self.assertTrue(paused['ok'],paused)
                link=self.w.real;mutate(self.w)
                self.assertFalse((await self.s.resume())['ok'])
                self.assertEqual([name for name,_args in link.calls],['GrpInterrupt'])
                await self.w.executor.cancel('subtest end')
                await asyncio.gather(*link.publishers,return_exceptions=True)

    async def test_stale_pose_and_permission_or_red_safety_prevent_continue_command(self):
        await self.motion(real=True);self.assertTrue((await self.s.pause())['ok'])
        self.w.permission=False;self.assertFalse((await self.s.resume())['ok'])
        self.w.permission=True;self.w.safety['level']='red';self.assertFalse((await self.s.resume())['ok'])
        self.w.safety['level']='green';self.w.real.pose_fresh=False
        self.assertFalse((await self.s.resume())['ok'])
        self.assertEqual(len(self.w.real.calls),1)

    async def test_wrong_token_is_rejected_and_each_pause_rotates_token(self):
        await self.motion();first=await self.s.pause()
        self.assertFalse((await self.s.resume('old-client-token'))['ok'])
        self.assertTrue(self.s.state()['can_resume'])
        self.assertTrue((await self.s.resume(first['token']))['ok'])
        second=await self.s.pause();self.assertNotEqual(first['token'],second['token'])
        self.assertFalse((await self.s.resume(first['token']))['ok'])
        self.assertTrue((await self.s.resume(second['token']))['ok'])

    async def test_invalid_pause_cannot_be_rebound_to_old_motion_by_another_pause(self):
        await self.motion();token=(await self.s.pause())['token']
        self.w.session_id='different-world'
        self.assertFalse((await self.s.pause())['ok'])
        self.assertFalse((await self.s.resume(token))['ok'])
        self.assertFalse((await self.s.pause())['ok'])
        self.assertTrue(self.s.hold)

    async def test_cancel_clears_live_and_persisted_progress_and_new_explicit_epoch_can_run(self):
        await self.motion(program=True);old_epoch=self.w.executor.epoch
        token=(await self.s.pause())['token']
        result=await self.s.cancel()
        self.assertTrue(result['ok']);self.assertTrue(result['cancelled']);self.assertTrue(result['terminal'])
        self.assertEqual(self.w.forgotten,2);self.assertIsNone(self.w.saved_resume)
        self.assertEqual(self.w.runner.steps,[]);self.assertEqual(self.w.runner.state,'aborted')
        self.assertFalse((await self.s.resume(token))['ok']);self.w.runner.resume()
        self.assertEqual(self.w.runner.state,'aborted');self.assertIsNone(self.w.sim.traj)
        with self.assertRaises(asyncio.CancelledError):await self.s.checkpoint(old_epoch)
        await self.motion()
        await self.s.checkpoint(self.w.executor.epoch)
        self.assertTrue((await self.s.pause())['ok'],'new explicit motion must not inherit cancellation')

    async def test_failed_physical_stop_still_terminates_progress_but_cannot_claim_confirmed_cancel(self):
        await self.motion();await self.s.pause();self.w.stop_settled=False
        result=await self.s.cancel()
        self.assertFalse(result['ok']);self.assertFalse(result['cancelled']);self.assertTrue(result['terminal'])
        self.assertTrue(self.s.hold);self.assertFalse((await self.s.resume())['ok'])
        self.assertEqual(self.w.runner.steps,[]);self.assertIsNone(self.w.saved_resume)

    async def test_powered_off_proof_retires_failed_cancel_without_commands_or_old_resume(self):
        await self.motion(real=True)
        # No real pause prerequisite: force an unconfirmed cancellation directly.
        self.w.stop_settled=False;await self.s.cancel()
        self.assertTrue(self.s.hold);generation=self.s._generation;link=self.w.real
        calls=link.calls[:];stops=self.w.stops
        self.s.abandon_powered_off();self.w._stop_unknown=False
        self.assertEqual(self.s._generation,generation+1)
        self.assertFalse(self.s.hold);self.assertTrue(self.s.terminal)
        self.assertIsNone(self.s.token);self.assertIsNone(self.s._binding)
        self.assertEqual(self.s.phase,'cancelled');self.assertEqual(link.calls,calls);self.assertEqual(self.w.stops,stops)
        self.assertFalse((await self.s.resume('old-token'))['ok']);self.assertFalse((await self.s.resume())['ok'])
        self.w.settings['mode']='sim';self.w.real=None
        await self.motion();await self.s.checkpoint(self.w.executor.epoch)
        self.assertTrue((await self.s.pause())['ok'])

    async def test_cancel_is_shielded_from_http_cancellation_and_duplicate_calls_coalesce(self):
        await self.motion();self.w.stop_gate=asyncio.Event()
        caller=asyncio.create_task(self.s.cancel());await self.w.stop_started.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):await caller
        second=asyncio.create_task(self.s.cancel());await asyncio.sleep(0)
        self.assertEqual(self.w.stops,1);self.assertTrue(self.s.terminal)
        self.w.stop_gate.set();self.assertTrue((await second)['ok'])

    async def test_pause_operation_survives_request_cancellation_without_duplicate_interrupt(self):
        await self.motion(real=True);link=self.w.real;link.ack_gate=asyncio.Event()
        caller=asyncio.create_task(self.s.pause());await link.started.wait();caller.cancel()
        with self.assertRaises(asyncio.CancelledError):await caller
        second=asyncio.create_task(self.s.pause());await asyncio.sleep(0)
        self.assertEqual(len(link.calls),1)
        self.assertFalse((await self.s.resume())['ok'])
        link.ack_gate.set();self.assertTrue((await second)['ok'])

    async def test_cancel_preempts_pending_pause_and_late_ack_cannot_restore_it(self):
        await self.motion(real=True);link=self.w.real;link.ack_gate=asyncio.Event()
        caller=asyncio.create_task(self.s.pause());await link.started.wait()
        result=await self.s.cancel();self.assertTrue(result['ok'])
        link.ack_gate.set();self.assertFalse((await caller)['ok'])
        self.assertEqual(self.s.phase,'cancelled');self.assertIsNone(self.s.token)
        self.assertFalse((await self.s.resume())['ok'])

    async def test_checkpoint_holds_manual_motion_and_resumes_same_epoch(self):
        await self.motion();await self.s.pause()
        waiting=asyncio.create_task(self.s.checkpoint(self.w.executor.epoch))
        await asyncio.sleep(.005);self.assertFalse(waiting.done())
        await self.s.resume();await asyncio.wait_for(waiting,.1)

    async def test_routes_are_fixed_readonly_state_and_strict_control_payloads(self):
        app=FastAPI();app.include_router(create_motion_session_router(self.w));self.s=self.w.motion_session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
            result=(await client.get('/api/motion/state')).json()
            self.assertEqual(result['state'],result['status']);self.assertEqual(self.w.stops,0)
            await self.motion()
            result=(await client.post('/api/motion/pause',json={})).json();self.assertTrue(result['ok'])
            self.assertFalse((await client.post('/api/motion/resume',json={'token':'old'})).json()['ok'])
            self.assertFalse((await client.post('/api/motion/resume',json={'token':None})).json()['ok'])
            self.assertFalse((await client.post('/api/motion/pause',json={'url':'bad'})).json()['ok'])
            self.assertTrue((await client.post('/api/motion/resume',json={'token':result['token']})).json()['ok'])
            self.assertTrue((await client.post('/api/motion/cancel',json={})).json()['cancelled'])
            self.assertFalse((await client.post('/api/motion/resume',json={})).json()['ok'])


if __name__=='__main__':unittest.main(verbosity=2)
