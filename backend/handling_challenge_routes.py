"""Handling game facade over the existing, single simulation execution core."""
from fastapi import APIRouter, Body
from .application.handling_challenge import HandlingChallenge


def create_handling_challenge_router(world):
    router=APIRouter(prefix='/api/challenge/handling')
    service=HandlingChallenge(world);router.challenge=service
    @router.get('')
    async def state():return service.state()
    @router.post('/setup')
    async def setup(payload:dict=Body(...)):return service.setup(payload)
    @router.post('/inspect')
    async def inspect(payload:dict=Body(...)):
        # 预检失败是业务结果（路径没过、场景变了），不是服务器故障：
        # 不接住就会变成 500，前端拿不到"为什么"。
        try:
            return await service.inspect(payload)
        except (ValueError,RuntimeError) as exc:
            return {'ok':False,'why':str(exc)}
    @router.post('/run')
    async def run(payload:dict=Body(...)):
        try:
            return service.run(payload)
        except (ValueError,RuntimeError) as exc:
            return {'ok':False,'why':str(exc)}
    @router.post('/cancel')
    async def cancel(payload:dict=Body(...)):
        if not service.challenge or payload.get('challenge_id')!=service.challenge['id']:
            return {'ok':False,'why':'关卡已变化，请先读取当前关卡'}
        return await service.cancel()
    return router
