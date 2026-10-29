"""共用仿真手掌算法的31项纯类回归，仅允许离线副本执行。

契约迁移原因（不改变生产边界）：
* 裸法兰/自动整机包络夹具已过期：改为当前显示工具与显式可达 TCP 盒。
* 框约束 TCP，不要求整机在相机框内；底座始终固定在 Z=0。
* World 负责启动轨迹；纯类从传入 current_q 起步，不能要求瞬移。
* 仿真丢掌/超时回中等待且保留令牌，主动停止才撤销。
* 当前速度界限为300mm/s、model.vmax；IK决策间隔1/30s，有限重试。
* 全机碰撞/几何失效由 World 的 validate_step 门禁验证，不伪称纯类自带。
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import json
import inspect
import math
import os
from pathlib import Path
import random
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.hand_follow import HandFollow
from backend.core.kinematics import RobotModel
from backend.core.hand_envelope import robot_bounds, contains_bounds, make_enclosure
from backend.core.sim_tool import DEFAULT_TOOL, model_for


class HandFollowTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ['HUYAN_NO_REAL'], '1')
        self.assertNotIn('backend.core.world', sys.modules, '纯类测试不得启动World或后台控制')
        self.network = patch('asyncio.open_connection', side_effect=AssertionError('禁止连接硬件或后端'))
        self.network.start()
        self.addCleanup(self.network.stop)
        sockets = patch('socket.socket.connect', side_effect=AssertionError('禁止套接字连接'))
        sockets.start()
        self.addCleanup(sockets.stop)

    def configuration(self, **options):
        aspect = options.get('aspect', 16/9)
        width = min(160., 90.*aspect)
        height = width/aspect
        payload = dict(session='camera-1', aspect=aspect, depth=False, rotation=False,
                       enclosure={'min': [-width/2, -450., 750.-height/2],
                                  'max': [width/2, -350., 750.+height/2]})
        payload.update(options)
        return payload

    def make(self, **options):
        model = model_for('elfin5', DEFAULT_TOOL)
        follow = HandFollow(model)
        result = follow.configure(self.configuration(**options), now=10.)
        self.assertTrue(result["ok"], result.get("why"))
        return model, follow

    def start(self, follow, now=10.):
        result = follow.start({"token": follow.token, "session": follow.session}, now=now)
        self.assertTrue(result["ok"], result.get("why"))

    def frame(self, follow, seq=0, **values):
        return dict(token=follow.token, session=follow.session, seq=seq,
                    u=.5, v=.5, palm_scale=.2, angle=0., age_ms=0., **values)

    def sample(self, follow, seq=0, now=10., **values):
        payload = self.frame(follow, seq)
        payload.update(values)
        result = follow.sample(payload, now=now)
        self.assertTrue(result["ok"], result.get("why"))

    def check_state(self, model, follow):
        s = follow.snapshot()
        json.dumps(s, allow_nan=False)
        offset = s["base_offset"]
        self.assertEqual(offset[0], 0.)
        self.assertEqual(offset[2], HandFollow.BASE_Z)
        self.assertEqual(offset[2], 0., "The virtual base must rest on the ground")
        self.assertEqual(offset, [0., 0., 0.], '前后跟随必须弯曲关节，不许平移底座')
        for value, low, high in zip(s['q'], model.lower, model.upper):
            self.assertTrue(math.isfinite(value) and low <= value <= high)
        local = model.pose_vec(s["q"])
        expected = [local[i] + offset[i] for i in range(3)] + local[3:]
        self.assertEqual(s["actual"], expected)
        for i in range(3):
            self.assertLessEqual(s["bounds"]["min"][i], s["actual"][i])
            self.assertGreaterEqual(s["bounds"]["max"][i], s["actual"][i])
        body = robot_bounds(model, s["q"], offset)
        self.assertGreaterEqual(body["min"][2], HandFollow.GROUND_CLEARANCE)

    def test_configure_actual_ik_rectangle_and_nonsingular_seed(self):
        model, f = self.make()
        self.check_state(model, f)
        s = f.snapshot()
        self.assertFalse(s["active"])
        self.assertTrue(f._nonsingular(s["q"]))
        width = s["bounds"]["max"][0] - s["bounds"]["min"][0]
        height = s["bounds"]["max"][2] - s["bounds"]["min"][2]
        self.assertAlmostEqual(width / height, 16/9)
        for point in s["corners"] + [s["actual"][:3]]:
            local = [point[i] - s["base_offset"][i] for i in range(3)]
            q, ep, er, ok = model.ik(s["q"], local + s["actual"][3:], restarts=False, iters=45)
            self.assertTrue(ok, (ep, er))

    def test_seed_fk_posture_evidence(self):
        model = model_for('elfin5', DEFAULT_TOOL)
        evidence = []
        for values in ((-30.,30.,0.,30.,0.), (24.6,95.9,0.,18.7,0.),
                       (-77.7,-95.9,0.,108.2,0.)):
            q = [-90., *values]
            tf = model.fk(q)
            point = lambda name: [tf[name][i][3]*1000. for i in range(3)]
            shoulder, elbow, wrist_root, wrist = map(point,
                ('elfin_link2','elfin_link3','elfin_link4','elfin_link5'))
            upper = [b-a for a,b in zip(shoulder,elbow)]
            forearm = [b-a for a,b in zip(wrist_root,wrist)]
            row = dict(q=q,shoulder=shoulder,elbow=elbow,wrist=wrist,
                upper=upper,forearm=forearm,upper_up=upper[2]/math.dist(shoulder,elbow),
                forearm_forward=-forearm[1]/math.dist(wrist_root,wrist),tcp=model.pose_vec(q))
            evidence.append(dict(q=q, upper_up=round(row['upper_up'],4),
                forearm_forward=round(row['forearm_forward'],4),
                tcp=[round(v,2) for v in row['tcp'][:3]]))
            self.assertGreater(math.dist(shoulder,elbow),300.)
        print('SEED_FK '+json.dumps(evidence),flush=True)

    def test_natural_center_automatic_box_and_tool_compatibility(self):
        tools = [None, DEFAULT_TOOL, dict(kind='library',id='robotiq-2f85',rotation=[0,0,0])]
        for tool in tools:
            for direction in (0,90,180,270):
                with self.subTest(tool=tool,direction=direction):
                    f = HandFollow(model_for('elfin5',tool))
                    lower, upper = [-360.]*6, [360.]*6
                    result = f.configure(dict(session='natural-auto',aspect=4/3,depth=False,
                        rotation=False,direction=direction),lower=lower,upper=upper,now=10.)
                    print('AUTO_CENTER '+json.dumps(dict(tool=tool,direction=direction,
                        ok=result['ok'],why=result.get('why'),q=f._center_q)),flush=True)
                    self.assertTrue(result['ok'], result.get('why'))
                    self.assertEqual(f.base_offset,[0.,0.,0.])
                    self.assertTrue(f._valid_q(f._center_q))
                    reference = robot_bounds(f.model,f.reference_q,[0.,0.,0.])
                    self.assertEqual(f.outer_bounds,make_enclosure(reference,4/3,margin=80.))
                    self.assertTrue(f._inside(f.actual))
                    box = dict(min=[-400.,-550.,450.],max=[400.,-250.,950.])
                    result = f.configure(dict(session='natural-tool-custom',aspect=4/3,depth=True,
                        rotation=False,direction=direction,enclosure=box),lower=lower,upper=upper,now=11.)
                    self.assertTrue(result['ok'], result.get('why'))
                    self.assertLess(math.dist(f.model.pose_vec(f._center_q)[:3],[0.,-400.,700.]),.01)
                    self.assertEqual(f.snapshot()['bounds'],box)

    def test_base_rests_on_original_fk_ground_and_cannot_penetrate_it(self):
        model, f = self.make()
        self.start(f)
        state = f.snapshot()
        self.assertEqual(state["base_offset"], [0., 0., 0.])
        # Independent of configurable constants: raw fixed-base FK must remain
        # at the original URDF ground, not be lifted together with the camera box.
        base = model.fk(state["q"])["elfin_base"]
        self.assertEqual(base[2][3] * 1000., 0.)
        self.assertEqual(base[2][3] * 1000. + state["base_offset"][2], 0.)
        body = robot_bounds(model, state["q"], [0., 0., 0.])
        self.assertEqual(body["min"][2], 0.)
        self.assertTrue(f._body_inside(state["q"], [0., 0., 0.]))
        # A tiny real penetration must fail even if the fixed enclosure extends
        # below the floor. No positive epsilon may silently admit it.
        self.assertFalse(f._body_inside(state["q"], [0., 0., -.001]))

    def test_corners_map_exactly_once_without_mirroring(self):
        _, f = self.make()
        self.start(f)
        for seq, (u, v) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
            self.sample(f, seq, u=u, v=v)
            self.assertEqual(f.snapshot()["target"][:3], f.snapshot()["corners"][seq])

    def test_configure_rejects_unknown_or_invalid_fields(self):
        _, f = self.make()
        good = dict(session="a", aspect=1., depth=False, rotation=False)
        bad = [dict(good, extra=1), dict(good, aspect=True), dict(good, aspect=float("nan")),
               dict(good, aspect=10**1000), dict(good, session=""), dict(good, rotation=1)]
        for p in bad:
            self.assertFalse(f.configure(p, now=10.)["ok"])
        self.assertFalse(f.configure(good, lower=[True]*6, now=10.)["ok"])
        self.assertFalse(f.configure(good, lower=[0]*6, upper=[0]*6, now=10.)["ok"])

    def test_start_rotates_token_old_frames_cannot_run(self):
        _, f = self.make()
        old = self.frame(f)
        token = f.token
        self.start(f)
        self.assertNotEqual(token, f.token)
        self.assertFalse(f.sample(old, now=10.)["ok"])
        self.assertFalse(f.start(dict(token=f.token, session=f.session, extra=True), now=10.)["ok"])
        self.assertFalse(f.start(dict(token=f.token, session=f.session), now=10.)["ok"])

    def test_every_start_preserves_supplied_actual_and_requests_center(self):
        model, f = self.make()
        center_q = list(f.q)
        center_pose = list(f.actual)
        for restart in range(3):
            start_time = 10. + restart * 2.
            actual_q = list(f.q)
            actual_pose = list(f.actual)
            result = f.start({'token': f.token, 'session': f.session},
                             now=start_time, current_q=actual_q)
            self.assertTrue(result['ok'], result.get('why'))
            state = f.snapshot()
            self.assertEqual(state["q"], actual_q)
            self.assertEqual(state["actual"], actual_pose)
            self.assertEqual(state["target"], center_pose)
            self.assertIsNot(f.q, f._center_q)
            self.assertIsNot(f.q, actual_q)
            self.assertTrue(state['waiting'])
            for axis in range(3):
                center = (state["bounds"]["min"][axis] + state["bounds"]["max"][axis]) / 2
                self.assertAlmostEqual(state["target"][axis], center, delta=.01)
            for i in range(25):
                stamp = start_time + i*.02
                self.sample(f, i, now=stamp, u=1., v=0.)
                f.tick(.02, now=stamp)
                self.check_state(model, f)
            self.assertGreater(math.dist(f.q, center_q), .1)
            self.assertEqual(f._center_q, center_q)
            f.stop()

    def test_samples_reject_nan_bool_huge_age_session_and_replay(self):
        _, f = self.make()
        self.start(f)
        for key, val in (("u", True), ("u", float("nan")), ("v", 2), ("seq", True),
                         ("seq", 10**1000), ("angle", float("inf")), ("angle", 10**1000),
                         ("palm_scale", 0), ("palm_scale", -1), ("age_ms", 351),
                         ("age_ms", -1), ("session", "other"), ("extra", 1)):
            p = self.frame(f)
            p[key] = val
            self.assertFalse(f.sample(p, now=10.)["ok"], (key, val))
        self.sample(f)
        self.assertFalse(f.sample(self.frame(f), now=10.)["ok"])
        self.assertEqual(f.seq, 0)

    def test_loss_waits_returns_center_and_fresh_frames_resume_same_token(self):
        for use_tick in (False, True):
            _, f = self.make()
            self.start(f)
            token = f.token
            if use_tick:
                f.tick(.02, now=10.501)
            else:
                self.sample(f, now=10.501, hand_present=False)
            self.assertTrue(f.active)
            self.assertTrue(f.waiting)
            self.assertTrue(f.returning)
            self.assertEqual(token, f.token)
            self.assertEqual(f.target, f._base)
            self.sample(f, 1, now=10.502)
            self.assertFalse(f.waiting)
            self.assertFalse(f.returning)
            f.stop()
            self.assertNotEqual(token, f.token)

    def test_camera_motion_ttl_and_loss_hold_are_independent(self):
        model, f = self.make()
        self.start(f)
        self.assertTrue(f.sample(dict(self.frame(f), u=.8), now=10., camera_gap=True)['ok'])
        f.tick(.02, now=10.)
        before, actual = list(f.q), list(f.actual)
        # Silence reaches the original 300ms motion TTL, then holds for 1s.
        for stamp in (10.3, 10.5, 11.299):
            self.assertFalse(f.tick(.02, now=stamp))
            self.assertEqual((f.q, f.actual), (before, actual))
            self.assertFalse(f.returning)
            self.check_state(model, f)
        self.assertFalse(f.tick(0., now=11.3))
        self.assertTrue(f.returning)
        self.assertTrue(f.sample(dict(self.frame(f, seq=1), u=.2), now=11.3, camera_gap=True)['ok'])
        self.assertFalse(f.returning)
        self.assertTrue(f.tick(.02, now=11.3))
        self.check_state(model, f)

    def test_old_frame_age_counts_toward_watchdog(self):
        _, f = self.make()
        self.start(f)
        self.sample(f, now=10.1, age_ms=350)
        f.tick(.02, now=10.26)
        self.assertTrue(f.active)
        self.assertTrue(f.returning)
        self.assertTrue(f.waiting)
        self.assertAlmostEqual(f._last_sample, 9.75)

    def test_depth_waits_for_stable_baseline_and_clamps(self):
        m, f = self.make(aspect=1., depth=True)
        self.start(f)
        base_y = f.actual[1]
        for seq, scale in enumerate((.1, .3, .2, .2, .2)):
            self.sample(f, seq, palm_scale=scale)
            self.assertEqual(f.target[1], base_y)
        self.sample(f, 5, palm_scale=.8)
        self.assertEqual(f.target[1], f.snapshot()["bounds"]["min"][1])
        self.sample(f, 6, palm_scale=.01)
        self.assertEqual(f.target[1], f.snapshot()["bounds"]["max"][1])

    def test_rotation_unwrap_and_only_rz_changes(self):
        m, f = self.make(aspect=1., rotation=True)
        self.start(f)
        self.sample(f, angle=179)
        base = f.target[5]
        self.sample(f, 1, angle=-179)
        self.assertAlmostEqual(f.target[5], base + 2)
        self.sample(f, 2, angle=-90)
        self.assertAlmostEqual(f.target[5], base + 45)
        self.assertEqual(f.target[3:5], f.actual[3:5])

    def test_motion_at_20_and_50hz_fk_bounds_and_speed(self):
        for hz in (20, 50):
            model, f = self.make()
            self.start(f)
            initial = list(f.q)
            # 正确：使用当前300mm/s和模型关节速度上限，保留到达断言。
            corner = f.snapshot()["corners"][1]
            endpoint = [corner[j]-f.base_offset[j] for j in range(3)]+f.actual[3:]
            solved, ep, er, ok = model.ik(f.q, endpoint, iters=45, restarts=False)
            self.assertTrue(ok, (ep, er))
            self.assertTrue(f._body_inside(solved, f.base_offset),
                            ('端点整机不得穿过Z=0地面', f.snapshot()))
            local_start = model.pose_vec(f.q)
            previous, joint_seconds = list(f.q), 0.
            for part in range(1, 41):
                along = [a+(b-a)*part/40 for a,b in zip(local_start,endpoint)]
                q, ep, er, ok = model.ik(previous,along,iters=45,restarts=False)
                self.assertTrue(ok,(ep,er))
                joint_seconds += max(abs(a-b)/(model.vmax[j]*.3)
                                     for j,(a,b) in enumerate(zip(previous,q)))
                previous = q
            cart_seconds = math.dist(f.actual[:3], corner)/(300*.3)
            budget = cart_seconds+joint_seconds+2
            print(f"CORNER_DEADLINE hz={hz} cart_s={cart_seconds:.4f} joint_s={joint_seconds:.4f} budget_s={budget:.4f}",flush=True)
            for i in range(math.ceil(hz * budget)):
                stamp, dt = 10 + i/hz, 1/hz
                self.sample(f, i, now=stamp, u=1., v=0.)
                previous_q, previous_actual = list(f.q), list(f.actual)
                f.tick(dt, now=stamp, speed_pct=30)
                self.check_state(model, f)
                self.assertLessEqual(math.dist(previous_actual[:3], f.actual[:3]), 300*.3*dt+1e-7)
                for axis, (a,b) in enumerate(zip(previous_q, f.q)):
                    self.assertLessEqual(abs(a-b), model.vmax[axis]*.3*dt+1e-7)
            self.assertGreater(math.dist(initial, f.q), .1)
            self.assertLess(math.dist(f.target[:3], f.actual[:3]), 2., f.snapshot())

    def test_ik_rate_and_iterations_bounded(self):
        model, f = self.make()
        self.start(f)
        from backend.core import hand_follow as implementation
        original, calls = implementation.solve_position_step, []
        stamp = 10.
        def counted(*a, **kw):
            calls.append((stamp, kw))
            return original(*a, **kw)
        with patch.object(implementation,'solve_position_step',side_effect=counted):
            for i in range(50):
                stamp = 10+i*.02
                self.sample(f, i, now=stamp, u=.9, v=.1)
                f.tick(.02, now=stamp)
        # Shared position solver: at most one bounded decision per interval.
        stamps = sorted(set(t for t, _ in calls))
        self.assertGreater(len(calls), 0)
        self.assertLessEqual(len(stamps), 25)
        self.assertTrue(all(b-a >= HandFollow.IK_INTERVAL-1e-9 for a,b in zip(stamps, stamps[1:])))
        self.assertTrue(all(sum(t == s for t,_ in calls) == 1 for s in stamps))
        self.assertTrue(all(c['max_joint_step']<=8 and c['max_cartesian_step']<=24 and math.isfinite(c['deadline']) for _,c in calls))

    def test_unreachable_solver_keeps_last_valid_q(self):
        model, f = self.make()
        self.start(f)
        self.sample(f, u=1., v=0.)
        old = list(f.q)
        with patch('backend.core.hand_follow.solve_position_step',return_value={
                'q':None,'position_error_mm':100.,'progress_mm':0.,'cause':'stalled','iterations':18}):
            self.assertFalse(f.tick(.02, now=10.))
        self.assertEqual(old, f.q)
        self.check_state(model, f)

    def test_fk_outside_candidate_is_never_committed(self):
        model, f = self.make()
        self.start(f)
        self.sample(f, u=1.)
        old = list(f.q)
        original = model.pose_vec
        model.ik = lambda *a, **kw: ([v+.1 for v in old], 0., 0., True)
        def outside(q):
            p = original(q)
            if list(q) != old:
                p[0] = f._hi[0] + 1
            return p
        model.pose_vec = outside
        self.assertFalse(f.tick(.02, now=10.))
        self.assertEqual(f.q, old)

    def test_stop_and_snapshots_are_isolated(self):
        _, f = self.make()
        self.start(f)
        old = f.token
        s = f.snapshot()
        s["q"][0] = 999
        s["bounds"]["min"][0] = 999
        self.assertNotEqual(s["q"], f.q)
        self.assertNotEqual(s["bounds"]["min"], f._lo)
        f.stop()
        self.assertFalse(f.active)
        self.assertNotEqual(old, f.token)
        self.assertFalse(f.tick(.02, now=10.))

    def test_random_targets_remain_in_closed_box(self):
        model, f = self.make()
        self.start(f)
        rng = random.Random(42)
        for i in range(120):
            now = 10+i*.02
            self.sample(f, i, now=now, u=rng.random(), v=rng.random())
            f.tick(.02, now=now)
            self.check_state(model, f)

    def test_bad_tick_parameters_and_clock_reversal_stop(self):
        for dt, speed, now in ((True, 100, 10.), (.02, True, 10.),
                               (.02, 101, 10.), (.02, 100, 9.), (float("nan"), 100, 10.)):
            _, f = self.make()
            self.start(f)
            self.assertFalse(f.tick(dt, now=now, speed_pct=speed))
            self.assertFalse(f.active)

    def test_aspect_extremes_and_impossible_joint_limits(self):
        for aspect in (.5, 3.):
            m, f = self.make(aspect=aspect)
            self.check_state(m, f)
            s = f.snapshot()
            self.assertAlmostEqual((s["bounds"]["max"][0]-s["bounds"]["min"][0]) /
                                   (s["bounds"]["max"][2]-s["bounds"]["min"][2]), aspect)
        self.assertFalse(f.configure(self.configuration(aspect=1.),
                                     lower=[-.01]*6, upper=[.01]*6, now=10.)["ok"])
        self.assertFalse(f.active)
        self.assertFalse(f.configured)

    def test_depth_and_rotation_together_fk_safe_and_reachable(self):
        m, f = self.make(depth=True, rotation=True)
        self.start(f)
        for i in range(300):
            now = 10+i*.02
            self.sample(f, i, now=now, u=.75, v=.25,
                        palm_scale=.2 if i < 3 else .22, angle=0. if i < 3 else 30.)
            f.tick(.02, now=now)
            self.check_state(m, f)
        self.assertLess(math.dist(f.actual[:3], f.target[:3]), 1., f.snapshot())
        self.assertLess(abs((f.actual[5]-f.target[5]+180)%360-180), .2)

    def test_long_frame_cannot_replay_a_large_step(self):
        m, f = self.make()
        self.start(f)
        self.sample(f, u=1., v=0.)
        q, p = list(f.q), list(f.actual)
        f.tick(1., now=10.)
        self.check_state(m, f)
        # 正确：墙钟长帧最多补算0.1s，不得重放整秒的位移。
        self.assertLessEqual(math.dist(p[:3], f.actual[:3]), 300*.1+1e-7)
        for axis, (a,b) in enumerate(zip(q,f.q)):
            self.assertLessEqual(abs(a-b), m.vmax[axis]*.1+1e-7)

    def test_nonlinear_fk_speed_correction_does_not_repeatedly_halve_steps(self):
        m, f = self.make()
        self.start(f)
        initial = list(f.actual)
        for i in range(50):
            previous = list(f.actual)
            self.sample(f, i, now=10+i*.02, u=1., v=0.)
            f.tick(.02, now=10+i*.02, speed_pct=30)
            self.check_state(m,f)
            self.assertLessEqual(math.dist(previous[:3],f.actual[:3]), 300*.3*.02+1e-7)
        # 正确：在有阻尼及关节限速的现行控制下保留最低进展要求。
        self.assertGreater(math.dist(initial[:3],f.actual[:3]), 20.)

    def test_zero_speed_and_zero_dt_do_not_move(self):
        _, f = self.make()
        self.start(f)
        self.sample(f, u=1., v=0.)
        q = list(f.q)
        self.assertFalse(f.tick(.02, now=10., speed_pct=0))
        self.assertFalse(f.tick(0., now=10.))
        self.assertEqual(f.q, q)
        self.assertTrue(f.active)

    def test_depth_bends_joints_with_fixed_base_and_loss_returns_center(self):
        for hz in (20, 50):
            m, f = self.make(depth=True, depth_range=[.1, .3])
            self.start(f)
            q0 = list(f.q)
            for i in range(3):
                self.sample(f, i, now=10+i/hz)
                f.tick(1/hz, now=10+i/hz)
            seq, stamp = 3, 10+3/hz
            for scale, face in ((.3, 'min'), (.1, 'max')):
                for _ in range(hz*4):
                    previous = list(f.actual)
                    self.sample(f, seq, now=stamp, palm_scale=scale)
                    f.tick(1/hz, now=stamp, speed_pct=30)
                    self.check_state(m, f)
                    self.assertLessEqual(math.dist(previous[:3], f.actual[:3]), 300*.3/hz+1e-7)
                    self.assertEqual(f.base_offset, [0., 0., 0.])
                    stamp += 1/hz
                    seq += 1
                self.assertEqual(f.target[1], f.snapshot()['bounds'][face][1])
                self.assertLess(math.dist(f.actual[:3], f.target[:3]), 1.)
                self.assertGreater(math.dist(f.q, q0), .1)
            token = f.token
            f.tick(.02, now=stamp+.6)
            self.assertTrue(f.active)
            self.assertTrue(f.returning)
            self.assertEqual(f.token, token)
            for i in range(hz*4):
                f.tick(1/hz, now=stamp+.7+i/hz)
                self.check_state(m, f)
            self.assertLess(math.dist(f.actual[:3], f._base[:3]), 1.)
            self.check_state(m, f)

    def test_mixed_xz_depth_rotation_shares_world_speed_budget(self):
        m, f = self.make(depth=True, rotation=True)
        self.start(f)
        q0 = list(f.q)
        initial = m.pose_vec(q0)
        b = f.snapshot()["bounds"]
        final = [b["min"][0]+.8*(b["max"][0]-b["min"][0]), initial[1],
                 b["max"][2]-.2*(b["max"][2]-b["min"][2])-HandFollow.BASE_Z,
                 initial[3], initial[4], initial[5]+20.]
        # Independently solve a continuous task-space path. Bound test time by
        # both Cartesian travel and total rate-limited joint travel, plus 2 s
        # settling. Adding the budgets is conservative (axes can move together),
        # but never assumes that TCP distance alone determines arrival time.
        previous, joint_seconds = list(q0), 0.
        for part in range(1, 41):
            target = [a+(z-a)*part/40 for a,z in zip(initial,final)]
            q, ep, er, ok = m.ik(previous, target, iters=45, restarts=False)
            self.assertTrue(ok, (ep, er))
            joint_seconds += max(abs(a-z)/(m.vmax[j]*.5)
                                 for j,(a,z) in enumerate(zip(previous,q)))
            previous = q
        world_final = list(final[:3])
        world_final[1] -= 100*math.log(.28/.2)
        world_final[2] += HandFollow.BASE_Z
        cart_seconds = math.dist(f.actual[:3],world_final)/(300*.5)
        budget = cart_seconds+joint_seconds+2.
        print(f"MIXED_DEADLINE cart_s={cart_seconds:.4f} joint_s={joint_seconds:.4f} budget_s={budget:.4f}",flush=True)
        for i in range(math.ceil(budget/.02)):
            stamp = 10+i*.02
            previous = list(f.actual)
            self.sample(f, i, now=stamp, u=.5 if i < 3 else .8,
                        v=.5 if i < 3 else .2, palm_scale=.2 if i < 3 else .28,
                        angle=0. if i < 3 else 20.)
            f.tick(.02, now=stamp, speed_pct=50)
            self.check_state(m, f)
            self.assertLessEqual(math.dist(previous[:3], f.actual[:3]), 300*.5*.02+1e-7)
        self.assertGreater(math.dist(q0, f.q), .1)
        self.assertEqual(f.base_offset, [0., 0., 0.])
        self.assertLess(f.actual[1], initial[1]-10.)
        self.assertLess(math.dist(f.actual[:3], f.target[:3]), 1., f.snapshot())
        self.assertLess(abs((f.actual[5]-f.target[5]+180)%360-180), .2)
        held = f.snapshot()
        f.stop()
        self.assertFalse(f.tick(.02, now=10+budget+.02))
        self.assertEqual(f.base_offset, held["base_offset"])

    def test_disabled_depth_ignores_near_far_and_snapshot_offset_is_owned(self):
        m, f = self.make()
        self.start(f)
        q0 = list(f.q)
        for i in range(100):
            stamp = 10+i*.02
            self.sample(f, i, now=stamp, palm_scale=.8 if i % 2 else .01)
            f.tick(.02, now=stamp)
            self.check_state(m, f)
            self.assertEqual(f.base_offset, [0., 0., HandFollow.BASE_Z])
            self.assertEqual(f.q, q0)
        s = f.snapshot()
        s["base_offset"][1] = 10000.
        self.assertEqual(f.base_offset[1], 0.)

    def test_outer_envelope_is_fixed_aspect_matched_and_snapshot_owned(self):
        m, f = self.make(depth=True, rotation=True)
        s = f.snapshot()
        box = s["outer_bounds"]
        self.assertEqual(box, s["auto_outer_bounds"])
        self.assertAlmostEqual((box["max"][0]-box["min"][0]) /
                               (box["max"][2]-box["min"][2]), s["aspect"])
        reference = robot_bounds(m, s["reference_q"], s["base_offset"])
        self.assertEqual(reference, s["reference_body_bounds"])
        self.assertGreaterEqual(reference["min"][1] - box["min"][1], 180.)
        for i in range(3):
            self.assertAlmostEqual((box["min"][i]+box["max"][i])/2,
                                   (reference["min"][i]+reference["max"][i])/2, delta=1e-7)
        reference_tcp = m.pose_vec(s["reference_q"])
        reference_tcp[2] += HandFollow.BASE_Z
        self.assertGreater(math.dist(reference_tcp[:3], s["actual"][:3]), 20.)
        self.assertEqual(box, make_enclosure(reference, s['aspect'], margin=80.))
        for i, (x, z) in enumerate(((0, 1), (1, 1), (1, 0), (0, 0))):
            self.assertEqual(s["outer_corners"][i][0], box["max" if x else "min"][0])
            self.assertEqual(s["outer_corners"][i][2], box["max" if z else "min"][2])
        self.start(f)
        for i in range(30):
            self.sample(f, i, now=10+i*.02, u=1., v=0.)
            f.tick(.02, now=10+i*.02)
            self.check_state(m, f)
            self.assertEqual(f.snapshot()["outer_bounds"], box)
        s["outer_bounds"]["min"][0] = -1e6
        s["auto_outer_bounds"]["max"][0] = 1e6
        self.assertNotEqual(f.outer_bounds, s["outer_bounds"])
        self.assertNotEqual(f.auto_outer_bounds, s["auto_outer_bounds"])

    def test_enclosure_schema_rejects_invalid_but_allows_elevated_tcp_workspace(self):
        m, f = self.make()
        body = robot_bounds(m, f.q, f.base_offset)
        good = f.snapshot()['bounds']
        payload = dict(session="a", aspect=16/9, depth=False, rotation=False)
        invalid = [None, {}, {"min": [0]*3, "max": [0]*3},
                   {"min": [0]*3, "max": [5001]*3},
                   {"min": [True, 0, 0], "max": [1]*3},
                   {"min": [float("nan"), 0, 0], "max": [1]*3},
                   {"min": [0, 0], "max": [1]*3}, dict(good, extra=1),
                   {'min': list(good['max']), 'max': list(good['min'])}]
        for box in invalid:
            r = f.configure(dict(payload, enclosure=box), now=10.)
            self.assertFalse(r["ok"], box)
            self.assertFalse(f.active)
            self.assertFalse(f.configured)
        self.assertFalse(f.configure(dict(payload, unknown=1), now=10.)["ok"])
        # 正确：抬高的是TCP工作区，不是机器人底座，不能沿用整机外盒限制。
        self.assertTrue(f.configure(dict(payload, enclosure=good), now=10.)['ok'])
        self.assertEqual(f.snapshot()['bounds'], good)
        self.assertEqual(f.base_offset, [0., 0., 0.])
        self.assertLess(body['min'][2], good['min'][2])
        self.check_state(m, f)

    def test_manual_enclosure_limits_tcp_depth_with_fixed_base_and_restores_default(self):
        m, f = self.make(depth=True)
        box = f.snapshot()['bounds']
        box['min'][1], box['max'][1] = -420., -380.
        payload = self.configuration(depth=True)
        self.assertTrue(f.configure(dict(payload, enclosure=box), now=10.)["ok"])
        self.assertEqual(f.snapshot()['bounds'], box)
        self.assertNotEqual(f.snapshot()["auto_outer_bounds"], box)
        self.start(f)
        q0 = list(f.q)
        for i in range(150):
            self.sample(f, i, now=10+i*.02, palm_scale=.2 if i < 3 else .8)
            f.tick(.02, now=10+i*.02)
            self.check_state(m, f)
            self.assertEqual(f.base_offset, [0., 0., 0.])
            self.assertEqual(f.snapshot()['bounds'], box)
        self.assertGreater(math.dist(f.q, q0), .1)
        self.assertLess(math.dist(f.actual[:3], f.target[:3]), 1.)
        self.assertEqual(f.target[1], f.snapshot()["bounds"]["min"][1])
        f.stop()
        self.assertTrue(f.configure(payload, now=14.)["ok"])
        self.assertEqual(f.snapshot()['bounds'], payload['enclosure'])
        self.assertEqual(f.snapshot()["outer_bounds"], f.snapshot()["auto_outer_bounds"])
        self.assertEqual(f.base_offset, [0., 0., HandFollow.BASE_Z])

    def test_manual_tcp_center_must_be_reached_or_configuration_refused(self):
        m, f = self.make()
        box = f.snapshot()['bounds']
        for key in ("min", "max"):
            # 正确：固定J6=0并保持工具正向时，不假定任意X偏移仍可达。
            # 使用独立Y/Z调整构造可达中心，另以远超臂展的中心验证拒绝。
            box[key][1] += 5.
            box[key][2] += 5.
        r = f.configure(dict(session="camera-1", aspect=16/9, depth=False, rotation=False,
                             enclosure=box), now=10.)
        self.assertTrue(r["ok"], r.get("why"))
        self.start(f)
        for axis in range(3):
            self.assertAlmostEqual(f.actual[axis], (box["min"][axis]+box["max"][axis])/2, delta=.01)
        self.check_state(m, f)
        unreachable = {key: [v+10000 for v in box[key]] for key in ("min", "max")}
        r = f.configure(dict(session="camera-1", aspect=16/9, depth=False, rotation=False,
                             enclosure=unreachable), now=11.)
        self.assertFalse(r["ok"])
        self.assertFalse(f.active)
        self.assertIn('无法到达', r['why'])

    def test_missing_geometry_fails_closed_during_setup_and_tick(self):
        m, f = self.make()
        self.start(f)
        self.sample(f, u=1., v=0.)
        old = list(f.q)
        del m.links["elfin_base"]["visual"]["bbox"]
        # 正确：生产World在每步提交前调用几何门禁；异常必须拒绝候选。
        self.assertFalse(f.tick(.02, now=10., validate_step=lambda _before, q:
                                f._body_inside(q, [0., 0., 0.])))
        self.assertTrue(f.active)
        self.assertIn('异常', f.blocked_reason)
        self.assertEqual(f.q, old)
        self.assertFalse(f.configure(dict(session="a", aspect=1., depth=False, rotation=False), now=11.)["ok"])


def probe_box_width():
    """Compare exact production code with a one-literal change in this copy only."""
    from backend.core import hand_follow
    source = inspect.getsource(HandFollow)
    assert source.count("width = 400 * .72 ** attempt") == 1
    namespace = dict(vars(hand_follow))
    exec(source.replace("width = 400 * .72 ** attempt", "width = 100 * .72 ** attempt"), namespace)
    baseline = namespace["HandFollow"]
    results = []
    for aspect in (.5, 1., 4/3, 16/9, 3.):
        for depth in (False, True):
            for rotation in (False, True):
                row = dict(aspect=aspect, depth=depth, rotation=rotation)
                for width, cls in ((100, baseline), (400, HandFollow)):
                    m = RobotModel("elfin5")
                    f = cls(m)
                    begin = time.perf_counter()
                    result = f.configure(dict(session="probe", aspect=aspect,
                                              depth=depth, rotation=rotation), now=10.)
                    elapsed = time.perf_counter() - begin
                    state = result["state"]
                    dimensions = [state["bounds"]["max"][i] - state["bounds"]["min"][i] for i in range(3)]
                    row[str(width)] = dict(ok=result["ok"], xyz_mm=dimensions,
                                           elapsed_s=elapsed, q=state["q"],
                                           validated_samples=(27 if rotation else 9) if result["ok"] else 0)
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    print("BOX_FEASIBILITY_JSON=" + json.dumps(results, ensure_ascii=False), flush=True)


def probe_center_seeds():
    for q in ([0,0,120,30,-60,0], [0,20,110,45,-65,0],
              [0,30,100,30,-70,0], [0,45,100,30,-70,0], [0,-10,130,30,-70,0]):
        m = RobotModel("elfin5", tcp_translation_mm=[0,0,40], tcp_rpy_deg=[0,0,180])
        f = HandFollow(m)
        p = m.pose_vec(q)
        p[2] += HandFollow.BASE_Z
        b = robot_bounds(m, q, [0,0,HandFollow.BASE_Z])
        print(json.dumps({"q":q,"tcp":[round(v,2) for v in p[:3]],
                          "body":[[round(v,2) for v in b[k]] for k in ("min","max")],
                          "nonsingular":f._nonsingular(q)}), flush=True)


def diagnose_mixed_settling():
    """Reproduce the previous failure without modifying production or assertions."""
    from backend.core import hand_follow
    source = inspect.getsource(HandFollow)
    marker = "seeds = ([0, -25, 65, 30, -65, 0]"
    assert source.count(marker) == 1
    namespace = dict(vars(hand_follow))
    exec(source.replace(marker, "seeds = ([0, -20, 120, 30, -70, 0]"), namespace)
    for name, cls in (("current_90deg", HandFollow), ("alternative_0deg", namespace["HandFollow"])):
        m = RobotModel("elfin5")
        f = cls(m)
        assert f.configure(dict(session="diag", aspect=16/9, depth=True, rotation=True), now=10.)["ok"]
        assert f.start(dict(session="diag", token=f.token), now=10.)["ok"]
        rejected = [0]
        original_guard = f._body_inside
        def guard(q, offset):
            ok = original_guard(q, offset)
            rejected[0] += int(not ok)
            return ok
        f._body_inside = guard
        samples = []
        for i in range(600):
            stamp = 10+i*.02
            assert f.sample(dict(token=f.token, session="diag", seq=i, age_ms=0.,
                                 u=.5 if i < 3 else .8, v=.5 if i < 3 else .2,
                                 palm_scale=.2 if i < 3 else .28, angle=0. if i < 3 else 20.), now=stamp)["ok"]
            f.tick(.02, now=stamp, speed_pct=50)
            if i in (399, 599):
                samples.append(dict(seconds=(i+1)*.02,
                                    residual_mm=math.dist(f.actual[:3], f.target[:3]),
                                    body_rejections=rejected[0], status=f.status, why=f.why))
        print(json.dumps(dict(trajectory=name, samples=samples)), flush=True)


def probe_reference_centers():
    # Fixed, existing candidates only; no random seeds or enlarged boxes.
    for tcp in (0, 40):
        m = RobotModel("elfin5", tcp_translation_mm=[0,0,tcp],
                       tcp_rpy_deg=[0,0,180] if tcp else [0,0,0])
        for seed in ([90,-20,120,30,-70,0], [0,-10,130,30,-70,0],
                     [0,-30,-90,30,60,0], [25,-35,75,25,-55,10], [0,-25,65,30,-65,0]):
            body = robot_bounds(m, seed, [0,0,HandFollow.BASE_Z])
            box = make_enclosure(body,16/9)
            center = [(a+b)/2 for a,b in zip(box["min"],box["max"])]
            p = m.pose_vec(seed)
            target = [center[i]-(HandFollow.BASE_Z if i == 2 else 0) for i in range(3)]+p[3:]
            begin = time.perf_counter()
            q, ep, er, ok = m.ik(seed,target,iters=80,restarts=False,tol_mm=1e-4,tol_deg=1e-4)
            solved_body = robot_bounds(m,q,[0,0,HandFollow.BASE_Z])
            row = dict(tcp=tcp, seed=seed, ok=ok, error_mm=ep,
                       fit=contains_bounds(solved_body,box,40), floor_mm=solved_body["min"][2],
                       center=[round(v,2) for v in center],
                       dimensions=[round(b-a,2) for a,b in zip(box["min"],box["max"])],
                       seconds=round(time.perf_counter()-begin,4))
            print("REFERENCE_CENTER "+json.dumps(row),flush=True)


def probe_compact_centers():
    m = RobotModel("elfin5", tcp_translation_mm=[0,0,40], tcp_rpy_deg=[0,0,180])
    for reference in ([0,-10,130,30,-70,0], [0,-20,120,30,-70,0], [0,-25,65,30,-65,0]):
        body = robot_bounds(m,reference,[0,0,HandFollow.BASE_Z])
        box = make_enclosure(body,16/9)
        center = [(a+b)/2 for a,b in zip(box["min"],box["max"])]
        for label, rpy in (("nominal",m.pose_vec(reference)[3:]), ("tool_down",[180.,0.,180.]), ("tool_up",[0.,0.,180.])):
            begin=time.perf_counter()
            q, ep, er, ok = m.ik(reference, [center[0],center[1],center[2]-HandFollow.BASE_Z]+rpy,
                                 iters=60,restarts=False,tol_mm=1e-4,tol_deg=1e-4)
            solved=robot_bounds(m,q,[0,0,HandFollow.BASE_Z])
            print("COMPACT_CENTER "+json.dumps(dict(reference=reference, orientation=label,
                  ok=ok, fit=contains_bounds(solved,box,40), error_mm=round(ep,5),
                  body_xyz=[round(b-a,2) for a,b in zip(solved["min"],solved["max"])],
                  box_xyz=[round(b-a,2) for a,b in zip(box["min"],box["max"])],
                  seconds=round(time.perf_counter()-begin,4))),flush=True)


def profile_configuration():
    for tcp in (0,40):
        for rotation in (False,True):
            m=RobotModel("elfin5",tcp_translation_mm=[0,0,tcp],tcp_rpy_deg=[0,0,180] if tcp else [0,0,0])
            f=HandFollow(m)
            started=time.perf_counter()
            r=f.configure(dict(session="profile",aspect=16/9,depth=True,rotation=rotation),now=10.)
            s=r["state"]
            body=robot_bounds(m,s["q"],s["base_offset"])
            print(json.dumps(dict(tcp=tcp,rotation=rotation,ok=r["ok"],seconds=round(time.perf_counter()-started,3),
                  inner_width=round(s["bounds"]["max"][0]-s["bounds"]["min"][0],2),
                  outer_xyz=[round(b-a,2) for a,b in zip(s["outer_bounds"]["min"],s["outer_bounds"]["max"])],
                  center_xyz=[round(v,2) for v in s["actual"][:3]], floor=round(body["min"][2],2),
                  body_y=round(body["max"][1]-body["min"][1],2))),flush=True)
            if tcp == 40 and rotation and not r["ok"]:
                from backend.core import hand_follow
                source=inspect.getsource(HandFollow)
                namespace=dict(vars(hand_follow))
                exec(source.replace("for attempt in range(7):", "for attempt in range(10):"),namespace)
                narrower=namespace["HandFollow"](m)
                started=time.perf_counter()
                result=narrower.configure(dict(session="narrow",aspect=16/9,depth=True,rotation=True),now=10.)
                state=result["state"]
                print(json.dumps(dict(narrower10=True,ok=result["ok"],seconds=round(time.perf_counter()-started,3),
                      width=state["bounds"]["max"][0]-state["bounds"]["min"][0])),flush=True)


if __name__ == "__main__":
    if os.environ.get("HUYAN_HAND_FOLLOW_CONFIGURATION") == "1":
        profile_configuration()
    elif os.environ.get("HUYAN_HAND_FOLLOW_COMPACT") == "1":
        probe_compact_centers()
    elif os.environ.get("HUYAN_HAND_FOLLOW_REFERENCE") == "1":
        probe_reference_centers()
    elif os.environ.get("HUYAN_HAND_FOLLOW_SETTLING") == "1":
        diagnose_mixed_settling()
    elif os.environ.get("HUYAN_HAND_FOLLOW_SEEDS") == "1":
        probe_center_seeds()
    elif os.environ.get("HUYAN_HAND_FOLLOW_PROBE") == "1":
        probe_box_width()
    else:
        unittest.main(verbosity=2, defaultTest=os.environ.get("HUYAN_HAND_FOLLOW_TEST"))
