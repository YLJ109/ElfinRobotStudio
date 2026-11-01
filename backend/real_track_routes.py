"""Role-scoped tracking API over the single qualified real follow controller.

No alternate driver, commissioning endpoint or implicit connect/enable exists.
Capture validation is the same server-owned observation lookup as palm follow.
"""
from fastapi import APIRouter,Body
from fastapi.responses import JSONResponse


def create_real_track_router(world,verified_sample,camera_state):
    router=APIRouter(prefix='/api/real/hand-track')

    @router.get('/status')
    async def status():
        state=world.real_jog_follow.snapshot(role='track')
        return dict(ok=True,state=state,available=state['available'],why=state['why'],
                    blockers=state['blockers'],motion_enabled=state['active'])

    @router.post('/{action}')
    async def action(action:str,payload:dict=Body(default={})):
        if action=='status':
            return JSONResponse({'ok':False,'why':'状态查询请使用 GET'},status_code=405,headers={'Allow':'GET'})
        if action not in ('configure','start','sample','stop'):
            return JSONResponse({'ok':False,'why':'未知真机追踪动作'},status_code=404)
        controller=world.real_jog_follow
        # A stale follow/track page must not stop the other role's owner,
        # including when its camera stamp is invalid.
        if action!='configure' and getattr(controller,'role','follow')!='track':
            return {'ok':False,'why':'当前运动属于另一互动模式','state':controller.snapshot(role='track')}
        try:
            verified,captured=verified_sample(action,payload)
        except ValueError:
            if (payload.get('token')==controller.token and payload.get('session')==controller.session):
                world._request_real_jog_stop('手掌观测帧已过期，停止追踪')
            raise
        return await world.real_hand_follow_action(action,verified,camera_state(),captured_at=captured,role='track')
    return router
