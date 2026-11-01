from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio,time,unittest,copy
from types import SimpleNamespace as NS
import cv2 as cv
import numpy as np
from backend.core.vision_workspace import VisionWorkspace
from backend.core.play_vision import bind_observations
from backend.application.vision_tasks import VisionTasks,VisionTaskError
from backend.application.remote_camera import RemoteCamera
from check_remote_camera import Pipeline
class Workpiece(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.camera=NS(snapshot=lambda:{'active':None});self.vision=VisionWorkspace(self.camera);self.pipeline=Pipeline();self.submitted=[];self.pipeline.submit=lambda *a,**k:self.submitted.append((a,k));self.revoked=0
  async def revoke():self.revoked+=1
  self.remote=RemoteCamera(self.pipeline,on_revoke=revoke,workspace=self.vision,peer_factory=lambda:None)
  self.image=np.full((240,320,3),90,np.uint8);cv.circle(self.image,(90,100),25,(230,0,0),-1);cv.rectangle(self.image,(200,70),(260,130),(0,220,0),-1)
 async def asyncTearDown(self):await self.remote.close()
 async def activate(self):
  pair=await self.remote.pair('workpiece');joined=self.remote.join(pair['code'],'https://station');self.remote.epoch=self.vision.begin_remote(joined['session'])['epoch'];return joined
 def publish(self,seq=1):
  self.assertTrue(self.vision.remote_frame(self.image,session=self.remote.session,epoch=self.remote.epoch,seq=seq,received_at=time.monotonic()));self.vision.process();return self.vision.snapshot()
 async def test_actual_pixels_common_result_and_readonly_find_count_no_echo(self):
  j=await self.activate();snap=self.publish();r=snap['result'];self.assertEqual(r['source'],'remote');self.assertIsNone(r['captured_at']);self.assertFalse(r['capture_age_available']);self.assertFalse(r['control_valid']);self.assertEqual({o['color'] for o in r['objects']},{'red','green'})
  tasks=VisionTasks(self.vision);state=tasks.state();self.assertTrue(state['available'])
  found=tasks.preview({'mode':'find','context_id':state['context_id'],'selector':{'color':'red'}})['task'];self.assertAlmostEqual(found['result']['selected']['center_px'][0],90,delta=1);self.assertIsNone(found['result']['frame']['captured_at']);self.assertFalse(found['result']['motion'])
  counted=tasks.preview({'mode':'inspection','context_id':state['context_id'],'selector':{'color':'red'},'expected_count':1})['task'];self.assertTrue(counted['result']['passed'])
  beat=await self.remote.beat(j['token'],'https://station');self.assertNotIn('image',beat['result']);self.assertFalse(beat['result']['control_valid']);self.assertEqual(len(beat['result']['objects']),2);self.assertEqual(self.submitted,[])
  with self.assertRaisesRegex(ValueError,'远程视频'):bind_observations(snap,[],time.time())
 async def test_same_service_calibration_and_slots_current_session_only(self):
  await self.activate();self.publish();self.vision.calibrate({'points':[[10,10,10,10],[300,10,300,10],[300,220,300,220],[10,220,10,220],[100,150,100,150],[200,180,200,180]],'plane_z':0})
  self.publish(2);tasks=VisionTasks(self.vision);state=tasks.state();result=tasks.preview({'mode':'inspection','context_id':state['context_id'],'selector':{'color':'red'},'slots':[{'id':'A','xy_mm':[90,100]}],'tolerance_mm':5})['task']['result'];self.assertTrue(result['passed'])
  await self.remote.revoke();self.assertIsNone(self.vision.calibration);self.assertIsNone(self.vision.latest);self.assertFalse(tasks.state()['available'])
 async def test_epochs_out_of_order_bounded_slot_and_expiry(self):
  await self.activate();old=(self.remote.session,self.remote.epoch);self.publish()
  self.assertFalse(self.vision.remote_frame(self.image,session=old[0],epoch=old[1],seq=1,received_at=time.monotonic()))
  for seq in range(2,20):self.vision.remote_frame(self.image,session=old[0],epoch=old[1],seq=seq,received_at=time.monotonic())
  self.assertEqual(self.vision.remote_data[2],19);self.assertEqual(len(self.vision.remote_data),5)
  self.vision.process();self.vision.latest['received_at']=time.time()-2;self.assertTrue(self.vision.snapshot()['stale']);self.assertFalse(VisionTasks(self.vision).state()['available'])
  await self.activate();self.assertFalse(self.vision.remote_frame(self.image,session=old[0],epoch=old[1],seq=30,received_at=time.monotonic()));self.assertIsNone(self.vision.calibration)
 async def test_inference_failure_never_looks_like_valid_empty_detection(self):
  j=await self.activate();self.publish();self.vision.invalidate('检测未完成：合成失败')
  result=(await self.remote.beat(j['token'],'https://station'))['result'];self.assertFalse(result['valid']);self.assertFalse(result['control_valid']);self.assertIn('合成失败',result['error']);self.assertNotIn('image',result)
 async def test_first_frame_wait_is_not_detector_failure_but_stream_expiry_is(self):
  j=await self.activate();self.vision.process();self.assertIsNone((await self.remote.beat(j['token'],'https://station'))['result'])
  self.publish();self.vision.remote_received=time.monotonic()-2;self.vision.process()
  result=(await self.remote.beat(j['token'],'https://station'))['result'];self.assertFalse(result['valid']);self.assertIn('过期',result['error'])
 async def test_disconnect_and_late_inference_never_restore_old_result(self):
  await self.activate();self.publish();original=self.vision.analyze
  def switched(*args):
   result=original(*args);self.vision.end_remote(self.remote.session,self.remote.epoch);return result
  self.vision.analyze=switched;self.vision.remote_frame(self.image,session=self.remote.session,epoch=self.remote.epoch,seq=2,received_at=time.monotonic());self.vision.process();self.assertIsNone(self.vision.latest)
 async def test_receiver_feeds_existing_hsv_not_hand_and_releases_track(self):
  await self.activate();outer=self
  class Frame:
   width=320;height=240
   def to_ndarray(inner,format):outer.assertEqual(format,'rgb24');return outer.image
  class Track:
   stopped=False;first=True
   async def recv(inner):
    if inner.first:inner.first=False;return Frame()
    await asyncio.Event().wait()
   def stop(inner):inner.stopped=True
  track=Track();self.remote.worker=asyncio.create_task(self.remote._receive(track,self.remote.session))
  for _ in range(100):
   if self.vision.remote_data:break
   await asyncio.sleep(.005)
  self.assertIsNotNone(self.vision.remote_data);self.vision.process();self.assertEqual(len(self.vision.latest['objects']),2);self.assertEqual(self.submitted,[])
  await self.remote.revoke();self.assertTrue(track.stopped);self.assertIsNone(self.vision.remote_data);self.assertIsNone(self.vision.latest)
 async def test_source_switch_cleanup_does_not_erase_new_source(self):
  await self.activate();self.publish();self.vision.configure({'input':'image'});self.vision.latest={'new':'source'}
  await self.remote.expire();self.assertIsNone(self.remote.session);self.assertIsNone(self.vision.remote_data);self.assertEqual(self.vision.latest,{'new':'source'})
 async def test_real_offer_selects_exclusive_workpiece_mode_and_pair_api_validates(self):
  from fastapi import FastAPI
  import httpx
  from backend.remote_camera_routes import create_remote_camera_router
  class Peer:
   connectionState='new'
   def on(inner,event):return lambda callback:callback
   async def setRemoteDescription(inner,value):pass
   async def createAnswer(inner):return NS(type='answer',sdp='answer')
   async def setLocalDescription(inner,value):inner.localDescription=value
   async def close(inner):inner.closed=True
  configured=[];original=self.pipeline.configure
  def configure(**kw):configured.append(kw);return original(**kw)
  self.pipeline.configure=configure;self.remote.peer_factory=lambda:(Peer(),NS)
  app=FastAPI();app.include_router(create_remote_camera_router(self.remote))
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
   legacy=(await client.post('/api/vision/remote/pair')).json();self.assertEqual(legacy['detector'],'hand')
   invalid=await client.post('/api/vision/remote/pair',json={'detector':'both'});self.assertEqual(invalid.status_code,422)
   paired=(await client.post('/api/vision/remote/pair',json={'detector':'workpiece'})).json();self.assertEqual(paired['detector'],'workpiece')
   joined=self.remote.join(paired['code'],'https://station');self.assertEqual(joined['detector'],'workpiece')
   answer=await self.remote.offer(joined['token'],'https://station',{'type':'offer','sdp':'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\nb=AS:1500\r\na=sendonly\r\n'})
   self.assertEqual(answer['session'],joined['session']);self.assertEqual(configured,[{'enabled':False}]);self.assertTrue(self.vision.remote_identity()['active']);self.assertEqual(self.submitted,[])
   self.publish();await self.remote.pair('hand');self.assertIsNone(self.vision.latest);self.assertIsNone(self.vision.remote_session)
if __name__=='__main__':unittest.main()
