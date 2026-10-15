"""Offline regressions for Cartesian slider IK, using only official model geometry."""
import math
import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core.kinematics import RobotModel
from sim.backend.core.robot_sim import SimRobot
from sim.backend.core.world import World


def preview_world():
    # Exercise the real World method without constructing storage or any link.
    w = World.__new__(World)
    w.model = RobotModel('elfin5')
    w.joints_known = lambda: True
    w.current_joints = lambda: [0]*6
    w.limits = lambda: (w.model.lower, w.model.upper)
    w.precheck = lambda *a, **kw: None
    w.log = type('Log', (), {'add': lambda *a, **kw: None})()
    return w


class CartesianTests(unittest.TestCase):
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


class SimExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_movej_submits_one_coordinated_trajectory(self):
        w = preview_world()
        w.settings = {'mode': 'sim', 'real_move_cmd': 'relj'}
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
            for _ in range(50):
                await asyncio.sleep(.002)
                w.sim.tick(.05)
        ticker = asyncio.create_task(tick())
        await w._execute_move(target, 'offline', 0)
        await ticker
        self.assertEqual(calls, [target])
        self.assertEqual(w.sim.q,target)


if __name__ == '__main__':
    unittest.main(verbosity=2)
