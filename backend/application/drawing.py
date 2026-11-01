"""Checked, finite drawing plans over the existing Runner and motion owner."""
import asyncio
import copy
import math
import json
import logging
import secrets
import threading
import time
from ..core.planning import digest, inspect_path, waypoints
from ..core.trajectory import JointTrajectory, PreparedMotion
from ..core.kinematics import interpolate_pose, pose_rotation_distance
from .drawing_geometry import configuration, poses, tcp_samples, line_distance, trace_metadata,bounds,check_bounds
from .drawing_tcp import build_tool,tool_key
from ..core.safety import evaluate

MAX_COMMANDS=256
PREFLIGHT_SECONDS=90.
PLAN_TTL=120.

class Drawing:
    def __init__(self,world,*,clock=time.monotonic):
        self.world=world;self.clock=clock;self.plan=None;self.phase='idle';self.why=''
        self.worker=None;self.cancel_event=None;self.watch=None;self.lease=None
        self.guard_callback=self._guard
        self._tool_cache=None

    def _tool(self):
        key=tool_key(self.world)
        if self._tool_cache is None or self._tool_cache[1]['binding_id']!=key:self._tool_cache=build_tool(self.world)
        return self._tool_cache

    def _trace_tcp(self):
        p=self.plan
        try:
            model,definition=self._tool();trace=copy.deepcopy(p['trace_tcp'] if p else definition)
            valid=self.world.joints_known() and (self.world.settings.get('mode')!='real' or bool(self.world.real and self.world.real.state_valid()))
            valid=bool(valid and trace['binding_id']==definition['binding_id'])
            trace['valid']=valid;trace['pose']=model.pose_vec(self.world.current_joints()) if valid else None
            return trace
        except (ValueError,RuntimeError) as exc:
            return {**copy.deepcopy(p['trace_tcp'] if p else {}),'valid':False,'pose':None,'why':str(exc)}

    def _context(self):
        w=self.world;link=w.real
        return digest([w.session_id,w.settings.get('mode'),w.plan_context(),w.executor.epoch,
                       getattr(link,'command_session',None),getattr(link,'epoch',None),
                       w.motion_model().variant,w.motion_model().tcp_translation_mm,w.motion_model().tcp_rpy_deg,tool_key(w)])

    def _fresh(self):
        w=self.world
        if not w.joints_known():raise ValueError('当前关节反馈未知，不能绘制')
        q=w.current_joints()
        if len(q)!=6 or any(type(v) not in (int,float) or not math.isfinite(v) for v in q):raise ValueError('当前关节反馈无效')
        if w.settings.get('mode')=='real':
            link=w.real
            if not link or not link.cmd_up or not link.state_valid():raise ValueError('真机反馈已过期或连接无效')
            if not link.controller_info().get('motion_qualified'):raise ValueError('当前控制器运动资格未核验')
            self._tool()  # Existing trusted physical-tool resolver, no flange fallback.
            if w.settings.get('real_move_cmd')!='relj':raise ValueError('当前真机路径策略未通过分段绘制检查')
        elif w.settings.get('mode')!='sim' or w.real is not None:raise ValueError('绘制模式或连接无效')
        return list(q)

    def _idle(self):
        w=self.world;p=getattr(w,'robot_play',None)
        if (w.executor.busy or w.runner.state in ('running','waiting','paused') or w.queue_state in ('running','paused')
                or w.sim.traj or w.sim.jog or w.hand_follow.active or w.real_jog_follow.busy or w.free['on']
                or (p and (p.busy or p.active or p.held)) or w.objects.any_active()):
            raise ValueError('请先停止当前任务、运动和跟随，确认静止后绘制')
        if w._stop_interlocked():raise ValueError('停止尚未确认，不能开始绘制')
        if w.settings.get('mode')=='real' and (not w.real or w.real.snap.get('moving')!=0 or w.real.snap.get('inpos')!=1 or w.real.snap.get('free_drive') not in (0,False)):
            raise ValueError('当前真机反馈未证明静止或仍在手拖')

    def state(self):
        p=self.plan;w=self.world
        if p and self.phase=='ready':
            try:valid=self.clock()<=p['expires'] and self._context()==p['context']
            except Exception:valid=False
            if not valid:self.phase='expired';self.why='绘制计划已过期或上下文变化，请重新预检'
        if self.lease and self.phase in ('running','paused'):
            r=w.runner
            if r.steps is not self.lease['steps'] or r.run_id!=self.lease['run_id']:
                self.phase='failed';self.why='绘制程序已被替换，不接管新任务'
            elif r.state=='finished':
                try:
                    q=self._fresh();actual=self._tool()[0].pose_vec(q)
                    finish=p['path'][-1] if p['config']['shape']=='text' else p['config']['center']
                    if math.dist(actual[:3],finish[:3])>2 or pose_rotation_distance(actual,finish)>.2:
                        raise ValueError('程序结束但当前TCP未确认回到中心')
                    self.phase='completed';self.why='书写完成，已抬笔' if p['config']['shape']=='text' else '绘制完成，已回到中心'
                except Exception as exc:self.phase='failed';self.why=str(exc)
            elif r.state in ('aborted','error'):self.phase='failed';self.why=r.error or '绘制已停止'
            elif r.state=='paused':self.phase='paused'
            else:self.phase='running'
        progress=(dict(p['progress']) if p else {'completed':0,'total':0})
        if self.lease and p and w.runner.steps is self.lease['steps']:
            progress={'completed':w.runner.completed_index,'total':len(w.runner.steps)}
        return {'ok':True,'plan_id':p['id'] if p else None,'phase':self.phase,'mode':p['mode'] if p else w.settings.get('mode'),
                'trace_paths':copy.deepcopy(p.get('trace_paths',[])) if p else [],
                'actual_samples':copy.deepcopy(p.get('actual_samples',[])) if p else [],
                'session_id':p['session'] if p else w.session_id,'config':copy.deepcopy(p['config']) if p else None,
                'path':copy.deepcopy(p['path']) if p else [],'trace_path':copy.deepcopy(p['trace_path']) if p else [],
                'trace_active':bool(self.phase=='running' and self.lease and p and w.runner.steps is self.lease['steps'] and w.runner.run_id==p['id'] and w.runner.active_index is not None
                                    and w.runner.state=='running' and w.runner.current().get('drawing_trace') is True),
                'trace_tcp':self._trace_tcp(),'bounds':copy.deepcopy(p['bounds']) if p else None,
                'program':p.get('program','') if p else '',
                'progress':progress,'expires_in_s':max(0,round(p['expires']-self.clock(),2)) if p and p.get('expires') else None,
                'can_run':self.phase=='ready','why':self.why,
                'path_contract':'有界关节分段；TCP路径按实际执行策略采样检查，不代表控制器连续直线或纸面笔尖精度'}

    @staticmethod
    def _checked_start(value):
        """Composed-task origin: the already checked teaching point, not current feedback."""
        if not isinstance(value,(list,tuple)) or len(value)!=6:raise ValueError('组合起点必须是 6 个关节值')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in value):raise ValueError('组合起点关节必须是有限数值')
        return [float(v) for v in value]

    def preview(self,payload,prefix_joints=None):
        if self.worker and not self.worker.done():raise ValueError('绘制预检尚未结束，请先取消并等待退出')
        if self.phase in ('running','paused'):raise ValueError('绘制正在执行，请先取消')
        # A visual refresh is not a new motion plan. Reuse only the one fully
        # checked, unexpired text plan, bound to the exact same configuration,
        # owner context and joint origin. Never extend its permission lifetime.
        previous=self.plan
        if previous and self.phase=='ready' and previous['config']['shape']=='text' and prefix_joints is None:
            self._idle();current=self._fresh()
            candidate=configuration(payload,self.world.settings['mode'])
            if (candidate==previous['config'] and self.clock()<=previous['expires']
                    and self._context()==previous['context']
                    and current==previous['start']):
                return self.state()
        self.plan=None;self.lease=None;self.phase='idle';self.why=''
        self._idle();origin=list(self._fresh());w=self.world;mode=w.settings['mode'];config=configuration(payload,mode)
        # A composed task binds its already checked teaching point as the
        # geometric origin (IK/approach start, and run()'s arrival target),
        # while `origin` above stays the live feedback that must not move
        # during checking. Ordinary drawing keeps both equal to live feedback.
        q=self._checked_start(prefix_joints) if prefix_joints is not None else list(origin)
        writing=config['shape']=='text'
        if writing and not getattr(w.motion_model(),'pen_tool',False):raise ValueError('请先切换书写笔，再预览文字')
        command_limit=2048 if writing else MAX_COMMANDS
        bound_model,trace_tcp=self._tool();model=copy.copy(bound_model)
        model._fk_key=None;model._fk_value=None
        center_actual=model.pose_vec(q)
        if mode=='real':
            if math.dist(center_actual[:3],config['center'][:3])>.5 or pose_rotation_distance(center_actual,config['center'])>.2:
                raise ValueError('真机中心必须来自当前有效TCP，不能从远处自动接近')
            ack=w.speed_ack
            if (w.settings['real_speed_pct']!=config['speed_pct'] or ack.get('ok') is not True
                    or abs(w.real.snap.get('override',-1)*100-config['speed_pct'])>.05):
                raise ValueError('请先通过现有倍率入口设置并确认所选低速倍率')
        key=secrets.token_hex(16);cancel=threading.Event();self.cancel_event=cancel
        p={'id':key,'config':config,'mode':mode,'session':w.session_id,'context':self._context(),'start':q,
           'path':poses(config),'progress':{'completed':0,'total':0},'expires':None,
           'trace_tcp':copy.deepcopy(trace_tcp),'bounds':bounds(config),'actual_samples':[]}
        for target in p['path']:check_bounds(target[:3],p['bounds'])
        p['trace_path'],trace_flags=trace_metadata(config,p['path'])
        stroke_ids=[None]*len(p['path'])
        if writing:
            from .text_writing import paths,sequence
            p['trace_paths']=paths(config);stroke_ids=sequence(config)[2];p['actual_samples']=[]
            if len(p['path'])>2048:raise ValueError('文字路径过长，请减少字符')
        self.plan=p;self.phase='checking'
        settings=copy.deepcopy(w.settings);settings['sim_speed_pct' if mode=='sim' else 'real_speed_pct']=config['speed_pct']
        lo,hi=w.limits();objects=w.objects.list();deadline=time.monotonic()+PREFLIGHT_SECONDS
        def check():
            if cancel.is_set():raise RuntimeError('绘制预检已取消')
            if time.monotonic()>=deadline:raise RuntimeError('绘制完整路径预检超时')
        def build():
            commands=[];q0=q[:];maximum=0.;samples=0
            p['progress']['total']=len(p['path'])
            center_candidate=None
            if mode=='sim':
                # A solution for the centre alone may drive the wrist against
                # a soft limit later in the outline. Probe the WHOLE unchanged
                # path on bounded elbow/wrist seeds before choosing the joint
                # approach; no candidate is an execution permission.
                probe_diagnostics=[]
                def probe_segment(start,target,budget,depth=0):
                    check()
                    if budget[0]>=MAX_COMMANDS or budget[1]>=budget[2]:return None
                    budget[0]+=1
                    result,emm,edeg,ok=model.ik(start,target,lo,hi,iters=140,max_restarts=2,deadline=deadline,cancelled=cancel.is_set)
                    check()
                    solved=ok and all(a<=v<=b for v,a,b in zip(result,lo,hi))
                    if solved:
                        curve=JointTrajectory.build([start,result],model.vmax,config['speed_pct'])
                        report=inspect_path(model,start,result,settings,lo,hi,objects,trajectory=curve,check=check)
                        if report['risk_detected']:return None
                        try:error,_=tcp_samples(model,curve.inspection_samples(),model.pose_vec(start),target,check,box=p['bounds'])
                        except ValueError:return None
                        if error<=2:return list(result)
                    else:
                        budget[1]+=1
                        if len(probe_diagnostics)<8:probe_diagnostics.append({'target':list(target),'position_error_mm':emm,'rotation_error_deg':edeg})
                    origin=model.pose_vec(start)
                    if depth>=7 or budget[0]>=MAX_COMMANDS-2:return None
                    if math.dist(origin[:3],target[:3])<=.01 and pose_rotation_distance(origin,target)<=.01:return None
                    middle=probe_segment(start,interpolate_pose(origin,target,.5),budget,depth+1)
                    return None if middle is None else probe_segment(middle,target,budget,depth+1)
                seeds=[q,[0,-45,90,0,45,0],[0,-70,-90,0,100,0],[0,45,-90,0,90,0]]
                centers=[config['center'][:]]
                if config.get('center_policy')=='range_lower':
                    nominal=config['center'];size=[b-a for a,b in zip(p['bounds']['min'],p['bounds']['max'])]
                    # Prefer the inner/lower part of the SAME requested box.
                    # These are candidate poses only; the complete unchanged
                    # outline and actual approach must still pass below.
                    inner=[v-math.copysign(min(abs(v),min(150.,s*.2)),v) if v else 0.
                           for v,s in zip(nominal[:2],size[:2])]
                    current=model.pose_vec(q)[:3]+nominal[3:]
                    lower=nominal[2]-min(100.,size[2]*.2)
                    middle=[(a+b)/2 for a,b in zip(inner,nominal[:2])]+[(lower+nominal[2])/2]+nominal[3:]
                    candidates=[middle,current,inner+[lower]+nominal[3:],nominal[:]]
                    centers=[]
                    for center in candidates:
                        if center not in centers:centers.append(center)
                for center in centers:
                    trial_config={**config,'center':center};trial_path=poses(trial_config)
                    try:
                        for pose in trial_path:check_bounds(pose[:3],p['bounds'])
                    except ValueError:continue
                    probe_budget=[0,0,8 if config.get('center_policy')=='range_lower' else MAX_COMMANDS]
                    for seed in seeds:
                        check()
                        candidate,_,_,ok=model.ik(seed,trial_path[0],lo,hi,iters=140,max_restarts=2,deadline=deadline,cancelled=cancel.is_set)
                        if not ok:continue
                        probe=list(candidate)
                        for target in ([] if writing else trial_path[1:]):
                            probe=probe_segment(probe,target,probe_budget)
                            if probe is None:break
                        if probe is None:continue
                        approach_curve=JointTrajectory.build([q,candidate],model.vmax,config['speed_pct'])
                        approach_report=inspect_path(model,q,candidate,settings,lo,hi,objects,trajectory=approach_curve,check=check)
                        if not approach_report['risk_detected']:
                            center_candidate=list(candidate);config['center']=center;p['path']=trial_path
                            p['trace_path'],trace_flags[:]=trace_metadata(config,trial_path)
                            break
                    if center_candidate is not None:break
                if center_candidate is None:
                    logging.getLogger(__name__).warning('Drawing path rejected; bounded IK/approach diagnostics: %s',probe_diagnostics)
                    raise ValueError('当前姿态无法覆盖整个图形，请移动范围或减小图形')
            def segment(start,target,depth=0,approach=False,trace=False):
                nonlocal maximum,samples
                check()
                if approach and center_candidate is not None:result,emm,edeg,ok=center_candidate[:],0.,0.,True
                else:result,emm,edeg,ok=model.ik(start,target,lo,hi,iters=140,max_restarts=2,deadline=deadline,cancelled=cancel.is_set)
                check()
                if not ok:
                    # Follow the same geometric segment with smaller, checked
                    # continuation steps; never accept an unconverged result.
                    # This shares the existing 90s/depth/command bounds.
                    if not approach and depth<7 and len(commands)<command_limit-2:
                        origin=model.pose_vec(start)
                        if math.dist(origin[:3],target[:3])>.01 or pose_rotation_distance(origin,target)>.01:
                            mid=interpolate_pose(origin,target,.5)
                            middle=segment(start,mid,depth+1,trace=trace)
                            return segment(middle,target,depth+1,trace=trace)
                    raise ValueError(f'绘制路径逆解未收敛：{emm:.2f}毫米/{edeg:.2f}度')
                if any(not a<=v<=b for v,a,b in zip(result,lo,hi)):raise ValueError('绘制逆解超出限位')
                if mode=='sim':
                    curve=JointTrajectory.build([start,result],model.vmax,config['speed_pct'])
                    route=curve.inspection_samples()
                    report=inspect_path(model,start,result,settings,lo,hi,objects,trajectory=curve,check=check)
                else:
                    route=waypoints(start,result,settings['real_move_cmd'])
                    report=inspect_path(model,start,result,settings,lo,hi,objects,check=check)
                if report['risk_detected']:raise ValueError('绘制完整路径碰撞或安全限位预检未通过')
                error,count=tcp_samples(model,route,model.pose_vec(start),target,check,box=None if approach else p['bounds'])
                if not approach and error>2:
                    if depth>=7 or len(commands)>=command_limit-2:raise ValueError('实际分段路径TCP偏离超过2毫米，无法在有界命令内完成')
                    mid=interpolate_pose(model.pose_vec(start),target,.5)
                    middle=segment(start,mid,depth+1,trace=trace)
                    return segment(middle,target,depth+1,trace=trace)
                if len(commands)>=command_limit:raise ValueError('绘制路径超过有界命令数量')
                maximum=max(maximum,error if not approach else 0);samples+=count
                commands.append({'kind':'movej','joints':list(result),'line':len(commands)+1,
                                 'drawing_line_start':model.pose_vec(start),'drawing_line_end':target[:],
                                 'drawing_plan_id':p['id'],'drawing_trace':trace,
                                 'drawing_stroke_id':stroke_ids[i] if writing else (0 if trace else None),
                                 'drawing_within_bounds':not approach,
                                 'src':'MOVEJ '+','.join(format(v,'.10g') for v in result)})
                return list(result)
            for i,target in enumerate(p['path']):
                q0=segment(q0,target,approach=mode=='sim' and i==0,trace=trace_flags[i])
                p['progress']['completed']=i+1
            if mode=='sim' and config['shape'] in ('text','heart','square','rectangle','circle'):
                original=commands;commands=[];prepared={};index=0
                p['export_program']='\n'.join(step['src'] for step in original)
                durations=[0.,0.]
                def append_continuous(group,start,depth=0):
                    nonlocal maximum
                    if len(group)<=1 or depth>=8:
                        commands.extend(group);return
                    curve=JointTrajectory.build([start]+[step['joints'] for step in group],model.vmax,config['speed_pct'])
                    if len(curve.points)!=len(group)+1:
                        commands.extend(group);return
                    deviation=0.;bad=[]
                    for knot,step in enumerate(group):
                        a,b=curve.points[knot:knot+2]
                        count=max(2,math.ceil(3*max(abs(x-y) for x,y in zip(a,b))/.1))
                        if count>2048:raise ValueError('连续绘制路径采样超出上限')
                        invalid=False;edge_error=0.
                        for j in range(count+1):
                            check();pose=model.pose_vec(curve.at_path_time(curve.times[knot]+(curve.times[knot+1]-curve.times[knot])*j/count))
                            try:check_bounds(pose[:3],p['bounds'])
                            except ValueError:invalid=True
                            edge_error=max(edge_error,line_distance(pose[:3],step['drawing_line_start'][:3],step['drawing_line_end'][:3]))
                        if invalid or edge_error>2:bad.append(knot)
                        deviation=max(deviation,edge_error)
                    if bad:
                        # Keep only over-deviation edges as their original
                        # checked steps; recheck continuous runs on either side.
                        begin=0
                        for knot in bad:
                            append_continuous(group[begin:knot],start if begin==0 else group[begin-1]['joints'],depth+1)
                            commands.append(group[knot]);begin=knot+1
                        append_continuous(group[begin:],start if begin==0 else group[begin-1]['joints'],depth+1)
                        return
                    report=inspect_path(model,start,group[-1]['joints'],settings,lo,hi,objects,trajectory=curve,check=check)
                    if report['risk_detected']:
                        commands.extend(group);return
                    command={**group[0],'joints':group[-1]['joints'],'drawing_line_end':group[-1]['drawing_line_end'],
                             'drawing_continuous':len(prepared),'src':'MOVEJ '+','.join(format(v,'.10g') for v in group[-1]['joints'])}
                    prepared[len(prepared)]=PreparedMotion(curve,p['context'],tuple(lo),tuple(hi),json.dumps(report))
                    durations[0]+=sum(JointTrajectory.build([start if knot==0 else group[knot-1]['joints'],step['joints']],model.vmax,config['speed_pct']).duration for knot,step in enumerate(group))
                    durations[1]+=curve.duration
                    commands.append(command);maximum=max(maximum,deviation)
                while index<len(original):
                    first=original[index];end=index+1
                    if first['drawing_trace']:
                        while (end<len(original) and original[end]['drawing_trace']
                               and original[end]['drawing_stroke_id']==first['drawing_stroke_id']):end+=1
                    group=original[index:end]
                    append_continuous(group,q if index==0 else original[index-1]['joints'])
                    index=end
                p['prepared_strokes']=prepared
                p['unmerged_step_count']=len(original)
                p['continuous_metrics']={'strokes':len(prepared),'previous_ink_duration_s':durations[0],'ink_duration_s':durations[1],
                    'fallback_ink_steps':sum(step['drawing_trace'] and 'drawing_continuous' not in step for step in commands)}
                for index,command in enumerate(commands):command['line']=index+1
            return commands,maximum,samples
        async def prepare():
            try:
                steps,error,samples=await asyncio.to_thread(build);check()
                if self.plan is not p or self.phase!='checking':raise RuntimeError('绘制预检已被撤销')
                self._idle();self._fresh()
                if self._context()!=p['context'] or max(abs(a-b) for a,b in zip(w.current_joints(),origin))>.05:
                    raise ValueError('预检期间起点、场景、工具或连接已变化')
                p.update(steps=steps,program=p.get('export_program','\n'.join(s['src'] for s in steps)),expires=self.clock()+PLAN_TTL,
                         max_tcp_deviation_mm=error,sample_count=samples)
                self.phase='ready';self.why='完整路径预检通过'
            except Exception as exc:
                if self.plan is p and self.phase!='cancelled':self.phase='failed';self.why=str(exc)
        self.worker=asyncio.create_task(prepare())
        return self.state()

    def _ticket(self,key):
        if not isinstance(key,str) or not self.plan or key!=self.plan['id']:raise ValueError('绘制计划标识已失效')
        return self.plan

    def prepared_step(self,step):
        """Private Runner hook: only this plan's exact leased step may execute."""
        self._guard();p=self.plan;w=self.world
        if (p['mode']!='sim' or w.real is not None or step is not w.runner.current()
                or step.get('drawing_plan_id')!=p['id']):raise RuntimeError('连续文字执行身份已变化')
        prepared=p.get('prepared_strokes',{}).get(step.get('drawing_continuous'))
        if prepared is None or prepared.context_signature!=p['context']:raise RuntimeError('连续文字预检不存在或已失效')
        return prepared

    def _guard(self):
        p=self.plan;w=self.world;l=self.lease
        if not l or not p or l['steps'] is not w.runner.steps or l['run_id']!=w.runner.run_id:raise RuntimeError('绘制执行身份已失效')
        if self._context()!=p['context']:raise RuntimeError('绘制场景、工具、会话或连接已变化')
        self._fresh()
        if p['mode']=='real' and w.settings['real_speed_pct']!=p['config']['speed_pct']:raise RuntimeError('绘制速度已变化，请停止后重新预检')
        if p['mode']=='real' and (w.speed_ack.get('ok') is not True or abs(w.real.snap.get('override',-1)*100-p['config']['speed_pct'])>.05):
            raise RuntimeError('实际倍率反馈已变化或未确认，绘制已失效')
        if w.runner.active_index is not None:
            step=w.runner.steps[w.runner.active_index]
            model=self._tool()[0];actual=model.pose_vec(w.current_joints())
            if step['drawing_within_bounds']:check_bounds(actual[:3],p['bounds'],margin=0)
            if p['mode']=='real' and line_distance(actual[:3],step['drawing_line_start'][:3],step['drawing_line_end'][:3])>2:
                raise RuntimeError('当前反馈TCP偏离已检查线段超过2毫米，绘制已停止')
            if p['mode']=='real' and evaluate(model,w.current_joints(),w.settings,*w.limits(),caps=model.capsules(w.current_joints(),conservative=True))['level'] in ('red','alarm'):
                raise RuntimeError('当前实际工具包络安全检查未通过，绘制已停止')

    def run(self,payload):
        if not isinstance(payload,dict) or set(payload)-{'plan_id','confirm_real'} or 'plan_id' not in payload:raise ValueError('绘制执行请求无效')
        p=self._ticket(payload['plan_id']);self.state()
        if self.phase!='ready':raise ValueError('请先完成当前绘制计划预检')
        self._idle();q=self._fresh()
        if max(abs(a-b) for a,b in zip(q,p['start']))>.05:raise ValueError('已离开绘制预检起点')
        if p['mode']=='real' and payload.get('confirm_real') is not True:raise ValueError('真机绘制需要本轮明确确认')
        if not self.world._need(self.world.MOTION,'绘制运行'):raise ValueError(self.world.last_deny)
        w=self.world
        w.runner.load(p['steps']);w.runner.next_run_id=p['id']
        result=w.start_program()
        if not result.get('ok'):self.phase='failed';self.why=result.get('why','绘制启动失败');raise ValueError(self.why)
        self.lease={'steps':w.runner.steps,'run_id':w.runner.run_id,'epoch':w.executor.epoch}
        w.runner.execution_guard=self.guard_callback;self.phase='running';self.why='正在执行已检查的绘制路径'
        async def watch():
            while self.phase in ('running','paused'):
                try:self._guard()
                except Exception as exc:
                    self.phase='failed';self.why=str(exc)
                    if w.runner.steps is self.lease['steps'] and w.executor.epoch==self.lease['epoch']:
                        stopped=await w.stop_execution('绘制已撤销：'+str(exc))
                        if stopped.get('settled') is not True:self.why+='；停止未确认，请使用停止入口重新确认'
                    return
                if (p['config']['shape']=='text' or p['mode']=='sim') and self.phase=='running':
                    step=w.runner.current() if w.runner.active_index is not None else None
                    if step and step.get('drawing_trace') and step.get('drawing_stroke_id') is not None:
                        history=p['actual_samples'];stroke=step['drawing_stroke_id'];point=self._tool()[0].pose_vec(w.current_joints())
                        if not history or history[-1]['stroke']!=stroke or math.dist(history[-1]['point'][:3],point[:3])>=.3:
                            if len(history)>=4096:
                                self.phase='failed';self.why='书写轨迹超过显示预算，请减少字符'
                                await w.stop_execution(self.why);return
                            history.append({'seq':len(history),'stroke':stroke,'point':point})
                state=self.state()
                if state['phase']=='failed':
                    if w.runner.steps is self.lease['steps'] and w.executor.epoch==self.lease['epoch'] and not w.executor.busy:
                        stopped=await w.stop_execution('绘制执行失败：'+self.why)
                        if stopped.get('settled') is not True:self.why+='；停止未确认，请使用停止入口重新确认'
                    return
                if state['phase']=='completed':return
                await asyncio.sleep(.02)
        self.watch=asyncio.create_task(watch())
        return self.state()

    async def cancel(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'plan_id'}:raise ValueError('取消绘制需要当前计划标识')
        self._ticket(payload['plan_id'])
        if self.cancel_event:self.cancel_event.set()
        w=self.world;l=self.lease
        active=self.phase in ('running','paused') and w.runner.state in ('running','waiting','paused')
        self.phase='cancelled';self.why='绘制已取消，已完成部分保留，不自动续行'
        if active and l and w.runner.steps is l['steps'] and w.runner.run_id==l['run_id'] and w.executor.epoch==l['epoch']:
            stopped=await w.stop_execution('用户取消绘制')
            if stopped.get('ok') is not True or stopped.get('settled') is not True:
                self.phase='failed';self.why='绘制已撤销，但停止未确认，请使用停止入口重新确认：'+str(stopped.get('why',''))
                return {**self.state(),'ok':False}
        return self.state()

    async def pause(self,payload):
        self._operation(payload,'running');self._guard()
        value=await self.world.pause_program()
        if not value.get('ok'):raise ValueError(value.get('why','暂停未确认'))
        self.phase='paused';return self.state()

    async def resume(self,payload):
        self._operation(payload,'paused');self._guard()
        value=await self.world.resume_program()
        if not value.get('ok'):raise ValueError(value.get('why','继续未确认'))
        self.phase='running';return self.state()

    def _operation(self,payload,phase):
        if not isinstance(payload,dict) or set(payload)!={'plan_id'}:raise ValueError('绘制操作需要当前计划标识')
        self._ticket(payload['plan_id']);self.state()
        if self.phase!=phase:raise ValueError('当前绘制状态不允许此操作')

    async def close(self):
        if self.plan and self.phase in ('checking','ready','running','paused'):await self.cancel({'plan_id':self.plan['id']})
        if self.worker:await self.worker
        if self.watch:await self.watch
