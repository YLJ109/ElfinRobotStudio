"""Staged centre executor using the existing verified MoveRelJ protocol.

Startup only, NOT used for continuous following. All IO goes through the same
exclusive arbiter as Servo. Actual axis-wise paths are checked separately:
a safe simultaneous joint chord does not prove a safe sequence of axis moves.
No connection/enable/HTTP/IO outputs. Host owns stop confirmation and lease.
"""
import asyncio
import math
import time

from backend.core.protocol import encode
from staged_real_palm_pipeline import vector


class FiniteCenterExecutor:
    def __init__(self,transport,read_feedback,validate_segment,profile,*,
                 chunk_deg=10.,settle_timeout_s=120.,clock=time.monotonic,sleep=asyncio.sleep):
        if (type(chunk_deg) not in (float,int) or not 0<chunk_deg<=10.
                or type(settle_timeout_s) not in (float,int) or not 0<settle_timeout_s<=120.):
            raise ValueError('中心运动分段和等待期限无效')
        self.transport,self.read_feedback,self.validate_segment=transport,read_feedback,validate_segment
        self.profile,self.chunk,self.timeout=profile,chunk_deg,settle_timeout_s
        self.clock,self.sleep=clock,sleep
        self.generation=0
        self.state,self.reason='idle',''
        self.task=self.owner=None
        self.connection=self.context=None

    def invalidate(self,owner):
        if self.owner is None or self.owner==owner:self.generation+=1

    def _actual(self):
        f=self.read_feedback()
        if (not isinstance(f,dict) or not vector(f.get('q'),6)
                or not vector(f.get('velocity'),6) or not vector([f.get('stamp'),f.get('override')],2)
                or not 0<=self.clock()-f['stamp']<self.profile.feedback_max_age_s
                or not 0<f['override']<=.05 or type(f.get('frame_seq')) is not int
                or f.get('enabled') is not True or f.get('safety_ok') is not True
                or f.get('motion_allowed') is not True or f.get('connection') is None or not f.get('context')
                or (self.connection is not None and
                    (f['connection']!=self.connection or f['context']!=self.context))
                or any(not a<=v<=b for v,a,b in zip(f['q'],self.profile.lower,self.profile.upper))):
            raise RuntimeError('中心执行的真实反馈、倍率或上下文无效')
        return f

    async def start(self,goal,*,owner):
        if (self.state!='idle' or self.transport.owner!=owner or not vector(goal,6)
                or abs(goal[5])>1e-9
                or any(not a<=v<=b for v,a,b in zip(goal,self.profile.lower,self.profile.upper))):
            return dict(accepted=False,outcome='not_sent')
        try:
            f=self._actual()
            if f.get('idle') is not True:return dict(accepted=False,outcome='not_sent')
            self.connection,self.context=f['connection'],f['context']
            self.owner=owner
            self.state='running'
            generation=self.generation
            self.task=asyncio.create_task(self._run(list(goal),generation))
            return dict(accepted=True,outcome='accepted')
        except Exception as exc:
            self.state,self.reason='fault',str(exc)
            return dict(accepted=False,outcome='not_sent')

    async def _run(self,goal,generation):
        try:
            for _ in range(240):
                if generation!=self.generation:return
                f=self._actual()
                if f.get('idle') is not True:raise RuntimeError('上段未停稳，拒绝下一段')
                error=[b-a for a,b in zip(f['q'],goal)]
                if max(map(abs,error))<=.05:
                    self.state='settled'
                    return
                axis=max(range(6),key=lambda i:abs(error[i]))
                delta=max(-self.chunk,min(self.chunk,error[axis]))
                expected=list(f['q']);expected[axis]+=delta
                previous=list(f['q'])
                count=max(1,math.ceil(abs(delta)/.25))
                for i in range(1,count+1):
                    point=list(f['q']);point[axis]+=delta*i/count
                    if self.validate_segment(previous,point) is not True:
                        raise RuntimeError('实际单轴中心路径检查失败')
                    previous=point
                latest=self._actual()
                if (generation!=self.generation or latest['frame_seq']!=f['frame_seq']
                        or latest['q']!=f['q']):
                    if generation!=self.generation:return
                    raise RuntimeError('路径检查期间实际起点已变化')
                reply=await self.transport.exchange(
                    encode('MoveRelJ',0,axis,1 if delta>0 else 0,abs(delta)),
                    owner=self.owner,timeout=self.profile.stop_timeout_s)
                if generation!=self.generation:return
                if reply.get('name')!='MoveRelJ' or reply.get('ok') is not True:
                    raise RuntimeError('中心分段指令被拒绝')
                ack_at=self.clock()
                until=ack_at+self.timeout
                while generation==self.generation and self.clock()<until:
                    actual=self._actual()
                    if (actual['frame_seq']>f['frame_seq'] and actual['stamp']>ack_at
                            and actual.get('idle') is True
                            and max(abs(a-b) for a,b in zip(actual['q'],expected))<=.05
                            and max(map(abs,actual['velocity']))<=self.profile.start_velocity_deg_s):
                        break
                    await self.sleep(.01)
                else:
                    if generation!=self.generation:return
                    raise TimeoutError('中心分段未收到新的到位反馈')
            raise RuntimeError('中心运动超过分段预算')
        except Exception as exc:
            self.state,self.reason='fault',str(exc)

    async def stop(self,*,owner):
        if self.owner is not None and owner!=self.owner:return False
        self.invalidate(owner)
        if self.task is not None and not self.task.done():
            done,_=await asyncio.wait({self.task},timeout=self.profile.stop_timeout_s)
            if self.task not in done:
                await self.transport.abort(owner)
                return False
        # Original-session binding is enforced by the arbiter/raw adapter.
        reply=await self.transport.exchange(encode('GrpStop',0),owner=owner,
                                            timeout=self.profile.stop_timeout_s)
        self.state='stopped'
        return reply.get('name')=='GrpStop' and reply.get('ok') is True
