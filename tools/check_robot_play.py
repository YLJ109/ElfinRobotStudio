"""隔离世界的数值预检与加速仿真执行；不是实机或实时性能验收。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.world import World
from backend.robot_play_routes import RobotPlay
from backend.core import program
from backend.play_routes import PlayService
from backend.core.objects import ObjectStore
from _play_test_support import pixel_vision


class StopBoundaryTests(unittest.TestCase):
    """No IK/path planning: reproduce the exact tool-off/placement boundary."""
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        store=ObjectStore(Path(temp.name)/'objects.json')
        piece=store.add({'id':'stop-piece','kind':'box','size':[38,38,20],
            'xyz':[100,200,20],'gravity':False,'floating':True,
            'task':{'group':'a'*32,'mode':'sorting','role':'piece','symbol':'','slot':-1,'home':5}})
        self.pose=[100.,200.,40.,0.,0.,0.]
        model=NS(pose_vec=lambda _q:self.pose[:])
        self.w=NS(objects=store,settings={'mode':'sim'},real=None,
            runner=NS(state='aborted'),_sim_vacuum={'vacuum':False,'blow':False},
            motion_model=lambda:model,current_joints=lambda:[0.]*6,_safe_key='cached')
        self.service=RobotPlay(self.w)
        self.binding={'place':self.pose[:],'xyz':[100.,200.,20.],'slot':5}
        self.service.active={'bindings':{piece['id']:self.binding},'expected':{piece['id']:piece['xyz'][:]}}
        self.service.held={'id':piece['id'],'piece':piece,'offset':[0.,0.,-20.]}
        self.service.contact_ids={piece['id']}
        self.service.cancel=threading.Event()
        self.oid=piece['id']

    def stopped_object(self):
        return next(obj for obj in self.w.objects.list() if obj['id']==self.oid)

    def assert_released(self,slot,xyz):
        self.assertIsNone(self.service.active);self.assertIsNone(self.service.held)
        self.assertEqual(self.service.contact_ids,set());self.assertIsNone(self.w._safe_key)
        self.assertEqual(self.stopped_object()['task']['slot'],slot)
        self.assertEqual(self.stopped_object()['xyz'],xyz)
        saved=json.loads(self.w.objects.path.read_text(encoding='utf-8'))
        self.assertEqual(saved,self.w.objects.list(),'stop result must be persisted, not just displayed')
        self.assertTrue(self.service.cancel.is_set())

    def test_stop_just_after_vacuum_off_before_sim_place_commits_b_slot(self):
        # No blow command has run yet. TCP differs from object center by 20mm.
        self.service.stop()
        self.assert_released(5,[100.,200.,20.])
        self.assertEqual(self.w._sim_vacuum,{'vacuum':False,'blow':False})

    def test_stop_during_blow_wait_also_releases_without_changing_io(self):
        self.w._sim_vacuum['blow']=True
        self.service.stop()
        self.assert_released(5,[100.,200.,20.])
        self.assertEqual(self.w._sim_vacuum,{'vacuum':False,'blow':True})

    def test_near_destination_preserves_actual_position_instead_of_snapping(self):
        self.pose[0]+=.75
        self.service.stop()
        self.assert_released(5,[100.75,200.,20.])

    def test_off_destination_and_exact_one_mm_boundary_have_no_b_slot(self):
        for distance in (1.,1.001,50.):
            with self.subTest(distance=distance):
                # Restore one held/stopped cycle without a costly world/IK setup.
                piece=self.stopped_object();self.pose[0]=100.+distance
                self.service.active={'bindings':{self.oid:self.binding},'expected':{self.oid:piece['xyz'][:]}}
                self.service.held={'id':self.oid,'piece':piece,'offset':[0.,0.,-20.]}
                self.service.stop()
                self.assert_released(-1,[100.+distance,200.,20.])

    def test_vacuum_on_stop_retains_attachment_and_does_not_enable_blow(self):
        self.w._sim_vacuum['vacuum']=True
        self.service.stop()
        self.assertIsNone(self.service.active);self.assertIsNotNone(self.service.held)
        self.assertEqual(self.service.contact_ids,{self.oid})
        self.assertEqual(self.stopped_object()['task']['slot'],-1)
        self.assertEqual(self.w._sim_vacuum,{'vacuum':True,'blow':False})
        self.pose[0]+=10;self.service.tick()
        self.assertEqual(self.stopped_object()['xyz'],[110.,200.,20.])

    def test_off_without_active_binding_releases_as_unplaced(self):
        self.service.active=None
        self.service.stop()
        self.assert_released(-1,[100.,200.,20.])

    def test_repeated_stop_is_idempotent_and_released_piece_no_longer_follows_tcp(self):
        self.service.stop();self.assert_released(5,[100.,200.,20.])
        self.pose[0]+=100
        self.service.stop();self.service.tick()
        self.assert_released(5,[100.,200.,20.])

class RobotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w=World();self.w.objects.clear();self.w.settings['mode']='sim';self.w.real=None
        # Course tests explicitly choose the top-bar speed; generate(speed=...)
        # no longer overrides that authoritative speed. Keep all timeouts intact.
        self.w.set_speed(100,schedule=False)
        self.service=RobotPlay(self.w);self.w.robot_play=self.service
        self.scene=PlayService(self.w)
    async def asyncTearDown(self):
        await self.w.stop_execution('离线测试结束')
    async def plan(self):
        group=self.scene.setup({'mode':'sorting','counts':{'red':1,'green':0,'blue':0}})['group']
        pieces,_,_=self.scene.group(self.w.objects.list(),group)
        self.service.vision_workspace=pixel_vision(pieces)[0]
        return await self.service.generate({'group':group,'action':'sorting','speed':100})
    async def pump(self,predicate,timeout=20):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.w._tick(.05,time.monotonic())
            if predicate():return
            if self.w.runner.state=='aborted':self.fail('程序中止：'+self.w.runner.error)
            await asyncio.sleep(.002)
        self.fail('仿真执行超时：'+str(self.w.runner.snapshot()))
    async def test_actual_pick_place_motion_and_io_sequence(self):
        self.w.set_speed(8,schedule=False)
        planned=await self.plan();self.assertTrue(planned['ok'])
        self.assertFalse(any(step['kind']=='speed' for step in self.service.plans[planned['plan_id']]['steps']))
        self.assertNotIn('SPEED ',planned['source'])
        self.w.set_speed(100,schedule=False)
        self.assertEqual(len(planned['points']),5)
        start=self.w.current_joints()[:]
        self.assertTrue(self.service.run({'plan_id':planned['plan_id'],'source':planned['source']})['ok'])
        await self.pump(lambda:self.service.held is not None)
        self.assertEqual(self.w.settings['sim_speed_pct'],100,'generated tasks must not restore their old speed')
        self.w.set_speed(50,schedule=False)
        oid=self.service.held['id'];first=next(o for o in self.w.objects.list() if o['id']==oid)['xyz'][:]
        await self.pump(lambda:next(o for o in self.w.objects.list() if o['id']==oid)['xyz'][2]>first[2]+20)
        self.assertEqual(self.w.settings['sim_speed_pct'],50)
        self.w.set_speed(100,schedule=False)
        self.assertNotEqual(start,self.w.current_joints());self.assertTrue(self.w._sim_vacuum['vacuum'])
        await self.pump(lambda:self.w.runner.state=='finished',30)
        self.assertIsNone(self.service.held)
        placed=next(o for o in self.w.objects.list() if o['id']==oid)
        self.assertGreater(placed['xyz'][1],200);self.assertGreaterEqual(placed['task']['slot'],0)
        self.assertEqual(self.w._sim_vacuum,{'vacuum':False,'blow':False})
        self.assertEqual(self.w.settings['sim_speed_pct'],100)
        self.assertTrue(any('vacuum_on' in event['text'] for event in self.w.log.tail(2000)))
    async def test_stop_holds_part_and_revokes_program(self):
        planned=await self.plan();self.service.run({'plan_id':planned['plan_id'],'source':planned['source']})
        await self.pump(lambda:self.service.held is not None)
        oid=self.service.held['id'];await self.w.stop_execution('中途停止')
        xyz=next(o for o in self.w.objects.list() if o['id']==oid)['xyz'][:]
        self.assertIsNone(self.w.sim.traj);self.assertIsNone(self.service.active)
        self.assertTrue(self.w._sim_vacuum['vacuum']);self.assertIsNotNone(self.service.held)
        for _ in range(10):self.w._tick(.05,time.monotonic());await asyncio.sleep(.001)
        self.assertEqual(xyz,next(o for o in self.w.objects.list() if o['id']==oid)['xyz'])
        self.assertFalse(self.w._need(self.w.MOTION,'持件后的普通点动'))
        self.assertFalse((await self.w.load_program('WAIT 1'))['ok'])
        self.assertTrue((await self.w.tool_action('vacuum_off'))['ok'])
        self.assertIsNone(self.service.held)
        self.assertFalse(self.w.start_program()['ok'],'残留取放程序不能重启并先产生运动副作用')
    async def test_unbound_commands_and_blown_vacuum_rejected(self):
        self.assertEqual(program.parse('SIM_PICK object_1',lambda _:None)[0]['kind'],'sim_pick')
        self.assertEqual(program.parse('TOOL BLOW_ON',lambda _:None)[0]['action'],'blow_on')
        self.assertTrue((await self.w.tool_action('vacuum_on'))['ok'])
        self.assertFalse((await self.w.tool_action('blow_on'))['ok'])
        self.w.runner.load(program.parse('SIM_PICK object_1',lambda _:None));self.w.runner.start()
        for _ in range(30):self.w._tick(.01,time.monotonic());await asyncio.sleep(.002)
        self.assertIn('预检',self.w.runner.error)
    async def test_edited_program_and_scene_change_require_recheck(self):
        planned=await self.plan()
        with self.assertRaises(Exception):self.service.run({'plan_id':planned['plan_id'],'source':planned['source']+'\nWAIT 1'})
        self.assertIsNone(self.w.sim.traj)

    async def test_explicit_user_speed_source_still_executes_exact_instruction(self):
        planned=await self.plan()
        manual=await self.service.generate({'group':planned['group'],'action':'sorting','perception':'scene',
            'source':'SPEED 8\n'+planned['source']})
        self.assertTrue(manual['source'].startswith('SPEED 8\n'))
        self.assertTrue(any(step['kind']=='speed' and step['pct']==8 for step in self.service.plans[manual['plan_id']]['steps']))
        self.w.set_speed(100,schedule=False)
        self.assertTrue(self.service.run({'plan_id':manual['plan_id'],'source':manual['source']})['ok'])
        await self.pump(lambda:self.w.settings['sim_speed_pct']==8,5)
        self.assertEqual(self.w.settings['sim_speed_pct'],8)

    async def test_nine_objects_have_a18_b18_and_all_move_through_robot(self):
        group=self.scene.setup({'mode':'sorting','counts':{'red':3,'green':3,'blue':3}})['group']
        pieces,_,_=self.scene.group(self.w.objects.list(),group)
        self.service.vision_workspace=pixel_vision(pieces)[0]
        planned=await self.service.generate({'group':group,'action':'sorting','speed':100})
        self.assertEqual(sum(p['role'] in ('pick_up','pick') for p in planned['points']),18)
        self.assertEqual(sum(p['role'] in ('place_up','place') for p in planned['points']),18)
        self.service.run({'plan_id':planned['plan_id'],'source':planned['source']})
        await self.pump(lambda:self.w.runner.state=='finished',60)
        self.assertEqual(sum(p.get('task',{}).get('role')=='piece' and p['task']['slot']>=0 for p in self.w.objects.list()),9)

    async def test_chess_o_and_x_use_two_robot_cycles(self):
        group=self.scene.setup({'mode':'chess'})['group']
        planned=await self.service.generate({'group':group,'action':'chess','slot':0,'speed':100})
        self.assertEqual(planned['cycles'],2)
        self.service.run({'plan_id':planned['plan_id'],'source':planned['source']})
        await self.pump(lambda:self.w.runner.state=='finished',30)
        placed=[p for p in self.w.objects.list() if p.get('task',{}).get('role')=='piece' and p['task']['slot']>=0]
        self.assertEqual({p['task']['symbol'] for p in placed},{'O','X'})

if __name__=='__main__':
    # Keep the established offline runner; opt into fast boundary checks only.
    unittest.main(defaultTest='StopBoundaryTests' if os.environ.get('ELFIN_ROBOT_PLAY_STOP_ONLY')=='1' else None)
