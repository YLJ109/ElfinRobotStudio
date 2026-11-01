"""危险布尔参数必须保持 JSON 类型；拒绝把字符串 false 解释成开。"""
from _offline_guard import require_offline_copy
require_offline_copy()

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _app_test_support import application_for
from fastapi.testclient import TestClient
import backend.main as main


class ControlInputTests(unittest.TestCase):
    def test_mouse_route_has_separate_source_and_server_receive_time(self):
        world = SimpleNamespace(hand_follow_action=AsyncMock(return_value={"ok": True}))
        with application_for(world=world) as test_app, patch.object(test_app.state.resources.cameras, 'observation_stamp') as camera_stamp:
            client = TestClient(test_app, base_url="http://127.0.0.1:8710")
            result = client.post('/api/sim/mouse-follow/sample', json={
                'session': 'mouse-offline-contract', 'age_ms': -9999, 'frame_seq': 123})
            self.assertEqual(result.status_code, 200)
            args, kwargs = world.hand_follow_action.await_args
            self.assertEqual(args[1]['age_ms'], 0.)
            self.assertNotIn('frame_seq', args[1])
            self.assertIsNone(args[2])
            self.assertEqual(kwargs['input_kind'], 'mouse')
            self.assertGreater(kwargs['captured_at'], 0)
            camera_stamp.assert_not_called()
            self.assertIn(client.post('/api/real/mouse-follow/sample', json={}).status_code, (404, 405))

    def test_numeric_control_fields_do_not_accept_boolean_or_text_aliases(self):
        client = TestClient(main.app, base_url="http://127.0.0.1:8710")
        for value in (True, False, "1"):
            for route, body in (("movej", {"joints": [value, 0, 0, 0, 0, 0]}),
                                ("io/enddo", {"bit": value, "val": 1}),
                                ("io/enddo", {"bit": 0, "val": value}),
                                ("jog", {"axis": 0, "direction": value, "on": False})):
                with self.subTest(route=route, value=value):
                    self.assertEqual(client.post("/api/"+route, json=body).status_code, 422)
            result = client.post("/api/points", json={"name": "bad", "joints": [value, 0, 0, 0, 0, 0]})
            self.assertFalse(result.json()["ok"])

    def test_io_confirmation_requires_actual_json_boolean(self):
        client = TestClient(main.app, base_url="http://127.0.0.1:8710")
        for value in ("true", 1, "false", None):
            self.assertEqual(client.post("/api/calibrations/io", json={
                "name": "test", "group": "end", "direction": "output", "bit": 0,
                "confirmed": value}).status_code, 422)

    def test_bad_settings_do_not_persist_or_crash_after_restart(self):
        world = SimpleNamespace(update_settings=Mock())
        with application_for(world=world) as test_app:
            client = TestClient(test_app, base_url="http://127.0.0.1:8710")
            for value in (0, -1, 1.5, True, 100001):
                with self.subTest(value=value):
                    self.assertEqual(client.post("/api/settings", json={"log_keep": value}).status_code, 422)
            self.assertEqual(client.post("/api/settings", json={"fence": {
                "x": [-900, 900], "y": [-900, 900], "z": [-10, -1]}}).status_code, 422)
        world.update_settings.assert_not_called()

    def test_bad_import_type_returns_rejection_not_server_error(self):
        client = TestClient(main.app, base_url="http://127.0.0.1:8710")
        for value in ("scene", [1, 2], False, None):
            result = client.post("/api/scenes/import", json={"scene": value})
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()["ok"])

    def test_disk_failure_is_not_reported_as_log_clear_success(self):
        world = SimpleNamespace(log=SimpleNamespace(clear_file=Mock(side_effect=OSError()), add=Mock()))
        with application_for(world=world) as test_app:
            client = TestClient(test_app, base_url="http://127.0.0.1:8710")
            result = client.post("/api/logs/clear")
        self.assertEqual(result.status_code, 503)
        self.assertFalse(result.json()["ok"])
        world.log.add.assert_not_called()

    def test_invalid_booleans_never_reach_control_methods(self):
        world = SimpleNamespace(ready=AsyncMock(return_value={"ok": True}),
                                free_drive=AsyncMock(return_value={"ok": True}),
                                jog=AsyncMock(return_value={"ok": True}))
        with application_for(world=world) as test_app:
            client = TestClient(test_app, base_url="http://127.0.0.1:8710")
            for value in ("false", "true", 0, 1, None, []):
                for route, body in (("ready", {"then_enable": value}),
                                    ("free", {"on": value}),
                                    ("free", {"on": False, "hold": value}),
                                    ("jog", {"axis": 0, "direction": 1, "on": value})):
                    with self.subTest(route=route, value=value):
                        self.assertEqual(client.post("/api/"+route, json=body).status_code, 422)
        world.ready.assert_not_awaited()
        world.free_drive.assert_not_awaited()
        world.jog.assert_not_awaited()

    def test_false_stays_false_and_real_booleans_are_supported(self):
        world = SimpleNamespace(ready=AsyncMock(return_value={"ok": True}),
                                free_drive=AsyncMock(return_value={"ok": True}))
        with application_for(world=world) as test_app:
            client = TestClient(test_app, base_url="http://127.0.0.1:8710")
            self.assertEqual(client.post("/api/ready", json={"then_enable": False}).status_code, 200)
            self.assertEqual(client.post("/api/free", json={"on": False, "hold": False}).status_code, 200)
        world.ready.assert_awaited_once_with(False)
        world.free_drive.assert_awaited_once_with(False, False, "软件按钮")


if __name__ == "__main__":
    unittest.main(verbosity=2)
