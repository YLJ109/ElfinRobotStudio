"""视觉工件接口；独立于World和机器人指令。"""
import asyncio
import base64
import binascii
import json
import threading
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .vision_routes import MAX_BODY_BYTES, MAX_IMAGE_BYTES, VisionError


def create_workspace_vision_router(engine):
    router=APIRouter(prefix='/api/vision/workspace',tags=['vision-workspace-readonly'])
    slot=threading.Lock()

    async def body(request):
        if request.headers.get('content-type','').split(';')[0]!='application/json':
            raise VisionError(415,'invalid_content_type','需要JSON请求')
        chunks=[];size=0
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_BODY_BYTES: raise VisionError(413,'too_large','视觉请求体过大')
                chunks.append(chunk)
        try: value=json.loads(b''.join(chunks))
        except (ValueError,UnicodeError,RecursionError): raise ValueError('JSON无效') from None
        if not isinstance(value,dict): raise ValueError('请求必须是对象')
        return value

    async def execute(request, operation):
        if not slot.acquire(blocking=False): return JSONResponse({'ok':False,'why':'视觉配置任务繁忙'},429)
        transferred=False
        try:
            value=await body(request)
            task=asyncio.create_task(asyncio.to_thread(operation,value))
            def finished(done):
                slot.release()
                if not done.cancelled(): done.exception()
            task.add_done_callback(finished);transferred=True
            return JSONResponse(await asyncio.shield(task))
        except VisionError as exc: return JSONResponse({'ok':False,'why':exc.why},exc.status)
        except (ValueError,TypeError,binascii.Error) as exc: return JSONResponse({'ok':False,'why':str(exc)},422)
        except TimeoutError: return JSONResponse({'ok':False,'why':'视觉请求超时'},408)
        except (ImportError,OSError): return JSONResponse({'ok':False,'why':'视觉依赖或存储不可用'},503)
        except Exception: return JSONResponse({'ok':False,'why':'视觉任务未完成，请检查图像及标定数据'},500)
        finally:
            if not transferred: slot.release()

    @router.get('/state')
    async def state():
        return engine.snapshot()

    @router.post('/config')
    async def configure(request:Request):
        return await execute(request,engine.configure)

    @router.post('/calibrate')
    async def calibrate(request:Request):
        return await execute(request,engine.calibrate)

    @router.post('/image')
    async def image(request:Request):
        def upload(value):
            if set(value)!={'image_base64'}: raise ValueError('仅接受图片Base64，不接受路径或URL')
            encoded=value['image_base64']
            if not isinstance(encoded,str) or not encoded or len(encoded)>4*((MAX_IMAGE_BYTES+2)//3):
                raise VisionError(413,'too_large','图片须为1MiB以内JPEG/PNG')
            return engine.upload(base64.b64decode(encoded,validate=True))
        return await execute(request,upload)

    @router.post('/virtual')
    async def virtual(request:Request):
        return await execute(request,engine.virtual_frame)

    return router
