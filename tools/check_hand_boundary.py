"""Boundary-glide regression; disposable checkout only, never hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()

import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core import config
from backend.core.hand_follow import HandFollow
from backend.core.sim_tool import DEFAULT_TOOL, model_for


class BoundaryGlide(unittest.TestCase):
    def make(self,direction=0,compact=False,center_y=-400,center_z=750):
        f=HandFollow(model_for('elfin5',DEFAULT_TOOL))
        angle=math.radians(direction)
        center=[-center_y*math.sin(angle),center_y*math.cos(angle),center_z]
        half=[150,100,150] if compact else [400,150,250]
        lo,hi=map(list,zip(*config.MANUAL_LIMITS))
        result=f.configure(dict(session='offline',aspect=4/3,depth=True,rotation=False,
            depth_mode='gesture',direction=direction,
            enclosure={'min':[v-h for v,h in zip(center,half)],
                       'max':[v+h for v,h in zip(center,half)]}),lower=lo,upper=hi,now=10)
        self.assertTrue(result['ok'],result.get('why'))
        self.assertTrue(f.start(dict(token=f.token,session=f.session),current_q=f._center_q,now=10)['ok'])
        return f

    def follow(self,f,controls,frames=100):
        beginning=f.seq
        for _ in range(frames):
            seq=f.seq+1
            self.assertTrue(f.sample(dict(token=f.token,session=f.session,seq=seq,
                u=controls[0],v=controls[1],hand_open=controls[2],palm_scale=.2,
                angle=0,age_ms=0),now=10+(seq+1)/30)['ok'])
            before=list(f.actual);q_before=list(f.q)
            f.tick(1/30,now=10+(seq+1)/30)
            self.assertTrue(f._inside(f.actual),f.actual)
            self.assertLessEqual(math.dist(before[:3],f.actual[:3]),300/30+1e-6)
            for a,b,vmax in zip(q_before,f.q,f.model.vmax):
                self.assertLessEqual(abs(b-a),vmax/30+1e-6)
            self.assertEqual(f.base_offset,[0,0,0])
            self.assertNotEqual(f.why,'工作区边界保护',(seq-beginning,f.actual,controls))
        return f._rotate_pose(f.actual,inverse=True)[:3]

    def test_screenshot_front_face_can_slide_up_and_sideways_without_closing_palm(self):
        f=self.make()
        first=self.follow(f,[.614,.8122,1],180)
        after=self.follow(f,[.8,.4,1],100)
        self.assertGreater(after[0]-first[0],100)
        self.assertGreater(after[2]-first[2],150)
        self.assertLess(math.dist(after,[240,-550,800]),2)

    def test_latest_screenshot_y_minus_600_also_recovers_tangent_motion(self):
        f=self.make(center_y=-450,center_z=800)
        first=self.follow(f,[.308875,.8872,1],180)
        # Keep the tangent destination reachable with the requested tool pose;
        # the far upper corner is separately IK-limited, not a boundary lock.
        destination=[-80,-600,750,*f._base[3:]]
        _,_,_,reachable=f.model.ik(f._center_q,destination,f._lower,f._upper,
                                 iters=120,restarts=False,tol_mm=.08,tol_deg=.08)
        self.assertTrue(reachable)
        after=self.follow(f,[.4,.6,1],120)
        self.assertGreater(after[0]-first[0],50)
        self.assertGreater(after[2]-first[2],110)
        self.assertLess(math.dist(after,destination[:3]),2)

    def test_workspace_is_not_expanded_and_exact_display_targets_remain_unchanged(self):
        for direction in (0,90,180,270):
            f=self.make(direction,compact=True)
            target=[f._lo[0],f._lo[1],f._hi[2],*f._base[3:]]
            world=f._rotate_pose(target)
            tracked=f._rotate_pose(f._tracking_target(world),inverse=True)
            self.assertAlmostEqual(tracked[0],f._lo[0]+.5)
            self.assertAlmostEqual(tracked[1],f._lo[1]+.5)
            self.assertAlmostEqual(tracked[2],f._hi[2]-.5)
            target[0]-=.01
            self.assertFalse(f._inside(f._rotate_pose(target)))

    def test_front_and_back_faces_slide_on_all_four_directions(self):
        for direction in (0,90,180,270):
            for opening in (0,1):
                with self.subTest(direction=direction,opening=opening):
                    f=self.make(direction,compact=True)
                    first=self.follow(f,[.4,.65,opening],80)
                    after=self.follow(f,[.65,.35,opening],80)
                    self.assertGreater(after[0]-first[0],55)
                    self.assertGreater(after[2]-first[2],65)

    def test_other_four_faces_remain_inside_and_do_not_lock_tangent_motion(self):
        for first,second,axis in (([0,.6,.5],[0,.35,.5],2),([1,.6,.5],[1,.35,.5],2),
                                  ([.4,0,.5],[.65,0,.5],0),([.4,1,.5],[.65,1,.5],0)):
            with self.subTest(first=first):
                f=self.make(compact=True)
                before=self.follow(f,first,80)
                after=self.follow(f,second,80)
                self.assertGreater(after[axis]-before[axis],45)


if __name__=='__main__':
    import faulthandler
    faulthandler.dump_traceback_later(80,exit=True)
    unittest.main(verbosity=2)
