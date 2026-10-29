"""纯类小步验证与手势深度回归；只允许通过隔离 runner 执行。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import ast
import asyncio
import math
from pathlib import Path
import sys
import unittest
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.hand_follow import HandFollow
from backend.core.sim_tool import DEFAULT_TOOL, model_for
from backend.core import config


class LinearModel:
    """解析线性模型用于检验提交顺序，不代替真实几何碰撞验证。"""
    lower, upper, vmax = [-360.] * 6, [360.] * 6, [90.] * 6

    def pose_vec(self, q):
        return [10*q[0], 10*q[1], 700+10*q[2], *q[3:]]

    def _flange_matrix(self,q):
        # The new position servo works directly on unrounded physical FK.
        p=self.pose_vec(q)
        return [[1.,0.,0.,p[0]/1000],[0.,1.,0.,p[1]/1000],
                [0.,0.,1.,p[2]/1000],[0.,0.,0.,1.]]

    def jacobian(self, q):
        return [[(10. if i < 3 else 1.) if i == j else 0.
                 for j in range(6)] for i in range(6)]

    def ik(self, seed, pose, lower, upper, **kwargs):
        q = [pose[0]/10, pose[1]/10, (pose[2]-700)/10, *pose[3:]]
        ok = all(lo <= v <= hi for v, lo, hi in zip(q, lower, upper))
        return q, 0., 0., ok


class FollowTest(unittest.TestCase):
    def setUp(self):
        self.network = patch('socket.socket.connect', side_effect=AssertionError('禁止连接硬件'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.assertNotIn('backend.core.world', sys.modules)

    def make(self, direction=0):
        # 直接设置纯类运动初态，保留真实 tick、IK 连续性和速度限制实现。
        f = HandFollow(LinearModel())
        f.configured = f.active = True
        f.session = 'offline-safety'
        f.direction = direction
        f._lo, f._hi = [-100., -150., 500.], [100., 150., 900.]
        f._base = f.actual = [0., 0., 700., 0., 0., 0.]
        f.target = f._rotate_pose([50., 0., 700., 0., 0., 0.])
        f._last_sample = 10.
        f.waiting = False
        return f

    def frame(self, f, **changes):
        payload = dict(token=f.token, session=f.session, seq=f.seq+1,
                       u=.5, v=.5, palm_scale=.2, angle=0., age_ms=0.)
        payload.update(changes)
        return payload


class StepValidation(FollowTest):
    def test_micro_waypoint_requires_real_fk_progress_not_a_false_solver_success(self):
        f=self.make()
        f.target=[.2,0.,700.,0.,0.,0.]
        f.solve_goal(10.)
        self.assertIsNotNone(f._goal)
        self.assertGreater(f.model.pose_vec(f._goal)[0],.15)
        f._goal=None
        with patch('backend.core.hand_follow.solve_position_step',return_value={
                'q':list(f.q),'position_error_mm':0.,'progress_mm':1.,'cause':'reached','iterations':1}):
            f.solve_goal(10.04)
        self.assertIsNone(f._goal,'fake solver convergence cannot replace physical FK progress')
        self.assertEqual(f._solver_cause,'invalid_progress')

    def test_point_one_mm_jitter_has_one_bounded_solver_decision_per_interval_and_reverses(self):
        f=self.make()
        failure={'q':None,'position_error_mm':50.,'progress_mm':0.,'cause':'stalled','iterations':18}
        with patch('backend.core.hand_follow.solve_position_step',return_value=failure) as solve:
            for frame in range(100):
                x=50.+(.1 if frame%2 else -.1)
                stamp=10.+frame*.02
                self.assertTrue(f.sample(self.frame(f,u=(x+100)/200),now=stamp)['ok'])
                f.tick(.02,now=stamp)
                self.assertAlmostEqual(f.target[0],x)
                self.assertEqual(f.actual[0],0.)
                self.assertNotEqual(f.status,'跟随中')
                self.assertTrue(f.why)
            self.assertLessEqual(solve.call_count,51)
        self.assertTrue(f.sample(self.frame(f,u=.25),now=12.01)['ok'])
        self.assertTrue(f.tick(.02,now=12.01))
        self.assertLess(f.actual[0],0.)

    def test_expired_worker_is_a_bounded_hold_and_cannot_poison_future_target(self):
        f=self.make()
        f._solver_deadline=0.
        f.solve_goal(10.)
        self.assertIsNone(f._goal)
        self.assertEqual(f._solver_cause,'deadline')
        self.assertIsNone(f._rejected_target)
        self.assertIsNone(f._subdivision_resume)
        del f._solver_deadline
        f.solve_goal(10.04)
        self.assertIsNotNone(f._goal)

    def test_worker_telemetry_remains_json_safe_even_before_first_fk(self):
        import json
        f=self.make()
        f._solver_deadline=0.
        f.solve_goal(10.)
        json.dumps(f.snapshot(),allow_nan=False)

    def test_failed_local_target_is_retried_boundedly_without_legacy_failure_cache(self):
        f=self.make()
        with patch('backend.core.hand_follow.solve_position_step',return_value={
                'q':None,'position_error_mm':50.,'progress_mm':0.,'cause':'stalled','iterations':18}) as solve:
            f.solve_goal(10.)
            f.solve_goal(10.04)
            self.assertEqual(solve.call_count,2)
        f.solve_goal(10.08)
        self.assertIsNotNone(f._goal)

    def test_reversal_discards_unexecuted_goal_and_old_worker_cannot_restore_it(self):
        f = self.make()
        f._goal = [5.,0.,0.,0.,0.,0.]
        f._velocity = [1.,0.,0.,0.,0.,0.]
        old = copy.deepcopy(f)
        before = list(f.q)
        self.assertTrue(f.sample(self.frame(f,u=.1),now=10.)['ok'])
        self.assertEqual(f.q,before)
        self.assertIsNone(f._goal)
        self.assertEqual(f._velocity,[1.,0.,0.,0.,0.,0.],'反向保留速度，由阻尼积分减速而非瞬时清零')
        self.assertFalse(f.accepts_solver_goal(old))
        self.assertTrue(f.tick(.02,now=10.))
        self.assertLess(f.actual[0],0.)

    def test_worker_can_track_newer_input_in_same_direction_without_rewriting_it(self):
        f=self.make()
        f._goal=[1.,0.,0.,0.,0.,0.]
        old=copy.deepcopy(f)
        f.target=[60.,0.,700.,0.,0.,0.]
        before=copy.deepcopy(f.snapshot())
        self.assertTrue(f.accepts_solver_goal(old))
        self.assertEqual(f.snapshot(),before)

    def test_6_6hz_detections_and_short_losses_resume_without_expiring_ownership(self):
        f=self.make()
        token=f.token
        for frame in range(18):
            stamp=10.+frame/6.6
            missing=frame%5==4
            self.assertTrue(f.sample(self.frame(f,u=.8,hand_present=not missing),now=stamp)['ok'])
            self.assertEqual(f.token,token)
            self.assertTrue(f.active)
            self.assertEqual(f.returning,missing)
            for tick in range(7):
                f.tick(.02,now=stamp+tick*.02)
                self.assertTrue(f.active)
                self.assertEqual(f.returning,missing)
        self.assertFalse(f.waiting)
        self.assertGreater(f.actual[0],0.)

    def test_optional_callback_preserves_existing_tick_result(self):
        plain, checked = self.make(), self.make()
        self.assertTrue(plain.tick(.1, now=10.))
        self.assertTrue(checked.tick(.1, now=10., validate_step=lambda before, candidate: True))
        self.assertEqual(plain.q, checked.q)
        self.assertEqual(plain.actual, checked.actual)
        self.assertEqual(plain.base_offset, checked.base_offset)

    def test_every_substep_is_checked_before_commit_from_last_accepted_q(self):
        f = self.make()
        calls = []
        def validate(before, candidate):
            self.assertEqual(before, f.q)
            self.assertNotEqual(candidate, f.q)
            if calls:
                self.assertEqual(before, calls[-1][1])
            calls.append((before[:], candidate[:]))
            return True
        self.assertTrue(f.tick(.1, now=10., validate_step=validate))
        self.assertEqual(len(calls), 10)
        self.assertEqual(f.q, calls[-1][1])

    def test_first_refusal_keeps_pose_and_revokes_old_velocity_and_goal(self):
        f = self.make()
        before, pose, offset, token = f.q[:], f.actual[:], f.base_offset[:], f.token
        self.assertFalse(f.tick(.1, now=10., validate_step=lambda a, b: False))
        self.assertEqual((f.q, f.actual, f.base_offset), (before, pose, offset))
        self.assertEqual(f._velocity, [0.] * 6)
        self.assertIsNone(f._goal)
        self.assertTrue(f.active)
        self.assertEqual(f.token, token)
        self.assertTrue(f.blocked_reason)
        self.assertIn('路径', f.why)

    def test_later_refusal_preserves_earlier_safe_progress_and_returns_changed(self):
        f = self.make()
        calls = []
        def validate(before, candidate):
            calls.append((before[:], candidate[:]))
            return len(calls) < 3
        self.assertTrue(f.tick(.1, now=10., validate_step=validate))
        self.assertEqual(len(calls), 3)
        self.assertEqual(f.q, calls[1][1])
        self.assertEqual(f.actual, f.model.pose_vec(calls[1][1]))
        self.assertNotEqual(f.q, calls[2][1])
        self.assertEqual(f._velocity, [0.] * 6)
        self.assertIsNone(f._goal)

    def test_path_callback_can_reject_safe_endpoints_with_unsafe_chord_interior(self):
        f = self.make()
        calls = []
        def validate(before, candidate):
            calls.append((before[:], candidate[:]))
            # 两端不在障碍内，但弦线经过关节区间 (0.02, 0.025)。
            self.assertLessEqual(before[0], .02)
            self.assertGreaterEqual(candidate[0], .025)
            return max(before[0], candidate[0]) <= .02 or min(before[0], candidate[0]) >= .025
        self.assertFalse(f.tick(.1, now=10., validate_step=validate))
        self.assertEqual(len(calls), 1)
        self.assertEqual(f.q, [0.] * 6)

    def test_callback_exception_fails_closed_at_last_safe_pose(self):
        f = self.make()
        accepted = []
        def validate(before, candidate):
            if accepted:
                raise ValueError('离线几何检查失败')
            accepted.append(candidate[:])
            return True
        self.assertTrue(f.tick(.1, now=10., validate_step=validate))
        self.assertEqual(f.q, accepted[0])
        self.assertEqual(f.actual, f.model.pose_vec(f.q))
        self.assertEqual(f._velocity, [0.] * 6)
        self.assertIn('异常', f.why)
        self.assertIn('ValueError', f.blocked_reason)

    def test_only_explicit_true_authorizes_a_step(self):
        for result in (None, 1, 'true', [], {}):
            with self.subTest(result=result):
                f = self.make()
                self.assertFalse(f.tick(.1, now=10., validate_step=lambda a, b: result))
                self.assertEqual(f.q, [0.] * 6)
                self.assertTrue(f.blocked_reason)

    def test_callback_arguments_cannot_mutate_internal_joint_vectors(self):
        plain, checked = self.make(), self.make()
        def validate(before, candidate):
            before[:] = [999.] * 6
            candidate[:] = [-999.] * 6
            return True
        plain.tick(.1, now=10.)
        checked.tick(.1, now=10., validate_step=validate)
        self.assertEqual(checked.q, plain.q)
        self.assertEqual(checked.actual, plain.actual)

    def test_callback_cannot_bypass_bounds_but_position_follow_does_not_lock_orientation(self):
        for kind in ('workspace', 'joint', 'orientation'):
            with self.subTest(kind=kind):
                f = self.make()
                # 模拟已有 IK 目标，单独检验真实候选提交环节的内建保护。
                f._last_ik = 10.
                f._goal = [1., 0., 0., 0., 0., 0.]
                if kind == 'workspace': f._hi[0] = 0.
                elif kind == 'joint': f._upper[0] = 0.
                else: f._goal = [0., 0., 0., 20., 0., 0.]
                calls = []
                def validate(before, candidate):
                    calls.append((before[:], candidate[:]))
                    self.assertLessEqual(abs(candidate[3]),f.model.vmax[3]*.01+1e-8)
                    return True
                changed = f.tick(.01, now=10., validate_step=validate)
                if kind == 'orientation':
                    self.assertTrue(changed)
                    self.assertTrue(calls)
                    self.assertLessEqual(abs(f.q[3]), f.model.vmax[3]*.01+1e-8)
                else:
                    self.assertFalse(changed)
                    self.assertEqual(f.q, [0.] * 6)
                    self.assertEqual(calls, [])

    def test_stopping_inside_validator_cannot_commit_candidate_after_stop(self):
        f = self.make()
        def validate(before, candidate):
            f.stop('回调期间停止')
            return True
        self.assertFalse(f.tick(.1, now=10., validate_step=validate))
        self.assertFalse(f.active)
        self.assertEqual(f.q, [0.] * 6)

    def test_fresh_sample_can_resume_after_refusal_without_stale_reason(self):
        f = self.make()
        f.tick(.1, now=10., validate_step=lambda a, b: False)
        self.assertTrue(f.sample(self.frame(f, u=.2), now=10.04)['ok'])
        self.assertTrue(f.tick(.04, now=10.04, validate_step=lambda a, b: True))
        self.assertLess(f.q[0], 0.)
        self.assertEqual(f.blocked_reason, '')
        self.assertEqual(f.why, '')

    def test_loss_return_to_center_uses_same_step_validation(self):
        f = self.make()
        f.tick(.1, now=10.)
        before = f.q[:]
        f.return_to_center()
        self.assertFalse(f.tick(.04, now=10.04, validate_step=lambda a, b: False))
        self.assertEqual(f.q, before)
        self.assertTrue(f.returning)
        self.assertTrue(f.active)


class GestureDepth(FollowTest):
    def test_missing_or_null_opening_keeps_last_valid_local_depth_on_four_sides(self):
        for direction in (0, 90, 180, 270):
            for opening in (0., 1.):
                for explicit_null in (False, True):
                    with self.subTest(direction=direction, opening=opening, null=explicit_null):
                        f = self.make(direction)
                        f.depth, f.depth_mode = True, 'gesture'
                        self.assertTrue(f.sample(self.frame(f, hand_open=opening), now=10.)['ok'])
                        expected = 150. if opening == 0 else -150.
                        changes = {'hand_open': None} if explicit_null else {}
                        self.assertTrue(f.sample(self.frame(f, u=.8, v=.3, **changes), now=10.04)['ok'])
                        local = f._rotate_pose(f.target, inverse=True)
                        self.assertAlmostEqual(local[0], 60.)
                        self.assertAlmostEqual(local[1], expected)
                        self.assertAlmostEqual(local[2], 780.)

    def test_missing_before_first_valid_opening_uses_neutral_depth(self):
        f = self.make()
        f.depth, f.depth_mode = True, 'gesture'
        self.assertTrue(f.sample(self.frame(f), now=10.)['ok'])
        self.assertEqual(f.target[1], f._base[1])

    def test_invalid_opening_does_not_replace_last_valid_depth_or_sequence(self):
        f = self.make()
        f.depth, f.depth_mode = True, 'gesture'
        self.assertTrue(f.sample(self.frame(f, hand_open=1.), now=10.)['ok'])
        sequence = f.seq
        for opening in (float('nan'), True, -1., 1.1):
            self.assertFalse(f.sample(self.frame(f, hand_open=opening), now=10.01)['ok'])
            self.assertEqual(f.seq, sequence)
        self.assertTrue(f.sample(self.frame(f), now=10.04)['ok'])
        self.assertEqual(f.target[1], -150.)

    def test_full_palm_loss_resets_depth_hold_for_explicit_return_to_center(self):
        f = self.make()
        f.depth, f.depth_mode = True, 'gesture'
        self.assertTrue(f.sample(self.frame(f, hand_open=1.), now=10.)['ok'])
        self.assertTrue(f.sample(self.frame(f, hand_present=False), now=10.04)['ok'])
        self.assertTrue(f.returning)
        self.assertEqual(f.target, f._base)
        self.assertTrue(f.sample(self.frame(f), now=10.08)['ok'])
        self.assertEqual(f.target[1], f._base[1])

    def test_disabled_depth_stays_neutral_even_with_opening_data(self):
        f = self.make()
        f.depth, f.depth_mode = False, 'gesture'
        for opening in (1., 0., None):
            self.assertTrue(f.sample(self.frame(f, hand_open=opening), now=10.)['ok'])
            self.assertEqual(f.target[1], f._base[1])


class SourceModelIntegration(FollowTest):
    def make_real_model(self):
        f = HandFollow(model_for('elfin5', DEFAULT_TOOL))
        result = f.configure(dict(session='source-model', aspect=4/3, depth=True,
            depth_mode='gesture', rotation=False, enclosure={
                'min': [-400., -550., 450.], 'max': [400., -250., 950.]}), now=10.)
        self.assertTrue(result['ok'], result.get('why'))
        result = f.start(dict(token=f.token, session=f.session), now=10., current_q=f._center_q)
        self.assertTrue(result['ok'], result.get('why'))
        return f

    def test_source_fk_ik_each_commit_passes_callback_and_original_bounds(self):
        f = self.make_real_model()
        calls = []
        self.assertTrue(f.sample(self.frame(f, u=.55, v=.5, hand_open=.5), now=10.)['ok'])
        def validate(before, candidate):
            self.assertEqual(before, f.q)
            self.assertTrue(f._valid_q(candidate))
            self.assertTrue(f._inside(f.model.pose_vec(candidate)))
            calls.append((before[:], candidate[:]))
            return True
        initial = f.actual[:]
        self.assertTrue(f.tick(.04, now=10., validate_step=validate))
        self.assertEqual(len(calls), 4)
        self.assertEqual(f.q, calls[-1][1])
        self.assertLessEqual(math.dist(initial[:3], f.actual[:3]), 12.+1e-8)

    def test_public_restart_does_not_reuse_previous_session_depth(self):
        f = self.make_real_model()
        self.assertTrue(f.sample(self.frame(f, hand_open=1.), now=10.)['ok'])
        self.assertEqual(f.target[1], -550.)
        f.stop()
        self.assertTrue(f.start(dict(token=f.token, session=f.session), now=10.04,
                                current_q=f._center_q)['ok'])
        self.assertTrue(f.sample(self.frame(f), now=10.04)['ok'])
        self.assertEqual(f.target[1], f._base[1])


class CameraGapRegression(FollowTest):
    def test_center_setup_propagates_bounded_deadline_without_relaxing_tolerances(self):
        f = SourceModelIntegration().make_real_model()
        from backend.core import hand_follow as implementation
        original = implementation.solve_position_step
        with patch.object(implementation, 'solve_position_step', wraps=original) as calls:
            result = f.configure(dict(session='bounded-center',aspect=4/3,depth=True,rotation=False,
                depth_mode='gesture',enclosure={'min':[-400.,-550.,450.],'max':[400.,-250.,950.]}),now=11.)
        self.assertTrue(result['ok'],result.get('why'))
        self.assertTrue(calls.called)
        for call in calls.call_args_list:
            self.assertIn('deadline',call.kwargs)
            self.assertLessEqual(call.kwargs['max_joint_step'],8.)
            self.assertLessEqual(call.kwargs['max_cartesian_step'],24.)

    def test_already_centered_configuration_reuses_verified_pose_without_ik(self):
        f = SourceModelIntegration().make_real_model()
        payload = dict(session='center-reuse',aspect=4/3,depth=True,rotation=False,
            depth_mode='gesture',enclosure={'min':[-400.,-550.,450.],'max':[400.,-250.,950.]})
        center = list(f._center_q)
        with patch.object(f.model, 'ik', side_effect=AssertionError('已在中心不应再逆解')):
            result = f.configure(payload, now=11., current_q=center)
        self.assertTrue(result['ok'], result.get('why'))
        self.assertEqual(f._center_q, center)

    def test_sideways_box_center_uses_position_only_without_wrist_lock(self):
        model = model_for('elfin5', DEFAULT_TOOL)
        f = HandFollow(model)
        lower, upper = map(list, zip(*config.MANUAL_LIMITS))
        with patch.object(model, 'ik', side_effect=AssertionError('position setup must not impose ABC')):
            result = f.configure(dict(session='offline-sideways-center',aspect=4/3,depth=True,
                rotation=False,direction=0,depth_mode='gesture',
                enclosure={'min':[-950.,-75.,475.],'max':[-250.,75.,825.]}),
                lower=lower, upper=upper)
        self.assertTrue(result['ok'], result.get('why'))
        self.assertLessEqual(math.dist(f.model.pose_vec(f._center_q)[:3],[-600.,0.,650.]),.1)

    def test_six_hz_with_alternating_misses_never_oscillates_to_center(self):
        f = self.make()
        for index in range(40):
            stamp = 10.+index/6.
            present = index % 3 != 1
            self.assertTrue(f.sample(self.frame(f, u=.8, hand_present=present,
                age_ms=120. if present else 0.), now=stamp,
                captured_at=stamp-.12 if present else None, camera_gap=True)['ok'])
            self.assertFalse(f.returning, '单帧漏检不得改写为回中目标')
            if present:
                self.assertGreater(f.target[0], 40.)
            else:
                self.assertEqual(f.target, f.actual)

    def test_brief_camera_gap_holds_actual_and_clears_unexecuted_motion(self):
        f = self.make()
        self.assertTrue(f.sample(self.frame(f, u=.8), now=10.)['ok'])
        f._goal = [.1, 0., 0., 0., 0., 0.]
        f._velocity = [1., 0., 0., 0., 0., 0.]
        accepted = (list(f.q), list(f.actual), f._last_observation)
        for stamp in (10.31, 10.40, 10.59):
            reply = f.sample(self.frame(f, hand_present=False), now=stamp, camera_gap=True)
            self.assertTrue(reply['ok'])
            self.assertFalse(f.returning)
            self.assertEqual(reply['state']['input_phase'], 'holding')
            self.assertEqual((f.q, f.actual, f._last_observation), accepted)
            self.assertEqual(f.target, f.actual)
            self.assertIsNone(f._goal)
            self.assertEqual(f._velocity, [0.]*6)
            self.assertFalse(f.tick(.02, now=stamp))
        self.assertTrue(f.sample(self.frame(f, u=.7), now=10.60, camera_gap=True)['ok'])
        self.assertEqual(f.snapshot()['input_phase'], 'tracking')
        self.assertFalse(f.returning)

    def test_absence_heartbeats_cannot_extend_camera_gap_forever(self):
        f = self.make()
        f.sample(self.frame(f, u=.8), now=10.)
        for stamp in (10.31, 10.50, 10.64, 11.299):
            f.sample(self.frame(f, hand_present=False), now=stamp, camera_gap=True)
            self.assertFalse(f.returning)
        f.sample(self.frame(f, hand_present=False), now=11.3, camera_gap=True)
        self.assertTrue(f.returning)
        self.assertEqual(f.target, f._base)
        self.assertEqual(f._last_observation, 10.)


def proposed_world_methods():
    """Apply review-only diff IN MEMORY and extract methods; never import World.

    The runner is unchanged: production world.py is copied without overlay.
    Each exact old hunk and its declared line counts must match before testing.
    """
    root = Path(__file__).resolve().parents[1]
    source = (root/'backend/core/world.py').read_text(encoding='utf-8')
    staged_world = root/'tools/staged_follow_world.py'
    if staged_world.exists():
        assert source.rstrip() == staged_world.read_text(encoding='utf-8').rstrip(), 'runner must install the reviewed staged World'
        patch_text = ''
    else:
        patch_text = (root/'tools/staged_follow_world.patch').read_text(encoding='utf-8')
    import re
    sections = re.split(r'^(@@[^\n]+@@)\n', patch_text, flags=re.M)
    for index in range(1, len(sections), 2):
        header, body = sections[index:index+2]
        lines = body.splitlines(keepends=True)
        old = ''.join(line[1:] for line in lines if line[:1] in (' ', '-'))
        new = ''.join(line[1:] for line in lines if line[:1] in (' ', '+'))
        counts = re.fullmatch(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@', header)
        assert counts, header
        assert len(old.splitlines()) == int(counts[2]), header
        assert len(new.splitlines()) == int(counts[4]), header
        assert source.count(old) == 1, (header, old)
        source = source.replace(old, new, 1)
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'World')
    wanted = {'_hand_camera_identity', '_hand_input_identity', 'hand_follow_action',
              '_tick_hand_follow', '_poll_hand_solver'}
    cls.body = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted]
    assert len(cls.body) == len(wanted)
    namespace = dict(math=math, time=time, copy=copy, asyncio=asyncio, HandFollow=HandFollow,
                     tool_capsules=lambda model, caps: caps, RED='red', ALARM='alarm',
                     evaluate=lambda *a, **kw: {'level': 'green'})
    exec(compile(ast.Module(body=[cls], type_ignores=[]), '<review-only-world-methods>', 'exec'), namespace)
    return namespace['World']


def finish_without_event_loop(coroutine):
    # Sample routes have no await on these branches. Drive exactly one step;
    # even asyncio's Windows self-pipe sockets remain forbidden by the guard.
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        coroutine.close()
    raise AssertionError('unexpected asynchronous work in offline sample path')


class WorldCameraLossProposal(FollowTest):
    """Real method bodies with fake collaborators, not an initialized World."""
    def fixture(self):
        f = self.make()
        f.sample(self.frame(f, u=.8), now=10., camera_gap=True)
        f._goal = [1., 0., 0., 0., 0., 0.]
        cls = proposed_world_methods()
        w = cls.__new__(cls)
        w.hand_follow = f
        w._cfg_rev = w._hand_cfg_rev = 1
        w._hand_camera_key = (f.session, 640, 480)
        w._hand_input_kind = 'camera'
        w._hand_centering = False
        w._hand_async_solver = True
        w._hand_solver_task = None
        w._hand_available = lambda: True
        w._hand_busy = lambda: False
        w._hand_camera_reader = lambda: None
        w.motion_model = lambda: f.model
        w.objects = SimpleNamespace(list=lambda: [])
        w.limits = lambda: (f._lower, f._upper)
        w.settings = {'sim_speed_pct': 100}
        w.sim = SimpleNamespace(q=list(f.q))
        w.log = SimpleNamespace(add=lambda *args: None)
        w._object_hits = lambda *args: []
        return w, f

    def test_all_three_world_camera_gap_paths_hold_instead_of_immediate_return(self):
        for path in ('unavailable', 'changed', 'tick', 'reader-error'):
            with self.subTest(path=path):
                w, f = self.fixture()
                before = list(f.q)
                if path in ('unavailable', 'changed'):
                    camera = None
                    if path == 'changed':
                        camera = {'active': {'session': f.session, 'state': 'streaming',
                            'last_frame_at': time.time(),
                            'images': {'color': {'width': 800, 'height': 600, 'seq': 2}}}}
                    with patch('backend.core.hand_follow.time.monotonic', return_value=10.1):
                        result = finish_without_event_loop(w.hand_follow_action('sample', self.frame(f), camera))
                    self.assertTrue(result['ok'])
                else:
                    if path == 'reader-error':
                        def broken_reader():
                            raise RuntimeError('offline disconnection')
                        w._hand_camera_reader = broken_reader
                    w._tick_hand_follow(.02, 10.1)
                self.assertTrue(f.waiting)
                self.assertFalse(f.returning)
                self.assertEqual(f.input_phase, 'holding')
                self.assertEqual(f.q, before)
                self.assertEqual(w.sim.q, before)
                w._tick_hand_follow(.02, 11.099)
                self.assertFalse(f.returning)
                w._tick_hand_follow(0., 11.1)
                self.assertTrue(f.returning)

    def test_poll_discards_late_success_and_failure_in_hold_and_after_recovery(self):
        for failed in (False, True):
            for recovered in (False, True):
                with self.subTest(failed=failed, recovered=recovered):
                    w, f = self.fixture()
                    old = copy.deepcopy(f)
                    if failed:
                        old._goal = None
                    old.status = 'OLD RESULT MUST NOT INSTALL'
                    class Done:
                        def done(self): return True
                        def result(self):
                            return f, f.token, 1, False, 10., old
                    w._hand_solver_task = Done()
                    f.camera_missing(now=10.1)
                    if recovered:
                        f.sample(self.frame(f, u=.8), now=10.2, camera_gap=True)
                        f._last_ik = 10.2  # Avoid launching a new test worker.
                        w._hand_camera_reader = None
                    before = copy.deepcopy(f.snapshot())
                    w._poll_hand_solver(10.2)
                    self.assertIsNone(w._hand_solver_task)
                    self.assertEqual(f.snapshot(), before)
                    self.assertIsNone(f._goal)

    def test_poll_detects_silent_expiry_before_delivering_old_solver(self):
        w, f = self.fixture()
        old = copy.deepcopy(f)
        class Done:
            def done(self): return True
            def result(self): return f, f.token, 1, False, 10., old
        w._hand_camera_reader = None
        w._hand_solver_task = Done()
        before = list(f.q)
        w._poll_hand_solver(10.3)
        self.assertTrue(f.waiting)
        self.assertFalse(f.returning)
        self.assertIsNone(f._goal)
        self.assertEqual(f.q, before)

    def test_mouse_explicit_release_world_route_keeps_original_semantics(self):
        w, f = self.fixture()
        f.session = 'mouse-offline-12345'
        w._hand_input_kind = 'mouse'
        w._hand_camera_key = (f.session, 640, 480)
        with patch('backend.core.hand_follow.time.monotonic', return_value=10.1):
            result = finish_without_event_loop(w.hand_follow_action('sample',
                self.frame(f, hand_present=False), None, input_kind='mouse'))
        self.assertTrue(result['ok'])
        self.assertTrue(f.returning)

    def test_mouse_release_retains_immediate_return_but_camera_initial_loss_holds(self):
        f = self.make()
        f.sample(self.frame(f, hand_present=False), now=10., camera_gap=True)
        self.assertFalse(f.returning)
        self.assertTrue(f.waiting)
        f = self.make()
        f.sample(self.frame(f, u=.8), now=10.)
        f.sample(self.frame(f, hand_present=False), now=10.31)
        self.assertTrue(f.returning)
        f = self.make()
        f.sample(self.frame(f, u=.8), now=10.)
        f.tick(0., now=10.51)
        self.assertTrue(f.returning)

    def test_camera_loss_exact_one_second_boundary_and_recovery_cancel_return(self):
        for recover_at in (10.999, 11.0, 11.1):
            with self.subTest(recover_at=recover_at):
                f = self.make()
                f.sample(self.frame(f, u=.8), now=10., camera_gap=True)
                f._goal = [1., 0., 0., 0., 0., 0.]
                f._velocity = [1.]*6
                old = copy.deepcopy(f)
                f.sample(self.frame(f, hand_present=False), now=10., camera_gap=True)
                before = list(f.q)
                f.tick(.02, now=10.999)
                self.assertFalse(f.returning)
                self.assertEqual(f.q, before)
                self.assertFalse(f.accepts_solver_goal(old))
                self.assertFalse(f.accepts_solver_failure(old))
                if recover_at >= 11.:
                    f.tick(0., now=11.)
                    self.assertTrue(f.returning)
                self.assertTrue(f.sample(self.frame(f, u=.8), now=recover_at, camera_gap=True)['ok'])
                self.assertFalse(f.waiting)
                self.assertFalse(f.returning)
                self.assertFalse(f.accepts_solver_goal(old), 'pre-loss solver must remain invalid after recovery')
                self.assertTrue(f.tick(.02, now=recover_at))

    def test_silent_camera_gap_stops_at_motion_ttl_and_uses_same_loss_timer(self):
        f = self.make()
        f.sample(self.frame(f, u=.8), now=10., camera_gap=True)
        f._goal = [1., 0., 0., 0., 0., 0.]
        f._velocity = [1.]*6
        before = list(f.q)
        f.tick(.02, now=10.3)
        self.assertEqual(f.q, before)
        self.assertTrue(f.waiting)
        self.assertFalse(f.returning)
        f.tick(.02, now=11.299)
        self.assertEqual(f.q, before)
        self.assertFalse(f.returning)
        f.tick(0., now=11.3)
        self.assertTrue(f.returning)

    def test_stale_or_repeated_observations_cannot_reset_loss_clock(self):
        f = self.make()
        f.sample(self.frame(f, u=.8), now=10., camera_gap=True)
        f.sample(self.frame(f, hand_present=False), now=10.1, camera_gap=True)
        for stamp, age in ((10.15, 150.), (10.2, 300.), (10.3, 350.)):
            self.assertFalse(f.sample(self.frame(f, age_ms=age), now=stamp, camera_gap=True)['ok'])
            self.assertTrue(f.waiting)
        f.tick(0., now=11.1)
        self.assertTrue(f.returning)
        self.assertEqual(f._last_observation, 10.)


if __name__ == '__main__':
    unittest.main(verbosity=2)
