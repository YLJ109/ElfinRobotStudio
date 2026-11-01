"""Physical scene contacts: fake capsules, isolated files, no hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy
import math
import random
from pathlib import Path
import tempfile
import unittest
from backend.core.objects import ObjectStore
from backend.domain.object_contacts import distance,penetrates

class Contacts(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=ObjectStore(Path(self.directory.name)/'objects.json')
    def add(self,xyz=(0,0,50),size=(100,100,100),**extra):
        return self.store.add(dict(xyz=list(xyz),size=list(size),**extra))
    def settle(self,steps=240):
        for _ in range(steps):self.store.step(1/120,[])
        return self.store.list()
    def test_contact_allows_stacking_but_add_move_resize_import_are_atomic(self):
        base=self.add();top=self.add((0,0,150))
        before=self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'重叠'):self.add((0,0,100))
        self.assertEqual(self.store.path.read_bytes(),before)
        for patch in ({'xyz':[0,0,120]},{'size':[100,100,130]},{'scale':1.5}):
            with self.assertRaises(ValueError):self.store.update(top['id'],patch)
            self.assertEqual(self.store.path.read_bytes(),before)
        with self.assertRaises(ValueError):self.store.replace([base,{**top,'xyz':[0,0,60]}])
        self.assertEqual(self.store.path.read_bytes(),before)
    def test_box_stack_falls_to_real_top_and_becomes_idle(self):
        self.add();falling=self.add((0,0,350),gravity=True)
        self.settle()
        top=next(o for o in self.store.list() if o['id']==falling['id'])
        self.assertAlmostEqual(top['xyz'][2],150,delta=.1)
        self.assertFalse(self.store.any_active())
        self.assertTrue(next(o for o in self.store.with_vel() if o['id']==falling['id'])['supported'])
        self.store.remove(self.store.items[0]['id']);self.settle()
        self.assertAlmostEqual(self.store.items[0]['xyz'][2],50,delta=.1)
    def test_fast_fall_cannot_tunnel_through_a_two_mm_platform(self):
        self.add((0,0,201),(200,200,2))
        falling=self.add((0,0,800),(10,10,10),gravity=True)
        self.store._vel[falling['id']]=-10000
        self.store.step(.1,[])
        self.assertAlmostEqual(self.store.items[1]['xyz'][2],207,delta=.1)
    def test_sphere_rest_on_rotated_box_uses_actual_surface_not_global_aabb_top(self):
        self.add((0,0,150),(200,200,20),rotation_deg=[0,30,0])
        falling=self.add((50,0,300),(10,10,10),kind='sphere',gravity=True)
        self.settle()
        top=next(o for o in self.store.list() if o['id']==falling['id'])
        expected=150+20/math.cos(math.pi/6)-50*math.tan(math.pi/6)
        self.assertAlmostEqual(top['xyz'][2],expected,delta=.15)
    def test_legacy_overlap_can_be_repaired_incrementally_but_cannot_be_imported_as_valid(self):
        from backend.core.storage import write_json
        rows=[ObjectStore._sanitize(dict(id=str(i),kind='box',xyz=[x,0,50],size=[100,100,100])) for i,x in enumerate((0,20,500,520))]
        write_json(self.store.path,rows)
        restored=ObjectStore(self.store.path)
        self.assertEqual(len(restored.list()),4,'loading must retain user geometry rather than silently discard invalid legacy rows')
        changed=restored.list();changed[1]['xyz'][0]=150
        restored.replace(changed,validate_all=False)
        self.assertEqual(restored.list()[1]['xyz'][0],150)
        with self.assertRaises(ValueError):restored.replace(changed)
        changed[3]['xyz'][0]=650;restored.replace(changed)
        self.assertEqual(len(restored.list()),4)

    def test_scaled_cylinder_and_rotated_boxes_have_convex_contacts(self):
        cylinder=self.add((0,0,100),(20,20,50),kind='cylinder',scale=2)
        other=self.add((100,0,100),(20,20,20),rotation_deg=[0,0,45])
        self.assertFalse(penetrates(cylinder,other))
        with self.assertRaises(ValueError):self.store.update(other['id'],{'xyz':[0,0,100]})
        self.assertGreater(distance(cylinder,other),20)
    def test_robot_capsules_reject_static_editor_overlap_and_support_falling_objects(self):
        cap={'a':[-100,0,250],'b':[100,0,250],'r_mm':10,'link':'robot-link'}
        self.store._capsule_getter=lambda:[cap]
        with self.assertRaisesRegex(ValueError,'机器人'):self.add((0,0,250),(10,10,10))
        falling=self.add((0,0,400),(5,5,5),kind='sphere',gravity=True)
        for _ in range(180):self.assertEqual(self.store.step(1/120,[cap]),[])
        self.assertAlmostEqual(self.store.items[0]['xyz'][2],265,delta=.1)
    def test_rotated_thin_boxes_with_overlapping_aabbs_do_not_false_collide(self):
        first=self.add((0,0,100),(200,10,10),rotation_deg=[0,0,45])
        second=self.add((0,30,100),(200,10,10),rotation_deg=[0,0,45])
        self.assertFalse(penetrates(first,second))
    def test_cylinder_sphere_distance_matches_independent_analytic_surface_distance(self):
        rng=random.Random(923)
        cylinder=dict(kind='cylinder',xyz=[0,0,100],size=[30,30,80],scale=1,rotation_deg=[0,0,0])
        for _ in range(80):
            xyz=[rng.uniform(-100,100),rng.uniform(-100,100),rng.uniform(10,190)]
            sphere=dict(kind='sphere',xyz=xyz,size=[5,5,5],scale=1,rotation_deg=[0,0,0])
            expected=max(0.,math.hypot(max(0.,math.hypot(*xyz[:2])-30),max(0.,abs(xyz[2]-100)-40))-5)
            self.assertAlmostEqual(distance(cylinder,sphere),expected,delta=.002)

    def test_confirmed_cylinder_robot_penetration_does_not_invent_a_depth_measurement(self):
        self.add((0,0,200),(20,20,30),kind='cylinder')
        cap={'a':[0,0,200],'b':[0,0,200],'r_mm':2,'link':'arm'}
        hit=self.store.step(.01,[cap])[0]
        self.assertTrue(hit['penetrating']);self.assertIsNone(hit['gap_mm'])

    def test_contact_exemption_is_owned_piece_only_not_other_scene_objects(self):
        cap={'a':[-100,0,250],'b':[100,0,250],'r_mm':10,'link':'tool','contact_allowed':True}
        self.store._capsule_getter=lambda:[cap]
        self.store._contact_getter=lambda:['owned']
        self.add((0,0,250),(10,10,10),id='owned')
        with self.assertRaises(ValueError):self.add((40,0,250),(10,10,10))
        arm={**cap,'link':'upper-arm','contact_allowed':False}
        self.store._capsule_getter=lambda:[cap,arm]
        with self.assertRaisesRegex(ValueError,'upper-arm'):self.store.update('owned',{'xyz':[1,0,250]})
        self.assertEqual(self.store.step(.01,[cap],('owned',)),[])
        self.assertEqual(self.store.step(.01,[arm],('owned',))[0]['link'],'upper-arm')

if __name__=='__main__':unittest.main(verbosity=2)
