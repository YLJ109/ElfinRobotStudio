"""Read-only ArUco routes; deliberately independent of main and robot state.

Integration (not performed here)::

    from .vision_routes import create_vision_router
    app.include_router(create_vision_router(cameras=cameras))

``world`` is accepted for composition compatibility but never inspected or used.
No camera is scanned, opened, closed or configured. Uploads are base64 JPEG/PNG,
never URLs or paths. Coordinates are image pixels, NOT robot coordinates.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import struct
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

MAX_IMAGE_BYTES = 1024 * 1024  # 正确：Base64 JSON 仍低于全局2MiB请求上限。
MAX_PIXELS = 2_000_000
MAX_SIDE = 2048
MAX_BODY_BYTES = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 1024
DICTIONARIES = ("DICT_4X4_50", "DICT_5X5_100", "DICT_6X6_250", "DICT_7X7_50")
_SLOT = threading.Lock()  # Shared across router instances in this process.


class VisionError(Exception):
    def __init__(self, status, code, why):
        self.status, self.code, self.why = status, code, why


def _limits():
    return dict(max_image_bytes=MAX_IMAGE_BYTES, max_pixels=MAX_PIXELS,
                max_side=MAX_SIDE, concurrency=1, formats=["image/jpeg", "image/png"])


def _load_opencv():
    try:
        import cv2
        import numpy as np
    except (ImportError, OSError):
        raise VisionError(503, "capability_unavailable", "OpenCV / NumPy 不可用；未安装任何依赖") from None
    aruco = getattr(cv2, "aruco", None)
    modern = callable(getattr(aruco, "ArucoDetector", None))
    legacy = callable(getattr(aruco, "detectMarkers", None))
    params = callable(getattr(aruco, "DetectorParameters", None)) or callable(getattr(aruco, "DetectorParameters_create", None))
    if not aruco or not callable(getattr(aruco, "getPredefinedDictionary", None)) or not params or not (modern or legacy):
        raise VisionError(503, "capability_unavailable", "当前 OpenCV 缺少可用的 aruco API；未安装任何依赖")
    return cv2, np


def _dimensions(data):
    """Read only bounded PNG/JPEG headers before any native image allocation."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise VisionError(413, "image_too_large", "图片为空或超过 1 MiB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 33 or data[8:16] != b"\x00\x00\x00\rIHDR":
            raise VisionError(422, "invalid_image", "PNG 头损坏")
        width, height = struct.unpack(">II", data[16:24])
        mime = "image/png"
    elif data.startswith(b"\xff\xd8"):
        offset, width, height = 2, 0, 0
        sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset < len(data):
            if data[offset] != 0xFF:
                break
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xDA, 0xD9}:
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(data):
                break
            length = int.from_bytes(data[offset:offset + 2], "big")
            if length < 2 or offset + length > len(data):
                break
            if marker in sof:
                if length < 8:
                    break
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                break
            offset += length
        if not width or not height:
            raise VisionError(422, "invalid_image", "JPEG 头损坏或缺少尺寸")
        mime = "image/jpeg"
    else:
        raise VisionError(415, "unsupported_image", "仅支持 JPEG / PNG 图片")
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE and width * height <= MAX_PIXELS):
        raise VisionError(413, "pixel_limit", "图片最长边须 ≤ 2048，且总像素 ≤ 200 万")
    return width, height, mime


def _payload(value):
    if not isinstance(value, dict):
        raise VisionError(400, "invalid_request", "请求必须是 JSON 对象")
    source = value.get("source")
    if not isinstance(source, str):
        raise VisionError(400, "invalid_request", "source 必须是 upload 或 camera")
    allowed = {"source", "dictionary", "image_base64"} if source == "upload" else {"source", "dictionary", "session", "stream"}
    if source not in {"upload", "camera"} or set(value) - allowed:
        raise VisionError(400, "invalid_request", "仅接受上传图片或现有相机会话；不接受 URL、路径或其他字段")
    dictionary = value.get("dictionary", DICTIONARIES[0])
    if not isinstance(dictionary, str) or dictionary not in DICTIONARIES:
        raise VisionError(400, "invalid_dictionary", "不支持的 ArUco 字典")
    if source == "upload":
        encoded = value.get("image_base64")
        if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise VisionError(413, "image_too_large", "上传图片为空或超过 1 MiB")
    else:
        session, stream = value.get("session"), value.get("stream", "color")
        if not isinstance(session, str) or not (1 <= len(session) <= 128) or stream not in ("color", "infrared"):
            raise VisionError(400, "invalid_session", "需要现有相机会话及 color / infrared 流")
    return value, dictionary


def _preview_bytes(data, mime):
    """Drop orientation metadata without recompressing or changing pixel geometry."""
    if mime == "image/png":
        parts, offset = [data[:8]], 8
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset:offset + 4], "big")
            end = offset + 12 + length
            if end > len(data):
                raise VisionError(422, "invalid_image", "PNG 数据损坏")
            kind = data[offset + 4:offset + 8]
            if kind != b"eXIf":
                parts.append(data[offset:end])
            offset = end
            if kind == b"IEND":
                return b"".join(parts)
        raise VisionError(422, "invalid_image", "PNG 缺少结束块")
    parts, offset = [data[:2]], 2
    while offset + 4 <= len(data):
        start = offset
        if data[offset] != 0xFF:
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xDA, 0xD9}:
            return b"".join(parts) + data[start:]
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            parts.append(data[start:offset])
            continue
        length = int.from_bytes(data[offset:offset + 2], "big")
        end = offset + length
        if length < 2 or end > len(data):
            break
        if marker != 0xE1:  # APP1 may contain EXIF/XMP orientation.
            parts.append(data[start:end])
        offset = end
    raise VisionError(422, "invalid_image", "JPEG 数据损坏")


def _work(value, cameras):
    if value is None:
        cv, _ = _load_opencv()
        dictionaries = [name for name in DICTIONARIES if hasattr(cv.aruco, name)]
        if not dictionaries:
            raise VisionError(503, "capability_unavailable", "当前 aruco 缺少支持的字典")
        return dict(ok=True, status="available", available=True, dictionaries=dictionaries,
                    limits=_limits(), coordinate_space="image_pixels", calibrated=False, read_only=True)
    payload, dictionary_name = _payload(value)
    cv, np = _load_opencv()
    aruco = cv.aruco
    if not hasattr(aruco, dictionary_name):
        raise VisionError(503, "capability_unavailable", "当前 aruco 不支持所选字典")
    frame = None
    if payload["source"] == "upload":
        try:
            data = base64.b64decode(payload["image_base64"], validate=True)
        except (binascii.Error, ValueError):
            raise VisionError(400, "invalid_base64", "图片 base64 无效") from None
    else:
        if cameras is None:
            raise VisionError(409, "camera_unavailable", "未接入相机管理器；可上传小图")
        packet = cameras.stream_frame(payload.get("stream", "color"), payload["session"])
        if packet is None:
            raise VisionError(409, "frame_unavailable", "没有已开启的有效图像帧，或会话已变化")
        seq, stamp, data = packet
        if not isinstance(stamp, (float, int)) or not math.isfinite(stamp) or not -1 <= time.time() - stamp <= 3:
            raise VisionError(409, "frame_unavailable", "相机帧已过期")
        frame = dict(session=payload["session"], stream=payload.get("stream", "color"), seq=seq, captured_at=stamp)
    width, height, mime = _dimensions(data)
    # Ignore EXIF orientation so raw pixel corners and the returned preview agree.
    gray = cv.imdecode(np.frombuffer(data, dtype=np.uint8), cv.IMREAD_GRAYSCALE | cv.IMREAD_IGNORE_ORIENTATION)
    if gray is None or tuple(gray.shape[:2]) != (height, width):
        raise VisionError(422, "invalid_image", "图片无法解码或尺寸不一致")
    preview_data = _preview_bytes(data, mime)
    dictionary = aruco.getPredefinedDictionary(getattr(aruco, dictionary_name))
    parameters = aruco.DetectorParameters() if callable(getattr(aruco, "DetectorParameters", None)) else aruco.DetectorParameters_create()
    if callable(getattr(aruco, "ArucoDetector", None)):
        corners, ids, _ = aruco.ArucoDetector(dictionary, parameters).detectMarkers(gray)
    else:
        corners, ids, _ = aruco.detectMarkers(gray, dictionary, parameters=parameters)
    markers = []
    if ids is not None:
        for marker_id, points in zip(ids.flatten(), corners):
            points = points.reshape(4, 2).tolist()
            if not all(math.isfinite(v) and 0 <= v < bound for point in points for v, bound in zip(point, (width, height))):
                raise VisionError(422, "invalid_detection", "检测器返回了无效边界")
            markers.append(dict(id=int(marker_id), corners=points,
                                centroid=[sum(p[i] for p in points) / 4 for i in (0, 1)]))
    return dict(ok=True, status="detected" if markers else "empty", markers=markers,
                source=payload["source"], frame=frame, dictionary=dictionary_name,
                coordinate_space="image_pixels", calibrated=False, read_only=True,
                image=dict(width=width, height=height, mime=mime,
                           base64=base64.b64encode(preview_data).decode("ascii")))


def create_vision_router(world=None, cameras=None):
    """Compose with ``cameras=existing_manager``; world is intentionally unused.

    GET /api/vision/markers/capabilities; POST /api/vision/markers/detect.
    One non-queued operation per process. Request cancellation does not release
    the slot until the native worker has actually finished.
    """
    router = APIRouter(prefix="/api/vision/markers", tags=["vision-readonly"])

    async def execute(request=None):
        if not _SLOT.acquire(blocking=False):
            return JSONResponse(dict(ok=False, status="busy", why="已有视觉任务正在处理"), 429,
                                headers={"Retry-After": "1", "Cache-Control": "no-store"})
        transferred = False
        try:
            value = None
            if request is not None:
                if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise VisionError(415, "invalid_content_type", "需要 application/json")
                chunks, size = [], 0
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_BODY_BYTES:
                            raise VisionError(413, "body_too_large", "请求超过图片上传限制")
                        chunks.append(chunk)
                try:
                    value = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError, RecursionError):
                    raise VisionError(400, "invalid_json", "JSON 请求无效") from None
                _payload(value)  # Reject JSON null and unknown fields before dispatch.
            task = asyncio.create_task(asyncio.to_thread(_work, value, cameras))

            def finished(done):
                _SLOT.release()
                if not done.cancelled():
                    done.exception()  # Retrieve errors even after HTTP cancellation.

            task.add_done_callback(finished)
            transferred = True
            result = await asyncio.shield(task)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except VisionError as exc:
            return JSONResponse(dict(ok=False, status=exc.code, why=exc.why,
                                     available=False if exc.code == "capability_unavailable" else None,
                                     limits=_limits()), exc.status, headers={"Cache-Control": "no-store"})
        except TimeoutError:
            return JSONResponse(dict(ok=False, status="request_timeout", why="上传超时，请重试"), 408)
        except Exception:
            return JSONResponse(dict(ok=False, status="detection_error", why="视觉检测失败；未执行机器人操作"), 500)
        finally:
            if not transferred:
                _SLOT.release()

    @router.get("/capabilities")
    async def capabilities():
        return await execute()

    @router.post("/detect")
    async def detect(request: Request):
        return await execute(request)

    return router
