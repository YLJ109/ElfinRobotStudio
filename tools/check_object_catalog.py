"""Appearance persistence and conservative proxy checks; disposable runner only."""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.schemas import OBJECT_PRESETS, OBJECT_SURFACES
from backend.core.objects import ObjectStore, half_height, radius
from backend.core.scenes import SceneStore


class ObjectCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.objects = ObjectStore(self.root / 'objects.json')
        self.scenes = SceneStore(self.root / 'scenes.json')

    def test_legacy_defaults_and_all_presets_roundtrip(self):
        old = self.objects.add({'kind':'box','xyz':[1,2,3],'size':[100,200,300]})
        self.assertEqual((old['preset'],old['surface']),('box','painted'))
        self.assertEqual(len(OBJECT_PRESETS),19)
        rows=[]
        for index,(preset,kind) in enumerate(OBJECT_PRESETS.items()):
            item=self.objects.add({'kind':kind,'preset':preset,'surface':OBJECT_SURFACES[index%len(OBJECT_SURFACES)],
                'size':[100,200,300],'xyz':[500+index*500,2,3],'color':'#12abef'})
            self.assertEqual(item['preset'],preset)
            self.assertEqual(half_height(item),100 if kind=='sphere' else 150)
            self.assertGreaterEqual(radius(item),100)
            rows.append(item)
        self.scenes.put('工业场景',rows)
        exported=self.scenes.get('工业场景')['objects']
        restored=SceneStore(self.root/'scenes.json').get('工业场景')['objects']
        self.assertEqual(exported,restored)
        self.assertEqual(self.objects.replace(exported),restored)
        self.assertEqual(ObjectStore(self.root/'objects.json').list(),restored)
        # Arrays copy the same object contract and change only identity/position.
        clone={**rows[3],'id':'array-copy','xyz':[1000,2,3]}
        self.assertEqual(ObjectStore._sanitize(clone)['surface'],rows[3]['surface'])
        self.assertEqual(ObjectStore._sanitize(clone)['preset'],rows[3]['preset'])

    def test_invalid_appearance_is_atomic_for_add_edit_and_scene_import(self):
        item=self.objects.add({'kind':'box','preset':'pallet','surface':'wood'})
        before=self.objects.list()
        for patch in ({'surface':'remote-url'},{'surface':True},{'preset':'unknown'},{'preset':'drum'},
                      {'color':'url(https://example.invalid)'},{'color':42},{'kind':'mesh'},
                      {'surface':'steel-extra-long-token'},{'preset':[]},{'size':[float('nan'),1,1]}):
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):self.objects.update(item['id'],patch)
                self.assertEqual(self.objects.list(),before)
                with self.assertRaises(ValueError):self.objects.add({**item,**patch})
                self.assertEqual(self.objects.list(),before)
                with self.assertRaises(ValueError):self.scenes.put('bad',[item,{**item,**patch}])
                self.assertEqual(self.scenes.list(),[])

    def test_appearance_edits_preserve_physics_and_snapshot_copies(self):
        item=self.objects.add({'kind':'box','preset':'workbench','xyz':[1,2,300],'size':[400,500,600],
            'gravity':False,'floating':False,'floor':True,'collide':True})
        old=copy.deepcopy(item)
        result=self.objects.update(item['id'],{'surface':'steel','color':'#abc'})
        for field in ('kind','xyz','size','gravity','floating','floor','collide'):
            self.assertEqual(result[field],old[field])
        self.assertEqual(result['color'],'#aabbcc')
        self.assertEqual(item,old)
        self.assertEqual(json.loads((self.root/'objects.json').read_text())[0]['surface'],'steel')


if __name__ == '__main__':unittest.main(verbosity=2)
