"""Offline checks for the real MoveLTo path: qualification gate + dispatch.

No live controller, no World constructor. The gate is a pure function; the
dispatch test builds a bare World instance and swaps in a fake link, so every
rejection and the happy path are exercised without hardware.

Run: backend/.venv/Scripts/python.exe -B tools/check_real_movel.py
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import real_movel_qualification as gate_mod
from backend.core.kinematics import RobotModel
from backend.core.world import World


def good_record(**over):
    record = {
        "status": "verified", "scope": "real", "interface": "v6-ascii-movel",
        "record_id": "mv-2026-11-01", "verified_by": "onsite", "verified_at": "2026-11-01T10:00:00",
        "controller_version": "3.6.1.0", "robot_variant": "elfin5",
        "tool_context": "ctx", "lower": [-180.0] * 6, "upper": [180.0] * 6,
        "tcp_name": "TCP_1", "ucs_name": "Base",
        "evidence": {"straightness_mm": .3, "settle_s": .8, "stop_s": .4},
    }
    record.update(over)
    return record


class QualificationGate(unittest.TestCase):
    def resolve(self, record, **over):
        params = dict(version="3.6.1.0", variant="elfin5", tool_context="ctx",
                      lower=[-180.0] * 6, upper=[180.0] * 6,
                      tcp_name="TCP_1", ucs_name="Base")
        params.update(over)
        return gate_mod.resolve_record(record, **params)

    def test_missing_record_blocks_with_actionable_reason(self):
        result = self.resolve(None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["blockers"][0]["code"], "missing_commissioning")
        self.assertIn("现场", result["blockers"][0]["detail"])

    def test_record_must_match_controller_tool_limits_and_frames(self):
        for over, code in (
            ({"controller_version": "9.9.9"}, "commissioning_context_changed"),
            ({"robot_variant": "elfin10"}, "commissioning_context_changed"),
            ({"tool_context": "other"}, "commissioning_context_changed"),
            ({"lower": [-170.0] * 6}, "commissioning_context_changed"),
            ({"tcp_name": "TCP_2"}, "commissioning_frame_changed"),
            ({"ucs_name": "User_1"}, "commissioning_frame_changed"),
        ):
            with self.subTest(code=code, over=over):
                result = self.resolve(good_record(**over))
                self.assertFalse(result["ok"])
                self.assertEqual(result["blockers"][0]["code"], code)

    def test_incomplete_or_wrong_interface_record_is_rejected(self):
        for over in ({"status": "draft"}, {"scope": "sim"}, {"interface": "v6-ascii-cycle"},
                     {"record_id": ""}, {"evidence": {}}):
            with self.subTest(over=over):
                self.assertFalse(self.resolve(good_record(**over))["ok"])

    def test_straightness_and_timing_must_meet_admission_limits(self):
        cases = (({"straightness_mm": 1.5}, "straightness_out_of_tolerance"),
                 ({"settle_s": 3.0}, "timing_out_of_budget"),
                 ({"stop_s": 5.0}, "timing_out_of_budget"),
                 ({"straightness_mm": float("nan")}, "invalid_evidence"))
        for evidence, code in cases:
            with self.subTest(code=code):
                result = self.resolve(good_record(evidence={**good_record()["evidence"], **evidence}))
                self.assertFalse(result["ok"])
                self.assertEqual(result["blockers"][0]["code"], code)

    def test_verified_record_passes_and_yields_fingerprint(self):
        result = self.resolve(good_record())
        self.assertTrue(result["ok"], result["blockers"])
        self.assertTrue(result["fingerprint"])


class FakeLink:
    def __init__(self, snap=None, accept=True):
        self.cmd_up = True
        self.snap = {"moving": 0, "inpos": 1, "free_drive": 0, "override": .1,
                     "version": "3.6.1.0"}
        self.snap.update(snap or {})
        self._accept = accept
        self.sent = []
        self._asks = 0

    def state_valid(self):
        return True

    async def ask(self, name, *params, **kwargs):
        self.sent.append((name, params))
        self._asks += 1
        return SimpleNamespace(ok=self._accept, text="" if self._accept else "20018 运动忙")


def make_world(tmpdir, link, *, tool_ctx="ctx", joints=None):
    model = RobotModel("elfin5")
    w = World.__new__(World)
    w.real = link
    w.settings = {"mode": "real", "real_speed_pct": 10.0}
    w.speed_ack = {"ok": True}
    w.model = SimpleNamespace(variant="elfin5")
    w._data_dir = Path(tmpdir)
    w.executor = SimpleNamespace(check=lambda epoch: None)
    w.log = SimpleNamespace(add=lambda *args: None)
    w.motion_model = lambda: model
    w.current_joints = lambda: list(joints or [0.0, -30.0, 60.0, 0.0, 40.0, 0.0])
    w.limits = lambda: (list(model.lower), list(model.upper))
    w.tools = SimpleNamespace(mounted={"tool_id": "t1", "revision": 1},
                              get=lambda tool_id: {"name": "TCP_1", "tcp": {"translation_mm": [0, 0, 0]}})
    w.motion_session = None
    w._tool_ctx_for_test = tool_ctx
    return w, model


class Dispatch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write_record(self, record):
        (Path(self.tmp.name) / "real_movel_qualification.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def record_for(self, w):
        from backend.core.planning import digest
        model = w.motion_model()
        q = w.current_joints()
        start = model.pose_vec(q)
        record = good_record(
            version="3.6.1.0",
            lower=list(model.lower), upper=list(model.upper),
            tool_context=digest({"tool_id": "t1", "revision": 1,
                                 "tcp": {"translation_mm": [0, 0, 0]}}),
            tcp_name="TCP_1", ucs_name="Base")
        return record, start

    async def test_without_record_nothing_is_sent(self):
        link = FakeLink()
        w, model = make_world(self.tmp.name, link)
        target = model.pose_vec(w.current_joints())
        target[2] += 40
        with self.assertRaises(RuntimeError) as ctx:
            await w._real_movelto(target, 1)
        self.assertIn("missing_commissioning", str(ctx.exception))
        self.assertEqual(link.sent, [], "没有资格记录时绝不能向控制器发指令")

    async def test_unconfirmed_speed_blocks_before_dispatch(self):
        link = FakeLink()
        w, model = make_world(self.tmp.name, link)
        record, start = self.record_for(w)
        self.write_record(record)
        w.speed_ack = {"ok": None}
        target = list(start)
        target[2] += 40
        with self.assertRaises(RuntimeError) as ctx:
            await w._real_movelto(target, 1)
        self.assertIn("倍率", str(ctx.exception))
        self.assertEqual(link.sent, [])

    async def test_moving_or_hand_dragging_robot_is_refused(self):
        for snap in ({"moving": 1}, {"inpos": 0}, {"free_drive": 1}):
            with self.subTest(snap=snap):
                link = FakeLink(snap)
                w, model = make_world(self.tmp.name, link)
                record, start = self.record_for(w)
                self.write_record(record)
                target = list(start)
                target[2] += 40
                with self.assertRaises(RuntimeError):
                    await w._real_movelto(target, 1)
                self.assertEqual(link.sent, [])

    async def test_verified_record_dispatches_exactly_one_movlto(self):
        link = FakeLink()
        w, model = make_world(self.tmp.name, link)
        record, start = self.record_for(w)
        self.write_record(record)
        target = list(start)
        target[2] += 40
        await w._real_movelto(target, 1)
        self.assertEqual(len(link.sent), 1)
        name, params = link.sent[0]
        self.assertEqual(name, "MoveLTo")
        # 机器人号、6 个位姿分量、TCP 名称、用户坐标系名称
        self.assertEqual(len(params), 9)
        self.assertEqual(params[0], 0)
        self.assertEqual(params[7], "TCP_1")
        self.assertEqual(params[8], "Base")
        for got, want in zip(params[1:7], target):
            self.assertAlmostEqual(got, want, places=6)

    async def test_controller_rejection_is_reported_not_swallowed(self):
        link = FakeLink(accept=False)
        w, model = make_world(self.tmp.name, link)
        record, start = self.record_for(w)
        self.write_record(record)
        target = list(start)
        target[2] += 40
        with self.assertRaises(RuntimeError) as ctx:
            await w._real_movelto(target, 1)
        self.assertIn("20018", str(ctx.exception))

    async def test_target_already_reached_sends_nothing(self):
        link = FakeLink()
        w, model = make_world(self.tmp.name, link)
        record, start = self.record_for(w)
        self.write_record(record)
        await w._real_movelto(list(start), 1)
        self.assertEqual(link.sent, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
