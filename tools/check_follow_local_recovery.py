from _offline_guard import require_offline_copy
require_offline_copy()
import math,time,unittest
from unittest.mock import patch
from backend.core import position_servo as servo
from backend.core.kinematics import pose_rotation_distance
from backend.core.sim_tool import model_for,DEFAULT_TOOL
from backend.core.hand_follow import HandFollow
from check_position_servo import make_model,xyz
class Recovery(unittest.TestCase):
 def test_existing_follow_entry_recenters_real_null_wrist_without_changing_tcp(self):
  from check_hand_wrist_continuity import WristContinuity
  fixture=WristContinuity();f=fixture.make();f.q[3:]=[40.,0.,-40.];f.actual=f.model.pose_vec(f.q);f._base=list(f.actual);f.target=list(f.actual);fixed=list(f.actual)
  fixture.drive(f,fixed[:3],150)
  self.assertLess(abs(f.q[5]),1.);self.assertLess(math.dist(f.actual[:3],fixed[:3]),.2);self.assertLess(pose_rotation_distance(f.actual,fixed),.1)
 def test_real_aligned_null_motion_preserves_tcp_and_so3_without_wrapping(self):
  model=make_model();q=[-90.,10.,60.,40.,0.,-40.];fixed=model.pose_vec(q);lo=[-360.]*6;hi=[360.]*6
  for _ in range(12):
   result=servo.solve_position_step(model,q,fixed[:3],lo,hi,max_joint_step=4,preferred_j6=0.,deadline=time.monotonic()+.1)
   self.assertIsNotNone(result['q']);self.assertLessEqual(max(abs(a-b) for a,b in zip(q,result['q'])),4+1e-9);q=result['q']
   self.assertLess(math.dist(model.pose_vec(q)[:3],fixed[:3]),.001);self.assertLess(pose_rotation_distance(model.pose_vec(q),fixed),.001)
  self.assertLess(abs(q[5]),1.)
 def test_deadline_returns_no_goal_but_next_same_request_resumes_bounded_work(self):
  model=make_model();q=[-90.,10.,60.,0.,20.,0.];target=xyz(model,q);target[0]+=20;clock=[100.];original=type(model)._flange_matrix;calls=[0]
  def charged(instance,angles):
   calls[0]+=1;value=original(instance,angles);clock[0]+=.001;return value
  saved=None;found=None
  with patch.object(servo.time,'monotonic',side_effect=lambda:clock[0]),patch.object(type(model),'_flange_matrix',charged):
   for _ in range(30):
    found=servo.solve_position_step(model,q,target,[-360.]*6,[360.]*6,deadline=clock[0]+.0025,resume=saved,resume_context='same-session')
    if found['q'] is not None:break
    self.assertEqual(found['cause'],'deadline');saved=found.get('resume');self.assertIsNotNone(saved)
   self.assertIsNotNone(found['q'],'every round restarted and starved the same finite search')
  self.assertLess(math.dist(xyz(model,found['q']),target),math.dist(xyz(model,q),target));self.assertLess(calls[0],100)
 def test_resume_rejected_after_actual_joint_target_limits_or_session_changes_and_expired_never_moves(self):
  model=make_model();q=[-90.,10.,60.,0.,20.,0.];target=xyz(model,q);target[0]+=20;clock=[100.];original=type(model)._flange_matrix
  def charged(instance,angles):value=original(instance,angles);clock[0]+=.001;return value
  with patch.object(servo.time,'monotonic',side_effect=lambda:clock[0]),patch.object(type(model),'_flange_matrix',charged):
   result=servo.solve_position_step(model,q,target,[-360.]*6,[360.]*6,deadline=clock[0]+.0025,resume_context='session-config1');saved=result['resume'];self.assertIsNone(result['q'])
   expired=servo.solve_position_step(model,q,target,[-360.]*6,[360.]*6,deadline=clock[0]-.001,resume=saved,resume_context='session-config1');self.assertIsNone(expired['q']);self.assertEqual(expired['cause'],'deadline')
   for changed in ('q','target','limits','session'):
    qq=list(q);tt=list(target);high=[360.]*6;context='session-config1'
    if changed=='q':qq[0]+=.1
    if changed=='target':tt[0]-=40
    if changed=='limits':high[0]=0
    if changed=='session':context='session-config2'
    next_result=servo.solve_position_step(model,qq,tt,[-360.]*6,high,deadline=clock[0]+.0015,resume=saved,resume_context=context)
    self.assertIsNone(next_result['q']);self.assertIsNot(next_result.get('resume'),saved)
   self.assertLessEqual(len(saved.positions),servo.MAX_EVALUATIONS);self.assertLessEqual(len(saved.jacobians),servo.MAX_ITERATIONS)
 def test_unaligned_wrist_cannot_claim_null_motion_and_limits_are_not_clipped(self):
  model=make_model();q=[-90.,10.,60.,40.,20.,-40.];target=xyz(model,q)
  value=servo.solve_position_step(model,q,target,[-360.]*6,[360.]*6,preferred_j6=0.)
  self.assertEqual(value['q'],q);self.assertEqual(value['cause'],'reached')
  q[4]=0.;target=xyz(model,q);lo=list(q);hi=list(q)
  value=servo.solve_position_step(model,q,target,lo,hi,preferred_j6=0.)
  self.assertEqual(value['q'],q)
 def test_real_null_step_still_requires_original_path_gate_and_stop_clears_resume(self):
  from check_hand_wrist_continuity import WristContinuity
  f=WristContinuity().make();f.q[3:]=[40.,0.,-40.];f.actual=f.model.pose_vec(f.q);f._base=list(f.actual);f.target=list(f.actual)
  x,y,z=f.actual[:3];f.sample(dict(token=f.token,session=f.session,seq=1,u=(x-f._lo[0])/(f._hi[0]-f._lo[0]),v=(f._hi[2]-z)/(f._hi[2]-f._lo[2]),hand_open=(f._hi[1]-y)/(f._hi[1]-f._lo[1]),palm_scale=.2,angle=0,age_ms=0),now=10.1)
  before=list(f.q);calls=[];f.tick(1/30,now=10.1,validate_step=lambda a,b:calls.append((a,b)) or False)
  self.assertTrue(calls);self.assertEqual(f.q,before);self.assertIsNone(f._goal)
  f._subdivision_resume=object();f.stop();self.assertIsNone(f._subdivision_resume)
class StartupZero(unittest.IsolatedAsyncioTestCase):
 async def test_recorded_nonzero_wrist_plans_zero_center_without_changing_actual(self):
  original=[-90.148,-3.438,79.964,1.2795,6.602,-1.265]
  for direction in (0,90,180,270):
   model=model_for('elfin5',DEFAULT_TOOL);actual=list(original);actual[0]+=direction
   if actual[0]>180:actual[0]-=360
   supplied=list(actual);position=model.pose_vec(actual)[:3]
   f=HandFollow(model)
   result=f.configure(dict(session='zero-start',aspect=4/3,depth=True,rotation=False,direction=direction,depth_mode='gesture',enclosure={'min':[v-60 for v in position],'max':[v+60 for v in position]}),lower=[-360.]*6,upper=[360.]*6,current_q=actual,now=10.)
   self.assertTrue(result['ok'],result);self.assertEqual(actual,supplied);self.assertEqual(f._center_q[5],0.)
   self.assertLess(math.dist(model.pose_vec(f._center_q)[:3],position),.005)
   self.assertTrue(f.start(dict(token=f.token,session=f.session),current_q=supplied,now=10.)['ok'])
   self.assertEqual(f.q,supplied,'start only requests a center trajectory, never teleports actual joints')
 async def test_explicit_rotation_can_progress_without_drifting_away_from_new_target(self):
  model=model_for('elfin5',DEFAULT_TOOL);f=HandFollow(model)
  payload=dict(session='rotation-progress',aspect=4/3,depth=True,rotation=True,direction=0,depth_mode='gesture',enclosure={'min':[-400.,-550.,450.],'max':[400.,-250.,950.]})
  self.assertTrue(f.configure(payload,now=10.)['ok'])
  self.assertEqual(f._center_q[5],0.);f.start(dict(token=f.token,session=f.session),current_q=f._center_q,now=10.)
  initial=list(f.actual);f.waiting=False;f._angle0=0.;f._angle_prev=0.;f._angle_unwrapped=0.
  # An exact neighbouring FK proves reachability; a global yaw at fixed TCP
  # near a wrist singularity is a different, potentially infeasible task.
  reachable_q=list(f.q);reachable_q[0]+=10.;target=model.pose_vec(reachable_q)
  previous_error=pose_rotation_distance(initial,target)
  for index in range(160):
   now=10.01+index*.01
   self.assertTrue(f.sample(dict(token=f.token,session=f.session,seq=index+1,u=(target[0]+400.)/800.,v=(950.-target[2])/500.,hand_open=(-250.-target[1])/300.,palm_scale=.2,angle=10.,age_ms=0),now=now)['ok'])
   before=list(f.q);f.tick(.01,now=now)
   error=pose_rotation_distance(f.actual,f.target)
   self.assertLessEqual(error,max(.1,previous_error)+1e-8);previous_error=error
   self.assertTrue(f._valid_q(f.q));self.assertTrue(f._inside(f.actual))
   for a,b,cap in zip(before,f.q,model.vmax):self.assertLessEqual(abs(b-a),cap*.01+1e-6)
  self.assertGreater(pose_rotation_distance(initial,f.actual),9.9,(f.q,f._nonsingular(f.q),f._solver_detail,f.why,f.target,f.actual));self.assertLess(previous_error,.1)
  self.assertLess(math.dist(f.actual[:3],target[:3]),.2)

 async def test_fixed_rotation_translation_preserves_pose_and_converges(self):
  model=model_for('elfin5',{'kind':'library','id':'robotiq-2f140','rotation':[0,0,0]});f=HandFollow(model)
  self.assertTrue(f.configure(dict(session='pose-translation',aspect=4/3,depth=True,rotation=True,direction=0,enclosure={'min':[-400.,-550.,450.],'max':[400.,-250.,950.]}),current_q=[-89.993,24.563,95.898,.028,18.669,.023],now=10.)['ok'])
  f.start(dict(token=f.token,session=f.session),current_q=f._center_q,now=10.);f.waiting=False;f.target[0]+=10.;trace=[]
  for i in range(31):
   now=10.04+i*.01;f._last_observation=now;f.tick(.01,now=now)
   trace.append(dict(i=i,error=math.dist(f.actual[:3],f.target[:3]),angle=pose_rotation_distance(f.actual,f.target),cause=f._solver_cause,detail=f._solver_detail,goal_error=math.dist(model.pose_vec(f._goal)[:3],f.target[:3]) if f._goal else None))
   self.assertLess(pose_rotation_distance(f.actual,f.target),.1)
  print('ROTATION_FIXED_TRACE',trace,flush=True)
  self.assertLess(math.dist(f.actual[:3],f.target[:3]),.2)

 async def test_zero_outside_operating_limits_rejects_before_planning(self):
  f=HandFollow(make_model());lo=[-360.]*6;hi=[360.]*6;lo[5]=1.
  with patch('backend.core.hand_follow.solve_position_step',side_effect=AssertionError('invalid zero cannot enter solver')):
   result=f.configure(dict(session='zero-limit',aspect=4/3,depth=False,rotation=False),lower=lo,upper=hi,now=10.)
  self.assertFalse(result['ok']);self.assertIn('J6',result['why']);self.assertFalse(f.active)
 async def test_world_startup_still_requires_original_path_precheck(self):
  import asyncio
  from backend.core.world import World
  w=World();w._save_preferences({'mode':'sim'});w.sim.q=[-90.148,-3.438,79.964,1.2795,6.602,-1.265];before=list(w.sim.q)
  model=w.motion_model();center=model.pose_vec(before)[:3];session='mouse-zero-start'
  payload=dict(session=session,aspect=4/3,depth=True,rotation=False,direction=0,depth_mode='gesture',enclosure={'min':[v-60 for v in center],'max':[v+60 for v in center]})
  result=await w.hand_follow_action('configure',payload,{},input_kind='mouse')
  self.assertTrue(result['ok'],result);self.assertEqual(w.sim.q,before);self.assertEqual(w.hand_follow.q,before);self.assertEqual(w.hand_follow._center_q[5],0.)
  report={'risk_detected':True,'issues':[{'sample':0,'reasons':['test path refused']}]}
  with patch('backend.core.world.inspect_path',return_value=report) as check:
   started=await w.hand_follow_action('start',dict(session=session,token=w.hand_follow.token),{},input_kind='mouse')
   self.assertTrue(started['ok'],started);task=w.executor.task;self.assertIsNotNone(task);await asyncio.wait_for(asyncio.shield(task),2.)
  self.assertTrue(check.called);self.assertEqual(check.call_args.args[2][5],0.);self.assertEqual(w.sim.q,before);self.assertIsNone(w.sim.traj);self.assertEqual(w.executor.state,'failed');self.assertIn('test path refused',w.executor.error)

if __name__=='__main__':unittest.main()
