"""Pure math regressions; run via tools/run_offline_checks.py only."""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import json
import math
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.kinematics import RobotModel, _mat_mul, _mat_trans, rpy_to_mat
from backend.core import position_servo as servo


# Minimal URDF joint geometry snapshot from web/public/models/elfin5/robot.json.
# No World, model loader, meshes, config, tools catalog, persisted state, or I/O
# services. Also works when the isolation copy contains only tools + backend.
GEOMETRY = [
    ('world_base', 'world', 'elfin_base_link', [0,0,0], [0,0,0], 1, None),
    ('elfin_base_joint', 'elfin_base_link', 'elfin_base', [0,0,0], [0,0,math.pi/2], 1, None),
    ('elfin_joint1', 'elfin_base', 'elfin_link1', [0,0,.0735], [0,0,0], 1, 3.14),
    ('elfin_joint2', 'elfin_link1', 'elfin_link2', [-.078,0,.1465], [1.5708,1.3734,-1.5708], 1, 2.35),
    ('elfin_joint3', 'elfin_link2', 'elfin_link3', [-.37262,.074541,-.0060028], [0,0,1.3734], -1, 2.61),
    ('elfin_joint4', 'elfin_link3', 'elfin_link4', [0,.119,-.072], [0,1.5708,1.5708], 1, 3.14),
    ('elfin_joint5', 'elfin_link4', 'elfin_link5', [-.0605,0,.301], [-1.5708,0,1.5708], -1, 2.56),
    ('elfin_joint6', 'elfin_link5', 'elfin_link6', [0,-.1005,-.0605], [1.5708,1.5708,0], 1, 3.14),
    ('elfin_end_joint', 'elfin_link6', 'elfin_end_link', [0,0,.0545], [0,0,-1.5708], 1, None),
]


def make_model(tool_tcp=45.):
    """40mm connector + 10mm plate + 30mm cup: plate TCP=45, tip=80.

    RobotModel adds its calibrated 20mm flange correction, so subtract that
    correction exactly as sim_tool.model_for does for the reference tool.
    """
    model = RobotModel.__new__(RobotModel)
    model.root, model.flange = 'world', 'elfin_end_link'
    model.tcp_translation_mm = [0., 0., tool_tcp - 20.]
    model.tcp_rpy_deg = [0., 0., 90.]
    model.joints = []
    for name, parent, child, xyz, rpy, sign, limit in GEOMETRY:
        joint = dict(name=name, parent=parent, child=child, xyz=xyz[:],
                     rpy=rpy[:], axis=[0.,0.,float(sign)],
                     type='revolute' if limit else 'fixed')
        if limit:
            joint['limit'] = dict(lower=-limit, upper=limit)
        model.joints.append(joint)
    model.movable = [j for j in model.joints if j.get('limit')]
    model.lower = [math.degrees(j['limit']['lower']) for j in model.movable]
    model.upper = [math.degrees(j['limit']['upper']) for j in model.movable]
    model._children = {}
    for j in model.joints:
        model._children.setdefault(j['parent'], []).append(j)
    model._origins = {j['name']: _mat_mul(_mat_trans(*j['xyz']), rpy_to_mat(*j['rpy']))
                      for j in model.joints}
    model._fk_key, model._fk_value = None, None
    return model


def xyz(model, q):
    matrix = model._flange_matrix(q)
    return [matrix[i][3] * 1000. for i in range(3)]


class PlanarSingularity:
    """Two 100mm links at full extension: inward error has zero gradient."""
    lower, upper = [-180.] * 6, [180.] * 6

    def _flange_matrix(self, q):
        a, b = math.radians(q[0]), math.radians(q[0] + q[1])
        return _mat_trans(.1 * (math.cos(a) + math.cos(b)),
                          .1 * (math.sin(a) + math.sin(b)), q[2] / 1000.)

    def jacobian(self, q):
        a, b, s = math.radians(q[0]), math.radians(q[0]+q[1]), 100*math.pi/180
        return [[-s*(math.sin(a)+math.sin(b)), -s*math.sin(b), 0,0,0,0],
                [s*(math.cos(a)+math.cos(b)), s*math.cos(b), 0,0,0,0],
                [0,0,1,0,0,0]]


class PositionServo(unittest.TestCase):
    def step(self, model, q, target, lo=None, hi=None, **options):
        before = copy.deepcopy(model.__dict__)
        inputs = copy.deepcopy((q, target, lo, hi))
        origin = xyz(copy.copy(model), q)
        result = servo.solve_position_step(model, q, target,
                    model.lower if lo is None else lo,
                    model.upper if hi is None else hi, **options)
        self.assertEqual(model.__dict__, before, 'input model/cache was modified')
        self.assertEqual((q,target,lo,hi), inputs)
        self.assertLessEqual(result['iterations'], servo.MAX_ITERATIONS)
        self.assertEqual(set(result), {'q','position_error_mm','progress_mm','cause','iterations'})
        if result['q'] is not None:
            nxt = result['q']
            self.assertEqual(len(nxt), 6)
            for i in range(6):
                self.assertTrue(math.isfinite(nxt[i]))
                self.assertLessEqual(abs(nxt[i]-q[i]), options.get('max_joint_step',8.) + 1e-9)
                self.assertLessEqual(max(model.lower[i], (lo or model.lower)[i]), nxt[i]+1e-9)
                self.assertLessEqual(nxt[i], min(model.upper[i], (hi or model.upper)[i])+1e-9)
            p = xyz(copy.copy(model), nxt)
            error = math.dist(p, target)
            self.assertLessEqual(math.dist(p,origin),options.get('max_cartesian_step',24.)+1e-8)
            self.assertAlmostEqual(result['position_error_mm'], error, places=7)
            self.assertAlmostEqual(result['progress_mm'], math.dist(origin,target)-error, places=7)
            self.assertGreaterEqual(result['progress_mm'], 0.)
        return result

    def drive(self, model, q, target, steps=65, **options):
        for _ in range(steps):
            if math.dist(xyz(model,q),target) <= .06:
                return q
            result = self.step(model,q,target,**options)
            self.assertIsNotNone(result['q'], result)
            self.assertGreater(result['progress_mm'], 1e-8, result)
            q = result['q']
        self.fail(('target did not converge',target,xyz(model,q)))

    def center(self, model):
        # Local-only deterministic acquisition; no legacy global IK/restarts.
        return self.drive(model,[0.,-25.,85.,0.,-15.,0.],[0.,-400.,700.])

    def test_geometry_snapshot_matches_source_when_available(self):
        source = Path(__file__).resolve().parents[1]/'web/public/models/elfin5/robot.json'
        if not source.exists():
            self.skipTest('minimal tools/backend copy: using embedded URDF snapshot')
        joints = {j['name']:j for j in json.loads(source.read_text(encoding='utf-8'))['joints']}
        for joint in make_model().joints:
            actual = joints[joint['name']]
            for key in ('parent','child','xyz','rpy','axis','type'):
                self.assertEqual(joint[key],actual[key])
            if joint.get('limit'):
                for key in ('lower','upper'):
                    self.assertEqual(joint['limit'][key],actual['limit'][key])

    def test_classic_center_six_directions_and_new_tool(self):
        for tcp in (45.,80.):
            model = make_model(tcp)
            center = self.center(model)
            self.assertLess(math.dist(xyz(model,center),[0,-400,700]),.06)
            for axis in range(3):
                for sign in (-1,1):
                    with self.subTest(tcp=tcp,axis=axis,sign=sign):
                        target = [0.,-400.,700.]
                        target[axis] += sign*60.
                        moved = self.drive(model,center[:],target)
                        returned = self.drive(model,moved,[0.,-400.,700.])
                        self.assertLess(math.dist(xyz(model,returned),[0,-400,700]),.06)

    def test_aligned_wrist_both_signs_and_multi_axis_participation(self):
        model = make_model()
        for bend in (0., 1e-6, -1e-6):
            q = self.center(model)
            q[3],q[4],q[5] = 30.,bend,-30.
            origin = xyz(model,q)
            for shift in ((30,0,0),(-30,0,0),(0,30,0),(0,-30,0),(0,0,30),(0,0,-30),(25,-20,25)):
                target = [a+b for a,b in zip(origin,shift)]
                moved = self.drive(model,q[:],target)
                if shift == (25,-20,25):
                    self.assertGreaterEqual(sum(abs(a-b)>.05 for a,b in zip(moved,q)),3)
                    # Orientation is deliberately free, including the wrist.
                    self.assertGreater(abs(moved[4]-q[4]), .01)

    def test_zero_gradient_singularity_escapes_by_small_coordinated_motion(self):
        model = PlanarSingularity()
        q = [0.] * 6
        result = self.step(model,q,[199.8,0.,0.])
        self.assertIsNotNone(result['q'],result)
        self.assertGreater(result['progress_mm'],.05)
        self.assertGreater(abs(result['q'][0]),.1)
        self.assertGreater(abs(result['q'][1]),.1)
        self.assertLess(abs(result['q'][0]+result['q'][1]/2),.05)
        self.assertIsNone(self.step(model,q,[201.,0.,0.])['q'])

    def test_joint_and_cartesian_trust_and_limit_active_set(self):
        model = make_model()
        q = self.center(model)
        for cart, joint in ((.2,.1),(2.,.5),(24.,8.)):
            result = self.step(model,q,[200.,-500.,800.],max_joint_step=joint,max_cartesian_step=cart)
            self.assertIsNotNone(result['q'],result)
        lo, hi = q[:], q[:]
        self.assertIsNone(self.step(model,q,[100.,-400.,700.],lo,hi)['q'])
        lo,hi = model.lower[:],model.upper[:]
        lo[0]=hi[0]=q[0]
        result = self.step(model,q,[0.,-420.,720.],lo,hi)
        self.assertIsNotNone(result['q'],result)
        self.assertEqual(result['q'][0],q[0])
        invalid = q[:]
        invalid[0] = model.upper[0]+.1
        result = self.step(model,invalid,[0.,-400.,700.])
        self.assertEqual(result['cause'],'joint_limits')
        self.assertIsNone(result['q'])
        # Loose supplied limits cannot relax model hard limits.
        boundary = q[:]
        boundary[0] = model.upper[0]
        self.step(model,boundary,[100.,400.,700.],[-360.]*6,[360.]*6)

    def test_deadline_prevents_fk_and_can_cancel_mid_search(self):
        model = make_model()
        with patch.object(RobotModel,'_flange_matrix',side_effect=AssertionError('expired FK')):
            result = servo.solve_position_step(model,[0.]*6,[0.,-400.,700.],model.lower,model.upper,
                                               deadline=time.monotonic()-1.)
        self.assertEqual(result['cause'],'deadline')
        self.assertEqual(result['iterations'],0)
        self.assertIsNone(result['q'])
        clock = iter([0.,0.,0.,0.,0.,2.])
        with patch.object(servo.time,'monotonic',side_effect=lambda:next(clock,2.)):
            result = servo.solve_position_step(model,[0.]*6,[0.,-400.,700.],model.lower,model.upper,deadline=1.)
        self.assertEqual(result['cause'],'deadline')
        self.assertIsNone(result['q'])

    def test_nonfinite_and_malformed_inputs_hold(self):
        model = make_model()
        normal = dict(model=model,q=[0.]*6,target_xyz=[0.,-400.,700.],lower=model.lower,upper=model.upper)
        for key,value in [('q',[math.nan]*6),('q',[0.]*5),('q',None),
                          ('target_xyz',[0.,math.inf,0.]),('lower',[-math.inf]*6),
                          ('upper',[math.nan]*6),('deadline',math.nan),
                          ('max_joint_step',0.),('max_cartesian_step',-1.),
                          ('max_joint_step',True),('q',['0']*6)]:
            with self.subTest(key=key,value=value):
                result = servo.solve_position_step(**{**normal,key:value})
                self.assertEqual(result['cause'],'invalid_input')
                self.assertIsNone(result['q'])

    def test_budgets_and_no_wrist_angle_wrapping(self):
        model = PlanarSingularity()
        calls = []
        original = model._flange_matrix
        def counted(q):
            calls.append(1)
            return original(q)
        model._flange_matrix = counted
        result = servo.solve_position_step(model,[0.]*6,[1000.,1000.,1000.],model.lower,model.upper)
        self.assertLessEqual(len(calls),servo.MAX_EVALUATIONS)
        self.assertLessEqual(result['iterations'],servo.MAX_ITERATIONS)
        model = make_model()
        q = self.center(model)
        q[5] = 179.
        result = self.step(model,q,[30.,-420.,710.])
        self.assertIsNotNone(result['q'],result)
        self.assertGreater(result['q'][5],170.)

    def test_reached_holds_and_nonfinite_model_is_rejected(self):
        model = PlanarSingularity()
        result = self.step(model,[0.]*6,[200.,0.,0.])
        self.assertEqual(result['q'],[0.]*6)
        self.assertEqual(result['cause'],'reached')
        self.assertEqual(result['progress_mm'],0.)
        with patch.object(PlanarSingularity,'jacobian',return_value=[[math.nan]*6]*3):
            result = self.step(model,[0.]*6,[199.,0.,0.])
        self.assertIsNone(result['q'])
        self.assertEqual(result['cause'],'invalid_model')
        # A tiny deterministic evaluation allowance must also stop null search.
        with patch.object(servo,'MAX_EVALUATIONS',3):
            result = self.step(model,[0.]*6,[199.,0.,0.])
        self.assertIsNone(result['q'])
        self.assertEqual(result['cause'],'budget')


if __name__ == '__main__':
    unittest.main(verbosity=2)
