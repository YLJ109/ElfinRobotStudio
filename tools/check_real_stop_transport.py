"""停止传输回归：只用假连接，不导入 World、不连接硬件、不写运行数据。"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import protocol as P
from backend.core.robot_real import RealLink, Reply


class ProtocolTokenTests(unittest.TestCase):
    def test_every_token_rejects_delimiters_and_control_characters(self):
        for value in ("0,;PackageVersion", "TCP,other", "TCP\n", "TCP\r", "TCP\0", "TCP\x7f", "TCP\t"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                P.encode("MoveLTo", value)
        with self.assertRaises(ValueError):
            P.encode("GrpStop;PackageVersion", 0)

    def test_unranged_numbers_and_read_only_parameters_are_validated(self):
        link = RealLink("offline.invalid")
        for value in (float("nan"), float("inf"), "nan", True, "0,;PackageVersion", []):
            with self.subTest(value=value):
                self.assertIsNotNone(link._validate("MoveJTo", (0, value, 0, 0, 0, 0, 0)))
                self.assertIsNotNone(link._validate("GetErrorCodeStr", (value,)))
        self.assertIsNotNone(link._validate("GetErrorCodeStr", (1.5,)))
        self.assertIsNotNone(link._validate("ReadBrakeStatus", (.5,)))
        self.assertIsNone(link._validate("GetErrorCodeStr", (123456789,)))
        self.assertIsNone(link._validate("MoveJTo", (0, 999999., 0, 0, 0, 0, 0)))

    def test_string_parameters_and_normal_wire_format_are_preserved(self):
        link = RealLink("offline.invalid")
        self.assertIsNone(link._validate("MoveLTo", (0, 1, 2, 3, 0, 0, 0, "TCP_1", "Base")))
        self.assertEqual(P.encode("MoveRelJ", 0, 5, 1, .25), b"MoveRelJ,0,5,1,0.25,;")
        self.assertEqual(P.encode("PackageVersion"), b"PackageVersion,;")


class FakeWriter:
    def __init__(self, reader):
        self.reader = reader
        self.writes = []
        self.read_sent = asyncio.Event()
        self.stop_sent = asyncio.Event()
        self.stop_reply = True
        self.closed = False

    def write(self, data):
        # 正确：保留真实 _roundtrip，只替换 socket 的收发端。
        self.writes.append(data)
        if data.startswith(b"ReadOverride,"):
            self.read_sent.set()
        elif data == b"GrpStop,0,;":
            self.stop_sent.set()
            if self.stop_reply:
                self.reader.feed_data(b"GrpStop,OK,;")

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.closed = True


class StopTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.link = RealLink("offline.invalid")
        self.link._identity_disabled = True
        self.reader = asyncio.StreamReader()
        self.writer = FakeWriter(self.reader)
        self.connects = 0

        async def connect(*args, **kwargs):
            self.connects += 1
            if self.connects != 1:
                # 正确：测试中连第二条假连接都禁止，防止把重放伪装成修复。
                raise AssertionError("测试不允许新连接")
            return self.reader, self.writer

        self.connection_patch = patch("asyncio.open_connection", connect)
        self.connection_patch.start()
        self.loop_task = asyncio.create_task(self.link._cmd_loop())
        async def online():
            while not self.link.cmd_up:
                if self.loop_task.done():
                    await self.loop_task
                await asyncio.sleep(.001)
        await asyncio.wait_for(online(), .5)

    async def asyncTearDown(self):
        await self.link.stop()
        self.loop_task.cancel()
        await asyncio.gather(self.loop_task, return_exceptions=True)
        self.connection_patch.stop()

    async def begin_read(self):
        task = asyncio.create_task(self.link.ask("ReadOverride", 0))
        await asyncio.wait_for(self.writer.read_sent.wait(), .5)
        return task

    async def test_delay_over_point_six_does_not_cancel_pending_stop(self):
        reading = await self.begin_read()
        session = self.link.command_session
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.link.estop(command_session=session), .61)
        self.assertEqual(self.writer.writes, [b"ReadOverride,0,;"])
        self.reader.feed_data(b"ReadOverride,OK,0.05,;")
        await asyncio.wait_for(self.writer.stop_sent.wait(), .5)
        await reading
        self.assertEqual(self.connects, 1)
        self.assertEqual(self.writer.writes, [b"ReadOverride,0,;", b"GrpStop,0,;"])

    async def test_caller_cancellation_does_not_cancel_stop(self):
        reading = await self.begin_read()
        stopping = asyncio.create_task(self.link.estop(command_session=self.link.command_session))
        await asyncio.sleep(.01)
        stopping.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await stopping
        self.reader.feed_data(b"ReadOverride,OK,0.05,;")
        await asyncio.wait_for(self.writer.stop_sent.wait(), .5)
        await reading
        self.assertEqual(self.connects, 1)

    async def test_stream_epoch_change_preserves_original_command_session_stop(self):
        reading = await self.begin_read()
        session = self.link.command_session
        stopping = asyncio.create_task(self.link.estop(command_session=session))
        await asyncio.sleep(.01)
        self.link.invalidate_pending("仅状态流失效", preserve_inflight=True)
        self.assertEqual(self.link.command_session, session)
        self.reader.feed_data(b"ReadOverride,OK,0.05,;")
        reply = await asyncio.wait_for(stopping, .5)
        self.assertTrue(reply.ok)
        self.assertEqual(self.writer.writes[-1], b"GrpStop,0,;")
        await reading

    async def test_stream_disconnect_requests_stop_on_original_command_session(self):
        session = self.link.command_session
        stream_reader = asyncio.StreamReader()
        stream_reader.feed_eof()

        async def stream_connection(host, port):
            self.assertEqual(port, P.PORT_STREAM_JSON[0])
            return stream_reader, FakeWriter(stream_reader)

        with patch("asyncio.open_connection", stream_connection):
            streaming = asyncio.create_task(self.link._stream_loop())
            try:
                await asyncio.wait_for(self.writer.stop_sent.wait(), .5)
            finally:
                streaming.cancel()
                await asyncio.gather(streaming, return_exceptions=True)
        self.assertEqual(self.link.command_session, session)
        self.assertEqual(self.writer.writes, [b"GrpStop,0,;"])
        self.assertEqual(self.connects, 1)

    async def test_old_session_is_rejected_without_queuing(self):
        old = self.link.command_session
        self.link._begin_controller_identity()
        reply = await self.link.estop(command_session=old)
        self.assertFalse(reply.ok)
        self.assertEqual(self.writer.writes, [])
        self.assertTrue(self.link.q.empty())

    async def test_pending_stop_is_not_replayed_on_replaced_writer(self):
        reading = await self.begin_read()
        stopping = asyncio.create_task(self.link.estop(command_session=self.link.command_session))
        await asyncio.sleep(.01)
        replacement = FakeWriter(asyncio.StreamReader())
        self.link._writer = replacement
        self.link._begin_controller_identity()
        self.link.invalidate_pending("命令连接已替换")
        reply = await asyncio.wait_for(stopping, .5)
        self.assertFalse(reply.ok)
        self.reader.feed_data(b"ReadOverride,OK,0.05,;")
        await reading
        await asyncio.sleep(.02)
        self.assertEqual(replacement.writes, [])
        self.assertFalse(self.writer.stop_sent.is_set())

    async def test_writer_replaced_after_send_cannot_confirm_stop_with_old_ack(self):
        self.writer.stop_reply = False
        stopping = asyncio.create_task(self.link.estop(command_session=self.link.command_session))
        await asyncio.wait_for(self.writer.stop_sent.wait(), .5)
        replacement = FakeWriter(asyncio.StreamReader())
        self.link._writer = replacement
        self.link._begin_controller_identity()
        self.link.invalidate_pending("命令连接已变化", preserve_inflight=True)
        self.reader.feed_data(b"GrpStop,OK,;")
        reply = await asyncio.wait_for(stopping, .5)
        self.assertFalse(reply.ok)
        self.assertEqual(reply.outcome, "unknown")
        self.assertEqual(replacement.writes, [])

    async def test_stop_task_has_bounded_wait_and_no_reconnect_replay(self):
        self.writer.stop_reply = False
        with patch("backend.core.robot_real.STOP_REQUEST_TIMEOUT_S", .06):
            reply = await asyncio.wait_for(self.link.estop(command_session=self.link.command_session), .3)
        self.assertFalse(reply.ok)
        self.assertTrue(reply.timeout)
        self.assertEqual(self.connects, 1)
        self.assertEqual(self.writer.writes, [b"GrpStop,0,;"])

    async def test_inflight_timeout_reports_unknown_and_does_not_replay_stop(self):
        with patch("backend.core.robot_real.REPLY_TIMEOUT_S", .04):
            reading = await self.begin_read()
            reply = await asyncio.wait_for(self.link.estop(command_session=self.link.command_session), .3)
        self.assertFalse(reply.ok)
        self.assertFalse(self.link.cmd_up)
        self.assertEqual(self.writer.writes, [b"ReadOverride,0,;"])
        self.assertEqual(self.connects, 1)
        await reading

    async def test_link_close_cleans_pending_independent_stop(self):
        reading = await self.begin_read()
        stopping = asyncio.create_task(self.link.estop(command_session=self.link.command_session))
        await asyncio.sleep(.01)
        await self.link.stop()
        reply = await asyncio.wait_for(stopping, .3)
        self.assertFalse(reply.ok)
        await reading
        self.assertTrue(self.link._stop_task.done())
        self.assertEqual(self.link._futures, {})
        self.assertEqual(self.link._pending, {})
        self.assertEqual(self.link._session_bound, {})

    async def test_graceful_close_drains_original_pending_stop_before_closing_writer(self):
        reading = await self.begin_read()
        stopping = asyncio.create_task(self.link.estop(command_session=self.link.command_session))
        await asyncio.sleep(.01)
        closing = asyncio.create_task(self.link.stop())
        try:
            await asyncio.sleep(.02)
            self.assertFalse(self.writer.closed)
            self.reader.feed_data(b"ReadOverride,OK,0.05,;")
            reply = await asyncio.wait_for(stopping, .5)
            await asyncio.wait_for(closing, .5)
            self.assertTrue(reply.ok)
            self.assertEqual(self.writer.writes.count(b"GrpStop,0,;"), 1)
            self.assertTrue(self.writer.closed)
            self.assertEqual(self.connects, 1)
            await reading
        finally:
            if not closing.done():
                closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)

    async def test_repeated_callers_share_one_stop(self):
        reading = await self.begin_read()
        first = asyncio.create_task(self.link.estop(command_session=self.link.command_session))
        second = asyncio.create_task(self.link.estop(command_session=self.link.command_session))
        await asyncio.sleep(.01)
        self.reader.feed_data(b"ReadOverride,OK,0.05,;")
        replies = await asyncio.wait_for(asyncio.gather(first, second), .5)
        self.assertTrue(all(reply.ok for reply in replies))
        self.assertEqual(self.writer.writes.count(b"GrpStop,0,;"), 1)
        await reading

    async def test_new_ordinary_command_cannot_pass_a_pending_stop(self):
        reading = await self.begin_read()
        stopping = asyncio.create_task(self.link.estop(command_session=self.link.command_session))
        await asyncio.sleep(.01)
        reply = await self.link.ask("MoveRelJ", 0, 0, 1, .25)
        self.assertFalse(reply.ok)
        self.reader.feed_data(b"ReadOverride,OK,0.05,;")
        await asyncio.wait_for(stopping, .5)
        await reading
        self.assertNotIn(b"MoveRelJ,0,0,1,0.25,;", self.writer.writes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
