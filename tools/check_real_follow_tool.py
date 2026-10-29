"""Pure offline tests. Runner registration belongs to mainline.

Direct: .venv/Scripts/python.exe -B tools/check_real_follow_tool.py
Only pure kinematics/safety imports; the existing gate is extracted with AST.
"""
import ast
from copy import copy, deepcopy
import math
from pathlib import Path
import sys
import unittest

from backend.core.real_follow_tool_binding import build_real_follow_model, evidence_digest, resolve_real_follow_tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def fixture():
    """SYNTHETIC test-only data; no assertion about the installed tool."""
    mounted = dict(tool_id='test-only-dual', state='manual_confirmed', source='manual', revision=8)
    tool = dict(id='test-only-dual', type='suction', revision='test-r1',
                mount=dict(status='verified', translation_mm=[10, 0, 0], quaternion_xyzw=[0, 0, 0, 1]),
                tcp=dict(status='verified', frame='flange', translation_mm=[10, 0, 50], rpy_deg=[0, 0, 90]),
                payload=dict(status='verified', frame='flange', mass_kg=0.4, com_mm=[10, 0, 20]),
                geometry=dict(status='verified', frame='mount', conservative=True, capsules=[
                    dict(a=[-50, 0, 0], b=[50, 0, 0], r_mm=10),
                    dict(a=[-50, 0, 0], b=[-50, 0, 40], r_mm=15),
                    dict(a=[50, 0, 0], b=[50, 0, 40], r_mm=15)]))
    certify(tool, mounted)
    return mounted, tool


def certify(tool, mounted):
    for name in ('mount', 'tcp', 'payload', 'geometry'):
        section = tool[name]
        section['verification'] = dict(record_id='SYNTHETIC-ONLY', method='measured',
                                       verified_by='test-fixture', verified_at='test-only',
                                       tool_revision=tool['revision'], mounted_revision=mounted['revision'],
                                       subject_sha256=evidence_digest(section, tool['revision'], mounted['revision']))


class FakeKinematics:
    """Pure numerical model with a rotated URDF flange at (100,200,300) mm."""
    def __init__(self, variant, tcp_translation_mm, tcp_rpy_deg):
        self.variant, self.flange = variant, 'test-flange'
        self.tcp_translation_mm, self.tcp_rpy_deg = tcp_translation_mm, tcp_rpy_deg
        self._fk_key, self._fk_value = None, None

    def fk(self, q):
        # Rotate physical flange Z onto world X, X onto world Y, Y onto world Z.
        return {self.flange: [[0, 0, 1, .1], [1, 0, 0, .2], [0, 1, 0, .3], [0, 0, 0, 1]]}

    def pose_vec(self, q):
        x, y, z = self.tcp_translation_mm
        return [100+20+z, 200+x, 300+y, *self.tcp_rpy_deg]

    def ik(self, q, pose):
        return self.pose_vec(q), pose

    def capsules(self, q, shrink=.9, conservative=False):
        return [dict(link='arm', a=[0, 0, 0], b=[0, 0, 10], r_mm=2)]

    def tool_plane_gaps(self, q, fence):
        return ['none-plane-fallback']


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.mounted, self.tool = fixture()

    def resolve(self, **kwargs):
        return resolve_real_follow_tool(self.mounted, self.tool, variant='test', config_revision=3, **kwargs)

    def build(self, factory=FakeKinematics):
        return build_real_follow_model(self.mounted, self.tool, variant='test', config_revision=3,
                                       model_factory=factory, flange_offset_z_mm=20)

    def codes(self):
        result = self.resolve()
        self.assertFalse(result['ok'], result)
        self.assertIsNone(result['binding'])
        self.assertTrue(all(set(b) == {'layer', 'code', 'path', 'detail'} for b in result['blockers']))
        return {b['code'] for b in result['blockers']}

    def test_valid_snapshot_is_detached_and_does_not_mutate_inputs(self):
        before = deepcopy((self.mounted, self.tool))
        result = self.resolve()
        self.assertTrue(result['ok'], result)
        self.assertEqual(before, (self.mounted, self.tool))
        result['binding']['tool']['tcp']['translation_mm'][0] = 999
        self.assertEqual(self.tool['tcp']['translation_mm'][0], 10)

    def test_unknown_controller_only_and_sim_are_distinct(self):
        for state in ('unknown', 'controller_config_only', 'simulated', 'none_confirmed'):
            with self.subTest(state=state):
                self.mounted['state'] = state
                self.assertIn('not_confirmed', self.codes())
        self.mounted['state'] = 'manual_confirmed'
        self.mounted['scope'] = 'simulation'
        self.assertIn('simulation_scope', self.codes())

    def test_partial_returns_all_layers_not_no_tool(self):
        self.tool = dict(id='test-only-dual', type='suction', revision='test-r1')
        layers = {b['layer'] for b in self.resolve()['blockers']}
        self.assertTrue({'mount', 'tcp', 'payload', 'geometry'} <= layers)

    def test_installed_template_is_not_photo_dual(self):
        self.mounted['tool_id'] = 'gripper-template'
        self.tool.update(id='gripper-template', type='parallel_gripper')
        self.tool['tcp']['status'] = 'unvalidated'
        self.tool['payload'] = dict(status='unknown', mass_kg=None, com_mm=None)
        self.tool.pop('geometry')
        self.assertFalse(self.build()['ok'])
        self.assertIsNone(self.build()['model'])

    def test_wrong_identity_and_hardware(self):
        self.tool['id'] = 'another'
        self.mounted['hardware_id'] = 'actual'
        self.assertTrue({'profile_mismatch', 'hardware_mismatch'} <= self.codes())

    def test_photo_dimensions_never_create_verification_or_payload(self):
        self.tool['geometry'] = dict(dimensions=dict(column=40, cup=30, plate=[300, 50, 10]))
        self.tool['payload'] = dict(status='unknown')
        self.assertTrue({'missing_record', 'missing_capsules', 'invalid_payload'} <= self.codes())
        self.tool['geometry']['status'] = 'verified'
        self.tool['geometry']['verification'] = dict(method='photo')
        self.assertIn('missing_record', self.codes())

    def test_revision_and_unversioned_changes_invalidate_context(self):
        original = self.resolve()['context_fingerprint']
        for section, key, value in [('tcp', 'translation_mm', [1, 2, 3]), ('geometry', 'conservative', False),
                                    ('payload', 'mass_kg', .5), ('mount', 'translation_mm', [1, 0, 0])]:
            with self.subTest(section=section):
                saved = deepcopy(self.tool)
                self.tool[section][key] = value
                self.assertIn('stale_record', self.codes())
                self.assertNotEqual(self.resolve()['context_fingerprint'], original)
                self.tool = saved
        self.mounted['revision'] += 1
        self.assertIn('stale_record', self.codes())
        self.assertNotEqual(self.resolve()['context_fingerprint'], original)
        self.mounted['revision'] -= 1
        self.tool['revision'] = 'test-r2'
        self.assertIn('stale_record', self.codes())
        self.tool['revision'] = 'test-r1'
        for kwargs in (dict(variant='other', config_revision=3), dict(variant='test', config_revision=4)):
            result = resolve_real_follow_tool(self.mounted, self.tool, **kwargs)
            self.assertNotEqual(result['context_fingerprint'], original)

    def test_malicious_and_unknown_types_fail_closed_without_executing(self):
        class Evil:
            def __deepcopy__(self, memo):
                raise AssertionError('Untrusted hook must never execute')
        for value in (Evil(), float('nan'), float('inf'), 10**1000, {1: 'bad'}, [[[]]]*20001):
            with self.subTest(kind=type(value).__name__):
                self.tool['extra'] = value
                self.assertIn('invalid_json', self.codes())
        self.tool.pop('extra')
        for obj, key in ((self.mounted, 'state'), (self.tool, 'type'),
                         (self.tool['geometry']['verification'], 'method')):
            old = obj[key]
            obj[key] = []
            self.assertFalse(self.resolve()['ok'])
            obj[key] = old
        recursive = []
        recursive.append(recursive)
        self.tool['extra'] = recursive
        self.assertIn('invalid_json', self.codes())

    def test_numeric_shape_frames_and_geometry_rejected_even_with_new_records(self):
        mutations = [('tcp', 'translation_mm', [True, 0, 0], 'invalid_tcp'),
                     ('tcp', 'rpy_deg', [0, 0], 'invalid_tcp'),
                     ('tcp', 'frame', 'simulation', 'invalid_frame'),
                     ('payload', 'mass_kg', -1, 'invalid_payload'),
                     ('payload', 'com_mm', [0, 0], 'invalid_payload'),
                     ('payload', 'inertia_kgm2', [1, 1, 1, 5, 0, 0], 'invalid_inertia'),
                     ('mount', 'quaternion_xyzw', [0, 0, 0, 2], 'invalid_quaternion'),
                     ('geometry', 'capsules', [], 'missing_capsules'),
                     ('geometry', 'capsules', [dict(a=[0, 0, 0], b=[0, 0, 1], r_mm=0)], 'invalid_capsule'),
                     ('geometry', 'capsules', [dict(a=[0, 0], b=[0, 0, 1], r_mm=1)], 'invalid_capsule'),
                     ('geometry', 'frame', 'tcp', 'invalid_envelope')]
        for name, key, value, code in mutations:
            with self.subTest(name=name, key=key):
                saved = deepcopy(self.tool)
                self.tool[name][key] = value
                certify(self.tool, self.mounted)
                self.assertIn(code, self.codes())
                self.tool = saved

    def test_none_preserves_legacy_builtin(self):
        self.mounted = dict(tool_id='none', state='none_confirmed', source='manual', revision=1)
        self.tool = dict(id='none', type='none', revision='r1',
                         mount=dict(translation_mm=[0, 0, 0], quaternion_xyzw=[0, 0, 0, 1]),
                         tcp=dict(status='verified', translation_mm=[0, 0, 0], rpy_deg=[0, 0, 0]),
                         payload=dict(status='verified', mass_kg=0, com_mm=[0, 0, 0]))
        result = self.build()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['model'].tcp_translation_mm, [0, 0, 0])
        self.assertEqual(result['model'].tool_plane_gaps([], {}), ['none-plane-fallback'])
        self.tool['tcp']['translation_mm'][0] = 1
        self.assertIn('nonzero_none', self.codes())

    def test_capsules_tcp_and_ik_share_physical_flange_offset_once(self):
        result = self.build()
        self.assertTrue(result['ok'], result)
        model = result['model']
        self.assertEqual(model.tcp_translation_mm, [10, 0, 50])
        self.assertEqual(model.pose_vec([]), [170, 210, 300, 0, 0, 90])
        self.assertEqual(model.ik([], [1])[0], model.pose_vec([]))
        caps = model.capsules([], shrink=.1, conservative=True)
        self.assertEqual(len(caps), 4)
        self.assertEqual(caps[1], dict(link='test-flange', a=[120, 160, 300], b=[120, 260, 300], r_mm=10))
        self.assertEqual([c['r_mm'] for c in caps[1:]], [10, 15, 15])
        self.tool['geometry']['capsules'][0]['r_mm'] = 999
        self.assertEqual(model.capsules([])[1]['r_mm'], 10)

    def test_mount_rotation_does_not_rotate_tcp_twice(self):
        s = math.sqrt(.5)
        self.tool['mount']['quaternion_xyzw'] = [0, 0, s, s]
        certify(self.tool, self.mounted)
        model = self.build()['model']
        cap = model.capsules([])[1]
        for actual, expected in zip(cap['a'], [120, 210, 250]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(model.pose_vec([]), [170, 210, 300, 0, 0, 90])

    def test_plane_gaps_use_whole_capsules_and_detect_collision(self):
        model = self.build()['model']
        gaps = model.tool_plane_gaps([], dict(x=[0, 180], y=[0, 500]))
        self.assertEqual(len(gaps), 5)
        self.assertEqual(next(g['gap_mm'] for g in gaps if g['surface'] == '背面墙'), 5)
        gaps = model.tool_plane_gaps([], dict(x=[0, 170], y=[0, 500]))
        self.assertEqual(next(g['gap_mm'] for g in gaps if g['surface'] == '背面墙'), -5)
        self.assertEqual(next(g['gap_mm'] for g in gaps if g['type'] == 'ground'), 285)

    def test_existing_safety_evaluator_sees_physical_tool_collision(self):
        from backend.core.safety import evaluate
        model = self.build()['model']
        settings = dict(mode='real', fence=dict(x=[0, 170], y=[0, 500]), ground_enabled=True,
                        safety_yellow_pct=90, safety_red_pct=95, safety_alarm_pct=100)
        report = evaluate(model, [0]*6, settings, lower=[-180]*6, upper=[180]*6)
        self.assertEqual(report['level'], 'alarm')
        self.assertTrue(any(c['type'] == 'fence' and c['gap_mm'] == -5 for c in report['collisions']))

    def test_invalid_model_offset_and_context_do_not_construct(self):
        for offset in (True, None, float('nan')):
            result = build_real_follow_model(self.mounted, self.tool, variant='test', config_revision=3,
                                             model_factory=FakeKinematics, flange_offset_z_mm=offset)
            self.assertFalse(result['ok'])
            self.assertIsNone(result['model'])
            self.assertEqual(result['blockers'][0]['code'], 'invalid_flange_offset')
        for revision in (None, True, -1, '3'):
            result = resolve_real_follow_tool(self.mounted, self.tool, variant='test', config_revision=revision)
            self.assertFalse(result['ok'])

    def test_worker_copy_has_independent_fk_cache_and_geometry(self):
        original = self.build()['model']
        original._fk_key = ('old',)
        worker = copy(original)
        self.assertIsNone(worker._fk_key)
        self.assertEqual(original._fk_key, ('old',))
        worker._fk_key = ('new',)
        self.assertEqual(worker._base._fk_key, ('new',))
        self.assertEqual(original._fk_key, ('old',))
        self.assertEqual(worker.capsules([]), original.capsules([]))

    def test_blocked_factory_never_called_and_constructor_failure_layered(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('Factory must not be called for blocked tool')
        self.mounted['state'] = 'unknown'
        self.assertIsNone(self.build(forbidden)['model'])
        self.mounted['state'] = 'manual_confirmed'
        result = self.build(forbidden)
        self.assertFalse(result['ok'])
        self.assertEqual(result['blockers'][0]['code'], 'construction_failed')

    def test_actual_kinematics_tcp_capsules_and_collision_share_one_frame(self):
        # These modules only read static model assets. Never import World,
        # ToolStore, config, main, drivers, runtime state or HTTP clients.
        from backend.core.kinematics import RobotModel, TCP_OFFSET_Z_MM, capsule_distance
        from backend.core.safety import tool_capsules
        result = build_real_follow_model(self.mounted, self.tool, variant='elfin5', config_revision=3,
                                         model_factory=RobotModel, flange_offset_z_mm=TCP_OFFSET_Z_MM)
        self.assertTrue(result['ok'], result)
        model = result['model']
        for q in ([0]*6, [0, -30, 60, 10, -20, 5]):
            matrix = model.fk(q)[model.flange]
            expected_tcp = [matrix[i][3]*1000+sum(matrix[i][j]*v for j, v in enumerate([10, 0, 50+TCP_OFFSET_Z_MM]))
                            for i in range(3)]
            for actual, expected in zip(model.pose_vec(q)[:3], expected_tcp):
                self.assertAlmostEqual(actual, expected, places=7)
            physical = tool_capsules(model, model.capsules(q, conservative=True))[-3:]
            expected_a = [matrix[i][3]*1000+sum(matrix[i][j]*v for j, v in enumerate([-40, 0, TCP_OFFSET_Z_MM]))
                          for i in range(3)]
            for actual, expected in zip(physical[0]['a'], expected_a):
                self.assertAlmostEqual(actual, expected, places=7)
            self.assertEqual(physical[0]['r_mm'], 10)
            obstacle = dict(a=list(physical[0]['a']), b=list(physical[0]['a']), r_mm=1)
            self.assertLess(capsule_distance(physical[0], obstacle), 0)
            worker = copy(model)
            self.assertIsNone(worker._fk_key)
            target = worker.pose_vec(q)
            solved, _, _, ok = worker.ik(q, target, worker.lower, worker.upper, restarts=False)
            self.assertTrue(ok)
            self.assertLess(math.dist(worker.pose_vec(solved)[:3], target[:3]), .01)

    def test_missing_profile_and_malformed_sections_are_structured_failures(self):
        for tool in (None, [], {}, 'unknown'):
            result = resolve_real_follow_tool(self.mounted, tool, variant='test', config_revision=3)
            self.assertFalse(result['ok'])
            self.assertTrue(result['blockers'])
        for name in ('mount', 'tcp', 'payload', 'geometry'):
            saved = self.tool[name]
            for value in (None, [], True, 'unknown'):
                self.tool[name] = value
                self.assertFalse(self.resolve()['ok'])
            self.tool[name] = saved

    def test_verification_status_alone_is_not_evidence(self):
        for name in ('mount', 'tcp', 'payload', 'geometry'):
            with self.subTest(name=name):
                saved = self.tool[name].pop('verification')
                self.assertIn('missing_record', self.codes())
                self.tool[name]['verification'] = saved

    def test_detected_confirmation_and_record_revision_type(self):
        self.mounted.update(state='detected_verified', source='detected', hardware_id='unit-test')
        self.tool['identity'] = dict(hardware_id='unit-test')
        self.assertTrue(self.resolve()['ok'])
        self.mounted['source'] = 'manual'
        self.assertIn('invalid_source', self.codes())
        self.mounted['source'] = 'detected'
        self.tool['tcp']['verification']['mounted_revision'] = True
        self.assertIn('stale_record', self.codes())


class CurrentGateRegression(unittest.TestCase):
    def test_current_non_none_is_rejected_even_with_verified_tcp_payload(self):
        path = Path(__file__).resolve().parents[1] / 'backend/core/world.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        world = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'World')
        method = next(n for n in world.body if isinstance(n, ast.FunctionDef) and n.name == '_real_jog_state')
        statement = next(n for n in method.body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == 'confirmed_none' for t in n.targets))
        scope = {'mounted': {'tool_id': 'actual-dual-cup', 'state': 'manual_confirmed'},
                 'tool': {'type': 'suction', 'tcp': {'status': 'verified'},
                          'payload': {'status': 'verified'}}}
        exec(compile(ast.Module(body=[statement], type_ignores=[]), str(path), 'exec'), scope)
        self.assertIs(scope['confirmed_none'], False)
        scope['mounted']['tool_id'] = 'none'
        scope['tool']['type'] = 'none'
        exec(compile(ast.Module(body=[statement], type_ignores=[]), str(path), 'exec'), scope)
        self.assertIs(scope['confirmed_none'], True)


if __name__ == '__main__':
    unittest.main()
