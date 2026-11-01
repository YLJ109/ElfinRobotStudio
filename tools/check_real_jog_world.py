"""Offline World/HTTP integration checks; no live World import or constructor.

Run: backend/.venv/Scripts/python.exe -B tools/check_real_jog_world.py
Extracts the actual World class and HTTP handlers from their AST. All mutable
stores, transport and camera frames are fakes. Pure safety/kinematics are real.
No backend startup, persisted workstation data, services or hardware are used.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import ast
import asyncio
import copy
import json
import math
import secrets
import socket
import threading
import time
import unittest
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace, MappingProxyType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
import httpx
from backend.core import controls
from backend.domain import suction
from backend.core.kinematics import RobotModel, capsule_distance
from backend.core.motion import MotionExecutor
from backend.core.objects import radius as object_radius, capsule_object_gap
from backend.core.planning import digest
from backend.core.program import Runner
from backend.core.real_jog_follow import RealJogFollow, MAX_STEP_DEG, MAX_OVERRIDE
from backend.core.real_servo import real_follow_readiness
from backend.core.robot_sim import SimRobot
from backend.core.safety import (ALARM, GREEN, RED, evaluate, tool_capsules, object_capsules,
                                 contact_profile, recede_verdict)


def forbidden(*args, **kwargs):
    raise AssertionError("Live World, persistence and network are forbidden")


CONFIG = SimpleNamespace(
    LIMIT_SOURCES={"controller": ("fake", [(-180., 180.)] * 6)},
    speed_key=lambda mode: "real_speed_pct" if mode == "real" else "sim_speed_pct",
    save_settings=forbidden, no_real=lambda: True)
WORLD_PATH = ROOT / "backend/core/world.py"
WORLD_AST = ast.parse(WORLD_PATH.read_text(encoding="utf-8-sig"))
WORLD_NODE = next(n for n in WORLD_AST.body if isinstance(n, ast.ClassDef) and n.name == "World")
WORLD_GLOBALS = {**globals(), "config": CONFIG}
KEYS = [n for n in WORLD_AST.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in ("FAST_KEYS", "TELE_KEYS") for t in n.targets)]
exec(compile(ast.Module(body=[*KEYS, WORLD_NODE], type_ignores=[]), str(WORLD_PATH), "exec"), WORLD_GLOBALS)
World = WORLD_GLOBALS["World"]
World.__init__ = forbidden
World.start = forbidden
World._boot_real = forbidden


class Reply:
    def __init__(self, name, ok=True):
        self.name, self.ok = name, ok
        self.timeout, self.outcome = False, "acknowledged" if ok else "rejected"
    def as_dict(self):
        return {"name": self.name, "ok": self.ok}


class FakeLink:
    def __init__(self):
        self.epoch, self.frame_seq = 4, 1
        self.cmd_up = self.stream_up = True
        self.jogging = self._jog_intent = None
        self.host, self.last_command = "fake-never-connected", None
        self.snap = dict(joints=[0.] * 6, stamp=time.monotonic(), connection_epoch=4,
                         fsm=33, enabled=1, moving=0, inpos=1, err_code=0,
                         paused=0, free_drive=0, override=.05)
        self.calls, self.hook = [], None
        self.confirm_stop, self.stop_ok = True, True
        self.feedback_tasks = []
    def publish(self, **values):
        self.frame_seq += 1
        self.snap.update(stamp=time.monotonic(), connection_epoch=self.epoch, **values)
    def state_valid(self):
        return (self.stream_up and self.snap["connection_epoch"] == self.epoch
                and 0 <= time.monotonic()-self.snap["stamp"] <= .5)
    async def ask(self, name, *params, **options):
        self.calls.append((name, params, options))
        if self.hook:
            await self.hook(name)
        return Reply(name, self.stop_ok if name == "GrpStop" else True)
    async def estop(self):
        self.invalidate_pending("stop")
        rep = await self.ask("GrpStop", 0)
        if rep.ok and self.confirm_stop:
            # 模拟持续周期反馈。只发一帧会在宿主机调度抖动时全部落在 ACK
            # 屏障之前，正确的生产代码必须拒绝那种情况，不能靠放宽屏障修测试。
            stop_epoch = self.epoch
            async def feedback():
                for _ in range(100):
                    await asyncio.sleep(.01)
                    if self.epoch != stop_epoch or not self.stream_up:
                        return
                    self.publish(moving=0, inpos=1)
            self.feedback_tasks.append(asyncio.create_task(feedback()))
        return rep
    def invalidate_pending(self, reason, advance=True, **kwargs):
        if advance:
            self.epoch += 1
    async def stop(self):
        self.cmd_up = self.stream_up = False
        await self.close_feedback()
    async def close_feedback(self):
        for task in self.feedback_tasks:
            task.cancel()
        await asyncio.gather(*self.feedback_tasks, return_exceptions=True)
    def controller_info(self):
        return {"status": "fake", "motion_qualified": False}
    def lag_ms(self):
        return (time.monotonic()-self.snap["stamp"])*1000


class FakeModel:
    variant, flange = "elfin5", "tool"
    names = [f"J{i}" for i in range(6)]
    lower, upper, vmax = [-180.] * 6, [180.] * 6, [90.] * 6
    tcp_translation_mm = tcp_rpy_deg = [0.] * 3
    def __init__(self):
        self.samples, self.pose_hook = [], None
    def pose_vec(self, q):
        self.samples.append(list(q))
        return self.pose_hook(q) if self.pose_hook else [q[0], 0., 500.+q[1], 20., 30., 40.]
    def flange_pose(self, q):
        return self.pose_vec(q)
    def capsules(self, q, **kwargs):
        p = self.pose_vec(q)[:3]
        return [{"link": self.flange, "a": p, "b": p, "r_mm": 1.}]
    def ik(self, seed, pose, lower, upper, **kwargs):
        q = list(seed)
        q[0] = pose[0]
        return q, 0., 0., True


class FakeTools:
    def __init__(self):
        self.mounted = {"state": "none_confirmed", "tool_id": "none", "revision": 1}
        self.tool = {"id": "none", "type": "none", "tcp": {"status": "verified"},
                     "payload": {"status": "verified"}}
    def get(self, tool_id):
        return copy.deepcopy(self.tool) if tool_id else None
    def snapshot(self):
        return {"mounted": copy.deepcopy(self.mounted), "tool": self.get(self.mounted["tool_id"])}


class FakeObjects:
    def __init__(self):
        self.items = []
    def list(self):
        return copy.deepcopy(self.items)
    def any_active(self):
        return False
    def with_vel(self):
        return self.list()


def fixture():
    w = World.__new__(World)
    w._settings_store = CONFIG
    w.settings = dict(mode="real", limit_source="controller", custom_limits=None,
        real_speed_pct=5., sim_speed_pct=10., vel_source="urdf", real_move_cmd="relj",
        safety_yellow_pct=90., safety_red_pct=95., safety_alarm_pct=100.,
        ground_enabled=True, self_collision=True,
        fence={"enabled": True, "x": [-1000., 1000.], "y": [-1000., 1000.], "z": [0., 1500.]})
    w.model = FakeModel()
    w.motion_model = lambda: w.model
    w.real, w.tools, w.objects = FakeLink(), FakeTools(), FakeObjects()
    w.points = SimpleNamespace(list=lambda: [])
    w.calibrations = SimpleNamespace(items={"version": 1})
    w.log = SimpleNamespace(add=lambda *a, **k: None, tail=lambda n: [], seq=0)
    w.executor, w.runner, w.sim = MotionExecutor(w.log.add), Runner(), SimRobot(w.model, w.model.vmax)
    w.queue_state = "idle"
    w.robot_play = None
    w.motion_session = None
    w._cfg_rev = w._real_jog_generation = w._hand_generation = 0
    w._real_jog_checked = None
    w._real_jog_link, w._real_jog_connection = w.real, w.real.epoch
    w._real_jog_stop_confirmed, w._real_jog_stop_reply = False, None
    w._ready_task = w._speed_task = w._task = w._boot_connect = None
    w.free = {"on": False}
    w.safety = {"level": GREEN}
    w.ready_state, w.speed_ack, w.last_deny = {"running": False}, {}, ""
    w.hand_follow = SimpleNamespace(active=False, snapshot=lambda: {"sim_only": True}, stop=lambda r: {})
    w._hand_centering = w._hand_moving = False
    w._hand_camera_reader = lambda: {"active": {"state": "streaming", "session": "fake-camera",
        "last_frame_at": time.time(), "images": {"color": {"width": 640, "height": 480, "seq": 1}}}}
    w.real_jog_follow = RealJogFollow(w._real_jog_state, w._real_jog_solve, w._real_jog_check_step,
                                     w._real_jog_move, w._real_jog_stop_motion)
    w.plans, w.preview_q = {}, None
    w._save_sim_pose = lambda: None
    w._armed, w._challenge, w._ack_until = False, None, 0.
    return w


def config_payload():
    return dict(session="fake-camera", aspect=640/480, depth=True, rotation=False,
                depth_mode="gesture", direction=0,
                enclosure={"min": [-100., -100., 400.], "max": [100., 100., 600.]})


class WorldIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_ik_keeps_slot_until_actual_worker_exit(self):
        entered, release = threading.Event(), threading.Event()
        options=[]
        def slow(seed, pose, lower, upper, **kw):
            options.append(kw)
            entered.set()
            release.wait(1.)
            return list(seed),0.,0.,True
        w=self.w
        with patch.object(FakeModel,'ik',side_effect=slow):
            task=asyncio.create_task(w._real_jog_solve([0.]*6,[1.,0.,500.,20.,30.,40.]))
            try:
                for _ in range(50):
                    if entered.is_set():break
                    await asyncio.sleep(.002)
                self.assertTrue(entered.is_set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertTrue(callable(options[0].get('cancelled')))
                self.assertTrue(options[0]['cancelled']())
                self.assertLessEqual(options[0]['deadline'],time.monotonic()+.12)
                w.real.publish()
                reply=await w._real_jog_solve([0.]*6,[2.,0.,500.,20.,30.,40.])
                self.assertFalse(reply['ok'])
                self.assertEqual(len(options),1,'取消await不应创建并行IK线程')
                self.assertEqual(w.real.calls,[])
            finally:
                release.set()
                await asyncio.gather(task,return_exceptions=True)
                pending=getattr(w,'_real_jog_ik_task',None)
                if pending:await asyncio.gather(pending,return_exceptions=True)

    async def asyncSetUp(self):
        self.w = fixture()
        self.no_connect = patch("socket.socket.connect", forbidden)
        self.no_open = patch("asyncio.open_connection", forbidden)
        self.no_connect.start()
        self.no_open.start()
    async def asyncTearDown(self):
        if self.w.real_jog_follow.active:
            await self.w.real_jog_follow.stop()
        tasks = (self.w.real_jog_follow._run_task, self.w.real_jog_follow._stop_task)
        await asyncio.gather(*(t for t in tasks if t), return_exceptions=True)
        if isinstance(self.w.real, FakeLink):
            await self.w.real.close_feedback()
        self.no_open.stop()
        self.no_connect.stop()
    async def configure(self, **changes):
        return await self.w.real_hand_follow_action("configure", {**config_payload(), **changes})
    async def activate(self):
        self.assertTrue((await self.configure())["ok"])
        d = self.w.real_jog_follow
        result = await self.w.real_hand_follow_action("start", dict(token=d.token, session=d.session, confirm_real=True))
        self.assertTrue(result["ok"], result)
    async def test_state_is_raw_real_fk_and_nonrecursive(self):
        w = self.w
        with patch.object(w.real_jog_follow, "snapshot", forbidden):
            state = w._real_jog_state()
        self.assertEqual(state["connection"], (id(w.real), w.real.epoch))
        self.assertEqual(state["stamp"], w.real.snap["stamp"])
        self.assertEqual(state["pose"], w.model.pose_vec(w.real.snap["joints"]))
        self.assertTrue(w._real_jog_gate(state))
        w.real.snap["stamp"] -= .151
        self.assertFalse(w._real_jog_gate(w._real_jog_state()))
    async def test_sim_and_disconnected_reads_skip_all_geometry_and_stores(self):
        for mode, link in (("sim", self.w.real), ("real", None)):
            self.w.settings["mode"], self.w.real = mode, link
            with patch.object(self.w, "motion_model", forbidden), patch.object(self.w, "plan_context", forbidden):
                self.assertIsNone(self.w._real_jog_state()["connection"])
    async def test_real_model_cache_changes_with_tcp_revision_model_and_settings(self):
        w = self.w
        factory = unittest.mock.Mock(side_effect=lambda *a, **kw: FakeModel())
        with patch.dict(WORLD_GLOBALS, RobotModel=factory):
            del w._cfg_rev  # Real-mode construction creates HandFollow before this field.
            model = World.motion_model(w)
            w._cfg_rev = 0
            self.assertIs(World.motion_model(w), model)
            self.assertEqual(factory.call_count, 1)
            w.tools.tool["tcp"]["translation_mm"] = [0., 0., 10.]
            self.assertIsNot(World.motion_model(w), model)
            w.tools.mounted["revision"] += 1
            World.motion_model(w)
            w.model.variant = "elfin3"
            World.motion_model(w)
            w._cfg_rev += 1
            World.motion_model(w)
            self.assertEqual(factory.call_count, 5)
    async def test_verified_none_only(self):
        for state, tool_id, kind, tcp, payload, expected in (
            ("none_confirmed", "none", "none", "verified", "verified", True),
            ("manual_confirmed", "none", "none", "verified", "verified", True),
            ("controller_config_only", "none", "none", "verified", "verified", False),
            ("none_confirmed", "none", "none", "unvalidated", "verified", False),
            ("none_confirmed", "none", "none", "verified", "unknown", False),
            ("manual_confirmed", "gripper", "parallel_gripper", "verified", "verified", False)):
            with self.subTest(state=state, kind=kind, tcp=tcp, payload=payload):
                self.w.tools.mounted.update(state=state, tool_id=tool_id)
                self.w.tools.tool.update(type=kind, tcp={"status": tcp}, payload={"status": payload})
                self.assertIs(self.w._real_jog_state()["confirmed_tool"], expected)
    async def test_configure_start_wait_without_motion_or_enable(self):
        self.assertTrue((await self.configure(depth_range=[10, 20]))["ok"])
        d = self.w.real_jog_follow
        token = d.token
        self.assertFalse((await self.w.real_hand_follow_action("start", dict(token=token, session=d.session)))["ok"])
        await self.activate()
        self.assertTrue(d.active)
        self.assertTrue(d.waiting)
        self.assertEqual(d._orientation, [20., 30., 40.])
        self.assertEqual(self.w.real.calls, [])
    async def test_configuration_fence_floor_rotation_and_disabled_checks(self):
        for changes in (
            {"enclosure": {"min": [-1001., 0., 400.], "max": [1., 10., 500.]}},
            {"enclosure": {"min": [0., 0., -1.], "max": [10., 10., 500.]}},
            {"rotation": True},
            {"direction": 90, "enclosure": {"min": [990., -100., 400.], "max": [999., 100., 500.]}}):
            self.assertFalse((await self.configure(**changes))["ok"], changes)
        for key in ("fence", "ground"):
            if key == "fence": self.w.settings["fence"]["enabled"] = False
            else: self.w.settings["ground_enabled"] = False
            self.assertFalse((await self.configure())["ok"])
            self.w.settings["fence"]["enabled"] = self.w.settings["ground_enabled"] = True
        self.assertEqual(self.w.real.calls, [])
    async def test_start_rejects_outside_box_not_ready_override_and_tool(self):
        for name, value in (("fsm", 24), ("override", .051), ("joints", [200.] + [0.] * 5), ("stamp", time.monotonic()-1)):
            old = self.w.real.snap[name]
            self.w.real.snap[name] = value
            result = await self.configure()
            d = self.w.real_jog_follow
            result = await self.w.real_hand_follow_action("start", dict(token=d.token, session=d.session, confirm_real=True))
            self.assertFalse(result["ok"], name)
            self.w.real.snap[name] = old
        self.assertFalse((await self.configure(rotation=True))["ok"])
        self.assertEqual(self.w.real.calls, [])
    async def test_chord_checks_samples_limits_objects_floor_and_red(self):
        w = self.w
        start, end = [0.] * 6, [.25] + [0.] * 5
        box = config_payload()["enclosure"]
        self.assertTrue(await w._real_jog_check_step(start, end, box))
        self.assertGreaterEqual(len({q[0] for q in w.model.samples}), 9)
        self.assertFalse(await w._real_jog_check_step(start, [.251] + [0.] * 5, box))
        self.assertFalse(await w._real_jog_check_step(start, [.1, .1, 0., 0., 0., 0.], box))
        w.model.pose_hook = lambda q: [q[0], 0., 500.+200*math.sin(math.pi*q[0]/.25), 20., 30., 40.]
        self.assertFalse(await w._real_jog_check_step(start, end, box))
        w.model.pose_hook = lambda q: [q[0], 0., -1., 20., 30., 40.]
        self.assertFalse(await w._real_jog_check_step(start, end, {"min": [-100., -100., -100.], "max": [100., 100., 600.]}))
        w.model.pose_hook = None
        w.objects.items = [{"name": "obstacle", "kind": "sphere", "xyz": [0., 0., 500.], "size": [10.]}]
        self.assertFalse(await w._real_jog_check_step(start, end, box))
        w.objects.items = []
        w.settings["fence"]["x"][1] = 5.
        self.assertFalse(await w._real_jog_check_step(start, end, box))  # >=95%, before contact
    async def test_real_model_path_uses_existing_evaluator(self):
        w = self.w
        model = RobotModel("elfin5")
        w.motion_model = lambda: model
        q = [0., 25., -55., 0., 40., 0.]
        w.real.publish(joints=q)
        p = model.pose_vec(q)
        box = {"min": [v-20 for v in p[:3]], "max": [v+20 for v in p[:3]]}
        end = [q[0]+.1, *q[1:]]
        expected = evaluate(model, q, w.settings, *w.limits())
        with patch.dict(WORLD_GLOBALS, evaluate=unittest.mock.Mock(wraps=evaluate)):
            actual = await w._real_jog_check_step(q, end, box)
            self.assertGreater(WORLD_GLOBALS["evaluate"].call_count, 0)
        self.assertEqual(actual, expected["level"] not in (RED, ALARM))
    async def test_worker_context_and_connection_changes_reject_results(self):
        original = asyncio.to_thread
        for operation in ("solve", "check"):
            for mutation in ("link", "epoch", "settings", "tool", "objects", "q"):
                w = fixture()
                async def changed(fn, *a, **kw):
                    value = await original(fn, *a, **kw)
                    if mutation == "link": w.real = FakeLink()
                    elif mutation == "epoch": w.real.epoch += 1
                    elif mutation == "settings": w._cfg_rev += 1
                    elif mutation == "tool": w.tools.mounted["revision"] += 1
                    elif mutation == "objects": w.objects.items.append({"id": "changed"})
                    else: w.real.publish(joints=[.1] + [0.] * 5)
                    return value
                with self.subTest(operation=operation, mutation=mutation), patch("asyncio.to_thread", changed):
                    if operation == "solve":
                        value = (await w._real_jog_solve([0.] * 6, [1., 0., 500., 20., 30., 40.]))["ok"]
                        if mutation == "q": continue  # driver checks the measured start after solve
                    else:
                        value = await w._real_jog_check_step([0.] * 6, [.1] + [0.] * 5, config_payload()["enclosure"])
                    self.assertFalse(value)
                    self.assertEqual(w.real.calls, [])
    async def test_move_requires_one_use_single_axis_proof_and_returns_only_ack(self):
        w = self.w
        await self.activate()
        end = [.25] + [0.] * 5
        self.assertFalse(await w._real_jog_move(0, .25, end))
        self.assertTrue(await w._real_jog_check_step([0.] * 6, end, config_payload()["enclosure"]))
        self.assertTrue(await w._real_jog_move(0, .25, end))
        self.assertEqual(w.real.calls, [("MoveRelJ", (0, 0, 1, .25), {"timeout": .25})])
        self.assertEqual(w.real.snap["joints"], [0.] * 6)
        self.assertFalse(await w._real_jog_move(0, .25, end))
    async def test_move_rejects_context_change_after_check_or_ack(self):
        w = self.w
        await self.activate()
        end = [.1] + [0.] * 5
        self.assertTrue(await w._real_jog_check_step([0.] * 6, end, config_payload()["enclosure"]))
        w._cfg_rev += 1
        self.assertFalse(await w._real_jog_move(0, .1, end))
        self.assertEqual(w.real.calls, [])
        self.assertTrue(await w._real_jog_check_step([0.] * 6, end, config_payload()["enclosure"]))
        async def change(_): w._cfg_rev += 1
        w.real.hook = change
        self.assertFalse(await w._real_jog_move(0, .1, end))
        w.real.hook = None
    async def test_interlocks_revoke_follow_before_ordinary_writes(self):
        for name in ("need", "free", "speed", "tool", "clear", "raw", "enable", "ready"):
            w = self.w = fixture()
            await self.activate()
            if name == "need": result = {"ok": w._need(w.MOTION, "ordinary motion")}
            elif name == "free": result = await w.free_drive(True)
            elif name == "speed":
                old = dict(w.settings)
                w.set_speed(50.)
                self.assertEqual(w.settings, old)
                result = w.speed_ack
            elif name == "tool": result = w.select_tool("gripper")
            elif name == "clear": result = w.clear_tool()
            elif name == "raw": result = await w.ask_real("MoveRelJ", 0, 0, 1, 1.)
            elif name == "enable": result = await w.power("enable")
            else: result = await w.ready()
            self.assertFalse(result["ok"], name)
            self.assertFalse(w.real_jog_follow.active)
            await w.real_jog_follow.stop()
            self.assertTrue(all(call[0] == "GrpStop" for call in w.real.calls), name)
    async def test_stop_ack_needs_new_capture_and_can_retry_owned_epoch(self):
        w = self.w
        w.real.confirm_stop = False
        # An idle pre-ACK frame never proves stopping, even if its age is fresh.
        self.assertFalse(await w._real_jog_stop_motion("fake stop"))
        self.assertEqual(w._real_jog_connection, w.real.epoch)
        w.real.confirm_stop = True
        self.assertTrue(await w._real_jog_stop_motion("manual retry"))
        self.assertTrue(w._real_jog_stop_confirmed)
    async def test_stop_never_targets_replaced_or_reconnected_link(self):
        old = self.w.real
        self.w.real = FakeLink()
        self.assertFalse(await self.w._real_jog_stop_motion("replaced"))
        self.assertEqual(old.calls, [])
        self.assertEqual(self.w.real.calls, [])
        self.w.real = old
        old.epoch += 1
        self.assertFalse(await self.w._real_jog_stop_motion("reconnected"))
        self.assertEqual(old.calls, [])

    async def test_state_epoch_loss_still_stops_same_bound_command_session(self):
        w = self.w
        w.real.command_session = 7
        w._real_jog_command_session = 7
        w.real.epoch += 1
        original = w.real.estop
        async def bound_stop(*, command_session):
            self.assertEqual(command_session, 7)
            return await original()
        w.real.estop = bound_stop
        self.assertTrue(await w._real_jog_stop_motion("仅状态代次变化"))
        self.assertEqual([row[0] for row in w.real.calls], ["GrpStop"])

    async def test_reconnected_command_session_never_receives_old_follow_stop(self):
        w = self.w
        w.real.command_session = 8
        w._real_jog_command_session = 7
        self.assertFalse(await w._real_jog_stop_motion("命令连接已变化"))
        self.assertEqual(w.real.calls, [])
    async def test_stop_replaced_during_ack_cannot_confirm_or_stop_new_link(self):
        w, old = self.w, self.w.real
        new = FakeLink()
        async def replace(_): w.real = new
        old.hook = replace
        self.assertFalse(await w._real_jog_stop_motion("changed during ACK"))
        self.assertEqual(new.calls, [])
        self.assertFalse(w._real_jog_stop_confirmed)
    async def test_rejected_stop_ack_can_be_manually_retried_on_same_link(self):
        w = self.w
        w.real.stop_ok = False
        self.assertFalse(await w._real_jog_stop_motion("rejected"))
        w.real.stop_ok = True
        self.assertTrue(await w._real_jog_stop_motion("retry"))
    async def test_unknown_stop_keeps_ordinary_motion_locked(self):
        w = self.w
        w.real_jog_follow._stop_unknown = True
        w.real = FakeLink()  # owned original link is still bound
        self.assertFalse(w._need(w.MOTION, "move while stop unknown"))
        await w.real_jog_follow.stop()
        self.assertTrue(w.real_jog_follow.busy)
        self.assertEqual(w.real.calls, [])
    async def test_stop_execution_and_close_stop_once_without_recursion(self):
        for close in (False, True):
            w = self.w = fixture()
            await self.activate()
            if close: await w.stop()
            else: self.assertTrue((await w.stop_execution("test stop"))["settled"])
            self.assertEqual([c[0] for c in w.real.calls], ["GrpStop"])
            self.assertFalse(w.real_jog_follow.busy)
    async def test_http_handlers_order_status_and_fake_qualification(self):
        w = self.w
        source = ast.parse((ROOT / "backend/main.py").read_text(encoding="utf-8-sig"))
        wanted = {"real_hand_follow_status", "real_hand_follow", "_verified_hand_sample"}
        handlers = [n for n in ast.walk(source) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in wanted]
        self.assertEqual([n.name for n in handlers], ["_verified_hand_sample", "real_hand_follow_status", "real_hand_follow"])
        app = FastAPI()
        namespace = {"app": app, "world": w, "Body": Body, "JSONResponse": JSONResponse,
                     "real_follow_readiness": real_follow_readiness, "_hand_camera_state": w._hand_camera_reader}
        exec(compile(ast.Module(body=handlers, type_ignores=[]), "main_http_handlers", "exec"), namespace)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake") as c:
            self.assertFalse((await c.post("/api/real/hand-follow/start", json={"qualified": True})).json()["ok"])
            self.assertEqual(w.real.calls, [])
            await self.activate()
            data = (await c.get("/api/real/hand-follow/status?qualified=true")).json()
            # This fixture deliberately exercises the historical finite driver;
            # the HTTP contract now advertises the new online driver by default.
            self.assertEqual(data["driver"], "StartServo/PushServoJ")
            self.assertTrue(data["motion_enabled"])
            self.assertTrue(data["state"]["active"])
            self.assertFalse(data["servo"]["motion_enabled"])
            self.assertFalse(data["servo"]["available"])
            self.assertEqual((await c.post("/api/real/hand-follow/status", json={})).status_code, 405)
            self.assertTrue((await c.post("/api/real/hand-follow/stop", json={})).json()["ok"])
            self.assertEqual([x[0] for x in w.real.calls], ["GrpStop"])
    async def test_world_snapshot_keeps_sim_and_real_follow_separate(self):
        w = self.w
        w.session_id, w._slow_at = "fake", time.monotonic()
        w.preview_q = w.resume = w.stop_result = None
        w._target_evidence = w._preview_target = None
        w.obj_hits, w.sim_state = [], {}
        w.ctrl, w.preview_safety = lambda: {}, lambda: None
        w.build_telemetry = forbidden
        snap = w.snapshot()
        self.assertEqual(snap["hand_follow"], {"sim_only": True})
        self.assertIsNone(snap['target_evidence'])
        self.assertIsNone(snap['target_plan_id'])
        self.assertIn("real_hand_follow", snap)
        self.assertEqual(snap["real_hand_follow"]["strategy"], "finite_relj_compat")


if __name__ == "__main__":
    unittest.main(verbosity=2)
