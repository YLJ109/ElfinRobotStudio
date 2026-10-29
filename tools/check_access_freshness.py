"""Host、输入模型和服务端原始采集时效回归；禁止访问硬件。"""
from _offline_guard import require_offline_copy
require_offline_copy()

from collections import deque
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from backend.access import local_peer, trusted_host
from backend.core.cameras import CameraManager
from backend.schemas import RealCommand
import backend.main as main


def camera(captured):
    result = CameraManager()
    result._active = {"session": "camera-a", "state": "streaming"}
    result._frame_history = {"color": deque([("camera-a", 10, captured)], maxlen=512)}
    return result


class AccessTests(unittest.TestCase):
    def test_remote_http_requires_header_not_url_credential(self):
        world = SimpleNamespace(log=SimpleNamespace(add=Mock()), settings={"mode": "sim"})
        with patch.dict("os.environ", {"HUYAN_TOKEN": "test-boundary-token"}), patch.object(main, "world", world):
            client = TestClient(main.app, base_url="http://127.0.0.1:8710", client=("192.0.2.1", 1234))
            self.assertEqual(client.get("/api/settings?token=test-boundary-token").status_code, 403)
            self.assertEqual(client.get("/api/settings", headers={"x-huayan-token": "test-boundary-token"}).status_code, 200)
            self.assertEqual(client.get("/api/settings", headers={"x-huayan-token": "wrong"}).status_code, 403)

    def test_remote_websocket_does_not_authenticate_from_url(self):
        with patch.dict("os.environ", {"HUYAN_TOKEN": "test-boundary-token"}):
            client = TestClient(main.app, base_url="http://127.0.0.1:8710", client=("192.0.2.1", 1234))
            with self.assertRaises(WebSocketDisconnect) as error:
                with client.websocket_connect("/ws?token=test-boundary-token"):
                    self.fail("URL 里的凭据不能通过握手")
            self.assertEqual(error.exception.code, 4403)

    def test_rebinding_host_is_rejected_even_from_loopback(self):
        with patch.dict("os.environ", {"HUYAN_ALLOWED_HOSTS": ""}):
            for host in ("evil.invalid:8710", "localhost.evil.invalid", "127.0.0.1@evil.invalid", "[::1]:0"):
                self.assertFalse(trusted_host(host, "127.0.0.1"), host)
            for host in ("127.0.0.1:8710", "localhost:5173", "[::1]:8710"):
                self.assertTrue(trusted_host(host, "127.0.0.1"), host)
            self.assertTrue(trusted_host("testserver", "testclient"))
            self.assertFalse(trusted_host("testserver", "127.0.0.1"))

    def test_unknown_network_peer_is_not_local(self):
        self.assertFalse(local_peer("not-an-ip"))
        self.assertFalse(local_peer(""))
        self.assertTrue(local_peer("127.0.0.1"))
        self.assertTrue(local_peer("::1"))

    def test_configured_host_is_explicit(self):
        with patch.dict("os.environ", {"HUYAN_ALLOWED_HOSTS": "workstation.example,192.0.2.10"}):
            self.assertTrue(trusted_host("workstation.example:8710"))
            self.assertFalse(trusted_host("other.example:8710"))

    def test_http_and_websocket_reject_rebinding_before_handler(self):
        client = TestClient(main.app, base_url="http://evil.invalid:8710")
        self.assertEqual(client.get("/api/state").status_code, 400)
        with self.assertRaises(WebSocketDisconnect) as error:
            with client.websocket_connect("/ws", headers={"host": "evil.invalid:8710"}):
                self.fail("未知 Host 不应成功建立连接")
        self.assertEqual(error.exception.code, 4403)

    def test_raw_input_schema_rejects_missing_name_and_bad_params(self):
        client = TestClient(main.app, base_url="http://127.0.0.1:8710")
        for payload in ({}, {"name": "ReadActPos", "params": "0"},
                        {"name": "ReadActPos", "params": [True]},
                        {"name": "GrpStop;GrpEnable", "params": []}):
            self.assertEqual(client.post("/api/real/ask", json=payload).status_code, 422)


class CaptureFreshnessTests(unittest.TestCase):
    def test_original_capture_age_includes_transport_and_rejects_unknown_session(self):
        manager = camera(10.)
        with patch("backend.core.cameras.time.monotonic", return_value=10.45):
            self.assertAlmostEqual(manager.observation_age_ms("camera-a", 10), 450.)
            self.assertIsNone(manager.observation_age_ms("old-camera", 10))
            self.assertIsNone(manager.observation_age_ms("camera-a", 11))

    def test_arrival_cannot_renew_an_old_observation(self):
        manager = camera(time.monotonic() - .45)
        world = SimpleNamespace(real_hand_follow_action=AsyncMock(), _request_real_jog_stop=Mock())
        with patch.object(main, "cameras", manager), patch.object(main, "world", world):
            client = TestClient(main.app, base_url="http://127.0.0.1:8710")
            response = client.post("/api/real/hand-follow/sample", json={
                "session": "camera-a", "frame_seq": 10, "age_ms": 0, "hand_present": True})
        self.assertEqual(response.status_code, 422)
        world.real_hand_follow_action.assert_not_awaited()
        world._request_real_jog_stop.assert_called_once()

    def test_valid_sample_uses_server_age_not_client_age(self):
        manager = camera(time.monotonic() - .02)
        action = AsyncMock(return_value={"ok": True})
        world = SimpleNamespace(real_hand_follow_action=action)
        with patch.object(main, "cameras", manager), patch.object(main, "world", world), \
                patch.object(main, "_hand_camera_state", return_value={}):
            client = TestClient(main.app, base_url="http://127.0.0.1:8710")
            response = client.post("/api/real/hand-follow/sample", json={
                "session": "camera-a", "frame_seq": 10, "age_ms": 0, "hand_present": True})
        self.assertEqual(response.status_code, 200)
        payload = action.await_args.args[1]
        self.assertGreaterEqual(payload["age_ms"], 19.99)
        self.assertNotIn("frame_seq", payload)


class ProcessingFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_stamp_is_not_renewed_by_server_processing(self):
        from backend.core.real_jog_follow import RealJogFollow
        stop, move = AsyncMock(return_value=True), AsyncMock(return_value=True)
        driver = RealJogFollow(lambda: {}, AsyncMock(), AsyncMock(), move, stop, clock=lambda: 10.45)
        driver._guard = lambda *_args, **_kwargs: None
        driver._read = lambda: {}
        driver.active, driver.session = True, "camera-a"
        response = driver.sample({"token": driver.token, "session": "camera-a", "seq": 1,
                                  "age_ms": 0, "hand_present": True, "u": .5, "v": .5,
                                  "hand_open": .5}, captured_at=10.)
        self.assertFalse(response["ok"])
        self.assertFalse(driver.active)
        await driver.stop()
        move.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
