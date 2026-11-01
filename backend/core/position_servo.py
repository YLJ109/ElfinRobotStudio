"""Production bounded position-priority IK with verified local wrist null motion.

Angles are degrees, positions are mm, deadline is an absolute time.monotonic()
timestamp. No orientation target, wrapping, global restart, I/O or robot control.
The caller still owns collision checks and trajectory/speed validation.
"""
from __future__ import annotations

from copy import copy
import math
import time

from backend.core.kinematics import RobotModel,pose_rotation_distance

MAX_ITERATIONS = 18
MAX_EVALUATIONS = 320
POSITION_TOLERANCE_MM = 0.05


def _vector(value, size):
    if len(value) != size or any(type(v) not in (int, float) for v in value):
        raise ValueError("Expected a finite numeric vector")
    result = [float(v) for v in value]
    if not all(math.isfinite(v) for v in result):
        raise ValueError("Expected a finite numeric vector")
    return result


def _solve3(a, b):
    """Pivoted elimination of the positive definite 3x3 task-space system."""
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-18:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        v = m[col][col]
        for k in range(col, 4):
            m[col][k] /= v
        for r in range(3):
            if r != col:
                v = m[r][col]
                for k in range(col, 4):
                    m[r][k] -= v * m[col][k]
    result = [row[3] for row in m]
    return result if all(math.isfinite(v) for v in result) else None


def _dls(j, error, damping):
    a = [[sum(j[r][k] * j[c][k] for k in range(6))
          + (damping * damping if r == c else 0.) for c in range(3)]
         for r in range(3)]
    task = _solve3(a, error)
    return None if task is None else [sum(j[r][k] * task[r] for r in range(3)) for k in range(6)]


class _Stopped(Exception):
    pass


class _Resume:
    """Private bounded pure-geometry memo, never an executable motion goal."""
    def __init__(self,signature):
        self.signature=signature
        self.positions={}
        self.jacobians={}
        self.checkpoint=None


def solve_position_step(model: RobotModel, q, target_xyz, lower, upper, *,
                        max_joint_step=8., max_cartesian_step=24., deadline=None,
                        preferred_j6=None,resume=None,resume_context=None):
    """Return only local progress, bounded relative to the *entry* q and TCP.

    lower/upper are the caller's authoritative validated operating limits;
    URDF metadata must not narrow controller/config limits a second time.
    q=None means hold. Causes: invalid_input, invalid_model, joint_limits,
    deadline, budget, stalled, reached, progress. Error is Euclidean distance
    to the original (unclipped) target; progress is reduction of that distance.
    An expired deadline returns hold even if work already found a candidate.
    Invalid input / pre-FK cancellation has error=inf (unknown), progress=0.
    Both loop and FK/Jacobian budgets apply without a deadline; cancellation is polled
    before and after each FK/Jacobian. A synchronous FK cannot be preempted.
    """
    result = dict(q=None, position_error_mm=math.inf, progress_mm=0.,
                  cause="invalid_input", iterations=0)
    try:
        start = _vector(q, 6)
        target = _vector(target_xyz, 3)
        lo, hi = _vector(lower, 6), _vector(upper, 6)
        caps = _vector([max_joint_step, max_cartesian_step], 2)
        if min(caps) <= 0:
            return result
        if deadline is not None:
            deadline = _vector([deadline], 1)[0]
        if preferred_j6 is not None:preferred_j6=_vector([preferred_j6],1)[0]
        if any(a > b for a, b in zip(lo, hi)):
            return result
    except (TypeError, ValueError, OverflowError):
        return result

    evaluations = 0
    signature=(tuple(start),tuple(target),tuple(lo),tuple(hi),tuple(caps),preferred_j6,
               resume_context if resume_context is not None else id(model))
    memo=resume if isinstance(resume,_Resume) and resume.signature==signature else _Resume(signature)

    def check():
        if deadline is not None and time.monotonic() >= deadline:
            raise _Stopped("deadline")

    try:
        check()
        # RobotModel.fk caches on self; a private shallow shell shares read-only
        # geometry, but never writes cache keys/matrices on the caller's model.
        work = copy(model)
        work._fk_key, work._fk_value = None, None
        if any(not lo[i] <= start[i] <= hi[i] for i in range(6)):
            result["cause"] = "joint_limits"
            return result
        lo = [max(lo[i], start[i] - caps[0]) for i in range(6)]
        hi = [min(hi[i], start[i] + caps[0]) for i in range(6)]

        def position(angles):
            nonlocal evaluations
            check()
            key=tuple(angles)
            if key in memo.positions:return list(memo.positions[key])
            if evaluations >= MAX_EVALUATIONS or len(memo.positions)>=MAX_EVALUATIONS:
                raise _Stopped("budget")
            evaluations += 1
            matrix = work._flange_matrix(angles)
            p = _vector([matrix[i][3] * 1000. for i in range(3)], 3)
            memo.positions[key]=tuple(p)
            check()
            return p

        origin = position(start)
        initial_error = math.dist(origin, target)
        if not math.isfinite(initial_error):
            return result
        result["position_error_mm"] = initial_error
        if initial_error <= POSITION_TOLERANCE_MM:
            if preferred_j6 is not None and abs(start[5]-preferred_j6)>1e-6:
                reference=work.pose_vec(start)
                step=max(-caps[0],min(caps[0],preferred_j6-start[5]))
                for factor in (1.,.5,.25,.125):
                    for sign in (-1.,1.):
                        check()
                        candidate=list(start);candidate[5]+=step*factor;candidate[3]+=sign*step*factor
                        if not all(a<=v<=b for a,v,b in zip(lo,candidate,hi)):continue
                        point=position(candidate);pose=work.pose_vec(candidate);check()
                        # URDF angles are rounded; bound only numerical closure
                        # error, not the user's Cartesian or orientation budget.
                        if (math.dist(point,origin)<=.001 and math.dist(point,target)<=max(initial_error,.001)
                                and pose_rotation_distance(pose,reference)<=.001):
                            result.update(q=candidate,cause='wrist_recentering',position_error_mm=math.dist(point,target))
                            return result
            result.update(q=list(start), cause="reached")
            return result
        ratio = min(1., caps[1] / initial_error)
        goal = [origin[i] + (target[i] - origin[i]) * ratio for i in range(3)]
        current, p = list(start), origin
        cost = math.dist(p, goal)
        damping = .08
        if memo.checkpoint is not None:
            previous,damping=memo.checkpoint
            if all(a<=v<=b for a,v,b in zip(lo,previous,hi)):
                point=position(previous)
                if math.dist(point,origin)<=caps[1]+1e-9 and math.dist(point,target)<initial_error:
                    current,p,cost=list(previous),point,math.dist(point,goal)

        def candidate(direction, alpha=1.):
            # Clip bounds, never wrap a wrist or project an invalid entry pose.
            nxt = [max(lo[i], min(hi[i], current[i] + alpha * direction[i])) for i in range(6)]
            if max(abs(nxt[i] - current[i]) for i in range(6)) < 1e-10:
                return None
            point = position(nxt)
            distance = math.dist(point, goal)
            if (math.dist(point, origin) <= caps[1] + 1e-9
                    and distance < cost - max(1e-9, cost * 1e-9)
                    and math.dist(point, target) < initial_error - 1e-8):
                return nxt, point, distance
            return None

        cause = "budget"
        for iteration in range(MAX_ITERATIONS):
            check()
            result["iterations"] = iteration + 1
            if cost <= POSITION_TOLERANCE_MM:
                cause = "progress"
                break
            if evaluations >= MAX_EVALUATIONS:
                raise _Stopped("budget")
            jac_key=tuple(current)
            if jac_key in memo.jacobians:j=[list(row) for row in memo.jacobians[jac_key]]
            else:
                evaluations += 1
                j = [_vector(row, 6) for row in work.jacobian(current)[:3]]
                if len(memo.jacobians)<MAX_ITERATIONS:memo.jacobians[jac_key]=tuple(tuple(row) for row in j)
            if len(j) != 3:
                raise ValueError("Expected a 3x6 position Jacobian")
            check()
            error = [goal[i] - p[i] for i in range(3)]
            accepted = None
            for bump in range(4):
                check()
                lam = damping * (4 ** bump)
                active = [row[:] for row in j]
                # Re-solve when a boundary joint points outward, allowing the
                # remaining joints to contribute instead of stalling on clipping.
                for _ in range(7):
                    direction = _dls(active, error, lam)
                    if direction is None:
                        break
                    blocked = [i for i in range(6) if
                               (current[i] <= lo[i] + 1e-9 and direction[i] < 0)
                               or (current[i] >= hi[i] - 1e-9 and direction[i] > 0)]
                    if not blocked:
                        break
                    for i in blocked:
                        for row in active:
                            row[i] = 0.
                if direction is None:
                    continue
                scale = min(1., 3. / max(1e-15, max(map(abs, direction))))
                for backtrack in range(9):
                    check()
                    accepted = candidate(direction, scale * .5 ** backtrack)
                    if accepted:
                        break
                if accepted:
                    damping = max(.02, lam * .5)
                    break
            if accepted is None:
                # Deterministic, small projected coordinate directions explore
                # second-order curvature at zero task gradient. These are local
                # null motions (<1.5deg), never branch seeds / global restarts.
                best = None
                for axis in range(6):
                    check()
                    projection = _dls(j, [j[r][axis] for r in range(3)], .02)
                    if projection is None:
                        continue
                    null = [(1. if i == axis else 0.) - projection[i] for i in range(6)]
                    norm = max(map(abs, null))
                    if norm < 1e-5:
                        continue
                    for amplitude in (.5, 1.5):
                        for sign in (-1., 1.):
                            trial = candidate([sign * amplitude * v / norm for v in null])
                            if trial and (best is None or trial[2] < best[2]):
                                best = trial
                accepted = best
            if accepted is None:
                cause = "stalled"
                break
            current, p, cost = accepted
            memo.checkpoint=(tuple(current),damping)
        check()
        remaining = math.dist(p, target)
        if remaining < initial_error - 1e-8:
            result.update(q=current, position_error_mm=remaining,
                          progress_mm=initial_error - remaining,
                          cause="reached" if remaining <= POSITION_TOLERANCE_MM else cause)
        else:
            result["cause"] = cause
    except _Stopped as exc:
        result.update(q=None, progress_mm=0., cause=str(exc))
        if resume_context is not None and memo.positions and len(memo.positions)<MAX_EVALUATIONS:result['resume']=memo
    except (AttributeError, TypeError, ValueError, OverflowError, IndexError, KeyError):
        result.update(q=None, progress_mm=0., cause="invalid_model")
    return result
