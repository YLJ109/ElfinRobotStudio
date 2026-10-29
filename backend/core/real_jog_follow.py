"""Low-speed, finite MoveRelJ compatibility follower; never a Servo emulator.

Only one <=0.25 degree command may be in flight. New targets overwrite the
mailbox; a fresh, same-connection settled frame is required before another
step. No socket/enable/connect calls exist here: World injects the existing
validated transport and actual geometry/feedback adapters.
"""
from __future__ import annotations

import asyncio
import math
import secrets
import time

MAX_STEP_DEG = .25
MAX_OVERRIDE = .05
FEEDBACK_AGE_S = .15
TARGET_AGE_S = .3


def _number(value):
    return type(value) in (int,float) and math.isfinite(value)


def _vector(value,length):
    return isinstance(value,(list,tuple)) and len(value)==length and all(map(_number,value))


class RealJogFollow:
    def __init__(self, read_state, solve, check_step, move, stop_motion, *,
                 clock=time.monotonic, sleep=asyncio.sleep, pose_for_joints=None):
        self.read_state,self.solve,self.check_step=read_state,solve,check_step
        self.move,self.stop_motion=move,stop_motion
        self.clock,self.sleep=clock,sleep
        self.pose_for_joints=pose_for_joints
        self.token=secrets.token_hex(16)
        self.configured=self.active=False
        self.session=''
        self.direction=0
        self.depth=True
        self.aspect=1.
        self.box=None
        self._center=self._size=None
        self._connection=self._context=None
        self._q=[0.]*6
        self.actual=self.target=[0.]*6
        self._orientation=[0.]*3
        self.seq=-1
        self._captured=None
        self._goal=None
        self._had_palm=False
        self._generation=0
        self._run_task=self._watch_task=self._stop_task=None
        self._orphan_tasks=[]
        self._stop_unknown=False
        self._stop_confirmed=None
        self._last_feedback=None
        self.waiting=True
        self.status='真机低速点动，尚未配置'
        self.why=self.blocked_reason=''

    @property
    def busy(self):
        return (self.active or self._stop_unknown or any(not t.done() for t in self._orphan_tasks)
                or (self._stop_task is not None and not self._stop_task.done()))

    def _read(self):
        data=self.read_state()
        if not isinstance(data,dict) or data.get('mode')!='real' or data.get('connection') is None:
            raise ValueError('请先连接真机')
        if not _number(data.get('stamp')) or not 0 <= self.clock()-data['stamp'] <= FEEDBACK_AGE_S:
            raise ValueError('真机反馈过期')
        if not _vector(data.get('q'),6) or not _vector(data.get('pose'),6) or not data.get('context'):
            raise ValueError('真机位置或工具上下文未知')
        seq=data.get('frame_seq')
        if type(seq) is not int or seq<0:
            raise ValueError('真机反馈序号无效')
        previous=self._last_feedback
        if previous and previous[0]==data['connection'] and (
                seq<previous[1] or data['stamp']<previous[2] or
                (seq==previous[1] and (data['stamp']!=previous[2] or tuple(data['q'])!=previous[3]))):
            raise ValueError('反馈倒序或旧帧被重复续期')
        self._last_feedback=(data['connection'],seq,data['stamp'],tuple(data['q']))
        self._q=list(data['q']);self.actual=list(data['pose'])
        return data

    def _guard(self,data,*,starting=False):
        if data.get('enabled') is not True or data.get('motion_allowed') is not True:
            raise ValueError('真机需处于就绪、使能状态；系统不会自动使能')
        if data.get('safety_ok') is not True:
            raise ValueError('真机安全状态不允许运动')
        if data.get('confirmed_tool') is not True:
            raise ValueError('兼容跟随暂需确认无工具；未知或未建模工具不允许执行')
        if not _number(data.get('override')) or not 0 < data['override'] <= MAX_OVERRIDE+1e-9:
            raise ValueError('真机低速兼容跟随需实际速度倍率不高于5%')
        if data.get('busy_external') is not False:
            raise ValueError('其他运动或作业尚未结束')
        if data.get('camera_ok') is not True or data.get('camera_session')!=self.session:
            raise ValueError('相机画面已失效或变化')
        if data['connection']!=self._connection or data['context']!=self._context:
            raise ValueError('真机连接、工位或工具已变化')
        if starting and data.get('idle') is not True:
            raise ValueError('请先等待真机停稳')
        if self.box and not self._inside(data['pose']):
            raise ValueError('实际末端不在跟随框内；请先通过点位移入框内')

    def _inside(self,pose):
        return bool(self.box and all(lo <= v <= hi for v,lo,hi in zip(pose[:3],self.box['min'],self.box['max'])))

    def _rotate(self,x,y,z):
        turns=self.direction//90
        for _ in range(turns):x,y=-y,x
        return [self._center[0]+x,self._center[1]+y,self._center[2]+z]

    def _reply(self,ok,why=''):
        return {'ok':ok,'why':why,'state':self.snapshot()}

    def snapshot(self):
        issue=''
        try:
            data=self._read()
            if self.configured:self._guard(data,starting=not self.active)
            elif data.get('motion_allowed') is not True:issue='请连接真机并手动就绪'
        except Exception as exc:issue=str(exc)
        corners=[]
        display_box=self.box
        if self._size:
            x,y,z=[v/2 for v in self._size]
            corners=[self._rotate(a,-y,b) for a,b in [(-x,z),(x,z),(x,-z),(-x,-z)]]
            # The scene rotates box geometry once around its world center.
            # Its dimensions must stay local; world_bounds is the safety AABB.
            display_box={'min':[v-h/2 for v,h in zip(self._center,self._size)],
                         'max':[v+h/2 for v,h in zip(self._center,self._size)]}
        return {'configured':self.configured,'active':self.active,'waiting':self.waiting,'returning':False,
                'token':self.token,'session':self.session,'seq':self.seq,'direction':self.direction,
                'depth':self.depth,'rotation':False,'depth_mode':'gesture','aspect':self.aspect,
                'q':list(self._q),'actual':list(self.actual),'target':list(self.target),
                'base_offset':[0.,0.,0.],'bounds':display_box,'world_bounds':self.box,'outer_bounds':display_box,
                'corners':corners,'outer_corners':[],
                'status':self.status,'why':self.why or issue,'blocked_reason':self.blocked_reason,
                'available':not bool(issue) and not self._stop_unknown,
                'strategy':'finite_relj_compat','max_step_deg':MAX_STEP_DEG,
                'stop_confirmed':self._stop_confirmed,
                'hardware_qualified':False,'automatic_restart':False}

    def configure(self,payload):
        if self.busy:return self._reply(False,'请先停止当前跟随并确认停稳')
        if not isinstance(payload,dict):return self._reply(False,'配置无效')
        try:
            data=self._read()
            enclosure=payload.get('enclosure')
            if (not isinstance(enclosure,dict) or not _vector(enclosure.get('min'),3)
                    or not _vector(enclosure.get('max'),3)):
                raise ValueError('真机兼容跟随需显式设置范围')
            size=[b-a for a,b in zip(enclosure['min'],enclosure['max'])]
            if not all(0<v<=5000 for v in size):raise ValueError('跟随框尺寸无效')
            direction=payload.get('direction',0)
            if type(direction) is not int or direction not in (0,90,180,270):raise ValueError('跟随朝向无效')
            if payload.get('rotation') is not False:raise ValueError('真机点动兼容版保持启动时朝向，不支持工具旋转')
            if type(payload.get('depth')) is not bool or payload.get('depth_mode')!='gesture':raise ValueError('跟随方式无效')
            if not isinstance(payload.get('session'),str) or not 1<=len(payload['session'])<=128:raise ValueError('相机会话无效')
            if not _number(payload.get('aspect')) or not .5<=payload['aspect']<=3:raise ValueError('画面比例无效')
            if data.get('camera_session')!=payload['session'] or data.get('camera_ok') is not True:raise ValueError('请打开有效彩色画面')
            if _number(data.get('camera_aspect')) and abs(data['camera_aspect']-payload['aspect'])>.01:raise ValueError('画面比例已变化')
            center=[(a+b)/2 for a,b in zip(enclosure['min'],enclosure['max'])]
            span=[size[1],size[0],size[2]] if direction in (90,270) else size
            box={'min':[v-h/2 for v,h in zip(center,span)],'max':[v+h/2 for v,h in zip(center,span)]}
            if box['min'][2]<0:raise ValueError('跟随框不能低于Z=0地面')
            self.session=payload['session'];self.aspect=payload['aspect']
            self.depth=payload['depth'];self.direction=direction
            self._center,self._size,self.box=center,size,box
            self._connection,self._context=data['connection'],data['context']
            self._orientation=list(data['pose'][3:]);self.target=list(data['pose'])
            self.configured=True;self.seq=-1
            self.token=secrets.token_hex(16)
            self.status,self.why,self.blocked_reason='工作区已配置；启动不自动居中','',''
            return self._reply(True)
        except Exception as exc:
            self.configured=False
            return self._reply(False,str(exc))

    async def start(self,payload):
        if not isinstance(payload,dict) or payload.get('token')!=self.token or payload.get('session')!=self.session:
            return self._reply(False,'跟随配置已失效')
        if payload.get('confirm_real') is not True:return self._reply(False,'请明确确认启动真机低速点动')
        if not self.configured or self.busy:return self._reply(False,'工作区未就绪或停止未确认')
        try:
            data=self._read();self._guard(data,starting=True)
            self._orientation=list(data['pose'][3:])
            self._goal=self._captured=None;self._had_palm=False
            self.seq=-1;self._generation+=1
            self.token=secrets.token_hex(16)
            self.active=True;self.waiting=True
            self.status,self.why,self.blocked_reason='等待手掌；未自动居中','',''
            self._run_task=asyncio.create_task(self._run(self._generation))
            self._watch_task=asyncio.create_task(self._watchdog(self._generation))
            return self._reply(True)
        except Exception as exc:return self._reply(False,str(exc))

    def sample(self,payload,*,captured_at=None):
        if not isinstance(payload,dict) or not self.active or payload.get('token')!=self.token or payload.get('session')!=self.session:
            return self._reply(False,'跟随未启动或令牌已失效')
        try:
            self._guard(self._read())
            seq,age=payload.get('seq'),payload.get('age_ms')
            if type(seq) is not int or not self.seq<seq<=2**53-1 or not _number(age) or not 0<=age<=TARGET_AGE_S*1000:
                raise ValueError('样本过期或顺序无效')
            present=payload.get('hand_present',True)
            if type(present) is not bool:raise ValueError('手掌状态无效')
            self.seq=seq
            if not present:
                if self._had_palm:self.request_stop('手掌丢失，已停止；请手动重新开始')
                return self._reply(True)
            u,v,opening=payload.get('u'),payload.get('v'),payload.get('hand_open',.5)
            if not all(_number(n) and 0<=n<=1 for n in (u,v,opening)):
                raise ValueError('掌心坐标或张合程度无效')
            if self.depth and 'hand_open' not in payload:raise ValueError('缺少手掌张合数据')
            now = self.clock()
            captured = now-age/1000 if captured_at is None else captured_at
            if captured_at is not None and (not _number(captured) or not 0 <= now-captured <= TARGET_AGE_S):
                raise ValueError('原始采集帧已过期')
            if self._captured is not None and captured<self._captured:raise ValueError('旧样本不能续期')
            xyz=self._rotate((u-.5)*self._size[0],(.5-opening)*self._size[1] if self.depth else 0.,(.5-v)*self._size[2])
            # Keep a numerical inset without moving the visible box or axes.
            xyz=[max(lo+min(.5,(hi-lo)/4),min(hi-min(.5,(hi-lo)/4),p))
                 for p,lo,hi in zip(xyz,self.box['min'],self.box['max'])]
            target=xyz+self._orientation
            self.target=list(target);self._goal=(list(target),captured)
            self._captured=captured;self._had_palm=True;self.waiting=False
            self.status,self.blocked_reason='低速点动跟随中',''
            return self._reply(True)
        except Exception as exc:
            self.request_stop(str(exc))
            return self._reply(False,str(exc))

    async def _settled(self,expected,after_seq,after_stamp,generation):
        # Each finite move completes or faults. Never queue/retry it blindly.
        end=self.clock()+2.
        while self.active and generation==self._generation:
            data=self._read();self._guard(data)
            if self._goal is None or self.clock()-self._goal[1]>TARGET_AGE_S:
                raise ValueError('手掌目标过期，停止点动')
            if (data['frame_seq']>after_seq and data['stamp']>after_stamp and data.get('idle') is True
                    and max(abs(a-b) for a,b in zip(data['q'],expected))<=.05):return
            if max(abs(a-b) for a,b in zip(data['q'],expected))>1.:
                raise ValueError('实际点动偏差异常')
            if self.clock()>=end:raise ValueError('点动到位超时；没有发送后续小步')
            await self.sleep(.01)
        raise asyncio.CancelledError

    async def _watchdog(self,generation):
        # Independent of IK, geometry checks, ACK and arrival awaits. Motion
        # freshness cannot be postponed by a slow transaction callback.
        try:
            while self.active and generation==self._generation:
                self._guard(self._read())
                if self._goal is not None and self.clock()-self._goal[1]>TARGET_AGE_S:
                    raise ValueError('手掌目标过期，已停止；请重新开始')
                await self.sleep(.01)
        except asyncio.CancelledError:
            if self.active and generation==self._generation:self.request_stop('点动监测任务已取消')
            raise
        except Exception as exc:self.request_stop(str(exc))

    async def _run(self,generation):
        try:
            while self.active and generation==self._generation:
                data=self._read();self._guard(data)
                if self._goal is None:
                    await self.sleep(.02);continue
                goal,captured=self._goal[0][:],self._goal[1]
                if self.clock()-captured>TARGET_AGE_S:raise ValueError('手掌目标过期，已停止；请重新开始')
                if data.get('idle') is not True:raise ValueError('存在非本次跟随的运动')
                start=list(data['q'])
                result=await asyncio.wait_for(self.solve(start,goal),.15)
                if not self.active or generation!=self._generation:break
                if not isinstance(result,dict) or result.get('ok') is not True or not _vector(result.get('q'),6):
                    self.blocked_reason='目标暂不可达，保持当前位置'
                    await self.sleep(.05);continue
                now=self._read();self._guard(now)
                if self.clock()-captured>TARGET_AGE_S or max(abs(a-b) for a,b in zip(start,now['q']))>.01:
                    await self.sleep(.01);continue
                errors=sorted(enumerate(b-a for a,b in zip(start,result['q'])),key=lambda pair:-abs(pair[1]))
                chosen=None
                for axis,error in errors:
                    if abs(error)<=.05:continue
                    delta=max(-MAX_STEP_DEG,min(MAX_STEP_DEG,error))
                    expected=start[:];expected[axis]+=delta
                    safe=await asyncio.wait_for(self.check_step(start,expected,self.box),.15)
                    if not self.active or generation!=self._generation:break
                    if safe is True and self._relevant_step(goal,data['pose'],expected):
                        chosen=(axis,delta,expected);break
                if chosen is None:
                    self.blocked_reason='' if max(abs(e) for _,e in errors)<=.05 else '下一小步未通过路径检查'
                    await self.sleep(.05);continue
                final=self._read();self._guard(final)
                if (not self.active or generation!=self._generation or self.clock()-captured>TARGET_AGE_S
                        or max(abs(a-b) for a,b in zip(start,final['q']))>.01
                        or not self._relevant_step(goal,final['pose'],chosen[2])):
                    await self.sleep(.005);continue
                axis,delta,expected=chosen
                ok=await asyncio.wait_for(self.move(axis,delta,expected),.3)
                if ok is not True:raise ValueError('点动回执失败或未知，不重试旧指令')
                # A pre-ACK arrival cannot confirm this move: require a later capture.
                barrier=self._read();self._guard(barrier)
                await self._settled(expected,barrier['frame_seq'],self.clock(),generation)
                self.blocked_reason=''
                await self.sleep(.02)
        except asyncio.CancelledError:
            if self.active:self.request_stop('点动任务已取消')
            raise
        except Exception as exc:self.request_stop(str(exc) or '点动处理超时')

    def _relevant_step(self,solved_goal,current_pose,expected):
        if solved_goal==self.target:return True
        if not callable(self.pose_for_joints):return False
        # Do not starve while the camera keeps publishing small changes during
        # IK/path awaits. An older solve may supply ONE already-safe microstep
        # only if actual FK proves it gets closer to the newest target. A tiny
        # reversal must not inherit the previous direction merely by tolerance.
        candidate=self.pose_for_joints(expected)
        if not _vector(candidate,6):return False
        return math.dist(candidate[:3],self.target[:3])+1e-6 < math.dist(current_pose[:3],self.target[:3])

    def request_stop(self,reason='已停止真机跟随'):
        if self._stop_task is not None and not self._stop_task.done():return self._stop_task
        was_active=self.active
        self._generation+=1;self.active=False;self.configured=False
        self._goal=None;self.waiting=True
        self.status,self.why=reason,reason
        self.token=secrets.token_hex(16)
        if not was_active and not self._stop_unknown:return None
        self._stop_unknown=True
        self._stop_confirmed=None
        self._stop_task=asyncio.create_task(self._cleanup(reason))
        return self._stop_task

    async def _cleanup(self,reason):
        # Stop is launched before waiting for cancellation. An adapter that is
        # slow to acknowledge cancellation cannot indefinitely delay GrpStop.
        stopping=asyncio.create_task(self.stop_motion(reason))
        workers=[t for t in (self._run_task,self._watch_task,*self._orphan_tasks)
                 if t is not None and t is not asyncio.current_task() and not t.done()]
        for task in workers:task.cancel()
        try:
            done,_=await asyncio.wait({stopping},timeout=2.)
            confirmed=bool(done and not stopping.cancelled() and stopping.result() is True)
            # A callback that outlives the stop ACK could still produce a late
            # transport side effect. Its eventual exit is not a second stop.
            late_worker=any(not task.done() for task in workers)
            if not done:
                stopping.cancel();workers.append(stopping)
            if workers:
                _,pending=await asyncio.wait(set(workers),timeout=.1)
            else:pending=set()
            self._orphan_tasks=[t for t in pending if not t.done()]
            self._stop_unknown=not confirmed or late_worker or bool(self._orphan_tasks)
        except (Exception,asyncio.CancelledError):
            self._stop_unknown=True
            stopping.cancel()
            self._orphan_tasks=[t for t in (*workers,stopping) if not t.done()]
        for task in self._orphan_tasks:
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        self._stop_confirmed=not self._stop_unknown
        if self._stop_unknown:self.status=self.why='停止尚未确认，请现场确认；未解锁其他运动'

    async def stop(self,reason='已停止真机跟随',token=None):
        if token is not None and token!=self.token:return self._reply(False,'旧跟随令牌已失效')
        task=self.request_stop(reason)
        if task is not None:await asyncio.shield(task)
        return self._reply(not self._stop_unknown,self.why)
