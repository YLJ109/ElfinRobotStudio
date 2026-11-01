"""Self-generated GLB and isolated storage only; no downloaded models."""
from _offline_guard import require_offline_copy
require_offline_copy()
import base64
from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
import unittest
import asyncio
from unittest.mock import patch
from backend.core.scene_assets import SceneAssetStore, inspect_glb, MAX_BYTES
from backend.core.objects import ObjectStore
from backend.core.scenes import SceneStore


def glb(change=None):
    positions=[0.,0.,0., .1,0.,0., 0.,.1,.1]
    doc={'asset':{'version':'2.0'},'buffers':[{'byteLength':36}],
         'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':36}],
         'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3','min':[0,0,0],'max':[.1,.1,.1]}],
         'meshes':[{'primitives':[{'attributes':{'POSITION':0},'mode':4}]}],
         'nodes':[{'mesh':0}],'scenes':[{'nodes':[0]}],'scene':0}
    if change: change(doc)
    encoded=json.dumps(doc,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%4)
    binary=struct.pack('<9f',*positions)
    size=12+8+len(encoded)+8+len(binary)
    return struct.pack('<III',0x46546c67,2,size)+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(binary),0x004e4942)+binary


META={'name':'自制测试三角形','units':'m','source':'本测试直接生成的原始几何','license':'CC0-1.0','role':'obstacle'}


class Assets(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=SceneAssetStore(self.root/'assets')

    def test_inspection_is_readonly_and_dimensions_are_actual(self):
        result=inspect_glb(glb(),META)
        self.assertEqual(result['triangles'],1)
        for size in result['size_mm']: self.assertAlmostEqual(size,100,places=3)
        self.assertFalse(self.store.path.exists())

    def test_commit_reopen_and_safe_id_are_stable(self):
        info=inspect_glb(glb(),META)
        record=self.store.commit(glb(),info)
        reopened=SceneAssetStore(self.store.path)
        self.assertEqual(reopened.get(record['asset_id'])['source'],META['source'])
        self.assertEqual(reopened.read_glb(record['asset_id']),glb())
        for unsafe in ('../outside','/etc/passwd','a'*63,'z'*64):
            with self.assertRaises(ValueError): reopened.get(unsafe)

    def test_external_dependencies_complexity_and_bad_bytes_never_write(self):
        changes=[lambda d:d['buffers'][0].update(uri='https://example.invalid/model.bin'),
                 lambda d:d.update(images=[{'uri':'../image.png'}]),
                 lambda d:d['accessors'][0].update(count=100000000),
                 lambda d:d['nodes'][0].update(children=[0]),
                 lambda d:d.update(extensionsUsed=['KHR_draco_mesh_compression']),
                 lambda d:d['meshes'][0]['primitives'][0].update(mode=1),
                 lambda d:d['meshes'][0]['primitives'][0].update(material=999),
                 lambda d:d.update(materials=[{'pbrMetallicRoughness':{'baseColorTexture':{'index':0}}}])]
        for change in changes:
            with self.assertRaises(ValueError): inspect_glb(glb(change),META)
        for data in (b'bad',glb()[:-1],b'0'*(MAX_BYTES+1)):
            with self.assertRaises(ValueError): inspect_glb(data,META)
        self.assertFalse(self.store.path.exists())

    def test_cache_digest_and_symlink_cannot_substitute_another_file(self):
        asset=self.store.commit(glb(),inspect_glb(glb(),META))
        path=self.store._file(asset['asset_id'])
        raw=json.loads(path.read_text(encoding='utf-8'));raw['metadata']['license']='forged'
        path.write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaises(ValueError):self.store.get(asset['asset_id'])

    def test_missing_license_units_invalid_role_and_oversize_are_rejected(self):
        for update in ({'license':''},{'license':'未知'},{'license':'unKnown'},{'source':'来源不明'},{'source':''},{'units':''},{'role':'graspable'},
                       {'size_mm':[1000,1000,1000]}):
            with self.assertRaises(ValueError): inspect_glb(glb(),{**META,**update})

    def test_failed_commit_is_atomic(self):
        from backend.core import scene_assets
        with patch.object(scene_assets,'write_json',side_effect=OSError('fake full disk')):
            with self.assertRaises(OSError): self.store.commit(glb(),inspect_glb(glb(),META))
        self.assertEqual(list(self.store.path.glob('*.json')),[])

    def test_object_and_scene_preserve_asset_reference_and_conservative_proxy(self):
        from backend.core import scene_assets
        store=SceneAssetStore(self.root/'scene_assets')
        asset=store.commit(glb(),inspect_glb(glb(),META))
        objects=ObjectStore(self.root/'objects.json')
        obj=objects.add({'name':META['name'],'kind':'box','size':asset['size_mm'],'asset_id':asset['asset_id']})
        scenes=SceneStore(self.root/'scenes.json');scenes.put('资源现场',[obj])
        reopened=SceneStore(scenes.path).get('资源现场')['objects'][0]
        self.assertEqual(reopened['asset_id'],asset['asset_id'])
        self.assertTrue(reopened['collide'])
        with self.assertRaises(ValueError): objects.update(obj['id'],{'size':[501,100,100]})
        with self.assertRaises(ValueError): objects.add({**obj,'task':{'group':'a'*32,'mode':'sorting','role':'piece','symbol':'','slot':0,'home':0}})


class AssetAPI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from backend.core import config
        from backend.core.world import World
        from backend.workspace_routes import Workspace
        from backend.scene_asset_routes import create_scene_asset_router
        from fastapi import FastAPI
        state=patch.object(config,'_settings',deepcopy(config.DEFAULTS));state.start();self.addCleanup(state.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.world=World();self.world.objects=ObjectStore(Path(self.temp.name)/'objects.json')
        self.workspace=Workspace(self.world)
        self.app=FastAPI();self.app.include_router(create_scene_asset_router(self.world,workspace=self.workspace))
        self.body={'glb':base64.b64encode(glb()).decode(),'metadata':META,'object_id':'a'*12}

    async def test_import_actual_asset_then_undo_redo_reopen_and_idempotent_retry(self):
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1') as client:
            inspected=(await client.post('/api/scene-assets/inspect',json=self.body)).json()
            self.assertTrue(inspected['ok']);self.assertEqual(self.world.objects.list(),[])
            body={**self.body,'revision':inspected['revision']}
            response=await client.post('/api/scene-assets/import',json=body)
            self.assertTrue(response.json()['ok'],response.text)
            asset_id=response.json()['object']['asset_id']
            loaded=await client.get(f'/api/scene-assets/{asset_id}/model.glb')
            self.assertEqual(loaded.content,glb())
            self.assertTrue((await client.post('/api/scene-assets/import',json=body)).json()['reused'])
            self.assertEqual(len(self.world.objects.list()),1)
            self.workspace.history('undo',{'revision':self.workspace.scene_revision()})
            self.assertEqual(self.world.objects.list(),[])
            self.workspace.history('redo',{'revision':self.workspace.scene_revision()})
            self.assertEqual(ObjectStore(self.world.objects.path).list()[0]['asset_id'],asset_id)

    async def test_stale_scene_and_failed_object_save_leave_original_scene(self):
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1') as client:
            response=await client.post('/api/scene-assets/import',json={**self.body,'revision':'stale'})
            self.assertEqual(response.status_code,409);self.assertEqual(self.world.objects.list(),[])
            inspected=(await client.post('/api/scene-assets/inspect',json=self.body)).json()
            with patch.object(self.world.objects,'replace',side_effect=OSError('fake disk full')):
                response=await client.post('/api/scene-assets/import',json={**self.body,'revision':inspected['revision']})
            self.assertEqual(response.status_code,503);self.assertEqual(self.world.objects.list(),[])

    async def test_cancelled_validation_cannot_later_publish_an_object(self):
        import httpx
        import threading
        from backend import scene_asset_routes
        entered=threading.Event();release=threading.Event()
        def slow(data,metadata):
            entered.set();release.wait(3);return inspect_glb(data,metadata)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1') as client:
            with patch.object(scene_asset_routes,'inspect_glb',side_effect=slow):
                task=asyncio.create_task(client.post('/api/scene-assets/import',json={**self.body,'revision':self.workspace.scene_revision()}))
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                task.cancel();release.set()
                with self.assertRaises(asyncio.CancelledError):await task
            await asyncio.sleep(.02)
            self.assertEqual(self.world.objects.list(),[])

    async def test_forged_token_and_concurrent_scene_revision_never_commit(self):
        import httpx
        import threading
        from backend import scene_asset_routes
        entered=threading.Event();release=threading.Event()
        def slow(data,metadata):
            entered.set();release.wait(3);return inspect_glb(data,metadata)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1') as client:
            forged=await client.post('/api/scene-assets/import',json={**self.body,'token':'forged','revision':self.workspace.scene_revision()})
            self.assertFalse(forged.json()['ok']);self.assertEqual(self.world.objects.list(),[])
            with patch.object(scene_asset_routes,'inspect_glb',side_effect=slow):
                task=asyncio.create_task(client.post('/api/scene-assets/import',json={**self.body,'revision':self.workspace.scene_revision()}))
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                other=self.world.objects.add({'name':'并发保留物体','kind':'box','size':[10]*3})
                release.set();response=await task
            self.assertEqual(response.status_code,409)
            self.assertEqual(self.world.objects.list(),[other])


if __name__=='__main__':unittest.main()
