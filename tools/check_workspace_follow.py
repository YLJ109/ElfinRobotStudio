"""Current shared-coordinate/follow contract. Run via run_offline_checks.py."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import copy
import json
import math
from pathlib import Path
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import config
from backend.core.hand_follow import HandFollow
from backend.core.sim_tool import DEFAULT_TOOL, model_for
from backend.core.kinematics import pose_rotation_distance
from backend.core.safety import evaluate
from backend.core.world import World


def configuration(direction=0):
    angle=math.radians(direction)
    center=[400*math.sin(angle),-400*math.cos(angle),700]
    return dict(session="offline",aspect=4/3,depth=True,rotation=False,direction=direction,
        enclosure={"min":[v-d for v,d in zip(center,[400,150,250])],
                   "max":[v+d for v,d in zip(center,[400,150,250])]})


def camera():
    return {"active":{"session":"offline","state":"streaming","last_frame_at":time.time(),
        "images":{"color":{"width":640,"height":480,"seq":1}}}}


class Geometry(unittest.TestCase):
    def test_gesture_depth_fist_back_open_forward_on_all_four_sides(self):
        for direction in (0,90,180,270):
            f=HandFollow(model_for('elfin5',DEFAULT_TOOL))
            lo,hi=map(list,zip(*config.MANUAL_LIMITS))
            result=f.configure(dict(configuration(direction),depth_mode='gesture'),lower=lo,upper=hi,now=10)
            self.assertTrue(result['ok'],result.get('why'))
            self.assertEqual(result['state']['depth_mode'],'gesture')
            f.start({'token':f.token,'session':f.session},current_q=f._center_q,now=10)
            seq=0
            for opening in (0.,.5,1.):
                for scale in (.06,.3):
                    seq+=1
                    result=f.sample(dict(token=f.token,session=f.session,seq=seq,u=.5,v=.5,
                        palm_scale=scale,hand_open=opening,angle=0,age_ms=0),now=10+seq/30)
                    self.assertTrue(result['ok'],result.get('why'))
                    expected=f._rotate_pose([(f._lo[0]+f._hi[0])/2,f._hi[1]-opening*(f._hi[1]-f._lo[1]),
                        (f._lo[2]+f._hi[2])/2,*f._base[3:]])
                    self.assertLess(math.dist(f.target[:3],expected[:3]),1e-8)
            last_target=list(f.target)
            seq+=1
            self.assertTrue(f.sample(dict(token=f.token,session=f.session,seq=seq,u=.5,v=.5,
                palm_scale=.2,angle=0,age_ms=0),now=11)['ok'])
            # 正确：掌心有效但张合漏检不构成回中指令，保持上次有效深度。
            self.assertGreater(math.dist(last_target[:3],f._base[:3]),100)
            self.assertEqual(f.target,last_target)
            self.assertTrue(f.active)
            for invalid in (-.01,1.01,True,float('nan'),'open'):
                result=f.sample(dict(token=f.token,session=f.session,seq=seq+1,u=.5,v=.5,
                    palm_scale=.2,hand_open=invalid,angle=0,age_ms=0),now=11.1)
                self.assertFalse(result['ok'])
                self.assertEqual(f.seq,seq)

    def test_axial_tool_translation_and_absence_do_not_wind_shoulder_toward_limit(self):
        model=model_for('elfin5',{'kind':'library','id':'robotiq-2f140','rotation':[0,0,0]})
        f=HandFollow(model)
        self.assertTrue(f.configure(configuration(),current_q=[-89.993,24.563,95.898,.028,18.669,.023],now=10)['ok'])
        f.start({'token':f.token,'session':f.session},current_q=f._center_q,now=10)
        for i in range(1,241):
            now=10+i/30
            self.assertTrue(f.sample({'token':f.token,'session':f.session,'seq':i,'u':.65,'v':.4,
                'palm_scale':.2,'angle':0,'age_ms':0,'hand_present':i<=120},now=now)['ok'])
            f.tick(1/30,now=now)
            self.assertLess(abs(f.q[0]),324,'small translation must not create an almost full shoulder turn')
        self.assertLess(math.dist(f.actual[:3],f._base[:3]),.2)
        self.assertTrue(f.active)

    def test_follow_translation_at_euler_pole_never_interpolates_false_half_turn(self):
        model=model_for('elfin5',{'kind':'library','id':'robotiq-2f140','rotation':[0,0,0]})
        f=HandFollow(model)
        self.assertTrue(f.configure(configuration(),current_q=[-89.993,24.563,95.898,.028,18.669,.023],now=10)['ok'])
        f.start({'token':f.token,'session':f.session},current_q=f._center_q,now=10)
        f.waiting=False;f.target[0]+=10
        real_pose=model.pose_vec;center=list(f.q)
        def equivalent(q):
            value=real_pose(q)
            if list(q)==center:value=[*value[:3],value[3]+180,180-value[4],value[5]+180]
            return value
        from backend.core.hand_follow import solve_position_step
        before=list(f.q)
        with patch.object(model,'pose_vec',side_effect=equivalent),patch('backend.core.hand_follow.solve_position_step',wraps=solve_position_step) as solver:
            f.tick(.01,now=10.04)
        self.assertTrue(solver.called)
        self.assertEqual(solver.call_args.args[2],f._tracking_target(f.target)[:3])
        self.assertTrue(f._valid_q(f.q));self.assertTrue(f._inside(f.actual))
        self.assertLessEqual(max(abs(a-b) for a,b in zip(before,f.q)),max(model.vmax)*.01+1e-6)

    def test_explicit_rotation_mode_keeps_SO3_waypoint_at_euler_pole(self):
        model=model_for('elfin5',{'kind':'library','id':'robotiq-2f140','rotation':[0,0,0]})
        f=HandFollow(model)
        self.assertTrue(f.configure({**configuration(),'rotation':True},current_q=[-89.993,24.563,95.898,.028,18.669,.023],now=10)['ok'])
        f.start({'token':f.token,'session':f.session},current_q=f._center_q,now=10)
        f.waiting=False;f.target[0]+=10
        real_pose=model.pose_vec;center=list(f.q)
        def equivalent(q):
            value=real_pose(q)
            if list(q)==center:value=[*value[:3],value[3]+180,180-value[4],value[5]+180]
            return value
        with patch.object(model,'pose_vec',side_effect=equivalent),patch.object(model,'ik',wraps=model.ik) as solver:
            f.tick(.01,now=10.04)
        self.assertTrue(solver.called)
        self.assertLess(pose_rotation_distance(solver.call_args.args[1],f.target),.001)
        # New explicit-pose case: retain speed caps and the existing damping;
        # 300ms observes settling without changing any archived test deadline.
        for index in range(30):
            previous=list(f.q)
            f._last_observation=10.05+index*.01
            f.tick(.01,now=10.05+index*.01)
            self.assertLess(pose_rotation_distance(f.actual,f.target),.1)
            self.assertTrue(f._valid_q(f.q));self.assertTrue(f._inside(f.actual))
            for a,b,cap in zip(previous,f.q,model.vmax):self.assertLessEqual(abs(b-a),cap*.01+1e-6)
        self.assertLess(math.dist(f.actual[:3],f.target[:3]),.2)

    def test_four_directions_share_fixed_base_and_exact_tool_centre(self):
        for direction in (0,90,180,270):
            with self.subTest(direction=direction):
                model=model_for("elfin5",DEFAULT_TOOL)
                follow=HandFollow(model)
                lo,hi=map(list,zip(*config.MANUAL_LIMITS))
                result=follow.configure(configuration(direction),lower=lo,upper=hi,now=10.)
                self.assertTrue(result["ok"],result.get("why"))
                expected=[(a+b)/2 for a,b in zip(follow._lo,follow._hi)]
                self.assertLess(math.dist(follow.actual[:3],expected),.2)
                self.assertEqual(follow.q[5],0)
                self.assertEqual(follow.base_offset,[0,0,0])
                self.assertAlmostEqual(model.fk(follow.q)["elfin_base"][2][3],0)
                self.assertTrue(follow._inside(follow.actual))
                matrix=model._flange_matrix(follow.q)
                normal=[matrix[i][2] for i in range(3)]
                wanted=[math.sin(math.radians(direction)),-math.cos(math.radians(direction)),0]
                self.assertLess(math.dist(normal,wanted),.01)

    def test_lost_hand_returns_to_center_without_revoking_session(self):
        model=model_for("elfin5",DEFAULT_TOOL); follow=HandFollow(model)
        self.assertTrue(follow.configure(configuration(),now=10.)["ok"])
        follow.start(dict(token=follow.token,session=follow.session),now=10.)
        token=follow.token
        for seq,scale in enumerate((.2,.2,.2,.4),1):
            result=follow.sample(dict(token=token,session=follow.session,seq=seq,u=.8,v=.2,
                palm_scale=scale,angle=0,age_ms=0,hand_present=True),now=10.+seq/30)
            self.assertTrue(result["ok"])
        self.assertNotEqual(follow.target[:3],follow._base[:3])
        result=follow.sample(dict(token=token,session=follow.session,seq=5,u=.5,v=.5,
            palm_scale=1,angle=0,age_ms=0,hand_present=False),now=10.2)
        self.assertTrue(result["ok"])
        self.assertEqual(follow.target,follow._base)
        self.assertTrue(follow.waiting)
        follow.tick(1/30,now=11.,speed_pct=100)
        self.assertTrue(follow.active)
        self.assertEqual(follow.token,token)

    def test_tool_only_yellow_red_and_contact_boundaries(self):
        model=model_for("elfin5",DEFAULT_TOOL)
        settings=copy.deepcopy(config.DEFAULTS)
        settings["fence"]={"x":[-100,100],"y":[-100,100],"z":[0,1400],"enabled":True}
        geometry_fixture=SimpleNamespace(flange=model.flange,lower=model.lower,upper=model.upper)
        for x,level in ((89,"green"),(90,"yellow"),(95,"red"),(100,"alarm")):
            caps=[dict(link=model.flange,a=[x,0,700],b=[x,0,700],r_mm=0),
                  dict(link="elfin_link2",a=[9999,9999,-500],b=[9999,9999,-500],r_mm=200)]
            result=evaluate(geometry_fixture,[0]*6,settings,caps=caps)
            # 仿真里"接近余量"只做提示不拦截（SIM01），所以看诊断字段 workspace_level；
            # 真正越界的执行级别 level 仍然是绿色。
            self.assertEqual(result["workspace_level"],level)
            self.assertEqual(result["fence_level"],level)
            self.assertEqual(result["level"],"green")
        caps=[dict(link=model.flange,a=[0,0,0],b=[0,0,-1],r_mm=0)]
        # 已经插进地面是真实穿透，任何模式都必须报警。
        self.assertEqual(evaluate(geometry_fixture,[0]*6,settings,caps=caps)["level"],"alarm")
        self.assertGreaterEqual(len([c for c in model.capsules([0]*6) if c["link"]==model.flange]),3)


class SharedWorld(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config._settings=copy.deepcopy(config.DEFAULTS)
        config._settings.update(mode="sim",auto_connect=False,tts=False,sim_speed_pct=100)
        self.world=World()
        self.world._hand_camera_reader=camera

    async def asyncTearDown(self):
        await self.world.stop()

    async def centered_follow(self):
        world=self.world
        result=await world.hand_follow_action("configure",configuration(),camera())
        self.assertTrue(result["ok"],result)
        # This fixture starts after the independently tested normal center move.
        world.sim.q=list(world.hand_follow._center_q)
        result=world.hand_follow.start(dict(token=world.hand_follow.token,session="offline"),current_q=world.sim.q)
        self.assertTrue(result["ok"],result)
        return world.hand_follow.token

    async def test_sim_follow_routes_refuse_real_mode_or_any_real_link_without_sending(self):
        calls=[]
        class LinkMustNotBeUsed:
            async def ask(self,*args,**kwargs):
                calls.append(args)
                raise AssertionError('Simulation follow must never command hardware')
        world=self.world
        try:
            for mode,link in (('real',None),('sim',LinkMustNotBeUsed()),('real',LinkMustNotBeUsed())):
                world.settings['mode']=mode;world.real=link
                for action in ('configure','start','sample','stop'):
                    result=await world.hand_follow_action(action,{},camera())
                    self.assertFalse(result['ok'])
                    self.assertFalse(world.hand_follow.active)
            self.assertEqual(calls,[])
        finally:
            world.settings['mode']='sim';world.real=None

    async def test_camera_loss_waits_indefinitely_then_resumes_same_owned_session(self):
        world=self.world;token=await self.centered_follow()
        held=list(world.sim.q)
        world._hand_camera_reader=lambda:{}
        world._tick_hand_follow(.02,time.monotonic()+300)
        self.assertEqual(world.sim.q,held);self.assertTrue(world.hand_follow.active)
        self.assertTrue(world.hand_follow.waiting);self.assertEqual(world.hand_follow.token,token)
        payload=dict(token=token,session="offline",seq=1,u=.6,v=.5,palm_scale=.2,angle=0,age_ms=0)
        # The explicit +300s tick above advances the synthetic monotonic clock.
        # Subsequent samples must share that clock instead of real wall time.
        with patch('backend.core.hand_follow.time.monotonic',return_value=world.hand_follow._clock_value+.1):
            result=await world.hand_follow_action("sample",payload,{})
        self.assertTrue(result["ok"],result);self.assertTrue(result["state"]["waiting"])
        world._hand_camera_reader=camera
        with patch('backend.core.hand_follow.time.monotonic',return_value=world.hand_follow._clock_value+.1):
            result=await world.hand_follow_action("sample",payload,camera())
        self.assertTrue(result["ok"],result);self.assertFalse(world.hand_follow.waiting)
        self.assertEqual(world.hand_follow.token,token)

    async def test_red_candidate_is_rejected_but_does_not_cancel_follow_or_spam_logs(self):
        world=self.world;token=await self.centered_follow()
        held=list(world.sim.q)
        now=time.monotonic()
        for seq in range(1,5):
            result=world.hand_follow.sample(dict(token=token,session="offline",seq=seq,
                u=.6,v=.5,palm_scale=.2,angle=0,age_ms=0),now=now+seq/30)
            self.assertTrue(result["ok"],result)
            with patch('backend.core.world.evaluate',return_value={"level":"red"}):
                world._tick_hand_follow(1/30,now+seq/30)
            self.assertEqual(world.sim.q,held)
            self.assertTrue(world.hand_follow.active);self.assertTrue(world.hand_follow.waiting)
            self.assertEqual(world.hand_follow.token,token)

    async def test_other_motion_cannot_silently_take_over_an_armed_follow(self):
        world=self.world;token=await self.centered_follow()
        self.assertFalse(world._need(world.MOTION,"点位执行"))
        self.assertIn("主动停止",world.last_deny)
        self.assertTrue(world.hand_follow.active);self.assertEqual(world.hand_follow.token,token)

    async def test_start_moves_shared_joints_smoothly_then_tracks(self):
        world=self.world
        result=await world.hand_follow_action("configure",configuration(),camera())
        self.assertTrue(result["ok"],result)
        self.assertEqual(world.hand_follow.q,world.sim.q)
        initial=list(world.sim.q)
        result=await world.hand_follow_action("start",dict(session="offline",token=world.hand_follow.token),camera())
        self.assertTrue(result["ok"])
        self.assertEqual(world.sim.q,initial,"click must not teleport the arm")
        largest_step=0.
        for _ in range(1200):
            previous=list(world.sim.q)
            world._tick(.02,time.monotonic())
            largest_step=max(largest_step,max(abs(a-b) for a,b in zip(previous,world.sim.q)))
            await asyncio.sleep(.001)
            if not world.executor.busy and not world._hand_centering:
                break
        self.assertEqual(world.executor.state,"completed",world.executor.error)
        self.assertLess(largest_step,2.1)
        self.assertEqual(world.sim.q,world.hand_follow.q)
        self.assertLess(math.dist(world.motion_model().pose_vec(world.sim.q)[:3],[0,-400,700]),.2)
        snap=world.snapshot()
        self.assertLess(math.dist(snap["tcp"]["xyz_mm"],world.hand_follow.actual[:3]),.01)
        self.assertEqual(snap["tcp_frame"]["translation_mm"],[0,0,5])
        start_x=world.motion_model().pose_vec(world.sim.q)[0]
        for seq in range(1,25):
            result=await world.hand_follow_action("sample",dict(token=world.hand_follow.token,
                session="offline",seq=seq,u=.65,v=.4,palm_scale=.2,angle=0,
                age_ms=0,hand_present=True),camera())
            self.assertTrue(result["ok"],result)
            world._tick(1/30,time.monotonic())
            await asyncio.sleep(1/30)
        self.assertTrue(world.hand_follow.active,world.hand_follow.why)
        self.assertGreater(world.motion_model().pose_vec(world.sim.q)[0],start_x+20)
        self.assertEqual(world.sim.q,world.hand_follow.q)
        self.assertEqual(world.hand_follow.base_offset,[0,0,0])
        # Latest requirement: losing the palm smoothly returns the shared TCP
        # to center, without cancelling or replaying a stale palm target.
        token = world.hand_follow.token
        model = world.motion_model()
        held=list(world.sim.q)
        for seq in range(25,130):
            result = await world.hand_follow_action("sample",dict(token=token,
                session="offline",seq=seq,u=.5,v=.5,palm_scale=1,angle=0,
                age_ms=0,hand_present=False),camera())
            self.assertTrue(result["ok"],result)
            world._tick(1/30,time.monotonic())
            await asyncio.sleep(1/30)
        self.assertTrue(world.hand_follow.active)
        self.assertEqual(world.hand_follow.token,token)
        self.assertNotEqual(world.sim.q,held)
        self.assertTrue(world.hand_follow.waiting)
        self.assertEqual(world.hand_follow.target,world.hand_follow._base)
        self.assertLess(math.dist(model.pose_vec(world.sim.q)[:3],[0,-400,700]),.2)
        result=await world.hand_follow_action("sample",dict(token=token,
            session="offline",seq=130,u=.35,v=.6,palm_scale=.2,angle=0,
            age_ms=0,hand_present=True),camera())
        self.assertTrue(result["ok"],result);self.assertFalse(world.hand_follow.waiting)
        self.assertEqual(world.hand_follow.token,token)

    async def test_point_readout_uses_shared_tcp_without_rewriting_saved_point(self):
        world=self.world
        for source in ("sim","real"):
            original=dict(id=1,code="P1",name="P1",source=source,joints=[0]*6,tcp={"xyz_mm":[1,2,3]})
            world.points.items=[original]
            projected=world.point_items()[0]
            self.assertEqual(projected["tcp"],world.motion_model().flange_pose([0]*6))
            self.assertEqual(original["tcp"]["xyz_mm"],[1,2,3])

    async def test_saved_sim_pose_restores_state_without_starting_any_trajectory(self):
        world=self.world;q=[-20,25,45,10,30,0]
        world.sim.q=q[:]
        with patch.object(config,'no_real',return_value=False):
            world._save_sim_pose();world.sim.q=[0]*6;world._restore_sim_pose()
        self.assertEqual(world.sim.q,q);self.assertIsNone(world.sim.traj)
        self.assertFalse(world.hand_follow.active);self.assertIsNone(world.real)
        path=config.DATA_DIR/'sim_pose.json'
        for bad in ([99999]*6,[0]*5,[False]*6,[float('nan')]*6):
            path.write_text(json.dumps({'version':1,'model':world.model.variant,'q':bad}),encoding='utf-8')
            with patch.object(config,'no_real',return_value=False):world._restore_sim_pose()
            self.assertEqual(world.sim.q,q)
        path.unlink()

    async def test_pause_resume_preserves_active_simulated_trajectory(self):
        world=self.world
        world.runner.load([dict(kind="movej",joints=[10,0,0,0,0,0],line=1,src="test")])
        world.runner.start(); world.sim.move_j([10,0,0,0,0,0])
        self.assertTrue((await world.pause_program())["ok"])
        q=list(world.sim.q); world.sim.tick(.2); self.assertEqual(q,world.sim.q)
        self.assertTrue((await world.resume_program())["ok"])
        world.sim.tick(.1); self.assertNotEqual(q,world.sim.q)


if __name__=="__main__":
    import faulthandler
    faulthandler.dump_traceback_later(40, exit=True)
    unittest.main(verbosity=2)
