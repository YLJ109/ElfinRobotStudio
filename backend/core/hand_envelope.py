"""Pure whole-arm AABB geometry for the independent hand-follow simulation.

robot.json visual.bbox.min/max and visual origins, as well as model.fk(), use
metres/radians. Public bounds and base offsets here use millimetres. bbox.center
is NOT necessarily (min+max)/2 in the exported meshes: never derive corners from
that field. Iterate model.links, not just movable joints (the base is fixed).

The reference tool is attached to model.flange (elfin_end_link), NOT to a
TCP-offset pose. Its conservative local box is x +/-150, y +/-25, z 0..60 mm,
rotated 180 degrees about local Z. The 300x50x10 plate, two 50-mm cups and a
radius-20 connector inside the plate depth are covered. This does not represent
arbitrary imported tools, protruding hoses, or unmeasured mounting extensions.

No World, state persistence, IO, network or transport dependency. Invalid or
missing visible geometry raises ValueError, never an unsafe partial envelope.
"""
from __future__ import annotations

from itertools import product
import math
from numbers import Real

__all__ = ["robot_bounds", "tool_bounds", "contains_bounds", "union_bounds", "make_enclosure"]


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _vector(value, length, name):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    return [_number(v, name) for v in value]


def _bounds(value):
    if not isinstance(value, dict) or "min" not in value or "max" not in value:
        raise ValueError("bounds require min/max vectors")
    lo = _vector(value["min"], 3, "min")
    hi = _vector(value["max"], 3, "max")
    if any(a > b for a, b in zip(lo, hi)):
        raise ValueError("bounds min must not exceed max")
    return lo, hi


def _matrix(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("FK transform must be a finite 4x4 matrix")
    rows = [_vector(row, 4, "FK row") for row in value]
    if rows[3] != [0., 0., 0., 1.]:
        raise ValueError("FK transform must be affine")
    return rows


def _corners(lo, hi):
    return product(*[(a, b) for a, b in zip(lo, hi)])


def _apply(matrix, point):
    return [sum(matrix[i][j] * point[j] for j in range(3)) + matrix[i][3] for i in range(3)]


def _rpy(point, angles):
    """Fixed-axis URDF XYZ: Rz @ Ry @ Rx, angles in radians."""
    x, y, z = point
    rx, ry, rz = angles
    c, s = math.cos(rx), math.sin(rx)
    y, z = c*y - s*z, s*y + c*z
    c, s = math.cos(ry), math.sin(ry)
    x, z = c*x + s*z, -s*x + c*z
    c, s = math.cos(rz), math.sin(rz)
    return [c*x - s*y, s*x + c*y, z]


def _from_points(points):
    lo, hi, count = [math.inf]*3, [-math.inf]*3, 0
    for point in points:
        point = _vector(point, 3, "transformed corner")
        count += 1
        lo = [min(a, b) for a, b in zip(lo, point)]
        hi = [max(a, b) for a, b in zip(hi, point)]
    if not count:
        raise ValueError("no geometric points")
    return {"min": lo, "max": hi}


def tool_bounds(flange_transform, base_offset_mm=None, rotation_z_deg=180):
    """Reference tool AABB; transform is the raw end-link FK in metres.

    Optional rotation is for geometric verification; robot_bounds always uses
    the reference tool's 180-degree mounting. Do not pass model.flange_pose().
    """
    transform = _matrix(flange_transform)
    offset = _vector([0, 0, 0] if base_offset_mm is None else base_offset_mm, 3, "base offset")
    angle = math.radians(_number(rotation_z_deg, "tool rotation"))
    points = []
    for corner in _corners([-.15, -.025, 0.], [.15, .025, .06]):
        p = _apply(transform, _rpy(corner, [0., 0., angle]))
        points.append([v*1000 + d for v, d in zip(p, offset)])
    return _from_points(points)


def robot_bounds(model, q, base_offset_mm=None):
    """Return min/max mm of every visual link + the default reference tool.

    Missing visual (None) is a legitimate framelike URDF link. A present visual
    must have valid bbox/origins and an FK transform; otherwise fail closed.
    The helper does not mutate q, FK matrices, model TCP or input geometry.
    """
    if not isinstance(q, (list, tuple)) or not q:
        raise ValueError("q must contain finite joint angles")
    angles = [_number(v, "joint angle") for v in q]
    offset = _vector([0, 0, 0] if base_offset_mm is None else base_offset_mm, 3, "base offset")
    transforms = model.fk(angles)
    if not isinstance(transforms, dict) or not isinstance(model.links, dict) or not model.links:
        raise ValueError("model must provide links and FK transforms")
    points = []
    for name, link in model.links.items():
        if not isinstance(link, dict):
            raise ValueError(f"invalid link: {name}")
        visual = link.get("visual")
        if visual is None:
            continue
        if not isinstance(visual, dict):
            raise ValueError(f"invalid visual: {name}")
        lo, hi = _bounds(visual.get("bbox"))
        origin = _vector(visual.get("origin_xyz"), 3, "visual origin_xyz")
        rpy = _vector(visual.get("origin_rpy"), 3, "visual origin_rpy")
        transform = _matrix(transforms.get(name))
        for corner in _corners(lo, hi):
            local = [a+b for a, b in zip(_rpy(corner, rpy), origin)]
            world = _apply(transform, local)
            points.append([v*1000+d for v, d in zip(world, offset)])
    if not points:
        raise ValueError("model has no visual geometry")
    flange = _matrix(transforms.get(model.flange))
    return union_bounds(_from_points(points), tool_bounds(flange, offset))


def union_bounds(*bodies):
    """Union of one or more AABBs, e.g. union_bounds(*sampled_pose_bounds)."""
    if not bodies:
        raise ValueError("union requires at least one bounds")
    points = []
    for body in bodies:
        lo, hi = _bounds(body)
        points.extend([lo, hi])
    return _from_points(points)


def contains_bounds(body, box, margin=0):
    """True iff the entire body is inside box with >=margin mm on every axis.

    Boundary equality is included. Invalid bounds/margins raise ValueError.
    No positive tolerance silently admits points outside the envelope.
    """
    a, b = _bounds(body)
    lo, hi = _bounds(box)
    margin = _number(margin, "margin")
    if margin < 0:
        raise ValueError("margin must be nonnegative")
    return all(x-l >= margin and h-y >= margin for x, y, l, h in zip(a, b, lo, hi))


def make_enclosure(body, aspect, margin=40):
    """Smallest centered X/Z aspect-matched enclosure with >=40 mm padding.

    Supported camera width/height ratio: .5..3 (same as hand-follow configure).
    X/Z get at least margin on EACH side. Y gets margin + 100 mm on EACH side
    for whole-arm relative-depth motion. No fixed multi-metre workspace is used.
    Returns a fixed box; callers must not grow it to accept an out-of-box move.
    """
    lo, hi = _bounds(body)
    aspect = _number(aspect, "aspect")
    margin = _number(margin, "margin")
    if not .5 <= aspect <= 3:
        raise ValueError("camera aspect must be between .5 and 3")
    if margin < 40:
        raise ValueError("enclosure margin must be at least 40 mm")
    center = [a/2+b/2 for a, b in zip(lo, hi)]
    width = max(hi[0]-lo[0]+2*margin, (hi[2]-lo[2]+2*margin)*aspect)
    height = width/aspect
    low = [center[0]-width/2, lo[1]-margin-100, center[2]-height/2]
    high = [center[0]+width/2, hi[1]+margin+100, center[2]+height/2]
    # Center/half-extent subtraction can cancel near zero: one ulp of the
    # resulting face alone is not enough. Round outward at the scale of all
    # operands (only a few floating-point ulps, not a larger work area).
    rounding = 8 * max(math.ulp(v) for v in lo+hi+center+[width,height,margin])
    result = {"min":[math.nextafter(v-rounding, -math.inf) for v in low],
              "max":[math.nextafter(v+rounding, math.inf) for v in high]}
    _bounds(result)
    if not contains_bounds(body, result, margin):
        raise ValueError("enclosure arithmetic cannot preserve the requested margin")
    return result
