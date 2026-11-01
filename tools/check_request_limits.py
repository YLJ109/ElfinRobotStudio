"""请求体实际长度和慢速上传边界；纯 ASGI，无网络与硬件。"""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.request_limits import RequestSizeLimit


class BodyTests(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, headers=(), packets=None, **options):
        self.app, self.send = AsyncMock(), AsyncMock()
        self.scope = {"type": "http", "headers": list(headers)}
        receive = AsyncMock(side_effect=packets or [{"type": "http.request", "body": b"{}"}])
        await RequestSizeLimit(self.app, max_bytes=4, **options)(self.scope, receive, self.send)

    async def test_declared_oversize_is_refused_without_app(self):
        await self.run_request(headers=[(b"content-length", b"5")])
        self.app.assert_not_awaited()
        self.assertEqual(self.send.call_args_list[0].args[0]["status"], 413)

    async def test_chunked_oversize_and_false_small_length_are_refused(self):
        for headers in ([], [(b"content-length", b"1")]):
            await self.run_request(headers=headers, packets=[{"type": "http.request", "body": b"123", "more_body": True},
                                                            {"type": "http.request", "body": b"45"}])
            self.app.assert_not_awaited()
            self.assertEqual(self.send.call_args_list[0].args[0]["status"], 413)

    async def test_valid_chunks_replay_exact_bytes_once(self):
        await self.run_request(packets=[{"type": "http.request", "body": b"12", "more_body": True},
                                        {"type": "http.request", "body": b"34"}, {"type": "http.disconnect"}])
        replay = self.app.call_args.args[1]
        self.assertEqual((await replay())["body"], b"1234")
        self.assertEqual((await replay())["type"], "http.disconnect")

    async def test_disconnect_does_not_enter_business_handler(self):
        await self.run_request(packets=[{"type": "http.disconnect"}])
        self.app.assert_not_awaited()

    async def test_invalid_content_length_returns_400(self):
        await self.run_request(headers=[(b"content-length", b"-1")])
        self.assertEqual(self.send.call_args_list[0].args[0]["status"], 400)

    async def test_extreme_length_header_does_not_reach_unbounded_integer_conversion(self):
        await self.run_request(headers=[(b"content-length", b"9" * 5000)])
        self.assertEqual(self.send.call_args_list[0].args[0]["status"], 413)
        await self.run_request(headers=[(b"content-length", b"0" * 5000)])
        self.app.assert_awaited_once()

    async def test_slow_body_is_bounded(self):
        app, send = AsyncMock(), AsyncMock()
        async def receive():
            await asyncio.Event().wait()
        await RequestSizeLimit(app, read_timeout=.01)({"type": "http", "headers": []}, receive, send)
        app.assert_not_awaited()
        self.assertEqual(send.call_args_list[0].args[0]["status"], 408)

    async def test_websocket_does_not_consume_handshake_packets(self):
        app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        scope = {"type": "websocket"}
        await RequestSizeLimit(app)(scope, receive, send)
        app.assert_awaited_once_with(scope, receive, send)
        receive.assert_not_awaited()

    async def test_only_exact_backup_preview_post_has_bounded_larger_body(self):
        for method,path,expected in [('POST','/api/backup/preview',True),('GET','/api/backup/preview',False),('POST','/api/backup/preview/other',False),('POST','/api/program/run',False)]:
            app,send=AsyncMock(),AsyncMock()
            scope={'type':'http','method':method,'path':path,'headers':[]}
            receive=AsyncMock(return_value={'type':'http.request','body':b'x'*(2*1024*1024+1)})
            await RequestSizeLimit(app)(scope,receive,send)
            self.assertEqual(app.await_count,1 if expected else 0)
            if not expected:self.assertEqual(send.call_args_list[0].args[0]['status'],413)
        app,send=AsyncMock(),AsyncMock()
        receive=AsyncMock(side_effect=[{'type':'http.request','body':b'x'*(16*1024*1024),'more_body':True},{'type':'http.request','body':b'x'*(16*1024*1024+1)}])
        await RequestSizeLimit(app)({'type':'http','method':'POST','path':'/api/backup/preview','headers':[(b'content-length',b'1')]},receive,send)
        app.assert_not_awaited();self.assertEqual(send.call_args_list[0].args[0]['status'],413)


if __name__ == "__main__":
    unittest.main(verbosity=2)
