from _offline_guard import require_offline_copy
require_offline_copy()
import json,tempfile,unittest
from pathlib import Path
from fastapi import HTTPException
from unittest.mock import patch
from backend.core.world import World
from backend.workspace_routes import Workspace

class Persistence(unittest.TestCase):
    def test_legacy_add_gravity_then_workspace_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            world=World(data_dir=Path(folder));ws=Workspace(world)
            item=world.objects.add(dict(kind='box',xyz=[900,900,300],size=[100,100,160],gravity=True))
            for _ in range(100):world.objects.step(.1,[])
            self.assertEqual(world.objects.list()[0]['xyz'][2],80)
            result=ws.edit_scene(dict(id=item['id'],patch={'name':'落地编辑'},revision=ws.scene_status()['revision']),'object')
            self.assertTrue(result['ok']);self.assertEqual(world.objects.list()[0]['name'],'落地编辑')
    def test_internal_legacy_writes_publish_once_and_invalidate_workspace_history(self):
        with tempfile.TemporaryDirectory() as folder:
            world=World(data_dir=Path(folder));ws=Workspace(world)
            for operation in ('add','update','remove','clear','replace','save'):
                if not world.objects.list():world.objects.add(dict(kind='box',xyz=[900,900,80],size=[100,100,160]))
                item=world.objects.list()[0]
                ws.edit_scene(dict(id=item['id'],patch={'name':'分支'+operation},revision=ws.scene_status()['revision']),'object')
                self.assertTrue(ws.scene_status()['can_undo'])
                before=world.objects.persisted_generation
                if operation=='add':world.objects.add(dict(kind='sphere',xyz=[900,800,80],size=[20,20,20]))
                elif operation=='update':world.objects.update(item['id'],{'name':'旧API改名'})
                elif operation=='remove':world.objects.remove(item['id'])
                elif operation=='clear':world.objects.clear()
                elif operation=='replace':world.objects.replace(world.objects.list())
                else:world.objects._save()
                self.assertEqual(world.objects.persisted_generation,before+1)
                self.assertEqual(world.objects.persisted_disk,world.objects.disk_signature())
                self.assertFalse(ws.scene_status()['can_undo']);self.assertFalse(ws.undo_stack);self.assertFalse(ws.redo_stack)

    def test_disk_write_failure_does_not_publish_new_identity_or_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            world=World(data_dir=Path(folder));objects=world.objects
            before=(objects.persisted_generation,objects.persisted_disk,objects.list())
            with patch('backend.core.storage.os.replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):objects.add(dict(kind='box',xyz=[900,800,80],size=[100,100,160]))
            self.assertEqual((objects.persisted_generation,objects.persisted_disk,objects.list()),before)

    def test_external_file_only_change_still_rejected_after_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            world=World(data_dir=Path(folder));ws=Workspace(world)
            item=world.objects.add(dict(kind='box',xyz=[900,900,80],size=[100,100,160]))
            raw=json.loads(world.objects.path.read_text(encoding='utf-8'));raw[0]['name']='外部修改'
            world.objects.path.write_text(json.dumps(raw),encoding='utf-8')
            original=world.objects.path.read_bytes()
            with self.assertRaises(HTTPException) as error:ws.edit_scene(dict(id=item['id'],patch={'name':'覆盖'},revision=ws.scene_status()['revision']),'object')
            self.assertEqual(error.exception.status_code,409);self.assertEqual(world.objects.path.read_bytes(),original)

if __name__=='__main__':unittest.main()
