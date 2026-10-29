"""Run checks in a fresh source copy; never import the original World."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = json.loads((ROOT / "tools/offline_checks.json").read_text(encoding="utf-8"))["checks"]
arguments = sys.argv[1:]
staged_follow = '--staged-follow' in arguments
staged_play = '--staged-play' in arguments
names = [name for name in arguments if name not in ('--staged-follow','--staged-play')] or ALLOWED
unknown = [name for name in names if name not in ALLOWED]
if unknown:
    raise SystemExit("Not an approved offline check (hardware scripts cannot run here): " + repr(unknown))
COPY = Path(tempfile.mkdtemp(prefix="huayan-fix-checks-")).resolve()
for rel in ("backend", "web/public/models", "web/public/tools", "tools"):
    if not (ROOT / rel).exists():
        continue
    shutil.copytree(ROOT / rel, COPY / rel,
                    ignore=shutil.ignore_patterns("__pycache__", "data", "*.png", "*.log", "*.STL" if rel == "tools" else "__none__"))
if (ROOT / "web/dist").exists():
    shutil.copytree(ROOT / "web/dist", COPY / "web/dist")
if staged_follow:
    # Explicit, fixed overlay inside the disposable copy only. The live
    # backend remains untouched until all staged regressions have passed.
    for source, target in [('staged_hand_follow.py', 'hand_follow.py'),
                           ('staged_position_servo.py', 'position_servo.py'),
                           ('staged_follow_world.py', 'world.py')]:
        shutil.copyfile(COPY / 'tools' / source, COPY / 'backend/core' / target)
if staged_play:
    for source,target in [('staged_play_routes.py','play_routes.py'),
                          ('staged_robot_play_routes.py','robot_play_routes.py'),
                          ('staged_planning.py','core/planning.py'),
                          ('staged_play_vision.py','core/play_vision.py'),
                          ('staged_play_main.py','main.py')]:
        shutil.copyfile(COPY / 'tools' / source,COPY / 'backend' / target)
(COPY / "sitecustomize.py").write_text(
    "import os,socket\nos.environ['HUYAN_NO_REAL']='1'\noriginal=socket.socket.connect\n"
    "def guarded(self,address):\n"
    " if address[0] not in ('127.0.0.1','::1'): raise RuntimeError('Offline tests: external network blocked')\n"
    " return original(self,address)\n"
    "socket.socket.connect=guarded\n", encoding="utf-8")
env = dict(os.environ, HUYAN_NO_REAL="1", HUYAN_OFFLINE_ROOT=str(COPY),
           PYTHONIOENCODING="utf-8", PYTHONPATH=str(COPY))
results = []
sys.stdout.reconfigure(encoding="utf-8")
for name in names:
    # No cross-test persisted state; keep all writes in COPY.
    data = COPY / "backend/data"
    if data.exists():
        shutil.rmtree(data)
    try:
        p = subprocess.run([sys.executable, str(COPY / "tools" / name)], cwd=COPY,
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    except subprocess.TimeoutExpired as exc:
        def decoded(value): return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else (value or '')
        p = subprocess.CompletedProcess(exc.cmd,124,decoded(exc.stdout),decoded(exc.stderr)+'\nOffline check timed out after 180s; temporary test child stopped.\n')
    (COPY / (name + ".log")).write_text(p.stdout + p.stderr, encoding="utf-8")
    results.append({"test": name, "exit_code": p.returncode, "log": str(COPY / (name + ".log"))})
    print(name, "PASS" if p.returncode == 0 else "FAIL", flush=True)
    print((p.stdout + p.stderr)[-6000:] if p.returncode else (p.stdout + p.stderr)[-1000:], flush=True)
output = ROOT / "output" / "checks" / (COPY.name + ".json")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({"copy": str(COPY), "tests": results}, indent=2), encoding="utf-8")
print("Verification report:", output, flush=True)
sys.exit(1 if any(r["exit_code"] for r in results) else 0)
