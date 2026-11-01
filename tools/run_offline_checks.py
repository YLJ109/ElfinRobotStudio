"""Run checks in a fresh source copy; never import the original World."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "tools/offline_checks.json").read_text(encoding="utf-8"))
ALLOWED = MANIFEST["checks"]
arguments = sys.argv[1:]
names = arguments or ALLOWED
unknown = [name for name in names if name not in ALLOWED and name not in MANIFEST.get('diagnostics',[])]
if unknown:
    raise SystemExit("Not an approved offline check (hardware scripts cannot run here): " + repr(unknown))
COPY = Path(tempfile.mkdtemp(prefix="huayan-fix-checks-")).resolve()
for rel in ("backend", "contracts", "web/public/models", "web/public/tools", "web/public/vision", "tools"):
    if not (ROOT / rel).exists():
        continue
    shutil.copytree(ROOT / rel, COPY / rel,
                    ignore=shutil.ignore_patterns("__pycache__", "data", "*.png", "*.log", "*.STL" if rel == "tools" else "__none__"))
if (ROOT / "web/dist").exists():
    shutil.copytree(ROOT / "web/dist", COPY / "web/dist")
# Install barriers before backend imports, including lazy OpenCV capture.
(COPY / "sitecustomize.py").write_text(
    (COPY / "tools/_offline_runtime.py").read_text(encoding="utf-8"), encoding="utf-8")
data_root = COPY / "isolated-data"
env = dict(os.environ, HUYAN_NO_REAL="1", HUYAN_OFFLINE_ROOT=str(COPY),
           ELFIN_DATA_DIR=str(data_root), PYTHONDONTWRITEBYTECODE="1",
           PYTHONIOENCODING="utf-8", PYTHONPATH=str(COPY))
# Developer diagnostics must not silently narrow the formal test suite.
for key in ("ELFIN_PLAY_CHECK_GROUP", "ELFIN_PLAY_PHYSICS_ONLY", "ELFIN_PLAY_TRACE", "ELFIN_PLAY_TRACE_EXIT"):
    env.pop(key, None)
vision_python = ROOT / 'backend/.venv-vision/Scripts/python.exe'
if vision_python.is_file():
    env.setdefault('ELFIN_VISION_PYTHON',str(vision_python))
source_hashes = {str(path.relative_to(COPY)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for folder, pattern in (("backend", "*.py"), ("contracts", "*.json"))
                 for path in sorted((COPY / folder).rglob(pattern))}
results = []
# These named checks contain complete, real-time simulation courses. Their
# internal motion/preflight deadlines remain unchanged; this is only the
# outer disposable-process lifetime (preflight + all actual paths + teardown).
LONG_CHECKS={'check_board_robot.py','check_robot_play.py','check_drawing.py','check_handling_level2_actual.py','check_handling_level3_actual.py','check_play_vision_rewrite.py','check_drawing_actual.py'}
evidence = ROOT / "output" / "checks" / COPY.name
evidence.mkdir(parents=True, exist_ok=True)
output = evidence.with_suffix('.json')
sys.stdout.reconfigure(encoding="utf-8")
for name in names:
    # No cross-test persisted state; keep all writes in COPY.
    for data in (data_root, COPY / "backend/data"):
        if not data.resolve().is_relative_to(COPY) or data.resolve() == COPY:
            raise RuntimeError("Refusing cleanup outside disposable checkout")
        if data.exists():
            shutil.rmtree(data)
    try:
        timeout_seconds=600 if name in LONG_CHECKS else 180
        p = subprocess.run([sys.executable, str(COPY / "tools" / name)], cwd=COPY,
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        def decoded(value): return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else (value or '')
        p = subprocess.CompletedProcess(exc.cmd,124,decoded(exc.stdout),decoded(exc.stderr)+f'\nOffline check timed out after {timeout_seconds}s; temporary test child stopped.\n')
    log = evidence / (name + ".log")
    log.write_text(p.stdout + p.stderr, encoding="utf-8")
    results.append({"test": name, "exit_code": p.returncode, "log": str(log),'timeout_seconds':timeout_seconds})
    output.write_text(json.dumps({"copy": str(COPY), "data_root": str(data_root),
                                 "source_hashes": source_hashes, "tests": results}, indent=2), encoding="utf-8")
    print(name, "PASS" if p.returncode == 0 else "FAIL", flush=True)
    print((p.stdout + p.stderr)[-6000:] if p.returncode else (p.stdout + p.stderr)[-1000:], flush=True)
output = ROOT / "output" / "checks" / (COPY.name + ".json")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({"copy": str(COPY), "data_root": str(data_root), "source_hashes": source_hashes, "tests": results}, indent=2), encoding="utf-8")
print("Verification report:", output, flush=True)
sys.exit(1 if any(r["exit_code"] for r in results) else 0)
