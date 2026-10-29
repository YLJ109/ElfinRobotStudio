"""Connection regressions: fake readers/writers only, no World or robot sockets."""
import asyncio
import json
from pathlib import Path
import struct
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import protocol as P
from backend.core import controls
from backend.core.robot_real import RealLink
from backend.core.robot_real import Desync


class ProtocolTests(unittest.TestCase):
    def test_non_finite_payload_numbers_remain_unknown(self):
        d = P.decode_state(b'{"nan":NaN,"inf":1e999,"normal":2.5}')
        self.assertEqual(d, {"nan": None, "inf": None, "normal": 2.5})
    def test_every_tcp_split_preserves_binary_frame_and_ascii_reply(self):
        payload = json.dumps({"StateAndError": {"robotState": 7}}).encode()
        frame = P.HDR.pack(P.MAGIC, len(payload) + P.HDR.size, len(payload)) + payload
        wire = b"ReadCurFSM,OK,7,;" + frame + b"ReadBoxInfo,OK,1,0,;"
        for split in range(1, len(wire)):
            with self.subTest(split=split):
                left, remainder, text = P.split_frames(wire[:split])
                right, remainder, tail = P.split_frames(remainder + wire[split:])
                self.assertEqual(left + right, [payload])
                self.assertEqual(remainder, b"")
                self.assertEqual(text + tail, b"ReadCurFSM,OK,7,;ReadBoxInfo,OK,1,0,;")

    def test_unknown_fsm_does_not_offer_blind_power_up(self):
        c = controls.derive("real", {"stream_up": True, "cmd_up": True},
                            {"fsm": 5, "enabled": 0, "err_code": 0}, {})
        self.assertFalse(c["primary"]["enabled"])
        self.assertNotEqual(c["primary"]["key"], "electrify")

    def test_initializing_is_not_another_start_master_button(self):
        c = controls.derive("real", {"stream_up": True, "cmd_up": True},
                            {"fsm": 15, "enabled": 0, "err_code": 0}, {})
        self.assertFalse(c["primary"]["enabled"])


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_telemetry_gap_is_stale_without_reconnecting_socket(self):
        link = RealLink("offline.invalid")
        observed = []
        payload = json.dumps({"PosAndVel": {"Actual_Position": [0]*6},
                              "StateAndError": {"robotState":5,"robotEnabled":0,"robotMoving":0,"InPos":1}}).encode()
        frame = P.HDR.pack(P.MAGIC, len(payload)+P.HDR.size, len(payload)) + payload
        class Reader:
            count = 0
            async def read(self, _size):
                self.count += 1
                if self.count == 2:
                    await asyncio.sleep(.03)
                    observed.append(link.state_valid())
                if self.count > 2:
                    raise asyncio.CancelledError
                return frame
        class Writer:
            def close(self): pass
        async def connect(*_args): return Reader(), Writer()
        with patch("asyncio.open_connection", connect), \
             patch("backend.core.robot_real.STATE_MAX_AGE_S", .01), \
             patch("backend.core.robot_real.STREAM_READ_TIMEOUT_S", .08):
            with self.assertRaises(asyncio.CancelledError):
                await link._stream_loop()
        self.assertEqual(observed, [False])
        self.assertEqual(link.epoch, 1)
        self.assertEqual(link.frame_seq, 2)

    async def test_stale_state_keeps_only_inflight_independent_replies(self):
        for name, retained in (("GrpReset", True), ("ReadActPos", True),
                               ("GrpDisable", True), ("Electrify", False), ("MoveJTo", False)):
            link = RealLink("offline.invalid")
            link._inflight = 1
            link.command_trace.append({"command_id": 1, "name": name})
            inflight = asyncio.get_running_loop().create_future()
            queued = asyncio.get_running_loop().create_future()
            link._futures = {1: inflight, 2: queued}
            link._pending = {1: (time.monotonic() + 3, 0), 2: (time.monotonic() + 3, 0)}
            link.invalidate_pending("stale telemetry", preserve_inflight=True)
            self.assertEqual(inflight.done(), not retained, name)
            self.assertTrue(queued.done(), name)
            self.assertEqual(link.epoch, 1)
            if retained:
                self.assertIn(1, link._futures)
                link.invalidate_pending("command socket closed")
                self.assertTrue(inflight.done())

    async def test_disconnected_action_replaces_old_success_but_queries_do_not(self):
        link = RealLink("offline.invalid")
        link.last_command = {"name": "Electrify", "ok": True}
        await link.ask("GrpEnable", 0)
        self.assertEqual(link.last_command["name"], "GrpEnable")
        self.assertFalse(link.last_command["ok"])
        self.assertEqual(link.last_command["outcome"], "not_sent")
        await link.ask("ReadCurFSM", 0)
        self.assertEqual(link.last_command["name"], "GrpEnable")

    async def test_expired_action_replaces_old_success(self):
        link = RealLink("offline.invalid")
        link.cmd_up = True
        link.last_command = {"name": "Electrify", "ok": True}
        reply = await link.ask("GrpEnable", 0, timeout=.001)
        self.assertFalse(reply.ok)
        self.assertEqual(link.last_command["name"], "GrpEnable")
        self.assertEqual(link.last_command["outcome"], "expired")

    async def test_old_matching_reply_cannot_confirm_a_new_command(self):
        link = RealLink("offline.invalid")
        link.ascii = b"Electrify,OK,;"
        class Writer:
            def write(self, data): pass
            async def drain(self): pass
        link._writer = Writer()
        with patch("backend.core.robot_real.REPLY_TIMEOUT_S", .01):
            with self.assertRaises(Desync):
                await link._roundtrip("Electrify,;", "Electrify")

    async def test_frozen_controller_timestamp_is_not_fresh_telemetry(self):
        link = RealLink("offline.invalid")
        link.stream_up = True
        frame = {"PosAndVel": {"Actual_Position": [0] * 6},
                 "StateAndError": {"robotState": 7, "robotEnabled": 0, "robotMoving": 0, "InPos": 1},
                 "MsgTitle": {"Stamp": "1000"}}
        link._on_frame(frame)
        self.assertTrue(link.state_valid())
        link._controller_progress_at -= 1
        link._on_frame(frame)
        self.assertFalse(link.state_valid())

    async def test_missing_slow_fields_clear_previous_measurements(self):
        link = RealLink("offline.invalid")
        link.snap.update(ecat_lost=0, volt48_out=48.0, model_name="old")
        link._parse_slow({}, link.snap, time.monotonic())
        self.assertIsNone(link.snap["ecat_lost"])
        self.assertIsNone(link.snap["volt48_out"])
        self.assertIsNone(link.snap["model_name"])
    async def test_closed_command_reader_clears_online_badge(self):
        reader = asyncio.StreamReader()
        reader.feed_eof()
        link = RealLink("offline.invalid")
        link.cmd_up = True
        await link._pump_reader(reader)
        self.assertFalse(link.cmd_up)
        self.assertTrue(link._dead)

    async def test_idle_loop_reconnects_a_dead_command_reader(self):
        calls = []
        link = RealLink("offline.invalid")
        class Writer:
            def close(self): pass
        async def connect(*args, **kwargs):
            calls.append(args)
            if len(calls) > 1:
                link._closing = True
                raise asyncio.CancelledError
            reader = asyncio.StreamReader()
            reader.feed_eof()
            return reader, Writer()
        with patch("asyncio.open_connection", connect):
            task = asyncio.create_task(link._cmd_loop())
            try:
                await asyncio.wait_for(task, 2.5)
            except asyncio.CancelledError:
                pass
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertGreaterEqual(len(calls), 2)

    async def test_command_stream_frames_do_not_accumulate_forever(self):
        payload = b'{"StateAndError":{"robotState":7}}'
        frame = P.HDR.pack(P.MAGIC, P.HDR.size + len(payload), len(payload)) + payload
        reader = asyncio.StreamReader()
        reader.feed_data(frame * 200 + b"ReadCurFSM,OK,7,;")
        reader.feed_eof()
        link = RealLink("offline.invalid")
        await link._pump_reader(reader)
        self.assertEqual(link.raw, b"")
        self.assertEqual(P.parse_reply(link.ascii)[:3], ("ReadCurFSM", True, ["7"]))

    async def test_malformed_frame_does_not_partially_replace_pose(self):
        link = RealLink("offline.invalid")
        link.snap.update(joints=[1.] * 6, stamp=time.monotonic())
        with self.assertRaises(ValueError):
            link._on_frame({"PosAndVel": {"Actual_Position": [2, 2, "nan", 2, 2, 2]},
                            "StateAndError": {"robotState": 7}})
        self.assertEqual(link.snap["joints"], [1.] * 6)

    async def test_boolean_flag_strings_are_parsed_as_flags(self):
        link = RealLink("offline.invalid")
        link.stream_up = True
        link._on_frame({"PosAndVel": {"Actual_Position": [0] * 6},
                        "StateAndError": {"robotState": "7", "robotEnabled": "0",
                                          "robotMoving": "0", "InPos": "1"}})
        self.assertTrue(link.state_valid())
        self.assertEqual(link.snap["enabled"], 0)


if __name__ == "__main__":
    unittest.main()
