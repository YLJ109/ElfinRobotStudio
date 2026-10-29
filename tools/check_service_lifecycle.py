"""服务控制权与订阅资源回归；仅在隔离副本运行。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend.main as main
from starlette.websockets import WebSocketDisconnect


class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_world_shutdown_requests_physical_stop_before_transport_close(self):
        from backend.core.world import World
        world = World()
        order = []
        async def stop_motion(reason):
            order.append("motion-stop")
            return {"ok": False, "settled": False}
        async def close_transport():
            order.append("close-link")
        world.real = SimpleNamespace(stop=close_transport)
        with patch.object(world, "stop_execution", side_effect=stop_motion):
            await world.stop()
        self.assertEqual(order, ["motion-stop", "close-link"])

    async def test_first_packet_disconnect_releases_subscription(self):
        queue = object()
        world = SimpleNamespace(subscribe=Mock(return_value=queue), unsubscribe=Mock(),
                                snapshot=Mock(return_value={}))
        socket = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"),
                                 headers={"host": "127.0.0.1:8710"}, query_params={},
                                 accept=AsyncMock(),
                                 send_json=AsyncMock(side_effect=WebSocketDisconnect()))
        with patch.object(main, "world", world):
            try:
                await main.ws_endpoint(socket)
            except WebSocketDisconnect:
                pass
        world.unsubscribe.assert_called_once_with(queue)

    async def test_shutdown_stops_control_before_camera_cleanup(self):
        order = []
        async def stop():
            order.append("control")
        def close():
            order.append("camera")
        with patch.object(main.world, "stop", side_effect=stop), \
                patch.object(main.cameras, "close", side_effect=close):
            await main._shutdown()
        self.assertEqual(order, ["control", "camera"])


class LeaseTests(unittest.TestCase):
    def test_second_process_refused_and_release_allows_restart(self):
        from backend.core.service_lease import ServiceLease
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.lock"
            lease = ServiceLease(path)
            command = [sys.executable, "-B", "-c",
                       "from backend.core.service_lease import ServiceLease; "
                       "import sys; lease=ServiceLease(sys.argv[1]); lease.acquire(); lease.release()", str(path)]
            lease.acquire()
            try:
                refused = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=10)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("CONTROL_OWNER_BUSY", refused.stderr)
            finally:
                lease.release()
            accepted = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            lease.release()  # 正确：重复释放无副作用，不删除锁文件。

    def test_startup_failure_releases_lease(self):
        lease = SimpleNamespace(acquire=Mock(), release=Mock())
        with patch.object(main, "service_lease", lease), \
                patch.object(main.world, "start", new=AsyncMock(side_effect=RuntimeError("startup failed"))), \
                patch.object(main.world, "stop", new=AsyncMock()) as stop:
            with self.assertRaises(RuntimeError):
                asyncio.run(main._startup())
        lease.release.assert_called_once()
        stop.assert_awaited_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
