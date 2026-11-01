"""Pure geometry regression. Run only through the disposable offline runner.

Register check_ik_reachability.py in tools/offline_checks.json, then run:
    backend/.venv/Scripts/python.exe -B tools/run_offline_checks.py check_ik_reachability.py
No World/main import, live backend, camera, hardware, or user-data access.
"""
import copy
import math
from pathlib import Path
import random
import sys
from unittest.mock import patch

from _offline_guard import require_offline_copy

require_offline_copy()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import ik_reachability as reach  # noqa: E402
from backend.core.kinematics import RobotModel  # noqa: E402


def main():
    target = [570.8209, 4.339706, 966.0592, 90, 0, -33]
    screenshot = RobotModel("elfin5", [0, 0, -15], [0, 0, 90])
    proof = reach.reachability_bound(screenshot, target)
    assert proof and proof["reason_code"] == "unreachable_pose", proof
    assert abs(proof["observed_lower_bound_mm"] - 953.8873504214401) < 1e-7, proof
    assert abs(proof["upper_bound_mm"] - 806.1778128634146) < 1e-7, proof
    assert abs(proof["gap_mm"] - 147.70953755802554) < 1e-7, proof
    assert proof["tolerance_mm"] > math.sqrt(3) * .05
    assert reach.reachability_bound(screenshot, target, position_only=True) is None
    assert reach.reachability_bound(screenshot, target[:3], position_only=True) is None

    # World uses a shallow model copy. Bound queries must neither populate nor
    # replace either model's FK cache, even when the copy shares geometry maps.
    screenshot.pose_vec([12., -25., 40., -15., 33., 60.])
    original_key, original_value = screenshot._fk_key, screenshot._fk_value
    original_content = copy.deepcopy(original_value)
    isolated = copy.copy(screenshot)
    assert reach.reachability_bound(isolated, target)["reason_code"] == "unreachable_pose"
    assert reach.reachability_bound(isolated, screenshot.pose_vec(list(original_key))) is None
    assert isolated._fk_key == original_key and isolated._fk_value is original_value
    assert screenshot._fk_key == original_key and screenshot._fk_value is original_value
    assert screenshot._fk_value == original_content

    # Hundreds of actual FK outputs, including arbitrary lateral tools, rotated
    # TCPs, zero/negative/cancelled/long offsets and joint-limit extremes.
    rng = random.Random(103927)
    tools = [([0, 0, -15], [0, 0, 90]), ([0, 0, 0], [0, 0, 0]),
             ([0, 0, -20], [90, 30, -40]), ([120, -75, 220], [45, -50, 135]),
             ([-300, 160, -190], [-90, 90, 25]), ([0, 0, 2500], [0, 180, 90]),
             ([2600, -1800, 3900], [120, -75, 172]),
             ([-1700, 800, -2300], [-177, 35, -150])]
    count = 0
    tolerance_count = 0
    for translation, rotation in tools:
        model = RobotModel("elfin5", translation, rotation)
        seeds = [[0.] * 6, model.lower, model.upper]
        seeds += [[rng.uniform(lo, hi) for lo, hi in zip(model.lower, model.upper)] for _ in range(40)]
        for q in seeds:
            pose = model.pose_vec(q)
            assert reach.reachability_bound(model, pose) is None, (translation, rotation, q, pose)
            assert reach.reachability_bound(model, pose, plane_constraint=True) is None, (q,pose)
            assert reach.reachability_bound(model, pose, position_only=True) is None, (q, pose)
            # UI rounded poses must not turn a reachable target into a rejection.
            assert reach.reachability_bound(model, [round(x, 3) for x in pose]) is None
            # This actual q remains an acceptable strict-IK solution: each
            # Cartesian error is .049mm and each WORLD SO(3) log-error component
            # is +/- .049deg. Construct the rotation on SO(3), not by adding
            # Euler angles (which would not preserve the component tolerance).
            axis = (1., -1., 1.)
            delta = reach.kin._mat_rot(axis, math.radians(math.sqrt(3) * .049))
            current = reach.kin.rpy_to_mat(*map(math.radians, pose[3:]))
            perturbed = reach.kin._mat_mul(delta, current)
            _, perturbed_rpy = reach.kin.mat_to_xyzrpy(perturbed)
            nearby = [pose[i] + (.049 if i != 1 else -.049) for i in range(3)]
            nearby += [math.degrees(v) for v in perturbed_rpy]
            assert reach.reachability_bound(model, nearby) is None, (translation, rotation, q, nearby)
            assert reach.reachability_bound(model, nearby, plane_constraint=True) is None, (translation,rotation,q,nearby)
            loose_rotation=reach.kin._mat_mul(reach.kin._mat_rot(axis,math.radians(math.sqrt(3)*.095)),current)
            _,loose_rpy=reach.kin.mat_to_xyzrpy(loose_rotation)
            loose=[pose[i]+(.095 if i!=1 else -.095) for i in range(3)]+list(map(math.degrees,loose_rpy))
            assert reach.reachability_bound(model,loose,plane_constraint=True,tolerance_scale=2.) is None,(q,loose)
            assert reach.reachability_bound(model, nearby, position_only=True) is None
            tolerance_count += 1
            count += 1
    assert count >= 200
    from backend.core.sim_tool import DEFAULT_TOOL,model_for
    grid_model=model_for('elfin5',DEFAULT_TOOL)
    corner=reach.reachability_bound(grid_model,[-600.,349.5,824.5,90.,0.,-90.],plane_constraint=True)
    assert corner and corner['proof']=='elfin5_wrist_shoulder_plane',corner
    print('GRID_CORNER_PROOF',corner)

    # The sphere must include the TCP lever, rather than reuse a bare-arm bound.
    long_tool = RobotModel("elfin5", [0, 0, 2500], [0, 0, 0])
    long_pose = long_tool.pose_vec([0.] * 6)
    assert math.hypot(*long_pose[:3]) > 2000
    assert reach.reachability_bound(long_tool, long_pose) is None
    far = [1e5, 0, 0, 0, 0, 0]
    for position_only in (False, True):
        result = reach.reachability_bound(long_tool, far, position_only=position_only)
        assert result and result["reason_code"] == "unreachable_position"

    # Exercise the tolerance gates directly near both analytic boundaries.
    # The synthetic target does not assert sufficiency/reachability at equality.
    sphere = sum(math.hypot(*s[4]) * 1000 for s in reach._CHAIN) + 5
    assert reach.reachability_bound(screenshot, [sphere + .08, 0, 0], position_only=True) is None
    assert reach.reachability_bound(screenshot, [sphere + .10, 0, 0], position_only=True)
    wrist_x = 78 + proof["upper_bound_mm"]
    # Reuse screenshot orientation, convert chosen W back into TCP coordinates.
    z6 = [-math.sin(math.radians(33)), -math.cos(math.radians(33)), 0.]
    for extra, reject in ((0., False), (.1, False),
                          (.99 * proof["tolerance_mm"], False),
                          (1.01 * proof["tolerance_mm"], True), (1., True)):
        pose = [wrist_x + extra + 160 * z6[0], 160 * z6[1], 220., 90., 0., -33.]
        result = reach.reachability_bound(screenshot, pose)
        assert bool(result) == reject, (extra, result)
        if result:
            assert result["reason_code"] == "unreachable_pose"

    # Decline unknown geometry even for far targets: no unsupported proof.
    mutations = [lambda m: setattr(m, "variant", "elfin10"),
                 lambda m: setattr(m, "flange", "elfin_link6"),
                 lambda m: setattr(m, "root", "other_world"),
                 lambda m: m.joints[5].update(axis=[1., 0., 0.]),
                 lambda m: m.joints[6].update(xyz=[-.0605, 0., .302]),
                 lambda m: m.joints[7].update(rpy=[math.pi / 2, math.pi / 2, 0.]),
                 lambda m: m.joints[3].update(parent="elfin_link2"),
                 lambda m: m.joints.append(copy.deepcopy(m.joints[-1])),
                 lambda m: m._origins["elfin_joint4"][0].__setitem__(3, .1),
                 lambda m: m._children.clear(),
                 lambda m: setattr(m, "tcp_translation_mm", [0, 0, float("nan")]),
                 lambda m: setattr(m, "tcp_rpy_deg", [0, float("inf"), 0]),
                 lambda m: setattr(m, "tcp_translation_mm", [False, 0, 0])]
    for mutate in mutations:
        model = copy.deepcopy(screenshot)
        mutate(model)
        assert reach.reachability_bound(model, far) is None, mutate
        assert reach.reachability_bound(model, far, position_only=True) is None, mutate
    for invalid in (None, [], [0] * 5, [0, 0, 0, 0, 0, True], [float("nan")] * 6, ["1"] * 6):
        assert reach.reachability_bound(screenshot, invalid) is None
    assert reach.reachability_bound(object(), far) is None
    class UnverifiedModel(RobotModel):
        pass
    unverified = copy.deepcopy(screenshot)
    unverified.__class__ = UnverifiedModel
    assert reach.reachability_bound(unverified, far) is None
    with patch.object(reach.kin, "TCP_OFFSET_Z_MM", 21):
        assert reach.reachability_bound(screenshot, far) is None
    # Prove this path cannot hide iterative IK calls.
    with patch.object(RobotModel, "ik", side_effect=AssertionError("must not call IK")):
        assert reach.reachability_bound(screenshot, target)["reason_code"] == "unreachable_pose"
    print(f"ik-reachability: PASS; {count} actual FK poses x strict/position/rounded; "
          f"{tolerance_count} FK targets within strict IK tolerances; "
          "shallow-copy cache isolation, screenshot proof, TCP sphere, "
          "tolerance boundaries, unsupported-chain fallbacks")


if __name__ == "__main__":
    main()
