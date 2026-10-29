"""RealLink subclass integrating the SAME original reader/command IO.

No extra sockets, timers, connection attempts or
automatic enabling are introduced. Only the existing RealLink lifecycle can
connect. A raw-IO fault latches this instance closed; recovery requires external
reconciliation and a NEW link, never silently reconnecting/replaying Servo.
Normal queued commands, identity probes and heartbeat roundtrips all enter one
arbiter. Live integration also MUST wire World's global motion/config lease.
"""
import asyncio
import time

from backend.core.robot_real import RealLink, Reply, Desync
from .real_servo_transport import ExclusiveServoTransport, TransportError


class _RawChannel:
    def __init__(self,link):self.link=link
    @property
    def session(self):
        link=self.link
        if not link.cmd_up or link._dead or link._closing or link._writer is None:return None
        return (link.command_session,id(link._writer))
    async def exchange(self,payload,*,session,deadline):
        if session!=self.session:raise ConnectionError('原命令连接已失效')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('写入前已超时')
        text=payload.decode('ascii')
        name=text.split(',',1)[0]
        reply=await RealLink._roundtrip(self.link,text,name,reply_timeout=remaining)
        if session!=self.session:raise ConnectionError('回执来自失效命令连接')
        return reply.as_dict()
    def abort(self,session):
        link=self.link
        # Compare the actual identity, even if cmd_up/_dead already changed.
        if session!=(link.command_session,id(link._writer)):return
        link._closing=True  # inherited loops must NOT reconnect this instance
        link.cmd_up=False
        link._dead=True
        link.invalidate_pending('在线命令结果未知，已隔离原通道，禁止重放')
        link.raw=link.ascii=b''
        link._data.set()
        if link._writer is not None:link._writer.close()


class _LinkTransport(ExclusiveServoTransport):
    def __init__(self,link):
        self.link=link
        self.stop_owner=self.stop_handler=self.stop_signal=None
        super().__init__(_RawChannel(link))
    def set_stop_handler(self,owner,handler,*,invalidate):
        if self.owner is not None or not callable(handler) or not callable(invalidate):return False
        self.stop_owner,self.stop_handler=owner,handler
        self.stop_signal=invalidate
        self.link._exclusive_stop_task=None
        return True
    def acquire(self,owner):
        link=self.link
        if (self.stop_owner!=owner or self.stop_handler is None or link.jogging
                or link._jog_intent is not None or link._inflight is not None
                or not link.q.empty() or link._pending or link._futures
                or (link._stop_task is not None and not link._stop_task.done())):
            return False
        return super().acquire(owner)


class ServoCapableRealLink(RealLink):
    """World's link factory. Constructing does not open a connection."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._servo_arbiter=None
        self._exclusive_stop_task=None

    @property
    def cmd_up(self):return getattr(self,'_command_up',False)

    @cmd_up.setter
    def cmd_up(self,value):
        self._command_up=value
        arbiter=getattr(self,'_servo_arbiter',None)
        if value is False and arbiter is not None and arbiter.owner is not None and not arbiter.quarantined:
            # The inherited loop sets cmd_up=False BEFORE its reconnect sleep.
            # Latch now, not after a replacement socket has been opened.
            self._closing=True
            arbiter._fault('独占运动期间命令连接断开，禁止自动重连')

    def servo_transport(self):
        if self._servo_arbiter is not None:
            return self._servo_arbiter  # quarantine persists across identity changes
        if _RawChannel(self).session is None:
            raise RuntimeError('原命令连接未建立，不能取得在线控制通道')
        self._servo_arbiter=_LinkTransport(self)
        return self._servo_arbiter

    async def _probe_controller_identity(self):
        arbiter=self._servo_arbiter
        if arbiter is not None and (arbiter.owner is not None or arbiter.quarantined):return False
        return await super()._probe_controller_identity()

    async def _roundtrip(self,text,name,*,reply_timeout=None):
        # All inherited normal/probe/heartbeat traffic is serialized here.
        # Servo's raw adapter explicitly invokes the BASE implementation once.
        arbiter=self.servo_transport()
        budget=reply_timeout
        if budget is None:
            from backend.core.robot_real import POWER_REPLY_TIMEOUTS, REPLY_TIMEOUT_S
            budget=POWER_REPLY_TIMEOUTS.get(name,REPLY_TIMEOUT_S)
        pending=self._pending.get(self._inflight)
        if pending is not None:
            budget=min(budget,pending[0]-time.monotonic())
        if budget<=0:
            reply=Reply(name,False,sent='普通请求排队期限已过，未发送');reply.outcome='not_sent'
            return reply
        try:
            result=await arbiter.exchange(text.encode('ascii'),owner=None,timeout=budget)
        except TransportError as exc:
            if exc.outcome=='not_sent':
                reply=Reply(name,False,sent=str(exc));reply.outcome='not_sent'
                return reply
            raise Desync(str(exc)) from exc
        reply=Reply(result['name'],result['ok'],result.get('fields'),result.get('err_code'),
                    result.get('rtt_ms',0.),result.get('timeout',False),text)
        reply.outcome=result.get('outcome',reply.outcome)
        return reply

    async def ask(self,name,*params,**kwargs):
        arbiter=self._servo_arbiter
        if name=='GrpStop' and arbiter is not None and arbiter.owner is not None:
            session=kwargs.get('command_session',self.command_session)
            return await self.estop(command_session=session)
        if arbiter is not None and (arbiter.owner is not None or arbiter.quarantined):
            reply=Reply(name,False,sent='在线跟随独占或通道已隔离；普通指令未发送')
            reply.outcome='not_sent'
            return reply
        expected_seq=self._seq+1
        try:
            reply=await super().ask(name,*params,**kwargs)
        except asyncio.CancelledError:
            if self._servo_arbiter is not None and self._inflight==expected_seq:
                self._servo_arbiter._fault('普通请求在途取消，执行结果未知')
            raise
        if (reply.outcome=='unknown' and self._servo_arbiter is not None
                and self._inflight==expected_seq):
            self._servo_arbiter._fault('普通请求期限已过，执行结果未知')
        return reply

    def _begin_stop(self,command_session,reason='停止请求'):
        arbiter=self._servo_arbiter
        if arbiter is None or arbiter.owner is None:
            return super()._begin_stop(command_session,reason)
        if command_session!=self.command_session:return None
        if self._exclusive_stop_task is not None:return self._exclusive_stop_task
        arbiter.stop_signal(reason)  # revoke old centres/IK BEFORE scheduling IO
        async def stop_owner():
            try:
                result=await arbiter.stop_handler(reason)
                confirmed=isinstance(result,dict) and result.get('stop_confirmed') is True
            except Exception:
                arbiter.quarantine(arbiter.owner,'独占任务停止处理异常，实际停止未知')
                confirmed=False
            reply=Reply('GrpStop',confirmed,sent=reason)
            reply.outcome='acknowledged' if confirmed else 'unknown'
            return reply
        self._exclusive_stop_task=asyncio.create_task(stop_owner())
        self._exclusive_stop_task.add_done_callback(lambda task:None if task.cancelled() else task.exception())
        return self._exclusive_stop_task
