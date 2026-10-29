"""私有入口隔离回归：注入假世界，绝不导入实际机器人世界。"""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response


class DesktopEntryTests(unittest.TestCase):
    def test_private_routes_precede_static_and_require_key(self):
        app = FastAPI()

        async def static(scope, receive, send):
            await Response(status_code=404)(scope, receive, send)

        app.mount('/', static)
        fake = ModuleType('backend.main')
        fake.app = app
        fake.world = SimpleNamespace(stop_execution=AsyncMock(return_value={'ok': False, 'settled': False}))
        fake.cameras = SimpleNamespace(close=Mock())
        server = SimpleNamespace(started=True, should_exit=False, run=Mock())
        listener = Mock()
        listener.getsockname.return_value = ('127.0.0.1', 12345)
        spec = importlib.util.spec_from_file_location('desktop_entry_under_test', Path(__file__).parents[1] / 'backend_entry.py')
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        key = 'test-key-' * 8
        with patch.dict(sys.modules, {'backend.main': fake}), patch.dict(os.environ, ELFIN_DESKTOP_KEY=key), \
                patch('uvicorn.Server', return_value=server), patch('socket.socket', return_value=listener):
            entry.main()
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/desktop/status').status_code, 403)
            headers = {'x-elfin-desktop-key': key}
            status = client.get('/api/desktop/status', headers=headers)
            self.assertEqual(status.status_code, 200)
            self.assertTrue(status.json()['ready'])
            self.assertEqual(client.post('/api/desktop/shutdown').status_code, 403)
            self.assertEqual(client.post('/api/desktop/shutdown', headers=headers).status_code, 409)
            self.assertFalse(server.should_exit)
            fake.cameras.close.assert_not_called()
            fake.world.stop_execution.return_value = {'ok': True, 'settled': True}
            self.assertEqual(client.post('/api/desktop/shutdown', headers=headers).status_code, 200)
            self.assertTrue(server.should_exit)
            fake.cameras.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
