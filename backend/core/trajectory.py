"""Pure, simulation-only trajectory contracts. No transport or controller claims.

World integration contract (wired in world.py; independently tested):
* MOVEJ: pass the same explicit ``sim_path_contract`` to inspect_path and
  SimRobot.move_j(contract=...). Default remains synchronized, not real-equivalent.
* MOVEL: planning.prepare_movel(...) on a copied configuration, then
  MotionExecutor.execute_prepared_sim(..., validate_context=callback). The callback
  must compare configuration revision, model/TCP/UCS/tool, limits, speed source,
  start state and mode against the captured context; reject real/connected mode.
  Keep the existing red preflight and per-tick safety/stop mechanisms enabled.

The C1 curve is a monotone cubic *joint* approximation through Cartesian IK
samples. It is neither exact Cartesian interpolation nor a certified controller
acceleration/jerk model. Inspection must sample THIS curve, not its chord route.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import json

# 梯形时钟里加速段（与减速段相同）占总时长的比例。取值偏小是因为加速段越短，
# 峰值速度相对平均速度越高；0.15 时峰值只有平均的 1.18 倍，中间 70% 行程匀速，
# 与控制器 MoveL 的观感接近，又不会把峰值顶到离谱。
TRAPEZOID_ACCEL_RATIO = 0.15
import math


def vector(values, size=6):
    values = tuple(values)
    if len(values) != size or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                  or not math.isfinite(v) for v in values):
        raise ValueError("需要六个有限数值")
    return values


def positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("速度/时间参数必须为有限正数")
    return value


def relative_route(start, target, chunk=10., max_chunks=240):
    """Ideal feedback-free geometry of the existing largest-error-axis adapter.

    Stop tolerance is .05 degrees, ties prefer the first axis. Real feedback,
    .001-degree wire rounding, settling, latency and dynamics are NOT simulated.
    """
    q, target = list(vector(start)), vector(target)
    positive(chunk)
    if isinstance(max_chunks, bool) or not isinstance(max_chunks, int) or max_chunks < 1:
        raise ValueError("分段上限必须为正整数")
    route = [q[:]]
    for _ in range(max_chunks):
        axis = max(range(6), key=lambda i: abs(target[i] - q[i]))
        delta = target[axis] - q[axis]
        if abs(delta) <= .05:
            return route
        q = q[:]
        q[axis] += max(-chunk, min(chunk, delta))
        route.append(q)
    # The adapter only tests arrival at the start of each iteration.
    raise ValueError("所需分段超过执行器当前能力，请分成多个作业点位")


@dataclass(frozen=True)
class JointTrajectory:
    points: tuple
    times: tuple
    tangents: tuple
    duration: float
    speed_pct: float
    vmax: tuple
    stop_at_knots: bool = False
    # 时间→路径参数的映射曲线。
    # 'quintic'：全程平滑，两端速度与加速度都为 0，但整段都在变速，没有匀速段。
    # 'trapezoid'：加速-匀速-减速，中间有一大段匀速，接近控制器 MoveL 的节拍。
    # 两者都把峰值速度标定到 speed_pct 对应的速度，区别只在速度曲线的形状。
    profile: str = 'quintic'

    @staticmethod
    def _quintic(k):
        return k*k*k*(10+k*(-15+6*k))

    def _map(self, k):
        if self.profile != 'trapezoid':
            return self._quintic(k)
        a = TRAPEZOID_ACCEL_RATIO
        peak = 1/(1-a)          # 峰值/平均，由"加速+减速各占 a、位移归一"推出
        if k <= a:
            return peak*k*k/(2*a)
        if k >= 1-a:
            return 1 - peak*(1-k)*(1-k)/(2*a)
        return peak*(a/2 + (k-a))

    @classmethod
    def _profile_peak(cls, profile):
        if profile == 'trapezoid':
            return 1/(1-TRAPEZOID_ACCEL_RATIO)
        return 1.875             # quintic 时钟的峰值斜率

    @classmethod
    def build(cls, points, vmax, speed_pct, *, stop_at_knots=False, profile='quintic'):
        points = tuple(vector(p) for p in points)
        vmax = vector(vmax)
        for v in vmax:
            positive(v)
        positive(speed_pct)
        if speed_pct > 100:
            raise ValueError("倍率不能超过100%")
        if profile not in ('quintic', 'trapezoid'):
            raise ValueError("轨迹时钟只能是 quintic 或 trapezoid")
        if not points or len(points) > 4096:
            raise ValueError("轨迹需要1至4096个点")
        # Remove only exact duplicates. Never shortcut or reorder a checked path.
        points = tuple(p for i, p in enumerate(points) if not i or p != points[i-1])
        limits = [v * speed_pct / 100 for v in vmax]
        spans = [max(max(abs(b[i]-a[i])/limits[i] for i in range(6)), .001)
                 for a, b in zip(points, points[1:])]
        times = [0.]
        for span in spans:
            times.append(times[-1] + span)
        slopes = [[(b[i]-a[i])/h for i in range(6)]
                  for a, b, h in zip(points, points[1:], spans)]
        tangents = [[0.]*6 for _ in points]
        if not stop_at_knots:
            for k in range(1, len(points)-1):
                for i in range(6):
                    left, right = slopes[k-1][i], slopes[k][i]
                    if left * right > 0:
                        # C1 at knots, monotone Bezier controls in each segment.
                        tangents[k][i] = math.copysign(min(abs(left), abs(right)), left)
        # Derivative of cubic Bezier is the convex hull of these three controls.
        # The clock's peak slope is analytic for both shapes. Both bounds are analytic.
        stretch = 1.
        for k, h in enumerate(spans):
            for i in range(6):
                controls = (tangents[k][i], 3*slopes[k][i]-tangents[k][i]-tangents[k+1][i], tangents[k+1][i])
                stretch = max(stretch, max(abs(v) for v in controls)/limits[i])
        duration = max(.05, times[-1]*stretch*cls._profile_peak(profile))
        return cls(points, tuple(times), tuple(tuple(t) for t in tangents),
                   duration, speed_pct, vmax, stop_at_knots, profile)

    def at_path_time(self, value):
        if len(self.points) == 1 or value >= self.times[-1]:
            return list(self.points[-1])
        if value <= 0:
            return list(self.points[0])
        k = min(bisect_right(self.times, value)-1, len(self.points)-2)
        h = self.times[k+1]-self.times[k]
        u = (value-self.times[k])/h
        return [(2*u**3-3*u**2+1)*a + (u**3-2*u**2+u)*h*ma
                + (-2*u**3+3*u**2)*b + (u**3-u**2)*h*mb
                for a, b, ma, mb in zip(self.points[k], self.points[k+1], self.tangents[k], self.tangents[k+1])]

    def at(self, elapsed):
        k = max(0., min(1., elapsed/self.duration))
        return self.at_path_time(self._map(k)*self.times[-1])

    def inspection_samples(self, spacing=1., max_samples=10000):
        positive(spacing)
        samples = [list(self.points[0])]
        for k, (a, b) in enumerate(zip(self.points, self.points[1:])):
            # Bezier derivative in normalized segment time <= 3*joint travel;
            # each adjacent sample is therefore <= spacing in every joint.
            n = max(1, math.ceil(3*max(abs(y-x) for x, y in zip(a, b))/spacing))
            if len(samples)+n > max_samples:
                raise ValueError("轨迹预检采样预算不足；拒绝放宽采样")
            for j in range(1, n+1):
                samples.append(self.at_path_time(self.times[k]+(self.times[k+1]-self.times[k])*j/n))
        return samples


@dataclass(frozen=True)
class PreparedMotion:
    trajectory: JointTrajectory
    context_signature: str
    lower: tuple
    upper: tuple
    inspection_json: str

    @property
    def report(self):
        return json.loads(self.inspection_json)
