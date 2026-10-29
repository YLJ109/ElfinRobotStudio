"""精确线段-AABB接触回归；仅经离线副本runner，无World或真实设备。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.objects import capsule_object_gap


def box(size=(2, 2, 2), xyz=(0, 0, 0)):
    return {'kind': 'box', 'xyz': list(xyz), 'size': list(size)}


def capsule(a, b=None, radius=1):
    return {'a': list(a), 'b': list(a if b is None else b), 'r_mm': radius}


def sampled_gap(cap, obj, divisions=2048):
    """Independent grid oracle, not an implementation of the production cuts.

    Distance to a closed AABB is 1-Lipschitz. The nearest grid parameter to the
    true minimizer is at most 1/(2*N) away. Thus 0 <= sampled-exact <= L/(2*N).
    This gives a rigorous discretization bound, including interior minima.
    """
    center, size = obj['xyz'], obj['size']
    start, end = cap['a'], cap['b']
    delta = [end[i] - start[i] for i in range(3)]
    best = math.inf
    for step in range(divisions + 1):
        t = step / divisions
        squared = 0.
        for axis in range(3):
            excess = max(abs(start[axis] + delta[axis] * t - center[axis]) - size[axis] / 2, 0.)
            squared += excess * excess
        best = min(best, squared)
    return math.sqrt(best) - cap['r_mm'], math.dist(start, end) / (2 * divisions)


class ObjectContactChecks(unittest.TestCase):
    def test_thin_chess_piece_uses_actual_top_not_circumsphere_height(self):
        cap = capsule((-80, 0, 16), (80, 0, 16), 2)
        for width in (70, 330, 1000):
            with self.subTest(width=width):
                # Top is z=11, so capsule surface has exactly 3mm clearance.
                self.assertAlmostEqual(capsule_object_gap(cap, box((width, width, 2), (0, 0, 10))), 3)
        # Passing just above the top is safe even with a very wide flat piece.
        self.assertGreater(capsule_object_gap(capsule((0, 0, 11.01), radius=.005),
                                                 box((70, 70, 2), (0, 0, 10))), 0)

    def test_real_intersections_are_negative_including_crossings_with_both_ends_outside(self):
        obj = box((70, 70, 2), (0, 0, 10))
        for cap in (capsule((-100, 0, 10), (100, 0, 10), 2),
                    capsule((0, 0, -50), (0, 0, 100), 2),
                    capsule((-100, -100, -90), (100, 100, 110), 2),
                    capsule((0, 0, 10), radius=2)):
            with self.subTest(cap=cap):
                self.assertAlmostEqual(capsule_object_gap(cap, obj), -2)

    def test_face_contact_tangent_and_epsilon_separation(self):
        obj = box()
        for gap in (-1e-5, 0, 1e-5):
            cap = capsule((-3, 0, 2 + gap), (3, 0, 2 + gap), 1)
            self.assertAlmostEqual(capsule_object_gap(cap, obj), gap, places=10)
        self.assertLess(capsule_object_gap(capsule((0, 0, 1.5), radius=1), obj), 0)

    def test_zero_length_segment_uses_point_to_box_distance(self):
        obj = box((2, 4, 6))
        self.assertAlmostEqual(capsule_object_gap(capsule((4, 5, 6), radius=.5), obj), math.sqrt(27) - .5)
        self.assertAlmostEqual(capsule_object_gap(capsule((0, 0, 0), radius=.5), obj), -.5)
        self.assertAlmostEqual(capsule_object_gap(capsule((1, 2, 3), radius=.5), obj), -.5)
        self.assertEqual(capsule_object_gap(capsule((0, 0, 0), radius=0), obj), 0)

    def test_endpoint_is_nearest_when_infinite_line_would_hit(self):
        # Infinite x-axis intersects the box; this finite segment never reaches it.
        cap = capsule((-5, 0, 0), (-3, 0, 0), .25)
        self.assertAlmostEqual(capsule_object_gap(cap, box()), 1.75)
        cap = capsule((3, 0, 0), (5, 0, 0), .25)
        self.assertAlmostEqual(capsule_object_gap(cap, box()), 1.75)

    def test_diagonal_stationary_minimum_is_between_endpoints_and_face_cuts(self):
        cap = capsule((2, 4, 0), (4, 2, 0), .5)
        # Nearest segment point is (3,3,0), nearest box edge is (1,1,0).
        self.assertAlmostEqual(capsule_object_gap(cap, box()), math.sqrt(8) - .5, places=12)
        self.assertAlmostEqual(capsule_object_gap(capsule((-3, -3, -3), (3, 3, 3), .2), box()), -.2)

    def test_parallel_segment_crosses_axis_regions_while_remaining_outside_two_faces(self):
        cap = capsule((-4, 2, 2), (4, 2, 2), .25)
        self.assertAlmostEqual(capsule_object_gap(cap, box()), math.sqrt(2) - .25, places=12)
        # A centerline on the top face remains an intersection for positive radius.
        self.assertAlmostEqual(capsule_object_gap(capsule((-4, 0, 1), (4, 0, 1), .25), box()), -.25)

    def test_sphere_keeps_radius_semantics_instead_of_box_full_extents(self):
        obj = {'kind': 'sphere', 'xyz': [0, 0, 0], 'size': [5, 99, 99]}
        self.assertAlmostEqual(capsule_object_gap(capsule((-10, 8, 0), (10, 8, 0), 2), obj), 1)
        self.assertAlmostEqual(capsule_object_gap(capsule((-10, 7, 0), (10, 7, 0), 2), obj), 0)
        self.assertAlmostEqual(capsule_object_gap(capsule((0, 0, 0), radius=2), obj), -7)
        self.assertAlmostEqual(capsule_object_gap(capsule((10, 0, 0), (20, 0, 0), 2), obj), 3)

    def test_cylinder_retains_existing_conservative_sphere_proxy(self):
        obj = {'kind': 'cylinder', 'xyz': [0, 0, 0], 'size': [3, 3, 8]}
        self.assertAlmostEqual(capsule_object_gap(capsule((-10, 8, 0), (10, 8, 0), 2), obj), 1)

    def test_translation_axis_permutation_endpoint_reversal_and_scale_invariance(self):
        obj, cap = box((4, 6, .2), (2, -3, 8)), capsule((-8, 5, 10), (12, -1, 9), .3)
        expected = capsule_object_gap(cap, obj)
        self.assertAlmostEqual(capsule_object_gap(capsule(cap['b'], cap['a'], cap['r_mm']), obj), expected, places=10)
        for order in ((2, 0, 1), (1, 2, 0)):
            rotated = {**obj, 'xyz': [obj['xyz'][i] for i in order], 'size': [obj['size'][i] for i in order]}
            segment = capsule([cap['a'][i] for i in order], [cap['b'][i] for i in order], cap['r_mm'])
            self.assertAlmostEqual(capsule_object_gap(segment, rotated), expected, places=10)
        shift = [999000, -899000, 799000]
        shifted = {**obj, 'xyz': [obj['xyz'][i] + shift[i] for i in range(3)]}
        segment = capsule([cap['a'][i] + shift[i] for i in range(3)], [cap['b'][i] + shift[i] for i in range(3)], cap['r_mm'])
        self.assertAlmostEqual(capsule_object_gap(segment, shifted), expected, places=8)
        for factor in (.01, 100):
            scaled = {**obj, 'xyz': [v * factor for v in obj['xyz']], 'size': [v * factor for v in obj['size']]}
            segment = capsule([v * factor for v in cap['a']], [v * factor for v in cap['b']], cap['r_mm'] * factor)
            self.assertAlmostEqual(capsule_object_gap(segment, scaled), expected * factor, places=8)

    def test_seeded_random_segments_match_dense_sampling_with_proven_error_bound(self):
        rng = random.Random(20261029)
        for index in range(64):
            center = [rng.uniform(-100, 100) for _ in range(3)]
            size = [rng.uniform(.1, 120) for _ in range(3)]
            if index % 3 == 0: size[2] = .1  # Strongly anisotropic thin objects.
            obj = box(size, center)
            start = [center[i] + rng.uniform(-150, 150) for i in range(3)]
            end = [center[i] + rng.uniform(-150, 150) for i in range(3)]
            if index % 8 == 0: end[index % 3] = start[index % 3]
            if index % 16 == 0: end = start[:]
            cap = capsule(start, end, rng.uniform(0, 12))
            sampled, bound = sampled_gap(cap, obj)
            exact = capsule_object_gap(cap, obj)
            with self.subTest(index=index):
                self.assertTrue(math.isfinite(exact))
                self.assertLessEqual(exact, sampled + 1e-8, 'exact minimum cannot exceed a sampled distance')
                self.assertLessEqual(sampled - exact, bound + 1e-8, 'difference exceeds the Lipschitz grid error bound')
                self.assertAlmostEqual(capsule_object_gap(capsule(end, start, cap['r_mm']), obj), exact, places=8)

    def test_contact_query_does_not_mutate_inputs_or_import_world(self):
        obj, cap = box((70, 70, 2), (10, 20, 10)), capsule((-20, 0, 30), (30, 40, 50), 3)
        before = copy.deepcopy((obj, cap))
        capsule_object_gap(cap, obj)
        self.assertEqual((obj, cap), before)
        self.assertNotIn('backend.core.world', sys.modules)
        self.assertNotIn('backend.main', sys.modules)


if __name__ == '__main__':
    unittest.main(verbosity=2)
