"""Pure programming helpers and retired studio read-only boundary, isolated only."""
from _offline_guard import require_offline_copy
require_offline_copy()
import sys, tempfile, unittest, json, hashlib
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.core import config
from backend.core.task_studio import generate, RecordingStore
from backend.task_routes import create_task_router, RETIRED_REASON
from backend.core.program import parse
from backend.core.points import PointStore

class StudioChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=patch.object(config,'DATA_DIR',Path(self.tmp.name));self.data.start()
    def tearDown(self):
        self.data.stop();self.tmp.cleanup()

    def test_templates_have_bounded_parseable_paths(self):
        for kind in ('raster','circle','pick-place','pallet'):
            with self.subTest(kind=kind):
                draft=generate({'kind':kind,'options':{'speed':12}})
                self.assertTrue(1<=draft['count']<=256)
                self.assertEqual(draft['speed'],12)
                self.assertTrue(parse(draft['program'],lambda key:None))
    def test_raster_alternates_and_circle_closes(self):
        pts=generate({'kind':'raster'})['poses']
        self.assertEqual(pts[0][0],pts[3][0])
        circle=generate({'kind':'circle'})['poses']
        for a,b in zip(circle[0],circle[-1]):self.assertAlmostEqual(a,b)
    def test_bad_and_excessive_inputs(self):
        for payload in ({'kind':'bad'},{'kind':'circle','options':{'radius':float('nan')}},
                        {'kind':'pallet','options':{'rows':10,'cols':10}},
                        {'kind':'raster','options':{'spacing':True}},
                        {'kind':'raster','options':{'width':float('inf')}},
                        {'kind':'circle','options':{'segments':False}}):
            with self.subTest(payload=payload),self.assertRaises(ValueError):generate(payload)
    def test_batch_points_all_or_nothing_and_unique_codes(self):
        store=PointStore();store.add('已有',[0.]*6)
        for rows in ([{'name':'已有','joints':[0.]*6}],
                     [{'name':'新','joints':[0.]*6},{'name':'新','joints':[1.]*6}],
                     [{'name':'新','joints':[float('nan')]*6}]):
            with self.assertRaises(ValueError):store.batch_add(rows)
        self.assertEqual(len(store.list()),1)
        result=store.batch_add([{'name':'新1','joints':[0.]*6},{'name':'新2','joints':[1.]*6}])
        self.assertEqual([row['code'] for row in result],['P2','P3'])
        result[0]['joints'][0]=99
        self.assertEqual(store.list()[1]['joints'][0],0)


class RetirementChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'not-created'
        class NoRobot:
            _data_dir=self.root
            def __getattr__(self,name):raise AssertionError('retired route accessed robot: '+name)
        app=FastAPI();app.include_router(create_task_router(NoRobot()));self.client=TestClient(app)
    def tearDown(self):self.client.close();self.tmp.cleanup()
    def test_missing_domain_is_empty_readonly_without_mkdir(self):
        reply=self.client.get('/api/studio');self.assertEqual(reply.status_code,200)
        self.assertEqual(reply.json()['recordings'],[]);self.assertTrue(reply.json()['readonly'])
        self.assertFalse(reply.json()['recording']['active']);self.assertFalse(self.root.exists())
        self.assertEqual(self.client.get('/api/studio/recordings/missing').status_code,404)
        self.assertFalse(self.root.exists())
    def test_all_previous_write_and_motion_routes_refuse_without_parsing_or_writing(self):
        paths=['','/generate','/inspect','/run','/record/start','/record/stop','/recordings/old','/recordings/old/inspect']
        for method in ('POST','PUT','DELETE','PATCH'):
            for path in paths:
                with self.subTest(method=method,path=path):
                    response=self.client.request(method,'/api/studio'+path,content=b'{malformed',headers={'content-type':'application/json'})
                    self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail'],RETIRED_REASON)
        self.assertFalse(self.root.exists())
    def test_history_get_export_is_detached_and_byte_preserving(self):
        self.root.mkdir();file=self.root/'studio_recordings.json'
        original=[{'id':'old','revision':7,'name':'历史轨迹','points':[{'q':[0]*6,'tcp':[1]*6,'t':0}],
                   'joints':[[0]*6],'poses':[[1]*6],'count':1,'duration':0,'custom':'保留'}]
        file.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8');before=file.read_bytes()
        response=self.client.get('/api/studio/recordings/old');self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['item'],original[0]);self.assertTrue(response.json()['readonly'])
        listing=self.client.get('/api/studio').json()['recordings'][0]
        self.assertNotIn('points',listing);self.assertNotIn('joints',listing);self.assertEqual(listing['revision'],7)
        store=RecordingStore(file);copy=store.get('old');copy['joints'][0][0]=999
        self.assertEqual(store.get('old')['joints'][0][0],0)
        for method in ('POST','PUT','DELETE'):
            self.assertEqual(self.client.request(method,'/api/studio/recordings/old',json={'revision':7}).status_code,409)
        self.assertEqual(file.read_bytes(),before)
    def test_corrupt_recording_fails_read_and_never_overwrites(self):
        self.root.mkdir();file=self.root/'studio_recordings.json';file.write_bytes(b'{broken')
        response=self.client.get('/api/studio');self.assertEqual(response.status_code,503)
        self.assertEqual(file.read_bytes(),b'{broken')
    def test_retired_class_has_no_recording_or_execution_methods(self):
        import backend.task_routes as routes
        self.assertFalse(hasattr(routes,'TaskStudio'))
        for name in ('start','stop','observe','edit','remove'):self.assertFalse(hasattr(RecordingStore,name))
    def test_original_sources_and_old_assertions_remain_hash_verifiable(self):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'tools/legacy_contracts_20261030/studio-retirement/manifest.json').read_text(encoding='utf-8'))
        for row in manifest['files']:
            self.assertEqual(hashlib.sha256((root/row['archive']).read_bytes()).hexdigest(),row['sha256'])

if __name__=='__main__':unittest.main()
