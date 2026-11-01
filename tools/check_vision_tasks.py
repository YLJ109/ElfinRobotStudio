from _offline_guard import require_offline_copy
require_offline_copy()
import copy,time,unittest
import cv2 as cv
import numpy as np
from backend.core.vision_workspace import VisionWorkspace
from backend.application.vision_tasks import VisionTasks,VisionTaskError
class Pixels(unittest.TestCase):
 def setUp(self):
  image=np.full((240,320,3),90,np.uint8);cv.circle(image,(90,100),25,(230,0,0),-1);cv.rectangle(image,(200,70),(260,130),(0,220,0),-1)
  config={'colors':['red','green'],'min_area':100,'roi':[0,0,1,1],'height_mm':20,'mapping':False,'enabled':True,'detection_enabled':True,'input':'virtual'}
  result=VisionWorkspace.analyze(image,config,None,'virtual:s:pose')
  for n,obj in enumerate(result['objects']):obj.update(id='V'+str(n+1),status='observed')
  result.update(source='virtual',source_key='virtual:s:pose',frame_seq=1,captured_at=time.time())
  self.snap={'instance_id':'instance','virtual_session':'s','config_generation':1,'config':config,'calibration':None,'result':result,'stale':False}
  class Engine:
   def snapshot(inner):return copy.deepcopy(self.snap)
  self.service=VisionTasks(Engine())
 def preview(self,**kw):
  state=self.service.state();return self.service.preview({'mode':'find','context_id':state['context_id'],'selector':{'color':'red'},'roi':[0,0,1,1],**kw})
 def test_same_frame_pixel_pointer_and_final(self):
  task=self.preview()['task'];r=task['result'];self.assertEqual(r['status'],'found');self.assertAlmostEqual(r['selected']['center_px'][0],90,delta=1);self.assertEqual(r['image'],self.snap['result']['image'])
  checked=self.service.inspect({'preview_id':task['preview_id']})['task'];done=self.service.run({'plan_id':checked['plan_id']})['task'];self.assertEqual(done['phase'],'completed');self.assertFalse(done['result']['motion'])
 def test_context_epoch_stale_cancel_unknown(self):
  task=self.preview()['task'];self.snap['config_generation']+=1
  with self.assertRaises(VisionTaskError):self.service.inspect({'preview_id':task['preview_id']})
  self.assertFalse(self.service.state()['task']['result']['valid'])
  self.snap['result']['captured_at']=time.time()-2
  self.assertFalse(self.service.state()['available'])
 def test_count_pixels_missing_extra_and_roi(self):
  state=self.service.state();t=self.service.preview({'mode':'inspection','context_id':state['context_id'],'selector':{'color':'red'},'roi':[0,0,1,1],'expected_count':2})['task']
  self.assertEqual(t['result']['count'],1);self.assertEqual(t['result']['missing_count'],1);self.assertFalse(t['result']['passed'])
  with self.assertRaises(VisionTaskError):self.preview(selector={'name':'螺丝'})
 def test_slots_require_calibration_and_unused_fields_rejected(self):
  with self.assertRaises(VisionTaskError):self.preview(mode='inspection',slots=[{'id':'A','xy_mm':[90,100]}],tolerance_mm=5)
  with self.assertRaises(VisionTaskError):self.preview(tolerance_mm=5)
 def calibrated(self):
  cal={'source_key':'virtual:s:pose','image_size':[320,240],'matrix':[[1,0,0],[0,1,0],[0,0,1]],'pixel_hull':[[0,0],[319,0],[319,239],[0,239]],'check_errors_mm':[0,0]}
  self.snap['calibration']=cal;self.snap['result']['calibrated']=True
  for obj in self.snap['result']['objects']:obj['mapped']={'xyz_mm':[*obj['center_px'],10],'coordinate_space':'base_xy_planar_estimate'}
 def slot_preview(self,xy):
  return self.service.preview({'mode':'inspection','context_id':self.service.state()['context_id'],'selector':{'color':'red'},'roi':[0,0,1,1],'slots':[{'id':'A','xy_mm':xy}],'tolerance_mm':5})['task']['result']
 def test_real_pixel_positions_missing_extra_misaligned_and_calibration(self):
  self.calibrated();self.assertTrue(self.slot_preview([90,100])['passed'])
  result=self.slot_preview([110,100]);self.assertEqual(result['slots'][0]['status'],'misaligned');self.assertAlmostEqual(result['slots'][0]['error_mm'],20,delta=1)
  result=self.slot_preview([170,100]);self.assertEqual(result['slots'][0]['status'],'missing');self.assertEqual(len(result['extras']),1)
  with self.assertRaises(VisionTaskError):self.slot_preview([500,100])
  self.snap['calibration']['check_errors_mm']=[0,11]
  with self.assertRaises(VisionTaskError):self.slot_preview([90,100])
 def test_full_roi_and_non_observed_excluded(self):
  result=self.preview(roi=[.25,0,.75,1])['task']['result'];self.assertEqual(result['status'],'not_found')
  self.snap['result']['objects'][0]['status']='holding'
  red=next(o for o in self.snap['result']['objects'] if o['color']=='red');red['status']='holding'
  self.assertEqual(self.preview()['task']['result']['status'],'not_found')
 def test_ambiguity_no_arbitrary_pointer_and_cancel_reentry(self):
  red=copy.deepcopy(next(o for o in self.snap['result']['objects'] if o['color']=='red'));red['id']='V99';self.snap['result']['objects'].append(red)
  t=self.preview()['task'];self.assertEqual(t['result']['status'],'ambiguous');self.assertIsNone(t['result']['selected'])
  with self.assertRaises(VisionTaskError):self.service.inspect({'preview_id':t['preview_id']})
  t=self.preview(selector={'track_id':'V99'})['task'];self.service.cancel({'preview_id':t['preview_id']})
  with self.assertRaises(VisionTaskError):self.service.inspect({'preview_id':t['preview_id']})
 def test_new_frame_recomputed_same_context_exact_image_and_source_switch(self):
  t=self.preview()['task'];key=t['context_id'];self.snap['result']['frame_seq']=2;self.snap['result']['image']['base64']='different-frame'
  checked=self.service.inspect({'preview_id':t['preview_id']})['task'];self.assertEqual(checked['context_id'],key);self.assertEqual(checked['result']['frame']['seq'],2);self.assertEqual(checked['result']['image']['base64'],'different-frame')
  self.snap['result']['source_key']='virtual:s:other';self.snap['config_generation']+=1
  with self.assertRaises(VisionTaskError):self.service.run({'plan_id':checked['plan_id']})
 def test_http_strict_fields_and_no_motion_permission(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from backend.routes.vision_tasks import create_vision_tasks_router
  app=FastAPI();app.include_router(create_vision_tasks_router(self.service.vision))
  with TestClient(app) as client:
   state=client.get('/api/vision/tasks/state').json();self.assertFalse(state['capabilities']['motion'])
   self.assertEqual(client.post('/api/vision/tasks/run',json={'plan_id':'bad'}).status_code,409)
   self.assertEqual(client.post('/api/vision/tasks/preview',json={'mode':'find','context_id':state['context_id'],'selector':{'color':'red'},'execute_robot':True}).status_code,422)
if __name__=='__main__':unittest.main()

