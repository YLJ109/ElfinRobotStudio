"""Isolated MOVEJ axis contract: AST-only World/route, fake transport, no startup.

Run only through tools/run_offline_checks.py.
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import ast
import asyncio
import copy
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.schemas import Move
from backend.core.motion import MotionExecutor
from tools import hy

METHODS = {"_move_to", "_execute_move", "_real_rel_step", "move_joints"}
tree = ast.parse((ROOT / "backend/core/world.py").read_text(encoding="utf-8-sig"))
node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "World")
node.body = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in METHODS]
inspect_path = Mock(return_value={"risk_detected": False})
scope = dict(asyncio=asyncio, copy=copy, inspect_path=inspect_path)
exec(compile(ast.Module(body=[node], type_ignores=[]), "isolated-world", "exec"), scope)
World = scope["World"]


class AxisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        inspect_path.reset_mock(return_value=True, side_effect=True)
        inspect_path.return_value = {"risk_detected": False}
        self.w = w = World()
        w.MOTION = 1
        w.REL_MAX_CHUNKS, w.REL_CHUNK_DEG = 240, 10
        w.q = [0.] * 6
        w.settings = {"mode": "real"}
        w.current_joints = lambda: list(w.q)
        w.log = SimpleNamespace(add=Mock())
        w.executor = MotionExecutor(Mock())
        w.runner = SimpleNamespace(state="idle")
        w._need = Mock(return_value=True)
        w.precheck = Mock(return_value=None)
        w._cfg_rev, w.preview_q = 0, None
        w.objects = SimpleNamespace(list=lambda: [])
        w.limits = lambda: ([-180.] * 6, [180.] * 6)
        w.motion_model = lambda: None
        w.real = SimpleNamespace(epoch=1, frame_seq=1, snap={"stamp": 1})
        self.sent = []

        async def ask(name, *params):
            self.sent.append((name, params))
            _, axis, direction, distance = params
            w.q[axis] += float(distance) * (1 if direction else -1)
            w.real.frame_seq += 1
            return SimpleNamespace(ok=True)

        w.real.ask = ask
        w._wait_motion_idle = AsyncMock(return_value=True)
        w._real_movejto = AsyncMock(side_effect=AssertionError("whole-vector transport forbidden"))

    async def run_move(self, target, axes=None):
        reply = await self.w.move_joints(target, allowed_axes=axes)
        if reply.get("accepted"):
            await self.w.executor.task
        return reply

    async def test_j6_only_through_submission_and_chunking(self):
        target = [.04] * 5 + [22.]
        self.assertTrue((await self.run_move(target, [5]))["ok"])
        self.assertEqual(self.w.executor.state, "completed")
        self.assertEqual([p[1] for _, p in self.sent], [5, 5, 5])
        self.assertEqual(self.w.q, [0.] * 5 + [22.])

    async def test_excluded_target_difference_rejects_before_submission(self):
        result = await self.run_move([.051, 0, 0, 0, 0, 1], [5])
        self.assertFalse(result["ok"])
        self.assertFalse(result["accepted"])
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.w.executor.task)

    async def test_default_still_moves_all_six_axes(self):
        self.assertTrue((await self.run_move([1.] * 6))["ok"])
        self.assertEqual({p[1] for _, p in self.sent}, set(range(6)))
        self.assertEqual(self.w.executor.state, "completed")

    async def test_arbitrary_explicit_subset(self):
        await self.run_move([1., 0, 0, 2., 0, 0], [3, 0])
        self.assertEqual({p[1] for _, p in self.sent}, {0, 3})

    async def test_invalid_axis_masks_rejected_at_every_entry(self):
        for axes in ([], [5, 5], [-1], [6], [True], [False], [5.0], ["5"], "5", {5}):
            with self.subTest(axes=axes):
                self.assertFalse((await self.w.move_joints([0.] * 6, axes))["ok"])
                with self.assertRaises(ValueError):
                    self.w._move_to([0.] * 6, allowed_axes=axes)
                with self.assertRaises(ValueError):
                    await self.w._execute_move([0.] * 6, "test", 0, axes)
                with self.assertRaises(ValueError):
                    await self.w._real_rel_step([0.] * 6, allowed_axes=axes)
        self.assertEqual(self.sent, [])

    async def test_drift_during_preflight_fails_without_commands(self):
        def drift(*args):
            self.w.q[0] = .06
            return {"risk_detected": False}
        inspect_path.side_effect = drift
        await self.run_move([0.] * 5 + [1.], [5])
        self.assertEqual(self.w.executor.state, "failed")
        self.assertIn("漂移", self.w.executor.error)
        self.assertEqual(self.sent, [])

    async def test_drift_between_chunks_stops_remaining_commands(self):
        original = self.w.real.ask
        async def drift(*args):
            result = await original(*args)
            self.w.q[1] = .06
            return result
        self.w.real.ask = drift
        await self.run_move([0.] * 5 + [22.], [5])
        self.assertEqual(self.w.executor.state, "failed")
        self.assertIn("漂移", self.w.executor.error)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1][1], 5)

    async def test_drift_after_final_chunk_cannot_report_success(self):
        original = self.w.real.ask
        async def drift(*args):
            result = await original(*args)
            self.w.q[2] = -.06
            return result
        self.w.real.ask = drift
        await self.run_move([0.] * 5 + [1.], [5])
        self.assertEqual(self.w.executor.state, "failed")
        self.assertEqual(len(self.sent), 1)

    async def test_drift_while_waiting_before_first_chunk(self):
        async def idle(**kwargs):
            self.w.q[0] = .06
            return True
        self.w._wait_motion_idle.side_effect = idle
        await self.run_move([0.] * 5 + [1.], [5])
        self.assertEqual(self.w.executor.state, "failed")
        self.assertEqual(self.sent, [])

    async def test_explicit_mask_never_uses_whole_vector_transport(self):
        self.w.settings["real_move_cmd"] = "movejto"
        await self.run_move([0.] * 5 + [1.], [5])
        self.assertEqual(self.w.executor.state, "completed")
        self.w._real_movejto.assert_not_awaited()

    async def test_sim_excluded_axes_keep_current_position_within_tolerance(self):
        self.w.settings["mode"] = "sim"
        self.w.sim = SimpleNamespace(traj=None, move_j=Mock())
        await self.run_move([.04] * 5 + [1.], [5])
        self.w.sim.move_j.assert_called_once_with([0.] * 5 + [1.])
        self.assertEqual(self.sent, [])

    async def test_safety_permission_busy_and_revision_checks_remain(self):
        target = [0.] * 5 + [1.]
        self.w._need.return_value = False
        self.w.last_deny = "denied"
        self.assertFalse((await self.run_move(target, [5]))["ok"])
        self.w._need.return_value = True
        self.w.runner.state = "running"
        self.assertFalse((await self.run_move(target, [5]))["ok"])
        self.w.runner.state = "idle"
        self.w.precheck.return_value = "红色安全预检"
        self.assertFalse((await self.run_move(target, [5]))["ok"])
        self.w.precheck.return_value = None
        inspect_path.return_value = {"risk_detected": True, "issues": [{"sample": 0}]}
        await self.run_move(target, [5])
        self.assertEqual(self.w.executor.state, "failed")
        def revision(*args):
            self.w._cfg_rev += 1
            return {"risk_detected": False}
        inspect_path.side_effect = revision
        await self.run_move(target, [5])
        self.assertEqual(self.w.executor.state, "failed")
        self.assertEqual(self.sent, [])

    async def test_cancelled_epoch_never_sends(self):
        self.w.executor.revoke("offline cancel")
        with self.assertRaises(asyncio.CancelledError):
            await self.w._execute_move([0.] * 5 + [1.], "test", 0, [5])
        self.assertEqual(self.sent, [])


class RouteAndGuardTests(unittest.TestCase):
    def setUp(self):
        # Windows asyncio uses socketpair internally. Block outbound client
        # creation without intercepting its event-loop wakeup connection.
        network = patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def test_actual_http_route_validation_and_plan_required(self):
        app = FastAPI()
        world = SimpleNamespace(execute_motion_target=AsyncMock(return_value={"ok": True}))
        main = ast.parse((ROOT / "backend/main.py").read_text(encoding="utf-8-sig"))
        route = next(n for n in ast.walk(main) if isinstance(n, ast.AsyncFunctionDef) and n.name == "movej")
        exec(compile(ast.Module(body=[route], type_ignores=[]), "isolated-route", "exec"),
             dict(app=app, world=world, Move=Move))
        client = TestClient(app)
        q = [0.] * 6
        for axes in ([], [5, 5], [-1], [6], [True], [False], [5.0], ["5"], "5"):
            self.assertEqual(client.post("/api/movej", json={"joints": q, "allowed_axes": axes}).status_code, 422)
        world.execute_motion_target.assert_not_awaited()
        self.assertEqual(client.post("/api/movej", json={"joints": q, "allowed_axes": [5]}).status_code, 200)
        self.assertFalse(client.post('/api/movej',json={'joints':q,'allowed_axes':[5]}).json()['ok'])
        world.execute_motion_target.assert_not_awaited()
        self.assertTrue(client.post('/api/movej',json={'joints':q,'allowed_axes':[5],'plan_id':'confirmed'}).json()['ok'])
        world.execute_motion_target.assert_awaited_once_with(q,'confirmed',allowed_axes=[5])
        world.execute_motion_target.reset_mock()
        for extra in ({}, {"allowed_axes": None}):
            self.assertFalse(client.post('/api/movej',json={'joints':q,**extra}).json()['ok'])
        world.execute_motion_target.assert_not_awaited()

    def test_guard_uses_standard_movej_and_only_j6(self):
        q = [10., 20., 30., 40., 50., 60.]
        state = {"mode": "real", "real": {"override": .05, "joints": q}}
        def response(path,payload,base):
            if path=='/api/preview':return {'ok':True,'plan_id':'j6-plan','preview':payload['joints'],'target_plan':{'allowed_axes':[5]}}
            return {'ok':True}
        with patch.object(hy, "get", return_value=state), patch.object(hy, "post", side_effect=response) as post:
            for delta in (-2, 0, 2):
                self.assertTrue(hy.Guard().move_j6(delta)["ok"])
                post.assert_called_with("/api/movej", {"joints": q[:5] + [60. + delta], "allowed_axes": [5],'plan_id':'j6-plan'}, hy.BASE)
            self.assertEqual(q[5], 60.)

    def test_guard_rejects_bad_delta_without_network(self):
        with patch.object(hy, "get") as get, patch.object(hy, "post") as post:
            for delta in (-2.001, 2.001, True, False, "1", None, float("nan"), float("inf")):
                with self.assertRaises(hy.GuardError):
                    hy.Guard().move_j6(delta)
            get.assert_not_called()
            post.assert_not_called()

    def test_guard_unconfirmed_or_wrong_axis_preview_never_sends_movement(self):
        state={'mode':'real','real':{'override':.01,'joints':[0.]*6}}
        for response in ({'ok':False},{'ok':True},
                         {'ok':True,'plan_id':'x','preview':[0.]*5+[1.],'target_plan':{'allowed_axes':[0,5]}},
                         {'ok':True,'plan_id':'x','preview':[9.]*6,'target_plan':{'allowed_axes':[5]}}):
            with patch.object(hy,'get',return_value=state),patch.object(hy,'post',return_value=response) as post:
                with self.assertRaises(hy.GuardError):hy.Guard().move_j6(1)
                post.assert_called_once_with('/api/preview',{'joints':[0.]*5+[1.],'allowed_axes':[5]},hy.BASE)

    def test_guard_caps_actual_override_even_with_relaxed_constructor(self):
        with patch.object(hy, "get") as get, patch.object(hy, "post") as post:
            for ov in (.050001, .5, -.01, None, True, "0.01", float("nan"), float("inf")):
                get.return_value = {"mode": "real", "real": {"override": ov, "joints": [0.] * 6}}
                with self.assertRaises(hy.GuardError):
                    hy.Guard(max_override=1).move_j6(1)
            get.return_value = {"mode": "real", "real": {"override": .04, "joints": [0.] * 6}}
            with self.assertRaises(hy.GuardError):
                hy.Guard(max_override=.03).move_j6(1)
            post.assert_not_called()

    def test_guard_requires_real_valid_joint_feedback(self):
        with patch.object(hy, "get") as get, patch.object(hy, "post") as post:
            for q in (None, [], [0.] * 5, [True] * 6, [float("nan")] * 6, ["0"] * 6):
                get.return_value = {"mode": "real", "real": {"override": .01, "joints": q}}
                with self.assertRaises(hy.GuardError):
                    hy.Guard().move_j6(1)
            get.return_value = {"mode": "sim", "real": {"override": .01, "joints": [0.] * 6}}
            with self.assertRaises(hy.GuardError):
                hy.Guard().move_j6(1)
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
