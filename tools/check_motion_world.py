"""Actual World/simulation wiring in a disposable copy; no physical device."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
from pathlib import Path
import sys
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.world import World
from backend.motion_session_routes import create_motion_session_router

class WorldMotionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w=World();self.w.settings['mode']='sim';self.w.real=None;self.w.objects.clear()
        create_motion_session_router(self.w);self.s=self.w.motion_session
    async def asyncTearDown(self):await self.w.stop_execution('offline teardown')
    async def pump(self,predicate):
        end=time.monotonic()+8
        while time.monotonic()<end:
            if predicate():return
            self.w._tick(.03,time.monotonic());await asyncio.sleep(.002)
        self.fail('actual simulation timed out: '+str(self.w.executor.snapshot()))
    async def start(self):
        q=self.w.current_joints();q[0]+=3
        reply=await self.w.move_joints(q);self.assertTrue(reply['ok'],reply)
        await self.pump(lambda:self.w.sim.traj is not None)
        return q
    async def test_manual_target_really_freezes_then_resumes_same_curve(self):
        target=await self.start();curve=self.w.sim.traj
        paused=await self.s.pause();self.assertTrue(paused['ok'],paused)
        q=self.w.current_joints()
        for _ in range(20):self.w._tick(.03,time.monotonic());await asyncio.sleep(.001)
        self.assertEqual(self.w.current_joints(),q);self.assertIs(self.w.sim.traj,curve)
        resumed=await self.s.resume(paused['token']);self.assertTrue(resumed['ok'],resumed)
        await self.pump(lambda:not self.w.executor.busy)
        self.assertLess(max(abs(a-b) for a,b in zip(target,self.w.current_joints())),.001)
    async def test_cancelled_target_has_no_old_resume_and_new_explicit_target_can_start(self):
        await self.start();paused=await self.s.pause();self.assertTrue(paused['ok'])
        cancelled=await self.s.cancel();self.assertTrue(cancelled['ok'],cancelled)
        q=self.w.current_joints();self.assertIsNone(self.w.sim.traj)
        self.assertFalse((await self.s.resume(paused['token']))['ok'])
        self.assertIsNone(self.w.resume);self.assertFalse(self.w.exec_file.exists())
        for _ in range(10):self.w._tick(.03,time.monotonic())
        self.assertEqual(self.w.current_joints(),q)
        await self.start();self.assertIsNotNone(self.w.sim.traj)

if __name__=='__main__':unittest.main()
