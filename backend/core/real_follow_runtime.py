"""World-owned real palm follow controller, sharing existing HTTP/stop/locks.

Status/configuration NEVER acquire a channel or send a command. Only an explicit
confirmed start with matching server commissioning and actual tool data creates
the host. No auto-connect, enable, TCP/load writes or hardware qualification.
"""
import asyncio
import copy
import math
import secrets
import time

from . import config, controls
from .kinematics import RobotModel, TCP_OFFSET_Z_MM
from .planning import digest
from .real_follow_tool_binding import build_real_follow_model, resolve_real_follow_tool
from .real_follow_qualification import load_record, resolve_record
from .real_follow_finite import FiniteCenterExecutor
from .real_follow_host import RealPalmFollowHost
from .real_palm_pipeline import vector
from .real_servo import RealServoSession
from .safety import evaluate, RED, ALARM


class RealPalmFollowController:
    def __init__(self,world):
        self.world=world
        self.token=secrets.token_hex(16)
        self.configured=False
        self.session=''
        self.seq=-1
        self.host=None
        self._task=self._stop_task=None
        self._stop_unknown=False
        self._stop_confirmed=None
        self.phase,self.why='idle','请配置工作区；启动先到中心，J6为0度'
        self._config=None
        self._bound=None
        self._model=None
        self._model_key=None

    @property
    def active(self):
        return self.phase in ('centering','waiting_hand','starting','following')

    @property
    def busy(self):
        return (self.active or self._stop_unknown
                or any(t is not None and not t.done() for t in (self._task,self._stop_task)))

    def _camera(self):
        w=self.world
        camera=w._hand_camera_reader() if callable(w._hand_camera_reader) else {}
        return w._hand_camera_identity(camera)

    def _tool(self, *, build=False):
        w=self.world
        mounted=w.tools.mounted
        tool=w.tools.get(mounted.get('tool_id'))
        # Physical binding revision is independent of UI/settings revisions;
        # limits/workcell are separately bound in record + live context below.
        kwargs=dict(config_revision=0,variant=w.model.variant)
        if build:
            result=build_real_follow_model(mounted,tool,**kwargs,model_factory=RobotModel,
                                           flange_offset_z_mm=TCP_OFFSET_Z_MM)
            if result['ok']:
                self._model=result['model'];self._model_key=result['context_fingerprint']
            return result
        return resolve_real_follow_tool(mounted,tool,**kwargs)

    def _qualification(self,tool):
        w=self.world
        link=w.real
        info=link.controller_info() if callable(getattr(link,'controller_info',None)) else {}
        reader=getattr(w,'_real_follow_qualification_reader',None)
        record=reader() if callable(reader) else load_record(config.DATA_DIR/'real_follow_qualification.json')
        lo,hi=w.limits()
        return resolve_record(record,version=info.get('version'),variant=w.model.variant,
                              tool_context=tool['context_fingerprint'],lower=lo,upper=hi)

    def _external_busy(self):
        w=self.world
        return bool(w.executor.busy or w.runner.state in ('running','waiting','paused')
            or w.queue_state in ('running','paused') or w.sim.traj or w.sim.jog
            or w.hand_follow.active or w.free.get('on')
            or any(t is not None and not t.done() for t in (w._ready_task,w._speed_task)))

    def _feedback(self):
        w=self.world;link=w.real
        s=dict(link.snap) if link else {}
        valid=bool(link and link.cmd_up and link.stream_up and link.state_valid())
        binding=(id(link),getattr(link,'command_session',None),id(getattr(link,'_writer',None)))
        return dict(connection=binding if valid else None,context=w.plan_context(),
            stamp=s.get('stamp'),frame_seq=getattr(link,'frame_seq',None),q=s.get('joints'),
            velocity=s.get('vel'),override=s.get('override'),
            enabled=bool(valid and s.get('enabled')==1),
            motion_allowed=bool(valid and w.settings.get('mode')=='real'
                and controls.motion_granted(s.get('fsm'),s.get('enabled'))
                and not self._external_busy() and s.get('free_drive')==0),
            safety_ok=bool(valid and s.get('err_code')==0 and s.get('paused')==0
                and s.get('free_drive')==0 and w.safety.get('level') in ('green','yellow')),
            idle=bool(valid and s.get('moving')==0 and s.get('inpos')==1))

    def _context(self):
        tool=self._tool()
        qualification=self._qualification(tool)
        camera=self._camera()
        if not tool['ok'] or not qualification['ok'] or camera is None:
            raise ValueError('相机、实际工具或控制器验证已失效')
        feedback=self._feedback()
        if feedback['connection'] is None:raise ValueError('原命令连接已失效')
        return dict(camera_session=camera[0],connection=feedback['connection'],tool_context=digest(
            [tool['context_fingerprint'],qualification['fingerprint'],self.world.plan_context(),
             self.world._cfg_rev,self.world._real_jog_generation]))

    def _readiness(self):
        w=self.world
        blockers=[]
        def block(code,detail):blockers.append(dict(layer='runtime',code=code,path='real_follow',detail=detail))
        if w.settings.get('mode')!='real' or w.real is None:
            block('not_connected','请先连接真机；系统不会自动连接或使能')
            return blockers
        if config.no_real():block('real_disabled','本进程禁止真机运动')
        tool=self._tool()
        labels={'identity':'实际工具','mount':'安装方向','tcp':'工具中心点','payload':'负载','geometry':'工具包络'}
        issues={'not_confirmed':'尚未确认','not_verified':'尚未验证','missing_record':'缺少验证记录',
            'stale_record':'验证记录与当前参数不一致','missing_capsules':'缺少碰撞包络',
            'profile_mismatch':'安装身份与档案不匹配','hardware_mismatch':'硬件身份不匹配',
            'invalid_payload':'质量或重心无效','invalid_tcp':'标定参数无效'}
        for original in tool['blockers']:
            item=dict(original)
            item['technical_detail']=item['detail']
            item['detail']=labels.get(item['layer'],'工具档案')+'：'+issues.get(item['code'],'参数或验证记录无效')
            blockers.append(item)
        blockers.extend(self._qualification(tool)['blockers'])
        f=self._feedback()
        if f['connection'] is None or not vector([f['stamp']],1) or not 0<=time.monotonic()-f['stamp']<.15:
            block('stale_feedback','真机反馈未就绪或已过期')
        if not vector(f.get('q'),6) or not vector(f.get('velocity'),6):
            block('missing_actual_joints','实际关节位置或速度反馈无效')
        if not f['enabled'] or not f['motion_allowed']:block('motion_not_ready','真机需手动就绪、使能，且无其他作业')
        if not f['safety_ok']:block('unsafe','真机安全状态不允许运动')
        if not vector([f['override']],1) or not 0<f['override']<=.05:
            block('actual_override','实际速度倍率须大于0且不高于5%')
        speed=w.settings.get('real_speed_pct')
        if not vector([speed],1) or not 0<speed<=5:block('requested_override','目标速度倍率须大于0且不高于5%')
        if self._camera() is None:block('camera_not_ready','请打开有效彩色画面')
        if (not w.settings.get('ground_enabled') or not w.settings.get('self_collision')
                or not w.settings.get('fence',{}).get('enabled')):
            block('safety_checks_disabled','真机跟随须开启围栏、地面和自碰撞检查')
        if not callable(getattr(w.real,'servo_transport',None)):
            block('transport_not_supported','当前连接未使用共享在线控制通道，请停止后重新建立连接')
        if self._stop_unknown or w._stop_interlocked():block('stop_unknown','停止尚未确认，请先核对现场')
        return blockers

    def snapshot(self):
        try:blockers=self._readiness()
        except Exception:
            blockers=[dict(layer='runtime',code='readiness_unavailable',path='real_follow',detail='真机跟随状态暂不可用')]
        host_phase=self.host.phase if self.host is not None and self.active else self.phase
        phase=host_phase if host_phase!='idle' else self.phase
        message=self.host.reason if self.host is not None and self.active else self.why
        cfg=self._config or {}
        bounds=cfg.get('enclosure')
        center=cfg.get('center');size=cfg.get('size')
        corners=[]
        direction=cfg.get('direction',0)
        if center and size:
            for x,z in [(-size[0]/2,size[2]/2),(size[0]/2,size[2]/2),
                        (size[0]/2,-size[2]/2),(-size[0]/2,-size[2]/2)]:
                y=-size[1]/2
                for _ in range(direction//90):x,y=-y,x
                corners.append([center[0]+x,center[1]+y,center[2]+z])
        raw=getattr(self.world.real,'snap',{})
        q=raw.get('joints') if isinstance(raw,dict) else None
        try:actual=self._model.pose_vec(q) if self._model is not None and vector(q,6) else None
        except Exception:actual=None
        return dict(configured=self.configured,active=self.active,busy=self.busy,
            waiting=phase in ('centering','waiting_hand','starting'),returning=False,
            phase=phase,stage=phase,status=message,why=message if self._stop_unknown else
                (blockers[0]['detail'] if blockers else message),blocked_reason=self.why if self._stop_unknown else '',
            blockers=blockers,available=not blockers,token=self.token,session=self.session,seq=self.seq,
            driver='StartServo/PushServoJ',strategy='online_position_j6_zero',
            control_policy='real-palm-position-j6-zero-v1',j6_deg=0.,
            hardware_qualified=not blockers,
            depth=cfg.get('depth',True),rotation=False,depth_mode='gesture',aspect=cfg.get('aspect',1.),
            direction=direction,bounds=bounds,outer_bounds=bounds,world_bounds=cfg.get('world_bounds'),
            corners=corners,outer_corners=[],base_offset=[0.,0.,0.],q=q,actual=actual,target=None,
            stop_confirmed=self._stop_confirmed,automatic_restart=False)

    def _reply(self,ok,why=''):
        return dict(ok=ok,why=why,state=self.snapshot())

    def configure(self,payload):
        if self.busy:return self._reply(False,'请先停止跟随并确认停稳')
        self.configured=False
        try:
            box=payload.get('enclosure')
            if not isinstance(box,dict) or not vector(box.get('min'),3) or not vector(box.get('max'),3):
                raise ValueError('请设置有效工作区')
            center=[(a+b)/2 for a,b in zip(box['min'],box['max'])]
            size=[b-a for a,b in zip(box['min'],box['max'])]
            direction=payload.get('direction',0)
            if (type(direction) is not int or direction not in (0,90,180,270)
                    or any(not 0<n<=5000 for n in size) or payload.get('rotation') is not False
                    or type(payload.get('depth')) is not bool or payload.get('depth_mode')!='gesture'):
                raise ValueError('跟随范围或方式无效；真机J6固定0度')
            camera=self._camera()
            if camera is None or payload.get('session')!=camera[0]:raise ValueError('彩色相机会话已变化')
            aspect=payload.get('aspect')
            if not vector([aspect],1) or not .5<=aspect<=3 or abs(aspect-camera[1]/camera[2])>.01:
                raise ValueError('画面比例无效或已变化')
            span=[size[1],size[0],size[2]] if direction in (90,270) else size
            lo=[v-s/2 for v,s in zip(center,span)];hi=[v+s/2 for v,s in zip(center,span)]
            fence=self.world.settings['fence']
            if (lo[2]<0 or not fence.get('enabled') or any(lo[i]<fence[a][0] or hi[i]>fence[a][1]
                    for i,a in enumerate(('x','y','z')))):
                raise ValueError('工作区须位于围栏内且高于地面')
            self._config=dict(center=center,size=size,direction=direction,enclosure=copy.deepcopy(box),
                world_bounds=dict(min=lo,max=hi),depth=payload['depth'],aspect=aspect)
            self.session=camera[0];self.seq=-1;self.token=secrets.token_hex(16)
            self.configured=True;self.phase='idle';self.host=None
            self.why='工作区已配置；启动先到中心，J6为0度'
            return self._reply(True)
        except (ValueError,TypeError,KeyError) as exc:return self._reply(False,str(exc))

    async def start(self,payload):
        if (not self.configured or self.busy or payload.get('token')!=self.token
                or payload.get('session')!=self.session or payload.get('confirm_real') is not True):
            return self._reply(False,'请明确确认启动，或重新配置工作区')
        blockers=self._readiness()
        if blockers:return self._reply(False,blockers[0]['detail'])
        try:
            f=self._feedback()
            if not f['idle']:return self._reply(False,'请先等待真机停稳')
            tool=self._tool(build=True)
            if not tool['ok']:return self._reply(False,tool['blockers'][0]['detail'])
            qualification=self._qualification(tool)
            if not qualification['ok']:return self._reply(False,qualification['blockers'][0]['detail'])
            profile=qualification['profile']
            self._bound=self._context()
            transport=self.world.real.servo_transport()
            finite=FiniteCenterExecutor(transport,self._feedback,self._validate_path,profile)
            driver=RealServoSession(transport,self._feedback,self._validate_path,profile)
            self.host=RealPalmFollowHost(self._model,driver,finite,self._feedback,self._context,self._validate_path)
            self.token=secrets.token_hex(16);self.seq=-1
            self._stop_confirmed=None;self._stop_unknown=False
            self._stop_task=None;self.phase='centering';self.why='正在到中心，J6到0度'
            self._task=asyncio.create_task(self._run())
            self._task.add_done_callback(lambda t:None if t.cancelled() else t.exception())
            return self._reply(True)
        except Exception as exc:return self._reply(False,str(exc) or type(exc).__name__)

    def _validate_path(self,start,end):
        if not vector(start,6) or not vector(end,6) or self._context()!=self._bound:return False
        w=self.world
        model=copy.copy(self._model)
        model._fk_key=model._fk_value=None
        settings=copy.deepcopy(w.settings);objects=copy.deepcopy(w.objects.list())
        lo,hi=w.limits()
        count=max(1,math.ceil(max(abs(a-b) for a,b in zip(start,end))/.25))
        if count>64:return False
        for i in range(count+1):
            q=[a+(b-a)*i/count for a,b in zip(start,end)]
            if any(not a<=v<=b for v,a,b in zip(q,lo,hi)):return False
            caps=model.capsules(q,conservative=True)
            hits=w._object_hits([c for c in caps if c['link']!='elfin_base'],objects)
            risk=evaluate(model,q,settings,lo,hi,caps=caps,obj_hits=hits)
            if risk['level'] in (RED,ALARM):return False
            if self.host is not None and self.host.phase in ('waiting_hand','starting','following'):
                box=self._config['world_bounds'];p=model.pose_vec(q)
                if any(not a<=v<=b for v,a,b in zip(p[:3],box['min'],box['max'])):return False
        return self._context()==self._bound

    async def _run(self):
        try:
            if not await self.host.begin(**{k:self._config[k] for k in ('center','size','direction')}):
                self.phase=self.host.phase;self.why=self.host.reason
                self._stop_unknown=bool(self.host.driver.transport.quarantined
                    or (self.host.driver._entered and not self.host.driver.stop_confirmed))
                return
            while self.host.phase in ('waiting_hand','starting','following'):
                if self._context()!=self._bound:raise ValueError('连接、工具、相机或工作区已变化')
                await self.host.pump()
                self.phase=self.host.phase;self.why=self.host.reason
                await asyncio.sleep(.005)
            self.phase=self.host.phase;self.why=self.host.reason
            if self.host.driver._entered and not self.host.driver.stop_confirmed:self._stop_unknown=True
        except asyncio.CancelledError:
            await self.stop('跟随任务已取消')
            raise
        except Exception as exc:
            # A shared stop already owns cleanup; do not wait on the task
            # which is itself waiting for this worker to retire.
            if self._stop_task is not None and not self._stop_task.done():return
            await self.stop(str(exc) or type(exc).__name__)

    def sample(self,payload,*,captured_at=None):
        if not self.active or payload.get('token')!=self.token or payload.get('session')!=self.session:
            return self._reply(False,'跟随令牌已失效')
        seq=payload.get('seq')
        if type(seq) is not int or not self.seq<seq<=2**53-1:return self._reply(False,'手掌样本顺序无效')
        self.seq=seq
        if payload.get('hand_present',True) is False:
            if self.host and self.host.phase in ('starting','following'):
                self.request_stop('手掌丢失，已停止；请手动重新开始')
            return self._reply(True)
        if self.host is None or self.host.phase=='idle' or self.host.phase=='centering':
            return self._reply(True)  # no camera target can interrupt startup
        frame_seq=payload.get('_verified_frame_seq')
        opening=payload.get('hand_open') if self._config['depth'] else .5
        accepted=self.host.submit(token=self.host.pipeline.token,session=self.session,seq=seq,
            frame_seq=frame_seq,captured_at=captured_at,u=payload.get('u'),v=payload.get('v'),opening=opening)
        return self._reply(accepted,'' if accepted else '手掌样本无效、过期或尚未到位')

    def request_stop(self,reason='用户停止'):
        if self.host is None or not self.busy:return None
        self.phase='stopping';self.why=reason
        self.host.request_stop(reason)
        if self._stop_task is None or self._stop_task.done():
            self._stop_task=asyncio.create_task(self.stop(reason))
            self._stop_task.add_done_callback(lambda t:None if t.cancelled() else t.exception())
        return self._stop_task

    async def stop(self,reason='用户停止',token=None):
        if token is not None and token!=self.token:return self._reply(False,'停止令牌已失效')
        current=asyncio.current_task()
        if self._stop_task is not None and self._stop_task is not current and not self._stop_task.done():
            return await asyncio.shield(self._stop_task)
        if self.host is None:
            self._stop_confirmed=True
            return self._reply(True)
        result=await self.host.stop(reason)
        self.phase=result['phase'];self.why=result['reason']
        self._stop_confirmed=result['stop_confirmed'] is True
        self._stop_unknown=not self._stop_confirmed
        self.world._real_jog_stop_confirmed=self._stop_confirmed
        self.world._real_jog_stop_reply=result
        if self._task is not None and self._task is not current and not self._task.done():
            await asyncio.wait({self._task},timeout=.2)
        self.token=secrets.token_hex(16)
        return self._reply(self._stop_confirmed,self.why)
