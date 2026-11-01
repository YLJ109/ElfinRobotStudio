"""Explicit application ownership and persistence isolation; no services."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import httpx
from backend import main


class Factory(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_sim_fallback_survives_unrelated_setting_save(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
            root=Path(directory)
            (root/'settings.json').write_text(json.dumps({'mode':'real','auto_connect':False}),encoding='utf-8')
            resources=main.build_dependencies(data_dir=root)
            self.assertEqual(resources.world.settings['mode'],'sim')
            resources.world.update_settings({'tts':False})
            self.assertEqual(resources.world.settings['mode'],'sim')
            self.assertIsNone(resources.world.real)

    async def test_failed_speed_save_does_not_publish_new_runtime_speed(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
            world=main.build_dependencies(data_dir=directory).world
            before=world.settings['sim_speed_pct']
            with patch('backend.core.config.write_json',side_effect=OSError('full')):
                with self.assertRaises(OSError):world.set_speed(42,schedule=False)
            self.assertEqual(world.settings['sim_speed_pct'],before)
            self.assertEqual(world.sim.speed_pct,before)

    async def test_asset_references_never_resolve_through_another_data_domain(self):
        from check_scene_assets import glb,META
        from backend.core.scene_assets import SceneAssetStore,inspect_glb
        with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
            root=Path(directory)
            a=main.build_dependencies(data_dir=root/'a')
            b=main.build_dependencies(data_dir=root/'b')
            payload=glb()
            asset=SceneAssetStore(root/'a/scene_assets').commit(payload,inspect_glb(payload,META))
            # 摆放物必须落在机器人连杆之外：落位即拦截是既定行为，
            # 摆在底座上会被直接拒绝，测不到"数据域隔离"这件事。
            obj=a.world.objects.add({'name':'owned asset','kind':'box','xyz':[400,0,100],
                                    'size':asset['size_mm'],'asset_id':asset['asset_id']})
            with self.assertRaises(ValueError):b.world.objects.add(obj)
            a.scenes.put('asset-scene',[obj])
            reopened=main.build_dependencies(data_dir=root/'a')
            self.assertEqual(reopened.world.objects.list()[0]['asset_id'],asset['asset_id'])
            self.assertEqual(reopened.scenes.get('asset-scene')['objects'][0]['asset_id'],asset['asset_id'])
            self.assertEqual(b.world.objects.list(),[])

    async def test_data_and_settings_persist_only_inside_own_app_domain(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
            root=Path(directory)
            a=main.create_app(main.build_dependencies(data_dir=root/'a')).state.resources
            b=main.create_app(main.build_dependencies(data_dir=root/'b')).state.resources
            self.assertFalse((root/'a').exists(),'construction must not write')
            self.assertFalse((root/'b').exists(),'construction must not write')
            a.world.update_settings({'tts':False,'sim_speed_pct':21})
            a.programs.put('left','WAIT 0')
            a.world.tools.select('none')
            a.world.objects.add({'name':'left-only','xyz':[400,0,100]})
            self.assertTrue(b.world.settings['tts'])
            self.assertEqual(b.programs.list(),[])
            self.assertEqual(b.world.objects.list(),[])
            self.assertFalse((root/'b').exists())
            reopened=main.create_app(main.build_dependencies(data_dir=root/'a')).state.resources
            self.assertFalse(reopened.world.settings['tts'])
            self.assertEqual(reopened.programs.get('left')['src'],'WAIT 0')
            self.assertEqual(reopened.world.objects.list()[0]['name'],'left-only')
            self.assertEqual(reopened.world.tools.mounted['tool_id'],'none')
            for store in (a.world.points,a.world.objects,a.world.tools,a.world.calibrations,a.world.log,a.programs,a.scenes):
                self.assertTrue(store.path.resolve().is_relative_to((root/'a').resolve()))
            a.lease.acquire();b.lease.acquire()
            try:
                duplicate=main.build_dependencies(data_dir=root/'a').lease
                with self.assertRaisesRegex(RuntimeError,'CONTROL_OWNER_BUSY'):duplicate.acquire()
            finally:a.lease.release();b.lease.release()

    async def test_failed_setting_save_is_atomic_and_does_not_affect_other_app(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
            root=Path(directory)
            a=main.build_dependencies(data_dir=root/'a')
            b=main.build_dependencies(data_dir=root/'b')
            with patch('backend.core.config.write_json',side_effect=OSError('full')):
                with self.assertRaises(OSError):a.world.update_settings({'tts':False})
            self.assertTrue(a.world.settings['tts']);self.assertTrue(b.world.settings['tts'])
            self.assertFalse((root/'a').exists())

    async def test_two_apps_have_one_independent_world_per_app(self):
        left=main.create_app()
        right=main.create_app()
        a,b=left.state.resources,right.state.resources
        self.assertIsNot(a.world,b.world)
        self.assertIsNot(a.world.settings,b.world.settings)
        self.assertIsNot(a.cameras,b.cameras)
        self.assertIsNot(a.lease,b.lease)
        self.assertIsNot(a.world.motion_session,b.world.motion_session)
        self.assertIs(a.world.robot_play.world,a.world)
        self.assertIs(a.world.motion_session.world,a.world)
        a.world.sim_state['fsm']=19
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=left),base_url='http://127.0.0.1:8710') as client:
            state=(await client.get('/api/state')).json()
            self.assertEqual(state['sim_state']['fsm'],19)
        self.assertEqual(b.world.sim_state['fsm'],7)

    async def test_failure_cleanup_uses_only_its_own_resource_bundle(self):
        left,right=main.create_app(),main.create_app()
        a,b=left.state.resources,right.state.resources
        with patch.object(a.world,'start',new=AsyncMock(side_effect=RuntimeError('isolated start failure'))), \
             patch.object(a.world,'stop',new=AsyncMock()) as stop, \
             patch.object(a.vision,'close',new=AsyncMock()),patch.object(a.cameras,'close'), \
             patch.object(a.lease,'acquire'),patch.object(a.lease,'release') as release, \
             patch.object(b.world,'stop',new=AsyncMock()) as foreign:
            with self.assertRaisesRegex(RuntimeError,'isolated start failure'):
                async with left.router.lifespan_context(left):pass
        stop.assert_awaited_once();release.assert_called_once();foreign.assert_not_awaited()


if __name__=='__main__':unittest.main(verbosity=2)
