"""Behavior regressions for actual World/RealLink. Use run_offline_checks.py."""
import asyncio
import math
import os
from pathlib import Path
import sys
import time
import unittest

os.environ["HUYAN_NO_REAL"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core import config
from sim.backend.core.world import World
from sim.backend.core.robot_real import RealLink, Reply
from sim.backend.core.kinematics import RobotModel, rotation_error, rpy_to_mat
from sim.backend.core.robot_sim import SimRobot


class FakeController(RealLink):
    def __init__(self):
        super().__init__("offline.invalid")
        self.cmd_up = self.stream_up = True
        self.calls = []
        self.stop_ok = True
        self.motion_started = asyncio.Event()
        self.duration = .03
        self.snap.update(joints=[0.] * 6, fsm=33, enabled=1, moving=0, inpos=1,
                         stamp=time.monotonic(), connection_epoch=self.epoch)
        self.tasks = []

    async def ask(self, name, *params, **kwargs):
        self.calls.append(name)
        if name == "MoveRelJ":
            axis, direction, distance = params[1:]
            self.snap.update(moving=1, inpos=0, stamp=time.monotonic())
            async def finish():
                await asyncio.sleep(self.duration)
                self.snap["joints"][axis] += float(distance) * (1 if direction else -1)
                self.frame_seq += 1
                self.snap.update(moving=0, inpos=1, stamp=time.monotonic())
            self.tasks.append(asyncio.create_task(finish()))
            self.motion_started.set()
        return Reply(name, self.stop_ok if name == "GrpStop" else True,
                     err_code=20018 if name == "GrpStop" and not self.stop_ok else None)


def real_world():
    config.save_settings({"mode": "sim", "auto_connect": False})
    w = World()
    w.settings = dict(w.settings, mode="real")
    w.real = FakeController()
    return w


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_before_start_ack_sends_explicit_stop(self):
        link=RealLink("offline.invalid"); link.cmd_up=True
        gate=asyncio.Event(); calls=[]
        async def ask(name,*args,**kwargs):
            calls.append((name,args))
            if args[-1]==1: await gate.wait()
            return Reply(name,True)
        link.ask=ask
        start=asyncio.create_task(link.jog_start(5,1))
        await asyncio.sleep(0)
        self.assertTrue((await link.jog_end()).ok)
        gate.set(); await start
        self.assertIsNone(link.jogging)
        self.assertEqual(calls[-1],('LongJogJ',(0,5,1,0)))

    async def test_absolute_move_cannot_finish_on_new_connection(self):
        w=real_world()
        async def ask(name,*args,**kwargs):
            w.real.epoch+=1
            w.real.snap.update(connection_epoch=w.real.epoch,stamp=time.monotonic())
            return Reply(name,True)
        w.real.ask=ask
        with self.assertRaises(RuntimeError): await w._real_movejto([0]*6)

    async def test_ready_does_not_continue_across_reconnect(self):
        w=real_world(); w.real.snap.update(fsm=7,enabled=0)
        calls=[]
        async def ask(name,*args,**kwargs):
            calls.append(name); w.real.epoch+=1
            w.real.snap.update(connection_epoch=w.real.epoch,stamp=time.monotonic(),fsm=14)
            return Reply(name,True)
        w.real.ask=ask
        self.assertTrue((await w.ready())["ok"])
        await w._ready_task
        self.assertEqual(calls,['Electrify'])
        self.assertFalse(w.ready_state['running'])

    async def test_old_failure_does_not_abort_new_program(self):
        w=real_world(); w.executor.state='failed'; w.executor.error='old failure'
        w.runner.load([{'kind':'label','line':1,'src':'new program','label':'start'}])
        w.start_program(); w._drive_executor(time.monotonic(),False)
        await w.executor.task
        w._drive_executor(time.monotonic(),False)
        self.assertEqual(w.runner.state,'finished')

    async def test_speed_ack_waits_for_new_actual_override(self):
        w=real_world(); w.real.snap['override']=.1
        speed=asyncio.create_task(w._real_set_override(5))
        await asyncio.sleep(.005)
        self.assertFalse(speed.done())
        w.real.snap.update(override=.05,stamp=time.monotonic())
        w.real.frame_seq += 1
        await speed
        self.assertEqual(w.speed_ack['outcome'],'confirmed',str(w.speed_ack)+str(w.real.snap))

    async def test_expired_motion_is_not_sent_by_actual_queue_loop(self):
        from unittest.mock import patch
        link=RealLink('offline.invalid'); sent=[]; entered=asyncio.Event(); gate=asyncio.Event()
        class Reader:
            async def read(self,*args): await asyncio.Event().wait()
        class Writer:
            def close(self): pass
        async def roundtrip(text,name):
            sent.append(name)
            entered.set(); await gate.wait()
            return Reply(name,True)
        link._roundtrip=roundtrip
        with patch('asyncio.open_connection',return_value=(Reader(),Writer())):
            loop=asyncio.create_task(link._cmd_loop())
            while not link.cmd_up: await asyncio.sleep(.001)
            first=asyncio.create_task(link.ask('ReadOverride',0))
            await entered.wait()
            expired=await link.ask('MoveRelJ',0,5,1,2,timeout=.001)
            self.assertEqual(expired.outcome,'expired')
            gate.set(); await first; await asyncio.sleep(.02)
            self.assertEqual(sent,['ReadOverride'])
            loop.cancel()
            with self.assertRaises(asyncio.CancelledError): await loop
            await link.stop()

    async def test_stop_revokes_all_remaining_chunks_and_reports_rejection(self):
        w = real_world()
        self.assertTrue((await w.move_joints([0, 0, 0, 0, 0, 25]))["ok"])
        await w.real.motion_started.wait()
        w.real.stop_ok = False
        result = await w.estop()
        self.assertFalse(result["ok"])
        await asyncio.sleep(.12)
        self.assertEqual(w.real.calls, ["MoveRelJ", "GrpStop"])
        self.assertFalse(w.executor.busy)

    async def test_second_move_is_not_submitted_while_first_is_pending(self):
        w = real_world()
        self.assertTrue((await w.move_joints([0, 0, 0, 0, 0, 20]))["ok"])
        self.assertFalse((await w.move_joints([0, 0, 0, 0, 0, 30]))["ok"])
        await w.stop_execution()

    async def test_program_waits_for_final_segment_before_finishing(self):
        w = real_world()
        w.runner.load([{"kind": "movej", "joints": [0, 0, 0, 0, 0, 15], "line": 1, "src": "move"}])
        w.runner.start()
        w._drive_executor(time.monotonic(), False)
        w._drive_executor(time.monotonic(), False)
        self.assertEqual(w.runner.completed_index, 0)
        self.assertEqual(w.runner.state, "running")
        await asyncio.wait_for(w.executor.task, 1)
        w._drive_executor(time.monotonic(), False)
        self.assertEqual(w.runner.state, "finished")
        self.assertEqual(w.runner.completed_index, 1)
        self.assertAlmostEqual(w.real.snap["joints"][5], 15)

    async def test_stale_or_disconnected_state_never_grants_motion(self):
        w = real_world()
        w.real.snap["stamp"] -= 1
        self.assertFalse(w.joints_known())
        self.assertFalse(w.motion_ok())
        self.assertFalse((await w.move_joints([0] * 6))["ok"])
        w.real.snap["stamp"] = time.monotonic()
        w.real.stream_up = False
        self.assertFalse(w.motion_ok())

    async def test_missing_inpos_is_not_idle(self):
        w = real_world()
        w.real.snap["inpos"] = None
        with self.assertRaises(RuntimeError):
            await w._wait_motion_idle(timeout=.02)

    async def test_connection_epoch_change_prevents_more_chunks(self):
        w = real_world()
        await w.move_joints([0, 0, 0, 0, 0, 25])
        await w.real.motion_started.wait()
        w.real.epoch += 1
        await w.executor.task
        self.assertEqual(w.real.calls.count("MoveRelJ"), 1)
        self.assertEqual(w.executor.state, "failed")

    async def test_expired_queue_item_is_not_eligible(self):
        link = RealLink("offline.invalid")
        link.cmd_up = True
        rep = await link.ask("MoveRelJ", 0, 5, 1, 2, timeout=.001)
        seq = (await link.q.get())[1]
        self.assertTrue(rep.timeout)
        self.assertEqual(rep.outcome, "expired")
        self.assertNotIn(seq, link._pending)
        self.assertNotIn(seq, link._futures)

    async def test_cancelled_call_and_reconnect_clear_queue(self):
        link = RealLink("offline.invalid")
        link.cmd_up = True
        task = asyncio.create_task(link.ask("MoveRelJ", 0, 5, 1, 2))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(link._pending)
        link.invalidate_pending("reconnect")
        self.assertTrue(link.q.empty())

    async def test_release_jog_is_allowed_after_permission_is_lost(self):
        w = real_world()
        w.real.jogging = (5, 1)
        w.real.snap["fsm"] = 7
        self.assertTrue((await w.jog(5, 1, False))["ok"])
        self.assertIn("LongJogJ", w.real.calls)


class NumericalTests(unittest.TestCase):
    def test_pause_and_speed_change_use_effective_time(self):
        sim = SimRobot(RobotModel(), [90] * 6)
        sim.move_j([0, 0, 0, 0, 0, 9])
        sim.tick(.1)
        before = sim.q[5]
        sim.paused = True
        sim.tick(5)
        sim.paused = False
        sim.tick(.01)
        self.assertLess(sim.q[5] - before, 1)
        sim.speed_pct = 20
        q = sim.q[5]
        elapsed = sim._elapsed
        sim.tick(.1)
        self.assertAlmostEqual(sim._elapsed - elapsed, .2)
        self.assertGreater(sim.q[5], q)

    def test_so3_wrap_and_pitch_singularity(self):
        a = rpy_to_mat(0, math.pi / 2, math.radians(179))
        b = rpy_to_mat(0, math.pi / 2, math.radians(-179))
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in rotation_error(a, b))), 2, places=5)
        self.assertAlmostEqual(abs(rotation_error(rpy_to_mat(math.pi, 0, 0), rpy_to_mat(0, 0, 0))[0]), 180)


if __name__ == "__main__":
    unittest.main(verbosity=2)
