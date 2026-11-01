"""Full placed geometry observes the same 110 percent fence as the editor view."""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy,json,math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from backend.core.objects import ObjectStore
from backend.core.world import World
from backend.core.config import SettingsStore
from backend.domain.scene_bounds import expanded_fence,object_bounds,validate_placement
from backend.workspace_routes import Workspace

FENCE={'x':[-1000,1000],'y':[-1000,1000],'z':[0,1300]}

class Bounds(unittest.TestCase):
    def test_scale_about_actual_center_and_full_primitive_support(self):
        for actual,expected in zip(expanded_fence(FENCE),[(-1100,1100),(-1100,1100),(-65,1365)]):
            for value,bound in zip(actual,expected):self.assertAlmostEqual(value,bound,places=9)
        for value,bound in zip(expanded_fence({'x':[100,300],'y':[-40,60],'z':[0,100]})[0],(90,310)):
            self.assertAlmostEqual(value,bound,places=9)
        box=ObjectStore._sanitize({'xyz':[1050,0,100],'size':[100,100,100]})
        validate_placement(box,FENCE)
        for item in ({**box,'xyz':[1050.01,0,100]}, {**box,'rotation_deg':[0,0,45]}, {**box,'scale':1.1}):
            with self.assertRaisesRegex(ValueError,'物体外形'):validate_placement(item,FENCE)
        cylinder=ObjectStore._sanitize({'kind':'cylinder','xyz':[0,0,100],'size':[20,20,100],'rotation_deg':[0,90,0]})
        half=[(b-a)/2 for a,b in object_bounds(cylinder)]
        self.assertAlmostEqual(half[0],50);self.assertAlmostEqual(half[1],20);self.assertAlmostEqual(half[2],20)
        sphere=ObjectStore._sanitize({'kind':'sphere','xyz':[1080,0,100],'size':[20,20,20]})
        validate_placement(sphere,FENCE)
        with self.assertRaises(ValueError):validate_placement({**sphere,'xyz':[1080.1,0,100]},FENCE)
    def test_invalid_fence_never_creates_unbounded_permission(self):
        for fence in (None,{},dict(FENCE,x=[True,1]),dict(FENCE,y=[float('nan'),4]),dict(FENCE,z=[4,4])):
            with self.subTest(fence=fence),self.assertRaises(ValueError):expanded_fence(fence)

class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.world=World(data_dir=self.folder,settings_store=SettingsStore(self.folder))
        self.world.update_settings({'fence':copy.deepcopy(FENCE)})
        self.store=self.world.objects
    def test_create_update_rotate_scale_import_and_array_are_atomic(self):
        item=self.store.add({'xyz':[1050,0,100],'size':[100,100,100]})
        before=self.store.list();data=self.store.path.read_bytes();generation=self.store.persisted_generation
        operations=[lambda:self.store.add({'xyz':[1080,0,100],'size':[100,100,100]}),
                    lambda:self.store.update(item['id'],{'rotation_deg':[0,0,45]}),
                    lambda:self.store.update(item['id'],{'scale':1.1}),
                    lambda:self.store.update(item['id'],{'xyz':[1050.1,0,100]}),
                    lambda:self.store.replace([*before,{'id':'outside','xyz':[2000,0,100]}])]
        for operation in operations:
            with self.assertRaisesRegex(ValueError,'物体外形'):operation()
            self.assertEqual(self.store.list(),before);self.assertEqual(self.store.path.read_bytes(),data);self.assertEqual(self.store.persisted_generation,generation)
        workspace=Workspace(self.world)
        # 阵列排布越出围栏必须被整体拒绝：路由层用 422 表达"请求数据不合法"，
        # 场景与撤销栈都不得发生任何变化。
        with self.assertRaises(HTTPException) as exc:
            workspace.edit_scene({'id':item['id'],'counts':[2,1,1],'spacing':[150,0,0],'revision':workspace.scene_status()['revision']},'array')
        self.assertEqual(exc.exception.status_code,422)
        self.assertIn('物体外形',str(exc.exception.detail))
        self.assertEqual(self.store.list(),before);self.assertEqual(workspace.undo_stack,[])
    def test_saved_legacy_outside_is_preserved_and_can_be_removed_or_moved_inside(self):
        original=ObjectStore._sanitize({'id':'legacy','name':'旧对象','xyz':[1500,0,100]})
        self.store.path.write_text(json.dumps([original]),encoding='utf-8');data=self.store.path.read_bytes()
        store=ObjectStore(self.store.path,fence_getter=lambda:FENCE)
        self.assertEqual(store.list()[0]['xyz'],original['xyz']);self.assertEqual(store.path.read_bytes(),data)
        store.update('legacy',{'name':'待调整'})
        with self.assertRaises(ValueError):store.update('legacy',{'xyz':[1600,0,100]})
        store.update('legacy',{'xyz':[900,0,100]});store.remove('legacy');self.assertEqual(store.list(),[])
    def test_fence_shrink_rejected_before_any_stop_or_settings_write(self):
        self.store.add({'xyz':[1000,0,100],'size':[100,100,100]})
        before=copy.deepcopy(self.world.settings);revision=self.world._cfg_rev
        with patch.object(self.world,'_request_real_jog_stop') as stop,self.assertRaisesRegex(ValueError,'物体外形'):
            self.world.update_settings({'fence':{**FENCE,'x':[-500,500]}})
        stop.assert_not_called();self.assertEqual(self.world.settings,before);self.assertEqual(self.world._cfg_rev,revision)
    def test_persistence_failure_leaves_original_geometry_and_disk(self):
        item=self.store.add({'xyz':[900,0,100]});before=self.store.list();data=self.store.path.read_bytes()
        with patch('backend.core.objects.write_json',side_effect=OSError('disk full')),self.assertRaises(OSError):self.store.update(item['id'],{'xyz':[950,0,100]})
        self.assertEqual(self.store.list(),before);self.assertEqual(self.store.path.read_bytes(),data)

if __name__=='__main__':unittest.main()
