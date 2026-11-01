"""Production controller/ASGI/host using synthetic feedback; no hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
from copy import deepcopy
import time
import unittest
from unittest.mock import patch
import httpx
from fastapi import FastAPI
import check_real_follow_runtime as fixtures
import check_real_follow_host as host_fixtures
import check_real_palm_pipeline as pipeline_fixtures
from backend.real_track_routes import create_real_track_router

class Runtime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.world,self.record=fixtures.fake_world();self.c=self.world.real_jog_follow
        self.world._real_follow_qualification_reader=lambda:deepcopy(self.record)
        self.allow=patch('backend.core.real_follow_runtime.config.no_real',return_value=False)
        self.allow.start();self.addCleanup(self.allow.stop)
        self.payload=dict(enclosure=dict(min=[-650.,-50.,600.],max=[-550.,50.,700.]),
            direction=270,rotation=False,depth=True,depth_mode='gesture',session='camera-test',aspect=640/480,standoff=120.)
    async def asyncTearDown(self):
        if self.c.host:await self.c.stop('test cleanup')
    def credentials(self):return dict(token=self.c.token,session=self.c.session,confirm_real=True)
    async def test_one_controller_preserves_role_and_busy_owner(self):
        self.assertTrue(self.c.configure(self.payload,role='track')['ok'])
        self.assertEqual(self.c.snapshot()['tcp_center'],[-480.,0.,650.])
        with patch('backend.core.real_follow_runtime.RealPalmFollowHost',fixtures.FakeHost):
            self.assertTrue((await self.c.start(self.credentials()))['ok'])
            follow=self.c.snapshot(role='follow')
            self.assertFalse(follow['active']);self.assertEqual(follow['token'],'');self.assertTrue(follow['busy'])
            self.assertFalse(self.c.configure({**self.payload,'standoff':0.},role='follow')['ok'])
            denied=await self.world.real_hand_follow_action('stop',{},role='follow')
            self.assertFalse(denied['ok']);self.assertTrue(self.c.active)
    async def test_real_gates_not_unlocked_by_track_configuration(self):
        self.assertTrue(self.c.configure(self.payload,role='track')['ok'])
        self.world._real_follow_qualification_reader=lambda:None
        self.assertFalse((await self.c.start(self.credentials()))['ok']);self.assertEqual(self.world.real.calls,[])
        self.world._real_follow_qualification_reader=lambda:deepcopy(self.record)
        self.world.real.snap['override']=.10
        self.assertFalse((await self.c.start(self.credentials()))['ok']);self.assertEqual(self.world.real.calls,[])
    async def test_tcp_offset_bounds_also_must_fit_real_fence(self):
        self.world.settings['fence'].update(x=[-700.,-500.])
        self.assertFalse(self.c.configure(self.payload,role='track')['ok'])
        self.assertEqual(self.world.real.calls,[])
    async def test_old_or_other_strategy_record_cannot_qualify_new_role(self):
        self.record.pop('approved_policies')
        self.assertIn('policy_not_commissioned',{b['code'] for b in self.c.snapshot(role='track')['blockers']})
        self.record['approved_policies']=[self.c.POLICIES['follow']]
        self.assertTrue(self.c.snapshot(role='follow')['available'])
        self.assertFalse(self.c.snapshot(role='track')['available'])
        self.assertTrue(self.c.configure(self.payload,role='track')['ok'])
        self.assertFalse((await self.c.start(self.credentials()))['ok'])
        self.assertEqual(self.world.real.calls,[])
    async def test_whole_chord_rejects_tilt_between_upright_endpoints(self):
        from types import SimpleNamespace as NS
        class Model:
            def capsules(self,q,**kwargs):return []
            def pose_vec(self,q):return [q[0],0.,650.,0.,0.,0.]
        self.c._model=Model();self.c._bound='current';self.c._context=lambda:'current'
        self.c.host=NS(phase='following')
        self.c._config=dict(tcp_bounds=dict(min=[-100.,-100.,600.],max=[100.,100.,700.]),forward=[-1.,0.,0.])
        self.world.objects=NS(list=lambda:[]);self.world._object_hits=lambda *a,**kw:[]
        start=[0.]*6;end=[1.,0.,0.,0.,0.,0.]
        with patch('backend.core.real_follow_runtime.evaluate',return_value={'level':'green'}):
            with patch('backend.core.camera_frame.camera_axes',return_value=([-1.,0.,0.],[0.,0.,1.])):
                self.assertTrue(self.c._validate_path(start,end))
            def axes(model,q):return ([-1.,0.,0.],[0.,0.,.9 if .4<q[0]<.6 else 1.])
            with patch('backend.core.camera_frame.camera_axes',side_effect=axes):
                self.assertFalse(self.c._validate_path(start,end))
        self.c.host=None
    async def test_route_roles_owned_capture_and_shared_stop(self):
        calls=[]
        def verified(action,payload):
            calls.append(action)
            if action=='sample':
                if payload.get('frame_seq')!=7:raise ValueError('stale frame')
                return {**payload,'_verified_frame_seq':7},time.monotonic()
            return dict(payload),None
        app=FastAPI();app.include_router(create_real_track_router(self.world,verified,self.world._hand_camera_reader))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
            status=(await client.get('/api/real/hand-track/status')).json()
            self.assertEqual(status['state']['role'],'track');self.assertFalse(status['state']['configured'])
            wrong=(await client.post('/api/real/hand-track/sample',json={})).json()
            self.assertFalse(wrong['ok']);self.assertEqual(calls,[])
            self.assertEqual((await client.post('/api/real/hand-track/status',json={})).status_code,405)
            self.assertTrue((await client.post('/api/real/hand-track/configure',json=self.payload)).json()['ok'])
            self.world.real.snap['stamp']=time.monotonic()
            with patch('backend.core.real_follow_runtime.RealPalmFollowHost',fixtures.FakeHost):
                self.assertTrue((await client.post('/api/real/hand-track/start',json=self.credentials())).json()['ok'])
                await asyncio.sleep(.015)
                sample=dict(token=self.c.token,session=self.c.session,seq=1,frame_seq=7,hand_present=True,u=.5,v=.5,hand_open=.5)
                self.assertTrue((await client.post('/api/real/hand-track/sample',json=sample)).json()['ok'])
                self.assertEqual(self.c.host.samples[-1]['frame_seq'],7)
                self.assertTrue((await client.post('/api/real/hand-track/stop',json=self.credentials())).json()['ok'])

class HostTrack(unittest.IsolatedAsyncioTestCase):
    async def test_full_pose_pipeline_uses_measured_wrist_without_reset_or_unlocking_limits(self):
        fixture=pipeline_fixtures.Bridge('test_four_directions_share_the_same_position_mapping_once');fixture.setUp()
        pipeline=fixture.pipeline;pipeline.lock_j6=False
        calls=[]
        def solve(model,q,target,lower,upper,**kwargs):
            calls.append((list(q),list(lower),list(upper)))
            return {'q':[q[0]+1.,*q[1:5],q[5]+1.]}
        pipeline.solve=solve
        self.assertTrue(fixture.sample(u=.7))
        self.assertTrue(await pipeline.publish_latest([0.,0.,0.,0.,0.,12.]))
        self.assertEqual(calls[0][0][5],12.)
        self.assertEqual(calls[0][1][5],fixture.driver.profile.lower[5])
        self.assertEqual(calls[0][2][5],fixture.driver.profile.upper[5])
        self.assertEqual(fixture.driver.published[-1][0][5],13.)
        self.assertFalse(await pipeline.publish_latest([1.,0.,0.,0.,0.,13.]))
    async def test_track_start_goes_directly_to_offset_center_before_servo(self):
        from backend.core.real_follow_host import RealPalmFollowHost
        fixture=host_fixtures.Host('test_center_first_no_servo_until_hand_then_zero_wrist_hold');fixture.setUp()
        def center(model,q,target,lo,hi,**kwargs):
            return {'q':[target[0],target[1],target[2]-500.,0.,0.,0.]}
        host=RealPalmFollowHost(host_fixtures.Model(),fixture.driver,fixture.finite,lambda:deepcopy(fixture.feedback),
            lambda:deepcopy(fixture.context),lambda a,b:True,clock=fixture.clock,sleep=fixture.sleep,startup_solve=center)
        self.assertTrue(await host.begin(center=[-60.,0.,500.],size=[20.,20.,20.],direction=270,standoff=120.,role='track'),host.reason)
        self.assertEqual(fixture.finite.calls,[('center',[60.,0.,0.,0.,0.,0.])])
        self.assertEqual(fixture.transport.packets,[])
        self.assertEqual(host.startup.center,[60.,0.,500.]);self.assertTrue(host.pipeline._ready())
        self.assertTrue((await host.stop())['stop_confirmed'])

if __name__=='__main__':unittest.main(verbosity=2)
