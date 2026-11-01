"""Vision transport limits and identity gates over in-memory HTTP."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import cv2
import httpx
import numpy as np
from fastapi import FastAPI
from backend.application.vision_pipeline import VisionPipeline
from backend.vision_pipeline_routes import create_pipeline_router


class Detector:
    def detect(self,image):return {'observations':[],'model':{'name':'fake','version':'1'}}
    def close(self):pass


class API(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipeline=VisionPipeline(SimpleNamespace(snapshot=lambda:{'active':None},raw_frame_copy=lambda _:None),lambda **_:Detector())
        await self.pipeline.start()
        self.addAsyncCleanup(self.pipeline.close)
        self.revoke=AsyncMock()
        app=FastAPI()
        @app.exception_handler(ValueError)
        async def invalid(request,exc):
            from fastapi.responses import JSONResponse
            return JSONResponse({'ok':False,'why':str(exc)},422)
        app.include_router(create_pipeline_router(self.pipeline,on_revoke=self.revoke))
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline')
        self.addAsyncCleanup(self.client.aclose)
        _,png=cv2.imencode('.png',np.zeros((24,32,3),dtype=np.uint8))
        self.image=png.tobytes()

    async def config(self,source='remote',session='remote-A'):
        response=await self.client.post('/api/vision/pipeline/config',json={'enabled':True,'source':source,'session':session})
        self.assertEqual(response.status_code,200,response.text)
        self.headers={'content-type':'image/png','x-vision-session':session,
                      'x-vision-epoch':str(response.json()['epoch']),'x-frame-seq':'1','x-capture-time':'123.4'}

    async def test_experimental_upload_has_no_video_echo_or_remote_control(self):
        await self.config()
        response=await self.client.post('/api/vision/pipeline/frame',content=self.image,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['ok'])
        for _ in range(100):
            state=(await self.client.get('/api/vision/pipeline')).json()
            if state['result']:break
            await asyncio.sleep(.005)
        self.assertTrue(state['result']['valid'])
        self.assertFalse(state['result']['control_valid'])
        self.assertNotIn('image',state['result'])
        self.assertEqual(state['transport'],'experimental_binary_upload')
        # 开启检测不是"换检测源"：启动跟随的流程就是先到中心、再开检测、再
        # 等画面，开检测本身撤销正在进行的动作会把用户刚点的开始作废。
        self.revoke.assert_not_awaited()

    async def test_disable_and_switch_revoke_old_epoch(self):
        await self.config()
        old=dict(self.headers)
        response=await self.client.post('/api/vision/pipeline/config',json={'enabled':False})
        self.assertEqual(response.status_code,200)
        self.assertEqual((await self.client.post('/api/vision/pipeline/frame',content=self.image,headers=old)).status_code,409)
        self.assertIsNone(self.pipeline.snapshot()['result'])
        # 只有关掉检测（开启那次不算）才撤销。
        self.assertEqual(self.revoke.await_count,1)

    async def test_switching_session_while_enabled_revokes(self):
        await self.config(session='remote-A')
        before=self.revoke.await_count
        response=await self.client.post('/api/vision/pipeline/config',
                                        json={'enabled':True,'source':'remote','session':'remote-B'})
        self.assertEqual(response.status_code,200)
        # 检测还开着却换了会话，这才是真的换了检测源，必须撤销。
        self.assertEqual(self.revoke.await_count,before+1)

    async def test_invalid_config_does_not_cancel_existing_owner(self):
        await self.config()
        before=self.pipeline.epoch
        result=await self.client.post('/api/vision/pipeline/config',json={'enabled':True,'source':'local','session':'not-open'})
        self.assertEqual(result.status_code,422)
        self.assertEqual(self.pipeline.epoch,before)
        # 开启这次不撤销，无效请求也不撤销。
        self.assertEqual(self.revoke.await_count,0)

    async def test_bad_identity_and_capture_timestamp_never_admitted(self):
        await self.config()
        for patch in ({'x-vision-session':'foreign'},{'x-vision-epoch':'0'},{'x-capture-time':'NaN'}):
            response=await self.client.post('/api/vision/pipeline/frame',content=self.image,headers={**self.headers,**patch})
            self.assertIn(response.status_code,(409,422))
        self.assertEqual(self.pipeline.stats['submitted'],0)

    async def test_body_mime_and_pixel_budgets_enforced(self):
        await self.config()
        result=await self.client.post('/api/vision/pipeline/frame',content=b'x'*(1024*1024+1),headers=self.headers)
        self.assertEqual(result.status_code,413)
        await asyncio.sleep(.1)
        result=await self.client.post('/api/vision/pipeline/frame',content=self.image,headers={**self.headers,'content-type':'text/plain'})
        self.assertEqual(result.status_code,415)
        _,oversized=cv2.imencode('.png',np.zeros((1000,1000,3),dtype=np.uint8))
        result=await self.client.post('/api/vision/pipeline/frame',content=oversized.tobytes(),headers=self.headers)
        self.assertEqual(result.status_code,413)
        self.assertEqual(self.pipeline.stats['submitted'],0)

    async def test_rate_limit_and_duplicate_sequence(self):
        await self.config()
        self.assertEqual((await self.client.post('/api/vision/pipeline/frame',content=self.image,headers=self.headers)).status_code,200)
        self.assertEqual((await self.client.post('/api/vision/pipeline/frame',content=self.image,headers=self.headers)).status_code,429)
        await asyncio.sleep(.1)
        self.assertEqual((await self.client.post('/api/vision/pipeline/frame',content=self.image,headers=self.headers)).status_code,422)


if __name__=='__main__':unittest.main(verbosity=2)
