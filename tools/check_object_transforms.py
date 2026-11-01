"""Isolated object transforms/history checks; never creates World or devices."""
import sys, tempfile, math, unittest, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tools'))
from backend.core.objects import ObjectStore,half_height,capsule_object_gap
from backend.workspace_routes import Workspace
from check_workspace_routes import stub
from fastapi import HTTPException
class TransformTests(unittest.TestCase):
 def setUp(self):
  directory=ROOT/'output'/'renewal'/'object-transform-data';directory.mkdir(parents=True,exist_ok=True)
  self.temp=tempfile.TemporaryDirectory(prefix='elfin-object-transform-',dir=directory);self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.world=stub(self.root);self.service=Workspace(self.world,self.root/'profiles.json')
 def add(self,**patch):return self.world.objects.add(dict(kind='box',size=[200,40,20],xyz=[0,0,300],**patch))
 def edit(self,action,**payload):return self.service.edit_scene(dict(revision=self.service.scene_status()['revision'],**payload),action)
 def test_migration_and_reopen_keep_stable_id_transform_and_lock(self):
  obj=self.add();self.assertEqual(obj['rotation_deg'],[0,0,0]);self.assertEqual(obj['scale'],1);self.assertFalse(obj['locked'])
  changed=self.world.objects.update(obj['id'],{'rotation_deg':[0,0,90],'scale':2,'locked':True})
  self.assertEqual(ObjectStore(self.world.objects.path).list(),[changed]);self.assertEqual(changed['id'],obj['id'])
 def test_rotation_and_scale_move_the_collision_proxy_with_the_visual(self):
  obj=self.add(rotation_deg=[0,0,90],scale=2)
  cap={'a':[0,150,300],'b':[0,150,300],'r_mm':1}
  self.assertLess(capsule_object_gap(cap,obj),0)
  cap['a']=cap['b']=[150,0,300];self.assertGreater(capsule_object_gap(cap,obj),0)
  self.assertAlmostEqual(half_height(obj),20)
  self.assertAlmostEqual(half_height(dict(obj,rotation_deg=[0,90,0])),200)
 def test_invalid_transform_and_lock_bypass_leave_disk_unchanged(self):
  obj=self.add();before=self.world.objects.path.read_bytes()
  for patch in [{'scale':0},{'scale':True},{'rotation_deg':[0,float('nan'),0]},{'rotation_deg':[0,0]},{'locked':'true'}]:
   with self.assertRaises(ValueError):self.world.objects.update(obj['id'],patch)
   self.assertEqual(self.world.objects.path.read_bytes(),before)
  self.world.objects.update(obj['id'],{'locked':True})
  for patch in [{'xyz':[100,0,300]},{'locked':False,'scale':2}]:
   with self.assertRaises(ValueError):self.world.objects.update(obj['id'],patch)
  with self.assertRaises(ValueError):self.world.objects.remove(obj['id'])
  self.world.objects.update(obj['id'],{'locked':False});self.assertTrue(self.world.objects.remove(obj['id']))
 def test_transform_below_ground_is_rejected_atomically(self):
  obj=self.add();self.world.objects.update(obj['id'],{'xyz':[0,0,10]});before=self.world.objects.list()
  with self.assertRaises(ValueError):self.world.objects.update(obj['id'],{'rotation_deg':[0,90,0]})
  self.assertEqual(self.world.objects.list(),before)
 def test_edit_duplicate_remove_share_the_existing_undo_redo_history(self):
  obj=self.add();original=self.world.objects.list()
  self.edit('object',id=obj['id'],patch={'rotation_deg':[0,0,45],'scale':2})
  changed=self.world.objects.list();self.assertEqual(changed[0]['id'],obj['id'])
  self.edit('duplicate',id=obj['id']);duplicated=self.world.objects.list();self.assertEqual(len(duplicated),2);self.assertNotEqual(duplicated[0]['id'],duplicated[1]['id'])
  self.edit('remove',id=obj['id']);self.assertEqual(len(self.world.objects.list()),1)
  self.service.history('undo',{'revision':self.service.scene_status()['revision']});self.assertEqual(self.world.objects.list(),duplicated)
  self.service.history('undo',{'revision':self.service.scene_status()['revision']});self.assertEqual(self.world.objects.list(),changed)
  self.service.history('undo',{'revision':self.service.scene_status()['revision']});self.assertEqual(self.world.objects.list(),original)
  self.service.history('redo',{'revision':self.service.scene_status()['revision']});self.assertEqual(self.world.objects.list(),changed)
 def test_locked_fixture_blocks_all_user_history_actions_except_explicit_unlock(self):
  obj=self.add(locked=True)
  for action,payload in [('object',{'id':obj['id'],'patch':{'scale':2}}),('duplicate',{'id':obj['id']}),('remove',{'id':obj['id']}),('array',{'id':obj['id'],'counts':[2,1,1],'spacing':[100,0,0]}),('align',{'ids':[obj['id']],'ground':True})]:
   with self.assertRaises(HTTPException):self.edit(action,**payload)
  self.edit('object',id=obj['id'],patch={'locked':False});self.assertFalse(self.world.objects.list()[0]['locked'])
 def test_locked_gravity_fixture_never_drifts_and_clears_old_velocity(self):
  obj=self.add(locked=True,gravity=True);self.world.objects._vel[obj['id']]=-200
  before=self.world.objects.list();self.assertFalse(self.world.objects.any_active())
  self.world.objects.step(.1,[]);self.assertEqual(self.world.objects.list(),before);self.assertEqual(self.world.objects._vel[obj['id']],0)
 def test_task_geometry_cannot_be_silently_changed_but_fixed_obstacles_have_real_transforms(self):
  task=dict(group='a'*32,mode='handling',role='piece',symbol='',slot=-1,home=0)
  obj=self.add(task=task)
  with self.assertRaises(ValueError):self.world.objects.update(obj['id'],{'scale':2})
  obstacle=self.add(task=dict(task,role='obstacle'),rotation_deg=[0,0,30],locked=True)
  self.assertEqual(obstacle['rotation_deg'],[0,0,30])
 def test_duplicate_ids_and_stale_revision_are_rejected_without_losing_scene(self):
  obj=self.add();before=self.world.objects.path.read_bytes()
  with self.assertRaises(ValueError):self.world.objects.add(obj)
  with self.assertRaises(ValueError):self.world.objects.replace([obj,obj])
  self.assertEqual(self.world.objects.path.read_bytes(),before)
  revision=self.service.scene_status()['revision'];self.world.objects.update(obj['id'],{'name':'external'})
  with self.assertRaises(HTTPException) as error:self.service.edit_scene(dict(revision=revision,id=obj['id'],patch={'scale':2}),'object')
  self.assertEqual(error.exception.status_code,409);self.assertEqual(self.world.objects.list()[0]['name'],'external')
 def test_ground_placement_uses_existing_history_and_stable_id(self):
  result=self.edit('add',spec=dict(kind='box',size=[100,100,20],xyz=[125,-250,10],name='placed',gravity=False))
  placed=result['objects'][0];self.assertEqual(placed['xyz'],[125,-250,10]);self.assertTrue(placed['id'])
  self.service.history('undo',dict(revision=result['revision']));self.assertEqual(self.world.objects.list(),[])
  status=self.service.scene_status();self.service.history('redo',dict(revision=status['revision']))
  self.assertEqual(self.world.objects.list()[0]['id'],placed['id'])
 def test_placement_rejects_task_lock_or_identity_injection_without_history(self):
  for forbidden in [{'id':'forged'},{'task':{}},{'locked':True}]:
   with self.assertRaises(HTTPException):self.edit('add',spec=dict(kind='box',size=[100,100,20],xyz=[0,0,10],**forbidden))
  self.assertEqual(self.world.objects.list(),[]);self.assertEqual(self.service.undo_stack,[])
if __name__=='__main__':unittest.main()
