"""Offline fake-host tests. No real transport, camera, World or execution."""
from _offline_guard import require_offline_copy
require_offline_copy()

from copy import deepcopy
import unittest

from backend.core.real_follow_startup import RealFollowStartup


class Model:
    def pose_vec(self,q):return [q[0],q[1],500.+q[2],q[3],q[4],q[5]]


class Startup(unittest.TestCase):
    def setUp(self):
        self.now=10.
        self.context=dict(connection=['arm',1],tool_context='validated:r1')
        self.segments=[]
        self.startup=RealFollowStartup(Model(),lambda:deepcopy(self.context),
            lambda a,b:self.segments.append((a,b)) or True,clock=lambda:self.now)
        self.feedback=dict(q=[0.,0.,0.,0.,0.,180.],stamp=10.,frame_seq=1,
            connection=['arm',1],enabled=True,safety_ok=True,motion_allowed=True,idle=True)

    def prepare(self):
        return self.startup.prepare(self.feedback,[0.,0.,500.],[-180.]*6,[180.]*6)

    def arrive(self,q=None,**changes):
        self.now+=.02
        self.feedback.update(q=list(q or [0.]*6),stamp=self.now,frame_seq=self.feedback['frame_seq']+1)
        self.feedback.update(changes)
        return self.startup.observe_arrival(self.startup.token,self.feedback)

    def test_180_to_zero_has_complete_small_chord_not_an_instant_wrist_target(self):
        plan=self.prepare()
        self.assertIsNotNone(plan)
        self.assertEqual(plan['q'],[0.]*6)
        self.assertEqual(self.startup.phase,'planned')
        self.assertEqual(len(self.segments),720)
        self.assertEqual(self.segments[0][0][5],180.)
        self.assertEqual(self.segments[-1][1][5],0.)
        for a,b in self.segments:self.assertLessEqual(max(abs(x-y) for x,y in zip(a,b)),.25)

    def test_ack_or_repeated_feedback_does_not_mean_arrived(self):
        plan=self.prepare()
        self.assertTrue(self.startup.mark_dispatched(plan['token'],self.feedback))
        for _ in range(4):self.assertFalse(self.startup.observe_arrival(plan['token'],self.feedback))
        self.assertFalse(self.arrive())
        self.assertFalse(self.arrive())
        self.assertTrue(self.arrive())

    def test_wrong_wrist_or_moving_feedback_resets_arrival_count(self):
        plan=self.prepare();self.startup.mark_dispatched(plan['token'],self.feedback)
        self.assertFalse(self.arrive())
        self.assertFalse(self.arrive(q=[0.,0.,0.,0.,0.,180.]))
        self.assertFalse(self.arrive(idle=False))
        self.assertFalse(self.arrive(idle=True))
        self.assertFalse(self.arrive())
        self.assertTrue(self.arrive())

    def test_collision_in_middle_blocks_entire_plan(self):
        self.startup.validate_segment=lambda a,b:not b[5]<90.
        self.assertIsNone(self.prepare())
        self.assertEqual(self.startup.phase,'blocked')

    def test_non_true_path_result_is_not_accepted(self):
        for result in (None,1,False):
            self.startup.validate_segment=lambda a,b:result
            self.assertIsNone(self.prepare())

    def test_invalid_feedback_and_limits_never_prepare(self):
        for changes in (dict(enabled=False),dict(stamp=9.),dict(q=[0.]*5),
                        dict(idle=False),dict(connection=['different',1])):
            old=deepcopy(self.feedback);self.feedback.update(changes)
            self.assertIsNone(self.prepare());self.feedback=old
        self.assertIsNone(self.startup.prepare(self.feedback,[0,0,500],[-180]*5+[1],[180]*6))

    def test_tool_change_stale_frame_and_stop_invalidate_startup(self):
        for cause in ('tool','stale','stop'):
            self.setUp();plan=self.prepare()
            self.startup.mark_dispatched(plan['token'],self.feedback)
            if cause=='tool':self.context['tool_context']='changed'
            if cause=='stale':self.now+=1.
            if cause=='stop':self.startup.cancel()
            self.assertFalse(self.startup.observe_arrival(plan['token'],self.feedback))
            self.assertEqual(self.startup.phase,'blocked')

    def test_context_changed_inside_path_check_blocks_plan(self):
        def change(a,b):self.context['tool_context']='changed';return True
        self.startup.validate_segment=change
        self.assertIsNone(self.prepare())

    def test_cancel_revokes_old_plan_and_no_automatic_restart(self):
        plan=self.prepare();self.startup.cancel('用户停止')
        self.assertFalse(self.startup.mark_dispatched(plan['token'],self.feedback))
        self.assertIsNone(self.startup.goal)

    def test_real_kinematics_preparation_retains_known_center_and_zero_j6(self):
        from backend.core.kinematics import RobotModel
        model=RobotModel('elfin5')
        goal=[0.,-30.,60.,0.,-30.,0.]
        self.feedback['q']=[*goal[:5],180.]
        self.startup.model=model
        self.assertIsNotNone(self.startup.prepare(self.feedback,model.pose_vec(goal)[:3],[-360.]*6,[360.]*6))
        self.assertEqual(self.startup.goal,goal)

    def test_slow_geometric_preparation_requires_fresh_unchanged_dispatch_start(self):
        def slow(a,b):self.now+=.001;return True
        self.startup.validate_segment=slow
        plan=self.prepare()
        self.assertIsNotNone(plan)
        self.assertGreater(self.now-self.feedback['stamp'],.15)
        self.assertFalse(self.startup.mark_dispatched(plan['token'],self.feedback))
        self.setUp();self.startup.validate_segment=slow
        plan=self.prepare()
        self.feedback['stamp']=self.now
        self.feedback['q'][5]=179.
        self.assertFalse(self.startup.mark_dispatched(plan['token'],self.feedback))

    def test_fresh_unchanged_start_can_dispatch_after_slow_geometry(self):
        def slow(a,b):self.now+=.001;return True
        self.startup.validate_segment=slow
        plan=self.prepare()
        self.feedback['stamp']=self.now
        self.assertTrue(self.startup.mark_dispatched(plan['token'],self.feedback))


if __name__=='__main__':unittest.main(verbosity=2)
