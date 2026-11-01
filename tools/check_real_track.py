"""Offline regressions for the real-machine tracking standoff.

真机追踪复用跟随的伺服通道（StartServo/PushServoJ + 资格门），只在目标点上
多做一件事：末端停在手掌前方 standoff 毫米处，而不是压在手掌上。没有真机
也能验证的就是这一层几何：目标点必须退后、必须仍在框内、必须不退到基座里。
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.real_palm_pipeline import RealPalmTargetPipeline


class FakePipeline(RealPalmTargetPipeline):
    """只测目标点换算，不碰驱动、不碰求解。"""

    def __init__(self, workspace):
        self.workspace = dict(workspace)
        self.model = None
        self.driver = None
        self.read_context = lambda: {}
        self.validate_path = lambda a, b: True
        self.solve = None
        self.startup = None
        self.generation = 0
        self.token = 't'
        self.context = {}
        self.latest = None
        self.seq = -1
        self.frame_seq = -1
        self.worker = None
        self.reason = ''

    def _same(self):
        return True

    def _ready(self):
        return True


def workspace(standoff, center=(600.0, 200.0, 450.0), size=(400.0, 300.0, 400.0)):
    return dict(center=list(center), size=list(size), direction=0, standoff=standoff,
                low=[-100, -100, -100], high=[1200, 1200, 1200])


def target_for(pipe, u=.5, v=.5, opening=.5):
    """复算 submit() 里的目标点公式，避免依赖时钟与准入检查。"""
    w = pipe.workspace
    x, y, z = (u - .5) * w['size'][0], (.5 - opening) * w['size'][1], (.5 - v) * w['size'][2]
    for _ in range(w['direction'] // 90):
        x, y = -y, x
    target = [a + b for a, b in zip(w['center'], (x, y, z))]
    off = w.get('standoff') or 0.
    if off > 0:
        r = math.sqrt(sum(c * c for c in target))
        if r > off + 50.:
            target = [t - t / r * off for t in target]
    return target


class StandoffTests(unittest.TestCase):
    def test_zero_standoff_keeps_follow_unchanged(self):
        centre = [600.0, 200.0, 450.0]
        got = target_for(FakePipeline(workspace(0)), .5, .5, .5)
        for a, b in zip(got, centre):
            self.assertAlmostEqual(a, b, places=6)

    def test_standoff_pulls_the_tool_back_from_the_palm(self):
        for off in (60.0, 100.0, 200.0):
            got = target_for(FakePipeline(workspace(off)), .5, .5, .5)
            palm = [600.0, 200.0, 450.0]
            self.assertAlmostEqual(math.dist(got, palm), off, delta=1.0,
                                   msg=f'standoff={off} 时末端没有退到手掌后方')
            # 退的方向必须沿着"基座 → 手掌"，不能在别的轴上乱偏。
            for g, p in zip(got, palm):
                self.assertLessEqual(abs(g), abs(p) + 1e-6)

    def test_standoff_never_enters_the_base(self):
        # 手掌靠近基座时，退后量要收敛，不能把末端塞进底座。
        pipe = FakePipeline(workspace(300.0, center=(180.0, 20.0, 400.0)))
        got = target_for(pipe, .5, .5, .5)
        self.assertGreaterEqual(math.dist(got, [0, 0, 0]), 50.0, f'末端退进了基座：{got}')

    def test_out_of_range_standoff_is_rejected(self):
        for bad in (-1, 800, 'many'):
            with self.assertRaises(ValueError):
                RealPalmTargetPipeline.configure(
                    FakePipeline(workspace(0)), camera_session='cam', connection=object(),
                    tool_context='tool', center=[600, 200, 450], size=[400, 300, 400],
                    direction=0, standoff=bad)


if __name__ == '__main__':
    unittest.main(verbosity=2)
