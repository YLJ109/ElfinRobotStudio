"""在解析 JSON 前限制实际请求体；不能只相信 Content-Length。"""
import asyncio
import time
from starlette.responses import JSONResponse


class RequestSizeLimit:
    def __init__(self, app, max_bytes=2 * 1024 * 1024, read_timeout=10.0):
        self.app, self.max_bytes, self.read_timeout = app, max_bytes, read_timeout

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        raw_length = dict(scope.get("headers", [])).get(b"content-length")
        status, why = None, ""
        if raw_length is not None:
            if not raw_length.isdigit():
                status, why = 400, "无效的请求长度"
            elif (len(raw_length.lstrip(b"0")) > len(str(self.max_bytes))
                  or int(raw_length.lstrip(b"0") or b"0") > self.max_bytes):
                status, why = 413, "请求体超过2MiB上限"
        if status:
            return await JSONResponse({"ok": False, "why": why}, status_code=status)(scope, receive, send)
        chunks, total = [], 0
        deadline = time.monotonic() + self.read_timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                packet = await asyncio.wait_for(receive(), remaining)
                if packet["type"] == "http.disconnect":
                    return
                body = packet.get("body", b"")
                total += len(body)
                if total > self.max_bytes:
                    return await JSONResponse({"ok": False, "why": "请求体超过2MiB上限"}, status_code=413)(scope, receive, send)
                chunks.append(body)
                if not packet.get("more_body", False):
                    break
        except asyncio.TimeoutError:
            return await JSONResponse({"ok": False, "why": "请求体接收超时"}, status_code=408)(scope, receive, send)
        received = False

        async def replay():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
