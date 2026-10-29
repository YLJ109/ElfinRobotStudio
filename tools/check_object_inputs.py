"""场景输入与圆柱碰撞口径回归；只在硬件禁用副本运行。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy
import math
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.objects import ObjectStore, radius
from backend.core.world import World
from backend.core.scenes import SceneStore
from check_object_catalog import ObjectCatalogTests  # Also run appearance/persistence contracts in this existing isolated object gate.


class ObjectInputTests(unittest.TestCase):
    def test_ground_constraint_on_create_edit_resize_and_import(self):
        for kind in ('box', 'cylinder', 'sphere'):
            with self.subTest(kind=kind):
                item = self.store.add({'kind': kind, 'size': [40, 60, 100], 'xyz': [0, 0, -500], 'floor': False})
                height = 40 if kind == 'sphere' else 50
                self.assertEqual(item['xyz'][2], height)
                self.assertTrue(item['floor'])
                moved = self.store.update(item['id'], {'xyz': [10, 20, -100]})
                self.assertEqual(moved['xyz'][2], height)
                enlarged = self.store.update(item['id'], {'size': [80, 90, 200]})
                self.assertEqual(enlarged['xyz'][2], 80 if kind == 'sphere' else 100)
                restored = self.store.replace([{**enlarged, 'xyz': [0, 0, -1]}])[0]
                self.assertEqual(restored['xyz'][2], enlarged['xyz'][2])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ObjectStore(Path(self.folder.name) / 'objects.json')
        self.original = self.store.add({'name': 'original'})

    def test_invalid_geometry_and_boolean_do_not_mutate_scene(self):
        before = self.store.list()
        for patch in ({'xyz': [math.nan, 0, 0]}, {'xyz': [True, 0, 0]}, {'xyz': [1e308, 0, 0]},
                      {'size': [math.inf, 1, 1]}, {'collide': []}, {'floor': 'false'}, {'xyz': [1, 2]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.update(self.original['id'], patch)
            self.assertEqual(self.store.list(), before)

    def test_malformed_patch_and_import_are_rejected_as_a_whole(self):
        before = copy.deepcopy(self.store.items)
        for patch in ([], 'xyz', 3):
            with self.assertRaises(ValueError): self.store.update(self.original['id'], patch)
        for spec in ({'xyz': 'invalid'}, {'gravity': 1}, {'size': [math.nan]}):
            with self.assertRaises(ValueError): self.store.add(spec)
        with self.assertRaises(ValueError): self.store.replace([self.original, 1])
        self.assertEqual(self.store.items, before)

    def test_cylinder_uses_displayed_radius_not_half_radius(self):
        cylinder = {'kind': 'cylinder', 'size': [100, 1, 20], 'xyz': [0, 0, 700], 'name': 'cylinder', 'collide': True}
        cap = {'link': 'elfin_end_link', 'a': [95, 0, 700], 'b': [95, 0, 700], 'r_mm': 0}
        self.assertGreater(radius(cylinder), 100)
        hits = World._object_hits([cap], [cylinder])
        self.assertEqual(len(hits), 1)
        self.assertLess(hits[0]['gap_mm'], 0)

    def test_scene_save_rejects_invalid_or_oversize_import_without_truncation(self):
        scenes = SceneStore(Path(self.folder.name) / 'scenes.json')
        scenes.put('old', [self.original])
        before = copy.deepcopy(scenes.items)
        for values in ([self.original, 1], [{'xyz': [math.inf, 0, 0]}], [self.original] * 1001):
            with self.assertRaises(ValueError): scenes.put('bad', values)
            self.assertEqual(scenes.items, before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
