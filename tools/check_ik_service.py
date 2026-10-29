"""逆解查询：有界并发、取消持槽、上下文失效、缓存隔离与精度回归。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import sys
import threading
import time
import statistics
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.ik_preview import PreviewIKService
from backend.core.kinematics import RobotModel
from backend.core.world import World


def fixture():
    model = RobotModel('elfin5')
    world = SimpleNamespace(settings={'mode': 'sim'}, real=None, _cfg_rev=0, session_id='offline',
        joints_known=lambda: True, current_joints=lambda: [0.]*6,
        limits=lambda: (model.lower, model.upper), motion_model=lambda: model,
        precheck=Mock(return_value=None), log=SimpleNamespace(add=Mock()))
    return world, model


class CalculationTests(unittest.TestCase):
    def test_query_does_not_write_per_drag_disk_log_or_shared_fk_cache(self):
        world, model = fixture()
        goal = model.pose_vec([10, 20, -30, 40, -50, 60])
        model.pose_vec([0]*6)
        old_key, old_value = model._fk_key, model._fk_value
        result = World.ik_pose(world, goal)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['reason_code'], 'solved')
        self.assertGreaterEqual(result['solve_ms'], 0)
        self.assertEqual(model._fk_key, old_key)
        self.assertIs(model._fk_value, old_value)
        world.log.add.assert_not_called()

    def test_cancelled_or_expired_results_never_include_executable_joints(self):
        world, model = fixture()
        goal = model.pose_vec([10, 20, -30, 40, -50, 60])
        for kwargs, code in (({'cancelled': lambda: True}, 'cancelled'), ({'budget_s': 0}, 'solve_timeout')):
            result = World.ik_pose(world, goal, **kwargs)
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['reason_code'], code)
            self.assertNotIn('joints', result)
            world.precheck.assert_not_called()

    def test_late_success_after_context_change_is_not_published(self):
        world, model = fixture()
        target = model.pose_vec([0]*6)
        def changed(*args, **kwargs):
            world._cfg_rev += 1
            return [0.]*6, 0., 0., True
        model.ik = changed
        result = World.ik_pose(world, target)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason_code'], 'context_changed')
        self.assertNotIn('joints', result)

    def test_low_level_budget_and_cancellation_are_checked_inside_restarts(self):
        _, model = fixture()
        target = [400, 400, 500, 60, 20, 90]
        checks = 0
        def cancelled():
            nonlocal checks
            checks += 1
            return checks > 3
        result = model.ik([0.]*6, target, cancelled=cancelled)
        self.assertFalse(result[3])
        self.assertLess(checks, 20, '取消不能继续遍历全部重启与回溯')
        self.assertFalse(model.ik([0.]*6, target, deadline=time.monotonic()-1)[3])

    def test_geometry_early_reject_avoids_iterative_work_without_relaxing_accuracy(self):
        m = RobotModel('elfin5', [0, 0, -15], [0, 0, 90])
        target = [570.8209, 4.339706, 966.0592, 90, 0, -33]
        world, _ = fixture()
        world.motion_model = lambda: m
        lower, upper = m.lower, m.upper
        previous, current = [], []
        for _ in range(5):
            start = time.perf_counter()
            old_result = m.ik([0]*6, target, lower, upper, iters=100)
            previous.append((time.perf_counter()-start)*1000)
            self.assertFalse(old_result[3])
            start = time.perf_counter()
            new_result = World.ik_pose(world, target)
            current.append((time.perf_counter()-start)*1000)
            self.assertEqual(new_result['reason_code'], 'unreachable_pose')
            self.assertNotIn('joints', new_result)
        print('离线固定不可达位姿5次中位耗时(ms)：数值搜索=', round(statistics.median(previous), 3),
              '几何早拒绝=', round(statistics.median(current), 3), flush=True)
        world.log.add.assert_not_called()


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_request_fails_busy_without_queueing_another_thread(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def solve(*args, **kwargs):
            calls.append(kwargs); started.set()
            release.wait(2)
            return {'ok': True}
        gate = PreviewIKService()
        world = SimpleNamespace(ik_pose=solve)
        first = asyncio.create_task(gate.solve(world, [0]*6))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            denied = await gate.solve(world, [1]*6)
            self.assertEqual(denied['reason_code'], 'ik_busy')
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            await first
        await asyncio.sleep(0)
        self.assertIsNone(gate._task)

    async def test_cancelled_await_keeps_slot_until_thread_really_exits(self):
        started, release, observed_cancel = threading.Event(), threading.Event(), threading.Event()
        def solve(*args, **kwargs):
            started.set()
            while not release.wait(.001):
                if kwargs['cancelled']():
                    observed_cancel.set()
            return {'ok': False}
        gate = PreviewIKService()
        world = SimpleNamespace(ik_pose=solve)
        first = asyncio.create_task(gate.solve(world, [0]*6))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            worker = gate._task
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertTrue(await asyncio.to_thread(observed_cancel.wait, 1))
            self.assertEqual((await gate.solve(world, [1]*6))['reason_code'], 'ik_busy')
        finally:
            release.set()
            await worker
        await asyncio.sleep(0)
        self.assertIsNone(gate._task)

    async def test_worker_error_releases_slot_without_hiding_error(self):
        gate = PreviewIKService()
        def fail(*args, **kwargs):
            raise ValueError('离线计算异常')
        with self.assertRaisesRegex(ValueError, '离线计算异常'):
            await gate.solve(SimpleNamespace(ik_pose=fail), [0]*6)
        await asyncio.sleep(0)
        self.assertIsNone(gate._task)


if __name__ == '__main__':
    unittest.main()
