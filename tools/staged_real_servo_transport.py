"""Offline staged, no-socket exclusive command arbiter (NOT a RealLink adapter).

Integration contract
--------------------
Run on ONE asyncio event loop. Inject an already-established single raw channel:

* ``channel.session``: non-None opaque identity, NEW identity on each command
  connection/writer. It must never be reused, even after reconnecting to one IP.
* ``async channel.exchange(payload, *, session, deadline) -> dict``: one raw
  write/drain/ACK transaction, NO queue/retry/reconnect. Honor the absolute
  monotonic deadline. Return ``{name: str, ok: bool, ...}`` only for a complete
  matching ACK. Preserve one reader; reject unsolicited/partial/late replies.
* ``channel.abort(session)``: SYNCHRONOUS, nonblocking revocation of that exact
  session: prohibit further writes and close/discard its pending reads/buffers.
  It MUST work even if exchange ignores task cancellation. Never touch a newer
  session or reconnect. Closing a channel is NOT confirmation of physical stop.

ALL commands including identity probes, jog heartbeat, queries, finite motion,
and stops must enter ``exchange`` here. The existing RealLink _cmd_loop,
_roundtrip, auto-reconnect and direct probe/heartbeat paths are unarbitrated;
disable/replace them before integration. Wrapping RealLink.ask is NOT valid.

Host: acquire(driver.owner), finite executor uses exchange(owner=driver.owner),
then driver.start() reacquires SAME owner idempotently; no release between them.
Release is single-state, not refcounted. Host owns physical stop confirmation,
motion authorization, command/argument validation, tool/path/feedback checking.
An ACK or release is not evidence of a safe/stationary arm. No reset/unquarantine
API exists: a replacement requires external reconciliation, never auto-restart.

Normal traffic uses owner=None. Servo commands require a held exclusive owner.
One bounded FIFO serves all admitted traffic. Timeout includes admission/queue,
write/drain and ACK. Unsent queue timeout/cancel is not_sent and leaves the raw
channel intact; once raw exchange starts EVERY uncertain outcome aborts and
quarantines permanently (including cancellation). No replay, even same-name ACK.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import inspect
import math
import re
import time


class TransportError(RuntimeError):
    def __init__(self, reason, *, outcome="not_sent"):
        super().__init__(reason)
        self.outcome = outcome


@dataclass(eq=False)
class _Ticket:
    generation: int


class ExclusiveServoTransport:
    """RealServoSession transport contract; no device discovery or socket code.

    acquire/release/quarantine are synchronous; exchange/abort are async.
    Cancellation propagates CancelledError; snapshot reports the latched unknown
    outcome. Failed admission raises TransportError(outcome='not_sent').
    """
    def __init__(self, channel, *, max_queue=32, max_payload=8192,
                 clock=time.monotonic):
        if (type(max_queue) is not int or not 1 <= max_queue <= 1024
                or type(max_payload) is not int or not 16 <= max_payload <= 65536):
            raise ValueError("Invalid bounded queue/payload capacity")
        if (not callable(getattr(channel, "exchange", None))
                or not callable(getattr(channel, "abort", None))):
            raise ValueError("Raw channel requires exchange and synchronous abort")
        self.channel, self.clock = channel, clock
        self.session = channel.session
        if self.session is None:
            raise ValueError("Raw command session must already exist")
        self.max_queue, self.max_payload = max_queue, max_payload
        self.owner = None
        self.quarantined, self.reason = False, ""
        self._queue = deque()
        self._active = self._raw_task = None
        self._generation = 0
        self._changed = asyncio.Event()
        self._loop = None
        self._aborted = False

    def _on_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if loop is not self._loop:
            raise RuntimeError("Arbiter is bound to one asyncio event loop")

    def _session_current(self):
        try:
            return self.channel.session == self.session
        except Exception:
            return False

    def _notify(self):
        previous, self._changed = self._changed, asyncio.Event()
        previous.set()

    @staticmethod
    def _consume(task):
        # A broken injected channel may ignore cancellation; never await it
        # beyond the deadline, nor let its eventual exception go unobserved.
        if not task.cancelled():
            task.exception()

    def _fault(self, reason):
        self.quarantined = True
        if not self.reason:
            self.reason = str(reason)
        self._generation += 1
        self._queue.clear()
        self._notify()
        if not self._aborted:
            self._aborted = True
            try:
                result = self.channel.abort(self.session)
                if inspect.isawaitable(result):
                    # An async abort cannot provide the immediate revocation
                    # guarantee; do not silently schedule an unsafe cleanup.
                    if asyncio.iscoroutine(result):
                        result.close()
                    elif isinstance(result, asyncio.Future):
                        result.cancel()
                    self.reason += "; raw abort violated synchronous contract"
            except Exception as exc:
                self.reason += "; raw abort failed: " + type(exc).__name__
        if self._raw_task is not None and not self._raw_task.done():
            self._raw_task.cancel()

    def _healthy(self):
        if not self.quarantined and not self._session_current():
            self._fault("Original command session changed; no replay or rebound stop")
        return not self.quarantined

    def acquire(self, owner):
        self._on_loop()
        if not isinstance(owner, str) or not owner or not self._healthy():
            return False
        if self.owner == owner:
            return True  # finite startup -> Servo transition, no refcount
        if self.owner is not None or self._active is not None or self._queue:
            return False
        self.owner = owner
        self._notify()
        return True

    def release(self, owner):
        self._on_loop()
        if self.owner is None or owner != self.owner:
            return False
        if self._active is not None or self._queue:
            return False  # never hand a live/queued transaction to another owner
        self.owner = None
        self._notify()
        return True  # deliberately does NOT clear quarantine

    def quarantine(self, owner, reason):
        self._on_loop()
        if self.owner is None or owner != self.owner:
            return False
        self._fault(reason or "Physical stop unknown")
        return True

    async def abort(self, owner):
        """Revoke queued intentions; close/quarantine only if raw IO is unknown.

        Idle abort keeps the ORIGINAL channel for a subsequent bound GrpStop.
        Inflight abort cannot promise GrpStop delivery: channel is quarantined.
        """
        self._on_loop()
        if self.owner is None or owner != self.owner:
            return False
        self._generation += 1
        self._queue.clear()
        self._notify()
        if self._active is not None:
            self._fault("Inflight command aborted; physical result unknown")
        else:
            self._healthy()
        return True

    def snapshot(self):
        return {"owned": self.owner is not None, "queued": len(self._queue),
                "inflight": self._active is not None,
                "quarantined": self.quarantined, "reason": self.reason,
                "automatic_restart": False, "physical_stop_confirmed": False}

    def _name(self, payload):
        if (type(payload) is not bytes or not 3 <= len(payload) <= self.max_payload
                or not payload.endswith(b",;") or payload.count(b";") != 1
                or any(c < 32 or c > 126 for c in payload)):
            raise TransportError("Exactly one bounded ASCII command frame required")
        name = payload.split(b",", 1)[0]
        if re.fullmatch(rb"[A-Za-z][A-Za-z0-9_]*", name) is None:
            raise TransportError("Invalid command name")
        return name.decode("ascii")

    def _admitted(self, owner, name):
        if not self._healthy():
            raise TransportError("Channel quarantined: " + self.reason)
        if owner != self.owner:
            raise TransportError("Exclusive owner mismatch; command/query not admitted")
        if name in ("StartServo", "PushServoJ") and self.owner is None:
            raise TransportError("Servo requires exclusive acquisition")

    async def exchange(self, payload, *, owner=None, timeout):
        self._on_loop()
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise TransportError("Positive finite timeout required")
        deadline = self.clock() + timeout  # BEFORE queue/admission, never renewed
        name = self._name(payload)
        self._admitted(owner, name)
        if len(self._queue) >= self.max_queue:
            raise TransportError("Bounded command queue full")
        ticket = _Ticket(self._generation)
        self._queue.append(ticket)
        try:
            while True:
                self._admitted(owner, name)
                if ticket.generation != self._generation:
                    raise TransportError("Queued intention revoked")
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise TransportError("Deadline expired before raw write")
                if self._active is None and self._queue and self._queue[0] is ticket:
                    self._queue.popleft()
                    self._active = ticket
                    break
                event = self._changed
                try:
                    await asyncio.wait_for(event.wait(), remaining)
                except TimeoutError as exc:
                    raise TransportError("Queue deadline expired; not sent") from exc

            # No await between final authorization and scheduling the raw IO.
            # Mark uncertainty before entering channel code (write may be sync).
            try:
                task = asyncio.create_task(self.channel.exchange(
                    payload, session=self.session, deadline=deadline))
                self._raw_task = task
                task.add_done_callback(self._consume)
                done, _ = await asyncio.wait({task}, timeout=max(0., deadline-self.clock()))
                if task not in done:
                    raise TimeoutError("Raw write/drain/ACK exceeded absolute deadline")
                reply = task.result()
                if (self.clock() >= deadline or ticket.generation != self._generation
                        or not self._healthy()):
                    raise RuntimeError("Late/revoked/or different-session acknowledgement")
                if (not isinstance(reply, dict) or reply.get("name") != name
                        or type(reply.get("ok")) is not bool):
                    raise RuntimeError("Missing or unmatched raw acknowledgement")
                return dict(reply)  # explicit negative ACK is known, not unknown
            except asyncio.CancelledError:
                self._fault("Inflight caller/raw task cancelled; command result unknown")
                raise
            except Exception as exc:
                self._fault(str(exc))
                raise TransportError(self.reason, outcome="unknown") from exc
        finally:
            if ticket in self._queue:
                self._queue.remove(ticket)
            if self._active is ticket:
                # Do not erase the task handle while it may ignore cancellation.
                self._active = None
                if self._raw_task is not None and self._raw_task.done():
                    self._raw_task = None
            self._notify()


# Descriptive alias for host code; same implementation and ownership state.
ServoTransportArbiter = ExclusiveServoTransport
