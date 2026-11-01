"""Remote camera capability negative and lifecycle tests, no sockets or cameras."""
from _offline_guard import require_offline_copy
require_offline_copy()
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import asyncio
import os
import httpx
from fastapi import FastAPI
from backend.application.remote_camera import RemoteCamera,RemoteError
from backend.remote_camera_routes import create_remote_camera_router

class Pipeline:
    def __init__(self):self.value=dict(session=None,epoch=0,result=None)
    def snapshot(self):return dict(self.value)
    def configure(self,**kw):
        self.value=dict(session=kw.get('session'),epoch=self.value['epoch']+1,result=None)
        return self.snapshot()

class Remote(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now=0.;self.revoked=0
        async def revoke():self.revoked+=1
        self.pipeline=Pipeline();self.remote=RemoteCamera(self.pipeline,on_revoke=revoke,peer_factory=lambda:None,clock=lambda:self.now)
    async def asyncTearDown(self):await self.remote.close()
    async def paired(self):
        pair=await self.remote.pair();return self.remote.join(pair['code'],'https://station')
    async def test_single_use_code_origin_token_and_scope(self):
        pair=await self.remote.pair();join=self.remote.join(pair['code'],'https://station')
        with self.assertRaises(RemoteError):self.remote.join(pair['code'],'https://station')
        with self.assertRaises(RemoteError):self.remote.authorize(join['token'],'https://other')
        with self.assertRaises(RemoteError):self.remote.authorize('wrong','https://station')
        self.remote.authorize(join['token'],'https://station')
        self.assertFalse(self.remote.snapshot()['control_available'])
        await self.remote.revoke()
        with self.assertRaises(RemoteError):self.remote.authorize(join['token'],'https://station')
    async def test_pair_expiry_and_attempt_limit(self):
        pair=await self.remote.pair();self.now=121
        with self.assertRaises(RemoteError):self.remote.join(pair['code'],'https://station')
        self.now=0;pair=await self.remote.pair()
        for _ in range(5):
            with self.assertRaises(RemoteError):self.remote.join('wrong','https://station')
        with self.assertRaises(RemoteError):self.remote.join(pair['code'],'https://station')
    async def test_expiry_clears_only_owned_pipeline_and_never_new_source(self):
        await self.paired();self.remote.epoch=self.pipeline.configure(enabled=True,source='remote',session=self.remote.session)['epoch']
        self.now=16;await self.remote.expire()
        self.assertIsNone(self.pipeline.snapshot()['session']);self.assertEqual(self.revoked,1)
        self.now=0;await self.paired();self.remote.epoch=self.pipeline.configure(enabled=True,session=self.remote.session)['epoch']
        self.pipeline.configure(enabled=True,session='new-local');await self.remote.expire()
        self.assertEqual(self.pipeline.snapshot()['session'],'new-local');self.assertEqual(self.revoked,1)
    async def test_heartbeat_does_not_extend_absolute_lease(self):
        join=await self.paired()
        for now in range(10,900,10):
            self.now=now;await self.remote.beat(join['token'],'https://station')
        self.now=900
        with self.assertRaises(RemoteError):await self.remote.beat(join['token'],'https://station')
    def test_sdp_rejects_audio_data_unbounded_oversize(self):
        good={'type':'offer','sdp':'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\nb=AS:1500\r\na=sendonly\r\n'}
        self.assertEqual(self.remote.validate_offer(good),good['sdp'])
        for sdp in (good['sdp']+'m=audio 9 x\r\n',good['sdp']+'m=application 9 x\r\n',good['sdp'].replace('b=AS:1500','b=AS:9999'),good['sdp'].replace('sendonly','sendrecv'),'x'*65537):
            with self.assertRaises(RemoteError):self.remote.validate_offer(dict(type='offer',sdp=sdp))
    async def test_fake_peer_negotiation_reentry_and_failure_release(self):
        class Peer:
            connectionState='new'
            def __init__(self):self.closed=False
            def on(self,event):return lambda fn:fn
            async def setRemoteDescription(self,value):pass
            async def createAnswer(self):return SimpleNamespace(type='answer',sdp='answer')
            async def setLocalDescription(self,value):self.localDescription=value
            async def close(self):self.closed=True
        peer=Peer();self.remote.peer_factory=lambda:(peer,lambda **kw:SimpleNamespace(**kw))
        join=await self.paired()
        value=dict(type='offer',sdp='v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\nb=AS:1500\r\na=sendonly\r\n')
        answer=await self.remote.offer(join['token'],'https://station',value)
        self.assertEqual(answer['type'],'answer');self.assertEqual(self.revoked,1)
        with self.assertRaises(RemoteError):await self.remote.offer(join['token'],'https://station',value)
        await self.remote.revoke();self.assertTrue(peer.closed);self.assertIsNone(self.pipeline.snapshot()['session'])
        peer=Peer()
        async def fail(value):raise RuntimeError('negotiation failure')
        peer.setRemoteDescription=fail
        self.remote.peer_factory=lambda:(peer,lambda **kw:SimpleNamespace(**kw))
        join=await self.paired()
        with self.assertRaises(RuntimeError):await self.remote.offer(join['token'],'https://station',value)
        self.assertTrue(peer.closed);self.assertEqual(self.remote.phase,'idle')
        self.assertIsNone(self.pipeline.snapshot()['session'])

    async def test_old_negotiation_failure_cannot_revoke_new_pair(self):
        entered=asyncio.Event();resume=asyncio.Event()
        class Peer:
            def on(self,event):return lambda fn:fn
            async def setRemoteDescription(self,value):
                entered.set();await resume.wait();raise RuntimeError('old failure')
            async def close(self):pass
        self.remote.peer_factory=lambda:(Peer(),lambda **kw:SimpleNamespace(**kw))
        old=await self.paired()
        value=dict(type='offer',sdp='m=video 9 x\r\nb=AS:1000\r\na=sendonly\r\n')
        task=asyncio.create_task(self.remote.offer(old['token'],'https://station',value))
        await entered.wait()
        new=await self.paired();resume.set()
        with self.assertRaises(RuntimeError):await task
        self.remote.authorize(new['token'],'https://station')
        self.assertEqual(self.remote.session,new['session'])

    def test_public_join_url_requires_explicit_trusted_https_origin(self):
        with patch.dict(os.environ,{'ELFIN_REMOTE_ORIGIN':'https://station:8711','HUYAN_ALLOWED_HOSTS':'station'}):
            self.assertEqual(self.remote.snapshot()['join_url'],'https://station:8711/remote-camera.html')
            for value in ('http://station:8711','https://evil','https://station/path','https://user@station','https://station/?token=x'):
                os.environ['ELFIN_REMOTE_ORIGIN']=value
                self.assertIsNone(self.remote.snapshot()['join_url'])

    async def test_oversize_track_is_stopped_and_owned_results_invalidated(self):
        join=await self.paired()
        self.remote.epoch=self.pipeline.configure(enabled=True,session=join['session'])['epoch']
        class Track:
            stopped=False
            async def recv(self):return SimpleNamespace(width=4096,height=4096)
            def stop(self):self.stopped=True
        track=Track()
        await asyncio.wait_for(self.remote._receive(track,join['session']),1)
        self.assertTrue(track.stopped);self.assertIsNone(self.pipeline.snapshot()['session'])
        self.assertEqual(self.remote.phase,'idle');self.assertIsNone(self.remote.token_hash)

    async def test_https_same_origin_and_no_robot_routes(self):
        app=FastAPI();app.include_router(create_remote_camera_router(self.remote))
        pair=await self.remote.pair()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://station') as client:
            self.assertEqual((await client.post('/api/vision/remote/join',json={'code':pair['code']})).status_code,403)
            self.assertEqual((await client.post('/api/vision/remote/join',headers={'origin':'https://evil'},json={'code':pair['code']})).status_code,403)
            join=(await client.post('/api/vision/remote/join',headers={'origin':'https://station'},json={'code':pair['code']})).json()
            headers={'origin':'https://station','authorization':'Bearer '+join['token']}
            self.assertEqual((await client.post('/api/vision/remote/heartbeat',headers=headers,json={})).status_code,200)
            self.assertEqual((await client.post('/api/control/move',headers=headers,json={})).status_code,404)
            await client.post('/api/vision/remote/leave',headers=headers,json={})
            self.assertEqual((await client.post('/api/vision/remote/heartbeat',headers=headers,json={})).status_code,403)

if __name__=='__main__':unittest.main()
