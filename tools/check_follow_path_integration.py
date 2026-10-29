"""检查真实 World 调用链；所有运动、对象和风险输入均为内存假件。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import time
import unittest
from unittest.mock import patch
from check_real_jog_world import fixture, WORLD_GLOBALS


class Follow:
    active = True
    BASE_Z = 0.
    def __init__(self, candidates):
        self.q = [0.] * 6
        self.candidates = candidates
        self.blocked_reason = ""
    def snapshot(self):
        return {"active": self.active}
    def wait(self, reason):
        self.blocked_reason = reason
    def tick(self, dt, now=None, speed_pct=100, *, validate_step=None):
        changed = False
        for value in self.candidates:
            candidate = [value] + [0.] * 5
            if validate_step is not None and not validate_step(self.q, candidate):
                break
            self.q = candidate
            changed = True
        return changed


class FollowPathTests(unittest.TestCase):
    def test_safe_endpoints_cannot_hide_intermediate_object_or_floor_collision(self):
        for kind in ("object", "ground"):
            for candidates in ([.25, .5, .75, 1.], [1.]):
                with self.subTest(kind=kind, candidates=candidates):
                    world = fixture()
                    world.settings["mode"], world.real = "sim", None
                    world._hand_camera_reader = None
                    world._hand_cfg_rev = world._cfg_rev
                    world.hand_follow = Follow(candidates)
                    checked = []
                    def risk(model, q, *args, **kwargs):
                        checked.append(q[0])
                        collision = .4 < q[0] < .6
                        return {"level": "alarm" if collision else "green", "joint_level": "green",
                                "collisions": [{"type": kind, "link": "tool"}] if collision else [],
                                "reasons": ["中途碰撞"] if collision else []}
                    with patch.dict(WORLD_GLOBALS, evaluate=risk):
                        world._tick_hand_follow(.1, time.monotonic())
                    self.assertTrue(any(.4 < value < .6 for value in checked))
                    self.assertLess(world.sim.q[0], .4)
                    self.assertIn("中途碰撞", world.hand_follow.blocked_reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
