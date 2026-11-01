"""Same-origin HTTPS signaling, camera-only capability on visitor paths."""
from urllib.parse import urlsplit
import json
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from .application.remote_camera import RemoteError

VISITOR_PATHS=frozenset('/api/vision/remote/'+p for p in ('join','offer','heartbeat','leave'))


def visitor_origin(request):
    origin=request.headers.get('origin','')
    expected='https://'+request.headers.get('host','')
    if request.url.scheme!='https' or origin!=expected:
        raise RemoteError('远程来源需要同源HTTPS页面',403)
    parsed=urlsplit(origin)
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise RemoteError('来源地址无效',403)
    return origin


def create_remote_camera_router(remote):
    router=APIRouter(prefix='/api/vision/remote',tags=['remote-camera'])
    router.remote=remote

    async def invoke(request,operation):
        try:
            origin=visitor_origin(request)
            token=request.headers.get('authorization','')
            token=token[7:] if token.startswith('Bearer ') else ''
            if request.url.path.endswith('/join'):
                value=await request.json()
                if not isinstance(value,dict) or set(value)!={'code'}:raise RemoteError('配对字段无效',422)
                return remote.join(value['code'],origin)
            remote.authorize(token,origin)
            if operation=='offer':return await remote.offer(token,origin,await request.json())
            if operation=='heartbeat':
                value=await request.json()
                if not isinstance(value,dict) or set(value)-{'clock'}:raise RemoteError('心跳字段无效',422)
                return await remote.beat(token,origin,value.get('clock'))
            return await remote.revoke()
        except RemoteError as exc:return JSONResponse({'ok':False,'why':str(exc)},status_code=exc.status)
        except (ValueError,TypeError):return JSONResponse({'ok':False,'why':'请求格式无效'},status_code=422)

    @router.get('/state')
    async def state():
        await remote.expire();return remote.snapshot()

    @router.post('/pair')
    async def pair(request:Request):
        try:
            raw=await request.body()
            value=json.loads(raw) if raw else {}
            if not isinstance(value,dict) or set(value)-{'detector'}:raise RemoteError('配对字段无效',422)
            return await remote.pair(value.get('detector','hand'))
        except RemoteError as exc:return JSONResponse({'ok':False,'why':str(exc)},status_code=exc.status)
        except (ValueError,TypeError):return JSONResponse({'ok':False,'why':'配对格式无效'},status_code=422)

    @router.post('/revoke')
    async def revoke():return await remote.revoke()

    @router.post('/join')
    async def join(request:Request):return await invoke(request,'join')

    @router.post('/offer')
    async def offer(request:Request):return await invoke(request,'offer')

    @router.post('/heartbeat')
    async def heartbeat(request:Request):return await invoke(request,'heartbeat')

    @router.post('/leave')
    async def leave(request:Request):return await invoke(request,'leave')
    return router
