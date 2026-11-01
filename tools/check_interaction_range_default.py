"""Shared interaction defaults: persistence only, no device lifecycle or motion."""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import httpx
from backend import main
from backend.core import config
from backend.schemas import validate_settings
VALUE={'center':[500,0,700],'size':[900,300,500],'direction':270,'depthRange':[.06,.30]}
class InteractionDefaults(unittest.IsolatedAsyncioTestCase):
 async def test_api_save_reopen_and_independent_domain(self):
  with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
   root=Path(directory);a=main.create_app(main.build_dependencies(data_dir=root/'a'));b=main.build_dependencies(data_dir=root/'b')
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a),base_url='http://127.0.0.1:8710') as client:
    response=await client.post('/api/settings',json={'interaction_range_default':VALUE});self.assertEqual(response.status_code,200);self.assertTrue(response.json()['ok']);self.assertEqual(response.json()['settings']['interaction_range_default'],VALUE)
   reopened=main.build_dependencies(data_dir=root/'a')
   self.assertEqual(reopened.world.settings['interaction_range_default'],VALUE);self.assertIsNone(b.world.settings['interaction_range_default'])
   self.assertEqual(json.loads((root/'a/settings.json').read_text(encoding='utf-8'))['interaction_range_default'],VALUE)
 async def test_running_task_save_is_display_only_without_permission_or_stop(self):
  with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
   resources=main.build_dependencies(data_dir=directory);world=resources.world;app=main.create_app(resources)
   world.runner.state='running';world.settings['mode']='real';self.assertFalse(world.motion_ok());epoch=world.executor.epoch;revision=world._cfg_rev
   stop=Mock(side_effect=AssertionError('display preference must not stop motion'))
   with patch.object(world,'_request_real_jog_stop',stop):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8710') as client:
     response=await client.post('/api/settings',json={'interaction_range_default':VALUE})
   self.assertTrue(response.json()['ok']);self.assertEqual(world.runner.state,'running');self.assertEqual(world._cfg_rev,revision);self.assertEqual(world.executor.epoch,epoch);self.assertFalse(world.motion_ok());self.assertIsNone(world.real);stop.assert_not_called()
 async def test_write_failure_keeps_memory_and_disk_unchanged_and_never_success(self):
  with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
   r=main.build_dependencies(data_dir=directory);r.world.update_settings({'interaction_range_default':VALUE});app=main.create_app(r)
   file=Path(directory)/'settings.json';before=file.read_bytes();memory=copy.deepcopy(r.world.settings);changed={**VALUE,'center':[400,0,700]}
   with patch('backend.core.config.write_json',side_effect=OSError('disk full')):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://127.0.0.1:8710') as client:response=await client.post('/api/settings',json={'interaction_range_default':changed})
   self.assertTrue(response.status_code>=400);self.assertEqual(file.read_bytes(),before);self.assertEqual(r.world.settings,memory);self.assertEqual(r.world._settings_store.get_settings(),memory)
 async def test_api_bad_preference_never_writes_or_changes_memory(self):
  with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
   resources=main.build_dependencies(data_dir=directory);app=main.create_app(resources);before=copy.deepcopy(resources.world.settings)
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://127.0.0.1:8710') as client:
    response=await client.post('/api/settings',json={'interaction_range_default':{**VALUE,'size':[0,300,500]}})
   self.assertTrue(response.status_code>=400);self.assertEqual(resources.world.settings,before);self.assertFalse((Path(directory)/'settings.json').exists())
 async def test_corrupt_legacy_value_falls_back_without_overwriting_original(self):
  bad=[{}, {'center':[1,2,3]}, {**VALUE,'center':[float('nan'),0,700]}, {**VALUE,'direction':True}, {**VALUE,'depthRange':[.3,.2]}, 'bad']
  for value in bad:
   with self.subTest(value=value),tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
    file=Path(directory)/'settings.json';file.write_text(json.dumps({'interaction_range_default':value}),encoding='utf-8');before=file.read_bytes()
    store=config.SettingsStore(directory);self.assertIsNone(store.get_settings()['interaction_range_default']);self.assertEqual(file.read_bytes(),before)
 async def test_validators_reject_bad_fields_and_direct_save_is_atomic(self):
  cases=[{**VALUE,'size':[0,300,500]},{**VALUE,'size':[900,300,5001]},{**VALUE,'center':[10001,0,0]},{**VALUE,'direction':45},{**VALUE,'depthRange':[.06,.061]},{**VALUE,'extra':True},{**VALUE,'depthRange':[True,.3]}]
  for value in cases:
   with self.subTest(value=value):
    with self.assertRaises(ValueError):validate_settings({'interaction_range_default':value})
    with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
     store=config.SettingsStore(directory);before=copy.deepcopy(store.get_settings())
     with self.assertRaises(ValueError):store.save_settings({'interaction_range_default':value})
     self.assertEqual(store.get_settings(),before);self.assertFalse((Path(directory)/'settings.json').exists())
  validate_settings({'interaction_range_default':None});validate_settings({'interaction_range_default':VALUE})
if __name__=='__main__':unittest.main()
