"""Current tool/model identity and full-depth palm mapping, with no hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core import config, sim_tool, tool_library
from backend.core.hand_follow import HandFollow
from backend.core.world import World


class PalmDepth(unittest.TestCase):
    def configure(self, direction=0, depth_range=None):
        a=math.radians(direction)
        center=[400*math.sin(a),-400*math.cos(a),700]
        model=sim_tool.model_for('elfin5',sim_tool.DEFAULT_TOOL)
        f=HandFollow(model)
        payload=dict(session='offline',aspect=4/3,depth=True,rotation=False,direction=direction,
            depth_range=depth_range if depth_range is not None else [.06,.30],
            enclosure={'min':[v-d for v,d in zip(center,[400,150,250])],
                       'max':[v+d for v,d in zip(center,[400,150,250])]})
        lo,hi=map(list,zip(*config.MANUAL_LIMITS))
        r=f.configure(payload,lower=lo,upper=hi,now=10)
        return f,r

    def test_min_max_and_mid_circle_span_full_depth_in_all_four_directions(self):
        for direction in (0,90,180,270):
            for scale,fraction in ((.01,0),(.06,0),(.18,.5),(.30,1),(.9,1)):
                with self.subTest(direction=direction,scale=scale):
                    f,r=self.configure(direction);self.assertTrue(r['ok'],r)
                    f.start(dict(token=f.token,session=f.session),now=10)
                    r=f.sample(dict(token=f.token,session=f.session,seq=1,u=.5,v=.5,
                        palm_scale=scale,angle=0,age_ms=0),now=10.1)
                    self.assertTrue(r['ok'],r)
                    local=f._rotate_pose(f.target,inverse=True)
                    self.assertAlmostEqual(local[1],f._hi[1]-fraction*(f._hi[1]-f._lo[1]))
                    self.assertTrue(f._inside(f.target))
                    self.assertEqual(f.base_offset,[0,0,0])

    def test_invalid_or_degenerate_depth_calibration_never_arms(self):
        for value in ([.3,.06],[.2,.2],[.06,.061],[False,.3],[.06,float('nan')],['x',.3]):
            f,r=self.configure(depth_range=value)
            self.assertFalse(r['ok']);self.assertFalse(f.configured)


class ToolIdentity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config._settings=copy.deepcopy(config.DEFAULTS)
        config._settings.update(mode='sim',auto_connect=False,tts=False)
        self.world=World()

    async def asyncTearDown(self):
        await self.world.stop()

    async def test_downloaded_tools_switch_model_tcp_envelope_and_profile_together(self):
        world=self.world
        library=tool_library.entries()
        self.assertGreaterEqual(len(library),3,'Ship three actual downloaded end-effectors, not empty placeholders')
        actual_mount=copy.deepcopy(world.tools.mounted)
        for entry in library:
            result=world.select_tool(entry['id'])
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['tool']['id'],entry['id'])
            self.assertEqual(result['mounted']['scope'],'simulation')
            self.assertEqual(result['visual']['id'],entry['id'])
            model=world.motion_model()
            self.assertTrue(getattr(model,'library_tool',None))
            caps=[cap for cap in model.capsules([0]*6) if cap['link']==model.flange]
            self.assertGreaterEqual(len(caps),1)
            self.assertEqual(model.library_tool['bounds_mm'],tool_library.collision_bounds(entry))
            for i in range(3):
                self.assertLessEqual(model.library_tool['bounds_mm']['min'][i],entry['bounds_mm']['min'][i])
                self.assertGreaterEqual(model.library_tool['bounds_mm']['max'][i],entry['bounds_mm']['max'][i])
            self.assertTrue(all(math.isfinite(n) for c in caps for n in [*c['a'],*c['b'],c['r_mm']]))
            self.assertTrue(all(c['r_mm']>0 for c in caps))
            snap=world.snapshot()
            self.assertEqual(snap['tools']['visual']['id'],entry['id'])
            self.assertEqual(snap['tcp_frame']['translation_mm'],result['tool']['tcp']['translation_mm'])
            self.assertEqual(world.tools.mounted,actual_mount,'Sim tool selection must not confirm a physical installation')
        self.assertTrue(world.select_tool('none')['ok'])
        self.assertIsNone(world.tool_snapshot()['visual'])
        self.assertFalse(getattr(world.motion_model(),'library_tool',None))

    async def test_unknown_library_and_client_tcp_cannot_replace_trusted_geometry(self):
        world=self.world
        self.assertFalse(world.select_tool('missing-tool')['ok'])
        self.assertFalse(world.set_sim_tool({'kind':'library','id':'missing-tool'})['ok'])
        item=tool_library.entries()[0]
        self.assertTrue(world.set_sim_tool({'kind':'library','id':item['id'],
            'tcp_mm':[90000,0,0],'bounds_mm':{'min':[-1]*3,'max':[1]*3}})['ok'])
        self.assertNotEqual(world.motion_model().tcp_translation_mm,[90000,0,0])

    async def test_tool_switch_after_settings_change_updates_live_and_saved_state(self):
        world=self.world
        world.update_settings({'ghost_opacity':.42})
        entry=next(item for item in tool_library.entries() if item['id']=='robotiq-2f140')
        result=world.select_tool(entry['id'])
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['tool']['id'],entry['id'])
        self.assertEqual(world.settings['sim_tool']['id'],entry['id'])
        self.assertEqual(config.get_settings()['sim_tool']['id'],entry['id'])
        self.assertEqual(world.snapshot()['tools']['visual']['id'],entry['id'])
        world.update_settings({'trail_len':38})
        self.assertEqual(world.tool_snapshot()['tool']['id'],entry['id'])

    async def test_moving_or_following_tool_switch_is_rejected(self):
        world=self.world;old=copy.deepcopy(world.settings.get('sim_tool',sim_tool.DEFAULT_TOOL))
        world.sim.jog=(0,1)
        self.assertFalse(world.select_tool('none')['ok'])
        world.sim.jog=None;world.hand_follow.active=True
        self.assertFalse(world.select_tool('none')['ok'])
        world.hand_follow.active=False
        self.assertEqual(world.settings.get('sim_tool',sim_tool.DEFAULT_TOOL),old)

    async def test_geometry_changes_invalidate_even_an_in_flight_plan_context(self):
        world=self.world
        before=world.plan_context()
        self.assertTrue(world.select_tool(tool_library.entries()[0]['id'])['ok'])
        self.assertNotEqual(world.plan_context(),before)
        updated=world.plan_context();world.settings['tts']=not world.settings.get('tts')
        self.assertEqual(world.plan_context(),updated,'Cosmetic preference changes do not alter trajectory geometry')

    async def test_all_downloaded_tools_can_use_the_same_follow_center_without_moving_the_base(self):
        for entry in tool_library.entries():
            with self.subTest(tool=entry['id']):
                print('Follow center with',entry['id'],flush=True)
                self.assertTrue(self.world.select_tool(entry['id'])['ok'])
                model=self.world.motion_model();follow=HandFollow(model)
                lo,hi=self.world.limits()
                result=follow.configure(dict(session='offline',aspect=4/3,depth=True,rotation=False,
                    direction=0,depth_range=[.06,.30],enclosure={'min':[-400,-550,450],'max':[400,-250,950]}),
                    lower=lo,upper=hi,now=10)
                self.assertTrue(result['ok'],result)
                self.assertLess(math.dist(follow.actual[:3],[0,-400,700]),.2)
                self.assertEqual(follow.base_offset,[0,0,0]);self.assertEqual(follow.q[5],0)

    async def test_sim_actions_do_not_write_real_tool_installation(self):
        world=self.world
        item=next(e for e in tool_library.entries() if e['type']=='parallel_gripper')
        self.assertTrue(world.select_tool(item['id'])['ok'])
        with patch.object(world.tools,'record_action',side_effect=AssertionError('physical tool profile write')):
            result=await world.tool_action('close')
        self.assertTrue(result['ok']);self.assertTrue(result['sim'])
        self.assertEqual(result['mounted']['action_state'],'active')
        world.settings['mode']='real'
        self.assertNotIn('visual',world.tool_snapshot())
        self.assertFalse(getattr(world.motion_model(),'library_tool',None))


if __name__=='__main__':
    import faulthandler
    faulthandler.dump_traceback_later(30,exit=True)
    unittest.main(verbosity=2)
