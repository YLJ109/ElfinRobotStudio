"""仿真机器人：按有效运动时间进行关节同步插值，支持暂停和实时倍率。

速度上限来源可切换（官方 URDF 90°/s vs 手册 E05 的 180/200°/s），
切换会在日志里记一条，因为它直接决定"仿真看起来比真机快还是慢"。
"""

from __future__ import annotations

import math
import time


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

    def move_j(self, target, blend=0.0):
        """关节空间同步运动：所有轴同时到达，耗时由最慢的那根决定。"""
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
        _s, _t, dur, t0 = self.traj
        return min(1.0, self._elapsed / dur) if dur else 1.0

    def remaining(self):
        if not self.traj:
            return 0.0
        _s, _t, dur, t0 = self.traj
        return max(0.0, dur - self._elapsed) * self._initial_speed / max(self.speed_pct, 1e-6)

    def state(self):
        moving = self.traj is not None or self.jog is not None
        return {"q": [round(v, 3) for v in self.q], "moving": int(moving),
                "inpos": int(not moving), "paused": int(self.paused)}
