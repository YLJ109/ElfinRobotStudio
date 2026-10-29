"""独立 core 边界回归；只允许在离线临时副本运行。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import config, program_store, scenes, storage, points, calibration, tools, ai_plan, objects
from types import SimpleNamespace
from backend.core.program import parse, ProgramError
from backend.core.python_program import Interpreter


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = patch.object(config, 'DATA_DIR', self.root)
        self.data.start()

    def tearDown(self):
        self.data.stop()
        self.temp.cleanup()

    def test_atomic_json_failure_preserves_file_and_cleans_temp(self):
        path = self.root / 'data.json'
        storage.write_json(path, {'old': 1})
        with self.assertRaises(ValueError):
            storage.write_json(path, {'bad': math.nan})
        self.assertEqual(json.loads(path.read_text()), {'old': 1})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_program_failure_is_transactional(self):
        store = program_store.ProgramStore()
        store.put('old', 'WAIT 0')
        before = copy.deepcopy(store.items)
        with patch.object(program_store, 'write_json', side_effect=OSError('磁盘已满')):
            with self.assertRaises(OSError): store.put('new', 'WAIT 1')
            self.assertEqual(store.items, before)
            with self.assertRaises(OSError): store.remove('old')
            self.assertEqual(store.items, before)

    def test_program_source_is_bounded_text(self):
        store = program_store.ProgramStore()
        for value in (None, {}, 'x' * 200001):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError): store.put('bad', value)
        self.assertEqual(store.items, {})

    def test_program_snapshot_is_detached(self):
        store = program_store.ProgramStore()
        row = store.put('a', 'WAIT 0')
        row['src'] = 'INVALID'
        store.get('a')['src'] = 'INVALID'
        self.assertEqual(store.get('a')['src'], 'WAIT 0')

    def test_scene_failure_is_transactional(self):
        store = scenes.SceneStore(self.root / 'scenes.json')
        store.put('old', [])
        before = copy.deepcopy(store.items)
        with patch.object(scenes, 'write_json', side_effect=OSError('磁盘已满')):
            with self.assertRaises(OSError): store.put('new', [])
            self.assertEqual(store.items, before)
            with self.assertRaises(OSError): store.remove('old')
            self.assertEqual(store.items, before)

    def test_scene_nonfinite_rejected_without_mutation(self):
        store = scenes.SceneStore(self.root / 'scenes.json')
        with self.assertRaises(ValueError): store.put('bad', [{'xyz': [0, 0, math.inf]}])
        self.assertEqual(store.items, {})

    def test_scene_names_cannot_escape(self):
        store = scenes.SceneStore(self.root / 'scenes.json')
        for name in ('../outside', '..\\outside', 'C:\\escape', 'a\nname'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError): store.put(name, [])
        self.assertFalse((self.root.parent / 'outside').exists())

    def test_legacy_motion_rejects_nonfinite(self):
        for command in ('MOVEJ', 'MOVEL'):
            for value in ('nan', 'inf', '1e999'):
                with self.subTest(command=command, value=value):
                    with self.assertRaises(ProgramError):
                        parse(f'{command} 0,0,0,0,0,{value}', lambda ref: None)

    def test_point_motion_rejects_nonfinite(self):
        point = {'id': 1, 'name': 'P1', 'joints': [0] * 5 + [math.nan],
                 'tcp': {'xyz_mm': [0, 0, math.inf], 'rpy_deg': [0, 0, 0]}}
        for command in ('MOVEJ', 'MOVEL'):
            with self.assertRaises(ProgramError): parse(command + ' P1', lambda ref: point)

    def interpreter(self, source, params=None):
        return Interpreter({'source': source, 'params': params or {}, 'points': []})

    def test_parameters_reject_host_objects_and_nonfinite(self):
        for value in (object(), {'x': math.nan}, [0] * 10001):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError): self.interpreter('WAIT(0)', {'a': value})

    def test_parameter_alias_is_copied(self):
        value = [1]
        list(self.interpreter('a.append(2)', {'a': value}).run())
        self.assertEqual(value, [1])

    def test_dict_keyword_update_enforces_bound(self):
        script = self.interpreter('d = dict(zip(range(10000), range(10000)))\nd.update(extra=1)')
        with self.assertRaises(ProgramError): list(script.run())

    def test_subscript_insertion_enforces_bound(self):
        script = self.interpreter('d = dict(zip(range(10000), range(10000)))\nd[10000] = 1')
        with self.assertRaises(ProgramError): list(script.run())

    def test_valid_commands_and_parameters_remain_compatible(self):
        self.assertEqual(parse('MOVEJ 1,2,3,4,5,6', lambda ref: None)[0]['joints'], [1,2,3,4,5,6])
        steps = list(self.interpreter('WAIT(a[0])', {'a': '[0.1]'}).run())
        self.assertEqual(steps[0]['sec'], .1)

    def test_point_rejects_nonfinite_tcp_and_boolean_joint(self):
        store = points.PointStore()
        with self.assertRaises(ValueError): store.add('a', [True] * 6)
        with self.assertRaises(ValueError):
            store.add('a', [0] * 6, tcp={'xyz_mm': [0, 0, math.nan]})
        self.assertEqual(store.items, [])

    def test_point_failed_write_preserves_memory(self):
        store = points.PointStore()
        row = store.add('a', [0] * 6)
        before = copy.deepcopy(store.items)
        with patch.object(points, 'write_json', side_effect=OSError('磁盘已满')):
            with self.assertRaises(OSError): store.update(row['id'], {'name': 'b'})
            self.assertEqual(store.items, before)
            with self.assertRaises(OSError): store.remove(row['id'])
            self.assertEqual(store.items, before)
            with self.assertRaises(OSError): store.add('b', [0] * 6)
            self.assertEqual(store.items, before)

    def test_calibration_failed_write_preserves_version_and_data(self):
        store = calibration.CalibrationStore()
        before = copy.deepcopy(store.items)
        with patch.object(calibration, 'write_json', side_effect=OSError('磁盘已满')):
            with self.assertRaises(OSError): store.save('frames', 'a', {'matrix': []})
        self.assertEqual(store.items, before)
        with self.assertRaises(ValueError): store.save('frames', 'bad', {'x': math.nan})
        self.assertEqual(store.items, before)

    def test_tool_failed_write_preserves_profile(self):
        store = tools.ToolStore()
        before = store.list()
        with patch.object(tools, 'write_json', side_effect=OSError('磁盘已满')):
            with self.assertRaises(OSError): store.add_or_update({'id': 'none', 'name': 'bad'})
        self.assertEqual(store.list(), before)

    def test_tool_rejects_nonfinite_orientation(self):
        store = tools.ToolStore()
        before = store.list()
        with self.assertRaises(ValueError):
            store.add_or_update({'id': 'bad', 'tcp': {'rpy_deg': [0, math.inf, 0]}})
        self.assertEqual(store.list(), before)

    def test_ai_dout_never_truncates_or_accepts_booleans(self):
        model = SimpleNamespace(lower=[-360] * 6, upper=[360] * 6)
        for field, value in (('bit', 1.9), ('val', .9), ('bit', True), ('val', False), ('bit', math.inf)):
            op = {'op': 'dout', 'bit': 1, 'val': 0, field: value}
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError): ai_plan.validate({'plan': [op]}, [], model)
        self.assertEqual(ai_plan.validate({'plan': [{'op': 'dout', 'bit': 1.0, 'val': 0}]}, [], model)['plan'][0]['bit'], 1)

    def test_ai_all_motion_values_are_finite_nonboolean(self):
        model = SimpleNamespace(lower=[-360] * 6, upper=[360] * 6)
        for op in ({'op': 'movej', 'joints': [True] * 6}, {'op': 'movej', 'joints': [math.inf] * 6},
                   {'op': 'speed', 'pct': True}, {'op': 'wait', 'sec': False}):
            with self.subTest(op=op):
                with self.assertRaises(ValueError): ai_plan.validate({'plan': [op]}, [], model)
        bad = {'id': 1, 'name': 'bad', 'joints': [math.nan] * 6}
        with self.assertRaises(ValueError): ai_plan.validate({'plan': [{'op': 'movej', 'point': 'bad'}]}, [bad], model)

    def test_ai_point_values_are_copied_and_limit_checked(self):
        model = SimpleNamespace(lower=[-360] * 6, upper=[360] * 6)
        point = {'id': 1, 'name': 'a', 'joints': [0] * 6}
        result = ai_plan.validate({'plan': [{'op': 'movej', 'point': 'a'}]}, [point], model)
        result['plan'][0]['joints'][0] = 999
        self.assertEqual(point['joints'][0], 0)
        point['joints'][0] = 999
        with self.assertRaises(ValueError): ai_plan.validate({'plan': [{'op': 'movej', 'point': 'a'}]}, [point], model)

    def test_objects_failed_import_preserves_scene(self):
        store = objects.ObjectStore(self.root / 'objects.json')
        store.add({'name': 'old'})
        before = store.list()
        with patch.object(objects, 'write_json', side_effect=OSError('磁盘已满')):
            with self.assertRaises(OSError): store.replace([{'name': 'new'}])
        self.assertEqual(store.list(), before)

    def test_objects_snapshots_do_not_alias_coordinates(self):
        store = objects.ObjectStore(self.root / 'objects.json')
        result = store.add({'xyz': [1, 2, 3]})
        result['xyz'][0] = 999
        store.list()[0]['xyz'][1] = 999
        self.assertEqual(store.list()[0]['xyz'], [1, 2, 3])

    def test_tool_mounted_failure_preserves_confirmation(self):
        store = tools.ToolStore()
        before = copy.deepcopy(store.mounted)
        with patch.object(tools, 'write_json', side_effect=OSError('磁盘已满')):
            for action in (lambda: store.select('none'), store.clear,
                           lambda: store.mark_unknown('test'), lambda: store.record_action('open', 'done')):
                with self.assertRaises(OSError): action()
                self.assertEqual(store.mounted, before)

    def test_json_replace_failure_preserves_previous_document(self):
        path = self.root / 'atomic.json'
        storage.write_json(path, {'old': 1})
        with patch.object(storage.os, 'replace', side_effect=OSError('替换失败')):
            with self.assertRaises(OSError): storage.write_json(path, {'new': 2})
        self.assertEqual(json.loads(path.read_text()), {'old': 1})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_point_second_write_failure_only_reserves_code(self):
        store = points.PointStore()
        original = points.write_json
        def fail_document(path, value):
            if path == store.path: raise OSError('点位写入失败')
            return original(path, value)
        with patch.object(points, 'write_json', side_effect=fail_document):
            with self.assertRaises(OSError): store.add('a', [0] * 6)
        self.assertEqual(store.items, [])
        self.assertEqual(store.next_number, 2)
        self.assertEqual(points.PointStore().add('b', [0] * 6)['code'], 'P2')

    def test_objects_all_failed_edits_preserve_scene_and_velocity(self):
        store = objects.ObjectStore(self.root / 'objects.json')
        row = store.add({'name': 'old', 'xyz': [1, 2, 3]})
        store._vel[row['id']] = -10
        before = store.list()
        with patch.object(objects, 'write_json', side_effect=OSError('磁盘已满')):
            for action in (lambda: store.add({'name': 'new'}), lambda: store.update(row['id'], {'xyz': [9, 8, 7]}),
                           lambda: store.remove(row['id']), store.clear):
                with self.assertRaises(OSError): action()
                self.assertEqual(store.list(), before)
                self.assertEqual(store._vel[row['id']], -10)

    def test_legacy_expansion_has_total_step_budget(self):
        source = 'LOOP 200\n' + 'WAIT 0\n' * 51 + 'END'
        with self.assertRaises(ProgramError): parse(source, lambda ref: None)

    def test_settings_write_failure_preserves_live_values(self):
        with patch.object(config, '_settings', copy.deepcopy(config.DEFAULTS)):
            live = config.get_settings()
            before = copy.deepcopy(live)
            with patch.object(config, 'write_json', side_effect=OSError('磁盘已满')):
                with self.assertRaises(OSError): config.save_settings({'log_keep': 5})
            self.assertEqual(live, before)

    def test_settings_defaults_are_not_mutated_by_live_nested_values(self):
        before = copy.deepcopy(config.DEFAULTS)
        try:
            with patch.object(config, '_settings', None):
                config.get_settings()['fence']['x'][0] = -123
                self.assertEqual(config.DEFAULTS, before)
        finally:
            config.DEFAULTS.clear()
            config.DEFAULTS.update(before)


if __name__ == '__main__':
    unittest.main()
