"""Drawing geometry, immutable tickets, real qualification and the actual Runner."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import copy
import math
import json
import time
import threading
import os
import uuid
from pathlib import Path
import unittest
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
from backend.core import config
from backend.core.world import World
from backend.core.program import Runner
from backend.application.drawing import Drawing
from backend.application.drawing_geometry import configuration,poses,line_distance,trace_metadata
from backend.core.planning import inspect_path as real_inspect
from backend.core.kinematics import rpy_to_mat,pose_rotation_distance
from backend.application.drawing_geometry import bounds,check_bounds

def options(**changes):
    return dict(shape='rectangle',center=[400,0,450,180,0,0],plane='xy',width_mm=8,height_mm=6,angle_deg=0,speed_pct=100,repeats=1,**changes) if not changes else {**options(),**changes}

class Geometry(unittest.TestCase):
    def test_direction_rotates_actual_path_and_range_together(self):
        for direction in (0,90,180,270):
            p=configuration(options(shape='rectangle',plane='xz',center=[-500,0,700,90,0,0],range_center_mm=[-500,0,700],range_size_mm=[900,300,500],width_mm=280,height_mm=240,direction=direction),'sim')
            box=bounds(p);path=poses(p)
            for pose in path:check_bounds(pose[:3],box)
            expected=[300,900,500] if direction in (90,270) else [900,300,500]
            self.assertEqual([b-a for a,b in zip(box['min'],box['max'])],expected)
            self.assertEqual(path[0],path[-1])
            self.assertEqual(path[0],p['center'])
        for direction in (True,45,float('nan')):
            with self.assertRaises(ValueError):configuration(options(direction=direction),'sim')
    def test_all_shapes_close_at_center_and_consume_dimensions(self):
        for shape in ('heart','square','rectangle','circle','nod','shake'):
            p=configuration(options(shape=shape,angle_deg=2 if shape in ('nod','shake') else 0),'sim');rows=poses(p)
            self.assertEqual(rows[0],p['center']);self.assertEqual(rows[-1],p['center']);self.assertLessEqual(len(rows),104)
            self.assertTrue(any(row!=rows[0] for row in rows));self.assertTrue(all(len(row)==6 and all(math.isfinite(v) for v in row) for row in rows))
            if shape in ('nod','shake'):self.assertEqual({tuple(row[:3]) for row in rows},{tuple(p['center'][:3])})
        self.assertNotEqual(poses(configuration(options(width_mm=10),'sim')),poses(configuration(options(width_mm=20),'sim')))
        self.assertEqual(line_distance([0,2,0],[0,0,0],[1,0,0]),2)
    def test_bounds_boolean_nan_injection_and_unused_parameters(self):
        for change in ({'width_mm':501},{'repeats':True},{'center':[True]*6},{'speed_pct':float('nan')},{'shape':'MOVEJ 0'},{'plane':'bad'},{'shape':'nod','angle_deg':0},{'source':'WAIT 1'}):
            with self.assertRaises(ValueError):configuration(options(**change),'sim')
        for change in ({'width_mm':51},{'speed_pct':6},{'shape':'shake','angle_deg':6}):
            with self.assertRaises(ValueError):configuration(options(**change),'real')
        self.assertEqual(configuration(options(shape='circle',height_mm=12),'sim')['height_mm'],8)
        self.assertEqual(configuration(options(shape='nod',angle_deg=-2,plane='yz'),'sim')['plane'],'xy')
        self.assertEqual(configuration(options(speed_pct=12.345),'sim')['speed_pct'],12.3,'plan speed must match the existing 0.1-percent actuator setting')
    def test_trace_outline_excludes_approach_return_and_marks_each_repeat(self):
        for shape in ('heart','circle','square','rectangle'):
            p=configuration(options(shape=shape,repeats=3),'sim');path=poses(p);outline,flags=trace_metadata(p,path)
            self.assertEqual(len(flags),len(path));self.assertLess(math.dist(outline[0],outline[-1]),1e-8)
            period=(len(path)-1)//3
            self.assertFalse(flags[0])
            for r in range(3):
                self.assertFalse(flags[1+r*period]);self.assertFalse(flags[(r+1)*period])
                self.assertTrue(all(flags[2+r*period:(r+1)*period]))
            self.assertTrue(all(pose!=p['center'] for pose in outline))
    def test_range_bounds_reserve_two_mm_and_reject_nonfinite_or_unbounded_sizes(self):
        p=configuration(options(shape='heart',plane='xz',width_mm=180,height_mm=140),'sim');box=bounds(p)
        self.assertEqual(p['range_size_mm'],[220,80,220]);self.assertEqual(box['min'],[290,-40,340])
        for target in poses(p):check_bounds(target[:3],box)
        for change in ({'range_size_mm':[4,80,220]},{'range_size_mm':[True,80,220]},{'range_size_mm':[220,float('nan'),220]},{'range_size_mm':[5001,80,220]}):
            with self.assertRaises(ValueError):configuration(options(**change),'sim')
        with self.assertRaises(ValueError):check_bounds([box['max'][0]-1,0,450],box)
        with self.assertRaises(ValueError):configuration(options(range_size_mm=[51,20,20]),'real')
        with self.assertRaises(ValueError):configuration(options(speed_pct=5,center_policy='range_lower'),'real')
        with self.assertRaises(ValueError):configuration(options(range_center_mm=[True,0,0]),'sim')

class GuardLifecycle(unittest.TestCase):
    def test_guard_failure_load_stop_and_terminal_preserve_journal_observer(self):
        r=Runner();events=[];r.event_sink=lambda name,*args,**kwargs:events.append(name)
        step={'kind':'movej','joints':[0]*6,'line':1,'src':'MOVEJ 0,0,0,0,0,0'}
        r.execution_guard=lambda:None;r.load([step]);self.assertIsNone(r.execution_guard)
        r.start();r.execution_guard=lambda:(_ for _ in ()).throw(RuntimeError('expired lease'))
        self.assertIsNone(r.advance(time.monotonic()));self.assertEqual(r.state,'aborted');self.assertIn('expired lease',r.error)
        self.assertIsNone(r.execution_guard);self.assertIn('terminal',events)
        r.load([step]);r.start();r.execution_guard=lambda:None
        self.assertIs(r.advance(time.monotonic()),step);r.complete();r.advance(time.monotonic())
        self.assertEqual(r.state,'finished');self.assertIsNone(r.execution_guard)
        r.execution_guard=lambda:None;r.stop();self.assertIsNone(r.execution_guard)

class DrawingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        p=patch.object(config,'_settings',copy.deepcopy(config.DEFAULTS));p.start();self.addCleanup(p.stop)
        self.w=World(data_dir=Path(os.environ['ELFIN_DATA_DIR'])/uuid.uuid4().hex);self.w.settings['mode']='sim';self.d=Drawing(self.w);self.now=10.
    async def asyncTearDown(self):
        await self.d.close();await self.w.stop_execution('isolated drawing check end')
    async def ready(self,payload=None):
        state=self.d.preview(payload or options());self.assertEqual(state['phase'],'checking');self.assertTrue(state['plan_id'])
        await asyncio.wait_for(self.d.worker,95)
        self.assertEqual(self.d.phase,'ready',self.d.why)
        return self.d.state()
    async def pump(self,predicate,seconds=20):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            self.w._tick(.05,time.monotonic());await asyncio.sleep(.003)
            if predicate():return
        self.fail(str(self.d.state()))
    async def test_actual_full_simulation_returns_center_and_releases_guard(self):
        state=await self.ready(options(speed_pct=8));before=self.w.current_joints()[:]
        self.w.set_speed(100,schedule=False)
        self.assertTrue(state['program'].startswith('MOVEJ '));self.assertLessEqual(len(self.d.plan['steps']),256)
        steps=self.d.plan['steps'];self.assertFalse(steps[0]['drawing_trace']);self.assertFalse(steps[-1]['drawing_trace'])
        self.assertTrue(any(s['drawing_trace'] for s in steps));self.assertTrue(all(s['drawing_plan_id']==state['plan_id'] for s in steps))
        self.assertEqual(self.w.runner.state,'idle');self.assertEqual(self.w.current_joints(),before)
        self.assertEqual(self.d.run({'plan_id':state['plan_id']})['phase'],'running')
        self.assertEqual(self.w.settings['sim_speed_pct'],100,'old preview speed must not overwrite the top bar')
        with self.assertRaises(ValueError):self.d.run({'plan_id':state['plan_id']})
        # A simulation override changes time without invalidating this path.
        self.w.set_speed(50,schedule=False)
        self.d._guard()
        self.assertEqual(self.w.settings['sim_speed_pct'],50)
        self.w.set_speed(100,schedule=False)
        self.d._guard()
        await self.pump(lambda:self.d.state()['phase']=='completed',30)
        self.assertEqual(self.w.settings['sim_speed_pct'],100)
        pose=self.d._tool()[0].pose_vec(self.w.current_joints())
        self.assertLess(math.dist(pose[:3],options()['center'][:3]),.2)
        self.assertIsNone(self.w.runner.execution_guard);self.assertFalse(self.w.executor.busy)
    async def test_idle_binding_is_actual_left_tip_and_changes_with_formal_tool_dimensions(self):
        from backend.core.kinematics import rpy_to_mat
        self.w.sim.q=[20,-30,60,10,40,-20]
        first=self.d.state()['trace_tcp'];self.assertTrue(first['valid']);self.assertEqual(first['kind'],'left_cup_tip')
        from backend.core.sim_tool import DEFAULT_TOOL
        self.w.settings['sim_tool']={**copy.deepcopy(DEFAULT_TOOL),'rotation':[13,-8,51]}
        self.w.settings['sim_tool']['dimensions'].update(length=280,edge=20,mountHeight=40,cupHeight=30)
        trace=self.d.state()['trace_tcp'];self.assertNotEqual(trace['binding_id'],first['binding_id'])
        base=self.w.motion_model();reference=base.reference_tool;d=reference['dimensions'];rotation=rpy_to_mat(*map(math.radians,reference['rotation']))
        local=[-(d['length']/2-d['edge']),0,d['mountHeight']+d['thickness']+d['cupHeight']]
        flange=base.fk(self.w.current_joints())[base.flange]
        rotated=[sum(rotation[i][j]*local[j] for j in range(3)) for i in range(3)]
        expected=[flange[i][3]*1000+sum(flange[i][j]*rotated[j] for j in range(3)) for i in range(3)]
        self.assertLess(math.dist(trace['pose'][:3],expected),1e-8)
        self.assertEqual(trace['offset_in_snapshot_tcp_mm'],[-120,0,35])
    async def test_default_heart_xz_80x60_from_home_preflight_and_actual_runner(self):
        await self.actual_heart(80,60)
    async def test_user_front_range_is_unchanged_and_large_six_shapes_execute_at_checked_tool_tip(self):
        for shape in ('heart','square','rectangle','circle','nod','shake'):
            before=self.w.current_joints()[:]
            state=await self.ready(options(shape=shape,center=[-500,0,700,90,0,0],plane='xz',width_mm=280,height_mm=240,
                range_center_mm=[-500,0,700],range_size_mm=[900,300,500],center_policy='range_lower',angle_deg=3 if shape in ('nod','shake') else 0))
            print('DRAWING_USER_RANGE',shape,json.dumps({'center':state['config']['center'],'commands':len(self.d.plan['steps']),'bounds':state['bounds']}),flush=True)
            if shape not in ('nod','shake'):
                print('DRAWING_CONTINUOUS',shape,json.dumps(self.d.plan['continuous_metrics']),flush=True)
                self.assertEqual(len(state['program'].splitlines()),self.d.plan['unmerged_step_count'])
            self.assertEqual(state['bounds']['min'],[-950,-150,450]);self.assertEqual(state['bounds']['max'],[-50,150,950])
            self.assertEqual(state['config']['range_center_mm'],[-500,0,700]);self.assertEqual(self.w.current_joints(),before)
            self.assertLessEqual(self.d.plan['max_tcp_deviation_mm'],2)
            self.d.run({'plan_id':state['plan_id']});await self.pump(lambda:self.d.state()['phase']=='completed',60)
            actual=self.d._tool()[0].pose_vec(self.w.current_joints())
            self.assertLess(math.dist(actual[:3],state['config']['center'][:3]),.2)
            from backend.core.kinematics import pose_rotation_distance
            self.assertLess(pose_rotation_distance(actual,state['config']['center']),.1)
            self.assertEqual(self.w.runner.completed_index,len(self.w.runner.steps));self.assertIsNone(self.w.sim.traj)
    async def test_large_front_heart_with_pen_from_home_and_writing_end(self):
        self.assertTrue(self.w.set_sim_tool({'kind':'pen'})['ok'])
        model=self.w.motion_model();lo,hi=self.w.limits()
        end,ep,er,ok=model.ik([0]*6,[-500,0,61,180,0,0],lo,hi,iters=160,max_restarts=2)
        self.assertTrue(ok,(ep,er))
        failures=[]
        for label,start in (('home',[0]*6),('writing_end',end)):
            self.w.sim.q=list(start);before=self.w.current_joints()[:]
            self.d.preview(options(shape='heart',center=[-500,0,700,90,0,0],plane='xz',width_mm=280,height_mm=240,
                range_center_mm=[-500,0,700],range_size_mm=[900,300,500],direction=270,
                center_policy='range_lower',angle_deg=0,speed_pct=100))
            await asyncio.wait_for(self.d.worker,95)
            print('USER_HEART_PEN',label,json.dumps({'phase':self.d.phase,'why':self.d.why,'start':start}),flush=True)
            self.assertEqual(self.w.current_joints(),before)
            if self.d.phase!='ready':failures.append((label,self.d.why))
            elif label=='writing_end':
                self.assertTrue(self.d.plan['prepared_strokes'])
                self.assertLess(len(self.d.plan['steps']),self.d.plan['unmerged_step_count'])
                print('USER_HEART_CONTINUOUS',json.dumps({'before':self.d.plan['unmerged_step_count'],'after':len(self.d.plan['steps']),**self.d.plan['continuous_metrics']}),flush=True)
                self.d.run({'plan_id':self.d.plan['id']})
                await self.pump(lambda:self.w.runner.current() and 'drawing_continuous' in self.w.runner.current() and self.w.sim.traj)
                self.assertTrue((await self.d.pause({'plan_id':self.d.plan['id']}))['ok'])
                frozen=self.w.current_joints()[:];owned=self.w.sim.traj
                for _ in range(4):self.w._tick(.025,time.monotonic());await asyncio.sleep(.005)
                self.assertEqual(self.w.current_joints(),frozen);self.assertIs(self.w.sim.traj,owned)
                self.assertTrue((await self.d.resume({'plan_id':self.d.plan['id']}))['ok'])
                await self.pump(lambda:self.d.state()['phase']=='completed',60)
                self.assertEqual(self.w.runner.completed_index,len(self.w.runner.steps))
                self.assertIsNone(self.w.sim.traj)
        self.assertEqual(failures,[])
    async def test_cancel_continuous_plane_curve_has_no_late_motion(self):
        state=await self.ready()
        self.assertTrue(self.d.plan['prepared_strokes'])
        self.d.run({'plan_id':state['plan_id']})
        await self.pump(lambda:self.w.runner.current() and 'drawing_continuous' in self.w.runner.current() and self.w.sim.traj)
        self.assertEqual((await self.d.cancel({'plan_id':state['plan_id']}))['phase'],'cancelled')
        self.assertIsNone(self.w.sim.traj);self.assertFalse(self.w.executor.busy)
        frozen=self.w.current_joints()[:]
        for _ in range(4):self.w._tick(.025,time.monotonic());await asyncio.sleep(.005)
        self.assertEqual(self.w.current_joints(),frozen)
    async def test_front_facing_pen_axis_and_large_heart_actual_execution(self):
        self.assertTrue(self.w.ensure_sim_task_tool('drawing')['ok'])
        model=self.w.motion_model();lo,hi=self.w.limits()
        end,ep,er,ok=model.ik([0]*6,[-500,0,61,180,0,0],lo,hi,iters=160,max_restarts=2)
        self.assertTrue(ok,(ep,er));self.w.sim.q=list(end)
        before=self.w.current_joints()[:]
        state=await self.ready(options(shape='heart',center=[-500,0,700,90,0,-90],plane='xz',width_mm=280,height_mm=240,
            range_center_mm=[-500,0,700],range_size_mm=[900,300,500],direction=270,
            center_policy='range_lower',angle_deg=0,speed_pct=100))
        print('USER_HEART_FRONT_PEN',json.dumps({'phase':state['phase'],'center':state['config']['center'],
            'before':self.d.plan['unmerged_step_count'],'after':len(self.d.plan['steps']),**self.d.plan['continuous_metrics']}),flush=True)
        self.assertEqual(self.w.current_joints(),before)
        rotation=rpy_to_mat(*map(math.radians,state['config']['center'][3:]))
        self.assertLess(math.dist([rotation[i][2] for i in range(3)],[-1,0,0]),1e-8)
        self.assertEqual(state['bounds']['min'],[-650,-450,450]);self.assertEqual(state['bounds']['max'],[-350,450,950])
        self.assertLessEqual(self.d.plan['max_tcp_deviation_mm'],2)
        self.d.run({'plan_id':state['plan_id']});await self.pump(lambda:self.d.state()['phase']=='completed',60)
        actual=self.d._tool()[0].pose_vec(self.w.current_joints())
        self.assertLess(math.dist(actual[:3],state['config']['center'][:3]),.2)
        self.assertLess(pose_rotation_distance(actual,state['config']['center']),.1)
        self.assertEqual(self.w.runner.completed_index,len(self.w.runner.steps));self.assertIsNone(self.w.sim.traj)
        history=self.d.state()['actual_samples'];self.assertTrue(history)
        self.assertTrue(all(sample['stroke']==0 for sample in history))
    async def test_default_heart_xz_180x140_left_cup_from_home_actual_runner(self):
        await self.actual_heart(180,140)
    async def test_default_large_square_and_circle_after_actual_heart_same_tool_tip(self):
        await self.actual_heart(180,140,center=[350,0,400,180,0,0])
        for shape in ('square','circle'):
            before=self.w.current_joints()[:]
            print('DRAWING_SEQUENCE_BEGIN',shape,json.dumps(before),flush=True)
            original=type(self.d._tool()[0]).ik;records=[]
            def observed(model,start,target,*args,**kwargs):
                result=original(model,start,target,*args,**kwargs)
                if len(records)<100:records.append({'start':list(start),'target':list(target),'ok':result[3],'error':result[1:3],'result':result[0]})
                return result
            try:
                with patch.object(type(self.d._tool()[0]),'ik',observed):
                    state=await self.ready(options(shape=shape,center=[350,0,400,180,0,0],plane='xz',width_mm=180,height_mm=180,range_size_mm=[220,80,220]))
            finally:print('DRAWING_SEQUENCE_IK',shape,json.dumps(records),flush=True)
            self.assertEqual(self.w.current_joints(),before)
            self.d.run({'plan_id':state['plan_id']})
            await self.pump(lambda:self.d.state()['phase']=='completed',60)
            actual=self.d._tool()[0].pose_vec(self.w.current_joints())
            self.assertLess(math.dist(actual[:3],[350,0,400]),.2)
            self.assertEqual(self.w.runner.completed_index,len(self.w.runner.steps));self.assertIsNone(self.w.sim.traj)
    async def test_original_large_square_corner_is_rejected_without_motion_or_relaxed_accuracy(self):
        before=self.w.current_joints()[:]
        self.d.preview(options(shape='square',plane='xz',width_mm=180,height_mm=180,range_size_mm=[220,80,220]))
        await asyncio.wait_for(self.d.worker,95)
        self.assertEqual(self.d.phase,'failed');self.assertIn('无法覆盖整个图形',self.d.why)
        self.assertEqual(self.w.current_joints(),before);self.assertEqual(self.w.runner.state,'idle');self.assertIsNone(self.w.sim.traj)
    async def actual_heart(self,width,height,center=None):
        center=center or options()['center']
        self.assertEqual(self.w.current_joints(),[0]*6)
        model=self.w.motion_model();original=type(model).ik;failures=[];began=time.monotonic()
        def observed(model,start,target,*args,**kwargs):
            result=original(model,start,target,*args,**kwargs)
            if not result[3] and len(failures)<20:failures.append({'start':list(start),'target':list(target),'result':result[:3]})
            return result
        try:
            with patch.object(type(model),'ik',observed):
                state=await self.ready(options(shape='heart',center=center,plane='xz',width_mm=width,height_mm=height,range_size_mm=[220,80,220]))
        finally:print('HEART_CONTINUATION_IK',json.dumps(failures),flush=True)
        print('HEART_DEFAULT_PLANNED',json.dumps({'elapsed_s':time.monotonic()-began,'commands':len(self.d.plan['steps']),
            'center_q':self.d.plan['steps'][0]['joints'],'max_tcp_deviation_mm':self.d.plan['max_tcp_deviation_mm']}),flush=True)
        self.assertLess(time.monotonic()-began,90)
        lo,hi=self.w.limits()
        self.assertTrue(all(all(a<=v<=b for a,v,b in zip(lo,step['joints'],hi)) for step in self.d.plan['steps']))
        self.assertLessEqual(self.d.plan['max_tcp_deviation_mm'],2)
        self.d.run({'plan_id':state['plan_id']})
        await self.pump(lambda:self.d.state()['phase']=='completed',60)
        actual=self.d._tool()[0].pose_vec(self.w.current_joints())
        self.assertLess(math.dist(actual[:3],center[:3]),.2)
        from backend.core.kinematics import pose_rotation_distance
        self.assertLess(pose_rotation_distance(actual,center),.1)
        plate=self.w.motion_model().pose_vec(self.w.current_joints())
        self.assertGreater(math.dist(plate[:3],actual[:3]),100,'actual left cup must reach the path, not the plate-centre TCP')
        self.assertEqual(state['trace_tcp']['kind'],'left_cup_tip');self.assertEqual(state['bounds']['margin_mm'],2)
        self.assertEqual(self.w.runner.completed_index,len(self.w.runner.steps));self.assertIsNone(self.w.runner.execution_guard)
    async def test_cancel_during_preflight_is_bounded_and_never_publishes_late_plan(self):
        state=self.d.preview(options());key=state['plan_id']
        with self.assertRaises(ValueError):self.d.preview(options())
        with self.assertRaises(ValueError):await self.d.cancel({'plan_id':'another'})
        await self.d.cancel({'plan_id':key});await asyncio.wait_for(self.d.worker,3)
        self.assertEqual(self.d.state()['phase'],'cancelled');self.assertNotIn('steps',self.d.plan)
        self.assertEqual(self.w.runner.state,'idle');self.assertIsNone(self.w.sim.traj)
    async def test_expired_context_old_session_start_and_arbitrary_source_refused(self):
        state=await self.ready();key=state['plan_id']
        with self.assertRaises(ValueError):self.d.run({'plan_id':key,'source':state['program']})
        self.w.sim.q[0]+=.1
        with self.assertRaisesRegex(ValueError,'起点'):self.d.run({'plan_id':key})
        self.w.sim.q[0]-=.1;self.d.plan['expires']=0
        with self.assertRaises(ValueError):self.d.run({'plan_id':key})
        self.assertEqual(self.d.phase,'expired');self.assertEqual(self.w.runner.state,'idle')
        self.d.plan['expires']=time.monotonic()+100;self.d.phase='ready';self.w.session_id+='changed'
        self.assertEqual(self.d.state()['phase'],'expired')
    async def test_real_unknown_tool_feedback_controller_and_remote_center_refused(self):
        self.w.settings['mode']='real'
        link=SimpleNamespace(cmd_up=True,state_valid=lambda:True,pose_valid=lambda:True,snap={'joints':[0]*6,'override':.05,'moving':0,'inpos':1,'free_drive':0},
                             controller_info=lambda:{'motion_qualified':False},epoch=2,command_session='fake')
        self.w.real=link
        with self.assertRaisesRegex(ValueError,'资格'):self.d.preview(options(speed_pct=5))
        link.controller_info=lambda:{'motion_qualified':True}
        with self.assertRaisesRegex(ValueError,'工具'):self.d.preview(options(speed_pct=5))
        self.w.tools.mounted={'tool_id':'none','state':'none_confirmed','source':'manual','revision':1}
        self.w.settings['real_move_cmd']='relj';self.w.settings['real_speed_pct']=5;self.w.speed_ack={'ok':True}
        with self.assertRaisesRegex(ValueError,'当前有效TCP'):self.d.preview(options(speed_pct=5))
        link.state_valid=lambda:False
        with self.assertRaisesRegex(ValueError,'反馈'):self.d.preview(options(speed_pct=5))
        self.w.real=None;self.w.settings['mode']='sim'
    async def test_real_fake_device_executes_only_checked_relative_axes_and_returns_center(self):
        try:await self.real_fake_run(False)
        finally:self.w.real=None;self.w.settings['mode']='sim'
    async def test_verified_non_none_tool_persists_and_fake_device_executes_real_envelope_model(self):
        try:await self.real_fake_run(True)
        finally:self.w.real=None;self.w.settings['mode']='sim'
    def install_synthetic_physical_tool(self):
        from check_real_follow_tool import fixture,certify
        from backend.core.tools import ToolStore
        mounted,profile=fixture();mounted['revision']=int(self.w.tools.mounted.get('revision',0))+1
        certify(profile,mounted)  # SYNTHETIC test-only producer, never production signing.
        self.w.tools.add_or_update(profile);self.w.tools.select(profile['id'])
        self.assertEqual(self.w.tools.mounted['revision'],mounted['revision'])
        reopened=ToolStore(self.w.tools.path)
        self.assertEqual(reopened.get(profile['id'])['geometry'],profile['geometry'])
        return profile
    async def real_fake_run(self,physical):
        w=self.w;w.settings.update(mode='real',real_move_cmd='relj',real_speed_pct=5)
        if physical:self.install_synthetic_physical_tool()
        else:w.tools.mounted={'tool_id':'none','state':'none_confirmed','source':'manual','revision':1}
        model=self.d._tool()[0];q,_,_,ok=model.ik([0,-45,90,0,45,0],[400,0,450,180,0,0],*w.limits())
        if physical:self.assertGreater(len(model.capsules(q,conservative=True)),len(w.motion_model().capsules(q,conservative=True)))
        self.assertTrue(ok);calls=[]
        link=SimpleNamespace(cmd_up=True,stream_up=True,epoch=4,command_session='disposable-fake',frame_seq=1,jogging=None,
            snap={'joints':q[:],'override':.05,'moving':0,'inpos':1,'free_drive':0,'fsm':33,'enabled':1,'stamp':time.monotonic()},
            state_valid=lambda:True,pose_valid=lambda:True,controller_info=lambda:{'motion_qualified':True})
        async def ask(command,*args):
            calls.append((command,args));self.assertEqual(command,'MoveRelJ')
            _,axis,sign,amount=args;link.snap['joints'][axis]+=(1 if sign else -1)*float(amount)
            link.frame_seq+=1;link.snap['stamp']=time.monotonic()
            return SimpleNamespace(ok=True)
        link.ask=ask;w.real=link;w.speed_ack={'ok':True}
        center=model.pose_vec(q);state=await self.ready(options(shape='nod',center=center,angle_deg=.5,speed_pct=5))
        self.assertLessEqual(self.d.plan['max_tcp_deviation_mm'],2)
        with self.assertRaisesRegex(ValueError,'本轮明确确认'):self.d.run({'plan_id':state['plan_id']})
        self.assertEqual(calls,[])
        self.d.run({'plan_id':state['plan_id'],'confirm_real':True})
        end=time.monotonic()+20
        while time.monotonic()<end and self.d.state()['phase']!='completed':
            w._drive_executor(time.monotonic(),False);await asyncio.sleep(.003)
            self.assertNotEqual(w.executor.state,'failed',w.executor.error)
        self.assertEqual(self.d.state()['phase'],'completed',self.d.why);self.assertTrue(calls)
        self.assertTrue(all(c[0]=='MoveRelJ' for c in calls));self.assertLess(math.dist(model.pose_vec(w.current_joints())[:3],center[:3]),2)
        self.assertIsNone(w.runner.execution_guard)
        w.real=None;w.settings['mode']='sim'
    async def test_physical_tool_body_collision_and_stale_records_refuse_without_transport(self):
        w=self.w;w.settings.update(mode='real',real_move_cmd='relj',real_speed_pct=5)
        profile=self.install_synthetic_physical_tool();model=self.d._tool()[0]
        q,_,_,ok=model.ik([0,-45,90,0,45,0],[400,0,450,180,0,0],*w.limits());self.assertTrue(ok)
        link=SimpleNamespace(cmd_up=True,stream_up=True,epoch=4,command_session='test-only',frame_seq=1,jogging=None,
            snap={'joints':q[:],'override':.05,'moving':0,'inpos':1,'free_drive':0,'fsm':33,'enabled':1,'stamp':time.monotonic()},
            state_valid=lambda:True,pose_valid=lambda:True,controller_info=lambda:{'motion_qualified':True},ask=AsyncMock())
        w.real=link;w.speed_ack={'ok':True}
        try:
            cap=model.capsules(q,conservative=True)[-1];location=cap['b'][:]
            # Inject an invalid obstacle only into this disposable test scene.
            # The normal edit gate forbids unknown physical robot geometry;
            # restore that gate immediately, before exercising real preflight.
            with patch.object(w.objects,'_capsule_getter',lambda:()):
                w.objects.add({'kind':'box','name':'只阻挡实际工具包络','xyz':location,'size':[4,4,4],
                               'gravity':False,'floating':True,'collide':True})
            center=model.pose_vec(q);self.d.preview(options(shape='nod',center=center,angle_deg=.5,speed_pct=5))
            await self.d.worker;self.assertEqual(self.d.phase,'failed');self.assertIn('安全',self.d.why)
            self.assertEqual(w.runner.state,'idle');link.ask.assert_not_awaited()
            # Editing a capsule without a new trusted record must never fall back to flange.
            actual=next(item for item in w.tools.items if item['id']==profile['id'])
            actual['geometry']['capsules'][0]['r_mm']+=1
            with self.assertRaisesRegex(ValueError,'工具验证'):self.d._fresh()
            self.assertFalse(self.d.state()['trace_tcp']['valid'])
        finally:w.real=None;w.settings['mode']='sim'
    async def test_pause_resume_cancel_preserves_partial_and_cannot_stop_replacement(self):
        state=await self.ready();key=state['plan_id'];self.d.run({'plan_id':key})
        initial=self.w.current_joints()[:]
        await self.pump(lambda:self.w.sim.traj is not None and max(abs(a-b) for a,b in zip(initial,self.w.current_joints()))>1)
        paused=await self.d.pause({'plan_id':key});self.assertEqual(paused['phase'],'paused')
        before=self.w.current_joints()[:]
        for _ in range(5):self.w._tick(.05,time.monotonic());await asyncio.sleep(.003)
        self.assertEqual(self.w.current_joints(),before)
        resumed=await self.d.resume({'plan_id':key});self.assertEqual(resumed['phase'],'running')
        await self.d.cancel({'plan_id':key});self.assertEqual(self.d.phase,'cancelled');self.assertIsNone(self.w.sim.traj)
        self.assertNotEqual(self.w.current_joints(),initial,'cancel must retain actual partial motion instead of restoring start')
        self.assertLess(self.w.runner.completed_index,len(self.w.runner.steps));self.assertIsNone(self.w.runner.execution_guard)
        self.w.runner.load([{'kind':'wait','sec':1,'line':1,'src':'WAIT 1'}]);self.w.runner.start()
        stop=AsyncMock();self.w.stop_execution=stop
        await self.d.cancel({'plan_id':key});stop.assert_not_awaited()
        self.w.runner.stop('test cleanup')
    async def test_runtime_scene_change_guard_stops_own_motion(self):
        state=await self.ready();self.d.run({'plan_id':state['plan_id']})
        await self.pump(lambda:self.w.sim.traj is not None)
        self.w.settings['ground_enabled']=not self.w.settings['ground_enabled']
        await self.pump(lambda:self.d.phase=='failed')
        await asyncio.sleep(.03)
        self.assertIsNone(self.w.sim.traj);self.assertNotEqual(self.w.runner.state,'finished')
        self.assertIsNone(self.w.runner.execution_guard)
    async def test_late_threaded_precheck_rechecks_lease_before_any_sim_move(self):
        entered=threading.Event();release=threading.Event();current=[True]
        def delayed(*args,**kwargs):
            entered.set()
            if not release.wait(2):raise RuntimeError('test thread was not released')
            return real_inspect(*args,**kwargs)
        def guard():
            if not current[0]:raise RuntimeError('old drawing lease revoked')
        with patch('backend.core.world.inspect_path',side_effect=delayed),patch.object(self.w.sim,'move_j') as move:
            task=asyncio.create_task(self.w._execute_move(self.w.current_joints(),'drawing check',self.w.executor.epoch,execution_guard=guard))
            while not entered.is_set():await asyncio.sleep(.005)
            current[0]=False;release.set()
            with self.assertRaisesRegex(RuntimeError,'lease revoked'):await task
            move.assert_not_called();self.assertIsNone(self.w.sim.traj)
    async def test_preview_api_is_immediate_and_requires_scoped_operations(self):
        from fastapi import FastAPI
        import httpx
        from backend.drawing_routes import create_drawing_router
        app=FastAPI();router=create_drawing_router(self.w);app.include_router(router)
        self.d=router.drawing
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/api/drawing/preview',json=options());self.assertEqual(response.status_code,200)
            value=response.json();self.assertEqual(value['phase'],'checking');self.assertFalse(value['can_run'])
            self.assertEqual((await client.post('/api/drawing/cancel',json={'plan_id':'wrong'})).status_code,422)
            self.assertEqual((await client.post('/api/drawing/run',json={'plan_id':value['plan_id'],'source':'WAIT 1'})).status_code,422)
            response=await client.post('/api/drawing/cancel',json={'plan_id':value['plan_id']});self.assertEqual(response.json()['phase'],'cancelled')
            await self.d.worker
    async def test_real_kinematic_rejection_keeps_actual_q_and_never_loads_runner(self):
        before=self.w.current_joints()[:]
        with patch.object(self.w,'limits',return_value=(before[:],before[:])):
            self.d.preview(options());await self.d.worker
        self.assertEqual(self.d.phase,'failed');self.assertIn('无法覆盖整个图形',self.d.why)
        self.assertEqual(self.w.current_joints(),before);self.assertEqual(self.w.runner.state,'idle')
        self.assertIsNone(self.w.sim.traj)
    async def test_cancel_unknown_stop_is_explicit_failure_not_success(self):
        state=await self.ready();key=state['plan_id'];self.d.run({'plan_id':key})
        await self.pump(lambda:self.w.sim.traj is not None)
        original=self.w.stop_execution
        async def unknown(reason):
            await original(reason)
            return {'ok':False,'settled':False,'why':'假设备拒绝停止确认'}
        self.w.stop_execution=unknown
        value=await self.d.cancel({'plan_id':key})
        self.assertFalse(value['ok']);self.assertEqual(value['phase'],'failed');self.assertIn('停止未确认',value['why'])
        self.assertIsNone(self.w.sim.traj);self.w.stop_execution=original
    async def test_replaced_runner_discards_old_queued_dispatch_without_failing_new_program(self):
        state=await self.ready();self.d.run({'plan_id':state['plan_id']})
        self.w._drive_executor(time.monotonic(),False)
        old=self.w.executor.task;self.assertIsNotNone(old)
        replacement=[{'kind':'wait','sec':.01,'line':1,'src':'WAIT .01'}]
        self.w.runner.load(replacement);self.w.runner.start()
        await asyncio.gather(old,return_exceptions=True);await asyncio.sleep(.03)
        self.assertIs(self.w.runner.steps,replacement);self.assertIsNone(self.w.sim.traj)
        self.assertNotEqual(self.w.executor.state,'failed');self.assertEqual(self.w.runner.state,'running')
        self.w._drive_executor(time.monotonic(),False);await asyncio.sleep(.02)
        self.w._drive_executor(time.monotonic(),False);self.w._drive_executor(time.monotonic(),False)
        self.assertEqual(self.w.runner.state,'finished');self.assertEqual(self.d.phase,'failed')
    async def test_preflight_deadline_cannot_publish_or_execute_a_partial_plan(self):
        with patch('backend.application.drawing.PREFLIGHT_SECONDS',0):
            state=self.d.preview(options());await self.d.worker
        self.assertEqual(self.d.phase,'failed');self.assertIn('超时',self.d.why)
        with self.assertRaises(ValueError):self.d.run({'plan_id':state['plan_id']})
        self.assertEqual(self.w.runner.steps,[]);self.assertIsNone(self.w.sim.traj)
    async def test_range_rejection_and_runtime_tip_guard_are_real_not_decorative(self):
        with self.assertRaisesRegex(ValueError,'范围|余量'):
            self.d.preview(options(shape='circle',plane='xy',width_mm=80,height_mm=80,range_size_mm=[84,40,84]))
        self.assertEqual(self.w.runner.state,'idle');self.assertIsNone(self.w.sim.traj)
        state=await self.ready();self.d.run({'plan_id':state['plan_id']})
        await self.pump(lambda:self.w.runner.active_index is not None and self.w.runner.current()['drawing_within_bounds'])
        before=self.w.current_joints()[:];self.w.sim.q[0]+=50
        with self.assertRaisesRegex(ValueError,'范围'):self.d._guard()
        await self.pump(lambda:self.d.phase=='failed');await asyncio.sleep(.03)
        self.assertIsNone(self.w.sim.traj);self.assertNotEqual(self.w.runner.state,'finished')

if __name__=='__main__':unittest.main()
