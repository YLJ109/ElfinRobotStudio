"""Dual suction production contract, fake IO only; never open hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()
from _app_test_support import application_for
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from backend.core import config
from backend.core.world import World


def mapping():
    return {'version':1,'sides':{side:{kind:{'alias':f'{side}_{kind}','on':1}
            for kind in ('vacuum','blow')} for side in ('left','right')}}


class Dual(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state=patch.object(config,'_settings',deepcopy(config.DEFAULTS));state.start();self.addCleanup(state.stop)
        self.world=World()
        self.world.select_tool('vacuum-reference')

    async def test_eight_addresses_are_independent_and_same_side_exclusive(self):
        w=self.world
        for side in ('left','right'):
            self.assertTrue((await w.tool_action('vacuum_on',side=side))['ok'])
            self.assertFalse((await w.tool_action('blow_on',side=side))['ok'])
            self.assertTrue((await w.tool_action('vacuum_off',side=side))['ok'])
            self.assertTrue((await w.tool_action('blow_on',side=side))['ok'])
            self.assertFalse((await w.tool_action('vacuum_on',side=side))['ok'])
            self.assertTrue((await w.tool_action('blow_off',side=side))['ok'])
        await w.tool_action('vacuum_on',side='left')
        self.assertTrue((await w.tool_action('blow_on',side='right'))['ok'])
        state=w.tool_snapshot()['mounted']['suction']
        self.assertTrue(state['left']['vacuum'])
        self.assertTrue(state['right']['blow'])
        self.assertIsNone(state['left']['holding'])
        self.assertEqual(w._sim_vacuum['vacuum'],state['left']['vacuum'])

    async def test_legacy_actions_use_left_and_invalid_side_has_no_effect(self):
        w=self.world
        self.assertTrue((await w.tool_action('vacuum_on'))['ok'])
        self.assertTrue(w._sim_vacuum['vacuum'])
        before=w.tool_snapshot()['mounted']['suction']
        self.assertFalse((await w.tool_action('vacuum_off',side='both'))['ok'])
        self.assertEqual(w.tool_snapshot()['mounted']['suction'],before)
        self.assertFalse((await w.tool_action('vacuum_off',side='left',duration_ms=500))['ok'])

    def setup_real(self):
        w=self.world
        w.settings['mode']='real'
        self.profile=deepcopy(w.tools.get('suction-template'))
        self.profile.update(id='dual-test',revision='r1',suction_io=mapping())
        w.tools.add_or_update(self.profile)
        w.tools.select('dual-test')
        for index,alias in enumerate(('left_vacuum','left_blow','right_vacuum','right_blow')):
            w.calibrations.items['io'][alias]={'direction':'output','group':'box','bit':index}
        self.values=[0]*8
        self.link=SimpleNamespace(command_session=7,epoch=1,frame_seq=10,
            state_valid=lambda:True,cmd_up=True,snap={'box_do':self.values,'end_do':[0]*3})
        w.real=self.link
        self.calls=[]
        async def write(alias,val):
            self.calls.append((alias,val))
            channel=w.calibrations.items['io'][alias]
            self.values[channel['bit']]=val
            self.link.frame_seq+=1
            return {'ok':True}
        w.write_alias=write
        self.assertTrue(w.confirm_tool_io('dual-test','r1')['ok'])

    async def test_real_requires_mapping_confirmation_and_fresh_readback(self):
        w=self.world
        w.settings['mode']='real'
        w.tools.select('suction-template')
        w.real=SimpleNamespace(state_valid=lambda:True,cmd_up=True)
        self.assertFalse((await w.tool_action('vacuum_on',side='left'))['ok'])
        self.setup_real()
        self.assertTrue((await w.tool_action('vacuum_on',side='right'))['ok'])
        self.assertEqual(self.calls,[('right_vacuum',1)])
        self.assertFalse((await w.tool_action('blow_on',side='right'))['ok'])
        self.assertEqual(len(self.calls),1)
        self.assertTrue((await w.tool_action('blow_on',side='left'))['ok'])

    async def test_io_change_and_reconnect_revoke_confirmation(self):
        self.setup_real()
        self.world.calibrations.items['version']+=1
        self.assertFalse((await self.world.tool_action('vacuum_on',side='left'))['ok'])
        self.assertEqual(self.calls,[])
        self.assertTrue(self.world.confirm_tool_io('dual-test','r1')['ok'])
        self.link.command_session+=1
        self.assertFalse((await self.world.tool_action('vacuum_on',side='left'))['ok'])
        self.assertEqual(self.calls,[])

    async def test_ack_without_new_frame_or_disconnect_is_not_success(self):
        self.setup_real()
        async def ack_only(alias,val): return {'ok':True}
        self.world.write_alias=ack_only
        self.assertFalse((await self.world.tool_action('vacuum_on',side='left'))['ok'])
        async def disconnected(alias,val):
            self.link.command_session+=1
            return {'ok':True}
        self.world.write_alias=disconnected
        self.assertFalse((await self.world.tool_action('vacuum_on',side='left'))['ok'])

    async def test_concurrent_manual_action_and_busy_task_rejected(self):
        self.setup_real()
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(alias,val):
            entered.set();await release.wait();return {'ok':False,'why':'synthetic failure'}
        self.world.write_alias=delayed
        task=asyncio.create_task(self.world.tool_action('vacuum_on',side='left'))
        await entered.wait()
        self.assertFalse((await self.world.tool_action('vacuum_on',side='right'))['ok'])
        release.set();self.assertFalse((await task)['ok'])
        self.world.runner.state='running'
        self.assertFalse((await self.world.tool_action('vacuum_on',side='left'))['ok'])

    async def test_mapping_overlap_invalid_and_profile_edit_requires_reconfirmation(self):
        self.setup_real()
        invalid=deepcopy(self.profile)
        invalid['suction_io']['sides']['right']['blow']['alias']='left_blow'
        with self.assertRaises(ValueError): self.world.tools.add_or_update(invalid)
        changed=deepcopy(self.profile);changed['revision']='r2'
        self.assertTrue(self.world.update_tool_profile(changed)['ok'])
        self.assertFalse((await self.world.tool_action('vacuum_on',side='left'))['ok'])
        reopened=type(self.world.tools)()
        self.assertNotEqual(reopened.mounted.get('io_confirmation',{}).get('profile_revision'),'r2')

    async def test_unknown_opposite_output_and_stale_state_never_send(self):
        self.setup_real()
        self.values[1]=None
        self.assertFalse((await self.world.tool_action('vacuum_on',side='left'))['ok'])
        self.assertEqual(self.calls,[])
        self.values[1]=0
        self.link.state_valid=lambda:False
        self.assertFalse((await self.world.tool_action('vacuum_on',side='left'))['ok'])
        self.assertIsNone(self.world.suction_snapshot()['left']['vacuum'])
        self.assertEqual(self.calls,[])

    async def test_cancellation_does_not_send_a_guessed_reverse_or_remain_successful(self):
        self.setup_real()
        entered=asyncio.Event()
        async def blocked(alias,val):
            self.calls.append((alias,val));entered.set();await asyncio.Event().wait()
        self.world.write_alias=blocked
        task=asyncio.create_task(self.world.tool_action('vacuum_on',side='left'))
        await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.calls,[('left_vacuum',1)])
        self.assertEqual(self.world.tools.mounted['action_state'],'unknown')
        self.assertFalse(self.world._tool_action_lock.locked())

    async def test_output_addresses_and_levels_must_be_explicit(self):
        self.setup_real()
        for change in ('input','same_address','bad_bit'):
            backup=deepcopy(self.world.calibrations.items)
            channel=self.world.calibrations.items['io']['right_blow']
            if change=='input': channel['direction']='input'
            if change=='same_address': channel['bit']=0
            if change=='bad_bit': channel['bit']=8
            self.assertFalse(self.world.confirm_tool_io('dual-test','r1')['ok'])
            self.world.calibrations.items=backup
        for patch_value in ({'tcp':'bad'},{'tcp':{'rpy_deg':[0,float('inf'),0]}},
                            {'payload':{'mass_kg':True}},{'revision':None},
                            {'suction_io':{'version':1,'sides':{}}}):
            response=self.world.update_tool_profile({'id':'invalid',**patch_value})
            self.assertFalse(response['ok'],patch_value)
            self.assertIsNone(self.world.tools.get('invalid'))

    async def test_active_low_outputs_are_confirmed_as_semantics_not_holding(self):
        self.setup_real()
        profile=deepcopy(self.profile)
        profile['suction_io']['sides']['left']['vacuum']['on']=0
        self.world.tools.add_or_update(profile)
        self.world.tools.select('dual-test')
        self.values[0]=1
        self.assertTrue(self.world.confirm_tool_io('dual-test','r1')['ok'])
        result=await self.world.tool_action('vacuum_on',side='left')
        self.assertTrue(result['ok'])
        self.assertEqual(self.calls,[('left_vacuum',0)])
        self.assertIsNone(result['mounted']['suction']['left']['holding'])

    async def test_right_release_cannot_drop_left_held_piece_and_projection_is_readonly(self):
        world=self.world
        released=[]
        world.robot_play=SimpleNamespace(held={'id':'piece'},active=False,release_manual=lambda:released.append(True))
        with self.assertRaises(TypeError): world._sim_vacuum['vacuum']=True
        self.assertTrue((await world.tool_action('vacuum_off',side='right'))['ok'])
        self.assertEqual(released,[])
        self.assertFalse(world.select_tool('none')['ok'])
        self.assertTrue((await world.tool_action('vacuum_off',side='left'))['ok'])
        self.assertEqual(released,[True])

    async def test_http_side_schema_and_profile_routes_use_production_core(self):
        import httpx
        from backend import main
        with application_for(world=self.world) as test_app:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app),base_url='http://127.0.0.1') as client:
                result=await client.post('/api/tools/action',json={'action':'vacuum_on','side':'right'})
                self.assertTrue(result.json()['ok'])
                self.assertFalse(result.json()['mounted']['suction']['left']['vacuum'])
                self.assertTrue(result.json()['mounted']['suction']['right']['vacuum'])
                self.assertEqual((await client.post('/api/tools/action',json={'action':'vacuum_on','side':'both'})).status_code,422)
                profile=await client.post('/api/tools/profile',json={'id':'api-dual','type':'suction','revision':'r1','suction_io':mapping()})
                self.assertTrue(profile.json()['ok'])
                confirmation=await client.post('/api/tools/io-confirm',json={'tool_id':'api-dual','revision':'r1'})
                self.assertFalse(confirmation.json()['ok'])

    async def test_reserved_output_cannot_bypass_tool_policy_and_motion_waits(self):
        self.setup_real()
        world=self.world
        result=await World.write_alias(world,'left_vacuum',1)
        self.assertFalse(result['ok'])
        result=await world.io_probe('box',0,1)
        self.assertFalse(result['ok'])
        world.calibrations.items['io']['left_vacuum'].update(group='end',bit=0)
        self.assertFalse((await world.write_end_do(0,1))['ok'])
        world.calibrations.items['io']['left_vacuum'].update(group='box',bit=0)
        entered=asyncio.Event();release=asyncio.Event()
        async def waiting(alias,val):
            self.assertTrue(world._tool_action_current())
            entered.set();await release.wait();return {'ok':False,'why':'fake canceled output'}
        world.write_alias=waiting
        task=asyncio.create_task(world.tool_action('vacuum_on',side='left'))
        await entered.wait()
        self.assertFalse(world._need(world.MOTION,'competing motion'))
        self.assertFalse(world._tool_action_current())
        release.set();await task
        self.assertIsNone(world._tool_action_task)

    async def test_profile_history_is_bounded_reopens_with_diff_and_atomic_on_failure(self):
        self.setup_real()
        from backend.core import tools
        before=self.world.tools.revisions('dual-test')
        changed=deepcopy(self.profile);changed.update(revision='r2',name='左右吸盘',provenance={'source':'synthetic test'})
        self.assertTrue(self.world.update_tool_profile(changed)['ok'])
        reopened=tools.ToolStore()
        rows=reopened.revisions('dual-test')
        self.assertEqual(rows[-1]['revision'],'r2')
        self.assertEqual(rows[-1]['source'],{'source':'synthetic test'})
        self.assertTrue(any(row['field']=='name' for row in rows[-1]['changes']))
        with patch.object(tools,'write_json',side_effect=OSError('fake full disk')):
            with self.assertRaises(OSError): reopened.add_or_update({'id':'dual-test','revision':'r3'})
        self.assertEqual(reopened.revisions('dual-test'),rows)
        for index in range(35): reopened.add_or_update({'id':'dual-test','revision':f'r{index+4}'})
        self.assertEqual(len(reopened.revisions('dual-test')),32)


if __name__=='__main__': unittest.main()
