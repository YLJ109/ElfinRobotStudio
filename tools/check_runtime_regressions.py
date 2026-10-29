"""API/telemetry tests. Must run through run_offline_checks.py in its temporary copy."""
import asyncio
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("HUYAN_OFFLINE_ROOT") != str(ROOT):
    raise SystemExit("Use tools/run_offline_checks.py check_runtime_regressions.py (isolated copy required)")
os.environ["HUYAN_NO_REAL"] = "1"
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from backend.core import config, telemetry
from backend.core.robot_real import RealLink
from backend.core.world import World
from backend.main import app
import backend.main as main
from backend.core.storage import write_json


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        config.save_settings({"mode": "sim", "auto_connect": False})
        no_network = patch("asyncio.open_connection", side_effect=AssertionError("No sockets in runtime tests"))
        no_network.start()
        self.addCleanup(no_network.stop)

    def test_preview_api_has_no_command_or_motion_side_effects(self):
        w = World()
        link = RealLink("offline.invalid")
        w.real = link
        w.settings["mode"] = "real"
        before = w.current_joints()
        tool_before = w.tool_snapshot()
        with patch.object(main, "world", w), \
             patch.object(link, "ask", new=AsyncMock(side_effect=AssertionError("Preview sent a command"))) as ask, \
             patch.object(w.executor, "submit", side_effect=AssertionError("Preview submitted motion")), \
             TestClient(app, base_url="http://127.0.0.1:8710") as c:
            # World.start is offline-guarded even when a real-mode snapshot is requested.
            target = [10., 20., 30., 40., 50., 60.]
            result = c.post("/api/preview", json={"joints": target})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json(), {"ok": True, "preview": target})
            self.assertFalse(w._preview_executing)
            self.assertEqual(w.current_joints(), before)
            self.assertEqual(w.tool_snapshot(), tool_before)
            self.assertEqual(c.post("/api/preview", json={"joints": None}).json()["preview"], None)
            ask.assert_not_awaited()

    def test_invalid_preview_payload_never_reaches_command_queue(self):
        w = World()
        w.set_preview([1.] * 6)
        with patch.object(main, "world", w), \
             patch.object(w.executor, "submit", side_effect=AssertionError("Invalid preview submitted motion")), \
             TestClient(app, base_url="http://127.0.0.1:8710") as c:
            for target in ([1., 2.], [0.] * 7):
                with self.subTest(target=target):
                    self.assertEqual(c.post("/api/preview", json={"joints": target}).status_code, 422)
            self.assertEqual(w.preview_q, [1.] * 6)

    def test_new_preview_revokes_prior_alignment_hold_and_execution_marker(self):
        w = World()
        q = w.current_joints()
        w.set_preview(q)
        w._preview_executing = True
        w._tick_preview(False, 100.)
        self.assertEqual(w._inpos_since, 100.)
        w.set_preview(q)
        self.assertEqual(w._inpos_since, 0.)
        w._tick_preview(False, 110.)
        self.assertEqual(w.preview_q, q)
        self.assertFalse(w._preview_executing)
        w.set_preview(None)
        self.assertIsNone(w.preview_q)
        self.assertEqual(w._inpos_since, 0.)

    def test_real_preview_requires_fresh_stationary_inpos_readback_and_full_hold(self):
        w = World()
        link = RealLink("offline.invalid")
        link.stream_up = link.cmd_up = True
        w.real, w.settings["mode"] = link, "real"
        link.snap.update(joints=[0.] * 6, stamp=time.monotonic(), connection_epoch=link.epoch,
                         moving=0, inpos=1, fsm=33, enabled=1, err_code=0)
        w.set_preview([0.] * 6)
        w._preview_executing = True
        w._tick_preview(False, 100.)
        for moving, inpos, fresh, outer_moving in ((1, 1, True, False), (0, 0, True, False),
                                                  (0, 1, False, False), (0, 1, True, True)):
            with self.subTest(moving=moving, inpos=inpos, fresh=fresh, outer=outer_moving):
                link.snap.update(moving=moving, inpos=inpos,
                                 stamp=time.monotonic() - (0 if fresh else 10))
                w._tick_preview(outer_moving, 102.)
                self.assertIsNotNone(w.preview_q)
                self.assertEqual(w._inpos_since, 0.)
        link.snap.update(moving=0, inpos=1, stamp=time.monotonic())
        w._tick_preview(False, 103.)
        w._tick_preview(False, 103.2)
        self.assertIsNotNone(w.preview_q)
        w._tick_preview(False, 104.)
        self.assertIsNone(w.preview_q)
        self.assertTrue(link.q.empty())

    def test_real_unknown_tool_tcp_remains_flange_without_simulation_offsets(self):
        w = World()
        # The simulated reference TCP must never leak into real calibration.
        w.settings["mode"] = "real"
        w.tools.mounted = w.tools._unknown()
        self.assertIsNone(w.tool_snapshot()["tool"])
        self.assertEqual(w.motion_model().tcp_translation_mm, [0., 0., 0.])
        self.assertEqual(w.motion_model().tcp_rpy_deg, [0., 0., 0.])
        self.assertEqual(w.motion_model().pose_vec(w.current_joints()), w.model.pose_vec(w.current_joints()))

    def test_main_tick_stale_stream_breaks_preview_continuous_alignment_hold(self):
        w = World()
        link = RealLink("offline.invalid")
        link.stream_up = link.cmd_up = True
        w.real, w.settings["mode"] = link, "real"
        link.snap.update(joints=[0.] * 6, stamp=time.monotonic(), connection_epoch=link.epoch,
                         moving=0, inpos=1, fsm=33, enabled=1, err_code=0)
        w.set_preview([0.] * 6)
        w._preview_executing = True
        w._tick_preview(False, 100.)
        link.snap["stamp"] -= 10.
        # Exercise the actual main-loop early return, not just _tick_preview.
        # No worker/command pump has been started on this link.
        w._tick(.02, 102.)
        self.assertIsNotNone(w.preview_q)
        self.assertEqual(w._inpos_since, 0., "A stale stream cannot count toward continuous settled time")
        link.snap["stamp"] = time.monotonic()
        w._tick_preview(False, 103.)
        self.assertIsNotNone(w.preview_q, "First fresh frame after a gap must restart the hold")
        self.assertTrue(link.q.empty())

    def test_saved_interrupted_execution_is_reported_but_never_auto_resumed(self):
        for dynamic in (False, True):
            with self.subTest(dynamic=dynamic):
                saved = {"state": "running", "dynamic": dynamic, "i": 0, "total": 1,
                         "steps": [{"k": "movej", "joints": [1.] * 6}]}
                path = config.DATA_DIR / "exec_state.json"
                previous = path.read_text(encoding="utf-8") if path.exists() else None
                self.addCleanup(lambda p=path, old=previous: p.write_text(old, encoding="utf-8") if old is not None else p.unlink(missing_ok=True))
                write_json(path, saved)
                with patch("backend.core.world.MotionExecutor.submit", side_effect=AssertionError("Refresh resumed motion")):
                    w = World()
                self.assertIsNotNone(w.resume)
                self.assertEqual(w.runner.state, "idle")
                self.assertFalse(w.executor.busy)
                self.assertIsNone(w._ready_task)
                self.assertIsNone(w._speed_task)
                self.assertIsNone(w.preview_q)

    def test_unexecuted_stored_point_preview_does_not_auto_disappear(self):
        config.save_settings({"mode": "sim", "auto_connect": False})
        w = World()
        q = w.current_joints()
        w.set_preview(q)
        w._tick_preview(False, 100.)
        w._tick_preview(False, 102.)
        self.assertEqual(w.preview_q, q)
        w._preview_executing = True
        w._tick_preview(False, 103.)
        w._tick_preview(False, 105.)
        self.assertIsNone(w.preview_q)
        w.set_preview(q)
        self.assertFalse(w._preview_executing)

    def test_isolated_connection_refusal_is_not_reported_as_success(self):
        with TestClient(app, base_url="http://127.0.0.1:8710") as c:
            result = c.post("/api/real/connect", json={"ip": "192.168.0.10"}).json()
            self.assertFalse(result["ok"])
            self.assertIn("HUYAN_NO_REAL", result["why"])

    def test_diag_returns_an_object_not_a_double_encoded_string(self):
        with TestClient(app, base_url="http://127.0.0.1:8710") as c:
            response = c.get("/api/diag")
            self.assertEqual(response.status_code, 200)
            self.assertIsInstance(response.json(), dict)
            self.assertIn("raw_frame", response.json())

    def test_snapshot_exposes_saved_ip_and_command_failure(self):
        config.save_settings({"mode": "sim", "auto_connect": False})
        w = World()
        w.settings = dict(w.settings, mode="real", robot_ip="192.168.0.10")
        link = RealLink("offline.invalid")
        link.stream_up = link.cmd_up = True
        link.snap.update(joints=[0.] * 6, fsm=7, enabled=0, moving=0, inpos=1,
                         stamp=time.monotonic(), connection_epoch=link.epoch)
        link.last_command = {"name": "GrpReset", "ok": False, "err_code": 20904,
                             "fields": ["20904", "0", "Emergency stop reset timeout"]}
        w.real = link
        s = w.snapshot(force_tele=True)
        self.assertEqual(s["settings"]["robot_ip"], "192.168.0.10")
        self.assertEqual(s["real"]["last_command"]["err_code"], 20904)

    def test_missing_telemetry_is_not_a_green_health_check(self):
        result = telemetry.preflight("real", {"cmd_up": False, "stream_up": False}, {}, {}, {}, 0, False)
        rows = {r["k"]: r for r in result["rows"]}
        for key in ("power", "ecat", "axerr", "err", "safe", "stream"):
            self.assertFalse(rows[key]["state"], key)

    def test_old_rate_does_not_hide_disconnected_state_stream(self):
        r = telemetry.preflight("real", {"cmd_up": True, "stream_up": False},
                                {"rate_hz": 20}, {}, {}, 0, False)
        self.assertFalse(next(row for row in r["rows"] if row["k"] == "stream")["state"])

    def test_scene_overlap_is_reported_even_if_objects_are_stationary(self):
        r = telemetry.preflight("real", {}, {}, {},
                                {"level": "alarm", "collisions": [{"type": "object"}]}, 0, False)
        row = next(row for row in r["rows"] if row["k"] == "obj")
        self.assertFalse(row["state"])
        self.assertFalse(row["blocking"])


class BackpressureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config.save_settings({"mode": "sim", "auto_connect": False})
        self.no_network = patch("asyncio.open_connection", side_effect=AssertionError("No sockets in queue tests"))
        self.no_network.start()

    async def asyncTearDown(self):
        self.no_network.stop()

    async def test_slow_subscriber_keeps_only_latest_frame_and_can_unsubscribe(self):
        w = World()
        q = w.subscribe()
        self.assertEqual(q.maxsize, 1)
        snapshots = iter({"seq": i} for i in range(1, 6))
        w._tick = Mock()
        w.snapshot = Mock(side_effect=lambda: next(snapshots))
        sleeps = 0
        async def advance_without_wait(_delay):
            nonlocal sleeps
            sleeps += 1
            self.assertEqual(q.qsize(), 1)
            w._broadcast_at = 0.
            if sleeps == 5:
                raise asyncio.CancelledError()
        with patch("backend.core.world.asyncio.sleep", side_effect=advance_without_wait):
            with self.assertRaises(asyncio.CancelledError):
                await w._loop()
        self.assertEqual(q.get_nowait(), {"seq": 5})
        w.unsubscribe(q)
        w.unsubscribe(q)
        self.assertNotIn(q, w.subscribers)

    async def test_full_command_queue_rejects_without_pending_future_leak(self):
        link = RealLink("offline.invalid")
        link.cmd_up = True
        self.assertGreater(link.q.maxsize, 0)
        for i in range(link.q.maxsize):
            link.q.put_nowait((1, i, "offline placeholder", "ReadCurFSM"))
        # A read-only query exercises queue admission; no writer/task is started.
        for _ in range(250):
            reply = await link.ask("ReadCurFSM", 0)
            self.assertFalse(reply.ok)
            self.assertEqual(reply.outcome, "not_sent")
        self.assertEqual(link.q.qsize(), link.q.maxsize)
        self.assertFalse(link._pending)
        self.assertFalse(link._futures)
        self.assertLessEqual(len(link.command_trace), 200)


if __name__ == "__main__":
    unittest.main()
