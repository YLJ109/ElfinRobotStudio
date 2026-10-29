"""Read-only motion audit. No World import, network, event loop, service or hardware.

python -B tools/check_motion_contract.py          # audit ran, NOT hardware approval
python -B tools/check_motion_contract.py --strict # nonzero while known gaps remain

Executes actual pure math/protocol modules and selected World methods with fake
transports. The source checkout and persisted workstation data are never written.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
os.environ["HUYAN_NO_REAL"] = "1"
ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "backend/core"
sys.path.insert(0, str(ROOT))


def forbidden_network(*args, **kwargs):
    raise RuntimeError("Motion contract audit forbids network access")


socket.socket.connect = forbidden_network
socket.create_connection = forbidden_network


def pure(relative, source=None):
    # These reviewed files only define pure functions/classes/constants.
    allowed = {"backend/core/robot_sim.py", "backend/core/kinematics.py",
               "backend/core/trajectory.py", "backend/core/protocol.py"}
    if relative not in allowed:
        raise RuntimeError("Refusing to import a stateful backend module")
    path = ROOT / relative
    namespace = {"__file__": str(path), "__name__": "motion_contract_pure",
                 "__package__": "backend.core"}
    exec(compile(path.read_text(encoding="utf-8-sig") if source is None else source, str(path), "exec"), namespace)
    return SimpleNamespace(**namespace)


def immediate(coroutine):
    # Every await in these isolated fixtures completes synchronously. Never
    # instantiate an asyncio event loop (including its Windows socketpair).
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    raise AssertionError("Fixture unexpectedly tried to suspend or perform I/O")


def reference_quaternion(pose):
    """Independent fixed-XYZ (Rz Ry Rx) oracle, W/X/Y/Z ordering."""
    x, y, z = [math.radians(v)/2 for v in pose[3:]]
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return [cx*cy*cz+sx*sy*sz, sx*cy*cz-cx*sy*sz,
            cx*sy*cz+sx*cy*sz, cx*cy*sz-sx*sy*cz]


def reference_slerp_matrix(start, target, fraction):
    a, b = reference_quaternion(start), reference_quaternion(target)
    dot = sum(x*y for x,y in zip(a,b))
    if dot < 0:
        b, dot = [-v for v in b], -dot
    dot = min(1., max(-1., dot))
    if dot > .999999:
        q = [(1-fraction)*x+fraction*y for x,y in zip(a,b)]
    else:
        angle = math.acos(dot)
        q = [(math.sin((1-fraction)*angle)*x+math.sin(fraction*angle)*y)/math.sin(angle) for x,y in zip(a,b)]
    norm = math.sqrt(sum(v*v for v in q))
    w, x, y, z = [v/norm for v in q]
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]


def audit():
    paths = ["backend/core/protocol.py"] + [f"backend/core/{n}.py" for n in
             ("world", "robot_real", "robot_sim", "planning", "motion", "kinematics", "hand_follow", "config")]
    # The required isolation runner intentionally does not copy frontend source.
    # Do not fabricate a UI qualification when running in that environment.
    ui_path = "web/src/panels/SettingsPanel.vue"
    if (ROOT/ui_path).exists():
        paths.append(ui_path)
    paths.append("backend/core/trajectory.py")
    raw_sources = {p:(ROOT/p).read_bytes() for p in paths}
    sources = {p:data.decode("utf-8-sig") for p,data in raw_sources.items()}
    protocol = pure("backend/core/protocol.py",sources["backend/core/protocol.py"])
    sim_module = pure("backend/core/robot_sim.py",sources["backend/core/robot_sim.py"])
    kinematics = pure("backend/core/kinematics.py",sources["backend/core/kinematics.py"])
    world_source = sources["backend/core/world.py"]
    world = ast.parse(world_source)
    world_class = next(n for n in world.body if isinstance(n, ast.ClassDef) and n.name == "World")
    names = {"_real_movejto", "_real_rel_step", "_execute_movel", "_hand_available"}
    methods = [n for n in world_class.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in methods} == names
    harness_ast = ast.Module(body=[ast.ClassDef(name="Harness", bases=[], keywords=[], body=methods,
                                               decorator_list=[])], type_ignores=[])
    from backend.core.planning import prepare_movel, digest
    async def offline_to_thread(function, *args, **kwargs):
        # Exercise the real planner synchronously; no thread/event loop or I/O.
        result = function(*args, **kwargs)
        if after_prepare[0]:
            after_prepare[0]()
        return result
    after_prepare = [None]
    namespace = {"math": math, "RED": "red", "ALARM": "alarm", "copy":copy,
                 "asyncio":SimpleNamespace(to_thread=offline_to_thread),
                 "prepare_movel":prepare_movel, "digest":digest,
                 "interpolate_pose": kinematics.interpolate_pose,
                 "pose_rotation_distance": kinematics.pose_rotation_distance,
                 "evaluate": lambda *a, **k: {"level": "green", "reasons": []}}
    exec(compile(ast.fix_missing_locations(harness_ast), "<selected World methods>", "exec"), namespace)
    Harness = namespace["Harness"]
    planning = ast.parse(sources["backend/core/planning.py"])
    waypoint_node = next(n for n in planning.body if isinstance(n, ast.FunctionDef) and n.name == "waypoints")
    from backend.core.trajectory import relative_route, JointTrajectory
    waypoint_namespace = {"math": math, "relative_route": relative_route}
    exec(compile(ast.Module(body=[waypoint_node], type_ignores=[]), "<actual waypoints>", "exec"), waypoint_namespace)
    waypoints = waypoint_namespace["waypoints"]
    checked = []

    assert protocol.encode("MoveRelJ", 0, 1, 1, "10.000") == b"MoveRelJ,0,1,1,10.000,;"
    assert len(protocol.CMDS["MoveJTo"][0].split(",")) == 7
    assert len(protocol.CMDS["MoveLTo"][0].split(",")) == 9
    checked.append("actual ASCII codec, joint direction/index and parameter counts")

    log = SimpleNamespace(add=lambda *a, **k: None)
    executor = SimpleNamespace(check=lambda *a: None)
    h = Harness()
    h.real = SimpleNamespace(epoch=1, frame_seq=1, snap={"stamp": 1.0, "joints": [0.] * 6})
    wire = []

    async def ask(name, *params):
        wire.append((name, params))
        if name == "MoveRelJ":
            _, axis, direction, distance = params
            h.real.snap["joints"][axis] += float(distance) * (1 if direction else -1)
        h.real.frame_seq += 1
        return SimpleNamespace(ok=True)

    async def wait(**kwargs):
        return True

    h.real.ask = ask
    h._wait_motion_idle = wait
    h.current_joints = lambda: list(h.real.snap["joints"])
    h.executor, h.log = executor, log
    h.REL_MAX_CHUNKS, h.REL_CHUNK_DEG = 240, 10.
    start, target = [0.] * 6, [20., 10., 0., 0., 0., 0.]
    immediate(h._real_rel_step(target, "offline contract", 0))
    relative_route = waypoints(start, target, "relj")
    reconstructed, q = [start[:]], start[:]
    for name, (_, axis, direction, distance) in wire:
        assert name == "MoveRelJ"
        q = q[:]
        q[axis] += float(distance) * (1 if direction else -1)
        reconstructed.append(q)
    assert reconstructed == relative_route
    checked.append("actual real-relative executor matches its per-axis preflight route")

    model = kinematics.RobotModel("elfin5")  # reads geometry JSON only
    sim = sim_module.SimRobot(model, [90.] * 6)
    sim.speed_pct = 100
    duration = sim.move_j(target)
    sim.tick(duration / 2)
    assert all(abs(a-b) < 1e-10 for a, b in zip(sim.q, [10., 5., 0., 0., 0., 0.]))
    tcp_gap = math.dist(model.pose_vec(sim.q)[:3], model.pose_vec(relative_route[1])[:3])
    assert tcp_gap > .5
    checked.append("same endpoints produce different sim/legacy-real intermediate joint and TCP paths")
    sim.tick(duration)
    assert sim.q == target and sim.traj is None
    checked.append("sim synchronized quintic reaches its target without teleporting")

    legacy = sim_module.SimRobot(model, [90.]*6)
    legacy.speed_pct = 100
    legacy.move_j(target, contract="legacy_relj")
    assert [list(p) for p in legacy.traj.points] == reconstructed
    assert legacy.traj.stop_at_knots and all(all(v == 0 for v in t) for t in legacy.traj.tangents)
    legacy.tick(legacy.traj.duration)
    assert legacy.q == target
    checked.append("explicit legacy_relj sim contract reproduces ideal adapter knot order, not controller timing")
    continuous = JointTrajectory.build([[0.]*6,[10.,0.,0.,0.,0.,0.],[20.,0.,0.,0.,0.,0.]], [90.]*6,100)
    knot = continuous.times[1]
    assert continuous.at_path_time(knot-1e-5)[0] < 10 < continuous.at_path_time(knot+1e-5)[0]
    assert continuous.tangents[1][0] > 0
    checked.append("continuous sim trajectory passes interior knot without a commanded zero velocity")

    wire.clear()
    try:
        immediate(h._real_movejto(target, 0))
    except RuntimeError as exc:
        assert "MoveJTo" in str(exc) and "禁止下发" in str(exc)
    else:
        raise AssertionError("Unqualified MoveJTo must be rejected before sending")
    assert wire == []
    checked.append("actual MoveJTo rejects before transport; no pretend heartbeat qualification")
    import re
    if ui_path in sources:
        assert re.search(r'<option\b[^>]*value="movejto"[^>]*\bdisabled\b', sources[ui_path])
        checked.append("MoveJTo UI option is disabled")
    config_ast = ast.parse(sources["backend/core/config.py"])
    assert any(isinstance(n, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "real_move_cmd"
               and isinstance(v, ast.Constant) and v.value == "relj" for k,v in zip(n.keys,n.values))
               for n in ast.walk(config_ast))
    checked.append("real command default remains relj; no transport protocol replacement")

    origin, destination = [0.,0.,0.,0.,0.,0.], [80.,-40.,20.,20.,40.,60.]
    assert kinematics.interpolate_pose(origin,destination,0) == origin
    assert kinematics.interpolate_pose(origin,destination,1) == destination
    middle = kinematics.interpolate_pose(origin,destination,.5)
    assert middle[:3] == [40.,-20.,10.] and origin == [0.]*6
    for bad in [-.01,1.01,math.nan,math.inf,True]:
        try:
            kinematics.interpolate_pose(origin,destination,bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid interpolation fraction accepted")
    for a,b in [(origin[:3],destination),(origin,destination[:5]),([math.nan]+origin[1:],destination),
                (origin,destination[:-1]+[math.inf]),(origin,destination[:-1]+[True])]:
        try:
            kinematics.interpolate_pose(a,b,.5)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid interpolation pose accepted")
    checked.append("actual pose interpolation preserves endpoints, translation and rejects invalid fractions")
    orientation_failures, orientation_probe_count, orientation_max_error = [], 0, 0.
    cases = [(origin,[0.,0.,0.,0.,0.,angle]) for angle in [90.,179.,-179.,179.9999,-179.9999,180.]]
    cases += [([0.,0.,0.,170.,80.,-170.],[0.,0.,0.,-170.,100.,170.]),
              ([0.,0.,0.,25.,89.9999,-40.],[0.,0.,0.,-30.,-89.9999,80.])]
    for a,b in cases:
        for fraction in [.1,.25,.5,.75,.9]:
            interpolated = kinematics.interpolate_pose(a,b,fraction)
            actual = kinematics.rpy_to_mat(*map(math.radians,interpolated[3:]))
            expected = reference_slerp_matrix(a,b,fraction)
            error = max(abs(actual[i][j]-expected[i][j]) for i in range(3) for j in range(3))
            orientation_probe_count += 1
            orientation_max_error = max(orientation_max_error,error)
            if error > 1e-4:
                orientation_failures.append({"start_rpy":a[3:],"target_rpy":b[3:],"fraction":fraction,
                                             "actual_rpy":interpolated[3:],"matrix_max_error":error})
    if not orientation_failures:
        checked.append("actual SO3 interpolation matches independent quaternion SLERP at wrap/near-pi/composite poses")

    requested, submissions = [], []
    initial_pose = [400., -400., 700., 0., 0., 179.]
    class RecordingIK:
        names = ['J'+str(i) for i in range(6)]
        lower, upper, vmax = [-10000.]*6, [10000.]*6, [90.]*6
        variant, tcp_translation_mm, tcp_rpy_deg = 'recording', [0.]*3, [0.]*3
        def pose_vec(self, q):
            return list(q)
        def ik(self, q, pose, *args, **kwargs):
            requested.append(pose[:])
            return pose[:], 0., 0., True
        def capsules(self, *a, **k):
            return []
        def jacobian(self, q):
            return [[float(i==j) for i in range(6)] for j in range(6)]
        def flange_pose(self, q):
            return {'xyz_mm':list(q[:3])}
    recording_model=RecordingIK()
    h.settings = {"mode":"sim", "vel_source":"urdf", "sim_speed_pct":100,
                  "fence":{"enabled":False}, "ground_enabled":False,
                  "safety_yellow_pct":90, "safety_red_pct":95, "safety_alarm_pct":100}
    h.real=None
    h.context_state={'revision':1}
    h.plan_context=lambda:digest(h.context_state)
    h.objects=SimpleNamespace(list=lambda:[])
    h.motion_model = lambda: recording_model
    h.limits = lambda: (recording_model.lower, recording_model.upper)
    h.current_joints=lambda:list(h.sim.q)
    def reset_sim():
        h.sim=sim_module.SimRobot(recording_model, recording_model.vmax)
        h.sim.speed_pct=100
        h.sim.q=initial_pose[:]
        def forbidden_segment(*args, **kwargs):
            raise AssertionError('World must not submit per-knot move_j')
        h.sim.move_j=forbidden_segment
    async def execute_prepared_sim(sim, prepared, epoch, *, validate_context):
        submissions.append(prepared)
        validate_context(prepared)
        sim.move_prepared(prepared)
        sim.tick(prepared.trajectory.duration)
        validate_context(prepared)
    h.executor=SimpleNamespace(check=lambda epoch:None, execute_prepared_sim=execute_prepared_sim)
    reset_sim()
    immediate(h._execute_movel([400., -400., 700., 0., 0., -179.], "offline", 0))
    assert len(requested) == 2 and abs(abs(requested[0][5])-180.) < 1e-6 and requested[-1][5] == -179.
    assert len(submissions)==1 and len(submissions[0].trajectory.points)==3
    wrap_rz = [p[5] for p in requested]
    checked.append("actual MOVEL calls SO3 interpolation: 179 to -179 degrees goes through 180, not zero")
    checked.append("actual World MOVEL prepares and submits one whole curve, never per-knot move_j")
    counts = []
    for translation,rotation in [(0.,40.),(160.,0.),(16.,40.),(2000.,0.)]:
        initial_pose[:] = [400.,-400.,700.,0.,0.,0.]
        requested.clear();submissions.clear();reset_sim()
        end = [400.+translation,-400.,700.,0.,0.,rotation]
        immediate(h._execute_movel(end,"offline sampling",0))
        expected = min(120,max(2,math.ceil(translation/8),math.ceil(rotation/2)))
        assert len(requested) == expected
        assert len(submissions)==1
        counts.append(len(requested))
    checked.append("actual MOVEL sample count uses translation and rotation, capped at 120")
    requested.clear();submissions.clear();reset_sim()
    after_prepare[0]=lambda:h.context_state.update(revision=2)
    try:
        immediate(h._execute_movel([432.,-400.,700.,0.,0.,0.],"stale context",0))
    except RuntimeError as exc:
        assert "变化" in str(exc)
    else:
        raise AssertionError('World must reject stale prepared context before submission')
    finally:
        after_prepare[0]=None
    assert submissions==[] and h.sim.traj is None and h.sim.q==initial_pose
    checked.append("actual World rejects context changed during planning before submitting any curve")
    h.settings = {"mode": "real"}
    try:
        immediate(h._execute_movel(initial_pose, "offline", 0))
    except RuntimeError as exc:
        assert "MoveLTo" in str(exc)
    else:
        raise AssertionError("Unqualified real MOVEL must remain closed")
    checked.append("actual real-MOVEL guard rejects before any transport call")

    for mode, real, expected in [("sim", None, True), ("sim", object(), False), ("real", None, False)]:
        h.settings, h.real = {"mode": mode}, real
        assert h._hand_available() is expected
    checked.append("actual palm-follow gate is simulation-only, including real-link exclusion")

    findings = [
        {"id": "MC01", "severity": "P1", "contract": "default sim/real joint path equivalence", "satisfied": False},
        {"id": "MC02", "severity": "P1", "contract": "unqualified MoveJTo cannot send", "satisfied": True,
         "status":"guarded_unqualified", "heartbeat_qualified":False},
        {"id": "MC03", "severity": "P2", "contract": "MOVEL pose interpolation and continuous path", "satisfied": False,
         "status":"simulation_wired_hardware_unqualified", "orientation_fixed":not orientation_failures,
         "segmented_stops_remain":False, "continuous_simulation_wired":True},
    ]
    return {"audit_executed": True, "checks_passed": len(checked), "checks_failed":int(bool(orientation_failures)), "checks": checked,
            "hardware_validated": False, "trajectory_equivalence": False,
            "optional_legacy_relj_geometry_available": True,
            "continuous_movel_core_available": True,
            "world_wiring_completed": True,
            "ui_source_checked": ui_path in sources,
            "fixtures": {"sim_midpoint_q": [10., 5., 0., 0., 0., 0.],
                         "real_first_waypoint_q": relative_route[1], "tcp_separation_mm": round(tcp_gap, 6),
                         "movel_wrap_requested_rz_deg":wrap_rz,"movel_sampling_counts":counts,
                         "so3_reference_mismatches":orientation_failures,
                         "so3_reference_probe_count":orientation_probe_count,
                         "so3_reference_matrix_max_error":orientation_max_error},
            "findings": findings,
            "source_sha256": {p: hashlib.sha256(data).hexdigest() for p,data in raw_sources.items()},
            "sources_changed_during_audit":[p for p,data in raw_sources.items() if (ROOT/p).read_bytes()!=data]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["sources_changed_during_audit"] or ("--strict" in sys.argv and any(not f["satisfied"] for f in result["findings"])):
        raise SystemExit(2)
