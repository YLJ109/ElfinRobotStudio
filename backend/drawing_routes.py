"""Per-app checked drawing API; no import-time resources or device operations."""
from fastapi import APIRouter, Body, HTTPException
from .application.drawing import Drawing

def create_drawing_router(world):
    router=APIRouter(prefix='/api/drawing',tags=['drawing']);service=Drawing(world);router.drawing=service
    def error(exc):raise HTTPException(status_code=409 if isinstance(exc,RuntimeError) else 422,detail=str(exc))
    @router.get('/state')
    async def state():return service.state()
    @router.post('/preview')
    async def preview(payload:dict=Body(...)):
        try:return service.preview(payload)
        except (ValueError,RuntimeError) as exc:error(exc)
    @router.post('/run')
    async def run(payload:dict=Body(...)):
        try:return service.run(payload)
        except (ValueError,RuntimeError) as exc:error(exc)
    @router.post('/cancel')
    async def cancel(payload:dict=Body(...)):
        try:return await service.cancel(payload)
        except (ValueError,RuntimeError) as exc:error(exc)
    @router.post('/pause')
    async def pause(payload:dict=Body(...)):
        try:return await service.pause(payload)
        except (ValueError,RuntimeError) as exc:error(exc)
    @router.post('/resume')
    async def resume(payload:dict=Body(...)):
        try:return await service.resume(payload)
        except (ValueError,RuntimeError) as exc:error(exc)
    return router
