"""Speed persistence regressions; run only through run_offline_checks.py.

Each cold load uses a fresh interpreter and a temporary settings directory.
No World, controller, service, browser or real network is started.
"""
from _offline_guard import require_offline_copy
require_offline_copy()

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

CHILD = """
import asyncio, json, socket, sys
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('Config loading must never access a network or controller')
socket.socket.connect = forbidden
socket.create_connection = forbidden
asyncio.open_connection = forbidden
from backend.core import config
config.DATA_DIR = Path(sys.argv[1])
snapshots = [dict(config.get_settings())]
for patch in json.loads(sys.argv[2]):
    snapshots.append(config.save_settings(patch))
    snapshots.append(dict(config.get_settings()))
print(json.dumps({'snapshots': snapshots,
                  'controller_loaded': 'backend.core.robot_real' in sys.modules}))
"""


class SpeedPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="speed-settings-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.path = self.data / "settings.json"

    def seed(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def cold_load(self, patches=()):
        result = subprocess.run([sys.executable, "-c", CHILD, str(self.data), json.dumps(patches)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["controller_loaded"])
        return payload["snapshots"]

    def test_cold_load_preserves_saved_sim_speed_but_resets_real_target_to_five(self):
        for mode in ("sim", "real"):
            with self.subTest(mode=mode):
                self.seed({"mode": mode, "sim_speed_pct": 37.5, "real_speed_pct": 64})
                before = self.path.read_bytes()
                current = self.cold_load()[0]
                self.assertEqual(current["sim_speed_pct"], 37.5)
                self.assertEqual(current["real_speed_pct"], 5)
                self.assertEqual(current["mode"], mode)
                self.assertEqual(self.path.read_bytes(), before, "Loading defaults must not rewrite saved settings")

    def test_user_can_change_both_speeds_in_process_then_only_real_resets_on_restart(self):
        self.seed({"sim_speed_pct": 28, "real_speed_pct": 82})
        snapshots = self.cold_load([{"sim_speed_pct": 43.5, "real_speed_pct": 12.5}, {"tts": False}])
        self.assertEqual(snapshots[0]["sim_speed_pct"], 28)
        self.assertEqual(snapshots[0]["real_speed_pct"], 5)
        for current in snapshots[1:]:
            self.assertEqual(current["sim_speed_pct"], 43.5)
            self.assertEqual(current["real_speed_pct"], 12.5, "Repeated get_settings must preserve this session's user choice")
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["sim_speed_pct"], 43.5)
        self.assertEqual(persisted["real_speed_pct"], 12.5)
        restarted = self.cold_load()[0]
        self.assertEqual(restarted["sim_speed_pct"], 43.5)
        self.assertEqual(restarted["real_speed_pct"], 5)

    def test_legacy_single_speed_migrates_only_to_simulation(self):
        self.seed({"speed_pct": 62, "real_speed_pct": 90})
        current = self.cold_load()[0]
        self.assertEqual(current["sim_speed_pct"], 62)
        self.assertEqual(current["real_speed_pct"], 5)
        self.assertNotIn("speed_pct", current)

    def test_explicit_sim_speed_takes_precedence_over_legacy_speed(self):
        self.seed({"speed_pct": 85, "sim_speed_pct": 32, "real_speed_pct": 90})
        current = self.cold_load()[0]
        self.assertEqual(current["sim_speed_pct"], 32)
        self.assertEqual(current["real_speed_pct"], 5)
        self.assertNotIn("speed_pct", current)

    def test_new_install_uses_defaults_without_creating_settings_file(self):
        current = self.cold_load()[0]
        self.assertEqual(current["sim_speed_pct"], 10)
        self.assertEqual(current["real_speed_pct"], 5)
        self.assertFalse(self.path.exists())

    def test_sim_only_update_does_not_overwrite_current_session_real_speed(self):
        snapshots = self.cold_load([{"real_speed_pct": 9}, {"sim_speed_pct": 71}])
        self.assertEqual(snapshots[-1]["sim_speed_pct"], 71)
        self.assertEqual(snapshots[-1]["real_speed_pct"], 9)
        restarted = self.cold_load()[0]
        self.assertEqual(restarted["sim_speed_pct"], 71)
        self.assertEqual(restarted["real_speed_pct"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
