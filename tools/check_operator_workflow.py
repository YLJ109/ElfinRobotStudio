"""真实操作链离线回归。只由 run_offline_checks.py 在已登记临时副本运行。"""
from _offline_guard import require_offline_copy
require_offline_copy()  # 必须先于任何 backend 导入；拒绝原工作区和主数据目录。

import asyncio
import copy
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from backend import main
from _app_test_support import app_for
from backend.core import config, tool_library
from backend.core.robot_real import RealLink, Reply
from backend.core.world import World


class OperatorWorkflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stages = []
        config._settings = copy.deepcopy(config.DEFAULTS)
        config._settings.update(mode='sim', auto_connect=False, tts=False)
        self.world = World()
        root = Path(os.environ['HUYAN_OFFLINE_ROOT']).resolve()
        self.assertTrue(Path(self.world.objects.path).resolve().is_relative_to(root))
        self.assertIsNone(self.world.real)
        self.app = app_for(world=self.world)
        self.patches = [
            patch('asyncio.open_connection', new=AsyncMock(side_effect=AssertionError('禁止网络/真机连接'))),
            patch.object(RealLink, 'start', new=AsyncMock(side_effect=AssertionError('禁止启动真机链路'))),
            patch.object(self.app.state.resources.cameras, 'scan', side_effect=AssertionError('禁止扫描相机')),
            patch.object(self.app.state.resources.cameras, 'open', side_effect=AssertionError('禁止打开相机')),
        ]
        for item in self.patches:
            item.start()
        # ASGI in-process, no lifespan: no service lease, sockets, camera or startup.
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                      base_url='http://127.0.0.1:8710')

    async def asyncTearDown(self):
        try:
            # All links attached below are deterministic mocks; no transport starts.
            await self.world.stop()
            await self.client.aclose()
        finally:
            for item in reversed(self.patches):
                item.stop()
            print(json.dumps({'workflow': self._testMethodName, 'hardware_validated': False,
                              'passed_stages': sum(s['status'] == 'pass' for s in self.stages),
                              'total_stages': len(self.stages), 'stages': self.stages}, ensure_ascii=False), flush=True)

    @contextmanager
    def stage(self, name):
        started = time.perf_counter()
        row = {'name': name, 'status': 'fail'}
        self.stages.append(row)
        try:
            yield
            row['status'] = 'pass'
        finally:
            row['wall_ms'] = round((time.perf_counter() - started) * 1000, 2)

    async def post(self, path, body=None, ok=True):
        response = await self.client.post(path, json={} if body is None else body)
        value = response.json()
        self.assertTrue(response.is_success, (path, response.status_code, value))
        self.assertIs(value.get('ok'), ok, (path, value))
        return value

    async def state(self):
        response = await self.client.get('/api/state')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def assert_solution(self, result, target):
        # Actual active TCP/model and returned q, never a rounded UI pose or a
        # synthetic identity solver. Keep the production full-pose precision.
        self.assertEqual(result['reason_code'], 'solved')
        self.assertIs(result['orientation_constrained'], True)
        self.assertFalse(result['clipped'])
        self.assertIsInstance(result['solve_ms'], (int, float))
        self.assertTrue(math.isfinite(result['solve_ms']) and result['solve_ms'] >= 0)
        error = self.world.motion_model()._err(result['joints'], target)
        self.assertLess(max(abs(v) for v in error[:3]), .05, ('position mm', error))
        self.assertLess(max(abs(v) for v in error[3:]), .05, ('orientation deg', error))
        lower, upper = self.world.limits()
        self.assertTrue(all(a <= q <= b for a,q,b in zip(lower,result['joints'],upper)))

    async def until(self, predicate, timeout=12):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            # Actual production tick/trajectory/safety, accelerated offline only.
            self.world._tick(.02, time.monotonic())
            await asyncio.sleep(.005)
        self.fail('离线阶段超时；未自动重试运动')

    async def test_ik_preview_move_stop_tool_and_recovery(self):
        w = self.world
        before = list(w.current_joints())
        goal = before[:]
        goal[5] += 3
        with self.stage('真实FK与示教IK，只求解不移动'):
            fk = await self.client.post('/api/fk', json={'joints': goal})
            self.assertEqual(fk.status_code, 200)
            pose = fk.json()
            target = pose['xyz_mm'] + pose['rpy_deg']
            solved = await self.post('/api/ik', {'target': target, 'seed': before})
            self.assert_solution(solved, target)
            q = solved['joints']
            self.assertEqual(len(q), 6)
            self.assertEqual(w.current_joints(), before)
            self.assertIsNone(w.preview_q)
            self.assertFalse(w.executor.busy)
        with self.stage('preview真实路由不启动执行器'):
            preview = await self.post('/api/preview', {'joints': q,'plan_id':solved['plan_id']})
            snapshot = await self.state()
            self.assertEqual(snapshot['preview'], q)
            self.assertEqual(snapshot['joints']['q'], [round(v, 3) for v in before])
            self.assertIn(snapshot['preview_safety']['level'], ('green', 'yellow'))
            self.assertFalse(w.executor.busy)
        with self.stage('显式MoveJ，受理不等于到位，真实仿真轨迹完成'):
            accepted = await self.post('/api/movej', {'joints': q,'plan_id':preview['plan_id']})
            self.assertTrue(accepted['accepted'])
            await self.until(lambda: not w.executor.busy)
            self.assertEqual(w.executor.state, 'completed', w.executor.error)
            self.assertLess(max(abs(a-b) for a,b in zip(w.current_joints(), q)), .001)
        with self.stage('执行中停止并撤销剩余运动，不自行续跑'):
            other = q[:]
            other[5] += 20
            other_preview=await self.post('/api/preview', {'joints':other})
            await self.post('/api/movej', {'joints': other,'plan_id':other_preview['plan_id']})
            await self.until(lambda: w.sim.traj is not None)
            stopped = await self.post('/api/estop')
            self.assertTrue(stopped['settled'])
            held = list(w.current_joints())
            for _ in range(4):
                w._tick(.02, time.monotonic())
                await asyncio.sleep(.005)
            self.assertEqual(w.current_joints(), held)
            self.assertIsNone(w.sim.traj)
            self.assertFalse(w.executor.busy)
        with self.stage('切换实际工具档案使旧preview和已检查计划失效'):
            await self.post('/api/preview', {'joints': held})
            checked = await self.post('/api/plans/inspect', {'joints': held})
            revision = w.plan_context()
            entry = next(item for item in tool_library.entries() if item['type'] == 'parallel_gripper')
            await self.post('/api/tools/select', {'tool_id': entry['id']})
            self.assertNotEqual(w.plan_context(), revision)
            self.assertIsNone(w.preview_q)
            self.assertFalse(w.plans)
            refused = await self.post('/api/plans/execute', {'plan_id': checked['plan_id']}, ok=False)
            self.assertIn('变化', refused['why'])
            self.assertFalse(w.executor.busy)
        with self.stage('失败求解不发布可执行目标，显式重新求解恢复'):
            rejected = await self.post('/api/ik', {'target': [5000, 0, 5000, 0, 0, 0]}, ok=False)
            self.assertNotIn('joints', rejected)
            # This specific 5m/5m target violates the active robot/TCP's
            # conservative geometric position bound; it is not search failure.
            self.assertEqual(rejected['reason_code'], 'unreachable_position')
            self.assertEqual(rejected['proof'], 'root_tcp_triangle_sphere')
            self.assertGreater(rejected['observed_lower_bound_mm'],
                               rejected['upper_bound_mm'] + rejected['tolerance_mm'])
            self.assertTrue(math.isfinite(rejected['solve_ms']) and rejected['solve_ms'] >= 0)
            self.assertIsNone(w.preview_q)
            current_fk = await self.client.post('/api/fk', json={'joints': held})
            target = current_fk.json()['xyz_mm'] + current_fk.json()['rpy_deg']
            recovered = await self.post('/api/ik', {'target': target, 'seed': held})
            self.assert_solution(recovered, target)
            await self.post('/api/preview', {'joints': recovered['joints']})
            self.assertFalse(w.executor.busy, '重新求解/预览不可自行恢复运动')
            self.assertIsNone(w.real)

    def mock_link(self):
        link = RealLink('offline.invalid')  # constructor only; start() is forbidden.
        link.cmd_up = link.stream_up = True
        link.snap.update(joints=[0.]*6, fsm=33, enabled=1, moving=0, inpos=1, err_code=0,
                         stamp=time.monotonic(), connection_epoch=link.epoch)
        return link

    async def test_mock_link_disconnect_stop_unknown_and_manual_recovery(self):
        w, packets, publishers = self.world, [], []
        link = self.mock_link()
        w.settings['mode'], w.real = 'real', link
        async def acknowledged_only():
            packets.append('GrpStop')
            link.epoch += 1
            link.snap.update(stamp=time.monotonic(), connection_epoch=link.epoch)
            return Reply('GrpStop', True)
        link.estop = acknowledged_only
        with self.stage('mock回执ACK不等于新鲜停稳反馈，未知停止保持闭锁'):
            result = await self.post('/api/estop', ok=False)
            self.assertTrue(result['acknowledged'])
            self.assertFalse(result['settled'])
            count = len(packets)
            # One later pose packet permits read-only planning, but cannot
            # resolve the already timed-out stop transaction or reopen motion.
            link.frame_seq+=1
            link.snap.update(stamp=time.monotonic(),frame_seq=link.frame_seq,connection_epoch=link.epoch)
            blocked_preview=await self.post('/api/preview',{'joints':[0.]*6})
            self.assertIsInstance(blocked_preview['plan_id'],str)
            denied=await self.post('/api/movej', {'joints': [0.]*6,'plan_id':blocked_preview['plan_id']}, ok=False)
            self.assertNotIn('计划编号',denied.get('why',''))
            self.assertFalse(w.motion_ok())
            self.assertEqual(len(packets), count)
        async def confirmed():
            packets.append('GrpStop')
            link.epoch += 1
            async def publish():
                await asyncio.sleep(.03)
                link.frame_seq += 1
                link.snap.update(joints=[0.]*6, moving=0, inpos=1, stamp=time.monotonic(), connection_epoch=link.epoch)
            publishers.append(asyncio.create_task(publish()))
            return Reply('GrpStop', True)
        link.estop = confirmed
        with self.stage('mock人工重试停止后新帧确认，恢复不自动下发MoveJ'):
            recovered = await self.post('/api/estop')
            self.assertTrue(recovered['settled'])
            self.assertEqual(packets, ['GrpStop', 'GrpStop'])
            self.assertFalse(w.executor.busy)
        with self.stage('mock断流或旧帧拒绝新运动，重连不重放队列'):
            fresh_preview=await self.post('/api/preview',{'joints':[0.]*6})
            link.stream_up = False
            await self.post('/api/movej', {'joints': [0.]*6,'plan_id':fresh_preview['plan_id']}, ok=False)
            link.stream_up = True
            link.snap['stamp'] = time.monotonic() - 2
            await self.post('/api/movej', {'joints': [0.]*6,'plan_id':fresh_preview['plan_id']}, ok=False)
            queued = self.mock_link()
            task = asyncio.create_task(queued.ask('MoveRelJ', 0, 5, 1, .25, timeout=.01))
            reply = await task  # no transport loop: it must expire unsent.
            self.assertEqual(reply.outcome, 'expired')
            queued.invalidate_pending('离线会话变化')
            self.assertTrue(queued.q.empty())
            self.assertFalse(queued._pending)
            self.assertTrue(queued.command_trace)
            self.assertTrue(all(row['outcome'] == 'expired' for row in queued.command_trace))
        with self.stage('mock停止过程中命令会话更换，不向新连接发送旧停止'):
            fresh = self.mock_link()
            async def replacement():
                packets.append('old-session-stop')
                w.real = fresh
                link.epoch += 1
                return Reply('GrpStop', True)
            link.estop = replacement
            result = await self.post('/api/estop', ok=False)
            self.assertFalse(result['settled'])
            self.assertFalse(fresh.command_trace)
            self.assertFalse(w.motion_ok())
        await asyncio.gather(*publishers)
        # Dispose mock state without any reconnect/disconnect route or real transport.
        w.real = None
        w.settings['mode'] = 'sim'


if __name__ == '__main__':
    unittest.main(verbosity=2)
