"""APP007/008 current evidence, immutable preview and actual single-executor contracts."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, AsyncMock
from backend.core import config
from backend.core.world import World
from backend.core.objects import ObjectStore
from backend.application.assembly_tasks import AssemblyTasks
from backend.application.run_journal import world_context
from check_handling_challenge import FakePlay
from check_play_vision_rewrite import vision,pixel_vision

class Contracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        p=patch.object(config,'_settings',deepcopy(config.DEFAULTS));p.start();self.addCleanup(p.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=World();self.w.objects=ObjectStore(Path(self.tmp.name)/'objects.json')
        self.play=self.w.robot_play=FakePlay(self.w)
        self.group=self.play.scene.setup({'mode':'sorting','counts':{'red':2,'green':0,'blue':0}})['group']
        self.pieces,_,_=self.play.scene.group(self.w.objects.list(),self.group)
        self.snap=vision(self.pieces);self.snap['config_generation']=1
        for obj in self.snap['result']['objects']:obj['shape']='rectangle'
        self.play.vision_snapshot=lambda:self.snap
        self.now=10.;self.service=AssemblyTasks(self.w,clock=lambda:self.now)
        self.payload=dict(mode='kit',group=self.group,columns=2,rows=1,cells=['blue','red'])
    def preview(self):return self.service.preview(self.payload)
    def setup_payload(self):return dict(mode='pattern',columns=5,rows=2,cells=['red']*10)
    async def checked(self):
        p=self.preview();return await self.service.inspect({'preview_id':p['preview_id']})
    def start_task(self):return self.service.run({'preview_id':self.service.current['preview_id'],'plan_id':self.service.plan['plan_id']})
    async def test_preview_is_nonmoving_and_pattern_shares_same_bounded_layout(self):
        a=self.preview();self.assertEqual(a['total'],2);self.assertIsNone(self.play.active)
        self.assertEqual(self.w.runner.state,'idle');self.payload['mode']='pattern';b=self.preview()
        self.assertEqual([p['xyz'] for p in a['placements']],[p['xyz'] for p in b['placements']])

    async def test_scene_recipe_previews_checks_and_starts_without_camera_or_calibration(self):
        self.payload.update(cells=['red','red'],perception='scene')
        with patch.object(self.play,'vision_snapshot',side_effect=AssertionError('场景配方不得访问相机')):
            checked=await self.checked()
            self.assertEqual(checked['phase'],'checked')
            self.assertEqual(self.service.current['evidence']['source'],'scene')
            started=self.start_task()
        self.assertEqual(started['phase'],'running')
        self.assertEqual(len(self.play.targets),2)

    async def test_scene_recipe_rejects_changed_color_and_never_uses_old_layout(self):
        self.payload.update(cells=['red','red'],perception='scene')
        await self.checked()
        self.w.objects.update(self.pieces[0]['id'],{'color':'#40c979'})
        with self.assertRaises(Exception):self.start_task()
        self.assertIsNone(self.play.active)
    async def test_kit_and_pattern_keep_their_identity_in_active_plan_context_and_actual_journal(self):
        from backend.core.program import parse
        for index,mode in enumerate(('kit','pattern')):
            self.payload['mode']=mode
            self.play.active=None;self.w.runner.state='idle'
            await self.checked()
            self.assertEqual(self.service.plan['action'],mode)
            self.assertEqual(self.play.plans['fake']['action'],mode)
            self.start_task()
            self.assertEqual(self.play.active['action'],mode)
            self.assertEqual(world_context(self.w)['action'],mode)
            # Exercise the real Runner start event and journal observer, without
            # advancing any motion or opening the journal persistence worker.
            self.w.runner.load(parse('WAIT 0',lambda _:None))
            run_id=str(index+1)*32;self.w.runner.next_run_id=run_id
            self.assertTrue(self.w.runner.start())
            record=self.w.run_journal.get(run_id)
            self.assertEqual(record['context']['action'],mode)
            self.assertEqual(record['context']['plan_id'],self.play.active['id'])
    async def test_recipe_setup_builds_ten_small_red_pieces_retains_user_objects_without_motion_or_detection(self):
        ordinary=self.w.objects.add({'name':'保留道具','kind':'box','xyz':[100,0,50]})
        before=self.w.runner.state
        with patch.object(self.play,'vision_snapshot',side_effect=AssertionError('布料不得采集或推理')):
            result=self.service.setup(self.setup_payload())
        pieces,_,_=self.play.scene.group(self.w.objects.list(),result['group'])
        self.assertEqual(len(pieces),10);self.assertTrue(all(p['size']==[38,38,24] and p['color']=='#e4553f' for p in pieces))
        self.assertIn(ordinary['id'],{p['id'] for p in self.w.objects.list()})
        self.assertEqual(self.w.runner.state,before);self.assertIsNone(self.play.active);self.assertIsNone(self.service.current)
    async def test_bad_grid_and_failed_storage_leave_original_scene_and_preview_intact(self):
        preview=self.preview();before=self.w.objects.list()
        for invalid in [dict(columns=6),dict(cells=['red']),dict(cells=[None]*10),dict(cells=['unknown']*10)]:
            with self.assertRaises(Exception):self.service.setup({**self.setup_payload(),**invalid})
            self.assertEqual(self.w.objects.list(),before);self.assertEqual(self.service.current['preview_id'],preview['preview_id'])
        with patch.object(self.w.objects,'_persist',side_effect=OSError('模拟磁盘失败')):
            with self.assertRaises(OSError):self.service.setup(self.setup_payload())
        self.assertEqual(self.w.objects.list(),before);self.assertEqual(self.service.current['preview_id'],preview['preview_id'])
    async def test_recipe_setup_rejects_real_connection_mode_held_and_busy(self):
        before=self.w.objects.list()
        for owner,key,value in [(self.w.settings,'mode','real'),(self.w,'real',object()),(self.play,'held',self.pieces[0]),(self.play,'busy',True),(self.play,'active',{'id':'another'})]:
            old=owner.get(key) if isinstance(owner,dict) else getattr(owner,key)
            try:
                if isinstance(owner,dict):owner[key]=value
                else:setattr(owner,key,value)
                with self.assertRaises(Exception):self.service.setup(self.setup_payload())
                self.assertEqual(self.w.objects.list(),before)
            finally:
                if isinstance(owner,dict):owner[key]=old
                else:setattr(owner,key,old)
    async def test_recipe_limit_is_internal_and_public_sorting_still_rejects_ten_or_recipe_field(self):
        for extra in [{},{'recipe':True}]:
            with self.assertRaises(Exception):self.play.scene.setup({'mode':'sorting','counts':{'red':10,'green':0,'blue':0},**extra})
        with self.assertRaises(Exception):self.play.scene.setup({'mode':'sorting','counts':{'red':28,'green':0,'blue':0}},recipe=True)
    async def test_capacity_missing_unknown_and_failed_replacement_revoke_old_ticket(self):
        for patching in [dict(columns=5,rows=1,cells=['blue']*5),dict(cells=['green',None]),dict(cells=['unknown',None])]:
            old=self.preview();original=deepcopy(self.payload);self.payload.update(patching)
            with self.assertRaises(Exception):self.preview()
            self.assertIsNone(self.service.current);self.assertIsNone(self.play.active)
            self.payload=original
            with self.assertRaises(Exception):await self.service.inspect({'preview_id':old['preview_id']})
    async def test_expired_and_old_preview_do_not_start(self):
        old=self.preview();new=self.preview()
        with self.assertRaises(Exception):await self.service.inspect({'preview_id':old['preview_id']})
        self.now+=181
        with self.assertRaises(Exception):await self.service.inspect({'preview_id':new['preview_id']})
        self.assertEqual(self.w.runner.state,'idle')
    async def test_real_mode_or_real_connection_cannot_preview(self):
        for mode,real in [('real',None),('sim',object())]:
            self.w.settings['mode']=mode;self.w.real=real
            with self.assertRaises(Exception):self.preview()
            self.assertIsNone(self.play.active)
    async def test_missing_stale_or_replayed_observations_cannot_preview(self):
        for change in ['empty','old','image','unknown_shape','mismatched_shape']:
            original=deepcopy(self.snap)
            if change=='empty':self.snap['result']['objects']=[]
            if change=='old':self.snap['result']['captured_at']=time.time()-2
            if change=='image':self.snap['result']['source']='image'
            if change=='unknown_shape':self.snap['result']['objects'][0]['shape']='unknown'
            if change=='mismatched_shape':self.snap['result']['objects'][0]['shape']='circle'
            with self.assertRaises(Exception):self.preview()
            self.snap=original
    async def test_run_revalidates_source_ttl_color_and_position(self):
        for change in ['source','old','color','position','shape','restore_config']:
            await self.checked();original=deepcopy(self.snap)
            if change=='source':self.snap['virtual_session']='different'
            if change=='old':self.snap['result']['captured_at']=time.time()-2
            if change=='color':self.snap['result']['objects'][0]['color']='green'
            if change=='position':self.snap['result']['objects'][0]['mapped']['xyz_mm'][0]+=3
            if change=='shape':self.snap['result']['objects'][0]['shape']='circle'
            if change=='restore_config':
                self.snap['config']['enabled']=False;self.snap['config_generation']+=1
                self.snap['config']['enabled']=True;self.snap['config_generation']+=1
            with self.assertRaises(Exception):self.start_task()
            self.assertIsNone(self.play.active);self.snap=original
    async def test_reentry_and_old_cancel_cannot_steal_running_owner(self):
        await self.checked();self.start_task()
        with self.assertRaises(Exception):self.start_task()
        with self.assertRaises(Exception):self.preview()
        with self.assertRaises(Exception):await self.service.cancel({'preview_id':'old'})
        self.assertIsNotNone(self.play.active)
    async def test_cancelled_partial_completion_keeps_actual_position_and_never_claims_all(self):
        await self.checked();self.start_task();piece,xyz,slot=self.play.targets[0]
        self.w.objects.update(piece['id'],{'xyz':xyz})
        with self.w.objects._lock:next(p for p in self.w.objects.items if p['id']==piece['id'])['task']['slot']=slot
        self.w.runner.state='aborted';self.play.active=None
        state=self.service.state();self.assertEqual(state['phase'],'cancelled');self.assertEqual(state['completed'],1)
        await self.service.cancel({'preview_id':state['preview_id']})
        self.assertEqual(self.service.state()['completed'],1);self.assertEqual(next(p for p in self.w.objects.list() if p['id']==piece['id'])['xyz'],xyz)
    async def test_teleport_without_committed_slot_does_not_count(self):
        await self.checked();self.start_task();piece,xyz,slot=self.play.targets[0];self.w.objects.update(piece['id'],{'xyz':xyz})
        self.w.runner.state='finished';self.play.active=None
        self.assertEqual(self.service.state()['completed'],0);self.assertEqual(self.service.state()['phase'],'failed')
    async def test_running_cancel_uses_world_stop_and_retains_held_piece(self):
        await self.checked();self.start_task();self.play.held=self.pieces[0]
        async def stopped(*args):self.w.runner.state='aborted';self.play.active=None
        with patch.object(self.w,'stop_execution',AsyncMock(side_effect=stopped)) as stop:
            result=await self.service.cancel({'preview_id':self.service.current['preview_id']})
        stop.assert_awaited_once();self.assertEqual(result['phase'],'cancelled')
        self.assertEqual(result['held'],self.pieces[0]['id']);self.assertEqual(result['completed'],0)
    async def test_cancelled_preflight_late_result_cannot_leave_an_executable_plan(self):
        preview=self.preview();entered=asyncio.Event();release=asyncio.Event()
        original=self.play.generate
        async def delayed(*args,**kwargs):
            entered.set();await release.wait();return await original(*args,**kwargs)
        self.play.generate=delayed
        pending=asyncio.create_task(self.service.inspect({'preview_id':preview['preview_id']}));await entered.wait()
        await self.service.cancel({'preview_id':preview['preview_id']});release.set()
        with self.assertRaises(Exception):await pending
        self.assertEqual(self.service.phase,'cancelled');self.assertFalse(self.play.plans)
        with self.assertRaises(Exception):self.service.run({'preview_id':preview['preview_id'],'plan_id':'fake'})
    async def test_routes_return_rejection_and_require_current_ticket(self):
        import httpx
        from fastapi import FastAPI
        from backend.assembly_routes import create_assembly_router
        app=FastAPI();router=create_assembly_router(self.w);app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
            setup=(await client.post('/api/assembly/setup',json=self.setup_payload())).json()
            self.assertTrue(setup['ok']);self.assertEqual(len([p for p in setup['objects'] if p.get('task',{}).get('role')=='piece']),10)
            # Reset to the earlier fixture: API setup intentionally replaces task objects.
            self.group=self.play.scene.setup({'mode':'sorting','counts':{'red':2,'green':0,'blue':0}})['group']
            self.pieces,_,_=self.play.scene.group(self.w.objects.list(),self.group);self.snap=vision(self.pieces)
            for obj in self.snap['result']['objects']:obj['shape']='rectangle'
            self.payload['group']=self.group
            bad=await client.post('/api/assembly/preview',json={**self.payload,'cells':['unknown',None]})
            self.assertFalse(bad.json()['ok']);self.assertTrue(bad.json()['why'])
            valid=(await client.post('/api/assembly/preview',json=self.payload)).json()
            self.assertTrue(valid['ok']);self.assertEqual(valid['phase'],'preview')
            old=await client.post('/api/assembly/run',json={'preview_id':'old','plan_id':'fake'})
            self.assertEqual(old.status_code,409);self.assertIsNone(self.play.active)

class Actual(unittest.IsolatedAsyncioTestCase):
    async def test_one_piece_actual_robotplay_path_runner_and_placement(self):
        p=patch.object(config,'_settings',deepcopy(config.DEFAULTS));p.start();self.addCleanup(p.stop)
        from backend.robot_play_routes import RobotPlay
        with tempfile.TemporaryDirectory() as temp:
            w=World();w.objects=ObjectStore(Path(temp)/'actual.json');play=w.robot_play=RobotPlay(w)
            group=play.scene.setup({'mode':'sorting','counts':{'red':1,'green':0,'blue':0}})['group']
            pieces,_,_=play.scene.group(w.objects.list(),group);engine,_=pixel_vision(pieces);play.vision_workspace=engine
            service=AssemblyTasks(w);preview=service.preview(dict(mode='pattern',group=group,columns=1,rows=1,cells=['blue']))
            try:
                checked=await service.inspect({'preview_id':preview['preview_id']});self.assertTrue(checked['ok'])
                # A new safe synthetic capture of the same static fixture is supplied after preflight.
                engine.latest['captured_at']=time.time();engine.latest['frame_seq']+=1
                started=service.run({'preview_id':preview['preview_id'],'plan_id':checked['plan_id']});self.assertEqual(started['phase'],'running')
                self.assertEqual(service.plan['action'],'pattern')
                self.assertEqual(play.active['action'],'pattern');self.assertEqual(world_context(w)['action'],'pattern')
                self.assertEqual(w.run_journal.get(w.runner.run_id)['context']['action'],'pattern')
                self.assertIs(w.robot_play,play);deadline=time.monotonic()+70
                while time.monotonic()<deadline:
                    w._tick(.05,time.monotonic());status=service.state()
                    if status['phase'] in ('completed','failed','cancelled'):break
                    await asyncio.sleep(.002)
                self.assertEqual(status['phase'],'completed',status);self.assertEqual(status['completed'],1);self.assertIsNone(play.held)
                goal=status['placements'][0];actual=next(p for p in w.objects.list() if p['id']==goal['object_id']);self.assertLessEqual(sum((a-b)**2 for a,b in zip(actual['xyz'],goal['xyz']))**.5,.5)
                self.assertEqual(w.runner.state,'finished')
                record=w.run_journal.get(w.runner.run_id)
                self.assertEqual(record['context']['action'],'pattern');self.assertEqual(record['context']['plan_id'],checked['plan_id'])
            finally:await w.stop_execution('APP007/008离线验收结束')

if __name__=='__main__':unittest.main()
