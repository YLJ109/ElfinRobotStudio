from _offline_guard import require_offline_copy
require_offline_copy()
from pathlib import Path
import tempfile,unittest
import asyncio,json
from unittest.mock import patch
from fastapi import FastAPI
import httpx
from backend.run_journal_routes import create_run_journal_router
from backend.core.program import Runner
from backend.application.run_journal import RunJournal
class Journal(unittest.TestCase):
    def test_dispatch_is_not_confirmation_and_cancel_preserves_last_confirmed(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=Runner();journal=RunJournal(runner,path=Path(folder)/'runs.json',context=lambda:{'session_id':'test','mode':'sim','config_revision':2,'scene_revision':'a'*64})
            runner.load([{'kind':'speed','pct':15,'line':1,'src':'SPEED 15'},{'kind':'wait','sec':10,'line':2,'src':'WAIT 10'}])
            runner.start();run_id=runner.snapshot()['run_id']
            runner.advance(0);runner.complete();runner.advance(1);runner.stop('取消')
            value=journal.get(run_id)
            self.assertEqual(value['last_confirmed']['line'],1);self.assertEqual(value['phase'],'aborted')
            self.assertEqual(value['confirmed_steps'],1)
    def fixture(self,path):
        runner=Runner();journal=RunJournal(runner,path=path,context=lambda:{'session_id':'unit','mode':'sim','config_revision':3,'scene_revision':'a'*64,'program_hash':'b'*64,'api_key':'not-allowed'})
        return runner,journal

    def test_wait_pause_resume_and_actual_confirmation_phase(self):
        with tempfile.TemporaryDirectory() as folder:
            runner,journal=self.fixture(Path(folder)/'runs.json')
            runner.load([{'kind':'wait','sec':10,'line':1,'src':'WAIT 10'}]);runner.start();identity=runner.run_id
            runner.advance(0);self.assertEqual(journal.get(identity)['phase'],'waiting')
            runner.pause(2);self.assertEqual(journal.get(identity)['phase'],'paused')
            runner.resume(100);self.assertEqual(journal.get(identity)['phase'],'waiting')
            runner.advance(107);self.assertIsNone(journal.get(identity)['last_confirmed'])
            runner.advance(108);value=journal.get(identity)
            self.assertEqual(value['phase'],'finished');self.assertEqual(value['last_confirmed']['line'],1)
            self.assertFalse(value['partial']);self.assertEqual(runner.run_id,identity)

    def test_plan_identity_is_shared_and_restart_marks_interrupted_without_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runs.json';runner,journal=self.fixture(path)
            runner.load([{'kind':'speed','line':4,'src':'SPEED 15','pct':15}]);runner.next_run_id='c'*32
            runner.start();self.assertEqual(runner.run_id,'c'*32);runner.advance(0);journal.flush()
            other=Runner();reopened=RunJournal(other,path=path,context=lambda:{})
            self.assertEqual(other.state,'idle');self.assertIsNone(other.run_id)
            self.assertEqual(reopened.get('c'*32)['phase'],'interrupted')
            self.assertIsNone(reopened.get('c'*32)['last_confirmed'])
            self.assertEqual(reopened.get('c'*32)['context']['scene_revision'],'a'*64)

    def test_stale_completion_and_repeated_stop_cannot_inflate_confirmed_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            runner,journal=self.fixture(Path(folder)/'runs.json')
            runner.load([{'kind':'speed','line':1,'src':'SPEED 15','pct':15}]);runner.start();identity=runner.run_id
            runner.advance(0);runner.stop('取消');runner.complete();runner.stop('重复停止')
            self.assertEqual(journal.get(identity)['confirmed_steps'],0)
            self.assertEqual(journal.get(identity)['reason'],'取消')

    def test_bounded_redacted_records_and_disk_failure_never_change_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runs.json';runner,journal=self.fixture(path)
            for _ in range(70):
                runner.load([{'kind':'speed','line':1,'src':'password=source-secret','pct':15}]);runner.start();runner.advance(0);runner.complete()
                runner.stop('连接192.168.1.1 HUYAN_TOKEN=secret-value Authorization=Bearer-secret-value')
            self.assertEqual(len(journal.list()['runs']),64)
            encoded=json.dumps(journal.list(),ensure_ascii=False)
            for sensitive in ('source-secret','secret-value','192.168.1.1','not-allowed','api_key'):self.assertNotIn(sensitive,encoded)
            before=runner.snapshot()
            with patch('backend.application.run_journal.write_json',side_effect=OSError('disk full')):journal.flush()
            self.assertEqual(runner.snapshot(),before);self.assertIsNotNone(journal.error)
            self.assertFalse(path.exists());journal.flush();self.assertIsNone(journal.error)
            self.assertLess(path.stat().st_size,2*1024*1024)

    def test_corrupt_or_credential_bearing_saved_record_is_preserved_and_not_served(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runs.json';runner,journal=self.fixture(path)
            runner.load([{'kind':'wait','line':1,'src':'WAIT 1','sec':1}]);runner.start();runner.stop();journal.flush()
            raw=json.loads(path.read_text(encoding='utf-8'));raw['runs'][0]['context']['api_key']='do-not-display';path.write_text(json.dumps(raw),encoding='utf-8')
            before=path.read_bytes();other=RunJournal(Runner(),path=path,context=lambda:{})
            self.assertEqual(other.list()['runs'],[]);self.assertIsNotNone(other.error)
            other.flush();self.assertEqual(path.read_bytes(),before)

    def test_script_failure_is_recorded_with_reason_not_as_success(self):
        from backend.core.program import parse
        with tempfile.TemporaryDirectory() as folder:
            runner,journal=self.fixture(Path(folder)/'runs.json')
            runner.load(parse('x = 1 / 0',lambda _:None));runner.start();identity=runner.run_id
            for _ in range(10):runner.advance(0)
            value=journal.get(identity)
            self.assertEqual(value['phase'],'error');self.assertTrue(value['reason']);self.assertEqual(value['confirmed_steps'],0)

class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_background_writer_shutdown_flushes_and_read_only_api_cannot_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runs.json';runner=Runner();journal=RunJournal(runner,path=path,context=lambda:{'mode':'sim'})
            app=FastAPI();app.include_router(create_run_journal_router(journal))
            await journal.start()
            runner.load([{'kind':'wait','line':1,'src':'WAIT 1','sec':1}]);runner.start();identity=runner.run_id;runner.advance(0)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                self.assertEqual((await client.get('/api/runs')).json()['runs'][0]['run_id'],identity)
                exported=(await client.get('/api/runs/'+identity+'/export')).json()
                self.assertTrue(exported['analysis_only']);self.assertNotIn('steps',exported['record'])
                self.assertEqual((await client.post('/api/runs/'+identity+'/run')).status_code,404)
                self.assertEqual((await client.post('/api/runs/'+identity)).status_code,405)
                self.assertEqual((await client.get('/api/runs/unknown')).status_code,404)
            await journal.close();self.assertIsNone(journal.thread)
            reopened=RunJournal(Runner(),path=path,context=lambda:{})
            self.assertEqual(reopened.get(identity)['phase'],'interrupted')

if __name__=='__main__':unittest.main()
