"""Production target mapping with capture and measured-arrival gates; no IO."""
from _offline_guard import require_offline_copy
require_offline_copy()
from types import SimpleNamespace as NS
import unittest
from backend.core.real_palm_pipeline import RealPalmTargetPipeline

class Mapping(unittest.TestCase):
    def make(self,direction=270,standoff=120.):
        context=dict(camera_session='camera',connection=('original',1),tool_context='actual')
        startup=NS(cancel=lambda reason:None,phase='blocked',context=context,center=None)
        pipe=RealPalmTargetPipeline(None,None,lambda:context,lambda a,b:True,startup=startup,clock=lambda:10.)
        pipe.configure(**context,center=[-600.,0.,650.],size=[100.,80.,60.],direction=direction,standoff=standoff,role='track')
        startup.phase='ready';startup.center=list(pipe.workspace['tcp_center'])
        return pipe
    def submit(self,p,**changes):
        args=dict(token=p.token,session='camera',seq=1,frame_seq=3,captured_at=9.9,u=.5,v=.5,opening=.5)
        args.update(changes)
        return p.submit(**args)
    def test_fixed_forward_offset_preserves_height_and_palm(self):
        for direction,offset in ((0,[0.,120.,0.]),(90,[-120.,0.,0.]),(180,[0.,-120.,0.]),(270,[120.,0.,0.])):
            p=self.make(direction);self.assertTrue(self.submit(p))
            self.assertEqual(p.latest['palm'],[-600.,0.,650.])
            self.assertEqual(p.latest['target'],[a+b for a,b in zip(p.latest['palm'],offset)])
    def test_follow_zero_offset_unchanged(self):
        p=self.make(standoff=0.);self.assertTrue(self.submit(p));self.assertEqual(p.latest['target'],p.latest['palm'])
    def test_actual_startup_must_arrive_at_tcp_not_palm_center(self):
        p=self.make();p.startup.center=list(p.workspace['center'])
        self.assertFalse(self.submit(p));self.assertIsNone(p.latest)
    def test_invalid_and_old_samples_cannot_update(self):
        for changes in ({'captured_at':9.6},{'token':'old'},{'session':'old'},{'frame_seq':-1},{'u':1.1}):
            p=self.make();self.assertFalse(self.submit(p,**changes));self.assertIsNone(p.latest)
    def test_full_tcp_workspace_has_same_offset_not_radial_shrink(self):
        p=self.make();self.assertTrue(self.submit(p,u=1.,v=0.,opening=1.))
        self.assertEqual(p.latest['target'][2],680.)
        self.assertTrue(all(a<=v<=b for a,v,b in zip(p.workspace['low'],p.latest['target'],p.workspace['high'])))
    def test_nonfinite_standoff_rejected(self):
        for n in (float('nan'),float('inf'),-1,501):
            with self.assertRaises(ValueError):self.make(standoff=n)

if __name__=='__main__':unittest.main(verbosity=2)
