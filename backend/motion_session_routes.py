"""Unified pause/resume/cancel ownership; no World construction or device discovery.

World integration: attach with create_motion_session_router(world), await
world.motion_session.checkpoint(epoch) before accepting/sending motion, and
include service.hold in real motion-wait pause/deadline accounting. Legacy
pause/resume endpoints must delegate here. Cancel requires world.forget_resume.
"""
from __future__ import annotations

import asyncio
import inspect
import math
import secrets
import time
from fastapi import APIRouter, Body


class MotionSession:
    def __init__(self, world, *, confirmation_timeout=2., command_timeout=3., poll_interval=.02):
        self.world=world
        self.confirmation_timeout=confirmation_timeout
        self.command_timeout=command_timeout
        self.poll_interval=poll_interval
        self.hold=False
        self.phase='idle'
        self.message=''
        self.terminal=False
        self._binding=None
        self._generation=0
        self._task=None
        self._action=None
        self._cancel_task=None
        self.token=None

    def _work(self):
        w=self.world
        return bool(w.executor.busy or w.runner.state in ('running','waiting','paused')
                    or w.sim.traj is not None or w.sim.jog is not None)

    def _context(self):
        play=getattr(self.world,'robot_play',None)
        if play and play.active:
            # RobotPlay separately guards expected held positions. Its context
            # intentionally excludes coordinates updated by the carrying tick.
            return ('robot_play',id(play.active),play.context(play.active['controlled']))
        return self.world.plan_context() if callable(getattr(self.world,'plan_context',None)) else None

    def _capture(self):
        w=self.world
        mode=w.settings.get('mode')
        if mode not in ('sim','real') or mode=='sim' and w.real is not None:
            raise RuntimeError('模式或连接状态无效')
        if not self._work():raise RuntimeError('没有可暂停的运动或程序')
        if getattr(getattr(w,'hand_follow',None),'active',False) or getattr(getattr(w,'real_jog_follow',None),'busy',False):
            raise RuntimeError('连续跟随需要先使用其专用停止入口')
        if w._stop_interlocked():raise RuntimeError('停止尚未确认，不能创建可恢复进度')
        if not getattr(w,'session_id',None):raise RuntimeError('运动会话标识缺失')
        link=w.real
        if mode=='real' and (link is None or not link.cmd_up or not link.state_valid()):
            raise RuntimeError('真机连接或状态失效，未确认暂停')
        return {'session':w.session_id,'mode':mode,'link':link,
                'connection':getattr(link,'epoch',None),'command_session':getattr(link,'command_session',None),
                'epoch':w.executor.epoch,'task':w.executor.task,'steps':w.runner.steps,
                'program':w.runner.state in ('running','waiting','paused'),
                'traj':w.sim.traj,'jog':w.sim.jog,
                'context':self._context()}

    def _valid(self):
        b=self._binding;w=self.world
        if not b:return False
        if (w.session_id!=b['session'] or w.settings.get('mode')!=b['mode'] or w.real is not b['link']
                or w.executor.epoch!=b['epoch'] or w.executor.task is not b['task']
                or w.runner.steps is not b['steps']):return False
        if b['context'] is not None and self._context()!=b['context']:return False
        if b['program']:
            if w.runner.state!='paused':return False
        elif not w.executor.busy and w.sim.traj is None and w.sim.jog is None:return False
        if b['mode']=='sim':
            if b['traj'] is not None and w.sim.traj is not b['traj']:return False
            if b['jog'] is not None and w.sim.jog is not b['jog']:return False
        else:
            link=b['link']
            if (link.epoch!=b['connection'] or getattr(link,'command_session',None)!=b['command_session']
                    or not link.cmd_up or not link.state_valid()):return False
        return True

    def state(self):
        work=self._work()
        try:current=asyncio.current_task()
        except RuntimeError:current=None
        pending=any(task is not None and task is not current and not task.done() for task in (self._task,self._cancel_task))
        valid=self._valid() if self._binding else False
        status=self.phase
        if status=='paused' and not valid:status='invalid'
        if not self._binding and not self.hold and work:status='running'
        return {'ok':True,'session_id':self.world.session_id,'token':self.token,'status':status,'state':status,
                'active':bool(work or self.hold),'paused':status=='paused',
                'pending':pending,'terminal':self.terminal,'why':self.message,
                'can_pause':bool(work and not self.hold and not pending),
                'can_resume':bool(status=='paused' and valid and not self.terminal and not pending),
                'can_cancel':bool(work or self.hold or self.world._stop_interlocked())}

    def _reply(self, ok, why=''):
        return {**self.state(),'ok':ok,'why':why or self.message}

    def _fault(self, generation, reason):
        if generation==self._generation:
            self.hold=True;self.phase='failed';self.terminal=True;self.message=reason
            if self._binding and self._binding['mode']=='real':self.world._stop_unknown=True
        return self._reply(False,reason)

    async def checkpoint(self, epoch=None):
        """World must call before each real segment; cancellation never unblocks it."""
        while self.hold:
            if epoch is not None:self.world.executor.check(epoch)
            if self.terminal:raise RuntimeError('运动会话已取消或失效，必须重新提交')
            if self._binding and not self._valid():raise RuntimeError('暂停运动的来源会话已失效')
            await asyncio.sleep(self.poll_interval)
        if epoch is not None:self.world.executor.check(epoch)

    async def _command(self, name, generation, paused):
        b=self._binding;link=b['link']
        if not self._valid():raise RuntimeError('暂停/继续绑定已失效，未发送控制命令')
        async with asyncio.timeout(self.command_timeout):
            reply=await link.ask(name,0)
        if generation!=self._generation:raise RuntimeError('操作已被取消或新事务撤销')
        if not self._valid():raise RuntimeError('控制回执对应的连接或运动已变化')
        if not reply.ok:raise RuntimeError('控制器拒绝'+('暂停' if paused else '继续'))
        # ACK alone is insufficient: require a later stream frame and timestamp.
        after,barrier=time.monotonic(),link.frame_seq
        end=after+self.confirmation_timeout
        while time.monotonic()<end:
            if generation!=self._generation:raise RuntimeError('操作已取消')
            if not self._valid():raise RuntimeError('等待确认时连接、状态或运动已失效')
            snap=link.snap;now=time.monotonic();stamp=snap.get('stamp')
            fresh=(type(stamp) in (int,float) and math.isfinite(stamp)
                   and after<stamp<=now and now-stamp<=.15 and link.frame_seq>barrier)
            confirmed=(snap.get('paused') in (1,True) and snap.get('moving') in (0,False)) if paused else (
                snap.get('paused') in (0,False) and snap.get('enabled') in (1,True)
                and snap.get('moving') in (0,1,False,True) and snap.get('err_code')==0)
            if fresh and confirmed:return
            await asyncio.sleep(self.poll_interval)
        raise RuntimeError(('暂停' if paused else '继续')+'尚无较新状态回读确认，请取消并检查控制器')

    async def _pause(self, generation):
        previous_paused=None
        try:
            if generation!=self._generation:return self._reply(False,'操作已取消')
            if self.world.settings.get('mode')=='sim' and self.world.real is None:
                previous_paused=self.world.sim.paused
                self.world.sim.paused=True
            self._binding=self._capture()
            self.token=secrets.token_hex(16)
            self.hold=True;self.phase='pausing';self.terminal=False;self.message='正在确认暂停'
            if self._binding['program']:self.world.runner.pause()
            if self._binding['mode']=='sim':self.world.sim.paused=True
            else:await self._command('GrpInterrupt',generation,True)
            if generation!=self._generation:return self._reply(False,'操作已取消')
            self.phase='paused';self.message='运动已暂停'
            return self._reply(True)
        except Exception as exc:
            if self._binding is None and previous_paused is not None:self.world.sim.paused=previous_paused
            return self._fault(generation,str(exc))

    async def _resume(self, generation):
        try:
            if self.terminal or self.phase!='paused' or not self._valid():
                return self._fault(generation,'暂停进度已失效或已取消，不能恢复；请取消后重新提交')
            w=self.world
            if w._stop_interlocked() or not w._need(w.MOTION,'统一继续运动'):
                return self._reply(False,getattr(w,'last_deny','') or '当前运动许可不足')
            if getattr(w,'safety',{}).get('level') in ('red','alarm'):
                return self._reply(False,'安全状态不允许继续运动')
            play=getattr(w,'robot_play',None)
            if play and play.active:play.guard()
            if self._binding['mode']=='real':
                pose_valid=getattr(w.real,'pose_valid',None)
                if callable(pose_valid) and not pose_valid():return self._fault(generation,'关节位姿回读已失效，不能恢复')
                self.phase='resuming';self.message='正在确认继续'
                await self._command('GrpContinue',generation,False)
            if generation!=self._generation:return self._reply(False,'操作已取消')
            if not self._valid():return self._fault(generation,'运动来源已变化，不能恢复')
            if self._binding['mode']=='sim':w.sim.paused=False
            if self._binding['program']:w.runner.resume()
            self.hold=False;self._binding=None;self.token=None;self.phase='running';self.message='继续原运动，未重新启动程序'
            return self._reply(True)
        except Exception as exc:return self._fault(generation,str(exc))

    @staticmethod
    def _observe(task):
        if not task.cancelled():task.exception()

    async def _operate(self, action):
        if self._cancel_task and not self._cancel_task.done():return self._reply(False,'正在取消运动')
        if self._task and not self._task.done():
            if self._action!=action:return self._reply(False,'另一个暂停/继续事务尚未完成')
            return await asyncio.shield(self._task)
        if action=='pause' and self.phase=='paused' and self._valid():return self._reply(True)
        if action=='pause' and self.hold:
            return self._reply(False,'旧暂停进度已失效或正在终结，请先取消，不能重新绑定旧运动')
        # Invalid/idle requests must not manufacture a held, resumable session.
        if action=='pause' and not self._work():return self._reply(False,'没有可暂停的运动或程序')
        if action=='resume' and not self._binding:return self._reply(False,'没有可恢复进度；取消终结后不能继续')
        self._generation+=1;generation=self._generation;self._action=action
        self._task=asyncio.create_task(self._pause(generation) if action=='pause' else self._resume(generation))
        self._task.add_done_callback(self._observe)
        return await asyncio.shield(self._task)

    async def pause(self):return await self._operate('pause')
    async def resume(self, token=None):
        if token is not None and (not isinstance(token,str) or token!=self.token):
            return self._reply(False,'暂停token已失效，未发送继续命令')
        return await self._operate('resume')

    async def _cancel(self):
        error='';result={}
        try:
            forgotten=self.world.forget_resume()
            if inspect.isawaitable(forgotten):await forgotten
        except Exception as exc:error='旧恢复进度撤销失败：'+str(exc)
        try:result=await self.world.stop_execution('统一取消运动：本次进度终结，禁止恢复')
        except Exception as exc:error=(error+'；' if error else '')+'停止失败：'+str(exc)
        # Clear live queue/interpreter even when physical stop is still unknown.
        self.world.runner.load([])
        self.world.runner.stop('已取消，原运动进度不可恢复')
        # stop/_persist_exec may have written a snapshot after the first forget.
        try:
            forgotten=self.world.forget_resume()
            if inspect.isawaitable(forgotten):await forgotten
        except Exception as exc:error=(error+'；' if error else '')+'取消后恢复进度清理失败：'+str(exc)
        ok=result.get('ok') is True and result.get('settled') is True and not error
        self.phase='cancelled' if ok else 'cancel_failed';self.hold=not ok
        self.message='已确认取消；原进度已终结' if ok else error or result.get('why') or '停止尚未确认，原进度已撤销'
        return {**self._reply(ok),'settled':result.get('settled') is True,'cancelled':ok}

    async def cancel(self):
        if not self._cancel_task or self._cancel_task.done():
            self._generation+=1;self._binding=None;self.token=None;self.hold=True
            self.terminal=True;self.phase='cancelling';self.message='正在取消并终结进度'
            self._cancel_task=asyncio.create_task(self._cancel())
            self._cancel_task.add_done_callback(self._observe)
        return await asyncio.shield(self._cancel_task)

    def abandon_powered_off(self):
        """Caller proves two fresh cold/idle frames; retire, never resume/send IO."""
        self._generation+=1;self._binding=None;self.token=None
        self.hold=False;self.terminal=True;self.phase='cancelled'
        self.message='已由下电静止回读撤销旧会话；旧进度终结，需重新提交'


def create_motion_session_router(world):
    service=MotionSession(world);world.motion_session=service
    router=APIRouter(prefix='/api/motion',tags=['motion-session'])
    @router.get('/state')
    async def state():return service.state()
    @router.post('/pause')
    async def pause(payload:dict=Body(default={})):
        if payload:return service._reply(False,'暂停请求不接受额外参数')
        return await service.pause()
    @router.post('/resume')
    async def resume(payload:dict=Body(default={})):
        if set(payload)-{'token'} or 'token' in payload and not isinstance(payload['token'],str):
            return service._reply(False,'继续请求只接受可选字符串token')
        return await service.resume(payload.get('token'))
    @router.post('/cancel')
    async def cancel(payload:dict=Body(default={})):
        if payload:return service._reply(False,'取消请求不接受额外参数')
        return await service.cancel()
    return router
