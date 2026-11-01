"""Actual synthetic RTP RGB -> sole industrial HSV -> read-only finding, loopback only."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio,json,socket,time,base64,os
from pathlib import Path
from fractions import Fraction
import cv2 as cv
import numpy as np
from av import VideoFrame
from aiortc import RTCPeerConnection,RTCConfiguration,VideoStreamTrack,RTCSessionDescription
import aioice.ice
from backend.application.remote_camera import RemoteCamera
from backend.application.vision_pipeline import VisionPipeline
from backend.core.vision_workspace import VisionWorkspace
from backend.application.vision_tasks import VisionTasks
class Synthetic(VideoStreamTrack):
 def __init__(self):super().__init__();self.n=0
 async def recv(self):
  await asyncio.sleep(1/15);self.n+=1
  rgb=np.full((240,320,3),90,np.uint8);cv.circle(rgb,(90,100),25,(230,0,0),-1);cv.rectangle(rgb,(200,70),(260,130),(0,220,0),-1)
  frame=VideoFrame.from_ndarray(rgb,format='rgb24');frame.pts=self.n*6000;frame.time_base=Fraction(1,90000);return frame
class Cameras:
 def snapshot(self):return {'active':None}
async def main():
 for action,address in [('bind',('0.0.0.0',0)),('sendto',('192.0.2.1',9))]:
  wire=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
  try:
   try:
    wire.bind(address) if action=='bind' else wire.sendto(b'x',address)
    raise AssertionError('network guard bypass')
   except RuntimeError:pass
  finally:wire.close()
 aioice.ice.get_host_addresses=lambda *a,**k:['127.0.0.1']
 calls=[]
 def forbidden(**kw):calls.append('hand');raise AssertionError('workpiece mode must not infer hands')
 camera=Cameras();vision=VisionWorkspace(camera);pipeline=VisionPipeline(camera,forbidden)
 async def revoke():pass
 remote=RemoteCamera(pipeline,on_revoke=revoke,workspace=vision);sender=RTCPeerConnection(RTCConfiguration(iceServers=[]));track=Synthetic();sender.addTransceiver(track,direction='sendonly')
 await vision.start();await pipeline.start()
 try:
  pair=await remote.pair('workpiece');join=remote.join(pair['code'],'https://station')
  await sender.setLocalDescription(await sender.createOffer());lines=sender.localDescription.sdp.split('\r\n');i=next(i for i,v in enumerate(lines) if v.startswith('m=video '));lines.insert(i+1,'b=AS:1500')
  answer=await remote.offer(join['token'],'https://station',{'type':'offer','sdp':'\r\n'.join(lines)});await sender.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'],type='answer'))
  until=time.monotonic()+15
  while time.monotonic()<until:
   snap=vision.snapshot();r=snap.get('result')
   if r and r['frame_seq']>=5 and len(r['objects'])==2:break
   await asyncio.sleep(.03)
  assert r and r['frame_seq']>=5,snap
  assert {(o['color'],o['shape']) for o in r['objects']}=={('red','circle'),('green','rectangle')},r['objects']
  assert r['session']==join['session'] and r['epoch']==answer['epoch'];assert r['captured_at'] is None and not r['control_valid']
  (Path(os.environ['HUYAN_OFFLINE_ROOT'])/'remote-industrial-received.jpg').write_bytes(base64.b64decode(r['image']['base64']))
  tasks=VisionTasks(vision);state=tasks.state();task=tasks.preview({'mode':'find','context_id':state['context_id'],'selector':{'color':'red','shape':'circle'}})['task']
  assert task['result']['selected'] and not task['result']['motion'];assert task['result']['image']==r['image']
  beat=await remote.beat(join['token'],'https://station');assert beat['result']['valid'] and 'image' not in beat['result'];assert calls==[];assert pipeline.snapshot()['stats']['inferred']==0
  peer=remote.peer;ice=[v.transport.transport for v in peer.getReceivers()]
  await sender.close();until=time.monotonic()+6
  while remote.session is not None and time.monotonic()<until:await asyncio.sleep(.03)
  assert remote.session is None and remote.worker is None;assert peer.connectionState=='closed';assert all(v.state=='closed' for v in ice)
  assert vision.latest is None and vision.remote_data is None and vision.calibration is None
  print(json.dumps(dict(identity=[r['source'],r['session'],r['epoch']],source_key=r['source_key'],seq=r['frame_seq'],received_at=r['received_at'],captured_at=r['captured_at'],age_basis=r['age_basis'],objects=[{k:o[k] for k in ('color','shape','center_px')} for o in r['objects']],same_frame_find=True,hand_inferred=0,video_echo=False,capture_age_available=False,control_valid=False,peer=peer.connectionState,ice=[v.state for v in ice],released=True,real_camera=False,external_network=False)))
 finally:
  track.stop();await sender.close();await remote.close();await vision.close();await pipeline.close()
asyncio.run(main())
