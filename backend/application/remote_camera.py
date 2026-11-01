"""Remote camera authentication and one-way WebRTC lifecycle, no robot authority."""
import asyncio
import hashlib
import hmac
import importlib.util
import secrets
import os
from urllib.parse import urlsplit
import time
import copy
from .remote_clock import RemoteClock


class RemoteError(ValueError):
    def __init__(self, why, status=409):
        super().__init__(why); self.status=status


class RemoteCamera:
    LIMITS=dict(width=1280,height=720,fps=15,max_sdp_bytes=65536,heartbeat_seconds=15,session_seconds=900)

    def __init__(self,pipeline,*,on_revoke,workspace=None,peer_factory=None,clock=time.monotonic):
        self.pipeline,self.on_revoke,self.clock=pipeline,on_revoke,clock
        self.peer_factory=peer_factory
        self.workspace=workspace;self.detector='hand'
        self.code=None;self.code_until=0;self.attempts=0
        self.token_hash=None;self.session=None;self.origin=None
        self.expires=self.heartbeat=0;self.epoch=None
        self.peer=None;self.worker=None;self.monitor=None
        self.phase='idle';self.error=None;self.seq=0;self._bytes_sample=None
        self.lock=asyncio.Lock()
        self.clock_estimate=RemoteClock(clock)

    @property
    def available(self):
        return self.peer_factory is not None or importlib.util.find_spec('aiortc') is not None

    def snapshot(self):
        public_origin=None
        configured=os.environ.get('ELFIN_REMOTE_ORIGIN','').strip()
        try:
            url=urlsplit(configured)
            allowed={'localhost','127.0.0.1','::1'} | {v.strip().lower() for v in os.environ.get('HUYAN_ALLOWED_HOSTS','').split(',') if v.strip()}
            if (url.scheme=='https' and url.hostname in allowed and not url.username and not url.password
                    and not url.path and not url.query and not url.fragment and (url.port is None or url.port>0)):
                public_origin=configured
        except ValueError:pass
        return dict(public_origin=public_origin,join_url=public_origin+'/remote-camera.html' if public_origin else None,
                    reason=None if self.available else '未安装远程视频接收依赖', ok=True,available=self.available,transport='webrtc',phase=self.phase,
                    session=self.session,epoch=self.epoch,error=self.error,limits=dict(self.LIMITS),control_available=False,
                    clock=self.clock_estimate.snapshot(),detector=self.detector)

    def _source(self):
        if self.detector=='workpiece':
            identity=self.workspace.remote_identity()
            return dict(session=identity['session'] if identity['active'] else None,epoch=identity['epoch'])
        return self.pipeline.snapshot()

    async def start(self):
        if self.monitor is None or self.monitor.done():self.monitor=asyncio.create_task(self._monitor())

    async def _monitor(self):
        while True:
            await asyncio.sleep(1)
            await self.expire()
            if self.peer:
                try:
                    report=await self.peer.getStats()
                    total=sum(getattr(v,'bytesReceived',0) for v in report.values() if getattr(v,'type',None)=='transport')
                    now=self.clock()
                    if self._bytes_sample:
                        then,previous=self._bytes_sample
                        # Includes DTLS/RTCP overhead and a finite startup burst allowance.
                        if now>then and total-previous>262144+200000*(now-then):
                            await self.revoke('远程视频超过带宽上限')
                    if self._bytes_sample is None:self._bytes_sample=(now,total)
                except Exception:
                    await self.revoke('远程视频统计不可用')

    async def expire(self):
        if self.session:
            p=self._source()
            switched=self.epoch is not None and (p['session'],p['epoch'])!=(self.session,self.epoch)
            if switched or self.clock()>=self.expires or self.clock()-self.heartbeat>=15:
                await self.revoke('来源已切换' if switched else '远程来源已超时')

    async def pair(self,detector='hand'):
        if detector not in ('hand','workpiece'):raise RemoteError('请选择手掌或工件检测',422)
        if detector=='workpiece' and self.workspace is None:raise RemoteError('当前工作站未接入工件检测',503)
        if not self.available:raise RemoteError('未安装远程视频接收依赖',503)
        await self.revoke()
        self.detector=detector
        self.code=secrets.token_urlsafe(9);self.code_until=self.clock()+120;self.attempts=0
        self.phase='pairing'
        return dict(ok=True,code=self.code,expires_in=120,detector=self.detector)

    def join(self,code,origin):
        self.attempts+=1
        if (not self.code or self.clock()>=self.code_until or self.attempts>5
                or not isinstance(code,str) or not hmac.compare_digest(code,self.code)):
            if self.attempts>=5:self.code=None
            raise RemoteError('配对码无效或已过期',403)
        self.code=None;token=secrets.token_urlsafe(32)
        self.clock_estimate.clear()
        self.token_hash=hashlib.sha256(token.encode()).digest()
        self.session=secrets.token_hex(16);self.origin=origin
        self.expires=self.clock()+900;self.heartbeat=self.clock();self.phase='paired';self.error=None
        return dict(ok=True,token=token,session=self.session,expires_in=900,limits=dict(self.LIMITS),detector=self.detector)

    def authorize(self,token,origin):
        digest=hashlib.sha256(token.encode()).digest() if isinstance(token,str) else b''
        if (not self.token_hash or not hmac.compare_digest(digest,self.token_hash)
                or origin!=self.origin or self.clock()>=self.expires or self.clock()-self.heartbeat>=15):
            raise RemoteError('远程来源凭据已失效',403)

    async def beat(self,token,origin,clock_sample=None):
        await self.expire();self.authorize(token,origin)
        probe=self.clock_estimate.exchange(clock_sample) if clock_sample is not None else None
        self.heartbeat=self.clock()
        p=self._source()
        result=None
        if self.detector=='workpiece' and p['session']==self.session:
            value=self.workspace.snapshot();raw=value.get('result')
            if raw and raw.get('session')==self.session and raw.get('epoch')==self.epoch:
                result=copy.deepcopy(raw);result.pop('image',None)
                result.update(observations=result['objects'],valid=not value['stale'] and not value['error'],control_valid=False)
                if not result['valid']:result['error']=value['error'] or '远程接收结果已过期'
            elif value['error'] and value['error']!='等待远程工件画面':
                result=dict(source='remote',session=self.session,epoch=self.epoch,valid=False,control_valid=False,
                            observations=[],objects=[],captured_at=None,capture_age_available=False,error=value['error'])
        elif self.detector=='hand' and p['session']==self.session:result=p['result']
        return dict(ok=True,state=self.snapshot(),clock_probe=probe,result=result)

    @staticmethod
    def validate_offer(value):
        if not isinstance(value,dict) or set(value)!={'type','sdp'} or value['type']!='offer':
            raise RemoteError('视频协商格式无效',422)
        sdp=value['sdp']
        if not isinstance(sdp,str) or not 1<=len(sdp.encode())<=65536:raise RemoteError('视频协商数据过大或无效',422)
        lines=sdp.replace('\r','').split('\n')
        media=[line for line in lines if line.startswith('m=')]
        if len(media)!=1 or not media[0].startswith('m=video ') or [v for v in lines if v in ('a=sendonly','a=recvonly','a=sendrecv','a=inactive')]!=['a=sendonly']:
            raise RemoteError('仅接受一路单向视频',422)
        # Explicit bandwidth limit is negotiated, not an unlimited upload promise.
        if not any(line.startswith('b=AS:') and line[5:].isdigit() and 1<=int(line[5:])<=1500 for line in lines):
            raise RemoteError('视频需明确不超过1500kbps带宽上限',422)
        return sdp

    async def offer(self,token,origin,value):
        self.authorize(token,origin);sdp=self.validate_offer(value)
        async with self.lock:
            self.authorize(token,origin)
            if self.phase!='paired':raise RemoteError('此配对已协商，请重新配对')
            if self.peer_factory:
                peer,description=self.peer_factory()
            else:
                from aiortc import RTCPeerConnection,RTCConfiguration,RTCSessionDescription
                peer=RTCPeerConnection(RTCConfiguration(iceServers=[]));description=RTCSessionDescription
            self.peer=peer;self._bytes_sample=None;session=self.session;self.phase='connecting'
            @peer.on('track')
            def track_received(track):
                if track.kind!='video' or self.worker:
                    track.stop();return
                self.worker=asyncio.create_task(self._receive(track,session))
            @peer.on('connectionstatechange')
            async def changed():
                if self.peer is peer and peer.connectionState in ('failed','closed','disconnected'):
                    await self.revoke('远程视频已断开')
            try:
                # Activation clears old results and revokes motion before accepting any frame.
                await self.on_revoke()
                if self.detector=='workpiece':
                    self.pipeline.configure(enabled=False)
                    state=self.workspace.begin_remote(session)
                else:
                    if self.workspace is not None:self.workspace.configure({'detection_enabled':False})
                    state=self.pipeline.configure(enabled=True,source='remote',session=session)
                self.epoch=state['epoch']
                async with asyncio.timeout(10):
                    await peer.setRemoteDescription(description(sdp=sdp,type='offer'))
                    answer=await peer.createAnswer();await peer.setLocalDescription(answer)
                self.authorize(token,origin)
                return dict(type='answer',sdp=peer.localDescription.sdp,session=session,epoch=self.epoch)
            except BaseException:
                # A late answer/failure from an old peer cannot revoke a newly
                # paired session while negotiation was awaiting ICE gathering.
                if self.session==session and self.peer is peer:
                    await self.revoke('远程视频协商失败')
                else:
                    await peer.close()
                raise

    async def _receive(self,track,session):
        # Drain the transport independently of ndarray conversion/inference.
        # Exactly one decoded frame is retained; newer frames replace it.
        latest=[None];ready=asyncio.Event();pull_error=[None]
        async def pull():
            try:
                while self.session==session:
                    frame=await asyncio.wait_for(track.recv(),5)
                    if not 0<frame.width<=1280 or not 0<frame.height<=720:
                        raise RemoteError('远程视频超过像素上限')
                    latest[0]=(frame,self.clock());ready.set()
            except asyncio.CancelledError:raise
            except Exception as exc:
                pull_error[0]=exc;ready.set()
        pump=asyncio.create_task(pull());last=-float('inf')
        try:
            while self.session==session:
                await ready.wait();ready.clear()
                if pull_error[0]:raise pull_error[0]
                frame,received=latest[0];latest[0]=None
                now=self.clock()
                if now-last<1/15:continue
                p=self._source()
                if (p['session'],p['epoch'])!=(session,self.epoch):break
                last=now;self.seq+=1
                image=await asyncio.to_thread(frame.to_ndarray,format='rgb24')
                if self.detector=='workpiece':
                    self.workspace.remote_frame(image,session=session,epoch=self.epoch,seq=self.seq,received_at=received)
                else:
                    self.pipeline.submit(image,source='remote',session=session,epoch=self.epoch,
                                         seq=self.seq,clock_domain='remote',received_at=received)
                self.phase='streaming'
        except asyncio.CancelledError:raise
        except Exception as exc:
            self.error=str(exc)
        finally:
            pump.cancel();await asyncio.gather(pump,return_exceptions=True)
            latest[0]=None;track.stop()
            if self.session==session:await self.revoke(self.error or '远程视频已结束')

    async def revoke(self,why=None):
        session,epoch=self.session,self.epoch
        self.token_hash=None;self.code=None;self.session=None;self.origin=None;self.epoch=None
        self.clock_estimate.clear()
        peer,self.peer=self.peer,None;worker,self.worker=self.worker,None
        self.phase='idle';self.error=why
        p=self._source()
        if session and (p['session'],p['epoch'])==(session,epoch):
            if self.detector=='workpiece':self.workspace.end_remote(session,epoch)
            else:self.pipeline.configure(enabled=False)
            await self.on_revoke()
        elif session and self.detector=='workpiece':self.workspace.end_remote(session,epoch)
        if worker and worker is not asyncio.current_task():
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)
        if peer:await peer.close()
        return self.snapshot()

    async def close(self):
        if self.monitor:
            self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True);self.monitor=None
        await self.revoke()
