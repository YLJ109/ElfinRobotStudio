from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio,threading,unittest
import httpx
from fastapi import FastAPI
from backend.vision_workspace_routes import create_workspace_vision_router
class Priority(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.enter=threading.Event();self.release=threading.Event();self.calls=[]
  class Engine:
   def virtual_frame(inner,v):self.calls.append('frame');self.enter.set();self.release.wait(5);return {'ok':True}
   def configure(inner,v):self.calls.append('config');return {'ok':True}
   def calibrate(inner,v):self.calls.append('calibrate');return {'ok':True}
   def snapshot(inner):return {'ok':True}
  app=FastAPI();app.include_router(create_workspace_vision_router(Engine()));self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
 async def asyncTearDown(self):self.release.set();await self.client.aclose()
 async def start_frame(self):
  task=asyncio.create_task(self.client.post('/api/vision/workspace/virtual',json={}))
  await asyncio.to_thread(self.enter.wait,2);return task
 async def test_priority_wait_blocks_new_frames_and_second_config(self):
  frame=await self.start_frame();config=asyncio.create_task(self.client.post('/api/vision/workspace/calibrate',json={}))
  await asyncio.sleep(.05);self.assertFalse(config.done())
  self.assertEqual((await self.client.post('/api/vision/workspace/virtual',json={})).status_code,429)
  self.assertEqual((await self.client.post('/api/vision/workspace/config',json={})).status_code,429)
  self.release.set();self.assertEqual((await frame).status_code,200);self.assertEqual((await config).status_code,200);self.assertEqual(self.calls,['frame','calibrate'])
 async def test_cancel_waiter_releases_pending_not_running_worker(self):
  frame=await self.start_frame();config=asyncio.create_task(self.client.post('/api/vision/workspace/config',json={}));await asyncio.sleep(.03);config.cancel()
  with self.assertRaises(asyncio.CancelledError):await config
  self.assertEqual((await self.client.post('/api/vision/workspace/virtual',json={})).status_code,429)
  self.release.set();await frame;self.assertEqual((await self.client.post('/api/vision/workspace/config',json={})).status_code,200)
 async def test_timeout_keeps_worker_owned_and_allows_later_retry(self):
  frame=await self.start_frame();response=await self.client.post('/api/vision/workspace/config',json={});self.assertEqual(response.status_code,408);self.assertEqual(self.calls,['frame'])
  self.assertEqual((await self.client.post('/api/vision/workspace/virtual',json={})).status_code,429)
  self.release.set();await frame;self.assertEqual((await self.client.post('/api/vision/workspace/config',json={})).status_code,200)
 async def test_cancel_started_explicit_worker_never_releases_ownership(self):
  entered=threading.Event();release=threading.Event()
  class Engine:
   def configure(inner,v):entered.set();release.wait(3);return {'ok':True}
   def virtual_frame(inner,v):return {'ok':True}
   def snapshot(inner):return {'ok':True}
  app=FastAPI();app.include_router(create_workspace_vision_router(Engine()))
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
   task=asyncio.create_task(client.post('/api/vision/workspace/config',json={}))
   try:
    await asyncio.to_thread(entered.wait,2);task.cancel()
    with self.assertRaises(asyncio.CancelledError):await task
    self.assertEqual((await client.post('/api/vision/workspace/config',json={})).status_code,429)
    self.assertEqual((await client.post('/api/vision/workspace/virtual',json={})).status_code,429)
    release.set()
    for _ in range(100):
     response=await client.post('/api/vision/workspace/virtual',json={})
     if response.status_code!=429:break
     await asyncio.sleep(.005)
    self.assertEqual(response.status_code,200)
   finally:release.set()
 async def test_invalid_body_releases_explicit_reservation_and_state_stays_live(self):
  response=await self.client.post('/api/vision/workspace/config',content=b'{bad',headers={'content-type':'application/json'})
  self.assertEqual(response.status_code,422)
  self.assertEqual((await self.client.get('/api/vision/workspace/state')).status_code,200)
  self.assertEqual((await self.client.post('/api/vision/workspace/config',json={})).status_code,200)
if __name__=='__main__':unittest.main()
