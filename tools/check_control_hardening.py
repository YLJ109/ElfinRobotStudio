"""控制边界回归：只允许在隔离副本中运行，使用假链路。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import config, protocol
from backend.core.robot_real import RealLink, Reply
from backend.core.world import World


class ProtocolBoundaryTests(unittest.TestCase):
    def test_encoder_rejects_command_delimiters_and_control_characters(self):
        for token in ("0;GrpEnable,0,;", "TCP,other", "TCP\n", "TCP\r", "TCP\x00"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                protocol.encode("MoveLTo", token)
        with self.assertRaises(ValueError):
            protocol.encode("GrpStop;GrpEnable", 0)

    def test_unranged_numeric_parameters_are_finite(self):
        link = RealLink("offline.invalid")
        for value in (float("nan"), float("inf"), "nan", "0;GrpEnable,0,;", True):
            with self.subTest(value=value):
                self.assertIsNotNone(link._validate("MoveJTo", (0, value, 0, 0, 0, 0, 0)))

    def test_normal_wire_format_is_unchanged(self):
        self.assertEqual(protocol.encode("MoveRelJ", 0, 5, 1, .25), b"MoveRelJ,0,5,1,0.25,;")
        self.assertEqual(protocol.encode("PackageVersion"), b"PackageVersion,;")
        self.assertEqual(protocol.encode("MoveLTo", 0, 1, 2, 3, 0, 0, 0, "TCP_1", "Base"),
                         b"MoveLTo,0,1,2,3,0,0,0,TCP_1,Base,;")


def fake_world():
    config.save_settings({"mode": "sim", "auto_connect": False})
    world = World()
    world.settings = dict(world.settings, mode="real")
    link = RealLink("offline.invalid")
    link.cmd_up = link.stream_up = True
    link.snap.update(joints=[0.] * 6, stamp=time.monotonic(), connection_epoch=link.epoch,
                     fsm=33, enabled=1, moving=0, inpos=1, err_code=0)
    world.real = link
    return world, link


class ControlBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_epoch_barrier_does_not_trigger_another_protective_stop(self):
        world, link = fake_world()
        calls=[]
        async def feedback():
            await asyncio.sleep(.03)
            link.frame_seq+=1
            link.snap.update(stamp=time.monotonic(),connection_epoch=link.epoch,moving=0,inpos=1)
        tasks=[]
        async def estop():
            calls.append('GrpStop');link.epoch+=1
            world._tick(.02,time.monotonic())
            tasks.append(asyncio.create_task(feedback()))
            return Reply('GrpStop',True)
        link.estop=estop
        result=await world.stop_execution('explicit cancellation')
        await asyncio.gather(*tasks)
        self.assertTrue(result['ok'],result)
        self.assertEqual(calls,['GrpStop'])
        self.assertFalse(world._stop_unknown)

    async def test_raw_write_cannot_bypass_dedicated_control_routes(self):
        world, link = fake_world()
        calls = []
        async def ask(name, *args, **kwargs):
            calls.append(name)
            return Reply(name, True)
        link.ask = ask
        for command, parameters in (("MoveRelJ", (0, 0, 1, .25)), ("GrpEnable", (0,)),
                                    ("SetEndDO", (0, 0, 1)), ("SetOverride", (0, 1.))):
            with self.subTest(command=command):
                result = await world.ask_real(command, *parameters)
                self.assertFalse(result["ok"])
        self.assertEqual(calls, [])

    async def test_raw_read_remains_available(self):
        world, link = fake_world()
        calls = []
        async def ask(name, *args, **kwargs):
            calls.append(name)
            return Reply(name, True)
        link.ask = ask
        self.assertTrue((await world.ask_real("ReadActPos", 0))["ok"])
        self.assertEqual(calls, ["ReadActPos"])

    async def test_unknown_stop_latches_motion_until_manual_stop_confirms(self):
        world, link = fake_world()
        async def estop():
            return Reply("GrpStop", False)
        link.estop = estop
        result = await world.stop_execution("停止被拒")
        self.assertFalse(result["ok"])
        self.assertFalse(world.motion_ok())
        self.assertFalse(world._need(world.MOTION, "未知停止后的运动"))
        self.assertFalse((await world.power("enable"))["ok"])
        self.assertFalse((await world.ready())["ok"])
        speed = world.settings["real_speed_pct"]
        world.set_speed(100.)
        self.assertEqual(world.settings["real_speed_pct"], speed)
        self.assertFalse(world.speed_ack["ok"])

    async def test_concurrent_stops_share_one_command_even_if_caller_cancels(self):
        world, link = fake_world()
        calls, gate, entered = [], asyncio.Event(), asyncio.Event()
        async def estop():
            calls.append("GrpStop")
            entered.set()
            await gate.wait()
            return Reply("GrpStop", False)
        link.estop = estop
        first = asyncio.create_task(world.stop_execution("第一次"))
        await entered.wait()
        second = asyncio.create_task(world.stop_execution("第二次"))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(world.motion_ok())
        gate.set()
        self.assertFalse((await second)["ok"])
        self.assertEqual(calls, ["GrpStop"])

    async def test_stale_state_requests_stop_on_existing_command_connection(self):
        world, link = fake_world()
        calls = []
        sent = asyncio.Event()
        link.snap.update(stamp=time.monotonic() - 1., moving=1, inpos=0)
        async def ask(name, *args, **kwargs):
            calls.append(name)
            if name == "GrpStop":
                sent.set()
            return Reply(name, False)
        link.ask = ask
        world._tele_at = time.monotonic()
        world._tick(.02, time.monotonic())
        await asyncio.wait_for(sent.wait(), timeout=1.)
        self.assertIn("GrpStop", calls, f"停止结果={world.stop_result}；未知闭锁={world._stop_unknown}")
        self.assertFalse(world.motion_ok())

    async def test_frame_before_stop_ack_is_not_stopped_confirmation(self):
        world, link = fake_world()
        async def estop():
            link.epoch += 1
            link.frame_seq += 1
            link.snap.update(stamp=time.monotonic(), connection_epoch=link.epoch, moving=0, inpos=1)
            await asyncio.sleep(.001)
            return Reply("GrpStop", True)
        link.estop = estop
        result = await world.stop_execution("测试停止")
        self.assertFalse(result["settled"])
        self.assertFalse(result["ok"])

    async def test_stop_is_sent_before_cancellation_resistant_worker_exits(self):
        world, link = fake_world()
        stop_sent, worker_release, worker_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def worker(epoch):
            worker_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await worker_release.wait()
        async def estop():
            stop_sent.set()
            return Reply("GrpStop", False)
        link.estop = estop
        world.executor.submit("慢取消任务", worker)
        await worker_started.wait()
        stopping = asyncio.create_task(world.stop_execution("测试优先停止"))
        try:
            # 等待事实事件，不把宿主机调度延迟误当作控制逻辑失败。
            await asyncio.wait_for(stop_sent.wait(), timeout=1.)
            self.assertTrue(stop_sent.is_set())
        finally:
            worker_release.set()
            await stopping


if __name__ == "__main__":
    unittest.main(verbosity=2)
