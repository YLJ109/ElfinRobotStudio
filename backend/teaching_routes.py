"""只存储仿真示教草稿；不拥有执行运动权限。"""
from fastapi import APIRouter,Body
from .core.task_studio import vector

def create_teaching_router(world):
    router=APIRouter(prefix='/api/teaching')
    @router.post('/batch')
    async def batch(payload:dict=Body(...)):
        if world.settings.get('mode')!='sim' or world.real is not None:
            raise ValueError('批量示教只保存仿真点位')
        rows=payload.get('points')
        if not isinstance(rows,list) or not 1<=len(rows)<=100:raise ValueError('每批需要1～100个点位')
        lower,upper=world.limits()
        checked=[]
        for row in rows:
            if not isinstance(row,dict):raise ValueError('点位格式错误')
            q=vector(row.get('joints'))
            if any(v<lo or v>hi for v,lo,hi in zip(q,lower,upper)):raise ValueError('点位关节超出硬限位')
            checked.append({'name':row.get('name'),'joints':q,'tcp':world.motion_model().pose_vec(q)})
        return {'ok':True,'items':world.points.batch_add(checked),'points':world.points.list()}
    return router
