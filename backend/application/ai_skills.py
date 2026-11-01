"""Finite AI task composition over the single checked Drawing and Runner."""
import asyncio
import copy
import math
import time
from ..core.ai_plan import skill_proposal

def drawing_config(world,shape):
    defaults=world.settings.get('interaction_range_default') or {}
    center=defaults.get('center',[-500,0,700]);size=defaults.get('size',[900,300,500])
    return {'shape':shape,'center':list(center)+[90,0,-90],'plane':'xz','width_mm':280,
            'height_mm':240,'angle_deg':0,'speed_pct':world.settings['sim_speed_pct'],'repeats':1,
            'direction':270,'range_center_mm':list(center),'range_size_mm':list(size),'center_policy':'range_lower'}

def _stop_note(stopped):
    return '' if stopped.get('settled') is True else '；停止未确认：'+str(stopped.get('why') or '')

async def _approach(world,joints,current):
    """Execute the checked prefix point through the existing plan/executor entry and wait for arrival.

    inspect_plan binds the exact start/context and inspects the route; execute_plan
    re-verifies both and hands the same MOVEJ to the one motion executor. This
    function only waits; it adds no motion path of its own.
    """
    if not current():return {'ok':False,'why':'AI请求已变化，未开始移动'}
    checked=await world.inspect_plan(list(joints))
    if not checked.get('ok'):return {'ok':False,'why':'组合起点未通过路径检查：'+str(checked.get('why') or '未知原因')}
    if not current():return {'ok':False,'why':'组合起点检查期间请求已变化，未开始移动'}
    started=await world.execute_plan(checked['plan_id'])
    if not started.get('ok'):return {'ok':False,'why':'组合起点未执行：'+str(started.get('why') or '未知原因')}
    report=checked.get('report') if isinstance(checked.get('report'),dict) else {}
    estimate=report.get('duration_estimate_s')
    limit=float(estimate)*2+10 if type(estimate) in (int,float) and math.isfinite(estimate) else 30.
    deadline=time.monotonic()+min(180.,max(15.,limit))
    epoch=world.executor.epoch
    while True:
        if not current():
            stopped=await world.stop_execution('AI请求已撤销，停止前往组合起点')
            return {'ok':False,'why':'前往组合起点途中请求已撤销，绘制未开始'+_stop_note(stopped)}
        if world.executor.epoch!=epoch:return {'ok':False,'why':'组合起点运动已被撤销或替换，绘制未开始'}
        if not world.executor.busy:break
        if time.monotonic()>=deadline:
            stopped=await world.stop_execution('组合起点运动超时')
            return {'ok':False,'why':'前往组合起点超时，绘制未开始'+_stop_note(stopped)}
        await asyncio.sleep(.02)
    snapshot=world.executor.snapshot()
    if snapshot.get('state')!='completed':
        return {'ok':False,'why':'组合起点运动未完成：'+str(snapshot.get('error') or snapshot.get('state') or '状态未知')}
    if not current():return {'ok':False,'why':'组合起点已到达，但请求已变化，绘制未开始'}
    if not world.joints_known():return {'ok':False,'why':'组合起点已到达，但关节反馈未知，绘制未开始'}
    q=world.current_joints()
    if len(q)!=6 or any(abs(a-b)>.05 for a,b in zip(q,joints)):
        return {'ok':False,'why':'组合起点运动结束但关节未到位，绘制未开始'}
    return {'ok':True}

async def run(world,drawing,plan,proposal):
    """World has consumed and verified this exact user's proposal before entry."""
    if world.settings.get('mode')!='sim' or world.real is not None:return {'ok':False,'why':'AI自动绘制组合仅用于仿真'}
    if drawing is None:return {'ok':False,'why':'绘制服务尚未就绪'}
    checked=skill_proposal(plan,world.points.list(),world.model,world.settings,world._ai_resources())
    if checked['skill_plan']!=proposal.get('skill_plan'):return {'ok':False,'why':'绘制技能与提案不同'}
    def current():
        return (world.settings.get('mode')=='sim' and world.real is None and world.session_id==proposal['session']
                and world._ai_generation==proposal['ai_generation'] and world.executor.epoch==proposal['epoch']
                and proposal.get('request_id') not in getattr(world,'_ai_cancelled_requests',[]))
    if not current():return {'ok':False,'why':'AI请求已变化，未开始移动'}
    if drawing.worker and not drawing.worker.done():return {'ok':False,'why':'绘制预检仍在处理，请先停止原任务'}
    if drawing.phase in ('running','paused'):return {'ok':False,'why':'绘制任务仍占用机器人'}
    switched=world.ensure_sim_task_tool('drawing')
    if not switched.get('ok'):return switched
    if not current():return {'ok':False,'why':'换笔期间请求已撤销，未开始移动'}
    prefix=copy.deepcopy(checked['plan'][0]['joints']) if len(checked['plan'])==2 else None
    # Preflight first, move second: a rejected outline must never drag the arm
    # to the prefix point. preview() binds that prefix as its start, and moving
    # does not change the plan context (epoch only advances on revoke), so the
    # checked plan stays valid until run() asks the same feedback to match.
    try:
        ticket=drawing.preview(drawing_config(world,checked['skill_plan']['shape']),prefix_joints=copy.deepcopy(prefix))
    except (ValueError,RuntimeError) as exc:
        return {'ok':False,'why':'绘制预检未通过，未开始移动：'+str(exc)}
    key=ticket['plan_id']
    try:
        while drawing.worker and not drawing.worker.done():
            if not current():
                await drawing.cancel({'plan_id':key})
                return {'ok':False,'why':'AI请求已撤销，整份任务未开始移动'}
            await asyncio.sleep(.02)
        if drawing.worker:await drawing.worker
        state=drawing.state()
        if not current():
            await drawing.cancel({'plan_id':key})
            return {'ok':False,'why':'请求已变化，整份任务未执行'}
        if state['plan_id']!=key or state['phase']!='ready':
            return {'ok':False,'why':state.get('why') or '完整组合路径未通过，起点也未移动'}
        if prefix is not None:
            approach=await _approach(world,prefix,current)
            if not approach.get('ok'):
                await drawing.cancel({'plan_id':key})
                return {**approach,'why':'完整路径已通过预检，但'+str(approach.get('why') or '前往组合起点失败')}
        result=drawing.run({'plan_id':key})
        return {**result,'ok':True,'skill_plan':checked['skill_plan'],'drawing_plan_id':key,'execution_kind':'drawing'}
    except (ValueError,RuntimeError) as exc:
        return {'ok':False,'why':'绘制未启动：'+str(exc)}
