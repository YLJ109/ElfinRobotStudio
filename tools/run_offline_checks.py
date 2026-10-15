"""Run checks in a fresh source copy; never import the original World."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COPY = Path(tempfile.mkdtemp(prefix="huayan-fix-checks-")).resolve()
for rel in ("sim/backend", "huayan_panel/core", "sim/web/public/models", "tools"):
    shutil.copytree(ROOT / rel, COPY / rel,
                    ignore=shutil.ignore_patterns("__pycache__", "data", "*.png", "*.log", "*.STL" if rel == "tools" else "__none__"))
(COPY / "sitecustomize.py").write_text(
    "import os,socket\nos.environ['HUYAN_NO_REAL']='1'\noriginal=socket.socket.connect\n"
    "def guarded(self,address):\n"
    " if address[0] not in ('127.0.0.1','::1'): raise RuntimeError('Offline tests: external network blocked')\n"
    " return original(self,address)\n"
    "socket.socket.connect=guarded\n", encoding="utf-8")
env = dict(os.environ, HUYAN_NO_REAL="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(COPY))
names = sys.argv[1:] or ["check_cartesian.py", "check_execution.py", "check_features.py", "check_backend.py", "check_tools.py", "check_cameras.py", "check_scenes.py", "check_datasheet_fields.py", "check_ik.py", "check_fk.py"]
results = []
sys.stdout.reconfigure(encoding="utf-8")
for name in names:
    # No cross-test persisted state; keep all writes in COPY.
    data = COPY / "sim/backend/data"
    if data.exists():
        shutil.rmtree(data)
    p = subprocess.run([sys.executable, str(COPY / "tools" / name)], cwd=COPY,
                       env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    (COPY / (name + ".log")).write_text(p.stdout + p.stderr, encoding="utf-8")
    results.append({"test": name, "exit_code": p.returncode, "log": str(COPY / (name + ".log"))})
    print(name, "PASS" if p.returncode == 0 else "FAIL", flush=True)
    print((p.stdout + p.stderr)[-6000:] if p.returncode else (p.stdout + p.stderr)[-1000:], flush=True)
output = ROOT / "docs/engineering-review-2026-10-12/verification.json"
output.write_text(json.dumps({"copy": str(COPY), "tests": results}, indent=2), encoding="utf-8")
sys.exit(1 if any(r["exit_code"] for r in results) else 0)
