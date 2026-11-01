"""Position-priority tracking, measured through independent production FK."""
from _offline_guard import require_offline_copy
require_offline_copy()

import math
import unittest
from backend.core.hand_follow import HandFollow
from backend.core.sim_tool import DEFAULT_TOOL, model_for
from backend.core import config


class PositionFollowing(unittest.TestCase):
    def test_four_directions_track_and_reverse_without_changing_base(self):
        for direction in (0, 90, 180, 270):
            with self.subTest(direction=direction):
                angle = math.radians(direction)
                center = [400*math.sin(angle), -400*math.cos(angle), 700]
                lower, upper = map(list, zip(*config.MANUAL_LIMITS))
                follow = HandFollow(model_for('elfin5', DEFAULT_TOOL))
                configured = follow.configure(dict(session='position-contract', aspect=4/3,
                    depth=True, rotation=False, direction=direction, depth_mode='gesture',
                    enclosure={'min':[a-b for a,b in zip(center,(200,150,200))],
                               'max':[a+b for a,b in zip(center,(200,150,200))]}),
                    lower=lower, upper=upper, now=10.)
                self.assertTrue(configured['ok'], configured)
                self.assertTrue(follow.start(dict(token=follow.token, session=follow.session),
                                            current_q=follow._center_q, now=10.)['ok'])
                for u, v in ((.55,.5), (.45,.5), (.5,.55), (.5,.45), (.5,.5)):
                    for _ in range(240):
                        before = list(follow.q)
                        now = follow._clock_value + 1/30
                        reply = follow.sample(dict(token=follow.token, session=follow.session,
                            seq=follow.seq+1, u=u, v=v, hand_open=.5,
                            palm_scale=.2, angle=0., age_ms=0.), now=now)
                        self.assertTrue(reply['ok'], reply)
                        follow.tick(1/30, now=now, speed_pct=100)
                        measured = follow.model.pose_vec(follow.q)
                        self.assertLess(math.dist(measured[:3], follow.actual[:3]), 1e-6)
                        self.assertTrue(follow._inside(measured))
                        self.assertEqual(follow.base_offset, [0.,0.,0.])
                        for old,new,cap,lo,hi in zip(before,follow.q,follow.model.vmax,lower,upper):
                            self.assertTrue(math.isfinite(new))
                            self.assertLessEqual(abs(new-old), cap/30+1e-6)
                            self.assertLessEqual(lo,new); self.assertLessEqual(new,hi)
                        if math.dist(measured[:3], follow.target[:3]) < 1.:
                            break
                    self.assertLess(math.dist(measured[:3], follow.target[:3]), 1.)


if __name__ == '__main__': unittest.main()
