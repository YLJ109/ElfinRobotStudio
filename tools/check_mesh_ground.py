"""Source-mesh false-positive and exact tool-plane regression, offline only."""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import json
import math
from pathlib import Path
import statistics
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core import config
from backend.core.kinematics import _mat_identity
from backend.core.mesh_clearance import mesh_vertices
from backend.core.sim_tool import model_for,DEFAULT_TOOL
from backend.core.safety import evaluate


class MeshGround(unittest.TestCase):
    def test_reported_false_ground_stop_has_source_mesh_clearance_not_empty_box_corner(self):
        m=model_for('elfin5',DEFAULT_TOOL)
        # IK reconstruction of rounded screenshot TCP [53.2,-638.9,548.4].
        # This is a reproducible geometry case, not a recorded controller pose.
        q=[-263.660272599,101.69532529,86.550563958,173.434449079,74.949600809,1.721835782]
        samples=m.moving_ground_clearances(q)
        link2=next(s for s in samples if s['link']=='elfin_link2')
        self.assertAlmostEqual(link2['gap_mm'],94.083,delta=.01)
        self.assertEqual(link2['geometry'],'source-mesh')
        self.assertGreater(min(s['gap_mm'] for s in samples),85)
        risk=evaluate(m,q,copy.deepcopy(config.DEFAULTS),*map(list,zip(*config.MANUAL_LIMITS)))
        self.assertEqual(risk['ground_level'],'green',risk)
        self.assertFalse(risk['collisions'])
        self.assertGreater(risk['nearest_ground']['gap_mm'],85)

    def test_real_mesh_penetration_is_still_alarm_and_mount_is_excluded(self):
        m=model_for('elfin5',DEFAULT_TOOL)
        settings=copy.deepcopy(config.DEFAULTS)
        penetration=[-114.65,123.9,83.65,.028,18.669,.023]
        risk=evaluate(m,penetration,settings,*map(list,zip(*config.MANUAL_LIMITS)))
        self.assertEqual(risk['ground_level'],'alarm',risk)
        self.assertTrue(any(h['type']=='ground' for h in risk['collisions']))
        rest=evaluate(m,[0]*6,settings)
        self.assertEqual(rest['ground_level'],'green')
        self.assertFalse(any(s['link']=='elfin_link1' for s in m.moving_ground_clearances([0]*6)))

    def test_warm_query_never_rereads_or_rebuilds_source_mesh(self):
        m=model_for('elfin5',DEFAULT_TOOL)
        q=[0,30,60,0,45,0]
        first=m.moving_ground_clearances(q)
        with patch.object(Path,'read_bytes',side_effect=AssertionError('hot path disk IO')):
            for _ in range(20):self.assertEqual(m.moving_ground_clearances(q),first)
            other=model_for('elfin5',DEFAULT_TOOL)
            self.assertEqual(other.moving_ground_clearances(q),first)

    def test_cached_mesh_runtime_timing_is_reported_not_a_live_fps_claim(self):
        m=model_for('elfin5',DEFAULT_TOOL);samples=[]
        for i in range(120):
            start=time.perf_counter()
            m.moving_ground_clearances([0,30+i*.01,60,0,45,0])
            samples.append((time.perf_counter()-start)*1000)
        print(json.dumps({'mesh_query_median_ms':statistics.median(samples),
            'mesh_query_p95_ms':sorted(samples)[int(len(samples)*.95)],
            'cache_hits':mesh_vertices.cache_info().hits,'live_fps_verified':False}))


class ToolPlanes(unittest.TestCase):
    def test_four_walls_use_tool_surfaces_with_90_95_contact_thresholds(self):
        tool=copy.deepcopy(DEFAULT_TOOL);tool['rotation']=[0,0,0]
        m=model_for('elfin5',tool);settings=copy.deepcopy(config.DEFAULTS)
        for axis,sign,half in ((0,1,150),(0,-1,150),(1,1,25),(1,-1,25)):
            for gap,level in ((11,'green'),(10,'yellow'),(5,'red'),(0,'alarm'),(-1,'alarm')):
                with self.subTest(axis=axis,sign=sign,gap=gap):
                    matrix=_mat_identity();matrix[2][3]=.7
                    matrix[axis][3]=sign*(900-half-gap)/1000
                    m.fk=lambda _q,matrix=matrix:{m.flange:matrix}
                    risk=evaluate(m,[0]*6,settings,caps=[])
                    self.assertAlmostEqual(risk['fence_gap_mm'],gap,places=4)
                    self.assertEqual(risk['fence_level'],level,risk)
                    self.assertEqual(risk['ground_level'],'green')
                    self.assertFalse(any('余量占用' in s for s in risk['reasons']))

    def test_floor_uses_cylinder_box_support_and_does_not_inflate_beyond_end_faces(self):
        tool=copy.deepcopy(DEFAULT_TOOL);tool['rotation']=[0,0,0]
        m=model_for('elfin5',tool);settings=copy.deepcopy(config.DEFAULTS)
        for gap,level in ((11,'green'),(10,'yellow'),(5,'red'),(0,'alarm'),(-1,'alarm')):
            matrix=_mat_identity();matrix[2][3]=gap/1000
            m.fk=lambda _q,matrix=matrix:{m.flange:matrix}
            risk=evaluate(m,[0]*6,settings,caps=[])
            self.assertAlmostEqual(risk['ground_gap_mm'],gap,places=4)
            self.assertEqual(risk['ground_level'],level,risk)


if __name__=='__main__':unittest.main(verbosity=2)
