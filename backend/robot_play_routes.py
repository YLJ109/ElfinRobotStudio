"""预检后复用程序执行器的仿真取放；不拥有真机通道。"""
import asyncio
import copy
import json
import math
import secrets
import threading
import time
from fastapi import APIRouter, Body
from .core import program, sim_tool
from .core.kinematics import rpy_to_mat, capsule_distance, pose_rotation_distance
from .core.objects import half_height, radius, capsule_object_gap
from .core.planning import digest, prepare_movel, inspect_path
from .core.trajectory import JointTrajectory, PreparedMotion
from .core.safety import evaluate
from .play_routes import PlayService, board_position, computer_move, outcome, fields, fail
from .application.sorting_score import SortingScore
from .core.play_vision import bind_observations, evidence_current, sorting_targets, scene_bindings

PREFLIGHT_SECONDS=90.

def carried_clearance(model,q,held,objects):
    pose=model.pose_vec(q);rotation=rpy_to_mat(*map(math.radians,pose[3:]))
    xyz=[pose[i]+sum(rotation[i][j]*held['offset'][j] for j in range(3)) for i in range(3)]
    piece=held['piece'];size=piece['size'];half=[size[0],size[0],size[2]/2] if piece['kind']=='cylinder' else [size[0]/2,size[1]/2,size[2]/2]
    if xyz[2]-half[2]<-.1:raise RuntimeError('携带工件将穿入地面，已停止')
    for obstacle in objects:
        if obstacle['id']==piece['id'] or not obstacle.get('collide',True):continue
        other=obstacle['size'];extent=[other[0]/2,other[1]/2,other[2]/2] if obstacle['kind']=='box' else [other[0],other[0],half_height(obstacle)]
        if all(abs(xyz[i]-obstacle['xyz'][i])<half[i]+extent[i]-.1 for i in range(3)):
            raise RuntimeError('携带工件与场景物体相交：'+obstacle['name'])
    carried={**piece,'xyz':xyz}
    for cap in model.capsules(q,conservative=True):
        if cap['link'] in (model.flange,'elfin_base'):continue
        if capsule_object_gap(cap,carried)<-.1:raise RuntimeError('携带工件与非工具连杆相交')
    return xyz


class RobotPlay:
    def __init__(self,world,vision_workspace=None):
        self.world=world;self.scene=PlayService(world)
        self.vision_workspace=vision_workspace
        self.plans={};self.active=None;self.held=None;self.contact_ids=set()
        self.busy=False;self.message='';self.cancel=None
        self.progress={'phase':'idle','step':0,'total':0}
        self.planning_started=None
        self.sorting_score=SortingScore()

    def vision_snapshot(self):
        engine=self.vision_workspace
        if engine is None:raise ValueError('分拣视觉工作区未绑定，请接入当前VisionWorkspace实例')
        # Snapshot only; never process/open/scan the camera here. Vision owns its
        # lock before CameraManager throughout its existing acquisition path.
        lock=getattr(engine,'lock',None)
        def read():
            snapshot=engine.snapshot()
            if snapshot.get('config',{}).get('input')=='camera':
                cameras=getattr(engine,'cameras',None)
                active=cameras.snapshot().get('active') if cameras is not None else None
                snapshot['camera_identity']={k:active.get(k) for k in
                    ('session','id','physical_device_id','kind','state')} if active else None
            return snapshot
        if lock is None:return read()
        with lock:return read()

    def cancel_planning(self):
        if self.cancel:
            self.cancel.set();self.message='正在取消预检，等待计算线程退出'
            self.progress={**self.progress,'phase':'cancelling'}
        return {'ok':True,'planning':self.busy,'message':self.message}

    def context(self,controlled):
        w=self.world;objects=w.objects.list()
        for obj in objects:
            if obj['id'] in controlled:
                obj.pop('xyz',None)
                if obj.get('task'):obj['task'].pop('slot',None)
        return digest({'session':w.session_id,'settings':{key:w.settings.get(key) for key in
            ('mode','sim_tool','limit_source','custom_limits','fence','self_collision','ground_enabled')},
            'limits':w.limits(),'objects':objects})

    def state(self):
        return {'ok':True,'planning':self.busy,'active':bool(self.active),
                'held':self.held and self.held['id'],'message':self.message,
                'progress':dict(self.progress),'elapsed_s':round(time.monotonic()-self.planning_started,2) if self.busy and self.planning_started is not None else 0,
                'timeout_s':PREFLIGHT_SECONDS,'can_cancel':self.busy and self.cancel is not None,
                'vacuum':dict(self.world._sim_vacuum),'execution':self.world.runner.snapshot(),'sorting_score':self.sorting_score.snapshot()}

    def targets(self,payload,evidence=None):
        pieces,a,b=self.scene.group(self.world.objects.list(),payload['group'],
                                    'sorting' if payload['action']=='sorting' else 'chess')
        if payload['action']=='chess':
            slot=payload.get('slot')
            if type(slot) is not int or not 0<=slot<=8:fail('需要0～8的棋盘位置')
            if len(pieces)!=9 or sum(p['task']['symbol']=='O' for p in pieces)!=5 or sum(p['task']['symbol']=='X' for p in pieces)!=4:
                fail('棋子必须完整保留5圈4叉，请重新布置',409)
            board=[None]*9
            for piece in pieces:
                index=piece['task']['slot']
                if index < -1:fail('棋子状态异常，请先复位',409)
                if index>=0:
                    if index>8 or board[index]:fail('棋盘状态异常，请先复位',409)
                    expected=[*board_position(b,index),b['xyz'][2]+half_height(b)+half_height(piece)]
                    if math.dist(piece['xyz'],expected)>.5:fail('已落棋子或B区被移动，请先复位棋局',409)
                    board[index]=piece['task']['symbol']
            if 'symbol' in payload:
                symbol=payload['symbol']
                if symbol not in ('O','X'):fail('棋步符号需要O或X')
                oc,xc=board.count('O'),board.count('X')
                if outcome(board) or board[slot] or not (oc==xc or oc==xc+1):fail('棋局已结束、轮次异常或格位不可用',409)
                if symbol!=('O' if oc==xc else 'X'):fail('O先行且双方逐枚交替，请等待对应回合',409)
                piece=next((p for p in pieces if p['task']['symbol']==symbol and p['task']['slot']==-1),None)
                if not piece:fail('对应符号的A区棋子不足，请重新布置',409)
                return [(piece,[*board_position(b,slot),b['xyz'][2]+half_height(b)+half_height(piece)],slot)]
            if outcome(board) or board.count('O')!=board.count('X') or board[slot]:fail('棋局已结束或格位不可用',409)
            result=[]
            for symbol in ('O','X'):
                if outcome(board):break
                index=slot if symbol=='O' else computer_move(board)
                piece=next((p for p in pieces if p['task']['symbol']==symbol and p['task']['slot']==-1),None)
                if not piece:fail('棋子不足，请重新布置',409)
                result.append((piece,[*board_position(b,index),b['xyz'][2]+half_height(b)+half_height(piece)],index))
                board[index]=symbol
            return result
        criterion=payload.get('criterion','color')
        if criterion not in ('color','shape'):fail('分拣规则需要颜色或形状')
        if any(p['kind'] not in ('box','cylinder') for p in pieces):fail('当前分拣仅支持平顶箱体或圆柱')
        if len(pieces)>27 or any((2*p['size'][0] if p['kind']=='cylinder' else max(p['size'][:2]))>82 for p in pieces):fail('工件不符合任务模板，请重新布置')
        if any(p['task']['slot']>=0 for p in pieces):fail('请先将全部工件复位回A区再重新识别分拣',409)
        if evidence is None:evidence=bind_observations(self.vision_snapshot(),pieces,time.time(),criterion=payload.get('criterion','color'))
        return sorting_targets(pieces,b,{oid:value['shape' if criterion=='shape' else 'color'] for oid,value in evidence['bindings'].items()})

    async def generate(self,payload,*,bound_targets=None):
        from .core.pick_place_program import generate_pick_place
        fields(payload,('group','action','symbol','slot','clearance','speed','wait','criterion','source','perception'),('group','action'))
        if payload.get('perception','camera') not in ('scene','camera'):fail('物料来源无效')
        if payload['action'] not in ('sorting','chess') and not (payload['action']=='handling' and bound_targets is not None):fail('任务动作无效')
        if 'symbol' in payload and payload['action']!='chess':fail('symbol仅用于单枚棋步')
        self.scene.guard.require_idle()
        if self.busy or self.active or self.held:fail('请先停止程序并处理吸附工件',409)
        descriptor=sim_tool.normalize(self.world.settings.get('sim_tool',sim_tool.DEFAULT_TOOL))
        if not descriptor or descriptor['kind']!='vacuum-reference':
            # 分拣必须用双吸盘，但"换工具"是系统该做的事，不该让用户先跑去别处
            # 手动切好再回来点开始。这里自动切到双吸盘参考工具（顺带放下上一轮
            # 任务攥着的仿真持件），确实切不过去才报具体原因。
            applied=self.world.ensure_sim_task_tool('chess')
            if not applied.get('ok'):fail(str(applied.get('why') or '无法自动切换到双吸盘参考工具'),409)
            descriptor=sim_tool.normalize(self.world.settings.get('sim_tool',sim_tool.DEFAULT_TOOL))
            if not descriptor or descriptor['kind']!='vacuum-reference':fail('请先选择双吸盘参考工具，不能以无工具状态吸取',409)
        d=descriptor['dimensions'];offset=[(d['length']-2*d['edge'])/2,0,d['thickness']/2+d['cupHeight']]
        # TCP是安装板中心；选右吸盘接触工件，避免让安装板中心额外远离基座140mm。
        orientation=[180.,0.,0.];rotation=rpy_to_mat(*map(math.radians,orientation))
        shift=[sum(rotation[i][j]*offset[j] for j in range(3)) for i in range(3)]
        evidence=None
        if payload['action']=='sorting':
            pieces,_,_=self.scene.group(self.world.objects.list(),payload['group'],'sorting')
            evidence=scene_bindings(pieces) if payload.get('perception')=='scene' else bind_observations(self.vision_snapshot(),pieces,time.time(),criterion=payload.get('criterion','color'))
        # Only the internal course controller may supply current, bound targets.
        # The HTTP generation handler never forwards this keyword.
        values=bound_targets if bound_targets is not None else self.targets(payload,evidence)
        controlled={p['id'] for p,_,_ in values}
        items=[];bindings={}
        for piece,place,slot in values:
            def pose(xyz):return [xyz[0]-shift[0],xyz[1]-shift[1],xyz[2]+half_height(piece)-shift[2],*orientation]
            pick=pose(piece['xyz']);target=pose(place)
            items.append({'id':piece['id'],'pick':pick,'place':target})
            bindings[piece['id']]={'pick':pick,'place':target,'xyz':place,'slot':slot,'initial':piece['xyz'][:],'size':piece['size'][:]}
        generated=generate_pick_place({'items':items,'wait':payload.get('wait',[400,0,450,180,0,0]),
            'clearance':payload.get('clearance',120),'speed':payload.get('speed',self.world.settings['sim_speed_pct']),'pick_wait':0,'blow_wait':0,'tool_id':'vacuum-reference'})
        # Generated tasks follow the live top-bar override; only source that
        # the caller explicitly supplies may carry a user SPEED instruction.
        generated['source']='\n'.join(line for line in generated['source'].splitlines() if not line.strip().startswith('SPEED '))
        source=payload.get('source',generated['source'])
        if not isinstance(source,str) or len(source)>100000:fail('程序文本无效')
        w=self.world;start=list(w.current_joints());context=self.context(controlled)
        settings=copy.deepcopy(w.settings);objects=copy.deepcopy(w.objects.list());lo,hi=w.limits()
        model=copy.copy(w.motion_model());model._fk_key=model._fk_value=None
        self.busy=True;cancel=threading.Event();self.cancel=cancel;self.plans={}
        self.planning_started=time.monotonic();deadline=self.planning_started+PREFLIGHT_SECONDS
        self.message='正在完整路径预检';self.progress={'phase':'parsing','step':0,'total':0}
        def checkpoint():
            if cancel.is_set():raise RuntimeError('预检已取消')
            if time.monotonic()>=deadline:raise RuntimeError('完整路径预检超时，请减少工件或分批执行')
        def prepare():
            steps=program.parse(source,w.resolve_point,point_items=w.point_items())
            allowed={'speed','movej','movel','wait','tool_action','require_tool','sim_pick','sim_place'}
            if any(step['kind'] not in allowed for step in steps):raise ValueError('取放程序只接受运动、工具、等待和绑定的取放命令')
            motions={};q=start[:];held=None;held_shape=None;current=None;source_lines=source.splitlines()
            handled=set();released=set();vacuum=False;blow=False;ctx={'robot_play':context,'mode':'sim','real_connected':False}
            for index,step in enumerate(steps):
                self.progress={'phase':'checking','step':index+1,'total':len(steps),'line':step['line'],'kind':step['kind']}
                checkpoint();kind=step['kind']
                if kind=='speed':settings['sim_speed_pct']=step['pct']
                elif kind=='require_tool':
                    if step['tool_id']!='vacuum-reference':raise ValueError('取放程序需要已选择的吸盘工具')
                elif kind=='tool_action':
                    action=step['action']
                    if action not in ('vacuum_on','vacuum_off','blow_on','blow_off'):raise ValueError('当前取放工具不支持该动作')
                    if action=='vacuum_on' and blow or action=='blow_on' and vacuum:raise ValueError('吸气与破真空不能同时开启')
                    if action.startswith('vacuum_'):vacuum=action=='vacuum_on'
                    if action.startswith('blow_'):blow=action=='blow_on'
                elif kind in ('sim_pick','sim_place'):
                    oid=step['object_id']
                    if oid not in bindings:raise ValueError('程序引用了未绑定的工件')
                    target=bindings[oid]['pick' if kind=='sim_pick' else 'place']
                    if math.dist(model.pose_vec(q)[:3],target[:3])>1:raise ValueError('取放命令不在对应取料/放料位，未执行')
                    if pose_rotation_distance(model.pose_vec(q),target)>.5:raise ValueError('吸盘姿态不在对应取放姿态')
                    if kind=='sim_pick':
                        if held or oid in handled or not vacuum or blow:raise ValueError('重复吸取、已有吸附物体或吸气状态不正确')
                        held=oid;handled.add(oid)
                        piece=next(o for o in objects if o['id']==oid);tcp=model.pose_vec(q);r=rpy_to_mat(*map(math.radians,tcp[3:]));delta=[piece['xyz'][i]-tcp[i] for i in range(3)]
                        held_shape={'piece':piece,'offset':[sum(r[j][i]*delta[j] for j in range(3)) for i in range(3)]}
                    else:
                        if held!=oid or vacuum or not blow:raise ValueError('释放对象不一致或未按停吸→破真空顺序操作')
                        held=None;held_shape=None;released.add(oid)
                        for item in objects:
                            if item['id']==oid:item['xyz']=bindings[oid]['xyz'][:]
                elif kind in ('movej','movel'):
                    destination=step['pose'] if kind=='movel' else model.pose_vec(step['joints'])
                    if held and pose_rotation_distance(model.pose_vec(q),destination)>.5:raise ValueError('当前场景工件携带仅支持保持向下姿态，不能中途翻转')
                    if current is None:
                        current=next((later['object_id'] for later in steps[index:] if later['kind']=='sim_pick'),None)
                    contact={current} if current else set()
                    obstacle=[o for o in objects if o['id'] not in contact]
                    if not motions and kind=='movel':
                        at_wait = model.pose_vec(q)
                        if math.dist(at_wait[:3], step['pose'][:3]) <= .05 and pose_rotation_distance(at_wait, step['pose']) <= .05:
                            target = q[:]
                        else:
                            candidates=[]
                            # Try the current branch first; extra seeds are fallbacks,
                            # not repeated work after a valid connected branch is found.
                            seeds=[q,[0,-45,90,0,45,0],[0,-70,-90,0,100,0],[0,45,-90,0,90,0]]
                            for seed in seeds:
                                checkpoint()
                                candidate,mm,deg,ok=model.ik(seed,step['pose'],lo,hi,iters=140,max_restarts=2,deadline=deadline,cancelled=cancel.is_set)
                                checkpoint()
                                if not ok:continue
                                probe,_,_,reached=model.ik(candidate,items[0]['pick'],lo,hi,iters=140,max_restarts=2,deadline=deadline,cancelled=cancel.is_set)
                                checkpoint()
                                if not reached:continue
                                risks=[evaluate(model,row,settings,lo,hi) for row in (candidate,probe)]
                                if any(r['level'] in ('red','alarm') for r in risks):continue
                                candidates.append(candidate)
                                break
                            if not candidates:raise ValueError('等待位和首个取料位未找到同一安全运动学分支，请调整料区或等待位')
                            target=candidates[0]
                        step.update(kind='movej',joints=list(target));step.pop('pose',None);kind='movej'
                        step['src']='MOVEJ '+','.join(format(v,'.9g') for v in target)
                        source_lines[step['line']-1]=step['src']+' # 等待位采用关节接近，避免零位翻腕直线'
                    if kind=='movel':
                        try:motion=prepare_movel(model,q,step['pose'],settings,lo,hi,obstacle,context=ctx,check=checkpoint,
                                                 deadline=deadline,cancelled=cancel.is_set)
                        except (ValueError,RuntimeError) as exc:raise RuntimeError(f"第{step['line']}行 {step['src']}：{exc}") from exc
                    else:
                        curve=JointTrajectory.build([q,step['joints']],model.vmax,settings['sim_speed_pct'])
                        report=inspect_path(model,q,step['joints'],settings,lo,hi,obstacle,trajectory=curve,check=checkpoint)
                        if report['risk_detected']:raise ValueError('等待位/关节路径未通过碰撞或限位预检')
                        motion=PreparedMotion(curve,digest(ctx),tuple(lo),tuple(hi),json.dumps(report))
                    motions[index]={'motion':motion,'contact':contact,'expected_start':q[:]}
                    if held_shape:
                        for sample in motion.trajectory.inspection_samples():checkpoint();carried_clearance(model,sample,held_shape,objects)
                    q=list(motion.trajectory.points[-1])
                    # 回等待位之后下一循环重新绑定当前允许接触的工件。
                    if not held and current in released and math.dist(model.pose_vec(q)[:3],generated['points'][0]['pose'][:3])<1:current=None
            if held or handled!=set(bindings) or released!=set(bindings):raise ValueError('程序必须对每个绑定工件完成一次吸取和一次释放')
            if vacuum or blow:raise ValueError('程序结束前必须停止吸气和破真空')
            return steps,motions,'\n'.join(source_lines)
        worker=asyncio.create_task(asyncio.to_thread(prepare))
        try:
            # Request returns at the deadline even if a dependency fails to cooperate.
            # busy stays held until the worker really exits: never start a second solver.
            try:steps,motions,source=await asyncio.wait_for(asyncio.shield(worker),max(.001,deadline-time.monotonic()))
            except TimeoutError:
                cancel.set();raise RuntimeError('完整路径预检超时，请减少工件或分批执行') from None
            checkpoint()
            self.scene.guard.require_idle(allow_play_planning=True)
            if evidence and evidence.get('source')!='scene' and not evidence_current(evidence,self.vision_snapshot()):fail('预检期间视觉来源或标定已变化，请重新采集生成',409)
            if context!=self.context(controlled) or any(abs(a-b)>.05 for a,b in zip(start,w.current_joints())):fail('预检期间场景或起点已变化，请重新生成',409)
            key=secrets.token_hex(16)
            plan={'id':key,'group':payload['group'],'action':payload['action'],'criterion':payload.get('criterion','color'),'context':context,'controlled':controlled,'start':start,'source':source,'steps':steps,'motions':motions,
                  'bindings':bindings,'vision_evidence':evidence,'expected':{key:v['initial'][:] for key,v in bindings.items()},
                  'expected_slots':{p['id']:p['task']['slot'] for p,_,_ in values},'expires':time.monotonic()+180}
            self.plans={key:plan};self.message=f'完整预检通过：{len(bindings)}个取放循环'
            self.progress={**self.progress,'phase':'complete'}
            return {'ok':True,'plan_id':key,'source':source,'points':generated['points'],'cycles':generated['cycles'],
                    'group':payload['group'],'action':payload['action'],'slot':payload.get('slot'),'symbol':payload.get('symbol'),'criterion':payload.get('criterion','color'),
                    'vision_evidence':evidence,'validated_hardware':False}
        except asyncio.CancelledError:
            cancel.set();self.message='预检请求已取消';self.progress={**self.progress,'phase':'cancelled'};raise
        except Exception as exc:
            self.message=str(getattr(exc,'detail',exc));self.progress={**self.progress,'phase':'failed'};raise
        finally:
            def finished(done=None):
                if done and not done.cancelled():done.exception()
                if self.cancel is cancel:self.busy=False;self.cancel=None
            if worker.done():finished()
            else:worker.add_done_callback(finished)

    def run(self,payload):
        fields(payload,('plan_id','source'),('plan_id','source'));self.scene.guard.require_idle()
        plan=self.plans.pop(payload['plan_id'],None)
        if not plan or plan['expires']<time.monotonic():fail('程序预检已过期，请重新生成',409)
        if plan.get('vision_evidence') and plan['vision_evidence'].get('source')!='scene' and not evidence_current(plan['vision_evidence'],self.vision_snapshot()):fail('视觉来源或标定已变化，请重新生成',409)
        if payload['source']!=plan['source']:fail('程序已修改，请先重新预检修改后的代码',409)
        if self.active or self.held or plan['context']!=self.context(plan['controlled']):fail('场景或工具已变化，请重新预检',409)
        if any(abs(a-b)>.05 for a,b in zip(plan['start'],self.world.current_joints())):fail('机器人已离开预检起点',409)
        if not self.world._need(self.world.MOTION,'场景取放程序'):fail(self.world.last_deny,409)
        # Enable the frozen-view lifecycle only after the strict source/view
        # comparison above and immediately before this plan owns the runner.
        plan['vision_execution_started']=True
        self.active=plan
        try:self.guard()
        except Exception:self.active=None;raise
        self.world.runner.load(plan['steps'])
        self.world.runner.next_run_id=plan['id']
        result=self.world.start_program()
        if not result.get('ok'):self.world.runner.next_run_id=None;self.active=None;return result
        if plan.get('action')=='sorting':self.sorting_score.begin(plan)
        return {'ok':True,'note':'仅仿真机器人取放，未向真实硬件发送'}

    def guard(self):
        w=self.world;plan=self.active
        if plan and plan.get('vision_evidence') and plan['vision_evidence'].get('source')!='scene' and not evidence_current(plan['vision_evidence'],self.vision_snapshot(),
                allow_view_change=plan.get('vision_execution_started') is True):
            raise RuntimeError('视觉来源或标定已变化，取放程序已撤销')
        if not plan or w.settings.get('mode')!='sim' or w.real is not None or plan['context']!=self.context(plan['controlled']):
            raise RuntimeError('取放场景、工具、模式或限位已变化，程序已撤销')
        items={item['id']:item for item in w.objects.list()}
        for oid,xyz in plan['expected'].items():
            if oid not in items or math.dist(items[oid]['xyz'],xyz)>.05:raise RuntimeError('任务工件被外部移动，程序已撤销')
            if oid in plan.get('expected_slots',{}) and items[oid]['task']['slot']!=plan['expected_slots'][oid]:
                raise RuntimeError('任务工件槽位被外部修改，程序已撤销')
        if self.held:carried_clearance(w.motion_model(),w.current_joints(),self.held,w.objects.list())

    async def motion(self,step,epoch):
        self.guard();index=self.world.runner.active_index
        segment=self.active['motions'].get(index)
        if not segment or step is not self.active['steps'][index]:raise RuntimeError('程序步骤与预检版本不一致')
        if any(abs(a-b)>.1 for a,b in zip(segment['expected_start'],self.world.current_joints())):raise RuntimeError('运动段起点已变化')
        self.contact_ids=set(segment['contact']);self.world._safe_key=None
        await self.world.executor.execute_prepared_sim(self.world.sim,segment['motion'],epoch,validate_context=lambda _:self.guard())

    def grip(self,kind,oid):
        self.guard();w=self.world;binding=self.active['bindings'].get(oid)
        if not binding:raise RuntimeError('工件不在已预检计划内')
        pose=w.motion_model().pose_vec(w.current_joints());target=binding['pick' if kind=='sim_pick' else 'place']
        if math.dist(pose[:3],target[:3])>1:raise RuntimeError('吸取/释放位置未到达')
        if kind=='sim_pick':
            if self.held or not w._sim_vacuum['vacuum'] or w._sim_vacuum['blow']:raise RuntimeError('吸气状态或吸附对象不正确')
            obj=next(p for p in w.objects.list() if p['id']==oid)
            rotation=rpy_to_mat(*map(math.radians,pose[3:]));delta=[obj['xyz'][i]-pose[i] for i in range(3)]
            self.held={'id':oid,'piece':obj,'offset':[sum(rotation[j][i]*delta[j] for j in range(3)) for i in range(3)],
                       'model':copy.copy(w.motion_model())}
        else:
            if not self.held or self.held['id']!=oid or w._sim_vacuum['vacuum'] or not w._sim_vacuum['blow']:
                raise RuntimeError('请先停止吸气并开启破真空再放料')
            # Preserve the actual owned grasp position instead of snapping to a plan.
            rotation=rpy_to_mat(*map(math.radians,pose[3:]))
            actual=[pose[i]+sum(rotation[i][j]*self.held['offset'][j] for j in range(3)) for i in range(3)]
            if math.dist(actual,binding['xyz'])>1:raise RuntimeError('工件释放位置与目标不一致')
            self._commit(oid,actual,binding['slot']);self.held=None
        self.world._safe_key=None

    def _commit(self,oid,xyz,slot=None):
        store=self.world.objects
        with store._lock:
            candidate=copy.deepcopy(store.items)
            obj=next((p for p in candidate if p['id']==oid),None)
            if not obj:raise RuntimeError('吸附工件已不存在')
            obj['xyz']=list(xyz)
            if slot is not None:obj['task']['slot']=slot
            store._persist(candidate);store.items=candidate;store._vel.pop(oid,None)
        if self.active:
            self.active['expected'][oid]=list(xyz)
            if slot is not None and 'expected_slots' in self.active:self.active['expected_slots'][oid]=slot
            if slot is not None:self.sorting_score.placed(self.active.get('id'),oid,list(xyz),slot)
            observer=getattr(self,'event_observer',None)
            if observer is not None and slot is not None:
                observer('placed',self.active,object_id=oid,xyz=list(xyz),slot=slot,stamp=time.monotonic())

    def display_attachment(self):
        """Expose the owned grasp in physical-flange coordinates for presentation."""
        if not self.held or self.world.settings.get('mode')!='sim' or self.world.real is not None:return None
        model=self.held.get('model') or self.world.motion_model()
        q=self.world.current_joints();pose=model.pose_vec(q);rotation=rpy_to_mat(*map(math.radians,pose[3:]))
        xyz=[pose[i]+sum(rotation[i][j]*self.held['offset'][j] for j in range(3)) for i in range(3)]
        flange=model.fk(q)[model.flange]
        delta=[xyz[i]/1000-flange[i][3] for i in range(3)]
        local=[sum(flange[j][i]*delta[j] for j in range(3)) for i in range(3)]
        return {'id':self.held['id'],'link':model.flange,'local_m':local}

    def tick(self):
        w=self.world
        # Motion validation must never veto terminal/explicit stop cleanup.
        # While executing, stop the actual curve first and retain attachment.
        if self.active and w.runner.state in ('running','waiting'):
            try:self.guard()
            except Exception as exc:
                reason='取放上下文失效，运动已停止：'+str(exc)
                self.stop()
                self.message=reason+('；仍吸附工件，保持吸气等待人工处理' if self.held else '')
                return
        if self.held and w.settings.get('mode')=='sim' and w.real is None:
            # Tool/config changes cannot reinterpret an already held offset.
            model=self.held.get('model') or w.motion_model()
            pose=model.pose_vec(w.current_joints());rotation=rpy_to_mat(*map(math.radians,pose[3:]))
            xyz=[pose[i]+sum(rotation[i][j]*self.held['offset'][j] for j in range(3)) for i in range(3)]
            with w.objects._lock:
                obj=next((item for item in w.objects.items if item['id']==self.held['id']),None)
                if obj:
                    obj['xyz']=xyz
                    if self.active:self.active['expected'][obj['id']]=xyz[:]
        if self.active and w.runner.state not in ('running','waiting','paused'):
            terminal=self.active
            self.message='取放程序完成' if w.runner.state=='finished' else '取放已停止；若仍吸附工件，保持吸气等待人工处理'
            self.active=None;self.contact_ids={self.held['id']} if self.held else set();w._safe_key=None
            self._finish_sorting(terminal,w.runner.state)
            observer=getattr(self,'event_observer',None)
            if observer is not None:observer('terminal',terminal,state=w.runner.state,stamp=time.monotonic())

    def stop(self):
        if self.cancel:self.cancel.set()
        # This is a cleanup path, not a motion authorization. Revoke ownership
        # BEFORE tick/placement handling; no guard or scene context is required.
        terminal=self.active
        binding=self.active['bindings'].get(self.held['id']) if self.active and self.held else None
        if self.active:
            for owner,method,args in ((getattr(self.world,'sim',None),'stop',()),
                                      (getattr(self.world,'executor',None),'revoke',('取放已停止',)),
                                      (self.world.runner,'stop',('取放已停止',))):
                operation=getattr(owner,method,None)
                if operation is not None:operation(*args)
            if hasattr(self.world.runner,'busy'):self.world.runner.busy=False
        self.active=None;self.plans={}
        self.contact_ids={self.held['id']} if self.held else set();self.world._safe_key=None
        self.tick()
        if self.held:
            obj=next((p for p in self.world.objects.list() if p['id']==self.held['id']),None)
            if not self.world._sim_vacuum['vacuum']:
                # VACUUM_OFF can complete before SIM_PLACE is dispatched. Do not
                # keep a non-vacuum attachment or teleport a stopped piece to B.
                slot=-1
                if binding:
                    pose=self.world.motion_model().pose_vec(self.world.current_joints())
                    if math.dist(pose[:3],binding['place'][:3])<1:slot=binding['slot']
                if obj:self._commit(obj['id'],obj['xyz'],slot)
                self.held=None;self.contact_ids=set();self.world._safe_key=None
                self.message='取放已停止；真空已关闭，工件保留当前位置'+('，已记录B区槽位' if slot>=0 else '，请手动整理')
            elif obj:self._commit(obj['id'],obj['xyz'])
        if terminal is not None:self._finish_sorting(terminal,'aborted')
        observer=getattr(self,'event_observer',None)
        if observer is not None and terminal is not None:observer('terminal',terminal,state='aborted',stamp=time.monotonic())

    def _finish_sorting(self,plan,state):
        if plan.get('action')!='sorting':return
        objects={v['id']:v for v in self.world.objects.list()}
        actual=not self.held and all(oid in objects and objects[oid].get('task',{}).get('slot')==goal['slot']
                  and math.dist(objects[oid]['xyz'],goal['xyz'])<=.5 for oid,goal in plan['bindings'].items())
        self.sorting_score.finish(plan['id'],state,actual)

    def release_manual(self):
        if self.active:raise RuntimeError('程序运行中不能手动释放持件')
        if self.held:
            oid=self.held['id'];obj=next((p for p in self.world.objects.list() if p['id']==oid),None)
            if obj:self._commit(oid,obj['xyz'],-1)
            self.held=None;self.contact_ids=set();self.world._safe_key=None
            self.message='已停止吸气并在当前位置保留仿真工件，请手动复位或整理'


def create_robot_play_router(world,vision_workspace=None):
    service=RobotPlay(world,vision_workspace);world.robot_play=service;router=APIRouter(prefix='/api/play/program')
    @router.get('/state')
    async def state():return service.state()
    @router.post('')
    async def generate(payload:dict=Body(...)):
        try:return await service.generate(payload)
        except (ValueError,RuntimeError) as exc:return {'ok':False,'why':str(exc)}
    @router.post('/run')
    async def run(payload:dict=Body(...)):return service.run(payload)
    @router.post('/cancel')
    async def cancel():return service.cancel_planning()
    @router.post('/release')
    async def release():
        if (world.settings.get('mode')!='sim' or world.real is not None or service.active or service.busy
                or world.executor.busy or world.runner.state in ('running','waiting','paused')):
            return {'ok':False,'why':'只能在已停止且未连接真机的仿真中手动释放'}
        for action in ('vacuum_off','blow_on','blow_off'):
            result=await world.tool_action(action)
            if not result.get('ok'):return result
        return {'ok':True,'note':'已停止吸气并完成破真空，仿真工件保留当前位置，需人工整理'}
    return router
