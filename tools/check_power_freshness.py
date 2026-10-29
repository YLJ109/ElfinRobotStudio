"""FSM7 timing reproducer. Run through run_offline_checks.py, never on live data.

The same-tick regression requires stop confirmation from a fresh new frame even
when its monotonic timestamp equals the previous frame's timestamp.
The clock and all production freshness/connection checks remain unchanged.
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import json
import time
import unittest
from unittest.mock import patch

from check_power_regressions import FakePowerController
from backend.core import config
from backend.core.world import World


class PowerFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config.save_settings({"mode": "sim", "auto_connect": False})
        self.w = World()
        self.w.settings["mode"] = "real"
        self.link = self.w.real = FakePowerController(7, 0)
        self.trace = []
        self.log_io = []
        original_add = self.w.log.add
        def timed_add(kind, text, extra=None):
            started = time.perf_counter()
            try:
                return original_add(kind, text, extra)
            finally:
                self.log_io.append({"kind": kind, "text": text,
                                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
        self.w.log.add = timed_add
        self.no_network = patch("asyncio.open_connection", side_effect=AssertionError("Offline timing test: no sockets"))
        self.no_network.start()

    async def asyncTearDown(self):
        print("LOG_IO " + json.dumps({"test": self.id(), "writes": self.log_io}, ensure_ascii=False), flush=True)
        for task in self.link.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.link.tasks, return_exceptions=True)
        self.no_network.stop()

    def observe(self, stage, **extra):
        link = self.link
        self.trace.append({"stage": stage, "stamp": link.snap["stamp"],
                           "age_ms": round((time.monotonic() - link.snap["stamp"]) * 1000, 3),
                           "frame_seq": link.frame_seq, "epoch": link.epoch,
                           "frame_epoch": link.snap["connection_epoch"],
                           "valid": link.state_valid(), "fsm": link.snap["fsm"],
                           "enabled": link.snap["enabled"], "calls": list(link.calls), **extra})

    async def test_same_tick_new_frame_preserves_idempotent_fsm7_cancel(self):
        original_publish = self.link.publish
        # Model two valid frames in the same GetTickCount64 tick, deterministically.
        # Only the fake frame timestamp is held equal; the clock/safety gate run normally.
        stamp = self.link.snap["stamp"]
        initial_seq = self.link.frame_seq
        def same_tick_publish(*args, **kwargs):
            original_publish(*args, **kwargs)
            first = self.link.frame_seq == initial_seq + 1
            if first:
                self.link.snap["stamp"] = stamp
            self.observe("new_frame_same_timestamp" if first else "periodic_frame")
        self.link.publish = same_tick_publish
        original_stop = self.w.stop_execution
        async def observed_stop(reason):
            self.observe("before_stop")
            result = await original_stop(reason)
            self.observe("after_stop", settled=result["settled"])
            return result
        self.w.stop_execution = observed_stop
        result = await self.w.cancel_ready()
        self.observe("cancel_result", result=result)
        print("FSM7_SAME_TICK " + json.dumps(self.trace, ensure_ascii=False), flush=True)
        self.assertGreater(self.link.frame_seq, initial_seq)
        self.assertTrue(self.w.stop_result["settled"],
                        "A fresh same-epoch stationary frame must confirm stop even when its timestamp is unchanged")
        self.assertTrue(result["ok"], "A newly received stationary disabled frame must preserve the idempotent power stage")
        self.assertEqual(self.link.snap["fsm"], 7)
        self.assertEqual(self.link.calls, ["GrpStop"])

    async def test_natural_fsm7_cancel_reports_stop_and_log_latency(self):
        initial_seq = self.link.frame_seq
        self.observe("before_natural_cancel")
        started = time.perf_counter()
        result = await self.w.cancel_ready()
        self.observe("natural_cancel_result", result=result, stop=self.w.stop_result,
                     elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                     frame_delta=self.link.frame_seq - initial_seq)
        print("FSM7_NATURAL " + json.dumps(self.trace, ensure_ascii=False), flush=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.link.calls, ["GrpStop"])

    async def test_distinct_timestamp_new_frame_preserves_idempotent_fsm7_cancel(self):
        async def cross_clock_tick(name):
            if name == "GrpStop":
                await asyncio.sleep(.035)
        self.link.hook = cross_clock_tick
        before = self.link.snap["stamp"]
        result = await self.w.cancel_ready()
        self.observe("distinct_timestamp_result", result=result, timestamp_advanced=self.link.snap["stamp"] > before)
        print("FSM7_DISTINCT_TICK " + json.dumps(self.trace, ensure_ascii=False), flush=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.w.stop_result["settled"])
        self.assertEqual(self.link.snap["fsm"], 7)
        self.assertEqual(self.link.calls, ["GrpStop"])

    async def test_missing_post_stop_frame_never_confirms_stale_disabled_state(self):
        self.link.feedback = False
        result = await self.w.cancel_ready()
        self.observe("missing_frame_result", result=result)
        print("FSM7_MISSING_FRAME " + json.dumps(self.trace, ensure_ascii=False), flush=True)
        self.assertFalse(result["ok"], "A remembered enabled=0 must not bypass freshness")
        self.assertFalse(self.w.stop_result["settled"])
        self.assertNotIn("BlackOut", self.link.calls)
        self.assertNotIn("GrpEnable", self.link.calls)

    async def test_feedback_false_stops_heartbeat_until_explicitly_resumed(self):
        self.link.feedback = False
        before = (self.link.frame_seq, self.link.snap["stamp"], self.link.snap["connection_epoch"])
        await asyncio.sleep(.13)
        self.assertEqual((self.link.frame_seq, self.link.snap["stamp"], self.link.snap["connection_epoch"]), before)
        self.link.feedback = True
        await asyncio.sleep(.13)
        self.assertGreater(self.link.frame_seq, before[0])
        self.link.feedback = False
        frozen = (self.link.frame_seq, self.link.snap["stamp"])
        await asyncio.sleep(.13)
        self.assertEqual((self.link.frame_seq, self.link.snap["stamp"]), frozen)

    async def test_twenty_consecutive_fsm7_cancels_keep_power_without_disable_commands(self):
        durations = []
        for index in range(20):
            with self.subTest(index=index):
                started = time.perf_counter()
                result = await self.w.cancel_ready()
                durations.append(round((time.perf_counter() - started) * 1000, 3))
                self.assertTrue(result["ok"], result)
                self.assertTrue(self.w.stop_result["settled"])
                self.assertEqual(self.link.snap["fsm"], 7)
                self.assertEqual(self.link.snap["enabled"], 0)
        self.assertEqual(self.link.calls, ["GrpStop"] * 20)
        print("FSM7_REPEAT " + json.dumps({"count": 20, "durations_ms": durations,
                                           "max_log_io_ms": max(row["elapsed_ms"] for row in self.log_io)}), flush=True)


if __name__ == "__main__":
    print("CLOCK " + str(time.get_clock_info("monotonic")), flush=True)
    unittest.main(verbosity=2)
