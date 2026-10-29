"""Hand-follow target/IK logic for the shared simulated robot.

Coordinates: mm/degrees, camera TL/TR/BR/BL -> world X/Z rectangle.
The caller owns the simulation-mode/generation gate and installs this candidate
only after configure finishes. World mirrors accepted ``q`` into World.sim.
"""
from __future__ import annotations

import math
import secrets
import time
from statistics import median

from .hand_envelope import robot_bounds, contains_bounds, make_enclosure
from .kinematics import rpy_to_mat, interpolate_pose, pose_rotation_distance, rotation_error, _solve6
from .position_servo import solve_position_step


def _number(value, low, high):
    return (type(value) in (int, float) and low <= value <= high
            and math.isfinite(value))


def _schema(payload, fields):
    return type(payload) is dict and set(payload) == set(fields)


def _copy_bounds(bounds):
    return {"min": list(bounds["min"]), "max": list(bounds["max"])}


class HandFollow:
    TIMEOUT = .5
    # Camera motion expires independently of the one-second lost-hand hold.
    OBSERVATION_TTL = .3
    CAMERA_LOSS_HOLD = 1.
    CONFIGURE_BUDGET_S = 2.
    IK_INTERVAL = 1 / 30
    BASE_Y_LIMIT = 100.
    BASE_Z = 0.
    MAX_AUTO_SPAN = 3000.
    GROUND_CLEARANCE = 0.
    MAX_COMPACT_Y = 400.
    # Solver-only inset, never an expanded fence or relaxed FK acceptance.
    # A Cartesian face target plus approximate IK/joint interpolation otherwise
    # locks all tangent motion on sub-micron outward deviations at that face.
    TRACKING_INSET_MM = .5
    # A solver may converge on another turn even with restarts disabled. These
    # are task-local trust/branch bounds, intersected with the real soft limits.
    # They never wrap the executed angles or authorize a wrist flip in flight.
    IK_JOINT_TRUST_DEG = 8.
    IK_LOOKAHEAD_MM = 24.
    FIXED_ORIENTATION_TOL_DEG = .1
    # Leave scheduling/installation headroom inside World's 0.5s result TTL.
    SOLVE_BUDGET_S = .025
    REJECT_RETRY_S = .25
    CACHE_TARGET_MM = .25
    SOLVE_TOTAL_BUDGET_S = .7

    def __init__(self, model):
        self.model = model
        self.configured = self.active = False
        self.waiting = False
        self.returning = False
        self.input_phase = "waiting"
        self.blocked_reason = ''
        self.status, self.why = "未配置", ""
        self.session, self.token = "", secrets.token_hex(16)
        self.aspect, self.depth, self.rotation = 1., False, False
        self.direction = 0
        self.depth_range = None
        self.depth_mode = "scale"
        self.q = [0.] * 6
        self._center_q = [0.] * 6
        self.target = self.actual = [0.] * 6
        self._lo, self._hi = [0.] * 3, [0.] * 3
        self._lower, self._upper = list(model.lower), list(model.upper)
        self._base = [0.] * 6
        self._local_base = [0.] * 6
        self.base_offset = [0., 0., self.BASE_Z]
        self.outer_bounds = {"min": [0.] * 3, "max": [0.] * 3}
        self.auto_outer_bounds = _copy_bounds(self.outer_bounds)
        self.reference_q = [0.] * 6
        self.reference_body_bounds = _copy_bounds(self.outer_bounds)
        self._goal = None
        self._local_target = None
        self._solver_cause = "idle"
        self._solver_detail = {}
        self._velocity = [0.] * 6
        self._last_sample = self._last_ik = None
        self._last_observation = None
        self._camera_input = False
        self._loss_since = None
        self._input_generation = 0
        self._clock_value = None
        self.seq = -1
        self._reset_tracking()

    def _reset_tracking(self):
        self._wrist_recovery_target = None
        self._wrist_recovery_reference = None
        self._reachability_cache = None
        self._scales = []
        self._scale0 = self._angle0 = self._angle_prev = None
        self._angle_unwrapped = 0.
        self._rejected_target = None
        self._rejected_until = 0.
        self._subdivision_resume = None
        self._budget_key = None
        self._budget_spent = 0.
        self._budget_retry_at = 0.
        self._gesture_depth = None

    def _clock(self, now):
        value = time.monotonic() if now is None else now
        if not _number(value, 0, 1e12):
            raise ValueError("无效单调时钟")
        if self._clock_value is not None and value < self._clock_value:
            raise ValueError("单调时钟倒退")
        self._clock_value = value
        return value

    def _reply(self, ok, why=""):
        return {"ok": ok, **({"why": why} if why else {}), "state": self.snapshot()}

    def _inside(self, pose):
        pose = self._rotate_pose(pose, inverse=True)
        return (len(pose) == 6 and all(math.isfinite(v) for v in pose)
                and all(a <= v <= b for a, v, b in zip(self._lo, pose[:3], self._hi)))

    def _rotate_pose(self, pose, inverse=False):
        center = [(a+b)/2 for a,b in zip(self._lo,self._hi)]
        angle = math.radians(self.direction) * (-1 if inverse else 1)
        c, s = math.cos(angle), math.sin(angle)
        x, y = pose[0]-center[0], pose[1]-center[1]
        return [center[0]+c*x-s*y, center[1]+s*x+c*y, *pose[2:]]

    def _tracking_target(self, pose):
        local=self._rotate_pose(pose,inverse=True)
        for i,(low,high) in enumerate(zip(self._lo,self._hi)):
            margin=min(self.TRACKING_INSET_MM,(high-low)*.05)
            local[i]=max(low+margin,min(high-margin,local[i]))
        return self._rotate_pose(local)

    def world_bounds(self):
        corners = [self._rotate_pose([x,y,z]) for x in (self._lo[0],self._hi[0])
                   for y in (self._lo[1],self._hi[1]) for z in (self._lo[2],self._hi[2])]
        return {"min": [min(p[i] for p in corners) for i in range(3)],
                "max": [max(p[i] for p in corners) for i in range(3)]}

    def _valid_q(self, q):
        return (len(q) == 6 and all(_number(v, a, b)
                for v, a, b in zip(q, self._lower, self._upper)))

    def _world_pose(self, q, offset):
        pose = list(self.model.pose_vec(q))
        return [pose[i] + offset[i] for i in range(3)] + pose[3:]

    def _body_inside(self, q, offset):
        body = robot_bounds(self.model, q, offset)
        # User-drawn bounds describe the TCP workspace; links may extend past
        # it. The real simulated collision invariant here is staying above floor.
        return body["min"][2] >= self.GROUND_CLEARANCE

    @staticmethod
    def _enclosure(value):
        if not _schema(value, ("min", "max")):
            return None
        if any(type(value[k]) is not list or len(value[k]) != 3 for k in ("min", "max")):
            return None
        lo, hi = value["min"], value["max"]
        if any(not _number(v, -1e6, 1e6) for v in lo + hi):
            return None
        if any(not 0 < b - a <= 5000 for a, b in zip(lo, hi)):
            return None
        return {"min": [float(v) for v in lo], "max": [float(v) for v in hi]}

    def _nonsingular(self, q):
        # Test the actual geometric Jacobian, not the theoretical arm radius.
        rows = [list(row) for row in self.model.jacobian(q)]
        for col in range(6):
            pivot = max(range(col, 6), key=lambda r: abs(rows[r][col]))
            if abs(rows[pivot][col]) < 1e-5:
                return False
            rows[col], rows[pivot] = rows[pivot], rows[col]
            for r in range(col + 1, 6):
                factor = rows[r][col] / rows[col][col]
                for k in range(col + 1, 6):
                    rows[r][k] -= factor * rows[col][k]
        return True

    def _position_center(self, reference, target, lo, hi, deadline):
        """Continuous bounded position IK; no ABC constraint or joint locks.

        This selects a planning endpoint only. It never moves the robot or
        modifies its base, and does not restart onto a different elbow seed.
        """
        q = list(reference)
        for _ in range(80):
            if time.monotonic() >= deadline:
                break
            error = math.dist(self.model.pose_vec(q)[:3], target[:3])
            if error <= .005 and time.monotonic() < deadline:
                return q, error, 0., True
            if error <= .08:
                # Setup keeps tighter center accuracy than the runtime
                # solver's 0.05mm settling radius. Translation only; six free
                # joints, tiny bounded correction, no ABC or wrist constraint.
                jac = self.model.jacobian(q)[:3]
                matrix = [[sum(jac[r][k]*jac[c][k] for k in range(6))
                           + (1e-4 if r == c else 0.) for c in range(3)]
                          + [0.]*3 for r in range(3)]
                matrix += [[float(r == c) for c in range(6)] for r in range(3,6)]
                pose = self.model.pose_vec(q)
                delta = _solve6(matrix, [target[i]-pose[i] for i in range(3)]+[0.]*3)
                if delta is None:
                    break
                candidate = [max(lo[k], min(hi[k], q[k]+max(-.1,min(.1,
                    sum(jac[r][k]*delta[r] for r in range(3)))))) for k in range(6)]
                if math.dist(self.model.pose_vec(candidate)[:3], target[:3]) >= error:
                    break
                q = candidate
                continue
            result = solve_position_step(self.model, q, target[:3], lo, hi,
                max_joint_step=self.IK_JOINT_TRUST_DEG,
                max_cartesian_step=self.IK_LOOKAHEAD_MM, deadline=deadline)
            if result.get('q') is None or result.get('progress_mm', 0.) <= 1e-8:
                break
            q = list(result['q'])
        return q, math.dist(self.model.pose_vec(q)[:3], target[:3]), 0., False

    def configure(self, payload, lower=None, upper=None, now=None, current_q=None):
        self.stop("重新配置")
        self.configured = False
        if type(payload) is not dict:
            return self._reply(False, "配置参数无效")
        payload = dict(payload)
        self.direction = payload.pop("direction", 0)
        self.depth_mode = payload.pop("depth_mode", "scale")
        if self.depth_mode not in ("scale", "gesture"):
            return self._reply(False, "前后控制方式无效")
        self.depth_range = payload.pop("depth_range", None)
        if self.depth_range is not None and (not isinstance(self.depth_range, list)
                or len(self.depth_range) != 2
                or not all(_number(v, .005, 2.) for v in self.depth_range)
                or self.depth_range[1] - self.depth_range[0] < .01):
            return self._reply(False, "掌圈范围无效：远端应小于近端，且至少相差0.01")
        if type(self.direction) is not int or self.direction not in (0,90,180,270):
            return self._reply(False, "跟随方向无效")
        if not (_schema(payload, ("session", "aspect", "depth", "rotation"))
                or _schema(payload, ("session", "aspect", "depth", "rotation", "enclosure"))):
            return self._reply(False, "配置字段不完整或含未知字段")
        enclosure = self._enclosure(payload["enclosure"]) if "enclosure" in payload else None
        if "enclosure" in payload and enclosure is None:
            return self._reply(False, "外盒需六个有限坐标，各轴尺寸须在0至5000mm内")
        if (type(payload["session"]) is not str or not 1 <= len(payload["session"]) <= 128
                or not _number(payload["aspect"], .5, 3)
                or type(payload["depth"]) is not bool or type(payload["rotation"]) is not bool):
            return self._reply(False, "配置参数无效")
        try:
            stamp = self._clock(now)
        except ValueError as exc:
            return self._reply(False, str(exc))
        lo = list(self.model.lower if lower is None else lower) if isinstance(lower, (list, tuple, type(None))) else []
        hi = list(self.model.upper if upper is None else upper) if isinstance(upper, (list, tuple, type(None))) else []
        if (len(lo) != 6 or len(hi) != 6 or any(
                not _number(a, -720, 720) or not _number(b, -720, 720) or a >= b
                for a, b, ml, mu in zip(lo, hi, self.model.lower, self.model.upper))):
            return self._reply(False, "六轴限位无效")
        self._lower, self._upper = lo, hi
        self.session, self.aspect = payload["session"], float(payload["aspect"])
        self.depth, self.rotation = payload["depth"], payload["rotation"]
        deadline = time.monotonic() + self.CONFIGURE_BUDGET_S
        # Rotation mode preserves its explicit pose task. Position mode below
        # prefers a measured upright/forward current posture, not any branch.
        j1 = self.direction - 90
        seeds = ([list(current_q)] if current_q is not None else []) + [
                 [j1, -30, 30, 0, 30, 0], [j1, 24.6, 95.9, 0, 18.7, 0],
                 [j1, -77.7, -95.9, 0, 108.2, 0]]
        if not self.rotation:
            # FK: this reference raises shoulder->elbow by 0.909 of its
            # length and extends elbow->wrist forward by 0.929 (Elfin5).
            # For custom boxes face the actual TCP center, not the view angle.
            if enclosure is not None:
                cx, cy = [(a+b)/2 for a,b in zip(enclosure['min'], enclosure['max'])][:2]
                if math.hypot(cx, cy) > 1.:
                    j1 = math.degrees(math.atan2(cy, cx))
            turns = [j1+360*k for k in (-2,-1,0,1,2) if lo[0] <= j1+360*k <= hi[0]]
            if turns:
                j1 = min(turns, key=abs)
            natural = [[j1, 24.6, 95.9, 0, 18.7, 0], [j1, -30, 30, 0, 30, 0]]
            current = [list(current_q)] if current_q is not None else []
            prefer_current = False
            if current and self._valid_q(current[0]):
                tf = self.model.fk(current[0])
                point = lambda name: [tf[name][i][3]*1000. for i in range(3)]
                shoulder, elbow, root, wrist = map(point,
                    ('elfin_link2','elfin_link3','elfin_link4','elfin_link5'))
                posture_box = enclosure or make_enclosure(
                    robot_bounds(self.model,current[0],[0.,0.,self.BASE_Z]),self.aspect,margin=80.)
                center_xy = [(a+b)/2 for a,b in zip(posture_box['min'],posture_box['max'])][:2]
                radial = math.hypot(*center_xy)
                upper_length = math.dist(shoulder,elbow)
                # Selection preference only: never lock an angle or reject a
                # usable fallback. Forward is radial to the requested center.
                prefer_current = (upper_length > 0 and radial > 1e-9
                    and (elbow[2]-shoulder[2])/upper_length >= .6
                    and sum((wrist[i]-root[i])*center_xy[i] for i in range(2))/radial > 0)
            seeds = (current+natural if prefer_current else natural+current) + [
                [j1, -77.7, -95.9, 0, 108.2, 0]]
        for reference in seeds:
            if time.monotonic() >= deadline:
                break
            if not self._valid_q(reference) or (self.rotation and not self._nonsingular(reference)):
                continue
            try:
                reference_body = robot_bounds(self.model, reference, [0., 0., self.BASE_Z])
                # Extra fixed padding (80 mm) provides room for the changed
                # folded center pose. The center still comes ONLY from reference
                # geometry; neither center IK nor grid samples may move/grow it.
                auto_outer = make_enclosure(reference_body, self.aspect, margin=80.)
            except ValueError:
                return self._reply(False, "模型几何无效，无法建立外盒")
            if enclosure is None and ((self.rotation and reference_body["max"][1]-reference_body["min"][1] > self.MAX_COMPACT_Y)
                    or max(b-a for a,b in zip(auto_outer["min"],auto_outer["max"])) > self.MAX_AUTO_SPAN):
                continue
            outer = auto_outer if enclosure is None else enclosure
            center = [(a+b)/2 for a,b in zip(outer["min"],outer["max"])]
            requested = [center[i]-(self.BASE_Z if i == 2 else 0.) for i in range(3)] + [90., 0., float(self.direction)]
            # Simulation should not reject a usable center because of sub-micron
            # numerical residuals or one unlucky IK branch. Keep the TCP close
            # to the requested center, while allowing the solver to find a pose.
            ik_lower, ik_upper = list(lo), list(hi)
            ik_lower[5] = ik_upper[5] = 0.0
            free_center = None
            measured = self.model.pose_vec(reference)
            measured_position = math.dist(measured[:3],requested[:3])
            measured_angle = pose_rotation_distance(measured,requested)
            if not self.rotation:
                free_center = self._position_center(reference, requested, lo, hi,
                    min(deadline, time.monotonic()+.45))
                if not free_center[3]:
                    continue
            elif measured_position <= .1 and measured_angle <= .1:
                # 已在同一合法中心无需再跑逆解，也不重新选腕轴分支。
                free_center = (list(reference),measured_position,measured_angle,True)
            radians = math.radians(self.direction)
            transverse = abs(math.cos(radians)*center[0] + math.sin(radians)*center[1])
            if free_center is None and enclosure is not None and transverse > 1.:
                # 横向自定义盒子的中心通常需要非零J6；先解完整六维任务。
                # 原流程先耗尽J6=0的五自由度重启，再重复同一批不朝向目标的种子。
                radial = list(reference)
                angle = math.degrees(math.atan2(center[1], center[0]))
                choices = [angle+360*k for k in (-2,-1,0,1,2) if lo[0] <= angle+360*k <= hi[0]]
                if choices:
                    radial[0] = min(choices,key=lambda v:abs(v-reference[0]))
                solved = self.model.ik(radial, requested, lo, hi, iters=80,
                    restarts=False, tol_mm=.1, tol_deg=.1, deadline=deadline)
                if solved[3] and solved[1] <= .1 and solved[2] <= .1:
                    free_center = solved
            if free_center is not None:
                seed, ep, er, ok = free_center
            elif getattr(self.model,"library_tool",None) or not getattr(self.model,"reference_tool",None):
                # Axially mounted grippers have an arbitrary roll about their
                # grasp normal. Constraining both that roll and J6=0 creates an
                # overdetermined task and made tool switching hang in IK.
                seed,ep,er,ok=self.model.ik(reference,requested,lo,hi,iters=80,
                    restarts=True,max_restarts=2,tol_mm=.1,tol_deg=.1,deadline=deadline)
                if ok:
                    seed=list(seed);seed[5]=0.
                    pose=self.model.pose_vec(seed)
                    expected=rpy_to_mat(*map(math.radians,requested[3:]))
                    actual=rpy_to_mat(*map(math.radians,pose[3:]))
                    ep=math.dist(pose[:3],requested[:3])
                    er=math.degrees(math.acos(max(-1.,min(1.,sum(expected[i][2]*actual[i][2] for i in range(3))))))
                    ok=ep<=1. and er<=1.
            else:
                seed, ep, er, ok = self.model.ik(reference, requested, ik_lower, ik_upper, iters=120,
                                               restarts=True, max_restarts=8,
                                               tol_mm=.1, tol_deg=.1, deadline=deadline)
                if not ok:
                    # A user box need not be radial to its chosen view. Fixing
                    # J6=0 turns the six-dimensional center task into five DOF
                    # (e.g. direction=270, center Y=600), rejecting usable boxes.
                    # Setup may select a real full-pose center with nonzero J6;
                    # World reaches it through the normal inspected trajectory.
                    # Runtime winding/trust gates remain relative to THIS center.
                    seed, ep, er, ok = self.model.ik(reference, requested, lo, hi,
                        iters=80, restarts=True, max_restarts=2, tol_mm=.1, tol_deg=.1,deadline=deadline)
            if (not ok or ep > 1.0 or er > 1.0 or not self._valid_q(seed)
                    or (enclosure is None and self.rotation and not self._nonsingular(seed))):
                continue
            # 回中前选有双向行程的同姿态关节分支，避免中心恰好落在±360°。
            # 只选择回中目标；实际关节仍通过正常轨迹运动，跟随中绝不绕角瞬移。
            alternate = list(seed)
            for i, value in enumerate(seed):
                if min(value-lo[i],hi[i]-value) < self.IK_JOINT_TRUST_DEG:
                    choices = [value+360*k for k in (-2,-1,0,1,2) if lo[i] <= value+360*k <= hi[i]]
                    alternate[i] = max(choices,key=lambda v:min(v-lo[i],hi[i]-v))
            if alternate != list(seed) and (self.rotation or current_q is None):
                original_pose, alternate_pose = self.model.pose_vec(seed), self.model.pose_vec(alternate)
                if (math.dist(original_pose[:3],alternate_pose[:3]) < .001
                        and pose_rotation_distance(original_pose,alternate_pose) < .001):
                    seed = alternate
            base = self.model.pose_vec(seed)
            initial_body = robot_bounds(self.model, seed, [0., 0., self.BASE_Z])
            if (initial_body["min"][2] < self.GROUND_CLEARANCE
                    or (enclosure is None and self.rotation and initial_body["max"][1]-initial_body["min"][1] > self.MAX_COMPACT_Y)):
                continue
            if enclosure is None and self.depth:
                # Full +/-100 mm base depth travel is included without growing
                # the fixed outer box after IK.
                if any(not contains_bounds(robot_bounds(self.model, seed, [0., dy, self.BASE_Z]), outer, 10.)
                       for dy in (-self.BASE_Y_LIMIT,self.BASE_Y_LIMIT)):
                    continue
            if enclosure is None and self.rotation and abs(math.cos(math.radians(base[4]))) < .2:
                continue
            world_base = [base[i] + (self.BASE_Z if i == 2 else 0.) for i in range(3)] + base[3:]
            if enclosure is not None:
                # The requested box bounds the TCP, not the grounded whole arm.
                self._lo, self._hi = list(enclosure["min"]), list(enclosure["max"])
                self.auto_outer_bounds = _copy_bounds(auto_outer)
                self.outer_bounds = _copy_bounds(auto_outer)
                self.reference_q = list(reference)
                self.reference_body_bounds = _copy_bounds(reference_body)
                self.q, self._local_base = list(seed), list(base)
                self._base = list(world_base)
                self.base_offset = [0., 0., self.BASE_Z]
                self._center_q = list(seed)
                self.actual, self.target = list(world_base), list(world_base)
                self._goal, self.seq = None, -1
                self._last_sample, self._last_ik = stamp, None
                self.configured = True
                self.status, self.why = "待启动", ""
                return self._reply(True)
            for attempt in range(7):
                if time.monotonic() >= deadline:
                    break
                # Setup is explicit and runs off the event loop. Prefer a useful
                # camera-sized rectangle, shrinking only after actual IK checks.
                width = 400 * .72 ** attempt
                half = [width / 2, self.BASE_Y_LIMIT, width / (2 * self.aspect)]
                self._lo = [world_base[i] - half[i] for i in range(3)]
                self._hi = [world_base[i] + half[i] for i in range(3)]
                # The base remains fixed. Validate nine X/Z points on the
                # middle plane, with explicit Rz ends only in rotation mode.
                reachable = True
                for fx in (-1, 0, 1):
                    for fy in (0,):
                        for fz in (-1, 0, 1):
                            for rz in ((-45, 0, 45) if self.rotation else (0,)):
                                target = [base[i] + f * half[i] for i, f in enumerate((fx, fy, fz))] + base[3:]
                                target[5] += rz
                                if not self.rotation:
                                    q, ep, er, ok = self._position_center(seed, target, lo, hi, deadline)
                                else:
                                    q, ep, er, ok = self.model.ik(seed, target, lo, hi, iters=45,
                                                                restarts=False, tol_mm=.08, tol_deg=.08,deadline=deadline)
                                if not ok or not self._valid_q(q) or ep > .08 or er > .08:
                                    reachable = False
                                    break
                            if not reachable: break
                        if not reachable: break
                    if not reachable: break
                if reachable:
                    self.auto_outer_bounds = _copy_bounds(auto_outer)
                    self.outer_bounds = _copy_bounds(outer)
                    self.reference_q = list(reference)
                    self.reference_body_bounds = _copy_bounds(reference_body)
                    self.q, self._local_base = list(seed), list(base)
                    self._base = list(world_base)
                    self.base_offset = [0., 0., self.BASE_Z]
                    self._center_q = list(seed)
                    self.actual, self.target = list(world_base), list(world_base)
                    self._goal, self.seq = None, -1
                    self._last_sample, self._last_ik = stamp, None
                    self.configured = True
                    self.status, self.why = "待启动", ""
                    return self._reply(True)
        self.status, self.why = "配置失败", ("跟随框中心暂时无法到达" if enclosure is not None
                                               else "限位内没有通过实测的居中跟随工作区")
        if time.monotonic() >= deadline:
            self.why = "中心求解超时，请调整范围后重试"
        return self._reply(False, self.why)

    def _credentials(self, payload):
        return (type(payload.get("token")) is str and payload["token"] == self.token
                and type(payload.get("session")) is str and payload["session"] == self.session)

    def start(self, payload, now=None, current_q=None):
        if not _schema(payload, ("token", "session")) or not self._credentials(payload):
            return self._reply(False, "配置或令牌已变化")
        if not self.configured or self.active:
            return self._reply(False, "请先配置或停止当前跟随")
        try:
            stamp = self._clock(now)
        except ValueError as exc:
            return self._reply(False, str(exc))
        try:
            if not self._body_inside(self._center_q, [0., 0., self.BASE_Z]):
                return self._reply(False, "初始整机超出外盒")
        except ValueError:
            return self._reply(False, "模型几何无效，无法启动")
        self.active = True
        self.token = secrets.token_hex(16)
        self.seq, self._last_sample, self._last_ik = -1, stamp, None
        self._last_observation = None
        self._loss_since = None
        self._input_generation += 1
        self._goal = None
        self._velocity = [0.] * 6
        # Start from the actual simulated posture. World moves to _center_q
        # using the normal synchronized SimRobot trajectory; never teleport.
        self.q = list(current_q) if current_q is not None else list(self._center_q)
        self.base_offset = [0., 0., self.BASE_Z]
        self.actual = self._world_pose(self.q, self.base_offset)
        self.target = list(self._base)
        self._reset_tracking()
        self.waiting = True
        self.returning = False
        self.status, self.why = "正在到中心，等待手掌", ""
        self.input_phase = "waiting"
        return self._reply(True)

    def wait(self, reason="未检测到手掌，保持位置等待"):
        """Suspend motion, not ownership. Only a fresh valid sample may resume."""
        if self.active:
            self._input_generation += 1
            self.waiting = True
            self.input_phase = "lost"
            self.returning = False
            self.target = list(self.actual)
            self._goal = None
            self._local_target = None
            self._last_ik = None
            self._velocity = [0.] * 6
            self._reset_tracking()
            self.status, self.why = str(reason)[:160], ""
        return self._reply(True)

    def return_to_center(self, reason="未检测到手掌，平滑返回中心并等待"):
        if self.active:
            if not self.returning:
                self._input_generation += 1
                self._goal = None
                self._local_target = None
                self._last_ik = None
                self._reset_tracking()
            self.waiting = self.returning = True
            self.input_phase = "lost"
            self.target = list(self._base)
            self.status, self.why = str(reason)[:160], ""
        return self._reply(True)

    def camera_missing(self, reason="手掌暂时丢失，保持当前位置", now=None):
        """One loss clock for missing palms, stream gaps and expired captures."""
        if not self.active:
            return self._reply(True)
        stamp = self._clock(now)
        self._camera_input = True
        if self._loss_since is None:
            # If no missing report arrived, expiry is the first known loss.
            deadline = (self._last_observation + self.OBSERVATION_TTL
                        if self._last_observation is not None else
                        self._last_sample if self._last_sample is not None else stamp)
            self._loss_since = min(stamp, deadline)
            self.wait(reason)
            self.input_phase = "holding"
        if stamp >= self._loss_since + self.CAMERA_LOSS_HOLD:
            return self.return_to_center("手掌连续丢失1秒，平滑返回中心并等待")
        return self._reply(True)

    def update_input_state(self, now):
        """Run before worker delivery AND joint integration; never renew age."""
        if not self.active:
            return
        stamp = self._clock(now)
        if self._camera_input:
            if self._loss_since is not None:
                self.camera_missing(now=stamp)
            elif self._last_observation is not None and stamp >= self._last_observation + self.OBSERVATION_TTL:
                self.camera_missing(now=stamp)
            elif self._last_observation is None and self._last_sample is not None:
                # Armed without a palm: stay put, using start as the loss onset.
                self.camera_missing(now=stamp)
        elif self._last_sample is not None and stamp - self._last_sample > self.TIMEOUT:
            self.return_to_center("等待新的输入，平滑返回中心")

    def sample(self, payload, now=None, *, captured_at=None, camera_gap=False):
        fields = ("token", "session", "seq", "u", "v", "palm_scale", "angle", "age_ms")
        payload = dict(payload) if isinstance(payload,dict) else {}
        hand_present = payload.pop("hand_present", True)
        opening = payload.pop("hand_open", None)
        if opening is not None and not _number(opening, 0, 1):
            return self._reply(False, "手掌张合程度无效")
        if type(hand_present) is not bool:
            return self._reply(False,"手掌状态无效")
        if not _schema(payload, fields) or not self._credentials(payload) or not self.active:
            return self._reply(False, "跟随未启动或帧令牌已失效")
        try:
            stamp = self._clock(now)
        except ValueError as exc:
            return self._reply(False, str(exc))
        if (type(payload["seq"]) is not int or not self.seq < payload["seq"] <= 2**53 - 1
                or not _number(payload["u"], 0, 1) or not _number(payload["v"], 0, 1)
                or not _number(payload["palm_scale"], 1e-6, 10)
                or not _number(payload["angle"], -3600, 3600)
                or not _number(payload["age_ms"], 0, 350)):
            return self._reply(False, "帧参数无效、过期或顺序错误")
        effective_stamp = stamp - payload["age_ms"] / 1000 if captured_at is None else captured_at
        if captured_at is not None and (not isinstance(captured_at, (int, float))
                or not math.isfinite(captured_at) or not 0 <= stamp-captured_at <= .3):
            return self._reply(False, "原始采集帧已过期")
        if hand_present and camera_gap and stamp >= effective_stamp + self.OBSERVATION_TTL:
            return self._reply(False, "原始采集帧已过期")
        if hand_present and self._last_observation is not None and effective_stamp < self._last_observation:
            return self._reply(False, "帧时间顺序错误")
        if (hand_present and self._loss_since is not None and self._last_observation is not None
                and effective_stamp <= self._last_observation):
            return self._reply(False, "恢复需要新的有效观测")
        self.seq, self._last_sample = payload["seq"], effective_stamp
        self._camera_input = bool(camera_gap)
        if not hand_present:
            if camera_gap:
                return self.camera_missing(now=stamp)
            return self.return_to_center()
        if self._loss_since is not None or self.returning:
            self._input_generation += 1
            self._goal = self._local_target = None
            self._velocity = [0.] * 6
            self._last_ik = None
        self._loss_since = None
        self._last_observation = effective_stamp
        self.input_phase = "tracking"
        self.waiting = False
        self.returning = False
        u, v = payload["u"], payload["v"]
        x = self._lo[0] if u == 0 else self._hi[0] if u == 1 else self._lo[0] + u * (self._hi[0] - self._lo[0])
        z = self._hi[2] if v == 0 else self._lo[2] if v == 1 else self._hi[2] - v * (self._hi[2] - self._lo[2])
        y = self._base[1]
        if self.depth and self.depth_mode == "gesture":
            # Local front is -Y; _rotate_pose carries this same front/back
            # meaning around the fixed base for all four selected directions.
            # Partial/missing finger geometry must never fall back to hand size.
            if opening is not None:
                y = self._hi[1] - opening * (self._hi[1] - self._lo[1])
                self._gesture_depth = y
            elif self._gesture_depth is not None:
                # 保持上次有效的局部深度；不能把张合漏检解释成回中指令。
                y = self._gesture_depth
        elif self.depth:
            self._scales = (self._scales + [payload["palm_scale"]])[-3:]
            if self.depth_range is not None:
                far, near = self.depth_range
                fraction = max(0., min(1., (median(self._scales)-far)/(near-far)))
                # A small/far palm reaches the far face, a large/near palm the
                # near face. Use the entire configured depth, not a log nudge.
                y = self._hi[1] - fraction * (self._hi[1]-self._lo[1])
            elif self._scale0 is None and len(self._scales) == 3:
                center = median(self._scales)
                if (max(self._scales) - min(self._scales)) / center <= .12:
                    self._scale0 = center
            if self.depth_range is None and self._scale0 is not None:
                # The matching front view looks from base -Y. A larger hand
                # approaches the lens, so the virtual TCP approaches that view.
                y -= 100 * math.log(payload["palm_scale"] / self._scale0)
        y = max(self._lo[1], min(self._hi[1], y))
        rz = self._base[5]
        if self.rotation:
            angle = payload["angle"]
            if self._angle_prev is None:
                self._angle0 = self._angle_unwrapped = angle
            else:
                self._angle_unwrapped += (angle - self._angle_prev + 180) % 360 - 180
            self._angle_prev = angle
            # Bound the accumulator as well as the command; noisy input cannot
            # build an arbitrarily large hidden rotation debt.
            self._angle_unwrapped = max(self._angle0 - 180, min(self._angle0 + 180, self._angle_unwrapped))
            rz += max(-45, min(45, self._angle_unwrapped - self._angle0))
        previous_target = self.target
        self.target = self._rotate_pose([x, y, z, self._base[3], self._base[4], rz])
        # A reversal must not keep integrating the previous direction while its
        # worker is in flight. Keep the accepted q; discard only unexecuted work.
        new_delta = [self.target[i]-self.actual[i] for i in range(3)]
        previous_delta = [previous_target[i]-self.actual[i] for i in range(3)]
        reversed_input = (math.dist(previous_target[:3],self.target[:3]) > 3.
            and sum(a*b for a,b in zip(previous_delta,new_delta)) < -9.)
        if reversed_input:
            self._wrist_recovery_reference = None
        reversed_goal = False
        if self._goal is not None:
            goal_pose = self.model.pose_vec(self._goal)
            old_delta = [goal_pose[i]-self.actual[i] for i in range(3)]
            reversed_goal = sum(a*b for a,b in zip(old_delta,new_delta)) < -1e-6
        if self._wrist_recovery_target is not None:
            # 内部回正路径本来就可能暂时远离用户目标。重复同一输入不能
            # 每帧清空其速度；真正的用户反向则立即撤销内部路径。
            if reversed_input:
                self._wrist_recovery_target = None
            else:
                reversed_goal = False
        if reversed_input or reversed_goal:
            self._goal = None
            # Preserve velocity for acceleration-limited braking/reversal.
            self._local_target = None
            # Includes reversal while _goal is still only in the worker. Merely
            # overtaking an old short goal is not a new input or an IK-rate waiver.
            if reversed_input:
                self._last_ik = None
        if self._goal is None:
            # Fresh observations are not evidence of motion or of IK success.
            previous=self._rejected_target or self._budget_key
            if (self._solver_cause not in ("local_continuity_hold", "budget_exhausted")
                    or not self._same_local_request(self._request_key(),previous)):
                self._solver_cause="waiting_solver"
                self.status, self.why = "等待局部求解", "新目标待连续路径求解；当前保持位置"
        else:
            self.status, self.why = "跟随中", ""
        return self._reply(True)

    def _request_key(self):
        target=self._tracking_target(self._solve_target())
        target[2]-=self.BASE_Z
        return (tuple(self.q),tuple(target),tuple(self._lower),tuple(self._upper),self.rotation)

    def _same_local_request(self, first, second):
        """A tiny SAME-DIRECTION scheduling neighborhood, not a reachability map.

        Only unchanged accepted joints/limits may reuse short-lived failures.
        Reversing across the current TCP or leaving 0.25mm resets immediately.
        Neither the actual target nor any FK acceptance threshold is quantized.
        """
        if first is None or second is None or first[2:] != second[2:]:
            return False
        if any(abs(a-b)>1e-9 for a,b in zip(first[0],second[0])):
            return False
        a,b=first[1],second[1]
        if math.dist(a[:3],b[:3])>self.CACHE_TARGET_MM or pose_rotation_distance(a,b)>.01:
            return False
        origin=[self.actual[i]-(self.BASE_Z if i==2 else 0.) for i in range(3)]
        return sum((a[i]-origin[i])*(b[i]-origin[i]) for i in range(3))>=0

    def accepts_solver_failure(self, result):
        return (not (self.waiting and not self.returning)
                and self._input_generation == result._input_generation
                and self._same_local_request(self._request_key(),result._request_key()))

    def record_solver_cost(self, key, cost, stamp):
        if not self._same_local_request(self._budget_key,key):
            self._budget_key,self._budget_spent,self._budget_retry_at=key,0.,0.
        self._budget_spent+=max(0.,cost)
        if self._budget_spent>=self.SOLVE_TOTAL_BUDGET_S:
            self._budget_retry_at=stamp+self.REJECT_RETRY_S

    def solver_ready(self, stamp):
        key=self._request_key()
        if not self._same_local_request(self._budget_key,key):
            self._budget_key,self._budget_spent,self._budget_retry_at=key,0.,0.
        if self._budget_spent>=self.SOLVE_TOTAL_BUDGET_S:
            if stamp<self._budget_retry_at:
                self._solver_cause="budget_exhausted"
                self.status,self.why="保持位置","局部累计计算预算已用完，短暂限频后重试；反向目标立即重新求解"
                return False
            self._budget_spent,self._budget_retry_at=0.,0.
        return True

    def accepts_solver_goal(self, result):
        """Latest-input gate for a threaded result; no state or cache writes.

        Identical/repeated observations can share a result. Moving input can
        also use an older short step ONLY when it still makes progress toward
        the latest target and its translation direction has not reversed.
        Lost-input holds and orientation changes never borrow a stale goal.
        The caller must additionally check owner/token/config/mode/age.
        """
        goal = result._goal
        if (goal is None or (self.waiting and not self.returning)
                or self._input_generation != result._input_generation
                or any(abs(a-b) > self.IK_JOINT_TRUST_DEG for a,b in zip(self.q,goal))):
            return False
        if self.target == result.target:
            return True
        if self.rotation and pose_rotation_distance(self.target,result.target) > .001:
            return False
        old_delta = [result.target[i]-self.actual[i] for i in range(3)]
        new_delta = [self.target[i]-self.actual[i] for i in range(3)]
        if sum(a*b for a,b in zip(old_delta,new_delta)) <= 0:
            return False
        if result._wrist_recovery_target is not None and not self.returning:
            return True
        # Worker model is an isolated copy, so this FK does not touch the
        # live model's single-entry cache either.
        goal_pose = result.model.pose_vec(goal)
        return math.dist(goal_pose[:3],self.target[:3]) < math.dist(self.actual[:3],self.target[:3])

    def _solve_target(self):
        return self.target

    def solve_goal(self, stamp):
        """Latest-target continuous servo: position first, no wrist-angle lock.

        The pure bounded local solver coordinates all six axes. World remains
        responsible for generation ownership and every executed path segment.
        """
        self._last_ik = stamp
        self._local_target = None
        self._wrist_recovery_target = self._wrist_recovery_reference = None
        self._reachability_cache = None
        self._rejected_target = self._subdivision_resume = None
        target = self._tracking_target(self.target)
        target[2] -= self.BASE_Z
        deadline = min(time.monotonic()+self.SOLVE_BUDGET_S,
                       getattr(self, '_solver_deadline', math.inf))
        started = time.monotonic()
        if self.rotation:
            # Explicit rotation mode retains its requested pose, but solves one
            # local bounded waypoint instead of old retry/hub/branch machinery.
            current = self.model.pose_vec(self.q)
            fraction = min(1., self.IK_LOOKAHEAD_MM/max(1e-9,math.dist(current[:3],target[:3])),
                           6./max(1e-9,pose_rotation_distance(current,target)))
            waypoint = interpolate_pose(current,target,fraction)
            low = [max(lo,q-self.IK_JOINT_TRUST_DEG) for lo,q in zip(self._lower,self.q)]
            high = [min(hi,q+self.IK_JOINT_TRUST_DEG) for hi,q in zip(self._upper,self.q)]
            q,ep,er,ok = self.model.ik(self.q,waypoint,low,high,iters=24,
                restarts=False,tol_mm=.2,tol_deg=.2,deadline=deadline,max_step_deg=2.)
            candidate_pose=self.model.pose_vec(q)
            current_cost=math.dist(current[:3],target[:3])+.5*pose_rotation_distance(current,target)
            candidate_cost=math.dist(candidate_pose[:3],target[:3])+.5*pose_rotation_distance(candidate_pose,target)
            progress=(self._valid_q(q) and all(a<=v<=b for a,v,b in zip(low,q,high))
                      and (ok or candidate_cost<current_cost-1e-5))
            result = dict(q=q if progress else None,position_error_mm=ep,
                          progress_mm=math.dist(current[:3],target[:3])-math.dist(self.model.pose_vec(q)[:3],target[:3]),
                          cause='pose_tracking' if progress else 'pose_local_limit',iterations=24)
        else:
            result = solve_position_step(self.model,list(self.q),target[:3],
                self._lower,self._upper,max_joint_step=self.IK_JOINT_TRUST_DEG,
                max_cartesian_step=self.IK_LOOKAHEAD_MM,deadline=deadline)
        self._goal = list(result['q']) if result.get('q') is not None else None
        self._solver_cause = result.get('cause','position_limit')
        if self._goal is not None:
            current=self.model.pose_vec(self.q)
            valid=(self._valid_q(self._goal) and
                   all(abs(a-b)<=self.IK_JOINT_TRUST_DEG+1e-8 for a,b in zip(self.q,self._goal)))
            if valid and not self.rotation:
                before=math.dist(current[:3],target[:3])
                after=math.dist(self.model.pose_vec(self._goal)[:3],target[:3])
                valid=before<=.05 or after<before-1e-8
            if not valid:
                self._goal=None
                self._solver_cause='invalid_progress'
        self._solver_detail = {key:(None if isinstance(value,float) and not math.isfinite(value) else value)
                               for key,value in result.items() if key!='q'}
        self._solver_detail.update(policy='position-priority-v2',
            elapsed_s=time.monotonic()-started,orientation_locked=bool(self.rotation))
        if self._goal is not None:
            self._local_target = self._world_pose(self._goal,[0.,0.,self.BASE_Z])
            self.status,self.why = ('返回中心','') if self.returning else ('跟随中','')
        else:
            self.status,self.why = '保持位置','当前方向暂未找到可行小步；换向后立即重新求解'

    def tick(self, dt, now=None, speed_pct=100, *, validate_step=None, solve=True):
        """推进纯类状态；可选同步回调 validate_step(before_q, candidate_q)。

        回调接收关节角列表副本，负责候选姿态及两姿态之间的路径检查。
        只有显式 True 才允许提交；拒绝或异常保留此前已接受的小步，
        清空剩余目标和速度。返回值仍表示本次是否已有安全姿态变化，
        不表示整批目标均已接受；默认 None 保持既有纯类调用兼容。
        """
        if not self.active:
            return False
        try:
            stamp = self._clock(now)
        except ValueError:
            self.stop("单调时钟异常")
            return False
        self.update_input_state(stamp)
        if not _number(dt, 0, 1) or not _number(speed_pct, 0, 100):
            self.stop("步长或速度参数无效")
            return False
        if dt == 0 or speed_pct == 0:
            return False
        if self.waiting and not self.returning:
            return False
        # Latest target only, <=30 IK updates/s with bounded near-face retries.
        if solve and (self._last_ik is None or stamp - self._last_ik >= self.IK_INTERVAL - 1e-9):
            self.solve_goal(stamp)
        if self._goal is None:
            self._velocity = [0.] * 6
            return False
        if (all(abs(a-b) <= 1e-10 for a,b in zip(self.q,self._goal))
                and all(abs(v) <= 1e-6 for v in self._velocity)):
            # 已静止时不重复验证零位移；World 常规安全评估仍检查环境变化。
            self._velocity = [0.] * 6
            return False
        # Bound wall-clock stalls and split at <=10 ms. Check every accepted
        # joint interpolation by FK, not merely the target or IK endpoint.
        remaining, changed = min(dt, .1), False
        factor = speed_pct / 100
        while remaining > 1e-9:
            step = min(.01, remaining)
            remaining -= step
            goal = self.q if self._goal is None else self._goal
            # Critically damped joint response carries velocity across each
            # latest-only IK waypoint. It eases reversals instead of snapping
            # to a fresh piecewise-constant joint velocity every camera frame.
            omega = 35. * math.sqrt(factor)
            decay = math.exp(-omega*step)
            error = [b-a for a,b in zip(self.q,goal)]
            velocity = [(v-omega*(v-omega*d)*step)*decay for v,d in zip(self._velocity,error)]
            delta = [d+(-d+(v-omega*d)*step)*decay for d,v in zip(error,self._velocity)]
            # Remove only the outward normal component near a TCP-box face.
            # Halving all six joints until the entire robot stops also removes
            # perfectly valid tangent motion (the old front/back-face freeze).
            local=self._rotate_pose(self.actual,inverse=True)
            jac=self.model.jacobian(self.q)[:3]
            radians=math.radians(-self.direction);c,s=math.cos(radians),math.sin(radians)
            rows=[[c*a-s*b for a,b in zip(jac[0],jac[1])],
                  [s*a+c*b for a,b in zip(jac[0],jac[1])],list(jac[2])]
            for axis,row in enumerate(rows):
                predicted=sum(a*b for a,b in zip(row,delta))
                low=min(0.,self._lo[axis]+.05-local[axis])
                high=max(0.,self._hi[axis]-.05-local[axis])
                corrected=max(low,min(high,predicted))
                if abs(corrected-predicted)>1e-12:
                    norm=sum(value*value for value in row)
                    if norm>1e-10:
                        delta=[d+(corrected-predicted)*v/norm for d,v in zip(delta,row)]
                        normal_v=sum(a*b for a,b in zip(row,velocity))
                        if normal_v*predicted>0:
                            velocity=[v-normal_v*a/norm for v,a in zip(velocity,row)]
            delta=[0. if abs(value)<1e-12 else value for value in delta]
            if not any(delta):
                self._velocity=velocity
                return changed
            ratios = [1.]
            for i, d in enumerate(delta):
                if d:
                    ratios.append(self.model.vmax[i] * factor * step / abs(d))
            end_pose = self._world_pose([a+d for a,d in zip(self.q,delta)], [0., 0., self.BASE_Z])
            distance = math.dist(self.actual[:3], end_pose[:3])
            if distance:
                ratios.append(300 * factor * step / distance)
            alpha = min(ratios)
            accepted = False
            for _ in range(8):
                candidate = [a + alpha * d for a, d in zip(self.q, delta)]
                offset = [0., 0., self.BASE_Z]
                pose = self._world_pose(candidate, offset)
                geometry_ok = (self._valid_q(candidate) and self._inside(pose)
                        and _number(offset[1], -self.BASE_Y_LIMIT, self.BASE_Y_LIMIT))
                continuity_ok = True
                # Position-follow leaves orientation free; joint limits and
                # World's swept-path checks still apply to every substep.
                geometry_ok = geometry_ok and continuity_ok
                travelled = math.dist(self.actual[:3], pose[:3])
                limit = 300 * factor * step
                if geometry_ok and travelled <= limit + 1e-8:
                    if validate_step is not None:
                        token = self.token
                        reason = "路径安全检查未通过"
                        try:
                            safe = validate_step(list(self.q), list(candidate)) is True
                        except Exception as exc:
                            safe = False
                            reason = f"路径安全检查异常（{type(exc).__name__}）"
                        # 同步回调可能触发停止／重配，旧候选不得在其后提交。
                        if not self.active or self.token != token:
                            return changed
                        if not safe:
                            self.status = "保持位置"
                            self.why = self.blocked_reason = reason
                            self._goal = None
                            self._velocity = [0.] * 6
                            return changed
                    if any(abs(a - b) > 1e-10 for a, b in zip(self.q, candidate)):
                        changed = True
                    self.q, self.actual, self.base_offset = candidate, list(pose), offset
                    self._velocity = [alpha*v for v in velocity]
                    if self.blocked_reason:
                        self.blocked_reason = self.why = ''
                        self.status = "返回中心" if self.returning else "跟随中"
                    accepted = True
                    break
                if geometry_ok and travelled > limit:
                    # FK is nonlinear in joint interpolation. A first-order
                    # speed estimate can overshoot by micrometres; halving here
                    # made practically every step run at half speed. Correct by
                    # measured distance, retaining the same strict speed bound.
                    alpha *= min(.999, limit/travelled*.999)
                else:
                    alpha *= .5
            if not accepted:
                self.status, self.why = "保持位置", ("工作区边界保护" if continuity_ok
                                                 else "腕轴连续性／姿态保护")
                self._goal = None
                self._velocity = [0.] * 6
                break
        return changed

    def stop(self, reason="已停止"):
        self.active = False
        self.input_phase = "stopped"
        self.waiting = False
        self.returning = False
        self.token = secrets.token_hex(16)
        self._goal = None
        self._loss_since = None
        self._input_generation += 1
        self._velocity = [0.] * 6
        self.status, self.why = "已停止", str(reason)[:160]
        return self._reply(True)

    def snapshot(self):
        a, b = self._lo, self._hi
        y = self._base[1]
        corners = [self._rotate_pose(p) for p in [[a[0], y, b[2]], [b[0], y, b[2]],
                   [b[0], y, a[2]], [a[0], y, a[2]]]]
        outer_a, outer_b = self.outer_bounds["min"], self.outer_bounds["max"]
        outer_y = (outer_a[1] + outer_b[1]) / 2
        outer_corners = [[outer_a[0], outer_y, outer_b[2]], [outer_b[0], outer_y, outer_b[2]],
                         [outer_b[0], outer_y, outer_a[2]], [outer_a[0], outer_y, outer_a[2]]]
        return {"configured": self.configured, "active": self.active, "waiting": self.waiting, "returning": self.returning,"blocked_reason":self.blocked_reason,
                "status": self.status, "why": self.why, "token": self.token,
                "input_phase": self.input_phase,
                "session": self.session, "aspect": self.aspect, "depth": self.depth,
                "direction": self.direction, "world_bounds": self.world_bounds(),
                "depth_range": list(self.depth_range) if self.depth_range else None,
                "depth_mode": self.depth_mode,
                "rotation": self.rotation, "control_policy": "position-priority-v2", "bounds": {"min": list(a), "max": list(b)},
                "corners": corners, "target": list(self.target),
                "local_target": list(self._local_target) if self._goal is not None and self._local_target else None,
                "solver_cause": self._solver_cause,
                "solver_detail": dict(self._solver_detail),
                "actual": list(self.actual), "q": list(self.q), "seq": self.seq,
                "base_offset": list(self.base_offset),
                "outer_bounds": _copy_bounds(self.outer_bounds),
                "auto_outer_bounds": _copy_bounds(self.auto_outer_bounds),
                "outer_corners": outer_corners,
                "reference_q": list(self.reference_q),
                "reference_body_bounds": _copy_bounds(self.reference_body_bounds)}
