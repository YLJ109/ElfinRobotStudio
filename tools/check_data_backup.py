"""Portable data, tamper rejection and atomic new-directory recovery."""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from backend.application.data_backup import DataBackup,digest,validate_bundle
from backend.core.objects import ObjectStore
from backend.core.points import PointStore
from backend.core.scenes import SceneStore
from backend.core.tools import ToolStore
from backend.core.program_store import ProgramStore
from backend.core.storage import write_json

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='backup-',dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        points=PointStore(self.root/'points.json')
        points.items=[{'id':12,'code':'P7','name':'原始点','joints':[0]*6,'tcp':None,'source':'real','note':'','created':0}];points._save()
        objects=ObjectStore(self.root/'objects.json')
        objects.add({'id':'stable-box','name':'工件','kind':'box','size':[20,30,40],'xyz':[400,0,20],'gravity':False})
        scenes=SceneStore(self.root/'scenes.json');scenes.put('现场一',objects.list())
        programs=ProgramStore(self.root/'programs.json');programs.put('程序一','WAIT 1')
        tools=ToolStore(self.root/'tools.json')
        world=SimpleNamespace(points=points,objects=objects,tools=tools,
            settings={'tts':True,'mode':'real','auto_connect':True,'robot_ip':'192.168.0.10','ai_api_key':'secret'},
            calibrations=SimpleNamespace(items={'io':{'左吸气':{'group':'box','direction':'output','bit':2,'confirmed_by_user':True,'version':9}}}))
        self.resources=SimpleNamespace(world=world,scenes=scenes,programs=programs,data_dir=self.root)
        self.backup=DataBackup(self.resources)
    def test_export_strips_connection_credentials_and_nonportable_qualification(self):
        value=self.backup.export();data=value['data']
        self.assertNotIn('192.168.0.10',json.dumps(value));self.assertNotIn('secret',json.dumps(value))
        self.assertEqual(data['points'][0]['id'],12);self.assertEqual(data['points'][0]['code'],'P7')
        self.assertEqual(data['points'][0]['source'],'imported');self.assertEqual(data['points'][0]['imported_source'],'real')
        self.assertNotIn('confirmed_by_user',data['io']['左吸气'])
        self.assertTrue(all(t['verification']=='unvalidated' for t in data['tools']))
        self.assertEqual(validate_bundle(value),data,'normalization must be idempotent')
    def test_restore_new_directory_reopens_all_data_and_never_replaces_live_files(self):
        before={p.name:p.read_bytes() for p in self.root.glob('*.json')}
        preview=self.backup.preview(self.backup.export());result=self.backup.restore(preview['token'])
        path=Path(result['data_dir']);self.assertEqual(self.backup.recovery_path(result['copy_id']),path)
        self.assertFalse(result['active']);self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.glob('*.json')})
        self.assertEqual(PointStore(path/'points.json').list()[0]['code'],'P7')
        self.assertEqual(ObjectStore(path/'objects.json').list()[0]['id'],'stable-box')
        self.assertEqual(len(SceneStore(path/'scenes.json').list()),1)
        self.assertEqual(ProgramStore(path/'programs.json').get('程序一')['src'],'WAIT 1')
        self.assertEqual(ToolStore(path/'tools.json').mounted['state'],'unknown')
        self.assertEqual(json.loads((path/'calibrations.json').read_text())['io'],{})
        settings=json.loads((path/'settings.json').read_text());self.assertEqual(settings['mode'],'sim');self.assertFalse(settings['auto_connect'])
        self.assertEqual(json.loads((path/'pending-io.json').read_text(encoding='utf-8'))['items']['左吸气']['bit'],2)
        with self.assertRaises(ValueError):self.backup.restore(preview['token'])
    def test_tamper_version_bad_data_and_script_credentials_rejected(self):
        original=self.backup.export()
        for mutate in (lambda b:b.update(version=2),lambda b:b['data']['points'][0].update(joints=[0]),lambda b:b['data']['settings'].update(robot_ip='x'),lambda b:b['data']['io']['左吸气'].update(bit=-1),lambda b:b['data']['programs']['程序一'].update(src='api_key="x"')):
            value=copy.deepcopy(original);mutate(value);value['sha256']=digest(value['data'])
            with self.assertRaises((ValueError,HTTPException)):validate_bundle(value)
        value=copy.deepcopy(original);value['data']['objects'][0]['name']='篡改'
        with self.assertRaises(ValueError):validate_bundle(value)
    def test_failed_write_publishes_no_partial_copy_and_preserves_live_data(self):
        before={p.name:p.read_bytes() for p in self.root.glob('*.json')}
        token=self.backup.preview(self.backup.export())['token'];calls=0
        def fail(path,value):
            nonlocal calls
            calls+=1
            if calls==3:raise OSError('injected disk full')
            write_json(path,value)
        with patch('backend.application.data_backup.write_json',side_effect=fail),self.assertRaises(OSError):self.backup.restore(token)
        self.assertEqual(list((self.root/'recovery').iterdir()),[])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.glob('*.json')})
    def test_preview_binds_current_data_expires_and_recovery_rejects_mutation(self):
        token=self.backup.preview(self.backup.export())['token'];self.resources.programs.put('新程序','WAIT 2')
        with self.assertRaises(ValueError):self.backup.restore(token)
        token=self.backup.preview(self.backup.export())['token'];self.backup.previews[token]['at']-=301
        with self.assertRaises(ValueError):self.backup.restore(token)
        token=self.backup.preview(self.backup.export())['token'];result=self.backup.restore(token)
        write_json(Path(result['data_dir'])/'settings.json',{'auto_connect':True})
        with self.assertRaises(ValueError):self.backup.recovery_path(result['copy_id'])
        with self.assertRaises(ValueError):self.backup.recovery_path('../outside')

    def test_modified_manifest_cannot_omit_settings_or_hide_added_files(self):
        for mutation in ('empty','omit','extra','settings'):
            result=self.backup.restore(self.backup.preview(self.backup.export())['token'])
            folder=Path(result['data_dir']);manifest=json.loads((folder/'restore-manifest.json').read_text(encoding='utf-8'))
            if mutation=='empty':manifest['files']={}
            elif mutation=='omit':manifest['files'].pop('settings.json')
            elif mutation=='extra':write_json(folder/'unexpected.json',{'mode':'real'})
            else:
                write_json(folder/'settings.json',{'mode':'real','auto_connect':True})
                import hashlib
                manifest['files']['settings.json']=hashlib.sha256((folder/'settings.json').read_bytes()).hexdigest()
            write_json(folder/'restore-manifest.json',manifest)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.backup.recovery_path(result['copy_id'])

    def test_nested_credentials_and_invalid_point_object_records_are_rejected(self):
        original=self.backup.export()
        for mutate in (lambda b:b['data']['points'][0].update(source={'verified':True}),
                       lambda b:b['data']['points'][0].update(note='HUYAN_TOKEN=secret-value'),
                       lambda b:b['data']['objects'][0].update(id=None),
                       lambda b:b['data']['objects'][0].update(size=[-1,20,30]),
                       lambda b:b['data']['tools'][0].setdefault('provenance',{}).update(ai_api_key='secret-value')):
            value=copy.deepcopy(original);mutate(value);value['sha256']=digest(value['data'])
            with self.assertRaises((ValueError,HTTPException)):validate_bundle(value)

    def test_self_generated_glb_roundtrip_preserves_stable_reference_and_bytes(self):
        from backend.core.scene_assets import SceneAssetStore,inspect_glb
        from check_scene_assets import glb,META
        source=glb();assets=SceneAssetStore(self.root/'scene_assets')
        record=assets.commit(source,inspect_glb(source,META))
        self.resources.world.objects.add({'id':'asset-object','name':'安全资源','kind':'box','xyz':[500,0,50],
                                         'size':[100,100,100],'asset_id':record['asset_id'],'collide':True})
        self.resources.scenes.put('资源现场',self.resources.world.objects.list())
        bundle=self.backup.export();preview=self.backup.preview(bundle)
        result=self.backup.restore(preview['token']);folder=self.backup.recovery_path(result['copy_id'])
        self.assertEqual(self.backup.recovery_digest(result['copy_id']),result['manifest_digest'])
        reopened=ObjectStore(folder/'objects.json')
        self.assertEqual(next(o for o in reopened.list() if o['id']=='asset-object')['asset_id'],record['asset_id'])
        self.assertEqual(SceneAssetStore(folder/'scene_assets').read_glb(record['asset_id']),source)
        self.assertEqual(next(s for s in SceneStore(folder/'scenes.json').list() if s['name']=='资源现场')['object_count'],2)
        bad=copy.deepcopy(bundle);bad['data']['assets']={};bad['sha256']=digest(bad['data'])
        with self.assertRaises(ValueError):validate_bundle(bad)
        bad=copy.deepcopy(bundle);bad['data']['assets'][record['asset_id']]['metadata']['license']='forged';bad['sha256']=digest(bad['data'])
        with self.assertRaises(ValueError):validate_bundle(bad)

    def test_preview_reports_actual_field_differences_and_caps_rows_without_asset_bytes(self):
        value=self.backup.export();value['data']['points'][0]['name']='修改点名'
        value['data']['programs']['程序一']['src']='WAIT 2\nWAIT 3'
        value['data']['objects'] += [{**copy.deepcopy(value['data']['objects'][0]),'id':'added-'+str(i),'name':'新增'+str(i)} for i in range(110)]
        value['sha256']=digest(value['data']);preview=self.backup.preview(value)
        changes={row['section']:row for row in preview['changes']}
        self.assertLessEqual(sum(len(row['rows']) for row in changes.values()),100)
        self.assertTrue(any(row['truncated'] for row in changes.values()))
        self.assertEqual(changes['objects']['changed_count'],110)
        self.assertNotIn('glb',json.dumps(preview))
        # A separate small diff verifies names/line summaries are actionable,
        # independent of the global row budget used by the large object set.
        value=self.backup.export();value['data']['points'][0]['name']='修改点名';value['data']['programs']['程序一']['src']='WAIT 2\nWAIT 3';value['sha256']=digest(value['data'])
        preview=self.backup.preview(value);changes={row['section']:row for row in preview['changes']}
        self.assertEqual(changes['points']['rows'][0]['fields'],['name'])
        self.assertIn('修改点名',changes['points']['rows'][0]['after'])
        self.assertIn('first_changed_line',changes['programs']['rows'][0]['after'])
        self.assertIn('WAIT 2',changes['programs']['rows'][0]['after'])

    def test_atomic_publish_failure_never_exposes_partial_copy_or_trusted_digest(self):
        token=self.backup.preview(self.backup.export())['token']
        with patch('backend.application.data_backup.os.rename',side_effect=OSError('publish interrupted')):
            with self.assertRaises(OSError):self.backup.restore(token)
        self.assertEqual(list((self.root/'recovery').iterdir()),[])
        self.assertFalse(self.backup.published_manifest_digests)
        self.assertEqual(self.resources.world.points.list()[0]['source'],'real')

    def test_recovery_parent_jump_is_rejected_before_creating_or_writing_any_copy(self):
        token=self.backup.preview(self.backup.export())['token']
        parent=self.root.resolve()/'recovery'
        original=Path.is_junction
        def junction(path):return path==parent or original(path)
        with patch.object(Path,'is_junction',junction):
            with self.assertRaises(ValueError):self.backup.restore(token)
        self.assertFalse(parent.exists())

    def test_restart_cannot_reactivate_unbound_old_copy_and_prepared_publication_is_same_parent(self):
        import os
        token=self.backup.preview(self.backup.export())['token'];rename=os.rename
        def checked_rename(stage,destination):
            self.assertEqual(Path(stage).parent,Path(destination).parent)
            self.assertTrue(Path(stage).name.startswith('.preparing-'))
            self.assertTrue((Path(stage)/'restore-manifest.json').is_file())
            return rename(stage,destination)
        with patch('backend.application.data_backup.os.rename',side_effect=checked_rename):result=self.backup.restore(token)
        self.assertTrue(self.backup.recovery_path(result['copy_id']).is_dir())
        other=DataBackup(self.resources)
        with self.assertRaises(ValueError):other.recovery_path(result['copy_id'])

if __name__=='__main__':unittest.main(verbosity=2)
