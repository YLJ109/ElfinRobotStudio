"""Isolated production World/API adapter checks, synthetic devices/records.

No server startup/lifespan, no camera capture, no controller qualification.
"""
from _offline_guard import require_offline_copy
require_offline_copy()
from _app_test_support import application_for

import asyncio
from copy import deepcopy
from dataclasses import asdict
import socket
import time
from types import SimpleNamespace as NS, MethodType
import unittest
from unittest.mock import patch

import httpx
from check_real_servo import profile, FakeTransport, Clock
from backend.core import config
from backend.core.tools import DEFAULT_TOOLS
from backend.core.real_follow_tool_binding import resolve_real_follow_tool
from backend.core.real_follow_qualification import resolve_record
from backend.core.real_follow_runtime import RealPalmFollowController


class FakeLink:
    def __init__(self):
        self.epoch=1;self.command_session=2;self._writer=object();self.frame_seq=1
        self.cmd_up=self.stream_up=True
        self.snap=dict(joints=[0.]*6,vel=[0.]*6,stamp=time.monotonic(),override=.05,
            fsm=33,enabled=1,moving=0,inpos=1,err_code=0,paused=0,free_drive=0)
        self.calls=[]
        self.transport=FakeTransport({},Clock())
    def state_valid(self):return True
    def controller_info(self):return dict(version='SYNTHETIC-NOT-HARDWARE')
    def servo_transport(self):self.calls.append('transport');return self.transport


class FakeHost:
    def __init__(self,model,driver,finite,read,context,validate):
        self.driver=driver;self.owned=False
        self.phase='idle';self.reason=''
        self.pipeline=NS(token='server-only-input-token')
        self.samples=[]
    async def begin(self,**kwargs):
        self.phase='waiting_hand';self.reason='TEST-only到位'
        return True
    async def pump(self):await asyncio.sleep(.005);return False
    def submit(self,**sample):self.samples.append(sample);return True
    def request_stop(self,reason):self.phase='stopping'
    async def stop(self,reason):
        self.phase='stopped';self.reason=reason
        return dict(phase='stopped',reason=reason,stop_confirmed=True)


def fake_world():
    tool=deepcopy(DEFAULT_TOOLS[0])
    mounted=dict(tool_id='none',state='none_confirmed',source='manual',revision=1)
    settings=deepcopy(config.DEFAULTS)
    settings.update(mode='real',real_speed_pct=5)
    camera=lambda:dict(active=dict(state='streaming',session='camera-test',last_frame_at=time.time(),
        images=dict(color=dict(width=640,height=480,seq=7))))
    world=NS(settings=settings,real=FakeLink(),model=NS(variant='elfin5'),_cfg_rev=0,_real_jog_generation=0,
        _hand_camera_reader=camera,tools=NS(mounted=mounted,get=lambda _:deepcopy(tool)),
        executor=NS(busy=False),runner=NS(state='idle'),queue_state='idle',sim=NS(traj=None,jog=None),
        hand_follow=NS(active=False),free={'on':False},_ready_task=None,_speed_task=None,
        safety={'level':'green'},limits=lambda:([-180.]*6,[180.]*6),plan_context=lambda:'test-workcell',
        _stop_interlocked=lambda:False,_real_follow_qualification_reader=lambda:None)
    # Same production camera identity function; no camera service operations.
    from backend.core.world import World
    world._hand_camera_identity=World._hand_camera_identity
    world.real_jog_follow=RealPalmFollowController(world)
    world._request_real_jog_stop=lambda reason:world.real_jog_follow.request_stop(reason)
    world.real_hand_follow_action=MethodType(World.real_hand_follow_action,world)
    binding=resolve_real_follow_tool(mounted,tool,config_revision=0,variant='elfin5')
    p=asdict(profile())
    for key in ('lower','upper','evidence'):p.pop(key)
    record=dict(status='verified',scope='real',interface='v6-ascii-cycle',port=10003,
        record_id='TEST-ONLY',verified_by='TEST',verified_at='TEST',controller_version='SYNTHETIC-NOT-HARDWARE',
        robot_variant='elfin5',tool_context=binding['context_fingerprint'],lower=[-180.]*6,upper=[180.]*6,
        evidence=dict(firmware='TEST',tool_load='TEST',stop='TEST',path_timing='TEST'),profile=p)
    return world,record


class Runtime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.network=patch.object(socket.socket,'connect',side_effect=AssertionError('No device/network'))
        self.network.start();self.addCleanup(self.network.stop)
        self.allow=patch('backend.core.real_follow_runtime.config.no_real',return_value=False)
        self.allow.start();self.addCleanup(self.allow.stop)
        self.world,self.record=fake_world()
        self.control=self.world.real_jog_follow
        self.payload=dict(enclosure=dict(min=[-50.,-50.,450.],max=[50.,50.,550.]),direction=0,
            rotation=False,depth=True,depth_mode='gesture',session='camera-test',aspect=640/480)

    async def asyncTearDown(self):
        if self.control.host is not None:await self.control.stop('test teardown')

    def qualify_fixture(self):self.world._real_follow_qualification_reader=lambda:deepcopy(self.record)
    def credentials(self):return dict(token=self.control.token,session='camera-test',confirm_real=True)

    async def test_default_missing_record_is_visible_and_never_acquires_or_sends(self):
        state=self.control.snapshot()
        self.assertFalse(state['available'])
        self.assertIn('missing_commissioning',{b['code'] for b in state['blockers']})
        self.assertTrue(self.control.configure(self.payload)['ok'])
        self.assertFalse((await self.control.start(self.credentials()))['ok'])
        self.assertEqual(self.world.real.calls,[])

    async def test_installed_world_and_link_factories_use_new_production_types(self):
        from backend import main
        from backend.core.real_follow_link import ServoCapableRealLink
        self.assertIsInstance(main.world.real_jog_follow,RealPalmFollowController)
        from backend.core import world as world_module
        self.assertIs(world_module.RealLink,ServoCapableRealLink)
        self.assertFalse(main.world.real_jog_follow.snapshot()['available'])

    async def test_browser_qualification_fields_do_not_unlock_missing_record(self):
        self.control.configure(self.payload)
        payload={**self.credentials(),'commissioned':True,'profile':self.record,'hardware_qualified':True}
        self.assertFalse((await self.control.start(payload))['ok'])
        self.assertEqual(self.world.real.calls,[])

    async def test_no_real_process_guard_and_actual_speed_still_block_qualified_fixture(self):
        self.qualify_fixture()
        with patch('backend.core.real_follow_runtime.config.no_real',return_value=True):
            self.assertIn('real_disabled',{b['code'] for b in self.control.snapshot()['blockers']})
        self.world.real.snap['override']=.051
        self.assertIn('actual_override',{b['code'] for b in self.control.snapshot()['blockers']})

    async def test_version_tool_and_limits_changes_invalidate_record(self):
        for key,value in (('controller_version','other'),('tool_context','other'),('upper',[90.]*6)):
            self.qualify_fixture();saved=self.record[key];self.record[key]=value
            self.assertIn('commissioning_context_changed',{b['code'] for b in self.control.snapshot()['blockers']})
            self.record[key]=saved

    async def test_known_tool_is_not_silently_converted_to_none(self):
        self.qualify_fixture()
        self.world.tools.mounted.update(tool_id='suction-template')
        self.world.tools.get=lambda _:deepcopy(DEFAULT_TOOLS[2])
        state=self.control.snapshot()
        self.assertFalse(state['available'])
        self.assertTrue(any(b['layer']=='geometry' for b in state['blockers']))
        self.assertEqual(self.world.real.calls,[])

    async def test_read_status_and_configuration_do_not_acquire_channel(self):
        self.qualify_fixture()
        for _ in range(5):self.assertTrue(self.control.snapshot()['available'])
        self.assertTrue(self.control.configure(self.payload)['ok'])
        self.assertEqual(self.world.real.calls,[])

    async def test_start_is_immediate_and_stop_invalidates_credentials(self):
        self.qualify_fixture();self.control.configure(self.payload)
        with patch('backend.core.real_follow_runtime.RealPalmFollowHost',FakeHost):
            result=await self.control.start(self.credentials())
            self.assertTrue(result['ok']);self.assertEqual(result['state']['phase'],'centering')
            token=result['state']['token']
            self.assertTrue(self.control.busy)
            await asyncio.sleep(.015)
            self.assertEqual(self.control.host.phase,'waiting_hand')
            self.assertTrue((await self.control.stop())['ok'])
            self.assertNotEqual(self.control.token,token)
            self.assertFalse(self.control.sample(dict(token=token,session='camera-test',seq=1))['ok'])

    async def test_actual_http_status_and_frame_proof_forwarding(self):
        from backend import main
        # Module import is intentionally not a feedback heartbeat. Publish an
        # explicit synthetic captures, as hardware would, rather than weakening
        # the 150ms production freshness gate. Application composition is not
        # a feedback publisher and may take longer than that TTL under load.
        self.world.real.snap['stamp']=time.monotonic()
        def publish_feedback():
            self.world.real.frame_seq+=1
            self.world.real.snap['stamp']=time.monotonic()
        self.qualify_fixture()
        camera_state=self.world._hand_camera_reader()
        camera=NS(snapshot=lambda:deepcopy(camera_state),stream_frame=lambda *a:('TEST',time.time()),
                  observation_stamp=lambda session,seq:time.monotonic() if session=='camera-test' and seq==7 else None)
        with application_for(world=self.world,cameras=camera) as test_app, patch('backend.core.real_follow_runtime.RealPalmFollowHost', FakeHost):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app),base_url='http://localhost') as client:
                # Deterministically model a cold application composition longer
                # than the real 150ms feedback TTL, without changing that TTL.
                await asyncio.sleep(.16)
                status=(await client.get('/api/real/hand-follow/status')).json()
                self.assertEqual(status['driver'],'StartServo/PushServoJ')
                self.assertIn('stale_feedback',{b['code'] for b in status['blockers']})
                configured=(await client.post('/api/real/hand-follow/configure',json=self.payload)).json()
                self.assertTrue(configured['ok'])
                stale=(await client.post('/api/real/hand-follow/start',json=self.credentials())).json()
                self.assertFalse(stale['ok'])
                self.assertIn('stale_feedback',{b['code'] for b in stale['state']['blockers']})
                self.assertEqual(self.world.real.calls,[])
                publish_feedback()
                started=(await client.post('/api/real/hand-follow/start',json=self.credentials())).json()
                self.assertTrue(started['ok'],started)
                await asyncio.sleep(.015)
                sample=dict(token=self.control.token,session='camera-test',seq=1,frame_seq=7,
                    _verified_frame_seq=9999,u=.5,v=.5,hand_open=.5,hand_present=True)
                publish_feedback()
                result=(await client.post('/api/real/hand-follow/sample',json=sample)).json()
                self.assertTrue(result['ok'],result)
                self.assertEqual(self.control.host.samples[-1]['frame_seq'],7)
                rejected=await client.post('/api/real/hand-follow/sample',json={**sample,'seq':2,'frame_seq':8})
                self.assertEqual(rejected.status_code,422)  # existing ValueError API contract
                stopped=(await client.post('/api/real/hand-follow/stop',json={})).json()
                self.assertTrue(stopped['ok'])

    async def test_qualification_rejects_unbounded_or_partial_parameters(self):
        for profile_patch in ({'feedback_max_age_s':1.},{'stop_timeout_s':30.},{'reply_timeout_s':1.}):
            record=deepcopy(self.record);record['profile'].update(profile_patch)
            result=resolve_record(record,version=record['controller_version'],variant='elfin5',
                tool_context=record['tool_context'],lower=record['lower'],upper=record['upper'])
            self.assertFalse(result['ok'])


if __name__=='__main__':unittest.main(verbosity=2)
