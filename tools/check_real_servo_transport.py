"""Fake-channel checks, no backend imports, no device or socket operations.

Standalone guarded invocation (does NOT edit runner/manifest):
  .venv/Scripts/python.exe tools/check_real_servo_transport.py --standalone-copy

The launcher copies just these two staged files and the existing offline guard
to a fresh temporary root; child must pass require_offline_copy(). Direct test
execution in the working tree is refused. No live config/data is copied.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import shutil


def _standalone():
    with tempfile.TemporaryDirectory(prefix="servo-transport-offline-") as directory:
        root = Path(directory)
        (root / "tools").mkdir()
        for name in ("staged_real_servo_transport.py", "check_real_servo_transport.py",
                     "_offline_guard.py"):
            shutil.copyfile(Path(__file__).with_name(name), root / "tools" / name)
        env = dict(os.environ, HUYAN_OFFLINE_ROOT=str(root), HUYAN_NO_REAL="1",
                   PYTHONPATH=str(root), PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, "-B", str(root / "tools" / Path(__file__).name)],
                                cwd=root, env=env, timeout=60)
        return result.returncode


if __name__ == "__main__" and sys.argv[1:] == ["--standalone-copy"]:
    raise SystemExit(_standalone())

from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import socket
import time
import unittest
from unittest.mock import patch

if (Path(os.environ['HUYAN_OFFLINE_ROOT'])/'backend/core/real_servo_transport.py').exists():
    from backend.core.real_servo_transport import ExclusiveServoTransport, TransportError
else:
    from staged_real_servo_transport import ExclusiveServoTransport, TransportError


def frame(name):
    return (name + ",0,;").encode("ascii")


class FakeChannel:
    """Cooperative fake, except explicit cancellation-resistant failure test."""
    def __init__(self):
        self.session = (1, "writer-a")
        self.calls, self.aborts, self.deadlines = [], [], []
        self.block = None
        self.write_delay = self.ack_delay = 0
        self.failure = self.reply = None
        self.active = self.maximum_active = 0
        self.closed = set()

    async def exchange(self, payload, *, session, deadline):
        if session != self.session or session in self.closed:
            raise ConnectionError("wrong/revoked raw session")
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.calls.append((payload, session, time.monotonic()))
        self.deadlines.append(deadline)
        try:
            if self.block is not None:
                await self.block.wait()
            await asyncio.sleep(self.write_delay)
            await asyncio.sleep(self.ack_delay)
            if self.failure:
                raise self.failure
            return self.reply if self.reply is not None else {
                "name": payload.split(b",")[0].decode(), "ok": True}
        finally:
            self.active -= 1

    def abort(self, session):
        self.aborts.append(session)
        self.closed.add(session)


class ArbiterChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.channel = FakeChannel()
        self.transport = ExclusiveServoTransport(self.channel, max_queue=2)
        self.jobs = []
        self.patches = [patch.object(socket.socket, "connect", side_effect=AssertionError("no sockets")),
                        patch.object(socket.socket, "connect_ex", side_effect=AssertionError("no sockets")),
                        patch.object(socket, "create_connection", side_effect=AssertionError("no sockets")),
                        patch.object(asyncio, "open_connection", side_effect=AssertionError("no devices"))]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for job in self.jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*self.jobs, return_exceptions=True)
        for item in reversed(self.patches):
            item.stop()

    def job(self, name="ReadState", *, owner=None, timeout=.5):
        task = asyncio.create_task(self.transport.exchange(frame(name), owner=owner, timeout=timeout))
        self.jobs.append(task)
        return task

    async def until(self, predicate):
        for _ in range(300):
            if predicate():
                return
            await asyncio.sleep(0)
        self.fail("expected task state not reached")

    async def error(self, awaitable, outcome="not_sent"):
        with self.assertRaises(TransportError) as raised:
            await awaitable
        self.assertEqual(outcome, raised.exception.outcome)

    async def test_finite_then_servo_same_owner_idempotent_not_refcounted(self):
        t = self.transport
        self.assertTrue(t.acquire("driver"))
        await t.exchange(frame("MoveJ"), owner="driver", timeout=.2)
        self.assertTrue(t.acquire("driver"))
        await t.exchange(frame("StartServo"), owner="driver", timeout=.2)
        await t.exchange(frame("PushServoJ"), owner="driver", timeout=.2)
        await t.abort("driver")  # idle revocation preserves bound stop channel
        await t.exchange(frame("GrpStop"), owner="driver", timeout=.2)
        self.assertTrue(t.release("driver"))
        self.assertFalse(t.release("driver"))
        self.assertTrue(t.acquire("next"))
        self.assertEqual([], self.channel.aborts)

    async def test_owner_blocks_normal_query_and_other_owner(self):
        self.assertTrue(self.transport.acquire("driver"))
        for name, owner in (("ReadState", None), ("MoveJ", None),
                            ("PackageVersion", "other"), ("GrpStop", "other")):
            await self.error(self.transport.exchange(frame(name), owner=owner, timeout=.1))
        self.assertFalse(self.transport.acquire("other"))
        self.assertFalse(self.transport.release("other"))
        self.assertFalse(await self.transport.abort("other"))
        self.assertFalse(self.transport.quarantine("other", "wrong owner"))
        self.assertEqual([], self.channel.calls)

    async def test_servo_without_owner_refused(self):
        for name in ("StartServo", "PushServoJ"):
            await self.error(self.transport.exchange(frame(name), timeout=.1))
        self.assertFalse(self.transport.acquire(""))
        self.assertFalse(self.transport.acquire(None))

    async def test_fifo_and_no_parallel_raw_writes(self):
        self.channel.block = asyncio.Event()
        first = self.job("ReadA")
        await self.until(lambda: bool(self.channel.calls))
        second, third = self.job("ReadB"), self.job("MoveJ")
        await self.until(lambda: self.transport.snapshot()["queued"] == 2)
        self.assertFalse(self.transport.acquire("driver"))
        self.channel.block.set()
        await asyncio.gather(first, second, third)
        self.assertEqual([frame(n) for n in ("ReadA", "ReadB", "MoveJ")],
                         [c[0] for c in self.channel.calls])
        self.assertEqual(1, self.channel.maximum_active)

    async def test_acquire_refuses_queued_normal_before_it_starts(self):
        self.channel.block = asyncio.Event()
        self.job("ReadA")
        await self.until(lambda: self.transport.snapshot()["inflight"])
        self.job("ReadB")
        await self.until(lambda: self.transport.snapshot()["queued"] == 1)
        self.assertFalse(self.transport.acquire("driver"))

    async def test_queue_bound(self):
        self.channel.block = asyncio.Event()
        self.job()
        await self.until(lambda: bool(self.channel.calls))
        self.job("ReadB")
        self.job("ReadC")
        await self.until(lambda: self.transport.snapshot()["queued"] == 2)
        await self.error(self.transport.exchange(frame("ReadD"), timeout=.1))
        self.assertEqual(1, len(self.channel.calls))

    async def test_queue_expiry_does_not_send_or_quarantine(self):
        self.channel.block = asyncio.Event()
        active = self.job()
        await self.until(lambda: bool(self.channel.calls))
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.02))
        self.assertFalse(self.transport.quarantined)
        self.assertEqual([], self.channel.aborts)
        self.channel.block.set()
        await active
        self.assertEqual(1, len(self.channel.calls))

    async def test_queued_cancel_is_not_unknown(self):
        self.channel.block = asyncio.Event()
        active = self.job()
        await self.until(lambda: bool(self.channel.calls))
        pending = self.job("MoveJ")
        await self.until(lambda: self.transport.snapshot()["queued"] == 1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertFalse(self.transport.quarantined)
        self.assertEqual(0, self.transport.snapshot()["queued"])
        self.channel.block.set()
        await active

    async def test_one_deadline_includes_queue_write_and_ack(self):
        self.channel.block = asyncio.Event()
        first = self.job()
        await self.until(lambda: bool(self.channel.calls))
        started = time.monotonic()
        second = self.job("MoveJ", timeout=.15)
        await self.until(lambda: self.transport.snapshot()["queued"] == 1)
        await asyncio.sleep(.035)
        self.channel.block.set()
        await first
        await second
        # The deadline was computed at admission, not raw write after queueing.
        self.assertLess(self.channel.deadlines[1] - self.channel.calls[1][2], .13)
        self.assertAlmostEqual(started + .15, self.channel.deadlines[1], delta=.015)

    async def test_write_and_ack_share_budget_timeout_unknown(self):
        self.channel.write_delay = self.channel.ack_delay = .045
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.065), "unknown")
        self.assertTrue(self.transport.quarantined)
        self.assertEqual([self.transport.session], self.channel.aborts)

    async def test_queue_write_ack_together_exhaust_one_budget(self):
        self.channel.block = asyncio.Event()
        first = self.job()
        await self.until(lambda: bool(self.channel.calls))
        second = self.job("MoveJ", timeout=.085)
        await self.until(lambda: self.transport.snapshot()["queued"] == 1)
        await asyncio.sleep(.035)
        self.channel.write_delay = self.channel.ack_delay = .035
        self.channel.block.set()
        # First itself consumes 70ms after the 35ms queue delay: the second
        # expires UNSENT, not with a newly granted raw timeout.
        await self.error(second)
        await first
        self.assertEqual(1, len(self.channel.calls))
        self.assertFalse(self.transport.quarantined)

    async def test_inflight_cancel_aborts_before_return_and_latches(self):
        self.assertTrue(self.transport.acquire("driver"))
        self.channel.block = asyncio.Event()
        active = self.job("PushServoJ", owner="driver")
        await self.until(lambda: bool(self.channel.calls))
        active.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await active
        self.assertTrue(self.transport.quarantined)
        self.assertEqual([self.transport.session], self.channel.aborts)
        self.assertTrue(self.transport.release("driver"))
        self.assertFalse(self.transport.acquire("driver"))
        await self.error(self.transport.exchange(frame("PushServoJ"), timeout=.1))

    async def test_unknown_failure_never_replays_same_name_late_ack(self):
        self.channel.block = asyncio.Event()
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.02), "unknown")
        self.channel.block.set()  # late matching ACK cannot authorize next MoveJ
        await asyncio.sleep(0)
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.1))
        self.assertEqual(1, len(self.channel.calls))

    async def test_cancellation_resistant_raw_cannot_hold_arbiter_past_deadline(self):
        exited = asyncio.Event()
        release = asyncio.Event()

        async def resistant(payload, *, session, deadline):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release.wait()
                exited.set()
                raise RuntimeError("late raw failure")

        self.channel.exchange = resistant
        started = time.monotonic()
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.02), "unknown")
        self.assertLess(time.monotonic() - started, .3)
        self.assertTrue(self.transport.quarantined)
        self.assertEqual([self.transport.session], self.channel.aborts)
        release.set()
        await asyncio.wait_for(exited.wait(), .5)
        await asyncio.sleep(0)  # done callback consumes late exception

    async def test_raw_exception_closes_original(self):
        self.channel.failure = ConnectionError("read failed")
        await self.error(self.transport.exchange(frame("ReadState"), timeout=.1), "unknown")
        self.assertEqual([self.transport.session], self.channel.aborts)

    async def test_wrong_or_malformed_ack_quarantines(self):
        for reply in ({"name": "Other", "ok": True}, {"name": "MoveJ", "ok": 1}, None):
            channel = FakeChannel()
            channel.reply = reply if reply is not None else []
            transport = ExclusiveServoTransport(channel)
            await self.error(transport.exchange(frame("MoveJ"), timeout=.1), "unknown")
            self.assertTrue(transport.quarantined)

    async def test_known_negative_ack_is_not_unknown(self):
        self.channel.reply = {"name": "MoveJ", "ok": False, "err_code": 20018}
        reply = await self.transport.exchange(frame("MoveJ"), timeout=.1)
        self.assertFalse(reply["ok"])
        self.assertFalse(self.transport.quarantined)
        self.assertEqual([], self.channel.aborts)

    async def test_original_session_required_for_stop_before_send(self):
        self.assertTrue(self.transport.acquire("driver"))
        original = self.channel.session
        self.channel.session = (2, "writer-b")
        await self.error(self.transport.exchange(frame("GrpStop"), owner="driver", timeout=.1))
        self.assertEqual([], self.channel.calls)
        self.assertEqual([original], self.channel.aborts)
        self.assertNotIn(self.channel.session, self.channel.closed)

    async def test_session_changed_during_stop_ack_is_unknown(self):
        self.assertTrue(self.transport.acquire("driver"))
        self.channel.block = asyncio.Event()
        active = self.job("GrpStop", owner="driver")
        await self.until(lambda: bool(self.channel.calls))
        original = self.channel.session
        self.channel.session = (2, "writer-b")
        self.channel.block.set()
        await self.error(active, "unknown")
        self.assertEqual([original], self.channel.aborts)

    async def test_abort_idle_revokes_queued_but_preserves_original_stop(self):
        self.assertTrue(self.transport.acquire("driver"))
        # Queue task is waiting for the arbiter but raw exchange hasn't started.
        self.transport._active = object()
        queued = self.job("PushServoJ", owner="driver")
        await self.until(lambda: self.transport.snapshot()["queued"] == 1)
        self.transport._active = None
        await self.transport.abort("driver")
        await self.error(queued)
        await self.transport.exchange(frame("GrpStop"), owner="driver", timeout=.1)
        self.assertEqual([frame("GrpStop")], [v[0] for v in self.channel.calls])
        self.assertEqual([], self.channel.aborts)

    async def test_abort_inflight_quarantines_queued_and_blocks_stop(self):
        self.assertTrue(self.transport.acquire("driver"))
        self.channel.block = asyncio.Event()
        active = self.job("PushServoJ", owner="driver")
        await self.until(lambda: bool(self.channel.calls))
        queued = self.job("PushServoJ", owner="driver")
        await self.until(lambda: self.transport.snapshot()["queued"] == 1)
        await self.transport.abort("driver")
        await asyncio.gather(active, return_exceptions=True)
        await self.error(queued)
        await self.error(self.transport.exchange(frame("GrpStop"), owner="driver", timeout=.1))
        self.assertEqual(1, len(self.channel.calls))

    async def test_release_refuses_inflight_and_queued(self):
        self.assertTrue(self.transport.acquire("driver"))
        self.channel.block = asyncio.Event()
        active = self.job("MoveJ", owner="driver")
        await self.until(lambda: bool(self.channel.calls))
        self.assertFalse(self.transport.release("driver"))
        self.assertTrue(self.transport.acquire("driver"))
        self.channel.block.set()
        await active
        self.assertTrue(self.transport.release("driver"))

    async def test_quarantine_release_never_unlocks_even_changed_session(self):
        self.assertTrue(self.transport.acquire("driver"))
        self.assertTrue(self.transport.quarantine("driver", "stop not confirmed"))
        self.assertTrue(self.transport.release("driver"))
        self.channel.session = (2, "writer-b")
        self.assertFalse(self.transport.acquire("next"))
        await self.error(self.transport.exchange(frame("ReadState"), timeout=.1))
        self.assertEqual("stop not confirmed", self.transport.reason)
        self.assertFalse(self.transport.snapshot()["physical_stop_confirmed"])

    async def test_bad_payloads_and_timeouts_never_send(self):
        for payload in (b"MoveJ,0,;MoveJ,0,;", b"MoveJ,0,;\n", b"MoveJ", "MoveJ,0,;",
                        b"MoveJ,\x00,;", b"1MoveJ,0,;", b"MoveJ," + b"a" * 8192 + b",;"):
            await self.error(self.transport.exchange(payload, timeout=.1))
        for timeout in (0, -1, True, float("nan"), float("inf"), None):
            await self.error(self.transport.exchange(frame("ReadState"), timeout=timeout))
        self.assertEqual([], self.channel.calls)

    async def test_synchronous_raw_start_failure_is_unknown(self):
        def broken(*args, **kwargs):
            raise RuntimeError("write failed before returning coroutine")
        self.channel.exchange = broken
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.1), "unknown")
        self.assertTrue(self.transport.quarantined)

    async def test_abort_failure_is_latched_not_suppressed_as_success(self):
        def broken(session):
            raise RuntimeError("close failed")
        self.channel.abort = broken
        self.channel.failure = ConnectionError("failed")
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.1), "unknown")
        self.assertIn("abort failed", self.transport.reason)
        await self.error(self.transport.exchange(frame("ReadState"), timeout=.1))

    async def test_async_raw_abort_is_contract_violation_and_never_unlocks(self):
        async def wrong_abort(session):
            raise AssertionError("must never await/schedule an async raw abort")
        self.channel.abort = wrong_abort
        self.channel.failure = ConnectionError("failed")
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.1), "unknown")
        self.assertIn("synchronous contract", self.transport.reason)
        await self.error(self.transport.exchange(frame("ReadState"), timeout=.1))

    async def test_synchronous_abort_return_value_does_not_change_semantics(self):
        original = self.channel.abort
        def acknowledged_abort(session):
            original(session)
            return True
        self.channel.abort = acknowledged_abort
        self.channel.failure = ConnectionError("failed")
        await self.error(self.transport.exchange(frame("MoveJ"), timeout=.1), "unknown")
        self.assertNotIn("synchronous contract", self.transport.reason)
        self.assertTrue(self.transport.quarantined)

    async def test_normal_queries_resume_after_single_idle_release(self):
        self.assertTrue(self.transport.acquire("driver"))
        self.assertTrue(self.transport.acquire("driver"))
        self.assertTrue(self.transport.release("driver"))
        reply = await self.transport.exchange(frame("PackageVersion"), timeout=.1)
        self.assertTrue(reply["ok"])

    async def test_late_ack_with_fake_monotonic_deadline_rejected(self):
        now = [100.]
        transport = ExclusiveServoTransport(self.channel, clock=lambda: now[0])
        original = self.channel.exchange
        async def late(*args, **kwargs):
            reply = await original(*args, **kwargs)
            now[0] += 1
            return reply
        self.channel.exchange = late
        await self.error(transport.exchange(frame("ReadState"), timeout=.1), "unknown")
        self.assertTrue(transport.quarantined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
