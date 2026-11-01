"""World-owned follow lifecycle; no sockets or HTTP commissioning unlock.

Host dependencies must be qualified adapters, NOT RealLink.ask passthrough:
finite.start(q, owner) -> {accepted: bool, outcome: 'accepted'|'not_sent'|'unknown'}
finite.stop(owner) -> bool (ACK only, actual stop still checked below).
finite.invalidate(owner) -> synchronous revoke of unsent/start intents; it must
be honored immediately before writing, including an awaited finite.start.
All finite/Servo/normal commands must use driver.transport's SINGLE arbiter.
read_feedback returns the actual Servo feedback schema, never target echoes.
"""
import asyncio
import math
import time

from .real_follow_startup import RealFollowStartup
from .real_palm_pipeline import RealPalmTargetPipeline, vector


class _Mailbox:
    def __init__(self, driver):
        self.profile,self.owner=driver.profile,driver.owner
        self.latest=None
    def publish(self,q,**metadata):
        self.latest=(list(q),dict(metadata))
        return True


class RealPalmFollowHost:
    def __init__(self, model, driver, finite, read_feedback, read_context, validate_path,
                 *, clock=time.monotonic, sleep=asyncio.sleep, solve=None,startup_solve=None,lock_j6=True):
        self.driver,self.finite=driver,finite
        if any(not callable(getattr(finite,name,None)) for name in ('start','stop','invalidate')):
            raise ValueError('有限执行器缺少启动、停止或在途撤销接口')
        self.read_feedback,self.read_context=read_feedback,read_context
        self.clock,self.sleep=clock,sleep
        kwargs={'center_solve':startup_solve} if startup_solve else {}
        self.startup=RealFollowStartup(model,read_context,validate_path,clock=clock,**kwargs)
        self.mailbox=_Mailbox(driver)
        kwargs={'solve':solve} if solve else {}
        self.pipeline=RealPalmTargetPipeline(model,self.mailbox,read_context,validate_path,
            startup=self.startup,clock=clock,lock_j6=lock_j6,**kwargs)
        self.phase,self.reason='idle','尚未启动'
        self.generation=0
        self.owned=False
        self.run_task=None
        self.connection=None
        self._stop_task=None
        bind_stop=getattr(driver.transport,'set_stop_handler',None)
        if bind_stop is not None and bind_stop(driver.owner,self.stop,invalidate=self.request_stop) is not True:
            raise ValueError('无法绑定共享通道的紧急停止入口')

    def snapshot(self):
        return dict(phase=self.phase,reason=self.reason,automatic_restart=False,
                    j6_deg=0. if self.pipeline.lock_j6 else None,startup_j6_deg=0.,
                    startup=self.startup.phase,driver=self.driver.snapshot())

    def _actual(self):
        feedback=self.read_feedback()
        override=feedback.get('override')
        if (not vector([override],1) or not 0<override<=.05
                or not vector(feedback.get('velocity'),6)):
            raise RuntimeError('实际倍率须不超过5%，且速度反馈必须有效')
        return feedback

    async def begin(self, *, center,size,direction=0,center_timeout_s=120.,standoff=0.,role='follow'):
        """Explicit user start only. Centre first, never a palm motion yet."""
        if self.phase!='idle' or not 0<center_timeout_s<=600:return False
        self.generation+=1
        generation=self.generation
        try:
            context=self.read_context()
            feedback=self._actual()
            self.pipeline.configure(**context,center=center,size=size,direction=direction,
                                    standoff=standoff,role=role)
            self.connection=feedback.get('connection')
            if self.driver.transport.acquire(self.driver.owner) is not True:
                self.phase,self.reason='fault','运动通道已有任务或故障锁定'
                return False
            self.owned=True
            plan=await asyncio.to_thread(self.startup.prepare,feedback,self.pipeline.workspace['tcp_center'],
                                        self.driver.profile.lower,self.driver.profile.upper)
            if generation!=self.generation:return False
            if plan is None:
                self.driver.transport.release(self.driver.owner)
                self.owned=False
                self.phase,self.reason='fault','中心路径未通过，未发送运动'
                return False
            self.phase,self.reason='centering','正在到中心，J6到0度'
            # Call immediately before the finite executor dispatch. This
            # records a pre-dispatch barrier, not an acknowledgement of arrival.
            if not self.startup.mark_dispatched(plan['token'],self._actual()):
                raise RuntimeError('准备期间实际上下文已失效')
            accepted=await asyncio.wait_for(self.finite.start(plan['q'],owner=self.driver.owner),
                                            timeout=min(1.,center_timeout_s))
            if generation!=self.generation:return False
            if not isinstance(accepted,dict) or accepted.get('accepted') is not True:
                if isinstance(accepted,dict) and accepted.get('outcome')=='not_sent':
                    self.driver.transport.release(self.driver.owner)
                    self.owned=False
                    self.phase,self.reason='fault','中心指令未发送'
                    return False
                raise RuntimeError('中心指令执行结果未知')
            until=self.clock()+center_timeout_s
            while generation==self.generation and self.clock()<until:
                if getattr(self.finite,'state',None)=='fault':
                    raise RuntimeError(getattr(self.finite,'reason','中心执行失败'))
                if self.startup.observe_arrival(plan['token'],self._actual()):
                    self.phase,self.reason='waiting_hand','中心已到位，J6为0度，等待手掌'
                    return True
                if self.startup.phase=='blocked':raise RuntimeError(self.startup.reason)
                await self.sleep(.01)
            if generation==self.generation:raise TimeoutError('中心到位超时')
            return False
        except asyncio.CancelledError:
            await self.stop('启动被取消')
            raise
        except Exception as exc:
            await self.stop(str(exc) or type(exc).__name__)
            return False

    def submit(self, **sample):
        if self.phase not in ('waiting_hand','following'):return False
        return self.pipeline.submit(**sample)

    async def pump(self):
        """One latest-only IK job; driver's independent loop owns send cadence."""
        if self.phase not in ('waiting_hand','following'):return False
        if self.run_task and self.run_task.done():
            await self.stop('在线控制已停止，需重新启动')
            return False
        generation=self.generation
        try:
            feedback=self._actual()
            # The bridge validates inputs/context; the actual driver validates
            # feedback velocity/freshness independently before any packet.
            if not self.startup._fresh(feedback):
                raise RuntimeError('实际反馈已过期或失去运动许可')
            self.mailbox.latest=None
            if not await self.pipeline.publish_latest(feedback['q']):return False
            if generation!=self.generation or not self.pipeline._same():return False
            prepared=self.mailbox.latest
            if prepared is None:return False
            q,metadata=prepared
            if self.phase=='waiting_hand':
                finite_task=getattr(self.finite,'task',None)
                if finite_task is not None and (not finite_task.done()
                        or getattr(self.finite,'state',None)!='settled'):
                    raise RuntimeError('中心执行任务尚未退出，不能交接在线控制')
                driver_feedback=self.driver.read_feedback()
                if (driver_feedback.get('connection')!=feedback.get('connection')
                        or driver_feedback.get('context')!=feedback.get('context')):
                    raise RuntimeError('在线控制与中心运动的实际反馈绑定不一致')
                self.phase,self.reason='starting','正在进入在线控制'
                if not await self.driver.start():
                    if not self.driver._entered:
                        self.driver.transport.release(self.driver.owner)
                    self.owned=False  # driver start failure owns bounded cleanup
                    self.phase,self.reason='fault',self.driver.reason
                    return False
                if generation!=self.generation:return False
            if not self.pipeline._same() or not self.pipeline._ready():
                raise RuntimeError('启动期间工具、相机或工位已变化')
            self._actual()
            if not self.driver.publish(q,**metadata):
                raise RuntimeError('手掌目标在启动或求解期间已失效')
            if generation!=self.generation:return False
            self.phase,self.reason='following',('手掌位置跟随，J6保持0度' if self.pipeline.lock_j6 else '正立平移跟随')
            if self.run_task is None:
                self.run_task=asyncio.create_task(self.driver.run())
            return True
        except asyncio.CancelledError:
            await self.stop('跟随计算已取消')
            raise
        except Exception as exc:
            await self.stop(str(exc) or type(exc).__name__)
            return False

    def request_stop(self, reason='用户停止'):
        """Synchronous invalidation for the shared transport's stop entry."""
        self.generation+=1
        # Base Servo uses this generation after every bounded IO await. Revoke
        # its mailbox synchronously, not only when async stop() gets a turn.
        self.driver._generation+=1
        self.driver.target=None
        self.finite.invalidate(self.driver.owner)
        self.pipeline.invalidate(reason)
        self.startup.cancel(reason)
        self.mailbox.latest=None

    async def stop(self, reason='用户停止'):
        self.request_stop(reason)
        if self._stop_task is None:
            self._stop_task=asyncio.create_task(self._stop_body(reason))
        # Cancellation of a request must not abandon physical stop checking.
        cancelled=False
        while not self._stop_task.done():
            try:await asyncio.shield(self._stop_task)
            except asyncio.CancelledError:cancelled=True
        result=self._stop_task.result()
        if cancelled:raise asyncio.CancelledError
        return result

    async def _stop_body(self,reason):
        self.phase,self.reason='stopping',reason
        confirmed=False
        try:
            if self.driver.state not in ('idle','stopped','fault'):
                result=await self.driver.stop(reason)
                confirmed=result['stop_confirmed'] is True
                self.owned=False
            elif self.driver.state in ('stopped','fault'):
                confirmed=self.driver.stop_confirmed is True
                if self.owned and not self.driver._entered:
                    self.driver.transport.release(self.driver.owner)
                self.owned=False
            elif self.owned:
                # Do not route a stop to a replacement/reconnected robot.
                if self.read_feedback().get('connection')!=self.connection:
                    raise RuntimeError('连接已变化，禁止向新连接补发停止')
                ack=await asyncio.wait_for(self.finite.stop(owner=self.driver.owner),
                                           self.driver.profile.stop_timeout_s)
                if ack is not True:raise RuntimeError('停止回执未知')
                barrier=self.read_feedback()
                ack_at=self.clock()
                last_seq=barrier.get('frame_seq',-1)
                last_stamp=barrier.get('stamp',-math.inf)
                until=ack_at+self.driver.profile.stop_timeout_s
                while self.clock()<until:
                    feedback=self.read_feedback()
                    velocity=feedback.get('velocity')
                    if (feedback.get('connection')==self.connection
                            and self.startup._fresh(feedback, require_motion_permission=False)
                            and feedback['frame_seq']>last_seq
                            and feedback['stamp']>max(ack_at,last_stamp)
                            and feedback.get('idle') is True and vector(velocity,6)
                            and max(map(abs,velocity))<=self.driver.profile.start_velocity_deg_s):
                        confirmed=True
                        break
                    await self.sleep(.005)
            else:confirmed=True  # no command owned/dispatched by this host
        except Exception as exc:
            self.reason+='；'+str(exc)
        finally:
            if self.owned:
                if not confirmed:self.driver.transport.quarantine(self.driver.owner,self.reason)
                self.driver.transport.release(self.driver.owner)
                self.owned=False
        self.phase='stopped' if confirmed else 'fault'
        return dict(phase=self.phase,stop_confirmed=confirmed,reason=self.reason)
