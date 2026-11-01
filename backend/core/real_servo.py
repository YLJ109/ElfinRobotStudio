"""Commissioning-only ServoJ driver. No sockets, auto-connect or HTTP unlock.

The host supplies a commissioned transport, fresh measured feedback and a
whole-path validator. This driver is NOT wired to live World motion until
controller timing, tool/load and stopping behaviour have been qualified.
V6 ASCII: StartServo(id, period_s, lookahead_s), PushServoJ(id, six degrees).
Camera inference may run freely; command production is fixed-period/latest-only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import secrets
import time

from .protocol import encode


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _six(values):
    if not isinstance(values, (list, tuple)) or len(values) != 6 or not all(map(_finite, values)):
        raise ValueError("需要六个有限关节角/参数")
    return tuple(float(v) for v in values)


@dataclass(frozen=True)
class ServoProfile:
    """All timing/limits come from commissioning; deliberately no live defaults.

    Evidence strings identify external records, not proof by themselves. This
    class is an internal boundary, never a browser-provided unlock structure.
    Values used by unit tests are test fixtures, not device recommendations.
    """
    period_s: float
    lookahead_s: float
    reply_timeout_s: float
    feedback_max_age_s: float
    target_max_age_s: float
    stop_timeout_s: float
    tracking_error_deg: float
    start_velocity_deg_s: float
    lower: tuple
    upper: tuple
    velocity: tuple
    acceleration: tuple
    evidence: tuple  # firmware, tool/load, stop response, swept path/timing

    def __post_init__(self):
        times = (self.period_s, self.lookahead_s, self.reply_timeout_s,
                 self.feedback_max_age_s, self.target_max_age_s, self.stop_timeout_s,
                 self.tracking_error_deg, self.start_velocity_deg_s)
        if not all(_finite(v) and v > 0 for v in times):
            raise ValueError("随动时序与误差参数必须是有限正数")
        # This driver supports the documented cycle mode only, not stiffness.
        if not 0 < self.reply_timeout_s < self.period_s < 1:
            raise ValueError("仅支持周期模式，回执期限须短于周期")
        if self.target_max_age_s > .3:
            raise ValueError("动作目标期限不得超过300ms")
        for name in ("lower", "upper", "velocity", "acceleration"):
            object.__setattr__(self, name, _six(getattr(self, name)))
        if any(lo >= hi for lo, hi in zip(self.lower, self.upper)):
            raise ValueError("关节上下限无效")
        if any(v <= 0 for v in self.velocity + self.acceleration):
            raise ValueError("速度和加速度必须为正数")
        if (type(self.evidence) is not tuple or len(self.evidence) != 4
                or any(not isinstance(v, str) or not v.strip() for v in self.evidence)):
            raise ValueError("缺少固件、工具负载、停止及路径时序的现场验证记录")


class RealServoSession:
    """Exclusive fixed-period producer; every fault latches until a new session.

    transport must implement acquire(owner), release(owner), exchange(bytes,
    owner=..., timeout=...) -> {name, ok}, and abort(owner). abort MUST discard
    queued writes and close an indeterminate in-flight command channel; it must
    never reconnect/replay. A stop is an attempt, not proof of a physical stop.
    quarantine(owner, reason) must latch the arbiter unavailable when physical
    stopping is unknown; releasing ownership must not unlock that fault.

    read_feedback() returns connection, context, stamp (monotonic capture time),
    frame_seq, q, velocity, enabled, safety_ok, idle and motion_allowed. Fresh
    copies must not renew the original capture timestamp. validate_path(a,b)
    must verify the actual transition/entire moving arm and active TCP/workbox.
    It is not enough to check only the target or an unconstrained TCP.
    """
    def __init__(self, transport, read_feedback, validate_path, profile, *,
                 clock=time.monotonic, sleep=asyncio.sleep):
        if not isinstance(profile, ServoProfile) or not callable(validate_path):
            raise ValueError("需要现场参数及路径检查器")
        if any(not callable(getattr(transport,name,None)) for name in
               ("acquire", "release", "exchange", "abort", "quarantine")):
            raise ValueError("传输缺少独占、撤销或故障锁定接口")
        self.transport, self.read_feedback = transport, read_feedback
        self.validate_path, self.profile = validate_path, profile
        self.clock, self.sleep = clock, sleep
        self.owner = secrets.token_urlsafe(24)
        self.state, self.reason = "idle", ""
        self.connection = self.context = None
        self.last_q = self.velocity = self.target = None
        self.sequence, self.frame_seq = -1, -1
        self._feedback_stamp = None
        self.next_due = None
        self.last_sent_at = self.last_interval_s = None
        self.lateness_s = 0.
        self.overruns = 0
        self.stop_confirmed = False
        self._owned = self._entered = False
        self._io = asyncio.Lock()
        self._generation = 0
        self._halt_task = None

    def snapshot(self):
        return {"state": self.state, "reason": self.reason,
                "stop_confirmed": self.stop_confirmed, "seq": self.sequence,
                "q_command": list(self.last_q) if self.last_q else None,
                "interval_s": self.last_interval_s, "lateness_s": self.lateness_s,
                "overruns": self.overruns,
                "automatic_restart": False}

    def _feedback(self, *, starting=False):
        data, now = self.read_feedback(), self.clock()
        if not isinstance(data, dict):
            raise ValueError("缺少真实反馈")
        stamp, seq = data.get("stamp"), data.get("frame_seq")
        if (not _finite(stamp) or not 0 <= now - stamp <= self.profile.feedback_max_age_s
                or type(seq) is not int or seq < self.frame_seq):
            raise ValueError("真实反馈过期或倒序")
        if (self._feedback_stamp is not None and
                (stamp < self._feedback_stamp or (seq == self.frame_seq and stamp != self._feedback_stamp))):
            raise ValueError("反馈时间倒序或旧帧时间被刷新")
        if data.get("connection") is None or not data.get("context"):
            raise ValueError("缺少连接及工具/工位绑定")
        if self.connection is not None and (data["connection"] != self.connection or data["context"] != self.context):
            raise ValueError("连接、工具或工位已变化")
        q, velocity = _six(data.get("q")), _six(data.get("velocity"))
        if any(not lo <= v <= hi for v, lo, hi in zip(q, self.profile.lower, self.profile.upper)):
            raise ValueError("实际关节已越限")
        if data.get("enabled") is not True or data.get("motion_allowed") is not True or data.get("safety_ok") is not True:
            raise ValueError("使能、控制器许可或安全状态无效")
        if starting and (data.get("idle") is not True or
                         max(map(abs, velocity)) > self.profile.start_velocity_deg_s):
            raise ValueError("开始前必须确认真机停稳")
        self.frame_seq = seq
        self._feedback_stamp = stamp
        return data, q

    async def _exchange(self, name, *params, timeout=None):
        deadline = self.clock()+(timeout or self.profile.reply_timeout_s)
        rep = await asyncio.wait_for(self.transport.exchange(encode(name, *params),
            owner=self.owner, timeout=timeout or self.profile.reply_timeout_s),
            timeout=timeout or self.profile.reply_timeout_s)
        if self.clock() > deadline:
            raise TimeoutError(f"{name} 回执已超期")
        if not isinstance(rep, dict) or rep.get("name") != name or rep.get("ok") is not True:
            raise RuntimeError(f"{name} 回执失败或结果未知")

    async def start(self):
        async with self._io:
            if self.state != "idle":
                return False
            try:
                data, q = self._feedback(starting=True)
                if self.validate_path(q, q) is not True:
                    raise ValueError("起点安全检查未通过")
                if self.transport.acquire(self.owner) is not True:
                    raise ValueError("运动通道已有所有者")
                self._owned = True
                self.connection, self.context = data["connection"], data["context"]
                self.state = "starting"
                self.last_q, self.velocity = q, (0.,) * 6
                generation = self._generation
                # StartServo may have taken effect even if its acknowledgement
                # is lost; from this point onwards all failures attempt a stop.
                self._entered = True
                await self._exchange("StartServo", 0, self.profile.period_s, self.profile.lookahead_s)
                await self.sleep(0)  # give already-requested stop/cancel a turn
                if generation != self._generation:
                    raise RuntimeError("启动已被停止请求撤销")
                _, actual = self._feedback(starting=True)
                if max(abs(a-b) for a,b in zip(actual,q)) > self.profile.tracking_error_deg:
                    raise ValueError("启动期间实际位置已变化")
                # First packet is measured hold, never a camera target/center jump.
                sent_at = self.clock()
                await self._exchange("PushServoJ", 0, *q)
                await self.sleep(0)
                if generation != self._generation:
                    raise RuntimeError("启动已被停止请求撤销")
                self.last_sent_at = sent_at
                self.state, self.next_due = "running", sent_at + self.profile.period_s
                return True
            except asyncio.CancelledError:
                await self._halt("启动被取消")
                raise
            except Exception as exc:
                await self._halt(str(exc))
                return False

    def publish(self, q, *, token, seq, captured_at):
        """Accept backend-solved joints only, never unauthenticated browser IK.

        Replacing the one mailbox entry does not alter source capture time.
        Lost hand/heartbeat must NOT be published as a fresh motion target.
        """
        if self.state != "running" or token != self.owner:
            return False
        now = self.clock()
        if (type(seq) is not int or seq <= self.sequence or not _finite(captured_at)
                or not 0 <= now - captured_at <= self.profile.target_max_age_s
                or (self.target is not None and captured_at < self.target[1])):
            return False
        try:
            q = _six(q)
        except ValueError:
            return False
        if any(not lo <= v <= hi for v,lo,hi in zip(q,self.profile.lower,self.profile.upper)):
            return False
        self.target = (q, float(captured_at))
        self.sequence = seq
        return True

    def _step(self, goal):
        # Bounded position servo: brake early, limit acceleration and velocity.
        # This is not controller trajectory/jerk equivalence certification.
        dt, p = self.profile.period_s, self.profile
        q, velocity = [], []
        for x, v, end, vmax, amax in zip(self.last_q,self.velocity,goal,p.velocity,p.acceleration):
            error = end-x
            desired = math.copysign(min(vmax, math.sqrt(2*amax*abs(error))),error) if error else 0.
            dv = max(-amax*dt, min(amax*dt, desired-v))
            nv = max(-vmax, min(vmax, v+dv))
            nx = x+nv*dt
            # Do not clamp to end: such a snap would bypass acceleration limits.
            q.append(nx)
            velocity.append(nv)
        return tuple(q), tuple(velocity)

    async def tick(self):
        async with self._io:
            if self.state != "running":
                return False
            p, now = self.profile, self.clock()
            if now < self.next_due:
                return False
            try:
                if now-self.next_due >= p.period_s:
                    self.overruns += 1
                    raise ValueError("控制周期超时，不补发积压指令")
                validated, actual = self._feedback()
                if self.target is None or not 0 <= now-self.target[1] <= p.target_max_age_s:
                    raise ValueError("视觉目标过期或丢掌，停止随动")
                if max(abs(a-b) for a,b in zip(actual,self.last_q)) > p.tracking_error_deg:
                    raise ValueError("实际跟踪偏差超限")
                q, velocity = self._step(self.target[0])
                if any(not lo <= v <= hi for v,lo,hi in zip(q,p.lower,p.upper)):
                    raise ValueError("下一步将越过关节限位")
                # Check both the measured-to-command transition and the planned
                # command chord, not only a point in the TCP box.
                if (self.validate_path(actual,q) is not True or
                        self.validate_path(self.last_q,q) is not True):
                    raise ValueError("下一步路径安全检查未通过")
                # A slow IK/collision callback must not cause a late command.
                checked_at = self.clock()
                if (checked_at-self.next_due >= p.period_s or
                        checked_at-self.target[1] > p.target_max_age_s):
                    self.overruns += 1
                    raise ValueError("路径计算或目标已超时")
                latest, latest_q = self._feedback()
                if latest['frame_seq'] != validated['frame_seq'] or latest_q != actual:
                    raise ValueError("路径检查期间反馈变化，不能使用旧起点发令")
                generation = self._generation
                sent_at = self.clock()
                self.lateness_s = max(0., sent_at-self.next_due)
                self.last_interval_s = sent_at-self.last_sent_at
                await self._exchange("PushServoJ", 0, *q)
                await self.sleep(0)
                if generation != self._generation:
                    raise RuntimeError("随动已被停止请求撤销")
                self._feedback()
                self.last_q, self.velocity = q, velocity
                # ACK latency must not add to every period. Slow validation may
                # shift this send, but never shorten the next actual interval.
                self.last_sent_at = sent_at
                self.next_due = sent_at+p.period_s
                return True
            except asyncio.CancelledError:
                await self._halt("控制任务被取消")
                raise
            except Exception as exc:
                await self._halt(str(exc))
                return False

    async def run(self):
        try:
            while self.state == "running":
                await self.sleep(max(0., self.next_due-self.clock()))
                await self.tick()
        except asyncio.CancelledError:
            await self.stop("控制任务被取消")
            raise

    async def stop(self, reason="人工停止"):
        # Invalidate a start/push across an await immediately, then serialize
        # stopping after that bounded transaction has returned/timed out.
        self._generation += 1
        async with self._io:
            if self.state in ("stopped", "fault"):
                return self.snapshot()
            await self._halt(reason)
            return self.snapshot()

    async def _halt(self, reason):
        # The bounded cleanup has its own task: cancelling the caller cannot
        # cancel abort/stop/confirmation halfway and release a moving channel.
        if self._halt_task is None:
            self._halt_task = asyncio.create_task(self._halt_body(reason))
        cancelled = False
        while not self._halt_task.done():
            try:
                await asyncio.shield(self._halt_task)
            except asyncio.CancelledError:
                cancelled = True
        self._halt_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _halt_body(self, reason):
        self.state, self.reason, self.target = "stopping", reason, None
        self.stop_confirmed = False
        try:
            if self._owned:
                # Revoke unsent targets; indeterminate writes must not replay.
                await asyncio.wait_for(self.transport.abort(self.owner), self.profile.stop_timeout_s)
            if self._entered:
                # A changed connection cannot be used to stop a different arm.
                data = self.read_feedback()
                if data.get("connection") != self.connection:
                    raise RuntimeError("连接已变化，停止状态未知；需现场确认")
                await self._exchange("GrpStop", 0, timeout=self.profile.stop_timeout_s)
                ack_at = self.clock()
                # Discard everything captured during/before the stop request or
                # ACK. Only a later, monotonically progressing capture confirms.
                barrier = self.read_feedback()
                after_seq = barrier.get("frame_seq", -1)
                until = self.clock()+self.profile.stop_timeout_s
                while self.clock() < until:
                    data = self.read_feedback()
                    stamp, seq = data.get("stamp"), data.get("frame_seq")
                    if (data.get("connection") == self.connection and type(seq) is int and seq > after_seq
                            and _finite(stamp) and stamp > ack_at
                            and (self._feedback_stamp is None or stamp >= self._feedback_stamp)
                            and 0 <= self.clock()-stamp <= self.profile.feedback_max_age_s
                            and data.get("idle") is True
                            and max(map(abs,_six(data.get("velocity")))) <= self.profile.start_velocity_deg_s):
                        self.stop_confirmed = True
                        break
                    await self.sleep(min(.01,self.profile.period_s))
                if not self.stop_confirmed:
                    raise RuntimeError("未收到新的停稳反馈；需现场确认停止")
            self.state = "stopped" if self.stop_confirmed else "fault"
        except asyncio.CancelledError:
            self.state = "fault"
            self.reason += "；停止清理被关闭，实际停止未知"
            raise
        except Exception as exc:
            self.state = "fault"
            self.reason += "；"+str(exc)
        finally:
            if self._owned:
                if self._entered and not self.stop_confirmed:
                    self.transport.quarantine(self.owner,self.reason)
                self.transport.release(self.owner)
                self._owned = False


def real_follow_readiness(*, connected=False):
    """Read-only commissioning status. No settings/checkbox can enable motion."""
    return {"available": False, "motion_enabled": False,
            "stage": "commissioning_required", "driver": "StartServo/PushServoJ",
            "connected": bool(connected), "automatic_start": False,
            "missing": ["控制器版本与Servo周期/命令口资格", "实际TCP、工具/负载与整臂路径",
                        "现场停止响应、断流及跟踪误差验证"],
            "why": "真机随动尚需现场验证，未开放运动"}
