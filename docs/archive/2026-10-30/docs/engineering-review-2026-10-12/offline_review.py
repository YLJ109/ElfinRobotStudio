"""Read-only source review: all imports, tests and writes run in temporary copies.

Run with the project's virtualenv Python. No controller or AI connections allowed.
This records current behavior; `issue_reproduced` is not a passing safety test.
"""
from pathlib import Path
import asyncio
import hashlib
import gzip
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

SOURCE = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("results.json")


def data_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (SOURCE / "sim/backend/data").glob("*") if p.is_file()}


def make_copy(base):
    for rel in ("sim/backend", "huayan_panel/core", "sim/web/public/models"):
        src = SOURCE / rel
        shutil.copytree(src, base / rel,
                        ignore=shutil.ignore_patterns("__pycache__", "data"))
    (base / "tools").mkdir(parents=True)
    for name in ("check_backend.py", "check_datasheet_fields.py", "check_ik.py",
                 "check_fk.py", "datasheet_full.json"):
        shutil.copy2(SOURCE / "tools" / name, base / "tools" / name)
    (base / "sitecustomize.py").write_text(
        "import os, socket\nos.environ['HUYAN_NO_REAL']='1'\n"
        "original=socket.socket.connect\noriginal_ex=socket.socket.connect_ex\n"
        "def guarded(self, address):\n"
        " if address[0] not in ('127.0.0.1', '::1'): raise RuntimeError('offline review: external network blocked')\n"
        " return original(self, address)\n"
        "def guarded_ex(self, address):\n"
        " if address[0] not in ('127.0.0.1', '::1'): raise RuntimeError('offline review: external network blocked')\n"
        " return original_ex(self, address)\n"
        "socket.socket.connect=guarded\nsocket.socket.connect_ex=guarded_ex\n",
        encoding="utf-8")


def worker(base):
    base = base.resolve()
    sys.path.insert(0, str(base))
    os.environ["HUYAN_NO_REAL"] = "1"
    from sim.backend.core import config
    from sim.backend.core.world import World
    from sim.backend.core.robot_real import RealLink, Reply
    from sim.backend.core.robot_sim import SimRobot
    assert config.DATA_DIR.is_relative_to(base), "Refusing writes outside temporary workspace"

    class FakeLink:
        def __init__(self):
            self.snap = {"joints": [0.] * 6, "fsm": 33, "enabled": 1,
                         "moving": 0, "inpos": 1}
            self.cmd_up, self.stream_up = True, True
            self.calls = []
            self.first = asyncio.Event()

        async def ask(self, name, *args, **kw):
            self.calls.append(name)
            if name == "MoveRelJ":
                axis, direction, distance = args[1:]
                self.snap["joints"][axis] += float(distance) * (1 if direction else -1)
                self.first.set()
            return Reply(name, True)

        async def estop(self):
            self.calls.append("GrpStop")
            return Reply("GrpStop", False, err_code=20018)

    def real_world():
        w = World()
        w.settings["mode"] = "real"
        w.real = FakeLink()
        return w

    results = []

    async def probes():
        w = real_world()
        w.real.stream_up = False
        results.append({"id": "stale-state", "issue_reproduced":
                        w.joints_known() and w.motion_ok() and w._need(w.MOTION, "probe"),
                        "known": w.joints_known(), "motion_ok": w.motion_ok()})

        w = real_world()
        w.REL_START_S = .015
        task = asyncio.create_task(w._real_rel_step([0, 0, 0, 0, 0, 25]))
        await w.real.first.wait()
        stop_ok = await w.estop()
        await asyncio.wait_for(task, 1)
        calls = w.real.calls
        stop_at = calls.index("GrpStop")
        results.append({"id": "motion-after-stop", "issue_reproduced":
                        "MoveRelJ" in calls[stop_at + 1:], "calls": calls,
                        "stop_reported_success_despite_rejection": stop_ok})

        w = real_world()
        w.REL_START_S = .01
        w.runner.load([{"kind": "movej", "joints": [0, 0, 0, 0, 0, 10],
                        "line": 1, "src": "first"},
                       {"kind": "movej", "joints": [0, 0, 0, 0, 0, 20],
                        "line": 2, "src": "second"}])
        w.runner.start()
        w._drive_executor(time.monotonic(), False)
        w._drive_executor(time.monotonic(), False)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        results.append({"id": "overlapping-program-moves", "issue_reproduced":
                        len(pending) >= 2, "tasks_created_before_controller_started": len(pending),
                        "program_index": w.runner.i})
        await asyncio.gather(*pending)

        link = RealLink("offline.invalid")
        link.cmd_up = True
        reply = await link.ask("MoveRelJ", 0, 5, 1, 2, timeout=.001)
        queued = await link.q.get()
        results.append({"id": "expired-command-retained", "issue_reproduced":
                        reply.timeout and queued[1] not in link._futures,
                        "queued_command": queued[3], "caller_timed_out": reply.timeout})

    asyncio.run(probes())
    from sim.backend.core.kinematics import _solve6
    j = [[0.] * 6 for _ in range(6)]
    j[0][0], j[0][1] = 1., 2.
    for i in range(2, 6):
        j[i][i] = 1.
    e, damping = [1., 0, 0, 0, 0, 0], .001
    code_matrix = [[sum(j[r][k] * j[c][k] for k in range(6))
                    + (damping if r == c else 0) for c in range(6)] for r in range(6)]
    normal_matrix = [[sum(j[k][r] * j[k][c] for k in range(6))
                      + (damping if r == c else 0) for c in range(6)] for r in range(6)]
    rhs = [sum(j[r][c] * e[r] for r in range(6)) for c in range(6)]
    code_step, normal_step = _solve6(code_matrix, rhs), _solve6(normal_matrix, rhs)
    results.append({"id": "ik-damping-matrix-mismatch", "issue_reproduced":
                    abs(code_step[1] - normal_step[1]) > 100,
                    "code_dq": code_step, "correct_normal_equation_dq": normal_step,
                    "note": "Synthetic singular Jacobian, algebra check; not a hardware trajectory."})
    w = World()
    w.settings["mode"] = "sim"
    w.settings["self_collision"] = True
    w.settings["ground_enabled"] = True
    q = [29.069, -17.547, -120.901, 2.186, -74.089, -101.866]

    def bench(fn, n=300):
        for _ in range(15):
            fn()
        samples = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - t) * 1000)
        samples.sort()
        return {"mean_ms": round(statistics.fmean(samples), 4),
                "p95_ms": round(samples[int(n * .95)], 4)}

    from sim.backend.core.safety import evaluate
    perf = {"fk": bench(lambda: w.model.flange_pose(q)),
            "capsules": bench(lambda: w.model.capsules(q)),
            "safety_including_fk": bench(lambda: evaluate(w.model, q, w.settings,
                                                        w.sim.lower, w.sim.upper))}
    w.sim.q = q[:]
    snap = w.snapshot()
    perf["snapshot"] = bench(w.snapshot)
    perf["serialization"] = bench(lambda: json.dumps(snap, ensure_ascii=False))
    perf["snapshot_bytes"] = len(json.dumps(snap, ensure_ascii=False).encode())
    perf["nominal_broadcast_hz"] = 50
    perf["note"] = "Microbenchmarks, not live browser or hardware latency."

    # Virtual clock proves pause-time is counted as motion-time.
    from unittest.mock import patch
    sim = SimRobot(w.model, w.model.vmax)
    clock = [100.]
    with patch("sim.backend.core.robot_sim.time.monotonic", side_effect=lambda: clock[0]):
        sim.move_j([0, 0, 0, 0, 0, 9])
        clock[0] += .1
        sim.tick(.02)
        before = sim.q[5]
        sim.paused = True
        clock[0] += 5
        sim.tick(.02)
        sim.paused = False
        sim.tick(.02)
        results.append({"id": "simulation-pause-jump", "issue_reproduced":
                        sim.q[5] == 9 and before < 9,
                        "before_pause_deg": before, "after_resume_deg": sim.q[5]})
    return {"behavior_probes": results, "performance": perf,
            "worker_data_directory": str(config.DATA_DIR)}


def main():
    before = data_hashes()
    base = Path(tempfile.mkdtemp(prefix="huayan-offline-review-")).resolve()
    make_copy(base)
    env = dict(os.environ, HUYAN_NO_REAL="1", PYTHONIOENCODING="utf-8",
               PYTHONPATH=str(base))
    checks = []
    for name in ("check_backend.py", "check_datasheet_fields.py", "check_ik.py", "check_fk.py"):
        p = subprocess.run([sys.executable, str(base / "tools" / name)], cwd=base,
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        (base / (name + ".log")).write_text(p.stdout + p.stderr, encoding="utf-8")
        signals = [line for line in p.stdout.splitlines()
                   if "FAIL" in line or "通过" in line or "失败" in line or "/6" in line]
        checks.append({"script": name, "exit_code": p.returncode, "signals": signals,
                       "log": str(base / (name + ".log"))})
        print(name, p.returncode, signals, flush=True)
    # Separate process so package imports and mutable settings cannot escape the copy.
    p = subprocess.run([sys.executable, str(Path(__file__)), "--worker", str(base)],
                       cwd=base, env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr)
    result = json.loads(p.stdout)
    js = """const fs=require('fs'),vm=require('vm');
const src=fs.readFileSync(process.argv[1],'utf8');
const start=src.indexOf('  function sampleAt(now)');
const end=src.indexOf('  const setAngles =',start);
const ctx=vm.createContext({buffer:[{t:1,q:[10,0,0,0,0,0]},
{t:1.1,q:[20,0,0,0,0,0]}],current:[20,0,0,0,0,0],INTERP_DELAY:.12});
const actual=vm.runInContext(src.slice(start,end)+';sampleAt(1.5)',ctx);
console.log(JSON.stringify({id:'interpolation-after-gap',issue_reproduced:actual[0]===10,
newest_deg:20,rendered_deg:actual[0],method:'Actual sampleAt function in isolated JavaScript VM'}));
"""
    frontend = subprocess.run(["node", "-e", js, str(SOURCE / "sim/web/src/scene.js")],
                              capture_output=True, text=True, encoding="utf-8", timeout=15)
    if frontend.returncode:
        raise RuntimeError(frontend.stderr)
    result["frontend_probe"] = json.loads(frontend.stdout)
    main_js = max((SOURCE / "sim/web/dist/assets").glob("index-*.js"),
                  key=lambda f: f.stat().st_mtime)
    stls = list((SOURCE / "sim/web/public/models/elfin5").glob("*.STL"))
    result["frontend_assets"] = {
        "main_js_bytes": main_js.stat().st_size,
        "main_js_gzip_bytes": len(gzip.compress(main_js.read_bytes())),
        "stl_bytes": sum(f.stat().st_size for f in stls),
        "stl_triangles": sum(int.from_bytes(f.read_bytes()[80:84], "little") for f in stls),
        "measurement": "Existing dist and public model files, not a fresh build"}
    after = data_hashes()
    changed = [name for name in before.keys() | after.keys() if before.get(name) != after.get(name)]
    result.update({"date": "2026-10-12", "temp_workspace": str(base), "checks": checks,
                   "original_data_unchanged": before == after,
                   "original_data_changed_files": changed})
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--worker" in sys.argv:
        print(json.dumps(worker(Path(sys.argv[-1])), ensure_ascii=False))
    else:
        main()
