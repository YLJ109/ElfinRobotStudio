"""原始帧时间和网络隔离契约；不打开设备，不连接运行中的服务。

仅通过 tools/run_offline_checks.py 执行。
HTTP 使用内存 ASGI TestClient，不启用应用 lifespan 或 World 后台循环。
"""
from _offline_guard import require_offline_copy
require_offline_copy()

from pathlib import Path
from collections import deque
import asyncio
import os
import sys
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from backend.core.cameras import CameraManager
from backend.core.hand_follow import HandFollow
from backend.core.sim_tool import DEFAULT_TOOL, model_for
from backend.core.world import World
import backend.main as main
from _app_test_support import application_for
import check_hand_follow as contract


class FrameContractTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ['HUYAN_NO_REAL'], '1')
        self.world = World()
        self.follow = HandFollow(model_for('elfin5', DEFAULT_TOOL))
        self.assertTrue(self.follow.configure(contract.configuration())['ok'])
        self.world.sim.q = list(self.follow.q)
        self.world.hand_follow = self.follow
        self.world._hand_cfg_rev = self.world._cfg_rev
        self.world._hand_camera_key = (contract.SESSION, 640, 480)
        reply = self.follow.start({'session': contract.SESSION, 'token': self.follow.token})
        self.assertTrue(reply['ok'])
        self.manager = CameraManager()
        self.manager._active = contract.camera()['active']
        self.manager._frame_history = {'color': deque(maxlen=512)}
        self.application = application_for(world=self.world,cameras=self.manager,camera_state=contract.camera)
        self.test_app = self.application.__enter__()
        self.addCleanup(self.application.__exit__,None,None,None)
        self.client = TestClient(self.test_app, base_url='http://127.0.0.1:8710')
        # 正确：除运行器的外网 tripwire，再封锁所有应用层连接和设备入口。
        for context in (
            patch('asyncio.open_connection', side_effect=AssertionError('禁止网络连接')),
            patch.object(CameraManager, 'open', side_effect=AssertionError('禁止打开相机')),
            patch.object(CameraManager, 'scan', side_effect=AssertionError('禁止扫描相机')),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.addCleanup(self.client.close)

    def frame(self, sequence=123, age=.02, owner=contract.SESSION):
        captured = time.monotonic()-age
        self.manager._frame_history['color'].append((owner, sequence, captured))
        return captured

    def post(self, **changes):
        payload = contract.observation(self.follow.token, frame_seq=123)
        payload.update(changes)
        return self.client.post('/api/sim/hand-follow/sample', json=payload)

    def test_real_camera_stamp_reaches_algorithm_unchanged_and_overrides_client_age(self):
        captured = self.frame(age=.05)
        with patch.object(self.follow, 'sample', wraps=self.follow.sample) as sampled:
            response = self.post(age_ms=0)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['ok'], response.json())
        self.assertEqual(sampled.call_args.kwargs['captured_at'], captured)
        self.assertGreaterEqual(sampled.call_args.args[0]['age_ms'], 49.)
        self.assertNotIn('frame_seq', sampled.call_args.args[0])
        self.assertEqual(self.follow._last_observation, captured)

    def test_unknown_sequence_session_boolean_and_future_stamp_cannot_authorize_motion(self):
        self.frame()
        before = list(self.world.sim.q)
        unchanged = self.follow.snapshot()
        for changes in ({'frame_seq': 124}, {'session': 'other-camera'},
                        {'frame_seq': True}, {'frame_seq': None}):
            with self.subTest(changes=changes):
                response = self.post(**changes)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.world.sim.q, before)
                self.assertTrue(self.follow.active)
                self.assertEqual(self.follow.snapshot(), unchanged,
                                 '拒绝的帧不能把当前目标改成回中目标')
        self.frame(age=-10.)
        self.assertEqual(self.post().status_code, 422)
        self.assertEqual(self.world.sim.q, before)
        self.assertEqual(self.follow.snapshot(), unchanged)

    def test_expired_capture_is_not_renewed_by_claiming_zero_client_age(self):
        self.frame(age=.45)
        unchanged = self.follow.snapshot()
        with patch.object(self.follow, 'sample', wraps=self.follow.sample) as sampled:
            response = self.post(age_ms=0)
        self.assertEqual(response.status_code, 422, response.text)
        sampled.assert_not_called()
        self.assertTrue(self.follow.active)
        self.assertEqual(self.follow.snapshot(), unchanged)
        # 正确：拒绝旧帧不发布动作；无有效输入仍由原看门狗进入回中。
        self.follow.tick(0., now=self.follow._last_sample+.51)
        self.assertTrue(self.follow.returning)

    def test_camera_absence_bridges_then_expires_without_relabeling_stale_capture(self):
        captured = self.frame(age=.03)
        self.assertTrue(self.post(u=.65).json()['ok'])
        accepted_target = list(self.follow.target)
        original = self.follow.sample
        def at(stamp):
            return lambda payload, **kwargs: original(payload, now=stamp, **kwargs)
        with patch.object(self.follow, 'sample', side_effect=at(captured+.35)):
            reply = self.post(seq=2, hand_present=False).json()
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['state']['input_phase'], 'holding')
        self.assertEqual(self.follow.target, self.follow.actual)
        self.assertEqual(self.follow._last_observation, captured)
        with patch.object(self.follow, 'sample', side_effect=at(captured+1.31)):
            reply = self.post(seq=3, hand_present=False).json()
        self.assertTrue(reply['ok'])
        self.assertTrue(reply['state']['returning'])

    def test_foreign_or_forged_expired_frame_cannot_retarget_running_follow(self):
        self.frame()
        self.assertTrue(self.post(u=.65).json()['ok'])
        unchanged = self.follow.snapshot()
        response = self.post(frame_seq=999, token='foreign-token', session='foreign-camera')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.follow.snapshot(), unchanged)

    def test_processing_delay_after_http_verification_still_expires_original_stamp(self):
        captured = self.frame()
        original = self.follow.sample
        def delayed(payload, **kwargs):
            # 正确：只控制算法读取时钟，不等待真实时间，不改变帧原始时间。
            return original(payload, now=captured+.45, **kwargs)
        before = (list(self.follow.q), list(self.follow.target))
        with patch.object(self.follow, 'sample', side_effect=delayed):
            response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['ok'])
        self.assertIn('过期', response.json()['why'])
        self.assertEqual((self.follow.q, self.follow.target), before)

    def test_same_camera_frame_cannot_rewind_or_replay_algorithm_sequence(self):
        self.frame()
        self.assertTrue(self.post(seq=10).json()['ok'])
        target = list(self.follow.target)
        for seq in (10, 9):
            response = self.post(seq=seq, u=.9)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['ok'])
            self.assertEqual(self.follow.target, target)

    def test_explicit_loss_needs_no_capture_but_forged_token_cannot_take_ownership(self):
        token = self.follow.token
        response = self.post(hand_present=False, frame_seq=None)
        self.assertTrue(response.json()['ok'])
        self.assertFalse(self.follow.returning)
        self.assertEqual(self.follow.input_phase, 'holding')
        self.assertEqual(self.follow.token, token)
        before = self.follow.snapshot()
        response = self.post(hand_present=False, token='forged', u=.9)
        self.assertFalse(response.json()['ok'])
        self.assertEqual(self.follow.snapshot(), before)

    def test_explicit_stop_revokes_then_stale_http_frame_cannot_restart(self):
        token = self.follow.token
        response = self.client.post('/api/sim/hand-follow/stop', json={'token': token})
        self.assertTrue(response.json()['ok'])
        self.frame()
        response = self.post(token=token)
        self.assertFalse(response.json()['ok'])
        self.assertFalse(self.follow.active)
        self.assertNotEqual(self.follow.token, token)

    def test_tripwires_really_raise_without_attempting_network_or_camera(self):
        with self.assertRaisesRegex(AssertionError, '禁止网络'):
            asyncio.run(asyncio.open_connection('192.0.2.10', 10003))
        for action in (self.manager.open, self.manager.scan):
            with self.assertRaisesRegex(AssertionError, '禁止'):
                action('offline-device')

if __name__ == '__main__':
    unittest.main(verbosity=2)
