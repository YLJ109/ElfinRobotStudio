"""仿真机器人：按有效运动时间进行关节同步插值，支持暂停和实时倍率。

速度上限来源可切换（官方 URDF 90°/s vs 手册 E05 的 180/200°/s），
切换会在日志里记一条，因为它直接决定"仿真看起来比真机快还是慢"。
"""

from __future__ import annotations

import math
import time
from .trajectory import JointTrajectory, relative_route, vector


class SimRobot:
    def __init__(self, model, vmax_map):
        self.model = model
        self.q = [0.0] * len(model.names)
        self.vmax = list(vmax_map)
        self.traj = None            # (start, target, dur, t0)
        self.jog = None              # (axis, direction) 手动连动
        self.paused = False
        self.speed_pct = 10.0
        self.lower = list(model.lower)      # 可由 World 切换成控制器软限位
        self.upper = list(model.upper)
        self._elapsed = 0.0
        self._initial_speed = self.speed_pct

    def set_vmax(self, vmax_map, source):
        self.vmax = list(vmax_map)
        self.vel_source = source

    def stop(self):
        self.traj = None
        self.jog = None
        self.paused = False

    def move_j(self, target, blend=0.0, *, contract="synchronized"):
        """关节空间同步运动：所有轴同时到达，耗时由最慢的那根决定。"""
        if contract not in ("synchronized", "legacy_relj"):
            raise ValueError("未知仿真路径契约")
        if contract == "legacy_relj":
            if blend != 0:
                raise ValueError("兼容逐轴预演不支持blend")
            vector(target)
            if any(not lo <= v <= hi for v, lo, hi in zip(target, self.lower, self.upper)):
                raise ValueError("目标超出关节限位")
            curve = JointTrajectory.build(relative_route(self.q, target), self.vmax,
                                          self.speed_pct, stop_at_knots=True)
            return self._start_curve(curve)
        if len(target) != len(self.q):
            raise ValueError("目标点数不对")
        for i, (t, lo, up) in enumerate(zip(target, self.lower, self.upper)):
            if not (lo - 1e-6 <= t <= up + 1e-6):
                raise ValueError(f"J{i + 1} 目标 {t:.3f}° 超出限位 [{lo:.1f}, {up:.1f}]")
        dur = 0.0
        for i in range(len(self.q)):
            d = abs(target[i] - self.q[i])
            v = max(self.vmax[i] * self.speed_pct / 100.0, 1e-6)
            dur = max(dur, d / v)
        # Quintic time scaling has peak ds/dt = 1.875. Scale duration so
        # smooth acceleration/deceleration never exceeds the configured vmax.
        dur = max(dur * 1.875, 0.05)
        self.traj = (list(self.q), list(target), dur, time.monotonic())
        self._elapsed = 0.0
        self._initial_speed = self.speed_pct
        self.jog = None
        return dur

    def _start_curve(self, curve):
        if tuple(self.q) != curve.points[0]:
            raise ValueError("轨迹起点已变化；必须重新预检")
        if tuple(self.vmax) != curve.vmax or self.speed_pct != curve.speed_pct:
            raise ValueError("速度配置已变化；必须重新预检")
        if any(not lo <= v <= hi for p in curve.points for v, lo, hi in zip(p, self.lower, self.upper)):
            raise ValueError("轨迹超出当前限位")
        self.traj = curve
        self._elapsed = 0.
        self._initial_speed = self.speed_pct
        self.jog = None
        return curve.duration

    def move_prepared(self, prepared):
        """Sim-only sink; caller MUST revalidate captured context and ownership.

        Consume only planning.prepare_movel's already inspected curve. No IK,
        rebuilding, corner shortcut or unsafe fallback occurs during execution.
        """
        if self.traj is not None or self.jog is not None:
            raise ValueError("已有运动所有者；禁止替换预检轨迹")
        if prepared.report["risk_detected"]:
            raise ValueError("红色预检禁止执行")
        if tuple(self.lower) != prepared.lower or tuple(self.upper) != prepared.upper:
            raise ValueError("限位配置已变化；必须重新预检")
        return self._start_curve(prepared.trajectory)

    def start_jog(self, axis, direction):
        self.jog = (axis, direction)
        self.traj = None

    def end_jog(self):
        self.jog = None

    def tick(self, dt):
        """推进物理状态，返回是否仍在运动。"""
        if self.paused:
            return self.traj is not None
        if self.traj:
            if isinstance(self.traj, JointTrajectory):
                # Speed override can slow or freeze; never exceed the bounds
                # used in preflight, even if a caller later requests >100%.
                if not math.isfinite(self.speed_pct) or self.speed_pct < 0 or not math.isfinite(dt) or dt < 0:
                    raise ValueError("无效的仿真时间/倍率")
                pct = min(self.speed_pct, 100.)
                self._elapsed += dt*pct/self._initial_speed
                self.q = self.traj.at(self._elapsed)
                if self._elapsed >= self.traj.duration:
                    self.q = list(self.traj.points[-1])
                    self.traj = None
                    return False
                return True
            start, target, dur, t0 = self.traj
            self._elapsed += max(0.0, dt) * self.speed_pct / max(self._initial_speed, 1e-6)
            el = self._elapsed
            k = min(1.0, el / dur) if dur else 1.0
            s = k*k*k*(10.0 + k*(-15.0 + 6.0*k))
            for i in range(len(self.q)):
                self.q[i] = start[i] + (target[i] - start[i]) * s
            if k >= 1.0:
                self.q = list(target)
                self.traj = None
                return False
            return True
        if self.jog:
            axis, direction = self.jog
            v = self.vmax[axis] * self.speed_pct / 100.0
            nxt = self.q[axis] + (v if direction else -v) * dt
            lo, up = self.lower[axis], self.upper[axis]
            self.q[axis] = max(lo, min(up, nxt))
            return True
        return False

    def progress(self):
        if not self.traj:
            return 1.0
        dur = self.traj.duration if isinstance(self.traj, JointTrajectory) else self.traj[2]
        return min(1.0, self._elapsed / dur) if dur else 1.0

    def remaining(self):
        if not self.traj:
            return 0.0
        dur = self.traj.duration if isinstance(self.traj, JointTrajectory) else self.traj[2]
        return max(0.0, dur - self._elapsed) * self._initial_speed / max(self.speed_pct, 1e-6)

    def state(self):
        moving = self.traj is not None or self.jog is not None
        return {"q": [round(v, 3) for v in self.q], "moving": int(moving),
                "inpos": int(not moving), "paused": int(self.paused)}
