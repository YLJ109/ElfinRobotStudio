"""Dedicated isolated simulation trajectory/ownership regressions. No real I/O."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import copy
import math
import json
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import config, planning
from backend.core.motion import MotionExecutor
from backend.core.robot_sim import SimRobot
from backend.core.trajectory import JointTrajectory, relative_route
from backend.core.world import World
from backend.core import world as world_module


class LinearModel:
    names = ['J'+str(i) for i in range(6)]
    lower, upper, vmax = [-1000.]*6, [1000.]*6, [90.]*6
    flange = 'tip'
    variant, tcp_translation_mm, tcp_rpy_deg = 'linear_fixture', [0.]*3, [0.]*3

    def pose_vec(self, q):
        return list(q)

    def ik(self, q, pose, *args, **kwargs):
        return list(pose), 0., 0., True

    def capsules(self, q, **kwargs):
        return [{'link':'tip', 'a':list(q[:3]), 'b':list(q[:3]), 'r_mm':.01}]

    def jacobian(self, q):
        return [[float(i == j) for i in range(6)] for j in range(6)]

    def flange_pose(self, q):
        return {'xyz_mm':list(q[:3])}


def fixture():
    model = LinearModel()
    settings = copy.deepcopy(config.DEFAULTS)
    settings.update(mode='sim', sim_speed_pct=100, vel_source='urdf', ground_enabled=False)
    settings['fence']['enabled'] = False
    context = {'mode':'sim', 'real_connected':False, 'revision':1, 'tool':'fixture', 'ucs':'base'}
    sim = SimRobot(model, model.vmax)
    sim.speed_pct = 100
    return model, settings, context, sim


def prepared(context=None, target=None):
    model, settings, original, sim = fixture()
    plan = planning.prepare_movel(model, sim.q, target or [32.,0.,0.,0.,0.,0.], settings,
                                 sim.lower, sim.upper, [], context=context or original)
    return plan, sim, original


class TrajectoryTests(unittest.TestCase):
    def _assert_q_close(self,actual,expected,tol=1e-9):
        self.assertEqual(len(actual),len(expected))
        for a,b in zip(actual,expected):
            self.assertAlmostEqual(a,b,delta=tol)

    def test_strict_audit_does_not_pretend_world_or_hardware_equivalence(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('check_motion_contract.py')),'--strict'],
                              capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,2,result.stderr)
        report=json.loads(result.stdout)
        self.assertFalse(report['hardware_validated'])
        self.assertFalse(report['trajectory_equivalence'])
        self.assertTrue(report['world_wiring_completed'])
        self.assertTrue(report['continuous_movel_core_available'])
        mc03=next(f for f in report['findings'] if f['id']=='MC03')
        self.assertTrue(mc03['continuous_simulation_wired'])
        self.assertFalse(mc03['segmented_stops_remain'])

    def test_explicit_route_matches_adapter_ties_tolerance_and_stop_order(self):
        target = [20.,10.,0.,0.,0.,0.]
        self.assertEqual(relative_route([0.]*6, target),
                         [[0.]*6, [10.,0.,0.,0.,0.,0.], [20.,0.,0.,0.,0.,0.], target])
        self.assertEqual(len(relative_route([0.]*6, [.04]*6)), 1)
        with self.assertRaises(ValueError): relative_route([0.]*6, target, chunk=0)
        with self.assertRaises(ValueError): relative_route([0.]*6, target, max_chunks=2)

    def test_c1_internal_knots_no_unnecessary_stops_and_no_overshoot(self):
        curve = JointTrajectory.build([[0.]*6,[10.,2.,0.,0.,0.,0.],[20.,1.,0.,0.,0.,0.]], [90.]*6, 100)
        t = curve.times[1]
        before, at, after = [curve.at_path_time(t+x) for x in (-1e-7,0,1e-7)]
        for i in range(6):
            self.assertAlmostEqual((at[i]-before[i])/1e-7, (after[i]-at[i])/1e-7, delta=.01)
        self.assertGreater(curve.tangents[1][0],0)
        self.assertEqual(curve.tangents[1][1],0)  # physical reversal, not hidden dwell
        for k, (a,b) in enumerate(zip(curve.points, curve.points[1:])):
            for j in range(101):
                q = curve.at_path_time(curve.times[k]+(curve.times[k+1]-curve.times[k])*j/100)
                for v,x,y in zip(q,a,b): self.assertTrue(min(x,y)-1e-10 <= v <= max(x,y)+1e-10)

    def test_numeric_speed_bound_and_sample_spacing(self):
        vmax = [90.,60.,30.,20.,100.,70.]
        curve = JointTrajectory.build([[0.]*6,[10.,-3.,20.,0.,0.,0.],[30.,-4.,22.,10.,0.,0.]],vmax,35)
        dt, previous = curve.duration/3000, curve.at(0)
        for k in range(1,3001):
            q = curve.at(k*dt)
            for i in range(6): self.assertLessEqual(abs(q[i]-previous[i])/dt,vmax[i]*.35+1e-6)
            previous=q
        samples=curve.inspection_samples()
        for a,b in zip(samples,samples[1:]): self.assertLessEqual(max(abs(x-y) for x,y in zip(a,b)),1+1e-9)
        with self.assertRaises(ValueError): curve.inspection_samples(max_samples=2)

    def test_rejects_invalid_inputs_and_removes_only_exact_duplicates(self):
        for bad in (float('nan'),float('inf'),True):
            with self.assertRaises(ValueError): JointTrajectory.build([[bad]*6],[90.]*6,100)
        for bad in (0,-1,101,float('nan'),True):
            with self.assertRaises(ValueError): JointTrajectory.build([[0.]*6],[90.]*6,bad)
        for bad in (0,-1,float('inf')):
            with self.assertRaises(ValueError): JointTrajectory.build([[0.]*6],[bad]*6,10)
        curve=JointTrajectory.build([[0.]*6,[0.]*6,[.01]*6],[90.]*6,100)
        self.assertEqual(len(curve.points),2)

    def test_prepared_is_immutable_and_report_does_not_alias(self):
        plan,sim,_=prepared()
        self.assertFalse(plan.report['risk_detected'])
        self.assertTrue(plan.report['continuous_simulation'])
        self.assertFalse(plan.report['controller_path_equivalent'])
        report=plan.report
        report['risk_detected']=True
        self.assertFalse(plan.report['risk_detected'])
        with self.assertRaises(AttributeError): plan.trajectory.duration=0
        self.assertEqual(sim.q,[0.]*6)

    def test_original_knot_checks_and_actual_curve_checks_are_both_retained(self):
        model,settings,context,sim=fixture()
        actual=planning.evaluate
        visited=[]
        def reject_curve(m,q,*args,**kwargs):
            visited.append(q[0])
            if 3 < q[0] < 5:
                return {'level':'red','reasons':['curve interior'], 'max_pct':95.,'collisions':[]}
            return actual(m,q,*args,**kwargs)
        with patch.object(planning,'evaluate',side_effect=reject_curve):
            with self.assertRaisesRegex(RuntimeError,'连续曲线'):
                planning.prepare_movel(model,sim.q,[32.,0.,0.,0.,0.,0.],settings,sim.lower,sim.upper,[],context=context)
        # 仿真 MOVEL 的结节点间距是 15mm / 3°（真机 8mm / 2°）：32mm 的目标
        # 对应 knots 0 / 10.67 / 21.33 / 32。结节点评估必须保留，
        # 曲线采样评估也必须同时存在（后者额外覆盖结点之间的姿态）。
        self.assertIn(32/3,visited) # original IK-knot evaluation retained
        self.assertTrue(any(3<x<5 for x in visited))
        self.assertIsNone(sim.traj)

    def test_objects_between_ik_knots_are_rejected(self):
        model,settings,context,sim=fixture()
        obj={'kind':'sphere','size':[1.], 'xyz':[4.,0.,0.], 'name':'obstacle','collide':True}
        with self.assertRaisesRegex(RuntimeError,'连续曲线'):
            planning.prepare_movel(model,sim.q,[32.,0.,0.,0.,0.,0.],settings,sim.lower,sim.upper,[obj],context=context)

    def test_real_mode_connection_ik_failure_and_cancel_cannot_prepare(self):
        model,settings,context,sim=fixture()
        for key,value in (('mode','real'),('real_connected',True)):
            bad=dict(context);bad[key]=value
            with self.assertRaises(ValueError): planning.prepare_movel(model,sim.q,[32.,0.,0.,0.,0.,0.],settings,sim.lower,sim.upper,[],context=bad)
        with patch.object(model,'ik',return_value=([0.]*6,100.,90.,False)):
            with self.assertRaisesRegex(RuntimeError,'逆解未收敛'): planning.prepare_movel(model,sim.q,[32.,0.,0.,0.,0.,0.],settings,sim.lower,sim.upper,[],context=context)
        def cancel(): raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): planning.prepare_movel(model,sim.q,[32.,0.,0.,0.,0.,0.],settings,sim.lower,sim.upper,[],context=context,check=cancel)

    def test_stale_start_limits_and_velocity_rejected_without_mutation(self):
        for change in ('q','lower','vmax'):
            plan,sim,_=prepared()
            getattr(sim,change)[0]+=.01
            with self.assertRaises(ValueError):sim.move_prepared(plan)
            self.assertIsNone(sim.traj)

    def test_simulation_defaults_to_full_speed(self):
        self.assertEqual(config.DEFAULTS['sim_speed_pct'],100)
        self.assertEqual(SimRobot(LinearModel(),LinearModel.vmax).speed_pct,100)
        # 真机冷启显示目标由用户指定为 10%；专用任务的 ≤5% 门槛另行保留。
        self.assertEqual(config.DEFAULTS['real_speed_pct'],10)

    def test_speed_changed_during_preparation_preserves_curve_and_scales_time(self):
        model,settings,context,sim=fixture()
        settings['sim_speed_pct']=10
        plan=planning.prepare_movel(model,sim.q,[32.,0.,0.,0.,0.,0.],settings,
                                   sim.lower,sim.upper,[],context=context)
        sim.speed_pct=100
        sim.move_prepared(plan)
        self.assertIs(sim.traj,plan.trajectory)
        sim.tick(plan.trajectory.duration*.025)
        # 逐分量近似比较：_elapsed 是累加出来的，duration 一变舍入路径就不同，
        # 用精确相等会把 1e-15 的浮点噪声当成功能错误。
        self._assert_q_close(sim.q,plan.trajectory.at(plan.trajectory.duration*.25))
        sim.speed_pct=50
        sim.tick(plan.trajectory.duration*.05)
        self._assert_q_close(sim.q,plan.trajectory.at(plan.trajectory.duration*.5))
        sim.tick(plan.trajectory.duration)
        self.assertEqual(sim.q,list(plan.trajectory.points[-1]))

    def test_pause_override_stop_progress_and_single_final_arrival(self):
        plan,sim,_=prepared()
        sim.move_prepared(plan)
        sim.tick(plan.trajectory.duration*.2)
        q=sim.q[:];progress=sim.progress()
        sim.paused=True;sim.tick(100)
        self.assertEqual(sim.q,q)
        sim.paused=False;sim.speed_pct=0;sim.tick(100)
        self.assertEqual(sim.progress(),progress)
        sim.speed_pct=50
        self.assertGreater(sim.remaining(),0)
        sim.tick(plan.trajectory.duration*2)
        self.assertEqual(sim.q,list(plan.trajectory.points[-1]))
        self.assertEqual(sim.progress(),1)
        self.assertEqual(sim.remaining(),0)
        sim.move_j([0.]*6,contract='legacy_relj');sim.stop()
        self.assertIsNone(sim.traj)

    def test_invalid_tick_cannot_mutate_a_prepared_curve(self):
        for dt,speed in ((-1,100),(math.nan,100),(math.inf,100),(.1,math.inf),(.1,math.nan)):
            plan,sim,_=prepared()
            sim.move_prepared(plan)
            sim.speed_pct=speed
            before=sim.q[:]
            with self.assertRaises(ValueError):sim.tick(dt)
            self.assertEqual(sim.q,before)
            self.assertEqual(sim.progress(),0)

    def test_preflight_and_execution_share_explicit_route(self):
        model,settings,context,sim=fixture()
        settings['sim_path_contract']='legacy_relj'
        target=[20.,10.,0.,0.,0.,0.]
        report=planning.inspect_path(model,sim.q,target,settings,sim.lower,sim.upper,[])
        sim.move_j(target,contract=settings['sim_path_contract'])
        self.assertEqual(report['waypoints'],[list(p) for p in sim.traj.points])
        self.assertAlmostEqual(report['duration_estimate_s'],sim.traj.duration,delta=.0051)
        for bad in ('waypoint','servo','typo'):
            with self.assertRaises(ValueError):sim.move_j(target,contract=bad)

    def test_sim_path_crossing_joint_95_percent_is_not_a_collision(self):
        model,settings,_,_=fixture()
        lower,upper=[-360.]*6,[360.]*6
        start,target=[-270.,0.,0.,0.,0.,0.],[-359.9,0.,0.,0.,0.,0.]
        report=planning.inspect_path(model,start,target,settings,lower,upper,[])
        self.assertFalse(report['risk_detected'],report['issues'])
        self.assertGreaterEqual(report['max_limit_pct'],95.)
        outside=planning.inspect_path(model,start,[-360.01,0.,0.,0.,0.,0.],settings,lower,upper,[])
        self.assertTrue(outside['risk_detected'])
        settings['mode']='real'
        strict=planning.inspect_path(model,start,target,settings,lower,upper,[])
        self.assertTrue(strict['risk_detected'],'真机仍沿用原有严格预检')

    def test_prepared_sink_cannot_take_over_existing_motion(self):
        plan,sim,_=prepared()
        sim.move_j([10.]*6)
        old=sim.traj
        with self.assertRaisesRegex(ValueError,'所有者'):sim.move_prepared(plan)
        self.assertIs(sim.traj,old)


class OwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_submission_and_context_rechecked_until_completion(self):
        plan,sim,context=prepared()
        executor=MotionExecutor(lambda *args:None)
        calls=[]
        def validate(p):
            calls.append(p.context_signature)
            if planning.digest(context)!=p.context_signature:raise RuntimeError('stale')
        task=asyncio.create_task(executor.execute_prepared_sim(sim,plan,0,validate_context=validate))
        await asyncio.sleep(.005)
        curve=sim.traj
        for _ in range(10):
            sim.tick(plan.trajectory.duration/10)
            self.assertTrue(sim.traj is curve or sim.traj is None)
        sim.tick(.001)
        await task
        self.assertGreaterEqual(len(calls),3)

    async def test_context_change_stops_owned_curve(self):
        plan,sim,context=prepared()
        executor=MotionExecutor(lambda *args:None)
        def validate(p):
            if planning.digest(context)!=p.context_signature:raise RuntimeError('stale')
        task=asyncio.create_task(executor.execute_prepared_sim(sim,plan,0,validate_context=validate))
        await asyncio.sleep(.005)
        context['revision']=2
        with self.assertRaisesRegex(RuntimeError,'stale'):await task
        self.assertIsNone(sim.traj)

    async def test_revoke_and_external_stop_do_not_report_success(self):
        for operation in ('revoke','stop'):
            plan,sim,_=prepared()
            executor=MotionExecutor(lambda *args:None)
            task=asyncio.create_task(executor.execute_prepared_sim(sim,plan,0,validate_context=lambda p:None))
            await asyncio.sleep(.005)
            if operation=='revoke':executor.revoke('test')
            else:sim.stop()
            with self.assertRaises((asyncio.CancelledError,RuntimeError)):await task
            self.assertIsNone(sim.traj)

    async def test_replacement_is_not_stopped_by_stale_owner(self):
        plan,sim,_=prepared()
        executor=MotionExecutor(lambda *args:None)
        task=asyncio.create_task(executor.execute_prepared_sim(sim,plan,0,validate_context=lambda p:None))
        await asyncio.sleep(.005)
        sim.stop()
        sim.move_j([10.]*6)
        replacement=sim.traj
        with self.assertRaisesRegex(RuntimeError,'替换'):await task
        self.assertIs(sim.traj,replacement)


def world_fixture():
    # The guard at file entry guarantees this World import/fixture is only in
    # the disposable copy. Never call World() or touch source checkout stores.
    model,settings,context,sim=fixture()
    w=World.__new__(World)
    w.settings,w.sim,w.real=settings,sim,None
    w.model=model
    w.motion_model=lambda:model
    w.current_joints=lambda:list(sim.q)
    w.limits=lambda:(sim.lower,sim.upper)
    w.object_items=[]
    w.objects=type('Objects',(),{'list':lambda self:copy.deepcopy(w.object_items)})()
    w.points=type('Points',(),{'list':lambda self:[]})()
    w.calibrations=type('Calibrations',(),{'items':{'version':1}})()
    w.tools=type('Tools',(),{'mounted':{}})()
    w.events=[]
    w.log=type('Log',(),{'add':lambda self,*args,**kwargs:w.events.append(args)})()
    w.executor=MotionExecutor(lambda *args:w.events.append(args))
    return w


class WorldWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_world_speed_slider_during_preflight_does_not_invalidate_sim_path(self):
        w=world_fixture();w.settings['sim_speed_pct']=10;w.sim.speed_pct=10;w._cfg_rev=7
        w._save_preferences=lambda patch:{**w.settings,**patch}
        w._real_jog_conflict=lambda reason:False
        original=world_module.prepare_movel
        def prepare_then_adjust(*args,**kwargs):
            result=original(*args,**kwargs)
            w.set_speed(100,schedule=False)
            return result
        with patch.object(world_module,'prepare_movel',side_effect=prepare_then_adjust):
            task=asyncio.create_task(w._execute_movel([32.,0.,0.,0.,0.,0.],'speed slider',0))
            try:
                await self.wait_curve(w,task)
                self.assertEqual(w._cfg_rev,7)
                self.assertEqual(w.sim.speed_pct,100)
                w.sim.tick(w.sim.traj.duration*.1+.001)
                await asyncio.wait_for(task,3)
                self.assertEqual(w.sim.q,[32.,0.,0.,0.,0.,0.])
            finally:
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def wait_curve(self,w,task):
        async def wait():
            while w.sim.traj is None:
                if task.done():
                    await task
                    self.fail('World returned without a continuous trajectory')
                await asyncio.sleep(.001)
        await asyncio.wait_for(wait(),3)

    async def test_actual_world_submits_one_prepared_curve_without_per_knot_moves(self):
        w=world_fixture()
        with patch.object(w.sim,'move_j',side_effect=AssertionError('per-knot move forbidden')), \
             patch.object(w.sim,'move_prepared',wraps=w.sim.move_prepared) as start, \
             patch.object(w.executor,'execute_prepared_sim',wraps=w.executor.execute_prepared_sim) as execute:
            task=asyncio.create_task(w._execute_movel([32.,0.,0.,0.,0.,0.],'world wiring',0))
            try:
                await self.wait_curve(w,task)
                owned=w.sim.traj
                # 32mm 目标按仿真 15mm 结节点间距 → 4 个节点（含首末）。
                self.assertEqual(len(owned.points),4)
                for _ in range(20):
                    w.sim.tick(owned.duration/20)
                w.sim.tick(.001)
                await asyncio.wait_for(task,3)
                self.assertEqual(execute.await_count,1)
                self.assertEqual(start.call_count,1)
                self.assertIs(start.call_args.args[0].trajectory,owned)
                self.assertEqual(w.sim.q,[32.,0.,0.,0.,0.,0.])
                self.assertEqual(sum('MOVEL 仿真完成' in str(e) for e in w.events),1)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)

    async def test_actual_world_context_change_in_flight_stops_curve_and_never_logs_completion(self):
        for change in ('scene','tool','limits','calibration','real_connection','mode'):
            with self.subTest(change=change):
                w=world_fixture()
                task=asyncio.create_task(w._execute_movel([32.,0.,0.,0.,0.,0.],'world context',0))
                try:
                    await self.wait_curve(w,task)
                    w.sim.tick(w.sim.traj.duration*.25)
                    before=w.sim.q[:]
                    if change=='scene':w.object_items.append({'name':'new object'})
                    elif change=='tool':w.tools.mounted={'tool_id':'changed','revision':2}
                    elif change=='limits':w.settings['custom_limits']={'changed':True}
                    elif change=='calibration':w.calibrations.items['version']=2
                    elif change=='real_connection':w.real=object()
                    else:w.settings['mode']='real'
                    with self.assertRaisesRegex(RuntimeError,'变化'):
                        await asyncio.wait_for(task,3)
                    self.assertIsNone(w.sim.traj)
                    self.assertEqual(w.sim.q,before)
                    self.assertFalse(any('MOVEL 仿真完成' in str(e) for e in w.events))
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task,return_exceptions=True)

    async def test_actual_world_rejects_context_changed_during_threaded_preflight(self):
        w=world_fixture()
        original=world_module.prepare_movel
        def prepare_then_change(*args,**kwargs):
            result=original(*args,**kwargs)
            w.calibrations.items['version']+=1
            return result
        with patch.object(world_module,'prepare_movel',side_effect=prepare_then_change), \
             patch.object(w.sim,'move_prepared',wraps=w.sim.move_prepared) as start:
            with self.assertRaisesRegex(RuntimeError,'变化'):
                await asyncio.wait_for(w._execute_movel([32.,0.,0.,0.,0.,0.],'stale preflight',0),3)
            start.assert_not_called()
            self.assertIsNone(w.sim.traj)
            self.assertEqual(w.sim.q,[0.]*6)

    async def test_actual_world_rejects_real_mode_and_connection_before_preparation(self):
        for mode,real in (('real',None),('sim',object())):
            w=world_fixture();w.settings['mode']=mode;w.real=real
            with patch.object(world_module,'prepare_movel',side_effect=AssertionError('must not plan')) as prepare:
                with self.assertRaises(RuntimeError):
                    await w._execute_movel([32.,0.,0.,0.,0.,0.],'forbidden',0)
                prepare.assert_not_called()
                self.assertIsNone(w.sim.traj)


if __name__=='__main__':unittest.main(verbosity=2)
