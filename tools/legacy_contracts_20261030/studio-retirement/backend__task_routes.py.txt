"""工艺工作区显式API；所有新增运动仅复用离线仿真执行器。"""
import asyncio
import copy
import math
import secrets
import time
import threading
from fastapi import APIRouter, Body
from .core.task_studio import RecordingStore, generate, vector, number, MAX_WAYPOINTS
from .core.planning import inspect_path, prepare_movel


class TaskStudio:
    def __init__(self,world):
        self.world=world
        self.recordings=RecordingStore()
        self.plans={}
        self.inspection_busy=False

    def observe(self):
        self.recordings.observe(self.world)

    def require_sim(self,idle=False):
        w=self.world
        if w.settings.get('mode')!='sim' or w.real is not None:
            raise ValueError('新增工艺和录制仅用于未连接真机的仿真')
        if idle and (w.executor.busy or w.hand_follow.active or w.sim.traj is not None
                     or w.sim.jog is not None or w.runner.state in ('running','waiting','paused')
                     or w.queue_state in ('running','paused')):
            raise ValueError('请先停止当前运动或跟随')

    async def inspect(self,payload):
        if set(payload)-{'joints','poses','speed'}:raise ValueError('检查字段无效')
        self.require_sim(idle=True)
        if self.inspection_busy:
            raise ValueError('路径检查正在进行，请等待完成')
        fields=[key for key in ('joints','poses') if key in payload]
        if len(fields)!=1:
            raise ValueError('请选择关节轨迹或TCP轨迹之一')
        field=fields[0];rows=payload[field]
        if not isinstance(rows,list) or not 1<=len(rows)<=MAX_WAYPOINTS:
            raise ValueError('检查需要1～256个路点；长录制请先显式简化')
        rows=[vector(row) for row in rows]
        w=self.world
        context=w.plan_context();start=list(w.current_joints())
        settings,objects=copy.deepcopy(w.settings),copy.deepcopy(w.objects.list())
        speed=number(payload.get('speed',settings.get('sim_speed_pct',10)),1,100)
        settings['sim_speed_pct']=speed
        lower,upper=w.limits()
        model=copy.copy(w.motion_model());model._fk_key=model._fk_value=None
        source=('MOVEL' if field=='poses' else 'MOVEJ')
        program='SPEED '+format(speed,'.8g')+'\n'+'\n'.join(source+' '+','.join(format(v,'.10g') for v in row) for row in rows)
        cancelled=threading.Event();deadline=time.monotonic()+30.
        def checkpoint():
            if cancelled.is_set():raise RuntimeError('路径检查已取消')
            if time.monotonic()>deadline:raise RuntimeError('路径检查超时，请减少路点后重试')
        def check():
            q=list(start);issues=[];path=[];seconds=0.
            for index,row in enumerate(rows):
                try:
                    checkpoint()
                    if field=='poses':
                        prepared=prepare_movel(model,q,row,settings,lower,upper,objects,
                            context={'mode':'sim','real_connected':False,'plan':context},check=checkpoint)
                        report=prepared.report
                        q=list(prepared.trajectory.points[-1])
                    else:
                        report=inspect_path(model,q,row,settings,lower,upper,objects)
                        q=list(row)
                    path.extend(report.get('tcp_path',[]))
                    seconds+=report.get('duration_estimate_s',0.)
                    if report['risk_detected']:
                        issues.append({'waypoint':index+1,'why':'路径接近碰撞或越限','details':report['issues'][:3]})
                        break
                except (ValueError,RuntimeError) as exc:
                    issues.append({'waypoint':index+1,'why':str(exc)})
                    break
            return {'risk_detected':bool(issues),'issues':issues,'tcp_path':path[:20000],
                    'duration_estimate_s':round(seconds,2),'waypoints':len(rows),
                    'hardware_verified':False,'note':'仿真近似轨迹与离散碰撞检查，不是连续扫掠或真机资格证明'}
        self.inspection_busy=True
        worker=asyncio.create_task(asyncio.to_thread(check))
        try:
            report=await asyncio.shield(worker)
            self.require_sim(idle=True)
            if context!=w.plan_context() or any(abs(a-b)>.01 for a,b in zip(start,w.current_joints())):
                raise ValueError('检查期间起点、工具或现场已变化，请重新检查')
            key=secrets.token_urlsafe(18)
            self.plans={k:v for k,v in self.plans.items() if v['until']>time.monotonic()}
            if len(self.plans)>=16:self.plans.pop(next(iter(self.plans)))
            self.plans[key]={'context':context,'start':start,'program':program,'report':report,'until':time.monotonic()+120}
            return {'ok':True,'plan_id':key,'report':report}
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finally:
            if worker.done():self.inspection_busy=False
            else:
                # 正确：HTTP取消不能提前释放仍在运行的线程占用。
                def finished(task):
                    self.inspection_busy=False
                    if not task.cancelled():task.exception()
                worker.add_done_callback(finished)

    async def run(self,key):
        if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('缺少有效的检查编号')
        self.require_sim(idle=True)
        plan=self.plans.get(key)
        if not plan or plan['until']<time.monotonic() or plan['report']['risk_detected']:
            raise ValueError('检查不存在、已过期或包含风险，请重新检查')
        w=self.world
        if plan['context']!=w.plan_context() or any(abs(a-b)>.01 for a,b in zip(plan['start'],w.current_joints())):
            raise ValueError('起点或现场已变化，请重新检查')
        result=await w.load_program(plan['program'])
        if not result.get('ok'):return result
        self.plans.pop(key,None)
        return w.start_program()


def create_task_router(world):
    router=APIRouter(prefix='/api/studio')
    studio=TaskStudio(world)
    router.studio=studio

    @router.get('')
    async def status():
        return {'ok':True,'recordings':studio.recordings.list(),'recording':studio.recordings.status(),
                'templates':['raster','circle','pick-place','pallet']}

    @router.post('/generate')
    async def generate_task(payload:dict=Body(...)):
        return {'ok':True,'draft':generate(payload)}

    @router.post('/inspect')
    async def inspect_task(payload:dict=Body(...)):
        return await studio.inspect(payload)

    @router.post('/run')
    async def run_task(payload:dict=Body(...)):
        return await studio.run(payload.get('plan_id'))

    @router.post('/record/start')
    async def record_start(payload:dict=Body(...)):
        studio.require_sim()
        return {'ok':True,'recording':studio.recordings.start(payload.get('name','未命名轨迹'))}

    @router.post('/record/stop')
    async def record_stop():
        return {'ok':True,'item':studio.recordings.stop(),'recording':studio.recordings.status(),
                'recordings':studio.recordings.list()}

    @router.get('/recordings/{key}')
    async def get_recording(key:str):
        return {'ok':True,'item':studio.recordings.get(key)}

    @router.put('/recordings/{key}')
    async def edit_recording(key:str,payload:dict=Body(...)):
        return {'ok':True,'item':studio.recordings.edit(key,payload)}

    @router.delete('/recordings/{key}')
    async def delete_recording(key:str):
        studio.recordings.remove(key)
        return {'ok':True,'recordings':studio.recordings.list()}

    @router.post('/recordings/{key}/inspect')
    async def inspect_recording(key:str):
        item=studio.recordings.get(key)
        field='joints' if item.get('joints') else 'poses'
        return await studio.inspect({field:item[field]})

    return router
