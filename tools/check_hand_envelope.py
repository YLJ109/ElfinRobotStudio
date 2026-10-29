"""Pure geometry tests. Run only: python tools/run_offline_checks.py check_hand_envelope.py.

No World/real-controller imports, live capture, HTTP or hardware reads.
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
from itertools import product
import math
from pathlib import Path
import statistics
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.kinematics import RobotModel, _mat_identity, _mat_mul, _mat_trans, rpy_to_mat
from backend.core.hand_envelope import robot_bounds, tool_bounds, contains_bounds, union_bounds, make_enclosure


class FakeModel:
    flange = "end"

    def __init__(self):
        self.links = {
            "world":{"visual":None},
            "base":{"visual":{"bbox":{"min":[-2., -1., -.5], "max":[-1., 1., .5],
                # Deliberately incorrect centroid metadata must not affect corners.
                "center":[500.,500.,500.],"size":[1.,2.,1.]},
                "origin_xyz":[0.,0.,0.],"origin_rpy":[0.,0.,0.]}},
            "end":{"visual":None},
        }
        self.transforms = {"world":_mat_identity(),"base":_mat_identity(),"end":_mat_identity()}

    def fk(self, q):
        if len(q) != 6:
            raise ValueError("six joints required")
        return self.transforms


def point_box(point):
    return {"min":list(point),"max":list(point)}


class HandEnvelopeTests(unittest.TestCase):
    def assertVec(self, a, b, places=7):
        for x, y in zip(a, b):
            self.assertAlmostEqual(x,y,places=places)

    def test_fixed_base_is_included_not_just_movable_links(self):
        body = robot_bounds(FakeModel(),[0]*6)
        self.assertVec(body["min"],[-2000,-1000,-500])
        self.assertVec(body["max"],[150,1000,500])

    def test_bbox_min_max_not_mesh_centroid(self):
        model = FakeModel()
        first = robot_bounds(model,[0]*6)
        model.links["base"]["visual"]["bbox"]["center"] = [-1e9]*3
        model.links["base"]["visual"]["bbox"]["size"] = [1e9]*3
        self.assertEqual(first,robot_bounds(model,[0]*6))

    def test_visual_origin_rotation_then_fk_then_offset(self):
        model = FakeModel()
        visual = model.links["base"]["visual"]
        visual.update(bbox={"min":[0,0,0],"max":[.1,.2,.3]},
                      origin_xyz=[1,2,3],origin_rpy=[0,0,math.pi/2])
        model.transforms["base"] = _mat_mul(_mat_trans(4,5,6),rpy_to_mat(math.pi/2,0,0))
        body = robot_bounds(model,[0]*6,[10,20,30])
        # Rz(90)(x,y,z)=(-y,x,z); Rx(90)(a,b,c)=(a,-c,b).
        for x,y,z in product([0,.1],[0,.2],[0,.3]):
            point = [(5-y)*1000+10,(2-z)*1000+20,(8+x)*1000+30]
            self.assertTrue(contains_bounds(point_box(point),body))
        self.assertVec(body["max"],[5010,2020,8130])

    def test_default_tool_matches_declared_300_50_60_mm_box(self):
        body = tool_bounds(_mat_identity())
        self.assertVec(body["min"],[-150,-25,0])
        self.assertVec(body["max"],[150,25,60])

    def test_tool_180_is_symmetric_but_90_swaps_width_depth(self):
        identity = _mat_identity()
        a, b = tool_bounds(identity,rotation_z_deg=0), tool_bounds(identity,rotation_z_deg=180)
        self.assertVec(a["min"],b["min"]); self.assertVec(a["max"],b["max"])
        c = tool_bounds(identity,rotation_z_deg=90)
        self.assertVec(c["min"],[-25,-150,0]); self.assertVec(c["max"],[25,150,60])

    def test_tool_rotates_in_local_frame_before_tilted_flange(self):
        matrix = _mat_mul(_mat_trans(.2,.3,.4),rpy_to_mat(math.pi/2,0,0))
        body = tool_bounds(matrix,[10,20,30])
        self.assertVec(body["min"],[60,260,405])
        self.assertVec(body["max"],[360,320,455])

    def test_tool_ignores_actual_model_tcp_and_inputs_are_not_mutated(self):
        model = RobotModel("elfin5")
        q = [0,-30,-90,30,60,0]
        before = copy.deepcopy(model.links)
        first = robot_bounds(model,q)
        model.tcp_translation_mm = [9999]*3
        model.tcp_rpy_deg = [170,80,150]
        self.assertEqual(first,robot_bounds(model,q))
        self.assertEqual(before,model.links)
        self.assertEqual(q,[0,-30,-90,30,60,0])

    def test_actual_robot_every_link_corner_and_tool_corner_contained(self):
        model = RobotModel("elfin5")
        self.assertIn("elfin_base",model.links)
        for q in ([0]*6,[0,-30,-90,30,60,0],[25,-35,75,25,-55,10],[-30,20,-70,40,65,-10]):
            with self.subTest(q=q):
                offset = [13,-27,52]
                body = robot_bounds(model,q,offset)
                tf = model.fk(q)
                for name,link in model.links.items():
                    visual = link.get("visual")
                    if visual is None:
                        continue
                    # Independent reference composition, using kinematics matrices.
                    local = _mat_mul(_mat_trans(*visual["origin_xyz"]),rpy_to_mat(*visual["origin_rpy"]))
                    matrix = _mat_mul(tf[name],local)
                    bb = visual["bbox"]
                    for p in product(*zip(bb["min"],bb["max"])):
                        world = [sum(matrix[i][j]*p[j] for j in range(3))+matrix[i][3] for i in range(3)]
                        point = [world[i]*1000+offset[i] for i in range(3)]
                        # Different matrix evaluation order can round a few ulps.
                        self.assertTrue(all(body["min"][i]-1e-8 <= point[i] <= body["max"][i]+1e-8 for i in range(3)))
                self.assertTrue(contains_bounds(tool_bounds(tf[model.flange],offset),body))

    def test_base_offset_translates_entire_robot_and_tool(self):
        model = RobotModel("elfin5")
        q = [0]*6
        a, b = robot_bounds(model,q),robot_bounds(model,q,[12,-15,81])
        for key in ("min","max"):
            self.assertVec(b[key],[a[key][i]+[12,-15,81][i] for i in range(3)])

    def test_union_all_sampled_poses_and_fixed_enclosure(self):
        model = RobotModel("elfin5")
        bodies = [robot_bounds(model,q) for q in ([0,-30,-90,30,60,0],[25,-35,75,25,-55,10])]
        merged = union_bounds(*bodies)
        box = make_enclosure(merged,16/9)
        original = copy.deepcopy(box)
        for body in bodies:
            self.assertTrue(contains_bounds(body,box,40))
        outside = copy.deepcopy(merged); outside["max"][0] = box["max"][0]+.001
        self.assertFalse(contains_bounds(outside,box))
        self.assertEqual(box,original)

    def test_enclosure_minimum_margin_floor_aspect_and_minimal_dimensions(self):
        body = {"min":[-100,-50,0],"max":[200,70,500]}
        for aspect in (.5,1,4/3,16/9,3):
            with self.subTest(aspect=aspect):
                box = make_enclosure(body,aspect)
                span = [box["max"][i]-box["min"][i] for i in range(3)]
                self.assertAlmostEqual(span[0]/span[2],aspect)
                self.assertAlmostEqual(span[0],max(380,580*aspect))
                self.assertAlmostEqual(span[2],max(580,380/aspect))
                self.assertAlmostEqual(span[1],400)  # 120 + 2*(40+100).
                self.assertTrue(contains_bounds(body,box,40))
                self.assertGreaterEqual(body["min"][2]-box["min"][2],40)
                self.assertGreaterEqual(body["min"][1]-box["min"][1],140)

    def test_contains_boundary_margin_and_tiny_real_violation(self):
        box = {"min":[0,0,0],"max":[100,100,100]}
        self.assertTrue(contains_bounds(box,box))
        self.assertFalse(contains_bounds(box,box,.0001))
        self.assertTrue(contains_bounds({"min":[40]*3,"max":[60]*3},box,40))
        self.assertFalse(contains_bounds({"min":[-1e-10,0,0],"max":[1,1,1]},box))

    def test_bad_geometry_fails_closed_instead_of_skipping_visible_link(self):
        for change in ("bbox","origin_xyz","origin_rpy","fk","flange"):
            with self.subTest(change=change):
                model = FakeModel()
                if change in ("bbox","origin_xyz","origin_rpy"):
                    model.links["base"]["visual"].pop(change)
                elif change == "fk":
                    model.transforms.pop("base")
                else:
                    model.transforms.pop("end")
                with self.assertRaises(ValueError): robot_bounds(model,[0]*6)

    def test_nonfinite_q_offset_visual_and_fk_rejected(self):
        for bad in (math.nan,math.inf,-math.inf,True,"0"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): robot_bounds(FakeModel(),[bad]+[0]*5)
                with self.assertRaises(ValueError): robot_bounds(FakeModel(),[0]*6,[bad,0,0])
                model = FakeModel(); model.links["base"]["visual"]["bbox"]["max"][0] = bad
                with self.assertRaises(ValueError): robot_bounds(model,[0]*6)
                model = FakeModel(); model.transforms["base"][0][0] = bad
                with self.assertRaises(ValueError): robot_bounds(model,[0]*6)

    def test_bad_bounds_margin_aspect_rejected(self):
        body = {"min":[0]*3,"max":[100]*3}
        for bad in (math.nan,math.inf,-1,True,"40"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): contains_bounds(body,body,bad)
                with self.assertRaises(ValueError): make_enclosure(body,bad)
        for margin in (0,39.9):
            with self.assertRaises(ValueError): make_enclosure(body,1,margin)
        for invalid in ({}, {"min":[2]*3,"max":[1]*3}, {"min":[0,0],"max":[1]*3}):
            with self.assertRaises(ValueError): union_bounds(invalid)
        with self.assertRaises(ValueError): union_bounds()

    def test_no_stateful_backend_or_transport_imports(self):
        self.assertNotIn("backend.core.world",sys.modules)
        self.assertNotIn("backend.core.robot_real",sys.modules)

    def test_performance_baseline_without_platform_timing_gate(self):
        """Record full robot+tool bounds + containment, not an empty FK stub.

        Cached-pose and changed-pose runs expose the FK cache difference. Timing
        is evidence, not a machine-dependent success criterion. No World/network.
        """
        model = RobotModel("elfin5")
        base = [0,-30,-90,30,60,0]
        offset = [0,0,60]
        box = make_enclosure(robot_bounds(model,base,offset),16/9)
        for _ in range(20):
            contains_bounds(robot_bounds(model,base,offset),box,40)
        for mode in ("cached_pose", "changing_pose"):
            timings = []
            for i in range(300):
                q = list(base)
                if mode == "changing_pose":
                    q[0] += math.sin(i*.07)*2
                started = time.perf_counter_ns()
                contains_bounds(robot_bounds(model,q,offset),box,40)
                timings.append((time.perf_counter_ns()-started)/1e6)
            ordered = sorted(timings)
            print(f"ENVELOPE_PERF {mode} n={len(timings)} "
                  f"median_ms={statistics.median(timings):.4f} "
                  f"p95_ms={ordered[math.ceil(.95*len(ordered))-1]:.4f} "
                  f"max_ms={max(timings):.4f}",flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
