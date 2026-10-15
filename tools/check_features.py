"""Offline acceptance for plans, calibration, task DSL, contracts and replay."""
import asyncio
import json
import math
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ["HUYAN_NO_REAL"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core.calibration import tcp_fit, workcell
from sim.backend.core.kinematics import RobotModel, rpy_to_mat
from sim.backend.core import program, config
from sim.backend.core.planning import inspect_path
from sim.backend.core.storage import write_json
from sim.backend.core.telemetry import Playback
from sim.backend.core.world import World


class GeometryTests(unittest.TestCase):
    def test_tcp_recovers_known_offset(self):
        offset, pivot, samples = [10.,20.,30.], [100.,200.,300.], []
        for angles in ([0,0,0],[90,0,0],[0,90,0],[0,0,90],[30,40,50]):
            rotation=rpy_to_mat(*[math.radians(v) for v in angles])
            p=[pivot[i]-sum(rotation[i][j]*offset[j] for j in range(3)) for i in range(3)]
            samples.append(p+angles)
        result=tcp_fit(samples)
        self.assertLess(result["rms_mm"],1e-6)
        for a,b in zip(offset,result["tcp_offset_mm"]): self.assertAlmostEqual(a,b)

    def test_tcp_rejects_unobservable_samples(self):
        with self.assertRaises(ValueError): tcp_fit([[0]*6]*4)

    def test_frame_is_orthogonal_and_collinearity_is_rejected(self):
        m=workcell([[1,2,3],[101,2,3],[1,102,3]])["matrix"]
        self.assertEqual(m[0], [1.,0.,0.,1.])
        with self.assertRaises(ValueError): workcell([[0,0,0],[10,0,0],[20,0,0]])

    def test_path_finds_obstacle_between_clear_endpoints(self):
        m=RobotModel(); settings=dict(config.DEFAULTS, self_collision=False, ground_enabled=False,
                                     fence={"enabled":False,"x":[-2000,2000],"y":[-2000,2000],"z":[-2000,2000]})
        # Put a small sphere at the wrist's middle pose, far from both end poses.
        start=[-60,-20,-100,0,-60,0]; end=[60,-20,-100,0,-60,0]
        midcap=next(cap for cap in m.capsules([0,-20,-100,0,-60,0],conservative=True) if cap['link']=='elfin_link6')
        xyz=[(a+b)/2 for a,b in zip(midcap['a'],midcap['b'])]
        objects=[{"kind":"sphere","size":[20,20,20],"xyz":xyz,"collide":True,"name":"middle"}]
        result=inspect_path(m,start,end,settings,[-360]*6,[360]*6,objects)
        self.assertTrue(result["risk_detected"])
        self.assertTrue(any(any(h["type"]=="object" for h in issue["collisions"]) for issue in result["issues"]))
        self.assertFalse(result["validated_hardware"])

    def test_task_parameters_wait_di_and_branch(self):
        steps=program.parse("SPEED ${speed}\nLABEL retry\nWAIT_DI arrived 1 TIMEOUT 3 DEBOUNCE 0.1\nIF_DI arrived 0 GOTO retry\nDOUT valve,1", lambda ref:None, {"speed":12})
        self.assertEqual(steps[2]["kind"],"wait_di")
        self.assertEqual(steps[3]["jump"],1)
        self.assertEqual(steps[4]["alias"],"valve")
        with self.assertRaises(program.ProgramError): program.parse("SPEED ${speed}",lambda ref:None,{"speed":"12\nDOUT 0,1"})

    def test_atomic_write_keeps_previous_file_on_serialization_error(self):
        path=config.DATA_DIR/"atomic-test.json"
        write_json(path,{"good":1})
        with self.assertRaises(ValueError): write_json(path,{"bad":float('nan')})
        self.assertEqual(json.loads(path.read_text()),{"good":1})

    def test_playback_ring_keeps_current_time_origin(self):
        ring=Playback(cap=2)
        for t in [1,2,3]: ring.push(t,[0]*6,"green")
        self.assertEqual(ring.summary()["t0"],2)
        self.assertEqual(ring.slice()["n"],2)


class FeatureAsyncTests(unittest.IsolatedAsyncioTestCase):
    def make_world(self):
        config.save_settings({"mode":"sim","auto_connect":False})
        w=World(); w.settings=dict(w.settings,mode="sim"); return w

    async def test_inspection_is_readonly_and_rejects_changed_scene(self):
        w=self.make_world()
        target=[0,0,0,0,0,2]
        result=await w.inspect_plan(target)
        self.assertTrue(result["ok"])
        self.assertFalse(w.executor.busy)
        w.update_settings({"ghost_opacity":.2})
        # Display-only settings intentionally do not invalidate motion plans.
        self.assertEqual(w.plans[result['plan_id']]['context'], w.plan_context())
        w.objects.add({'kind': 'sphere', 'xyz': [600,600,100], 'size': [40,40,40], 'gravity': False})
        self.assertFalse((await w.execute_plan(result["plan_id"]))["ok"])

    async def test_wait_di_needs_stable_input_and_times_out(self):
        w=self.make_world()
        w.calibrations.save("io","arrived",{"group":"end","direction":"input","bit":0})
        step={"alias":"arrived","val":1,"timeout":.1,"debounce":.02}
        with self.assertRaises(RuntimeError): await w._wait_di(step,w.executor.epoch)
        w.sim_io["end_di"][0]=1
        await w._wait_di(step,w.executor.epoch)

    async def test_ai_plan_cannot_be_changed_after_confirmation(self):
        w=self.make_world(); w.sim_state.update(fsm=33,enabled=1)
        fake={"ok":True,"plan":[{"op":"wait","sec":0.1}],"summary":"test"}
        with patch('sim.backend.core.ai_plan.make_plan',return_value=fake):
            proposal=await w.ai("test",[])
        changed=[{"op":"wait","sec":0.2}]
        self.assertFalse((await w.ai_run(changed,proposal["plan_id"]))["ok"])

    async def test_ai_plan_accepts_browser_numeric_roundtrip(self):
        """JavaScript JSON.stringify turns 0.0 into 0; that is not a plan edit."""
        w=self.make_world(); w.sim_state.update(fsm=33,enabled=1)
        fake={"ok":True,"plan":[{"op":"wait","sec":0.0}],"summary":"test"}
        with patch('sim.backend.core.ai_plan.make_plan',return_value=fake):
            proposal=await w.ai("test",[])
        browser_plan=[{"op":"wait","sec":0}]
        result=await w.ai_run(browser_plan,proposal["plan_id"])
        self.assertTrue(result["ok"],result)

    async def test_blackbox_has_explicit_invalid_state(self):
        w=self.make_world(); w._tick(.02,1.)
        w.settings["mode"]="real"; w._tick(.02,2.)
        bundle=w.blackbox_bundle()
        self.assertFalse(bundle["frames"][-1]["valid"])


class ContractTests(unittest.TestCase):
    def test_malformed_motion_io_and_calibration_return_422(self):
        from fastapi.testclient import TestClient
        from sim.backend.main import app
        with TestClient(app) as client:
            cases=[('/api/movej',{'joints':[0]}),('/api/movej',{'joints':['inf']*6}),
                   ('/api/jog',{'axis':99,'direction':1,'on':True}),('/api/io/enddo',{'bit':9,'val':1}),
                   ('/api/calibrations/frame',{'points':[[0,0,0]]}),
                   ('/api/calibrations/tcp',{'poses':[[0]*6]*3}),
                   ('/api/settings',{'ghost_opacity':None})]
            for path,body in cases:
                self.assertEqual(client.post(path,json=body).status_code,422,path)
            self.assertEqual(client.get('/api/state').status_code,200)


if __name__=='__main__': unittest.main(verbosity=2)
