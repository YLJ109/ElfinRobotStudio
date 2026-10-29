"""只读验证项目结构、资源和桌面解耦；不加载 World，不访问网络。"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import paths


class ProjectLayoutTests(unittest.TestCase):
    def test_every_software_regression_is_registered_but_live_camera_is_not(self):
        checks = json.loads((ROOT / "tools/offline_checks.json").read_text(encoding="utf-8"))["checks"]
        expected = {p.name for p in (ROOT / "tools").glob("check_*.py")} - {
            "check_camera_capture.py", "check_project_layout.py"}
        self.assertEqual(set(checks), expected)
        self.assertEqual(len(checks), len(set(checks)))

    def test_offline_runner_refuses_hardware_and_path_escape_before_importing_world(self):
        for name in ("check_camera_capture.py", "../backend/main.py", str(ROOT / "backend/main.py")):
            result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/run_offline_checks.py"), name],
                                    capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Not an approved offline check", result.stderr)

    def test_root_layout(self):
        for relative in ("backend/main.py", "web/package.json", "start.bat"):
            self.assertTrue((ROOT / relative).is_file(), relative)
        for relative in ("sim", "huayan_panel", "app.py", "run_panel.bat", "run_sim.bat"):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_resource_paths(self):
        self.assertEqual(paths.PROJECT_ROOT, ROOT)
        self.assertEqual(paths.DATA_DIR, ROOT / "backend/data")
        self.assertTrue((paths.MODEL_DIR / "elfin5/robot.json").is_file())
        self.assertTrue(paths.TOOL_CATALOG.is_file())
        self.assertEqual(paths.WEB_DIST, ROOT / "web/dist")

    def test_protocol_is_backend_owned(self):
        protocol = ROOT / "backend/core/protocol.py"
        self.assertTrue(protocol.is_file())
        self.assertNotIn("PyQt6", protocol.read_text(encoding="utf-8"))

    def test_no_retired_python_imports(self):
        for directory in ("backend", "tools"):
            for filename in (ROOT / directory).rglob("*.py"):
                if "__pycache__" in filename.parts:
                    continue
                tree = ast.parse(filename.read_text(encoding="utf-8-sig"), filename=str(filename))
                for node in ast.walk(tree):
                    names = ([entry.name for entry in node.names] if isinstance(node, ast.Import)
                             else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                    for name in names:
                        self.assertFalse(name.startswith(("huayan_panel", "sim.backend", "PyQt6")),
                                         f"{filename.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}")

    def test_launch_entry(self):
        launch = (ROOT / "tools/run_backend.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("'backend.main:app'", launch)
        self.assertNotIn("'sim.backend.main:app'", launch)
        start = (ROOT / "start.bat").read_text(encoding="utf-8-sig")
        self.assertNotIn("run_panel", start)
        self.assertNotIn("SkipGui", start)
        self.assertIn('pushd "%~dp0web"', start)

    def test_dependencies_do_not_install_gui(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["tool"]["setuptools"]["packages"]["find"]["include"], ["backend*"])
        doctor = (ROOT / "tools/env_doctor.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("PyQt6", doctor)
        self.assertIn("backend\\requirements.txt", doctor)

    def test_first_start_does_not_autoconnect(self):
        # 只读取常量，不导入会创建数据目录的配置模块。
        tree = ast.parse((ROOT / "backend/core/config.py").read_text(encoding="utf-8"))
        defaults = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "DEFAULTS" for target in node.targets))
        value = next(value for key, value in zip(defaults.keys, defaults.values)
                     if isinstance(key, ast.Constant) and key.value == "auto_connect")
        self.assertIs(ast.literal_eval(value), False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
