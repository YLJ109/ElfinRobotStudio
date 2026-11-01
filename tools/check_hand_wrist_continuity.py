"""Real-IK palm wrist regressions; run only through run_offline_checks.py."""
from _offline_guard import require_offline_copy
require_offline_copy()

import math
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import config
from backend.core.hand_follow import HandFollow
from backend.core.kinematics import pose_rotation_distance
from backend.core.sim_tool import DEFAULT_TOOL, model_for


class WristContinuity(unittest.TestCase):
    def test_reused_nonzero_reference_plans_real_bounded_progress_without_teleport(self):
        # 正确：沿用中心只要求TCP误差合格，不能另假定J4/J6必须在±1度内。
        f = self.screenshot_make(180)
        center = [90.14805436035276, -3.4378741220549034, 79.96426839999351,
                  1.2795394521629748, 6.602259542706106, -1.2649487171618097]
        f._center_q = center
        f._base = f.model.pose_vec(center)
        before = list(f.q)
        f.target=list(f._base)
        f.solve_goal(10.1)
        self.assertEqual(f.q,before,'planning cannot directly change accepted joints')
        self.assertIsNotNone(f._goal,'this recorded reference has a real bounded path')
        if f._goal is not None:
            self.assertTrue(f._valid_q(f._goal));self.assertTrue(f._inside(f.model.pose_vec(f._goal)))
            self.assertLessEqual(max(abs(a-b) for a,b in zip(before,f._goal)),f.IK_JOINT_TRUST_DEG+1e-8)
            self.assertLess(math.dist(f.model.pose_vec(f._goal)[:3],f.target[:3]),math.dist(f.actual[:3],f.target[:3]))
        else:self.assertTrue(f.why)

    def test_repeated_target_preserves_actual_joints_and_local_trust(self):
        f=self.screenshot_make(180)
        f.q=[90.,-10.,80.,-135.,-10.,135.]
        f.actual=f.model.pose_vec(f.q)
        f._velocity[5]=20.
        before=list(f.q)
        for stamp in (10.1,10.2):
            f.solve_goal(stamp)
            self.assertEqual(f.q,before)
            self.assertIsNotNone(f._goal,'same reachable target must produce bounded position progress')
            if f._goal is not None:
                self.assertLessEqual(max(abs(a-b) for a,b in zip(before,f._goal)),f.IK_JOINT_TRUST_DEG+1e-8)
                self.assertTrue(f._valid_q(f._goal))
                self.assertLess(math.dist(f.model.pose_vec(f._goal)[:3],f.target[:3]),math.dist(f.actual[:3],f.target[:3]))
        self.assertIsNone(f._wrist_recovery_target)

    def test_corner_position_progress_never_claims_unproven_arrival_and_inward_recovers(self):
        f=self.screenshot_make(180)
        before=list(f.q)
        f.sample(dict(token=f.token,session=f.session,seq=1,u=0.,v=0.,hand_open=.5,
            palm_scale=.2,angle=0.,age_ms=0),now=10.1)
        from backend.core.hand_follow import solve_position_step
        from backend.core.ik_reachability import reachability_bound
        proof=reachability_bound(f.model,f.target,position_only=True)
        with patch('backend.core.hand_follow.solve_position_step',wraps=solve_position_step) as solver:
            f.solve_goal(10.1);f.solve_goal(10.2)
        self.assertTrue(solver.called)
        self.assertEqual(f.q,before)
        if f._goal is not None:
            pose=f.model.pose_vec(f._goal)
            self.assertTrue(f._valid_q(f._goal));self.assertTrue(f._inside(pose))
            if proof:self.assertGreater(math.dist(pose[:3],f.target[:3]),.2)
        else:self.assertTrue(f.why)
        self.drive(f,(20,600,650),120)
        self.assertLess(math.dist(f.actual[:3],f._tracking_target(f.target)[:3]),2.)

    def test_near_aligned_wrist_step_is_real_pose_preserving_null_progress(self):
        f=self.screenshot_make(180)
        f.q=[90.0005,-10.4087,79.5909,79.4589,-.0012,-79.4552]
        f.actual=f.model.pose_vec(f.q)
        f.target=list(f.actual)
        before=list(f.q);fixed=list(f.actual)
        f.solve_goal(10.1)
        self.assertEqual(f.q,before)
        self.assertIsNotNone(f._goal)
        pose=f.model.pose_vec(f._goal)
        self.assertLess(math.dist(pose[:3],fixed[:3]),.001)
        self.assertLess(pose_rotation_distance(pose,fixed),.001)
        self.assertLess(abs(f._goal[5]-f._center_q[5]),abs(before[5]-f._center_q[5]))

    def test_exact_aligned_wrist_retains_precision_and_can_leave_in_both_directions(self):
        f=self.screenshot_make(180)
        f.q=[90.0562,-3.0143,86.9183,-.0891,0.,.0927]
        f.actual=f.model.pose_vec(f.q)
        for point in ((30,600,600),(-30,600,600)):
            self.drive(f,point,150)
            remaining=math.dist(f.actual[:3],f._tracking_target(f.target)[:3])
            print('EXACT_ALIGNED',point,'remaining',remaining,'detail',f._solver_detail,flush=True)
            self.assertLess(remaining,2.,(f.why,f._solver_detail))

    def test_reported_integrated_stall_under_coarse_solve_budget_pressure(self):
        f=self.screenshot_make(180)
        # Actual failed integration state (rounded readout), not a new/easier
        # target. Coarse failures are charged CPU time deterministically so a
        # fast developer machine cannot conceal repeated budget starvation.
        f.q=[90.0562,-3.0143,86.9183,-.0891,.0674,.0927]
        f.actual=f.model.pose_vec(f.q)
        from backend.core.position_servo import solve_position_step
        from backend.core.kinematics import RobotModel
        original_fk=RobotModel._flange_matrix
        clock=[100.];coarse=[]
        def charged(model,seed,target,*args,**options):
            def delayed(instance,q):
                value=original_fk(instance,q)
                if abs(f.q[4])<.25 and math.dist(f.actual[:3],target)>.5:
                    coarse.append(tuple(q));clock[0]+=.18
                return value
            with patch.object(RobotModel,'_flange_matrix',delayed):
                return solve_position_step(model,seed,target,*args,**options)
        with patch('backend.core.hand_follow.time.monotonic',side_effect=lambda:clock[0]), \
             patch('backend.core.hand_follow.solve_position_step',side_effect=charged) as solver:
            for point in ((30,600,600),(-30,600,600),(0,600,650)):
                self.drive(f,point,150)
                remaining=math.dist(f.actual[:3],f._tracking_target(f.target)[:3])
                self.assertLess(remaining,2.,(f.why,f._solver_detail))
        self.assertTrue(solver.called);self.assertTrue(coarse,'deadline pressure did not reach the real position FK boundary')
        self.assertLess(self.max_tick_s,.5)

    def screenshot_make(self, direction):
        f = HandFollow(model_for('elfin5', DEFAULT_TOOL))
        lo, hi = map(list, zip(*config.MANUAL_LIMITS))
        result = f.configure(dict(session='left-facing', aspect=2., depth=True,
            rotation=False, direction=direction, depth_mode='gesture', enclosure={
                'min': [-350.,525.,475.], 'max': [350.,675.,825.]}),
            lower=lo, upper=hi, now=10.)
        self.assertTrue(result['ok'],result)
        self.assertTrue(f.start(dict(token=f.token,session=f.session),
            current_q=f._center_q,now=10.)['ok'])
        return f

    def test_left_facing_screenshot_vertical_crossing_and_immediate_reverse(self):
        f=self.screenshot_make(180)
        for point in ((0,600,536.7),(30,600,536.7),(-30,600,536.7),
                      (0,600,500),(0,600,600),(30,600,600),
                      (-30,600,600),(0,600,650)):
            self.drive(f,point,150)
            print('LEFT_SCREENSHOT',point,[round(v,4) for v in f.q],
                  [round(v,4) for v in f.actual],f.why,flush=True)
            self.assertLess(math.dist(f.actual[:3],f._tracking_target(f.target)[:3]),2.,f.why)
            self.assertLessEqual(abs(f.q[5]-f._center_q[5]),180.+1e-8)
        self.assertLess(self.max_tick_s,.5,'有界局部求解也必须在World提交TTL内完成')
        print('180_POSITION_LOCAL_SOLVE max tick seconds:',self.max_tick_s,flush=True)

    def test_requested_270_y600_box_reaches_local_targets_and_reverses_within_ttl(self):
        f=self.screenshot_make(270)
        for point in ((0,600,630),(15,600,630),(-15,600,630),(0,600,650)):
            self.drive(f,point,120)
            self.assertLess(math.dist(f.actual[:3],f._tracking_target(f.target)[:3]),2.,f.why)
            self.assertLess(abs(f.q[5]-f._center_q[5]),30.,'局部反向不应旋腕绕圈')
        self.assertLess(self.max_tick_s,.5,'求解不能超过World结果提交TTL')
        print('270_Y600_SOLVE max tick seconds:',self.max_tick_s,flush=True)

    def make(self, center=(0, -500, 750), wrist_limit=360):
        f = HandFollow(model_for('elfin5', DEFAULT_TOOL))
        lo, hi = map(list, zip(*config.MANUAL_LIMITS))
        lo[5], hi[5] = -wrist_limit, wrist_limit
        result = f.configure(dict(session='wrist', aspect=4/3, depth=True,
            rotation=False, depth_mode='gesture', enclosure={
                'min': [v-h for v,h in zip(center, (400,150,250))],
                'max': [v+h for v,h in zip(center, (400,150,250))]}),
            lower=lo, upper=hi, now=10.)
        self.assertTrue(result['ok'], result.get('why'))
        self.assertTrue(f.start(dict(token=f.token, session=f.session),
            current_q=f._center_q, now=10.)['ok'])
        self.assertEqual((f._lower,f._upper),(lo,hi))
        self.assertEqual(f._center_q[5],0.)
        return f

    def drive(self, f, point, frames=60, speed=100, hz=30):
        x,y,z = point
        still = 0
        for _ in range(frames):
            seq = f.seq+1
            now = f._clock_value+1/hz
            self.assertTrue(f.sample(dict(token=f.token, session=f.session,
                seq=seq, u=(x-f._lo[0])/(f._hi[0]-f._lo[0]),
                v=(f._hi[2]-z)/(f._hi[2]-f._lo[2]),
                hand_open=(f._hi[1]-y)/(f._hi[1]-f._lo[1]),
                palm_scale=.2, angle=0, age_ms=0), now=now)['ok'])
            previous = list(f.q)
            before = list(f.actual)
            began=time.perf_counter()
            f.tick(1/hz, now=now, speed_pct=speed)
            self.max_tick_s=max(getattr(self,'max_tick_s',0.),time.perf_counter()-began)
            self.assertTrue(f._valid_q(f.q), f.q)
            self.assertTrue(f._inside(f.actual), f.actual)
            self.assertTrue(f._body_inside(f.q, f.base_offset), f.q)
            self.assertEqual(f.base_offset, [0,0,0])
            self.assertLess(math.dist(f.actual[:3],f.model.pose_vec(f.q)[:3]),1e-6)
            self.assertLess(pose_rotation_distance(f.actual,f.model.pose_vec(f.q)),1e-6)
            self.assertTrue(all(math.isfinite(v) for v in f.actual))
            self.assertLessEqual(math.dist(before[:3], f.actual[:3]), 300/hz*speed/100+1e-6)
            for a,b,cap in zip(previous, f.q, f.model.vmax):
                self.assertLessEqual(abs(b-a), cap/hz*speed/100+1e-6)
            self.max_wrist = max(getattr(self,'max_wrist',0), abs(f.q[5]))
            still = still+1 if max(abs(a-b) for a,b in zip(previous,f.q))<1e-9 else 0
            if still >= 4 and f.why and f._subdivision_resume is None:
                print('STILL_EXIT',f._solver_cause,f._solver_detail,
                      'budget',f._budget_spent,'resume',f._subdivision_resume,
                      'q',f.q,flush=True)
                break
        return list(f.q)

    def test_pixel_loops_crossing_center_and_front_face_do_not_wind(self):
        f = self.make()
        maximum = 0
        for cycle in range(3):
            for point in ((-20,-650,680),(20,-650,680),(20,-650,720),
                          (-20,-650,720),(0,-500,750)):
                self.drive(f, point, 70)
                maximum = max(maximum, abs(f.q[5]))
                self.assertLess(math.dist(f.actual[:3], f._tracking_target(f.target)[:3]), 2.)
        self.assertLess(maximum, 15., 'Translation wound J6 away from its centered branch')
        self.assertLess(abs(f.q[5]),.1)
        print('CENTER_CROSSING_LOOPS maximum J6:',maximum,flush=True)

    def test_loop_around_wrist_singularity(self):
        f = self.make()
        # Before the fix this exact real-IK pixel loop reached J6=-359.871deg
        # and stalled at X=-0.178 rather than reaching the last X=-20 target.
        for point in ((-40,-650,680),(-40,-650,550),(40,-650,550),
                      (40,-650,680),(-20,-650,680)):
            self.drive(f,point,90)
            print('SINGULAR',point,[round(v,3) for v in f.q],
                  [round(v,3) for v in f.actual],f.why,flush=True)
            if math.dist(f.actual[:3],f._tracking_target(f.target)[:3])>2:
                self.assertTrue(f.why, 'Unreachable continuity must be reported')
        self.assertLessEqual(self.max_wrist,180.+1e-8,'Translation made a gratuitous wrist turn')
        # Backtracking is a physical route out of the branch obstruction.
        for point in ((-40,-650,550),(-40,-650,680),(0,-500,750)):
            self.drive(f,point,100)
            self.assertLess(math.dist(f.actual[:3],f._tracking_target(f.target)[:3]),2.,f.why)
        self.assertLess(abs(f.q[5]),.1)

    def test_direct_singularity_crossing_uses_signed_bend_without_wrist_flip(self):
        f = self.make()
        for cycle in range(2):
            for point in ((0,-650,550),(-20,-650,550),(20,-650,550),
                          (0,-650,550),(0,-650,680),(0,-500,750)):
                self.drive(f,point,100)
                self.assertLess(math.dist(f.actual[:3],f._tracking_target(f.target)[:3]),2.,f.why)
                self.assertLess(abs(f.q[5]),30.)
        self.assertLess(abs(f.q[5]),.1)

    def test_narrow_limits_remain_exact_and_fully_blocked_target_holds_then_recovers(self):
        f = self.make(wrist_limit=5.)
        limits=(list(f._lower),list(f._upper))
        self.drive(f,(-100,-650,680),100)
        self.assertLess(math.dist(f.actual[:3],f._base[:3]),math.dist(f.target[:3],f._base[:3]))
        self.assertLess(math.dist(f.actual[:3],f.target[:3]),2.,'position-priority may reach the former fixed-pose obstruction')
        self.assertEqual((f._lower,f._upper),limits)
        self.assertLessEqual(abs(f.q[5]),5.)
        self.assertFalse(f._body_inside(f.q,[0,0,-.001]))
        before=list(f.q);f._lower=list(before);f._upper=list(before)
        self.drive(f,(100,-650,680),100)
        self.assertEqual(f.q,before);self.assertGreater(math.dist(f.actual[:3],f.target[:3]),10.)
        self.assertTrue(f.why);self.assertIsNone(f._goal)
        f._lower,f._upper=map(list,limits)
        self.drive(f,(0,-500,750),100,speed=30)
        self.assertLess(math.dist(f.actual[:3],f._base[:3]),2.)

    def test_far_corner_incomplete_progress_does_not_claim_arrival_or_exceed_caps(self):
        f = self.make()
        self.drive(f,(400,-650,1000),120,speed=30)
        self.assertGreater(math.dist(f.actual[:3],f.target[:3]),5.)
        self.assertGreater(f._solver_detail['position_error_mm'],5.)
        self.assertNotEqual(f._solver_cause,'reached')
        if f._goal is None:self.assertTrue(f.why)
        self.assertEqual(f.target[:3],[400,-650,1000])

    def test_genuinely_position_unreachable_corner_has_geometric_proof_and_recovers(self):
        from backend.core.ik_reachability import reachability_bound
        f=HandFollow(model_for('elfin5',DEFAULT_TOOL))
        lo,hi=map(list,zip(*config.MANUAL_LIMITS))
        result=f.configure(dict(session='position-proof',aspect=4/3,depth=True,rotation=False,depth_mode='gesture',
            enclosure={'min':[-1000.,-650.,50.],'max':[1000.,-350.,1450.]}),lower=lo,upper=hi,now=10.)
        self.assertTrue(result['ok'],result)
        self.assertTrue(f.start(dict(token=f.token,session=f.session),current_q=f._center_q,now=10.)['ok'])
        proof=reachability_bound(f.model,[1000.,-650.,1450.],position_only=True)
        self.assertIsNotNone(proof);self.assertEqual(proof['reason_code'],'unreachable_position')
        self.drive(f,(1000.,-650.,1450.),120,speed=30)
        self.assertGreater(math.dist(f.actual[:3],f.target[:3]),5.)
        self.assertNotEqual(f._solver_cause,'reached')
        self.drive(f,(0.,-500.,750.),150)
        self.assertLess(math.dist(f.actual[:3],f._tracking_target(f.target)[:3]),2.)

    def test_ownership_of_existing_wound_posture_does_not_wrap_angles(self):
        f = self.make()
        q = list(f._center_q)
        q[5] = 359.
        self.assertTrue(f._valid_q(q))
        self.assertTrue(f.stop()['ok'])
        self.assertTrue(f.start(dict(token=f.token,session=f.session),current_q=q,
                                now=11.)['ok'])
        self.assertEqual(f.q,q)
        before=list(f.q)
        f.sample(dict(token=f.token,session=f.session,seq=0,u=.5,v=.5,
            hand_open=.5,palm_scale=.2,angle=0,age_ms=0),now=11.1)
        f.tick(1/30,now=11.1)
        self.assertLess(abs(f.q[5]-before[5]),f.model.vmax[5]/30+1e-6)
        self.assertGreater(f.q[5],350.)

    def test_caps_at_20_and_50hz_and_zero_speed(self):
        for hz in (20,50):
            with self.subTest(hz=hz):
                f=self.make()
                for point in ((50,-520,730),(0,-500,750)):
                    self.drive(f,point,100,speed=30,hz=hz)
                    self.assertLess(math.dist(f.actual[:3],f.target[:3]),2.,f.why)
                before=list(f.q)
                self.drive(f,(50,-520,730),10,speed=0,hz=hz)
                self.assertEqual(f.q,before)

    def test_aligned_wrist_recenters_through_real_null_motion_at_fixed_tcp(self):
        f=self.make()
        # Fixture a physically possible aligned wrist, retaining the configured
        # zero J6 reference. All subsequent updates go through real IK/tick.
        q=list(f._center_q)
        q[3],q[4],q[5]=40.,0.,-40.
        f.q=list(q)
        f.actual=f.model.pose_vec(q)
        f._base=list(f.actual)
        f.target=list(f.actual)
        self.assertTrue(f._inside(f.actual))
        fixed=list(f.actual)
        self.drive(f,fixed[:3],150)
        self.assertLess(abs(f.q[5]),1.)
        self.assertLess(math.dist(f.actual[:3],fixed[:3]),.2)
        self.assertLess(pose_rotation_distance(f.actual,fixed),.1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
