"""Actual ICE/DTLS/SRTP video, strictly synthetic and IPv4 loopback."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio,json,socket,time
from fractions import Fraction
import numpy as np
from av import VideoFrame
from aiortc import RTCPeerConnection,RTCConfiguration,VideoStreamTrack
import aioice.ice
from backend.application.remote_camera import RemoteCamera
from backend.application.vision_pipeline import VisionPipeline

class Synthetic(VideoStreamTrack):
    def __init__(self):super().__init__();self.n=0
    async def recv(self):
        await asyncio.sleep(1/15);self.n+=1
        frame=VideoFrame.from_ndarray(np.full((120,160,3),self.n%255,dtype=np.uint8),format='rgb24')
        frame.pts=self.n*6000;frame.time_base=Fraction(1,90000)
        return frame
class Cameras:
    def snapshot(self):return {}
class Detector:
    def detect(self,*args,**kwargs):return dict(observations=[{'label':'synthetic'}],model={'name':'synthetic-test'})
    def close(self):pass

async def main():
    # Verify both network directions fail closed before any WebRTC activity.
    for action,address in (('bind',('0.0.0.0',0)),('sendto',('192.0.2.1',9))):
        wire=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            try:
                wire.bind(address) if action=='bind' else wire.sendto(b'x',address)
                raise AssertionError('network guard bypass')
            except RuntimeError:pass
        finally:wire.close()
    aioice.ice.get_host_addresses=lambda use_ipv4,use_ipv6:['127.0.0.1']
    pipeline=VisionPipeline(Cameras(),lambda **kw:Detector())
    async def revoke():pass
    remote=RemoteCamera(pipeline,on_revoke=revoke)
    sender=RTCPeerConnection(RTCConfiguration(iceServers=[]));track=Synthetic()
    sender.addTransceiver(track,direction='sendonly')
    try:
        pair=await remote.pair();join=remote.join(pair['code'],'https://station')
        await sender.setLocalDescription(await sender.createOffer())
        sdp=sender.localDescription.sdp.replace('m=video ', 'm=video ',1)
        lines=sdp.split('\r\n');index=next(i for i,v in enumerate(lines) if v.startswith('m=video '));lines.insert(index+1,'b=AS:1500')
        answer=await remote.offer(join['token'],'https://station',dict(type='offer',sdp='\r\n'.join(lines)))
        from aiortc import RTCSessionDescription
        await sender.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'],type='answer'))
        deadline=time.monotonic()+15
        while pipeline.snapshot()['stats']['submitted']<5 and time.monotonic()<deadline:await asyncio.sleep(.05)
        state=pipeline.snapshot();assert state['stats']['submitted']>=5,state
        assert state['result']['valid'] and not state['result']['control_valid'],state
        assert state['result']['source']=='remote'
        assert (state['source'],state['session'],state['epoch'])==('remote',join['session'],answer['epoch'])
        peer=remote.peer
        transports=[v.transport.transport for v in peer.getReceivers()]
        await sender.close()
        deadline=time.monotonic()+6
        while remote.session is not None and time.monotonic()<deadline:await asyncio.sleep(.05)
        assert remote.session is None,remote.snapshot()
        assert peer.connectionState=='closed'
        assert all(v.state=='closed' for v in transports)
        assert not pipeline.snapshot()['enabled'];assert remote.worker is None
        print(json.dumps(dict(identity=[state['source'],state['session'],state['epoch']],frames=state['stats']['submitted'],seq=state['result']['seq'],peer=peer.connectionState,ice=[v.state for v in transports],released=True,real_camera=False,external_network=False)))
    finally:
        track.stop();await sender.close();await remote.close();await pipeline.close()

asyncio.run(main())
