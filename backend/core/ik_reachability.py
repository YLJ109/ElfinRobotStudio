"""Conservative geometric early rejection, never an IK solver or safety check.

``None`` means no conclusion (including unsupported/malformed models), NOT
reachable. Proofs deliberately ignore joint limits, collisions and IK seeds.
Only the audited elfin5 chain and RobotModel TCP convention are supported.
This module does no I/O and does not instantiate a model or access hardware.
"""
from __future__ import annotations

import math

from . import kinematics as kin


# Match the default strict IK component tolerances, not a zero-error target.
POSITION_COMPONENT_TOL_MM = 0.05
ROTATION_COMPONENT_TOL_DEG = 0.05

# Pinned to the actual robot.json, including its rounded origin rotations.
# Any geometry/axis/topology change requires re-auditing, not extrapolation.
_CHAIN = (
    ("world_base", "fixed", "world", "elfin_base_link", (0., 0., 0.), (0., 0., 0.), (0, 0, 1)),
    ("elfin_base_joint", "fixed", "elfin_base_link", "elfin_base", (0., 0., 0.),
     (0., 0., 1.5707963267948966), (0, 0, 1)),
    ("elfin_joint1", "revolute", "elfin_base", "elfin_link1", (0., 0., .0735), (0., 0., 0.), (0, 0, 1)),
    ("elfin_joint2", "revolute", "elfin_link1", "elfin_link2", (-.078, 0., .1465),
     (1.5708, 1.3734, -1.5708), (0, 0, 1)),
    ("elfin_joint3", "revolute", "elfin_link2", "elfin_link3", (-.37262, .074541, -.0060028),
     (0., 0., 1.3734), (0, 0, -1)),
    ("elfin_joint4", "revolute", "elfin_link3", "elfin_link4", (0., .119, -.072),
     (0., 1.5708, 1.5708), (0, 0, 1)),
    ("elfin_joint5", "revolute", "elfin_link4", "elfin_link5", (-.0605, 0., .301),
     (-1.5708, 0., 1.5708), (0, 0, -1)),
    ("elfin_joint6", "revolute", "elfin_link5", "elfin_link6", (0., -.1005, -.0605),
     (1.5708, 1.5708, 0.), (0, 0, 1)),
    ("elfin_end_joint", "fixed", "elfin_link6", "elfin_end_link", (0., 0., .0545),
     (0., 0., -1.5708), (0, 0, 1)),
)
_DUMMY = ("elfin_dummy_joint", "fixed", "elfin_link6", "elfin_dummy_gripper", (0., -.0935, 0.),
          (math.pi / 2, math.pi / 2, 0.), (0, 0, 1))


def _vec(value, size=3):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return None
    if not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e9 for v in value):
        return None
    return tuple(float(v) for v in value)


def _norm(v):
    return math.hypot(*v)


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _scale(a, s):
    return tuple(x * s for x in a)


def _mv(r, v):
    return tuple(sum(r[i][j] * v[j] for j in range(3)) for i in range(3))


def _rot(rpy):
    return tuple(tuple(row[:3]) for row in kin.rpy_to_mat(*rpy)[:3])


def _product(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _transpose(r):
    return tuple(zip(*r))


def _audited(model):
    """Fail closed on model support, but never reject the requested target here.

    Also check FK's cached origins/children: a partially edited model must not
    acquire a proof for geometry different from the geometry its FK executes.
    """
    if (type(model) is not kin.RobotModel or model.variant != "elfin5"
            or model.root != "world" or model.flange != "elfin_end_link"
            or kin.TCP_OFFSET_Z_MM != 20.0):
        return None
    specs = (*_CHAIN, _DUMMY)
    if not isinstance(model.joints, list) or len(model.joints) != len(specs):
        return None
    children = {}
    for joint, spec in zip(model.joints, specs):
        name, kind, parent, child, xyz, rpy, axis = spec
        if (not isinstance(joint, dict)
                or any(joint.get(key) != val for key, val in
                       (("name", name), ("type", kind), ("parent", parent), ("child", child)))
                or _vec(joint.get("xyz")) != xyz or _vec(joint.get("rpy")) != rpy
                or _vec(joint.get("axis")) != axis
                or bool(joint.get("limit")) != (kind == "revolute")):
            return None
        origin = kin._mat_mul(kin._mat_trans(*xyz), kin.rpy_to_mat(*rpy))
        if model._origins.get(name) != origin:
            return None
        children.setdefault(parent, []).append(joint)
    moving = [j for j in model.joints if j.get("limit")]
    if (model.movable != moving or model.names != [j["name"] for j in moving]
            or model._children != children):
        return None
    translation = _vec(model.tcp_translation_mm)
    rotation = _vec(model.tcp_rpy_deg)
    if translation is None or rotation is None:
        return None
    return translation, rotation


def _failure(code, proof, observed, upper, tolerance, **values):
    # All outputs are JSON scalars/lists, unrounded to keep evidence reviewable.
    return {
        "reason_code": code,
        "why": (f"目标位置超出含 TCP 偏移的几何球形上界：{observed:.3f} > {upper:.3f} mm"
                if code == "unreachable_position" else
                f"固定姿态要求的虚拟腕心距离超出臂长上界：{observed:.3f} > {upper:.3f} mm")
               + f"（已保留 {tolerance:.3f} mm 容差）",
        "proof": proof,
        "model": "elfin5",
        "observed_lower_bound_mm": observed,
        "upper_bound_mm": upper,
        "gap_mm": observed - upper,
        "tolerance_mm": tolerance,
        "position_component_tolerance_mm": POSITION_COMPONENT_TOL_MM,
        "rotation_component_tolerance_deg": ROTATION_COMPONENT_TOL_DEG,
        **values,
    }


def reachability_bound(model, target, *, position_only=False, plane_constraint=False, tolerance_scale=1.):
    """Return a necessary-condition violation or None; never claim reachability.

    target: XYZ mm + fixed-axis RPY degrees (XYZ alone is also accepted in
    position_only mode). position_only skips ALL orientation-dependent proofs.
    Arbitrary finite RobotModel TCP translations/rotations are supported by
    inverse rigid transforms, not by assuming the tool lies along local Z.
    Unknown chains, malformed inputs and unaudited model types return None.
    """
    try:
        return _bound(model, target, position_only, plane_constraint, tolerance_scale)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        # This optimization is optional; bad inputs remain the caller's job.
        return None


def _bound(model, target, position_only, plane_constraint=False, tolerance_scale=1.):
    if type(position_only) is not bool or type(plane_constraint) is not bool:
        return None
    if type(tolerance_scale) not in (int,float) or not math.isfinite(tolerance_scale) or not 1<=tolerance_scale<=100:
        return None
    def failure(*args,**values):
        return _failure(*args,**values,
            position_component_tolerance_mm=POSITION_COMPONENT_TOL_MM*tolerance_scale,
            rotation_component_tolerance_deg=ROTATION_COMPONENT_TOL_DEG*tolerance_scale)
    size = len(target) if isinstance(target, (list, tuple)) else 0
    if size not in ((3, 6) if position_only else (6,)) or _vec(target, size) is None:
        return None
    audited = _audited(model)
    if audited is None:
        return None
    tcp, tcp_rpy = audited
    xyz = tuple(target[:3])
    # Root-to-end triangle inequality, then the full end-frame TCP lever.
    # offset and TCP translation share end-frame axes BEFORE tool rotation.
    lever_end = _add(tcp, (0., 0., kin.TCP_OFFSET_Z_MM))
    sphere = sum(_norm(spec[4]) * 1000 for spec in _CHAIN) + _norm(lever_end)
    numerical_pad = 1e-6 + 1e-12 * max(sphere, _norm(xyz), 1.)
    position_tol = math.sqrt(3) * POSITION_COMPONENT_TOL_MM*tolerance_scale
    sphere_tol = position_tol + numerical_pad
    distance = _norm(xyz)
    if distance > sphere + sphere_tol:
        return failure("unreachable_position", "root_tcp_triangle_sphere", distance, sphere, sphere_tol,
                        tcp_lever_end_mm=list(lever_end))
    if position_only:
        return None

    # Ri maps link i coordinates into world coordinates. q6 leaves ez invariant.
    # Rtcp = R6 * Rend_origin * Rtool; remove both fixed rotations, including
    # arbitrary TCP roll/pitch. Ptcp = P6 + R6 * (tend + Rend_origin*lever_end).
    end = _CHAIN[8]
    rend = _rot(end[5])
    rtool = _rot(tuple(math.radians(v) for v in tcp_rpy))
    rtcp = _rot(tuple(math.radians(v) for v in target[3:]))
    r6 = _product(_product(rtcp, _transpose(rtool)), _transpose(rend))
    wrist_offset = 100.5
    lever6 = _add(_scale(end[4], 1000), _mv(rend, lever_end))
    lever_wrist = _add(lever6, (0., 0., wrist_offset))
    wrist = _sub(xyz, _mv(r6, lever_wrist))

    # W = P6 - 100.5*z6. For the ACTUAL rounded URDF rotations:
    # v = t56 - 100.5*R56*ez; w = t45 + R45*(vz*ez).
    # q5 rotates only v's xy part, bounded by eps5; q4 rotates w's xy
    # part, bounded by eps4. Thus for every q4/q5:
    # |W-P2| <= |t23| + |t34 + wz*R34*ez| + eps5 + eps4.
    # No spherical-wrist or perfectly rounded pi/2 assumption is made.
    t23, t34, t45, t56 = (_scale(_CHAIN[i][4], 1000) for i in (4, 5, 6, 7))
    r34, r45, r56 = (_rot(_CHAIN[i][5]) for i in (5, 6, 7))
    ez = (0., 0., 1.)
    v = _sub(t56, _scale(_mv(r56, ez), wrist_offset))
    w = _add(t45, _mv(r45, (0., 0., v[2])))
    eps5, eps4 = math.hypot(v[0], v[1]), math.hypot(w[0], w[1])
    arm1 = _norm(t23)
    arm2 = _norm(_add(t34, _scale(_mv(r34, ez), w[2])))
    upper = arm1 + arm2 + eps5 + eps4
    # q1 moves P2 on this circle. Relaxing q1's limits only lowers Dmin.
    shoulder_radius = math.hypot(*_CHAIN[3][4][:2]) * 1000
    shoulder_z = (_CHAIN[2][4][2] + _CHAIN[3][4][2]) * 1000
    radial = math.hypot(wrist[0], wrist[1])
    required = math.hypot(radial - shoulder_radius, wrist[2] - shoulder_z)

    # Strict IK allows each SO(3) log-error component <= tol_deg. Perturbing
    # orientation by theta moves the recovered wrist by <= 2|lever|sin(theta/2).
    # Dmin is 1-Lipschitz, so reserve both this and Cartesian component errors.
    angle_tol = math.sqrt(3) * math.radians(ROTATION_COMPONENT_TOL_DEG*tolerance_scale)
    orientation_slack = 2 * _norm(lever_wrist) * math.sin(angle_tol / 2)
    tolerance = position_tol + orientation_slack + numerical_pad
    if required > upper + tolerance:
        return failure("unreachable_pose", "elfin5_virtual_wrist_triangle", required, upper, tolerance,
                        virtual_wrist_mm=list(wrist), wrist_lever_link6_mm=list(lever_wrist),
                        shoulder_circle_radius_mm=shoulder_radius, shoulder_height_mm=shoulder_z,
                        arm_lengths_mm=[arm1, arm2], urdf_rounding_slack_mm=eps5 + eps4,
                        orientation_slack_mm=orientation_slack)
    if plane_constraint:
        # 可选的更紧必要条件：J2/J3轴平行，虚拟腕心到肩的法向投影
        # 由链长确定，不是肩圆上任意一点都能朝向目标。保留URDF舍入误差。
        # n是J2轴在J1坐标中的方向；q1只旋转xy，nz保持不变。
        n = _mv(_rot(_CHAIN[3][5]), ez)
        shoulder = _scale(_CHAIN[3][4], 1000)
        arm2_vector = _add(t34, _scale(_mv(r34, ez), w[2]))
        planar_offset = t23[2] + arm2_vector[2]
        base_z = _CHAIN[2][4][2]*1000
        projection = sum(a*b for a,b in zip(shoulder,n)) + planar_offset - (wrist[2]-base_z)*n[2]
        norm_xy = n[0]*n[0] + n[1]*n[1]
        coefficient = (shoulder[0]*n[0]+shoulder[1]*n[1])/norm_xy
        residual = math.hypot(shoulder[0]-coefficient*n[0],shoulder[1]-coefficient*n[1])
        # 真正可接受姿态的W可能离目标W有tolerance偏差；同时放宽法向
        # 投影和最终距离，避免在容差边界误拒绝实际FK可达点。
        dot_upper = coefficient*projection + abs(coefficient)*(eps4+eps5+tolerance) + residual*radial
        required_plane = math.sqrt(max(0.,radial*radial+shoulder_radius**2-2*dot_upper
                                      +(wrist[2]-shoulder_z)**2))
        if required_plane > upper+tolerance:
            return failure("unreachable_pose", "elfin5_wrist_shoulder_plane", required_plane, upper, tolerance,
                virtual_wrist_mm=list(wrist), plane_rounding_slack_mm=eps4+eps5,
                shoulder_projection_upper_mm2=dot_upper)
    return None
