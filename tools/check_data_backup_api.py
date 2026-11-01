"""Production API validation and isolated reopening; no running device/application."""
from _offline_guard import require_offline_copy
require_offline_copy()
import tempfile
import asyncio
import threading
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx
from backend.main import create_app,build_dependencies

class BackupAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]);self.addCleanup(self.temp.cleanup)
        self.app=create_app(build_dependencies(data_dir=Path(self.temp.name)/'data'))
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1')
        self.addAsyncCleanup(self.client.aclose)
    async def test_export_preview_copy_activation_and_restart_are_separate(self):
        resources=self.app.state.resources
        resources.programs.put('备份程序','WAIT 0')
        exported=await self.client.get('/api/backup/export');self.assertEqual(exported.status_code,200,exported.text)
        self.assertIn('attachment',exported.headers['content-disposition'])
        preview=await self.client.post('/api/backup/preview',json=exported.json());self.assertEqual(preview.status_code,200,preview.text)
        restored=await self.client.post('/api/backup/restore-copy',json={'token':preview.json()['token']});self.assertEqual(restored.status_code,200,restored.text)
        self.assertFalse(restored.json()['active'])
        plan=await self.client.post('/api/backup/activation-plan',json={'copy_id':restored.json()['copy_id']});self.assertEqual(plan.status_code,200,plan.text)
        self.assertEqual(len(plan.json()['manifest_digest']),64)
        reopened=build_dependencies(data_dir=plan.json()['data_dir'])
        self.assertEqual(reopened.programs.get('备份程序')['src'],'WAIT 0')
        self.assertEqual(reopened.world.settings['mode'],'sim');self.assertIsNone(reopened.world.real)
        self.assertFalse(reopened.world.settings['auto_connect'])
        self.assertEqual(resources.data_dir,Path(self.temp.name)/'data')
    async def test_invalid_stale_and_non_idle_requests_cannot_create_recovery(self):
        for route,body in [('preview',{}),('restore-copy',{'token':'unknown'}),('activation-plan',{'copy_id':'../outside'})]:
            response=await self.client.post('/api/backup/'+route,json=body);self.assertEqual(response.status_code,422,response.text)
        self.app.state.resources.world.settings['mode']='real'
        response=await self.client.get('/api/backup/export');self.assertEqual(response.status_code,409)
        self.assertFalse((self.app.state.resources.data_dir/'recovery').exists())

    async def test_portable_package_larger_than_normal_api_limit_roundtrips_real_middleware(self):
        self.app.state.resources.programs.items={f'程序{i}':{'src':'# safe\n'*25000,'updated':0} for i in range(16)}
        exported=await self.client.get('/api/backup/export');self.assertEqual(exported.status_code,200)
        self.assertGreater(len(exported.content),2*1024*1024)
        response=await self.client.post('/api/backup/preview',content=exported.content,headers={'content-type':'application/json'})
        self.assertEqual(response.status_code,200,response.text)

    async def test_slow_validation_does_not_block_state_feedback(self):
        from backend.application.data_backup import validate_bundle
        exported=(await self.client.get('/api/backup/export')).json()
        entered,released=threading.Event(),threading.Event()
        def slow(bundle):
            entered.set()
            if not released.wait(3):raise RuntimeError('test release not received')
            return validate_bundle(bundle)
        with patch('backend.data_backup_routes.validate_bundle',side_effect=slow):
            pending=asyncio.create_task(self.client.post('/api/backup/preview',json=exported))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                response=await asyncio.wait_for(self.client.get('/api/state'),.5)
                self.assertEqual(response.status_code,200)
                self.assertFalse(pending.done(),'validation must still be blocked while feedback completes')
            finally:released.set()
            self.assertEqual((await pending).status_code,200)

    async def test_downloaded_export_survives_browser_json_number_normalization(self):
        exported=await self.client.get('/api/backup/export');self.assertEqual(exported.status_code,200)
        # JavaScript JSON.parse/stringify emits 0 and 1 for Python's 0.0/1.0.
        browser=json.loads(exported.content,parse_float=lambda s:int(float(s)) if float(s).is_integer() else float(s))
        response=await self.client.post('/api/backup/preview',json=browser)
        self.assertEqual(response.status_code,200,response.text)

if __name__=='__main__':unittest.main(verbosity=2)
