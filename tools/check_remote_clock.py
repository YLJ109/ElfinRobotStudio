from _offline_guard import require_offline_copy
require_offline_copy()
import unittest,math
from backend.application.remote_clock import RemoteClock
from backend.application.remote_camera import RemoteCamera,RemoteError
from check_remote_camera import Pipeline
class Clock(unittest.TestCase):
 def setUp(self):self.now=1.;self.clock=RemoteClock(lambda:self.now)
 def estimate(self):
  probe=self.clock.exchange({'sent_ms':100});self.now=1.1
  self.clock.exchange({'sent_ms':200,'previous':{'nonce':probe['nonce'],'received_ms':140}})
  return probe
 def test_offset_uncertainty_and_capture_not_qualified(self):
  self.estimate();s=self.clock.snapshot();self.assertEqual(s['offset_ms'],880);self.assertEqual(s['round_trip_ms'],40);self.assertEqual(s['uncertainty_ms'],20);self.assertEqual(s['status'],'estimated');self.assertFalse(s['capture_age_available']);self.assertFalse(s['control_qualified'])
 def test_replay_rollback_rtt_bounds_and_deadline(self):
  for case in ['replay','rollback','client_rollback','negative','too_large','deadline']:
   self.setUp();p=self.estimate();pending=self.clock.pending
   if case=='replay':previous={'nonce':p['nonce'],'received_ms':140}
   else:previous={'nonce':pending['nonce'],'received_ms':{'negative':199,'too_large':5201}.get(case,240)}
   if case=='rollback':self.now=.5
   if case=='deadline':self.now=9.101
   with self.assertRaises(ValueError,msg=case):self.clock.exchange({'sent_ms':239 if case=='client_rollback' else 6000,'previous':previous})
   self.assertEqual(self.clock.snapshot()['status'],'unknown')
 def test_bounded_samples_expire_and_server_rollback_invalidates(self):
  self.estimate()
  for i in range(20):
   p=self.clock.pending;self.now+=.1;self.clock.exchange({'sent_ms':300+i*100,'previous':{'nonce':p['nonce'],'received_ms':p['client_sent_ms']+40}})
  self.assertEqual(len(self.clock.samples),8);self.now+=10;self.assertEqual(self.clock.snapshot()['status'],'unknown')
  self.setUp();self.estimate();self.now=.1;self.assertEqual(self.clock.snapshot()['status'],'unknown')
 def test_payload_strict_numbers(self):
  for value in [None,[],{}, {'sent_ms':True},{'sent_ms':float('nan')},{'sent_ms':float('inf')},{'sent_ms':-1},{'sent_ms':2**52},{'sent_ms':1,'extra':1}]:
   with self.assertRaises(ValueError):self.clock.exchange(value)
 def test_server_processing_excluded_from_network_uncertainty(self):
  ticks=iter([1.,1.004,1.1,1.102]);clock=RemoteClock(lambda:next(ticks))
  p=clock.exchange({'sent_ms':100});clock.exchange({'sent_ms':200,'previous':{'nonce':p['nonce'],'received_ms':144}})
  self.assertEqual(clock.samples[0]['round_trip_ms'],40);self.assertEqual(clock.samples[0]['uncertainty_ms'],20);self.assertEqual(clock.samples[0]['offset_ms'],880)
class Session(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.now=1.;self.remote=RemoteCamera(Pipeline(),on_revoke=self.revoked,peer_factory=lambda:None,clock=lambda:self.now)
 async def revoked(self):pass
 async def asyncTearDown(self):await self.remote.close()
 async def join(self):
  p=await self.remote.pair();return self.remote.join(p['code'],'https://station')
 async def test_invalid_clock_does_not_renew_heartbeat_old_request_compatible(self):
  j=await self.join();before=self.remote.heartbeat;self.now=2
  with self.assertRaises(ValueError):await self.remote.beat(j['token'],'https://station',{'sent_ms':float('nan')})
  self.assertEqual(self.remote.heartbeat,before)
  result=await self.remote.beat(j['token'],'https://station');self.assertTrue(result['ok']);self.assertIsNone(result['clock_probe']);self.assertFalse(result['state']['clock']['control_qualified'])
 async def test_old_session_ack_token_and_revoke_clear_estimate(self):
  j=await self.join();a=await self.remote.beat(j['token'],'https://station',{'sent_ms':100});self.now=1.1
  await self.remote.beat(j['token'],'https://station',{'sent_ms':200,'previous':{'nonce':a['clock_probe']['nonce'],'received_ms':140}})
  self.assertEqual(self.remote.snapshot()['clock']['status'],'estimated')
  newer=await self.join();self.assertEqual(self.remote.snapshot()['clock']['status'],'unknown')
  with self.assertRaises(RemoteError):await self.remote.beat(j['token'],'https://station')
  with self.assertRaises(ValueError):await self.remote.beat(newer['token'],'https://station',{'sent_ms':300,'previous':{'nonce':a['clock_probe']['nonce'],'received_ms':140}})
  self.assertFalse(self.remote.snapshot()['clock']['control_qualified']);await self.remote.revoke();self.assertEqual(self.remote.snapshot()['clock']['status'],'unknown')
 async def test_http_invalid_payload_cannot_renew_and_legacy_empty_body_works(self):
  from fastapi import FastAPI
  import httpx
  from backend.remote_camera_routes import create_remote_camera_router
  app=FastAPI();app.include_router(create_remote_camera_router(self.remote));j=await self.join();initial=self.remote.heartbeat;self.now=2
  headers={'origin':'https://station','authorization':'Bearer '+j['token']}
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://station') as client:
   for payload in [{'clock':{'sent_ms':True}},{'clock':{'sent_ms':1,'untrusted':1}},{'robot_permission':True}]:
    reply=await client.post('/api/vision/remote/heartbeat',json=payload,headers=headers);self.assertEqual(reply.status_code,422);self.assertEqual(self.remote.heartbeat,initial)
   reply=await client.post('/api/vision/remote/heartbeat',json={},headers=headers);self.assertEqual(reply.status_code,200);self.assertIsNone(reply.json()['clock_probe']);self.assertEqual(self.remote.heartbeat,2)
if __name__=='__main__':unittest.main()
