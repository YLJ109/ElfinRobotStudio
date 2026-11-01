"""Bounded experimental upload transport; not a WebRTC or pairing service."""
import asyncio
import math
import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .vision_routes import _dimensions, VisionError, MAX_IMAGE_BYTES


def create_pipeline_router(pipeline, *, on_revoke):
    router = APIRouter(prefix='/api/vision/pipeline',tags=['vision-pipeline'])
    decoding = asyncio.Lock()
    last_upload = [-float('inf')]

    @router.get('')
    async def state():
        return pipeline.snapshot()

    @router.post('/config')
    async def configure(value: dict):
        if set(value)-{'enabled','source','session'} or 'enabled' not in value:
            raise ValueError('检测配置字段无效')
        # Validate and switch before revoking; invalid requests cannot cancel
        # another operator's existing detector configuration.
        current=pipeline.snapshot()
        # 重复提交同一份配置必须是幂等的：既不重置 epoch，也不撤销正在进行的
        # 跟随运动。前端轮询、页面恢复和组件重挂都会反复提交同一份 enabled
        # 配置，每次都撤销运动会把跟随当场打断。
        if (bool(value.get('enabled'))==bool(current.get('enabled'))
                and value.get('source')==current.get('source')
                and value.get('session')==current.get('session')):
            return current
        result = pipeline.configure(**value)
        # "把检测拉起来"不是"换检测源"。启动跟随的流程就是先到中心、再开
        # 检测、再等画面；如果开检测本身要撤销，正好把用户刚点的开始作废，
        # 界面表现就是"摄像头开了，跟随却被取消"。
        # 只有真的失去检测源才撤销：关掉检测，或检测还开着却换了相机会话。
        opened=bool(value.get('enabled'))
        switched=bool(current.get('enabled')) and value.get('session')!=current.get('session')
        if not opened or switched:
            await on_revoke()
        return result

    def decode(data):
        import cv2
        import numpy as np
        width,height,mime = _dimensions(data)
        if width*height > pipeline.MAX_PIXELS:
            raise VisionError(413,'pixel_limit','检测图像总像素不能超过1280×720')
        bgr = cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_COLOR)
        if bgr is None or bgr.shape[:2] != (height,width):
            raise ValueError('图像解码失败或尺寸不符')
        return bgr[:,:,::-1].copy(),mime

    @router.post('/frame')
    async def frame(request: Request):
        current = pipeline.snapshot()
        try:
            epoch = int(request.headers.get('x-vision-epoch',''))
            seq = int(request.headers.get('x-frame-seq',''))
            session = request.headers.get('x-vision-session','')
            captured_text = request.headers.get('x-capture-time')
            captured = float(captured_text) if captured_text is not None else None
            if captured is not None and not math.isfinite(captured):
                raise ValueError('采集时间无效')
        except (ValueError,OverflowError):
            return JSONResponse({'ok':False,'why':'帧身份或采集时间无效'},422)
        if (not current['enabled'] or current['source'] not in ('remote','virtual')
                or (session,epoch) != (current['session'],current['epoch'])):
            return JSONResponse({'ok':False,'why':'检测来源已失效或不接受上传'},409)
        if request.headers.get('content-type','').split(';')[0] not in ('image/jpeg','image/png'):
            return JSONResponse({'ok':False,'why':'实验上传仅接收JPEG或PNG二进制'},415)
        now = time.monotonic()
        if decoding.locked() or now-last_upload[0] < 1/15:
            return JSONResponse({'ok':False,'why':'上传过快，请保留最新帧'},429)
        last_upload[0] = now
        async with decoding:
            try:
                body = bytearray()
                async with asyncio.timeout(3.):
                    async for chunk in request.stream():
                        if len(body)+len(chunk)>MAX_IMAGE_BYTES:
                            return JSONResponse({'ok':False,'why':'图像超过1MiB'},413)
                        body.extend(chunk)
                image,mime = await asyncio.to_thread(decode,bytes(body))
                if request.headers.get('content-type','').split(';')[0] != mime:
                    raise ValueError('图像内容与类型不符')
                return pipeline.submit(image,source=current['source'],session=session,
                    epoch=epoch,seq=seq,captured_at=captured,clock_domain='remote',received_at=now)
            except VisionError as exc:
                return JSONResponse({'ok':False,'why':exc.why},exc.status)
            except ValueError as exc:
                return JSONResponse({'ok':False,'why':str(exc)},422)
            except TimeoutError:
                return JSONResponse({'ok':False,'why':'图像接收超时'},408)
    return router
