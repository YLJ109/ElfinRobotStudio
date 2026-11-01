"""示教逆解回归：只在隔离副本计算，不构造真实连接、不发送运动。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import sys
import random
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _app_test_support import application_for
from backend.core.kinematics import RobotModel
from backend.core import config
from backend.core.world import World
from backend.schemas import IK
from pydantic import ValidationError

TARGET = [570.8209, 4.339706, 966.0592, 90, 0, -33]
LOWER, UPPER = map(list, zip(*config.MANUAL_LIMITS))


def model():
    return RobotModel('elfin5', tcp_translation_mm=[0, 0, -15], tcp_rpy_deg=[0, 0, 90])


def preview_world(m):
    # 正确：只调用真实计算函数，禁止创建存储、相机或硬件连接。
    return SimpleNamespace(settings={'mode': 'sim'}, real=None,
        joints_known=lambda: True, current_joints=lambda: [0.]*6,
        limits=lambda: (LOWER, UPPER), motion_model=lambda: m,
        precheck=lambda *a, **kw: None, log=SimpleNamespace(add=Mock()))


class TeachingIKTests(unittest.TestCase):
    def test_screenshot_position_reachable_with_explicit_free_orientation(self):
        m = model()
        result = World.ik_pose(preview_world(m), TARGET, position_only=True)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['orientation_constrained'])
        self.assertLess(max(abs(a-b) for a, b in zip(result['achieved'][:3], TARGET[:3])), .05)
        self.assertGreater(result['requested_rot_err_deg'], .05)
        self.assertTrue(all(lo <= v <= hi for lo, v, hi in zip(LOWER, result['joints'], UPPER)))
        print('截图位置自动姿态：', result['joints'], result['achieved'], flush=True)

    def test_fixed_orientation_does_not_publish_inaccurate_screenshot_result(self):
        result = World.ik_pose(preview_world(model()), TARGET)
        self.assertFalse(result['ok'])
        self.assertNotIn('joints', result)
        self.assertIn('完整位姿', result['why'])
        self.assertEqual(result['reason_code'], 'unreachable_pose')
        self.assertGreater(result['gap_mm'], result['tolerance_mm'])

    def test_known_reachable_full_poses_from_singular_zero(self):
        m, rng = model(), random.Random(27)
        for index in range(68):
            known = [rng.uniform(lo*.95, hi*.95) for lo, hi in zip(LOWER, UPPER)]
            target = m.pose_vec(known)
            with self.subTest(index=index):
                q, p, r, ok = m.ik([0.]*6, target, LOWER, UPPER, iters=100)
                self.assertTrue(ok, (known, p, r))
                error = m._err(q, target)
                self.assertLess(max(abs(v) for v in error[:3]), .05)
                self.assertLess(max(abs(v) for v in error[3:]), .05)

    def test_tool_tcp_offset_is_included_in_conservative_reach_bound(self):
        m = RobotModel('elfin5', tcp_translation_mm=[0, 0, 500])
        target = m.pose_vec([0.]*6)
        self.assertTrue(m.ik([0.]*6, target, LOWER, UPPER)[3])
        result = m.ik([0.]*6, [5000, 0, 5000, 0, 0, 0], LOWER, UPPER, position_only=True)
        self.assertFalse(result[3])

    def test_free_orientation_cannot_be_used_as_implicit_real_or_partial_mode(self):
        w = preview_world(model())
        for mode, real in [('real', None), ('sim', object())]:
            w.settings['mode'], w.real = mode, real
            self.assertFalse(World.ik_pose(w, TARGET, position_only=True)['ok'])
        w.settings['mode'], w.real = 'sim', None
        self.assertFalse(World.ik_pose(w, TARGET, allow_partial=True, position_only=True)['ok'])
        for value in ('true', 'false', 0, 1, None):
            with self.assertRaises(ValidationError):
                IK(target=TARGET, position_only=value)
        self.assertFalse(IK(target=TARGET).position_only)

    def test_api_explicit_position_option_and_default_full_pose(self):
        from fastapi.testclient import TestClient
        import backend.main as main
        stub = SimpleNamespace(ik_pose=Mock(return_value={'ok': False, 'why': '离线接口桩'}))
        with application_for(world=stub) as test_app:
            client = TestClient(test_app, base_url='http://127.0.0.1:8710')
            for position_only in (False, True):
                response = client.post('/api/ik', json={'target': TARGET, 'position_only': position_only})
                self.assertEqual(response.status_code, 200)
                args, kwargs = stub.ik_pose.call_args
                self.assertEqual(args, (TARGET, None, False))
                self.assertIs(kwargs['position_only'], position_only)
                self.assertEqual(kwargs['budget_s'], .8)
                self.assertTrue(callable(kwargs['cancelled']))


if __name__ == '__main__':
    unittest.main()
