"""Offline regressions for Cartesian slider IK, using only official model geometry."""
import math
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.kinematics import RobotModel, interpolate_pose, pose_rotation_distance, rpy_to_mat
from backend.core.robot_sim import SimRobot
from backend.core.world import World
from backend.core import config
from backend.core.planning import prepare_movel


def preview_world():
    # Exercise the real World method without constructing storage or any link.
    w = World.__new__(World)
    w.preview_q = None
    w._preview_executing = False
    w._cfg_rev = 0
    w.settings = copy.deepcopy(config.DEFAULTS)
    # This suite verifies the raw flange math. Shared reference-tool TCP is
    # covered by check_workspace_follow.py instead of changing these targets.
    w.settings.update(mode='sim', sim_tool=None, sim_speed_pct=100)
    w.objects = type('Objects', (), {'list': lambda self: []})()
    w.model = RobotModel('elfin5')
    w.joints_known = lambda: True
    w.current_joints = lambda: [0]*6
    w.limits = lambda: (w.model.lower, w.model.upper)
    w.precheck = lambda *a, **kw: None
    w.log = type('Log', (), {'add': lambda *a, **kw: None})()
    return w


class CartesianTests(unittest.TestCase):
    def test_partial_preview_uses_shortest_rotation_across_wrap(self):
        # 正确：原目标不可达时，裁剪必须沿SO(3)短弧，不能把179到-179插成0度。
        w = preview_world()
        start = [0., 0., 700., 0., 0., 179.]
        target = [100., 0., 700., 0., 0., -179.]
        trials = []
        def reject(_model, seed, goal, *_args, **_kwargs):
            trials.append(list(goal))
            return list(seed), 100., 2., False
        with patch.object(RobotModel, 'ik', reject), \
             patch.object(RobotModel, 'pose_vec', return_value=start):
            result = w.ik_pose(target, [0.] * 6, allow_partial=True)
        self.assertFalse(result['ok'])
        self.assertGreater(len(trials), 1)
        for trial in trials[1:]:
            self.assertLessEqual(pose_rotation_distance(start, trial), 2.000001)

    def setUp(self):
        self.m = RobotModel('elfin5')

    def assert_pose(self, result, target):
        q, p, r, ok = result
        self.assertTrue(ok, f'Known reachable pose rejected: {p:.3f}mm / {r:.3f}deg')
        e = self.m._err(q, target)
        self.assertLess(max(abs(v) for v in e[:3]), .05)
        self.assertLess(max(abs(v) for v in e[3:]), .05)
        self.assertTrue(all(a <= x <= b for a, x, b in zip(self.m.lower, q, self.m.upper)))

    def test_known_reachable_pose_from_singular_zero(self):
        # Previous preview's seven starting seeds all reject this FK-generated pose.
        target = self.m.pose_vec([36.16276633, 8.77385203, -4.10573759,
                                  90.08759884, -28.25831472, -83.53935888])
        self.assert_pose(self.m.ik([0]*6, target), target)

    def test_retract_from_fully_extended_zero(self):
        target = self.m.pose_vec([0]*6)
        target[2] -= 5
        self.assert_pose(self.m.ik([0]*6, target), target)

    def test_geometric_jacobian_matches_unrounded_fk(self):
        q = [23, -36, 71, 18, -54, 82]
        j = self.m.jacobian(q)
        for axis in range(6):
            a, b = q[:], q[:]
            a[axis] += .01
            b[axis] -= .01
            pa, pb = self.m.pose_vec(a), self.m.pose_vec(b)
            for row in range(3):
                self.assertAlmostEqual(j[row][axis], (pa[row]-pb[row])/.02, delta=.12)

    def test_continuous_axis_drag_from_last_solution(self):
        q = [50, 0, -90, 0, 90, -50]
        target = self.m.pose_vec(q)
        for _ in range(12):
            target[0] += 5
            result = self.m.ik(q, target)
            self.assert_pose(result, target)
            q = result[0]

    def test_outside_workspace_does_not_claim_convergence(self):
        target = self.m.pose_vec([0]*6)
        target[2] += 100
        q, p, r, ok = self.m.ik([0]*6, target)
        self.assertFalse(ok)
        self.assertGreater(p, 90)

    def test_round_trip_at_orientation_wrap(self):
        q = [0, -25, 50, 0, -25, 179]
        target = self.m.pose_vec(q)
        seed = q[:]
        seed[5] -= 4
        self.assert_pose(self.m.ik(seed, target), target)

    def test_so3_interpolation_wraps_shortest_path_and_preserves_translation(self):
        start=[10,20,30,0,0,179];end=[30,40,50,0,0,-179]
        middle=interpolate_pose(start,end,.5)
        self.assertEqual(middle[:3],[20,30,40])
        self.assertAlmostEqual(abs(middle[5]),180,places=7)
        self.assertAlmostEqual(pose_rotation_distance(start,end),2,places=7)
        for fraction in (0,.1,.5,.9,1):
            p=interpolate_pose(start,end,fraction)
            self.assertAlmostEqual(pose_rotation_distance(start,p),2*fraction,places=6)
        self.assertEqual(interpolate_pose(start,end,1),end)

    def test_so3_compound_and_gimbal_interpolation_has_uniform_angular_progress(self):
        for start,end in (([0,0,0,70,89,179],[0,0,0,-40,91,-179]),
                          ([0,0,0,0,90,0],[0,0,0,180,90,180]),
                          ([0]*6,[0,0,0,180,0,0]),
                          ([0]*6,[0,0,0,0,0,-179.9999])):
            angle=pose_rotation_distance(start,end)
            for fraction in (.1,.5,.9):
                p=interpolate_pose(start,end,fraction)
                self.assertTrue(all(math.isfinite(n) for n in p))
                self.assertAlmostEqual(pose_rotation_distance(start,p),angle*fraction,places=5)
        self.assertLess(interpolate_pose([0]*6,[0,0,0,0,0,-179.9999],.5)[5],0)

    def test_pose_interpolation_rejects_nonfinite_or_invalid_fraction(self):
        for a,b,t in (([0]*5,[0]*6,.5),([0]*6,[0]*5,.5),([0]*6,[0]*6,-.1),
                      ([0]*6,[0]*6,float('nan')),([0]*6,[0,0,0,0,0,float('inf')],.5)):
            with self.assertRaises(ValueError):interpolate_pose(a,b,t)

    def test_world_uses_previous_ghost_seed_and_reports_achieved_pose(self):
        w = preview_world()
        seed = [50, 0, -90, 0, 90, -50]
        target = self.m.pose_vec(seed)
        target[0] += 10
        r = w.ik_pose(target, seed, True)
        self.assertTrue(r['ok'])
        self.assertFalse(r['clipped'])
        self.assertLess(max(abs(v) for v in self.m._err(r['joints'], target)[:3]), .05)
        self.assertLess(max(abs(a-b) for a,b in zip(seed,r['joints'])), 10)

    def test_boundary_clips_to_solved_pose_not_inaccurate_requested_target(self):
        w = preview_world()
        target = self.m.pose_vec([0]*6)
        target[0] += 100
        r = w.ik_pose(target, [0]*6, True)
        self.assertTrue(r['ok'])
        self.assertTrue(r['clipped'])
        self.assertLess(r['fraction'], 1)
        self.assertLess(max(abs(a-b) for a,b in zip(r['achieved'],self.m.pose_vec(r['joints']))), .01)
        self.assertGreater(r['requested_pos_err_mm'], .05)

    def test_no_progress_outside_workspace_keeps_previous_pose(self):
        w = preview_world()
        target = self.m.pose_vec([0]*6)
        target[2] += 100
        r = w.ik_pose(target, [0]*6, True)
        self.assertFalse(r['ok'])
        self.assertNotIn('joints', r)

    def test_smooth_profile_has_no_internal_stops_and_respects_speed(self):
        sim = SimRobot(self.m, [90]*6)
        sim.speed_pct = 100
        target = [30, -20, 40, 15, -25, 10]
        duration = sim.move_j(target)
        previous = sim.q[:]
        peak = 0
        velocities = []
        dt = duration / 200
        for _ in range(200):
            sim.tick(dt)
            v = [(a-b)/dt for a,b in zip(sim.q,previous)]
            velocities.append(v)
            peak = max(peak, max(abs(x) for x in v))
            self.assertAlmostEqual(sim.q[0]/target[0],sim.q[2]/target[2],places=9)
            previous = sim.q[:]
        self.assertLessEqual(peak, 90.001)
        self.assertTrue(all(abs(v[2]) > 0 for v in velocities[1:-1]))
        self.assertLess(abs(velocities[0][2]), 1)
        self.assertLess(abs(velocities[-1][2]), 1)
        for a,b in zip(sim.q,target): self.assertAlmostEqual(a,b,places=8)

    def test_prepared_movel_real_ik_curve_and_measured_cartesian_approximation(self):
        settings = copy.deepcopy(config.DEFAULTS)
        settings.update(mode='sim', vel_source='urdf', sim_speed_pct=100, ground_enabled=False)
        settings['fence']['enabled'] = False
        start = [0.,-25.,50.,0.,-25.,0.]
        pose = self.m.pose_vec(start)
        target = pose[:]; target[0] += 16.
        plan = prepare_movel(self.m,start,target,settings,self.m.lower,self.m.upper,[],
                             context={'mode':'sim','real_connected':False,'revision':1})
        sim = SimRobot(self.m,self.m.vmax)
        sim.q=start[:];sim.speed_pct=100
        sim.move_prepared(plan)
        max_cross_track=0.
        for _ in range(201):
            sim.tick(plan.trajectory.duration/200)
            actual=self.m.pose_vec(sim.q)
            max_cross_track=max(max_cross_track,math.hypot(actual[1]-pose[1],actual[2]-pose[2]))
        # Measured approximation for THIS reachable short path, not a universal
        # straight-line bound or controller/hardware qualification.
        self.assertLess(max_cross_track,.5)
        self.assertLess(math.dist(self.m.pose_vec(sim.q)[:3],target[:3]),.05)
        self.assertTrue(plan.report['continuous_simulation'])
        self.assertFalse(plan.report['validated_hardware'])


class SimExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_movej_submits_one_coordinated_trajectory(self):
        w = preview_world()
        w.executor = type('Executor', (), {'check': lambda *a: None})()
        w.sim = SimRobot(w.model, [90]*6)
        w.sim.speed_pct = 100
        target = [30, -20, 40, 15, -25, 10]
        calls = []
        original = w.sim.move_j
        def move(q):
            calls.append(q[:])
            return original(q)
        w.sim.move_j = move
        async def tick():
            while not calls:
                await asyncio.sleep(.002)
            while w.sim.traj:
                await asyncio.sleep(.002)
                w.sim.tick(.05)
        ticker = asyncio.create_task(tick())
        await w._execute_move(target, 'offline', 0)
        await ticker
        self.assertEqual(calls, [target])
        self.assertEqual(w.sim.q,target)


if __name__ == '__main__':
    unittest.main(verbosity=2)
