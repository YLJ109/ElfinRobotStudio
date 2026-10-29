"""Whole staged HandFollow contract; use run_offline_checks.py --staged-follow.

No World, camera, network, hardware or service imports. FOLLOW_TARGET records
are evidence of measured FK convergence, never a claim of global reachability.
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.hand_follow import HandFollow
from backend.core.kinematics import pose_rotation_distance
from backend.core.sim_tool import DEFAULT_TOOL, model_for, normalize
from backend.core.position_servo import solve_position_step
from backend.core.config import MANUAL_LIMITS


BOX = {'min': [-400., -550., 450.], 'max': [400., -250., 950.]}
CENTER = [0., -400., 700.]
TOOL = copy.deepcopy(DEFAULT_TOOL)
TOOL['dimensions'].update(mountHeight=40., cupHeight=30.)
TOOL = normalize(TOOL)


def world_point(direction, local):
    """Independent box rotation about the requested center, not robot base."""
    angle = math.radians(direction)
    c, s = math.cos(angle), math.sin(angle)
    x, y = local[0]-CENTER[0], local[1]-CENTER[1]
    return [CENTER[0]+c*x-s*y, CENTER[1]+s*x+c*y, local[2]]


def local_point(direction, world):
    return world_point(-direction, world)


class Rewrite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = []
        root = Path(__file__).resolve().parents[1]
        for staged, installed in (('staged_hand_follow.py','hand_follow.py'),
                                  ('staged_position_servo.py','position_servo.py')):
            original = (root/'tools'/staged).read_bytes()
            overlay = (root/'backend/core'/installed).read_bytes()
            if original != overlay:
                raise AssertionError('Use --staged-follow: overlay does not match '+staged)
            print('FOLLOW_SOURCE '+staged+' '+hashlib.sha256(original).hexdigest(),flush=True)

    @classmethod
    def tearDownClass(cls):
        stalled = [r for r in cls.records if r['status'] != 'CONVERGED']
        print('FOLLOW_SUMMARY '+json.dumps(dict(targets=len(cls.records),
              converged=len(cls.records)-len(stalled),stalled_not_passed=stalled)),flush=True)
        if 'backend.core.world' in sys.modules:
            raise AssertionError('Pure HandFollow check imported World')

    def make(self, direction=0):
        model = model_for('elfin5',copy.deepcopy(TOOL))
        f = HandFollow(model)
        lo,hi=map(list,zip(*MANUAL_LIMITS))
        result = f.configure(dict(session='staged-rewrite',aspect=4/3,depth=True,
            rotation=False,direction=direction,depth_mode='gesture',enclosure=copy.deepcopy(BOX)),
            lower=lo,upper=hi,now=10.)
        if not result['ok']:
            row = dict(direction=direction,local=CENTER,world=CENTER,
                label='configure-default-box',status='CONFIGURATION_FAILED',
                why=result.get('why'),joint1_limits=[model.lower[0],model.upper[0]])
            self.records.append(row)
            print('FOLLOW_TARGET '+json.dumps(row,ensure_ascii=False),flush=True)
        self.assertTrue(result['ok'],(direction,result.get('why')))
        self.assertEqual(f._lo,BOX['min'])
        self.assertEqual(f._hi,BOX['max'])
        self.assertEqual(f.base_offset,[0.,0.,0.])
        self.oracle = model_for('elfin5',copy.deepcopy(TOOL))
        self.assertLess(math.dist(self.fk(f._center_q),CENTER),.2)
        self.assertTrue(f.start(dict(token=f.token,session=f.session),
                               current_q=list(f._center_q),now=10.)['ok'])
        self.assertFalse(f.rotation)
        self.moving_ticks = 0
        return f

    def fk(self, q):
        # Independent model/cache and raw unrounded FK, never f.actual.
        matrix = self.oracle._flange_matrix(q)
        return [matrix[i][3]*1000. for i in range(3)]

    def check_q(self, f, q):
        self.assertEqual(len(q),6)
        for v,lo,hi in zip(q,f._lower,f._upper):
            self.assertTrue(math.isfinite(v))
            self.assertLessEqual(lo-1e-8,v)
            self.assertLessEqual(v,hi+1e-8)
        p = self.fk(q)
        local = local_point(f.direction,p)
        for v,lo,hi in zip(local,BOX['min'],BOX['max']):
            self.assertLessEqual(lo-1e-7,v)
            self.assertLessEqual(v,hi+1e-7)
        return p

    def path_guard(self, f):
        def validate(before, candidate):
            # Independently check swept joint interpolation, including endpoint.
            self.assertEqual(before,f.q)
            for t in (.25,.5,.75,1.):
                self.check_q(f,[a+(b-a)*t for a,b in zip(before,candidate)])
            for a,b,cap in zip(before,candidate,f.model.vmax):
                self.assertLessEqual(abs(b-a),cap*.01+1e-8)
            self.assertLessEqual(math.dist(self.fk(before),self.fk(candidate)),3.+1e-8)
            return True
        return validate

    def tick(self, f, dt=1/30, speed=100, guard=None):
        before, p = list(f.q), self.fk(f.q)
        result = f.tick(dt,now=f._clock_value+dt,speed_pct=speed,
                        validate_step=self.path_guard(f) if guard is None else guard)
        after = self.check_q(f,f.q)
        self.assertLess(math.dist(after,f.actual[:3]),1e-7)
        self.assertEqual(f.base_offset,[0.,0.,0.])
        for a,b,cap in zip(before,f.q,f.model.vmax):
            self.assertLessEqual(abs(b-a),cap*dt*speed/100+1e-8)
        self.assertLessEqual(math.dist(p,after),300.*dt*speed/100+1e-8)
        if math.dist(p,after)>1e-5:
            self.moving_ticks += 1
        return result

    def sample(self, f, local, *, present=True, gap=False, jitter=0., token=None):
        x,y,z = local
        payload = dict(token=f.token if token is None else token,session=f.session,seq=f.seq+1,
            u=(x+400.)/800.+jitter,v=(950.-z)/500.-jitter,
            hand_open=(-250.-y)/300.+jitter,palm_scale=.2,angle=0.,age_ms=0,
            hand_present=present)
        result = f.sample(payload,now=f._clock_value,camera_gap=gap)
        self.assertTrue(result['ok'],result.get('why'))
        if present:
            expected = world_point(f.direction,[x+800*jitter,y-300*jitter,z+500*jitter])
            self.assertLess(math.dist(f.target[:3],expected),1e-7)
        return payload

    def drive(self, f, local, *, frames=180, required=True, label='', speed=100):
        target = world_point(f.direction,local)
        start = self.fk(f.q)
        count = self.moving_ticks
        settle = .75 if any(abs(local[i]-face)<1e-8
                           for i in range(3) for face in (BOX['min'][i],BOX['max'][i])) else .2
        for frame in range(frames):
            self.sample(f,local)
            self.tick(f,speed=speed)
            if frame>=5 and math.dist(self.fk(f.q),target)<=settle:
                break
        error = math.dist(self.fk(f.q),target)
        row = dict(direction=f.direction,local=local,world=target,label=label,
            status='CONVERGED' if error<=2. else 'STALLED_NOT_PASSED',
            error_mm=round(error,6),net_motion_mm=round(math.dist(start,self.fk(f.q)),6),
            moving_ticks=self.moving_ticks-count,cause=f._solver_cause,
            detail=f._solver_detail,why=f.why)
        self.records.append(row)
        print('FOLLOW_TARGET '+json.dumps(row,ensure_ascii=False),flush=True)
        if required:
            self.assertLessEqual(error,2.,row)
            requested = math.dist(start,target)
            if requested>20.:
                self.assertGreater(math.dist(start,self.fk(f.q)),requested-2.,row)
                self.assertGreater(row['moving_ticks'],3,row)
        elif error>2.:
            self.assertNotEqual(f._solver_cause,'reached',row)
            self.assertTrue(f.why or f._solver_detail,row)
        return row

    def test_four_directions_full_mapping_and_motion(self):
        for direction in (0,90,180,270):
            with self.subTest(direction=direction):
                f = self.make(direction)
                # Repeat depth signs around center so an apparent forward
                # movement cannot be explained by a previous lateral target.
                for point in ([0,-500,700],[0,-300,700],CENTER,
                              [-100,-400,700],[100,-400,700],
                              [0,-400,600],[0,-400,800],CENTER):
                    self.drive(f,point,label='six-direction-and-reversal')
                self.assertFalse(f._solver_detail.get('orientation_locked',True))

    @staticmethod
    def posture(model, q):
        tf = model.fk(q)
        point = lambda name: [tf[name][i][3]*1000. for i in range(3)]
        shoulder, elbow, root, wrist = map(point,
            ('elfin_link2','elfin_link3','elfin_link4','elfin_link5'))
        forward = [model.pose_vec(q)[i]-shoulder[i] for i in range(2)]
        length = math.hypot(*forward)
        arm = [b-a for a,b in zip(shoulder,elbow)]
        forearm = [b-a for a,b in zip(root,wrist)]
        return dict(upper_up=arm[2]/math.dist(shoulder,elbow),
            forearm_forward=sum(a*b for a,b in zip(forearm[:2],forward))/length/math.dist(root,wrist))

    def test_natural_initial_center_and_nine_points_four_directions_40mm_tool(self):
        points = [CENTER,[-100.,-400.,700.],[100.,-400.,700.],
            [0.,-400.,600.],[0.,-400.,800.],[0.,-500.,700.],[0.,-300.,700.],
            [-80.,-450.,760.],[80.,-350.,640.]]
        for direction in (0,90,180,270):
            with self.subTest(direction=direction):
                f = self.make(direction)
                metric = self.posture(self.oracle, f._center_q)
                print('NATURAL_CENTER '+json.dumps(dict(direction=direction,q=f._center_q,**metric)),flush=True)
                self.assertGreater(metric['upper_up'],.85)
                self.assertGreater(metric['forearm_forward'],.75)
                initial = list(f.q)
                j2 = []
                for point in points:
                    self.drive(f,point,label='natural-40mm-nine-points')
                    self.assertGreater(f.q[2],0., 'DLS crossed the elbow branch')
                    self.assertEqual(f.base_offset,[0.,0.,0.])
                    j2.append(f.q[1])
                self.assertGreater(max(j2)-min(j2),1., 'J2 must participate freely')
                # Configure/start only select an endpoint; a supplied actual
                # posture remains unchanged until World performs its plan.
                f.stop()
                supplied = list(initial); supplied[0] += 1.
                self.assertTrue(f.start(dict(token=f.token,session=f.session),
                    now=f._clock_value,current_q=supplied)['ok'])
                self.assertEqual(f.q,supplied)

    def test_position_center_preserves_current_branch_and_nonzero_wrist(self):
        model = model_for('elfin5',copy.deepcopy(TOOL))
        for q in ([-90.,24.6,95.9,0.,18.7,65.],
                  [-90.,24.6,95.9,0.,18.7,359.]):
            with self.subTest(q=q):
                center = model.pose_vec(q)[:3]
                box = dict(min=[v-30. for v in center],max=[v+30. for v in center])
                f = HandFollow(model)
                with patch.object(model,'ik',side_effect=AssertionError('No ABC in position mode')):
                    result = f.configure(dict(session='preserve-branch',aspect=1.,depth=False,
                        rotation=False,enclosure=box),current_q=q,now=10.,
                        lower=[-360.]*6,upper=[360.]*6)
                self.assertTrue(result['ok'],result.get('why'))
                self.assertEqual(f._center_q,q)
                self.assertEqual(f.reference_q,q)
                self.assertEqual(f.base_offset,[0.,0.,0.])
                # A nearby custom center must follow the same continuous branch.
                box['min'][2] += 5.; box['max'][2] += 5.
                result = f.configure(dict(session='nearby-branch',aspect=1.,depth=False,
                    rotation=False,enclosure=box),current_q=q,now=11.,
                    lower=[-360.]*6,upper=[360.]*6)
                self.assertTrue(result['ok'],result.get('why'))
                self.assertLess(max(abs(a-b) for a,b in zip(q,f._center_q)),8.)
                self.assertGreater(q[2]*f._center_q[2],0.)
                self.assertLess(math.dist(model.pose_vec(f._center_q)[:3],
                    [center[0],center[1],center[2]+5.]),.01)

    def test_valid_folded_current_cannot_override_natural_seed_but_remains_fallback(self):
        model = model_for('elfin5',copy.deepcopy(TOOL))
        current = [-14.65,-22.45,-110.,-181.9,-11.85,12.45]
        lo, hi = [-360.]*6, [360.]*6
        payload = dict(session='folded-current',aspect=4/3,depth=False,
            rotation=False,enclosure=copy.deepcopy(BOX))
        f = HandFollow(model)
        result = f.configure(payload,current_q=current,lower=lo,upper=hi,now=10.)
        self.assertTrue(result['ok'],result.get('why'))
        self.assertTrue(f._valid_q(current))
        self.assertNotEqual(f.reference_q,current)
        self.assertGreater(self.posture(model,f._center_q)['upper_up'],.6)
        self.assertGreater(self.posture(model,f._center_q)['forearm_forward'],0.)
        self.assertGreater(f._center_q[2],0.)
        # Natural preference must not turn into a hard posture rejection.
        fallback = [-90.,-77.7,-95.9,0.,108.2,25.]
        center = model.pose_vec(fallback)[:3]
        payload['enclosure'] = dict(min=[v-30. for v in center],max=[v+30. for v in center])
        original = f._position_center
        attempts = []
        def unavailable_natural(reference,*args):
            attempts.append(list(reference))
            if list(reference) == fallback:
                return original(reference,*args)
            return list(reference),999.,0.,False
        with patch.object(f,'_position_center',side_effect=unavailable_natural):
            result = f.configure(payload,current_q=fallback,lower=lo,upper=hi,now=11.)
        self.assertTrue(result['ok'],result.get('why'))
        self.assertNotEqual(attempts[0],fallback)
        self.assertEqual(f._center_q,fallback)

    def test_natural_current_preference_rotates_with_target_center(self):
        for j1 in (-90.,0.,90.,180.):
            model = model_for('elfin5',copy.deepcopy(TOOL))
            q = [j1,24.6,95.9,0.,18.7,65.]
            center = model.pose_vec(q)[:3]
            box = dict(min=[v-30. for v in center],max=[v+30. for v in center])
            f = HandFollow(model)
            result = f.configure(dict(session='radial-preference',aspect=1.,depth=False,
                rotation=False,enclosure=box),current_q=q,lower=[-360.]*6,upper=[360.]*6,now=10.)
            self.assertTrue(result['ok'],result.get('why'))
            self.assertEqual(f.reference_q,q)
            self.assertEqual(f._center_q,q)

    def test_position_only_succeeds_when_orientation_unavailable_but_rotation_still_requires_pose(self):
        for rotation in (False, True):
            model = model_for('elfin5',copy.deepcopy(TOOL))
            f = HandFollow(model)
            with patch.object(model,'ik',return_value=([0.]*6,999.,999.,False)) as pose_ik:
                result = f.configure(dict(session='pose-unavailable',aspect=4/3,depth=False,
                    rotation=rotation,enclosure=copy.deepcopy(BOX)),now=10.)
            self.assertEqual(result['ok'],not rotation,result.get('why'))
            self.assertEqual(pose_ik.called,rotation)

    def test_real_fk_reachable_position_with_unreachable_abc_under_existing_limits(self):
        model = model_for('elfin5',copy.deepcopy(TOOL))
        q = [-90.,24.6,95.9,15.,18.7,65.]
        pose = model.pose_vec(q)
        self.assertGreater(pose_rotation_distance(pose,[*pose[:3],90.,0.,0.]),10.)
        box = dict(min=[v-30. for v in pose[:3]],max=[v+30. for v in pose[:3]])
        lo, hi = [v-.05 for v in q], [v+.05 for v in q]
        for rotation in (False,True):
            f = HandFollow(model)
            result = f.configure(dict(session='real-fk-abc-limit',aspect=1.,depth=False,
                rotation=rotation,enclosure=box),current_q=q,lower=lo,upper=hi,now=10.)
            self.assertEqual(result['ok'],not rotation,result.get('why'))
            if not rotation:
                self.assertEqual(f._center_q,q)
                self.assertLess(math.dist(model.pose_vec(f._center_q)[:3],pose[:3]),1e-8)

    def test_invalid_current_and_unreachable_custom_box_fail_closed_without_base_changes(self):
        model = model_for('elfin5',copy.deepcopy(TOOL))
        f = HandFollow(model)
        result = f.configure(dict(session='fallback-invalid',aspect=4/3,depth=False,
            rotation=False,enclosure=copy.deepcopy(BOX)),current_q=[999.]*6,now=10.)
        self.assertTrue(result['ok'],result.get('why'))
        self.assertGreater(self.posture(model,f._center_q)['upper_up'],.85)
        unreachable = dict(min=[10000.,10000.,10000.],max=[10100.,10100.,10100.])
        result = f.configure(dict(session='unreachable-position',aspect=1.,depth=False,
            rotation=False,enclosure=unreachable),now=11.)
        self.assertFalse(result['ok'])
        self.assertFalse(f.configured)
        self.assertEqual(f.base_offset,[0.,0.,0.])

    def test_nine_hz_jitter_and_short_camera_gaps_then_settle(self):
        f = self.make()
        target = [100.,-500.,760.]
        phases = []
        for frame in range(450):
            if frame%10==0:  # 90Hz motion, actual 9Hz sample cadence.
                sample_number = frame//10
                gap = sample_number%9 in (3,4)
                self.sample(f,target,present=not gap,gap=gap,
                            jitter=.0002*math.sin(sample_number))
                phases.append(f.input_phase)
                self.assertFalse(f.returning,'short camera gap unexpectedly triggered recenter')
            self.tick(f,dt=1/90)
        self.assertIn('holding',phases)
        self.assertGreater(self.moving_ticks,60)
        self.drive(f,target,label='9Hz-jitter-final-settle')
        # A fresh opposite input must travel the full distance, not a few mm.
        self.drive(f,[-100.,-300.,640.],label='9Hz-large-reversal')

    def test_full_box_mapping_endpoints_and_missing_gesture_depth(self):
        for direction in (0,90,180,270):
            with self.subTest(direction=direction):
                f = self.make(direction)
                for point in ([-400.,-550.,450.],[400.,-250.,950.],CENTER):
                    self.sample(f,point)
                self.sample(f,[0.,-500.,700.])
                # In gesture mode, missing finger geometry retains its last
                # depth even when apparent palm size changes substantially.
                payload = dict(token=f.token,session=f.session,seq=f.seq+1,
                    u=.5,v=.5,palm_scale=.9,angle=0.,age_ms=0)
                self.assertTrue(f.sample(payload,now=f._clock_value)['ok'])
                self.assertLess(math.dist(f.target[:3],world_point(direction,[0.,-500.,700.])),1e-7)

    def test_speed_scaling_and_zero_speed_do_not_teleport(self):
        f = self.make()
        self.sample(f,[100.,-500.,760.])
        before = list(f.q)
        for _ in range(8):
            self.sample(f,[100.,-500.,760.])
            self.tick(f,speed=0)
        self.assertEqual(f.q,before)
        self.drive(f,[100.,-500.,760.],speed=30,label='30-percent-speed')
        self.drive(f,CENTER,speed=50,label='50-percent-speed')

    def test_front_back_box_faces_retain_tangent_motion_under_jitter(self):
        for direction in (0,90,180,270):
            with self.subTest(direction=direction):
                f = self.make(direction)
                for depth in (-550.,-250.):
                    self.drive(f,[0.,depth,700.],label='depth-face-arrival')
                    self.drive(f,[100.,depth,760.],label='depth-face-tangent-positive')
                    self.drive(f,[-100.,depth,640.],label='depth-face-tangent-reverse')
                    # Jitter remains physically inside the selected face. The
                    # expected motion is ~200mm tangential, never a token nudge.
                    start = self.fk(f.q)
                    moving = self.moving_ticks
                    for frame in range(180):
                        if frame%10==0:
                            jitter = .0002*(.5+.5*math.sin(frame))
                            if depth == -550.:
                                jitter = -jitter
                            self.sample(f,[100.,depth,720.],jitter=jitter)
                        self.tick(f,dt=1/90)
                    self.assertGreater(math.dist(start,self.fk(f.q)),180.)
                    self.assertGreater(self.moving_ticks-moving,30)
                    self.drive(f,[100.,depth,720.],label='depth-face-jitter-settle')
                self.drive(f,CENTER,label='return-from-depth-faces')

    def test_clear_reversal_discards_old_goal_and_brakes(self):
        f = self.make()
        for _ in range(12):
            self.sample(f,[180.,-400.,700.])
            self.tick(f)
        p = self.fk(f.q)
        self.assertGreater(p[0],10.)
        self.sample(f,[-100.,-400.,700.])
        self.assertIsNone(f._goal,'opposite sample retained old IK waypoint')
        points = []
        for _ in range(9):
            self.sample(f,[-100.,-400.,700.])
            self.tick(f)
            points.append(self.fk(f.q))
        self.assertLess(points[-1][0],p[0]-5.,points)
        self.drive(f,[-100.,-400.,700.],label='immediate-reversal')

    def test_position_follow_does_not_lock_orientation(self):
        f = self.make()
        initial = self.oracle.pose_vec(f.q)
        self.drive(f,[100.,-500.,760.],label='free-orientation')
        final = self.oracle.pose_vec(f.q)
        self.assertGreater(pose_rotation_distance(initial,final),1.)
        self.assertFalse(f._solver_detail['orientation_locked'])
        self.assertGreaterEqual(sum(abs(a-b)>.05 for a,b in zip(f._center_q,f.q)),3)

    def test_validation_requires_literal_true_and_exception_holds(self):
        for answer in (False,None,1,'safe',{'ok':True},RuntimeError('denied')):
            with self.subTest(answer=repr(answer)):
                f = self.make()
                self.sample(f,[100.,-450.,740.])
                before,actual = list(f.q),list(f.actual)
                calls = []
                def deny(a,b):
                    calls.append((a,b))
                    if isinstance(answer,Exception):
                        raise answer
                    return answer
                self.tick(f,guard=deny)
                self.assertTrue(calls,'rejection contract was not exercised')
                self.assertEqual(f.q,before)
                self.assertEqual(f.actual,actual)
                self.assertIsNone(f._goal)
                self.assertEqual(f._velocity,[0.]*6)
                self.assertTrue(f.blocked_reason)
                # Recovery needs a newly validated movement.
                self.drive(f,[100.,-450.,740.],label='path-rejection-recovery')

    def test_validation_accept_then_reject_retains_last_accepted_substep(self):
        f = self.make()
        self.sample(f,[100.,-450.,740.])
        accepted = []
        def partial(a,b):
            if not accepted:
                self.path_guard(f)(a,b)
                accepted.append(list(b))
                return True
            return False
        self.assertTrue(self.tick(f,guard=partial))
        self.assertEqual(f.q,accepted[-1])
        self.assertIsNone(f._goal)
        self.assertEqual(f._velocity,[0.]*6)
        # The callback receives copies; changing them must not alter execution.
        self.sample(f,[100.,-450.,740.])
        expected = []
        def mutate_copies(a,b):
            expected.append(b[:])
            a[:]=[9999.]*6
            b[:]=[9999.]*6
            return True
        self.tick(f,guard=mutate_copies)
        self.assertTrue(expected)
        self.assertEqual(f.q,expected[-1])

    def test_stop_token_in_callback_and_stale_frames_cannot_restore_motion(self):
        f = self.make()
        old = f.token
        payload = self.sample(f,[100.,-450.,740.])
        before = list(f.q)
        def stop(a,b):
            f.stop('offline callback stop')
            return True
        self.tick(f,guard=stop)
        self.assertFalse(f.active)
        self.assertNotEqual(f.token,old)
        self.assertEqual(f.q,before)
        payload['seq'] += 1
        self.assertFalse(f.sample(payload,now=f._clock_value)['ok'])
        self.assertFalse(f.start(dict(token=old,session=f.session),now=f._clock_value)['ok'])
        for _ in range(20):
            self.tick(f)
        self.assertEqual(f.q,before)

    def test_long_loss_returns_to_center_and_fresh_sample_recovers(self):
        f = self.make()
        self.drive(f,[100.,-500.,760.],label='before-long-loss')
        previous_token = f.token
        for _ in range(180):
            self.tick(f)
        self.assertTrue(f.waiting)
        self.assertTrue(f.returning)
        self.assertTrue(f.active)
        self.assertEqual(f.token,previous_token)
        self.assertLessEqual(math.dist(self.fk(f.q),CENTER),2.)
        self.drive(f,[-100.,-300.,640.],label='fresh-after-long-loss')
        self.assertFalse(f.waiting)
        self.assertFalse(f.returning)

    def test_box_corners_report_measured_stalls_without_claiming_reachability(self):
        # Exploratory corners are explicitly separate from required convergence.
        # A stalled corner is reported as STALLED_NOT_PASSED, never reachable.
        f = self.make()
        for corner in ([-400.,-550.,950.],[400.,-550.,950.]):
            self.drive(f,corner,frames=120,required=False,label='exploratory-box-corner')
            self.drive(f,CENTER,label='recovery-from-corner')

    def test_controller_limits_270_compact_depth_face_diagnostics(self):
        for tool in (DEFAULT_TOOL,TOOL):
            for opening in (0.,1.):
                with self.subTest(tool=tool['dimensions'],opening=opening):
                    model = model_for('elfin5',copy.deepcopy(tool))
                    f = HandFollow(model)
                    center = [-400.,0.,750.]
                    box = dict(min=[-550.,-100.,600.],max=[-250.,100.,900.])
                    lo,hi = map(list,zip(*MANUAL_LIMITS))
                    result = f.configure(dict(session='compact-diagnostic',aspect=4/3,
                        depth=True,rotation=False,direction=270,depth_mode='gesture',enclosure=box),
                        lower=lo,upper=hi,now=10.)
                    self.assertTrue(result['ok'],result.get('why'))
                    self.assertTrue(f.start(dict(token=f.token,session=f.session),
                        current_q=list(f._center_q),now=10.)['ok'])
                    baseline = dict(tool=tool['dimensions'],opening=opening,center_q=f.q[:],
                        supplied_limits=[lo,hi],urdf_limits=[model.lower,model.upper])
                    print('FOLLOW_COMPACT_CENTER '+json.dumps(baseline),flush=True)
                    errors = []
                    for u,v in ((.4,.65),(.65,.35)):
                        before = list(f.q)
                        for frame in range(80):
                            now=f._clock_value+1/30
                            self.assertTrue(f.sample(dict(token=f.token,session=f.session,
                                seq=f.seq+1,u=u,v=v,hand_open=opening,palm_scale=.2,
                                angle=0.,age_ms=0),now=now)['ok'])
                            old,oldpose=list(f.q),list(f.actual)
                            f.tick(1/30,now=now)
                            for a,b,cap in zip(old,f.q,model.vmax):
                                self.assertLessEqual(abs(b-a),cap/30+1e-8)
                            self.assertLessEqual(math.dist(oldpose[:3],f.actual[:3]),10.+1e-8)
                            local=f._rotate_pose(f.actual,inverse=True)
                            for value,low,high in zip(local,box['min'],box['max']):
                                self.assertLessEqual(low-1e-7,value)
                                self.assertLessEqual(value,high+1e-7)
                        target=f._tracking_target(f.target)[:3]
                        raw=solve_position_step(model,f.q,target,lo,hi)
                        # Diagnostic only: identical geometry with model limit
                        # metadata aligned to explicitly supplied controller
                        # bounds. No change to f/model or executed movement.
                        comparison=copy.copy(model)
                        comparison.lower,comparison.upper=lo[:],hi[:]
                        supplied=solve_position_step(comparison,f.q,target,lo,hi)
                        error=math.dist(model.pose_vec(f.q)[:3],f.target[:3])
                        row=dict(direction=270,box=box,opening=opening,u=u,v=v,
                            q=f.q[:],q_delta=[b-a for a,b in zip(before,f.q)],
                            target=f.target[:3],actual=model.pose_vec(f.q)[:3],error_mm=error,
                            cause=f._solver_cause,detail=f._solver_detail,raw_helper=raw,
                            supplied_metadata_helper=supplied)
                        print('FOLLOW_COMPACT_DIAGNOSTIC '+json.dumps(row,allow_nan=False),flush=True)
                        errors.append(error)
                    self.assertLessEqual(max(errors),2.,baseline)

    def test_deadline_snapshot_is_strict_json_and_holds(self):
        f=self.make()
        self.sample(f,[100.,-450.,740.])
        before=list(f.q)
        f._solver_deadline=time.monotonic()-1.
        self.tick(f)
        self.assertEqual(f.q,before)
        self.assertEqual(f._solver_cause,'deadline')
        json.dumps(f.snapshot(),allow_nan=False)

    def test_caller_limits_accept_actual_270_start_outside_urdf_rounding(self):
        model=model_for('elfin5',copy.deepcopy(TOOL))
        lo,hi=map(list,zip(*MANUAL_LIMITS))
        for sign in (-1.,1.):
            q=[sign*179.999932,-1.20318,81.9487,.00386,6.848,0.]
            self.assertGreater(abs(q[0]),model.upper[0])
            origin=model.pose_vec(q)[:3]
            target=[origin[0],origin[1]+20.,origin[2]+10.]
            result=solve_position_step(model,q,target,lo,hi)
            self.assertIsNotNone(result['q'],result)
            self.assertGreater(result['progress_mm'],1.,result)
            for a,b,low,high in zip(q,result['q'],lo,hi):
                self.assertLessEqual(abs(b-a),8.+1e-9)
                self.assertLessEqual(low,b)
                self.assertLessEqual(b,high)
        q[0]=hi[0]+.001
        result=solve_position_step(model,q,target,lo,hi)
        self.assertIsNone(result['q'])
        self.assertEqual(result['cause'],'joint_limits')


if __name__ == '__main__':
    unittest.main(verbosity=2)
