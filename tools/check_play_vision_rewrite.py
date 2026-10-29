"""Run only via the approved offline runner with --staged-play. No World/HTTP."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import faulthandler
import copy
from collections import Counter
import json
import importlib
import importlib.util
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
# Explicit imports inside the disposable runner only, using backend package names
# for relative imports. This also works before --staged-play is registered.
for name, filename in [('backend.core.play_vision','staged_play_vision.py'),
                       ('backend.core.planning','staged_planning.py'),
                       ('backend.play_routes','staged_play_routes.py'),
                       ('backend.robot_play_routes','staged_robot_play_routes.py')]:
    parent, leaf = name.rsplit('.',1)
    package=importlib.import_module(parent)
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module);setattr(package,leaf,module)
from fastapi import HTTPException
from backend.core.objects import ObjectStore
from backend.core.play_vision import bind_observations, evidence_current, sorting_targets
from backend.core import planning
from backend.core.trajectory import JointTrajectory, PreparedMotion
from backend.play_routes import PlayService, COLORS
from backend.robot_play_routes import RobotPlay


def world(path):
    return NS(objects=ObjectStore(path),settings={'mode':'sim','sim_speed_pct':15,'vel_source':'urdf',
        'fence':{'enabled':False}},real=None,executor=NS(busy=False),runner=NS(state='idle',snapshot=lambda:{}),
        queue_state='idle',sim=NS(traj=None,jog=None,paused=False),hand_follow=NS(active=False),
        real_jog_follow=NS(busy=False),free={'on':False},ready_state={'running':False},_stop_interlocked=lambda:False,
        session_id='offline',limits=lambda:([-10000]*6,[10000]*6),current_joints=lambda:[0]*6,
        resolve_point=lambda _:None,point_items=lambda:[],_sim_vacuum={'vacuum':False,'blow':False})


def vision(pieces):
    key='virtual:offline:fixed-view';now=time.time()
    return {'instance_id':'offline-engine','virtual_session':'offline','stale':False,'config':{'enabled':True,'input':'virtual'},
        'calibration':{'source_key':key,'image_size':[640,480],'matrix':[[1,0,0],[0,1,0],[0,0,1]],
                       'check_errors_mm':[.2,.3],'plane_z':4},
        'result':{'source':'virtual','source_key':key,'frame_seq':7,'captured_at':now,
                  'calibrated':True,'image':{'width':640,'height':480},
                  'objects':[{'id':'V'+str(i),'status':'observed','color':('blue','red','green')[i%3],
                      'rgb':'#abcdef','mapped':{'coordinate_space':'base_xy_planar_estimate','xyz_mm':p['xyz'][:]}}
                      for i,p in enumerate(pieces)]}}


def pixel_vision(pieces):
    """Synthetic pixels through the real HSV/calibrate/map/track pipeline."""
    import cv2 as cv
    import numpy as np
    from backend.core.vision_workspace import VisionWorkspace, settings
    engine=VisionWorkspace(None)
    engine.virtual_session='synthetic'
    engine.config=settings({'input':'virtual','mapping':True,'height_mm':24,
                            'roi':[0,0,1,1],'colors':['red','green','blue']})
    image=np.full((640,640,3),120,dtype=np.uint8)
    expected={}
    bgr={'red':(0,0,255),'green':(0,255,0),'blue':(255,0,0)}
    for i,p in enumerate(pieces):
        x=round((p['xyz'][0]-290)*2);y=round((p['xyz'][1]+510)*2)
        color=('blue','red','green')[i%3];expected[p['id']]=color
        cv.rectangle(image,(x-72,y-72),(x+72,y+72),bgr[color],-1)
    ok,encoded=cv.imencode('.png',image);assert ok
    data=encoded.tobytes();key='virtual:synthetic:fixed-view'
    initial=engine.analyze(data,engine.config,None,key)
    initial.update(source='virtual',source_key=key,captured_at=time.time(),frame_seq=1)
    engine.latest=initial
    rows=[[x,y,290+x*.5,-510+y*.5] for x,y in [(0,0),(639,0),(639,639),(0,639),(200,200),(440,440)]]
    engine.calibrate({'points':rows,'plane_z':4,'max_error_mm':2})
    result=engine.analyze(data,engine.config,engine.calibration,key)
    now=time.time();result['objects']=engine.track(result['objects'],now)
    result.update(source='virtual',source_key=key,captured_at=now,frame_seq=2)
    engine.latest=result;engine.source_key=key
    return engine,expected


class GeometryEvidence(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.w=world(Path(temp.name)/'objects.json');self.scene=PlayService(self.w)
        self.group=self.scene.setup({'mode':'sorting'})['group']
        self.pieces,self.a,self.b=self.scene.group(self.w.objects.list(),self.group)
        self.snapshot=vision(self.pieces)

    def assert_fit(self, pieces, zone):
        for i,p in enumerate(pieces):
            for axis in (0,1):
                self.assertLessEqual(abs(p['xyz'][axis]-zone['xyz'][axis])+p['size'][axis]/2,zone['size'][axis]/2)
            for q in pieces[i+1:]:
                self.assertTrue(any(abs(p['xyz'][axis]-q['xyz'][axis]) >= (p['size'][axis]+q['size'][axis])/2 for axis in (0,1)))

    def test_default_counts_large_pieces_and_nonoverlap(self):
        self.assertEqual(Counter(p['color'] for p in self.pieces),Counter({c:3 for c in COLORS.values()}))
        self.assertTrue(all(p['size']==[72,72,24] for p in self.pieces))
        self.assert_fit(self.pieces,self.a)

    def test_shuffle_is_called_before_assigning_homes(self):
        with patch('backend.play_routes.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda values:values.reverse()
            result=self.scene.setup({'mode':'sorting'})
            rng.return_value.shuffle.assert_called_once()
        pieces,_,_=self.scene.group(result['objects'],result['group'])
        self.assertEqual(pieces[0]['color'],COLORS['blue'])

    def test_chess_larger_and_still_inside_a_and_b(self):
        result=self.scene.setup({'mode':'chess'});pieces,a,b=self.scene.group(result['objects'],result['group'])
        self.assertTrue(all(p['size']==[82,82,18] for p in pieces));self.assert_fit(pieces,a)
        from backend.play_routes import board_position
        placed=[{**p,'xyz':[*board_position(b,i),13]} for i,p in enumerate(pieces)]
        self.assert_fit(placed,b)

    def test_camera_labels_win_even_when_scene_colors_all_lie(self):
        for p in self.pieces:p['color']='#ff0000'
        evidence=bind_observations(self.snapshot,self.pieces,time.time())
        targets=sorting_targets(self.pieces,self.b,{oid:v['color'] for oid,v in evidence['bindings'].items()})
        placed=[{**p,'xyz':xyz} for p,xyz,_ in targets];self.assert_fit(placed,self.b)
        bands={color:{xyz[0] for p,xyz,_ in targets if evidence['bindings'][p['id']]['color']==color} for color in ('red','green','blue')}
        self.assertEqual(len({next(iter(xs)) for xs in bands.values()}),3)
        self.assertEqual(evidence['frame_seq'],7);self.assertEqual(len(evidence['bindings']),9)

    def test_real_pixel_detector_calibration_mapping_tracks_and_source_fit(self):
        engine,expected=pixel_vision(self.pieces)
        evidence=bind_observations(engine.snapshot(),self.pieces,time.time())
        self.assertEqual({oid:v['color'] for oid,v in evidence['bindings'].items()},expected)
        self.assertEqual(len(set(v['track_id'] for v in evidence['bindings'].values())),9)
        for p in self.pieces:
            self.assertLess(math.dist(p['xyz'],evidence['bindings'][p['id']]['xyz_mm']),.01)

    def test_27_capacity_uses_small_footprint_and_real_spacing(self):
        result=self.scene.setup({'mode':'sorting','counts':{'red':9,'green':9,'blue':9}})
        pieces,a,b=self.scene.group(result['objects'],result['group']);self.assert_fit(pieces,a)
        colors={p['id']:('red','green','blue')[i%3] for i,p in enumerate(pieces)}
        placed=[{**p,'xyz':xyz} for p,xyz,_ in sorting_targets(pieces,b,colors)]
        self.assert_fit(placed,b)

    def test_every_missing_or_invalid_evidence_fails_explicitly(self):
        mutations=[lambda s:s.update(result=None),lambda s:s['config'].update(enabled=False),
            lambda s:s['result'].update(source='image'),lambda s:s['result'].update(captured_at=time.time()-2),
            lambda s:s['result'].update(captured_at=float('nan')),lambda s:s.update(stale=True),
            lambda s:s.update(calibration=None),lambda s:s['calibration'].update(source_key='other'),
            lambda s:s['calibration'].update(image_size=[320,240]),lambda s:s['calibration'].update(check_errors_mm=[11,0]),
            lambda s:s['result']['objects'][0].update(status='holding'),
            lambda s:s['result']['objects'][0].update(mapped=None),lambda s:s['result']['objects'][0].update(color='yellow'),
            lambda s:s['result']['objects'].append(copy.deepcopy(s['result']['objects'][0])),
            lambda s:s['result']['objects'][1].update(id='V0'),
            lambda s:s['result']['objects'][0]['mapped'].update(xyz_mm=[0,0,0])]
        for mutate in mutations:
            snapshot=copy.deepcopy(self.snapshot);mutate(snapshot)
            with self.subTest(mutation=mutations.index(mutate)),self.assertRaises(ValueError):
                bind_observations(snapshot,self.pieces,time.time())

    def test_frozen_evidence_allows_next_frame_but_not_changed_source(self):
        before=copy.deepcopy(self.snapshot);evidence=bind_observations(before,self.pieces,time.time())
        before['result']['frame_seq']+=1
        self.assertTrue(evidence_current(evidence,before))
        for field in ('instance_id','config','calibration'):
            altered=copy.deepcopy(before);altered[field]=None
            self.assertFalse(evidence_current(evidence,altered))
        before['result']['source_key']='moved-camera';self.assertFalse(evidence_current(evidence,before))
        self.assertEqual(self.snapshot['result']['frame_seq'],7)

    def test_execution_view_changes_require_same_virtual_identity_config_and_calibration(self):
        evidence=bind_observations(self.snapshot,self.pieces,time.time())
        moved=copy.deepcopy(self.snapshot);moved['result']['source_key']='virtual:offline:moving-pose'
        self.assertFalse(evidence_current(evidence,moved))
        self.assertTrue(evidence_current(evidence,moved,allow_view_change=True))
        for mutate in (lambda s:s.update(instance_id='different-engine'),
                       lambda s:s.update(virtual_session='new-camera-session'),
                       lambda s:s['config'].update(enabled=False),
                       lambda s:s['calibration'].update(plane_z=8),
                       lambda s:s['result'].update(source_key='virtual:foreign:moving-pose'),
                       lambda s:s['result'].update(source='camera')):
            altered=copy.deepcopy(moved);mutate(altered)
            self.assertFalse(evidence_current(evidence,altered,allow_view_change=True))

    def test_real_camera_device_and_session_remain_mandatory_during_execution(self):
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['config']['input']='camera';snapshot['result'].update(source='camera',source_key='camera:session-a')
        snapshot['calibration']['source_key']='camera:session-a'
        snapshot['camera_identity']={'session':'session-a','id':'usb-a','physical_device_id':'device-a','kind':'usb','state':'streaming'}
        evidence=bind_observations(snapshot,self.pieces,time.time())
        self.assertTrue(evidence_current(evidence,snapshot,allow_view_change=True))
        for field,value in [('session','session-b'),('id','usb-b'),('physical_device_id','device-b'),('state','error')]:
            changed=copy.deepcopy(snapshot);changed['camera_identity'][field]=value
            self.assertFalse(evidence_current(evidence,changed,allow_view_change=True))

    def test_capacity_rejects_skewed_camera_counts(self):
        colors={p['id']:('green' if i==0 else 'blue' if i==1 else 'red') for i,p in enumerate(self.pieces)}
        with self.assertRaisesRegex(ValueError,'容量'):sorting_targets(self.pieces,self.b,colors)


class PlanningNumerics(unittest.TestCase):
    def model(self):
        return NS(vmax=[180]*6,pose_vec=lambda q:list(q),capsules=lambda *a,**k:[],
                  jacobian=lambda q:[[float(i==j) for j in range(6)] for i in range(6)],
                  flange_pose=lambda q:{'xyz_mm':list(q[:3])})

    def test_collision_samples_cancellable_and_red_check_preserved(self):
        model=self.model();curve=JointTrajectory.build([[0]*6,[20]*6],[180]*6,15)
        count=0
        def check():
            nonlocal count
            count+=1
            if count==5:raise RuntimeError('cancelled inside inspection')
        green={'max_pct':0,'collisions':[],'level':'green','reasons':[]}
        settings={'mode':'sim','vel_source':'urdf','fence':{'enabled':False}}
        with patch('backend.core.planning.evaluate',return_value=green),patch('backend.core.planning.tool_capsules',return_value=[]):
            with self.assertRaisesRegex(RuntimeError,'inside inspection'):
                planning.inspect_path(model,[0]*6,[20]*6,settings,[-100]*6,[100]*6,[],trajectory=curve,check=check)
        red={**green,'level':'red','reasons':['offline red sample']}
        with patch('backend.core.planning.evaluate',return_value=red),patch('backend.core.planning.tool_capsules',return_value=[]):
            report=planning.inspect_path(model,[0]*6,[20]*6,settings,[-100]*6,[100]*6,[],trajectory=curve)
        self.assertTrue(report['risk_detected']);self.assertEqual(report['samples'],len(curve.inspection_samples()))

    def test_movel_passes_deadline_to_ik_and_still_checks_curve(self):
        model=self.model();calls=[];deadline=time.monotonic()+1;cancelled=lambda:False
        def ik(q,pose,lo,hi,**kwargs):calls.append(kwargs);return list(pose),0,0,True
        model.ik=ik
        with patch('backend.core.planning.evaluate',return_value={'level':'green'}),patch('backend.core.planning.inspect_path',return_value={'risk_detected':True}) as inspect:
            with self.assertRaisesRegex(RuntimeError,'安全预检'):
                planning.prepare_movel(model,[0]*6,[8,0,0,0,0,0],{'mode':'sim','vel_source':'urdf'},[-100]*6,[100]*6,[],
                    context={'mode':'sim','real_connected':False},deadline=deadline,cancelled=cancelled)
        self.assertTrue(calls);self.assertTrue(all(c['deadline']==deadline and c['cancelled'] is cancelled for c in calls))
        self.assertIn('check',inspect.call_args.kwargs)

    def test_real_numeric_solver_obeys_deadline_and_cancel_without_world(self):
        from backend.core.kinematics import RobotModel
        model=RobotModel('elfin5')
        started=time.monotonic()
        result=model.ik([0]*6,[400,-350,16,180,0,0],iters=100000,max_restarts=8,deadline=started+.02)
        self.assertLess(time.monotonic()-started,1,'numeric IK deadline must interrupt internal loops')
        self.assertFalse(result[3])
        event=threading.Event();event.set();started=time.monotonic()
        result=model.ik([0]*6,[400,-350,16,180,0,0],iters=100000,cancelled=event.is_set)
        self.assertLess(time.monotonic()-started,.5)
        self.assertFalse(result[3])


class GenerationBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.w=world(Path(temp.name)/'objects.json');scene=PlayService(self.w)
        group=scene.setup({'mode':'sorting'})['group'];self.payload={'group':group,'action':'sorting'}
        pieces,_,_=scene.group(self.w.objects.list(),group);self.snapshot=vision(pieces)
        self.model=NS(vmax=[180]*6,pose_vec=lambda q:list(q),_fk_key=None,_fk_value=None)
        self.model.ik=lambda q,pose,lo,hi,**kwargs:(list(pose),0,0,True)
        self.w.motion_model=lambda:self.model
        self.service=RobotPlay(self.w,NS(snapshot=lambda:copy.deepcopy(self.snapshot)))
        self.w.robot_play=self.service
        self.patches=[patch('backend.robot_play_routes.evaluate',return_value={'level':'green'}),
            patch('backend.robot_play_routes.inspect_path',return_value={'risk_detected':False}),
            patch('backend.robot_play_routes.carried_clearance'),
            patch('backend.robot_play_routes.prepare_movel',side_effect=self.motion)]
        self.mocks=[p.start() for p in self.patches]
        for p in self.patches:self.addCleanup(p.stop)

    def motion(self,model,q,pose,settings,lo,hi,objects,**kwargs):
        kwargs['check']()
        return PreparedMotion(JointTrajectory.build([q,pose],model.vmax,15),'offline',tuple(lo),tuple(hi),json.dumps({'risk_detected':False}))

    async def test_nine_cycles_have_37_points_and_pinned_camera_evidence(self):
        result=await self.service.generate(self.payload)
        self.assertEqual(result['cycles'],9);self.assertEqual(len(result['points']),37)
        self.assertEqual(len(result['vision_evidence']['bindings']),9)
        self.assertEqual(len(self.service.plans[result['plan_id']]['motions']),72)
        self.assertTrue(self.mocks[2].called,'carried clearance must still be checked')
        self.assertFalse(self.service.busy);self.assertEqual(self.service.state()['progress']['phase'],'complete')

    async def test_absent_observation_does_not_start_any_ik(self):
        self.snapshot['result']=None
        with patch.object(self.model,'ik') as ik:
            with self.assertRaisesRegex(ValueError,'视觉观测'):await self.service.generate(self.payload)
        ik.assert_not_called();self.assertFalse(self.service.busy);self.assertFalse(self.service.plans)

    async def test_missing_injection_explains_failure(self):
        self.service.vision_workspace=None
        with self.assertRaisesRegex(ValueError,'未绑定'):await self.service.generate(self.payload)

    async def test_cancel_reaches_first_seed_ik_and_cannot_publish(self):
        entered=threading.Event();seen=[]
        def ik(q,pose,lo,hi,**kw):
            seen.append(kw);entered.set()
            kw['cancelled'].__self__.wait(1)
            return list(pose),0,0,True
        self.model.ik=ik
        task=asyncio.create_task(self.service.generate(self.payload))
        for _ in range(100):
            if entered.is_set():break
            await asyncio.sleep(.002)
        self.assertTrue(entered.is_set());self.assertTrue(self.service.state()['can_cancel'])
        self.service.cancel_planning()
        with self.assertRaisesRegex(RuntimeError,'已取消'):await asyncio.wait_for(task,1)
        self.assertIn('deadline',seen[0]);self.assertFalse(self.service.plans);self.assertFalse(self.service.busy)

    async def test_deadline_returns_without_unlocking_a_still_running_worker(self):
        entered=threading.Event();release=threading.Event()
        def ik(q,pose,lo,hi,**kw):
            entered.set();release.wait(1);return list(pose),0,0,True
        self.model.ik=ik
        try:
            with patch('backend.robot_play_routes.PREFLIGHT_SECONDS',.04):
                with self.assertRaisesRegex(RuntimeError,'超时'):await self.service.generate(self.payload)
            self.assertTrue(entered.is_set());self.assertTrue(self.service.busy);self.assertFalse(self.service.plans)
            self.assertEqual(self.service.progress['phase'],'failed')
        finally:release.set()
        for _ in range(100):
            if not self.service.busy:break
            await asyncio.sleep(.002)
        self.assertFalse(self.service.busy)

    async def test_carried_collision_prevents_plan_publication(self):
        self.mocks[2].side_effect=RuntimeError('offline carried collision')
        with self.assertRaisesRegex(RuntimeError,'carried collision'):await self.service.generate(self.payload)
        self.assertFalse(self.service.plans);self.assertFalse(self.service.busy)

    async def test_changed_calibration_during_planning_cannot_publish(self):
        original=self.motion
        def motion(*args,**kwargs):
            self.snapshot['calibration']['plane_z']=5
            return original(*args,**kwargs)
        self.mocks[3].side_effect=motion
        with self.assertRaises(HTTPException):await self.service.generate(self.payload)
        self.assertFalse(self.service.plans)

    async def invalidated_active_plan(self,vacuum):
        from backend.core.motion import MotionExecutor
        from backend.core.robot_sim import SimRobot
        from backend.core.program import Runner
        result=await self.service.generate(self.payload);plan=self.service.plans[result['plan_id']]
        self.model.names=list('abcdef');self.model.lower,self.model.upper=self.w.limits()
        self.w.sim=SimRobot(self.model,self.model.vmax);self.w.sim.speed_pct=15
        self.w.executor=MotionExecutor(lambda *args:None)
        self.w.runner=Runner();self.w.runner.load(plan['steps']);self.w.runner.start();self.w.runner.busy=True
        self.service.active=plan
        oid=next(iter(plan['bindings']));piece=next(p for p in self.w.objects.list() if p['id']==oid)
        self.service.held={'id':oid,'piece':piece,'offset':piece['xyz'][:],'model':self.model}
        self.service.contact_ids={oid};self.w._sim_vacuum.update(vacuum=vacuum,blow=False)
        self.w.sim.move_prepared(next(iter(plan['motions'].values()))['motion'])
        self.snapshot['calibration']['plane_z']+=1
        with self.assertRaisesRegex(RuntimeError,'视觉'):self.service.guard()
        return oid

    async def test_guard_failure_tick_stops_curve_and_retains_vacuum_attachment(self):
        oid=await self.invalidated_active_plan(True)
        self.service.tick()
        self.assertIsNone(self.w.sim.traj);self.assertEqual(self.w.runner.state,'aborted')
        self.assertFalse(self.w.runner.busy);self.assertIsNone(self.service.active)
        self.assertEqual(self.service.held['id'],oid);self.assertEqual(self.service.contact_ids,{oid})
        self.assertEqual(self.w._sim_vacuum,{'vacuum':True,'blow':False})
        self.assertIn('上下文失效',self.service.message)
        self.service.stop();self.assertEqual(self.service.held['id'],oid)

    async def test_explicit_stop_bypasses_invalid_source_and_releases_after_vacuum_off(self):
        oid=await self.invalidated_active_plan(False)
        with patch.object(self.service,'guard',side_effect=AssertionError('stop must never ask motion permission')) as guard:
            self.service.stop()
        guard.assert_not_called();self.assertIsNone(self.w.sim.traj);self.assertIsNone(self.service.active)
        self.assertIsNone(self.service.held);self.assertFalse(self.w.runner.busy)
        self.assertEqual(self.service.contact_ids,set())
        piece=next(p for p in self.w.objects.list() if p['id']==oid)
        self.assertEqual(piece['task']['slot'],-1)
        self.assertEqual(self.w._sim_vacuum,{'vacuum':False,'blow':False})

    def setup_chess(self):
        result=PlayService(self.w).setup({'mode':'chess'})
        return result['group']

    async def test_symbol_o_generates_only_one_cycle_and_one_piece(self):
        group=self.setup_chess()
        result=await self.service.generate({'group':group,'action':'chess','symbol':'O','slot':0})
        self.assertEqual(result['cycles'],1);self.assertEqual(len(result['points']),5)
        self.assertEqual(result['symbol'],'O');self.assertEqual(result['slot'],0)
        plan=self.service.plans[result['plan_id']]
        self.assertEqual(len(plan['bindings']),1);self.assertEqual(len(plan['motions']),8)
        oid=next(iter(plan['bindings']))
        piece=next(p for p in self.w.objects.list() if p['id']==oid)
        self.assertEqual(piece['task']['symbol'],'O')
        self.assertEqual(sum(line.startswith('SIM_PICK ') for line in result['source'].splitlines()),1)
        self.assertEqual(sum(line.startswith('SIM_PLACE ') for line in result['source'].splitlines()),1)

    async def test_symbol_x_after_o_is_its_own_separate_plan(self):
        group=self.setup_chess()
        first=self.service.targets({'group':group,'action':'chess','symbol':'O','slot':0})
        piece,xyz,slot=first[0];self.service._commit(piece['id'],xyz,slot)
        result=await self.service.generate({'group':group,'action':'chess','symbol':'X','slot':4})
        self.assertEqual(result['cycles'],1);self.assertEqual(result['symbol'],'X')
        binding=self.service.plans[result['plan_id']]['bindings'];self.assertEqual(len(binding),1)
        selected=next(p for p in self.w.objects.list() if p['id'] in binding)
        self.assertEqual(selected['task']['symbol'],'X')

    async def test_invalid_symbol_wrong_turn_and_occupied_slot_rejected(self):
        group=self.setup_chess()
        for symbol in ('X','x','red',None,True,[]):
            with self.subTest(symbol=symbol),self.assertRaises(HTTPException):
                self.service.targets({'group':group,'action':'chess','symbol':symbol,'slot':0})
        piece,xyz,slot=self.service.targets({'group':group,'action':'chess','symbol':'O','slot':0})[0]
        self.service._commit(piece['id'],xyz,slot)
        for symbol,slot in [('O',1),('X',0)]:
            with self.assertRaises(HTTPException):self.service.targets({'group':group,'action':'chess','symbol':symbol,'slot':slot})

    async def test_missing_piece_and_moved_placed_piece_rejected(self):
        group=self.setup_chess()
        piece,xyz,slot=self.service.targets({'group':group,'action':'chess','symbol':'O','slot':0})[0]
        self.service._commit(piece['id'],[xyz[0]+10,*xyz[1:]],slot)
        with self.assertRaises(HTTPException):self.service.targets({'group':group,'action':'chess','symbol':'X','slot':4})
        self.w.objects.remove(piece['id'])
        with self.assertRaises(HTTPException):self.service.targets({'group':group,'action':'chess','symbol':'O','slot':1})

    async def test_legacy_unspecified_symbol_still_plans_two_cycles(self):
        group=self.setup_chess()
        result=await self.service.generate({'group':group,'action':'chess','slot':0})
        self.assertEqual(result['cycles'],2)

    async def test_symbol_on_sorting_cannot_bypass_action_contract(self):
        with self.assertRaises(HTTPException):await self.service.generate({**self.payload,'symbol':'O'})

    async def test_complete_single_piece_draw_has_five_o_four_x_and_no_extra_turn(self):
        group=self.setup_chess()
        for index,slot in enumerate((0,1,2,4,3,5,7,6,8)):
            symbol='O' if index%2==0 else 'X'
            values=self.service.targets({'group':group,'action':'chess','symbol':symbol,'slot':slot})
            self.assertEqual(len(values),1)
            piece,xyz,destination=values[0];self.service._commit(piece['id'],xyz,destination)
        pieces,_,_=self.service.scene.group(self.w.objects.list(),group,'chess')
        self.assertEqual(Counter(p['task']['symbol'] for p in pieces),Counter({'O':5,'X':4}))
        self.assertTrue(all(p['task']['slot']>=0 for p in pieces))
        with self.assertRaises(HTTPException):self.service.targets({'group':group,'action':'chess','symbol':'X','slot':0})


class RealToolPhysics(unittest.IsolatedAsyncioTestCase):
    """Real IK, safety, curve inspection and carried envelopes; fake World only."""
    async def asyncSetUp(self):
        from backend.core import sim_tool, config
        self.sim_tool=sim_tool
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.w=world(Path(temp.name)/'objects.json')
        self.w.settings=copy.deepcopy(config.DEFAULTS)
        tool=copy.deepcopy(sim_tool.DEFAULT_TOOL)
        tool['dimensions'].update(mountHeight=40.,cupHeight=30.)
        self.w.settings.update(sim_tool=tool,sim_speed_pct=30.)
        self.model=sim_tool.model_for('elfin5',tool)
        self.w.motion_model=lambda:self.model
        self.w.limits=lambda:([lo for lo,hi in config.MANUAL_LIMITS],[hi for lo,hi in config.MANUAL_LIMITS])
        self.q=[0.]*6;self.w.current_joints=lambda:self.q[:]
        self.scene=PlayService(self.w)
        self.snapshot=None
        self.service=RobotPlay(self.w,NS(snapshot=lambda:copy.deepcopy(self.snapshot)))
        self.w.robot_play=self.service

    async def generate_real(self,payload,seconds=35):
        started=time.monotonic()
        with patch('backend.robot_play_routes.PREFLIGHT_SECONDS',seconds):
            result=await self.service.generate(payload)
        elapsed=time.monotonic()-started
        plan=self.service.plans[result['plan_id']]
        self.assertTrue(all(not s['motion'].report['risk_detected'] for s in plan['motions'].values()))
        print('REAL_TOOL_PHYSICS',payload['action'],payload.get('symbol'),result['cycles'],
              'motions',len(plan['motions']),'seconds',round(elapsed,3),flush=True)
        return result,plan

    async def test_real_tool40_30_single_o_then_x_full_curve_and_carried_collision_checks(self):
        group=self.scene.setup({'mode':'chess'})['group']
        for index,slot in enumerate((0,1,2,4,3,5,7,6,8)):
            symbol='O' if index%2==0 else 'X'
            result,plan=await self.generate_real({'group':group,'action':'chess','symbol':symbol,'slot':slot,'speed':30})
            self.assertEqual(result['cycles'],1);self.assertEqual(len(plan['motions']),8)
            oid=next(iter(plan['bindings']));binding=plan['bindings'][oid]
            # Emulate only the already validated object placement, no execution/World.
            self.service._commit(oid,binding['xyz'],binding['slot'])
            last=plan['motions'][max(plan['motions'])]['motion']
            self.q=list(last.trajectory.points[-1])
            self.service.plans={}

    async def test_real_tool40_30_nine_72mm_sorted_by_camera_binding(self):
        group=self.scene.setup({'mode':'sorting'})['group']
        pieces,_,_=self.scene.group(self.w.objects.list(),group)
        engine,expected=pixel_vision(pieces)
        self.service.vision_workspace=engine
        result,plan=await self.generate_real({'group':group,'action':'sorting','speed':30},seconds=60)
        self.assertEqual(result['cycles'],9);self.assertEqual(len(plan['motions']),72)
        self.assertEqual(len(result['vision_evidence']['bindings']),9)
        self.assertEqual({oid:v['color'] for oid,v in result['vision_evidence']['bindings'].items()},expected)


class MovingCameraExecution(unittest.IsolatedAsyncioTestCase):
    """Actual executor/runner/sim trajectories; camera is synthetic FK-view metadata."""
    async def asyncSetUp(self):
        await RealToolPhysics.asyncSetUp(self)

    generate_real=RealToolPhysics.generate_real

    async def test_nine_real_executed_cycles_allow_own_camera_pose_but_reject_external_changes(self):
        from backend.core.robot_sim import SimRobot
        from backend.core.motion import MotionExecutor
        from backend.core.program import Runner
        group=self.scene.setup({'mode':'sorting'})['group']
        pieces,_,_=self.scene.group(self.w.objects.list(),group)
        engine,expected=pixel_vision(pieces);self.service.vision_workspace=engine
        result,plan=await self.generate_real({'group':group,'action':'sorting','speed':30},seconds=60)
        w=self.w;w.runner=Runner();w.executor=MotionExecutor(lambda *args:None)
        w.sim=SimRobot(self.model,self.model.vmax);w.sim.lower,w.sim.upper=w.limits();w.sim.speed_pct=30
        w.current_joints=lambda:w.sim.q[:];w.MOTION=object();w._need=lambda *args:True
        w.start_program=lambda:{'ok':w.runner.start()}
        w._safe_key=None
        original_key=engine.latest['source_key']
        engine.latest['source_key']='virtual:synthetic:changed-before-start'
        with self.assertRaises(HTTPException):self.service.run({'plan_id':result['plan_id'],'source':result['source']})
        self.assertIsNone(w.sim.traj);self.assertIsNone(self.service.active)
        engine.latest['source_key']=original_key;self.service.plans[result['plan_id']]=plan
        self.assertTrue(self.service.run({'plan_id':result['plan_id'],'source':result['source']})['ok'])
        clock=0.;views=set();picked=[];placed=[];motions=0;checked_mutations=False

        def reject_external_changes():
            # At a real moving FK view, every identity/config/calibration change
            # still fails the production guard; restoring each is test cleanup.
            snapshot=copy.deepcopy(engine.latest)
            for field,new in [('instance_id','different'),('virtual_session','foreign')]:
                old=getattr(engine,field);setattr(engine,field,new)
                try:
                    with self.assertRaisesRegex(RuntimeError,'视觉'):self.service.guard()
                finally:setattr(engine,field,old)
            for mapping,key,value in [(engine.config,'height_mm',25),(engine.calibration,'plane_z',5)]:
                old=mapping[key];mapping[key]=value
                try:
                    with self.assertRaisesRegex(RuntimeError,'视觉'):self.service.guard()
                finally:mapping[key]=old
            oid=pieces[-1]['id']
            with w.objects._lock:
                obj=next(p for p in w.objects.items if p['id']==oid);old=obj['xyz'][:];obj['xyz'][0]+=1
            try:
                with self.assertRaisesRegex(RuntimeError,'外部移动'):self.service.guard()
            finally:
                with w.objects._lock:obj['xyz']=old
            self.assertEqual(engine.latest,snapshot)

        def complete_motion():
            w.runner.busy=False;w.runner.complete()

        started=time.monotonic()
        try:
            while w.runner.state not in ('finished','aborted'):
                self.assertLess(time.monotonic()-started,60,'offline executor pump must be bounded')
                # Numerical acceleration only: execute actual C1 curve and
                # validate guard at every pump/executor interval, never teleport.
                w.sim.tick(.15);clock+=.15
                if w.sim.traj is not None:
                    key='virtual:synthetic:'+planning.digest(self.model.flange_pose(w.sim.q))[:16]
                    engine.latest['source_key']=key;views.add(key)
                    # New view detections are deliberately unusable: execution
                    # must keep the frozen original frame/positions/categories.
                    engine.latest['objects']=[];engine.latest['calibrated']=False
                    if not checked_mutations:
                        reject_external_changes();checked_mutations=True
                self.service.tick()
                if w.executor.state=='failed':self.fail(w.executor.error)
                step=w.runner.advance(clock)
                if step:
                    kind=step['kind']
                    if kind in ('movej','movel'):
                        w.runner.busy=True;motions+=1
                        self.assertTrue(w.executor.submit('play-test',lambda epoch,step=step:self.service.motion(step,epoch),complete_motion))
                    elif kind=='speed':w.sim.speed_pct=step['pct'];w.runner.complete()
                    elif kind=='require_tool':w.runner.complete()
                    elif kind=='tool_action':
                        channel,on=step['action'].split('_');w._sim_vacuum['vacuum' if channel=='vacuum' else 'blow']=on=='on'
                        self.assertFalse(w._sim_vacuum['vacuum'] and w._sim_vacuum['blow']);w.runner.complete()
                    elif kind in ('sim_pick','sim_place'):
                        self.service.grip(kind,step['object_id'])
                        (picked if kind=='sim_pick' else placed).append(step['object_id']);w.runner.complete()
                    else:self.fail('Unexpected production program step '+kind)
                await asyncio.sleep(.001)
            self.service.tick()
            self.assertEqual(w.runner.state,'finished');self.assertIsNone(self.service.active);self.assertIsNone(self.service.held)
            self.assertEqual(motions,72);self.assertEqual(len(set(picked)),9);self.assertEqual(picked,placed)
            self.assertGreater(len(views),72);self.assertTrue(checked_mutations)
            self.assertEqual(w._sim_vacuum,{'vacuum':False,'blow':False})
            actual={p['id']:p for p in w.objects.list()}
            for oid,binding in plan['bindings'].items():
                self.assertLess(math.dist(actual[oid]['xyz'],binding['xyz']),.01)
                self.assertEqual(actual[oid]['task']['slot'],binding['slot'])
            self.assertEqual({oid:v['color'] for oid,v in plan['vision_evidence']['bindings'].items()},expected)
            self.assertEqual(plan['vision_evidence']['source_key'],original_key)
            # Exercise the actual executor's failure cleanup, not only a direct
            # guard assertion. Each foreign change happens AFTER motion starts.
            last_motion=plan['motions'][max(plan['motions'])]['motion']
            self.service.active=plan
            for mapping,key,value in [(engine.__dict__,'instance_id','foreign-engine'),
                                      (engine.__dict__,'virtual_session','foreign-session'),
                                      (engine.config,'height_mm',25),
                                      (engine.calibration,'plane_z',8)]:
                old=mapping[key];w.sim.q=list(last_motion.trajectory.points[0])
                task=asyncio.create_task(w.executor.execute_prepared_sim(w.sim,last_motion,w.executor.epoch,
                    validate_context=lambda _:self.service.guard()))
                await asyncio.sleep(.001);self.assertIsNotNone(w.sim.traj)
                mapping[key]=value
                try:
                    with self.assertRaisesRegex(RuntimeError,'视觉'):await asyncio.wait_for(task,1)
                    self.assertIsNone(w.sim.traj,'foreign identity/config/calibration must stop the owned curve')
                finally:mapping[key]=old
            self.service.active=None
            print('MOVING_CAMERA_EXECUTION PASS cycles=9 motions=72 unique_fk_views='+str(len(views))+
                  ' external_executor_stops=4 execution_seconds='+str(round(time.monotonic()-started,3)),flush=True)
        finally:
            w.sim.stop();await w.executor.cancel('offline test cleanup')


if __name__=='__main__':
    assert 'backend.core.world' not in sys.modules and 'backend.main' not in sys.modules
    trace_exit=os.environ.get('ELFIN_PLAY_TRACE_EXIT')=='1'
    trace_enabled=trace_exit or os.environ.get('ELFIN_PLAY_TRACE')=='1'
    if trace_enabled:faulthandler.dump_traceback_later(20,repeat=not trace_exit,exit=trace_exit)
    groups=os.environ.get('ELFIN_PLAY_CHECK_GROUP')
    try:unittest.main(defaultTest=groups.split(',') if groups else ('RealToolPhysics' if os.environ.get('ELFIN_PLAY_PHYSICS_ONLY')=='1' else None),verbosity=2)
    finally:faulthandler.cancel_dump_traceback_later()
