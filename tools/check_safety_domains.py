"""Safety causes remain independent while the combined interlock stays strict."""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.safety import evaluate
from backend.core import config,sim_tool


class SafetyDomains(unittest.TestCase):
    def setUp(self):
        self.model = SimpleNamespace(flange="tool", lower=[-360]*6, upper=[360]*6)
        self.settings = dict(safety_yellow_pct=90, safety_red_pct=95, safety_alarm_pct=100,
            fence=dict(x=[-900,900], y=[-900,900], z=[0,1400], enabled=True), ground_enabled=True)

    def risk(self, q=None, x=0, z=700):
        cap = dict(link="tool", a=[x,0,z], b=[x,0,z], r_mm=0)
        return evaluate(self.model, q or [0]*6, self.settings, caps=[cap])

    def test_joint_red_does_not_claim_a_fence_collision(self):
        r = self.risk([350,0,0,0,0,0])
        self.assertEqual(r["level"], "red")
        self.assertEqual(r["joint_level"], "red")
        self.assertEqual(r["workspace_level"], "green")
        self.assertEqual(r["fence_level"], "green")
        self.assertEqual(r["ground_level"], "green")
        self.assertEqual(r["collisions"], [])
        self.assertIn("J1", r["reasons"][0])

    def test_wall_yellow_red_and_contact_keep_combined_interlock(self):
        for gap, level in ((11,"green"),(10,"yellow"),(5,"red"),(0,"alarm"),(-1,"alarm")):
            with self.subTest(gap=gap):
                r = self.risk(x=900-gap)
                self.assertEqual(r["fence_level"], level)
                self.assertEqual(r["workspace_level"], level)
                self.assertEqual(r["level"], level)
                self.assertEqual(r["joint_level"], "green")
                self.assertEqual(r["ground_level"], "green")
                self.assertEqual(r["fence_gap_mm"], gap)

    def test_floor_does_not_claim_wall_collision(self):
        r = self.risk(z=4)
        self.assertEqual(r["level"], "red")
        self.assertEqual(r["ground_level"], "red")
        self.assertEqual(r["workspace_level"], "red")
        self.assertEqual(r["fence_level"], "green")
        self.assertTrue(any("地面" in reason for reason in r["reasons"]))
        self.assertFalse(any("围栏" in reason for reason in r["reasons"]))

    def test_object_collision_does_not_paint_walls_red(self):
        r = evaluate(self.model,[0]*6,self.settings,caps=[dict(link="tool",a=[0,0,700],b=[0,0,700],r_mm=0)],
            obj_hits=[dict(link="tool",name="box",gap_mm=-2)])
        self.assertEqual(r["level"], "alarm")
        self.assertEqual(r["workspace_level"], "green")
        self.assertTrue(any(c["type"] == "object" for c in r["collisions"]))

    def test_disabled_detection_reports_no_measured_gap(self):
        self.settings = copy.deepcopy(self.settings)
        self.settings["fence"]["enabled"] = False
        self.settings["ground_enabled"] = False
        r = self.risk(x=1000,z=-100)
        self.assertEqual(r["workspace_level"], "green")
        self.assertIsNone(r["fence_gap_mm"])
        self.assertIsNone(r["ground_gap_mm"])


class MovingLinkGround(unittest.TestCase):
    def test_current_default_thresholds_are_90_95_100(self):
        model=SimpleNamespace(flange='tool',lower=[-360]*6,upper=[360]*6)
        settings=copy.deepcopy(config.DEFAULTS)
        settings['mode']='real'
        self.assertEqual([settings[k] for k in ('safety_yellow_pct','safety_red_pct','safety_alarm_pct')],[90,95,100])
        for fraction,level in ((.89,'green'),(.90,'yellow'),(.949,'yellow'),(.95,'red'),(.99,'red'),(1.,'alarm')):
            result=evaluate(model,[360*fraction,0,0,0,0,0],settings,caps=[])
            self.assertEqual(result['level'],level)
            self.assertEqual(result['collisions'],[])

    def test_sim_joint_proximity_is_advisory_but_actual_limits_and_collision_remain_blocking(self):
        model=SimpleNamespace(flange='tool',lower=[-360]*6,upper=[360]*6)
        settings=copy.deepcopy(config.DEFAULTS)
        settings['mode']='sim'
        for angle in (342.,350.,360.,-360.):
            result=evaluate(model,[angle,0,0,0,0,0],settings,caps=[])
            self.assertEqual(result['level'],'green')
            self.assertFalse(result['joint_out_of_bounds'])
            self.assertIn('仿真提示，不拦截',result['reasons'][0])
        for angle in (360.001,-360.001):
            result=evaluate(model,[angle,0,0,0,0,0],settings,caps=[])
            self.assertEqual(result['level'],'alarm')
            self.assertTrue(result['joint_out_of_bounds'])
        cap=dict(link='tool',a=[0,0,-1],b=[0,0,-1],r_mm=0)
        result=evaluate(model,[350,0,0,0,0,0],settings,caps=[cap])
        self.assertEqual(result['level'],'alarm')
        self.assertTrue(result['collisions'])

    def test_reported_ghost_crossing_floor_is_alarm_even_when_tool_is_high(self):
        model=sim_tool.model_for('elfin5',sim_tool.DEFAULT_TOOL)
        settings=copy.deepcopy(config.DEFAULTS)
        q=[-114.65,123.9,83.65,.028,18.669,.023]
        result=evaluate(model,q,settings,*map(list,zip(*config.MANUAL_LIMITS)))
        self.assertEqual(result['ground_level'],'alarm')
        self.assertTrue(any(hit['type']=='ground' and hit['link']!='elfin_end_link' for hit in result['collisions']))
        self.assertGreater(model.pose_vec(q)[2],400)

    def test_joint_limit_is_not_a_collision_and_mounting_base_does_not_alarm(self):
        model=sim_tool.model_for('elfin5',sim_tool.DEFAULT_TOOL)
        settings=copy.deepcopy(config.DEFAULTS)
        for q in ([0]*6,[-360,67.15,83.65,.03,18.67,.02]):
            result=evaluate(model,q,settings,*map(list,zip(*config.MANUAL_LIMITS)))
            self.assertEqual(result['ground_level'],'green',result)
            self.assertFalse(result['collisions'],result)
        self.assertEqual(result['joint_level'],'alarm')

if __name__ == "__main__":
    unittest.main(verbosity=2)
