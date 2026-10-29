"""仿真取放生成器回归；仅经 run_offline_checks.py 在一次性副本执行。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.pick_place_program import generate_pick_place


def fixture(count=1):
    return {'items': [{'id': f'piece-{i + 1}', 'pick': [400, -350 + i, 50, 180, 0, 90],
                       'place': [400, 350 + i, 60, 180, 0, 90]} for i in range(count)],
            'wait': [350, 0, 500, 180, 0, 90], 'clearance': 100, 'speed': 10,
            'pick_wait': .2, 'blow_wait': .2, 'tool_id': 'vacuum-reference'}


def commands(source):
    return [line for line in source.splitlines() if line and not line.startswith('#')]


class PickPlaceChecks(unittest.TestCase):
    def test_one_cycle_exact_motion_tool_and_wait_order(self):
        result = generate_pick_place(fixture())
        self.assertEqual(result['cycles'], 1)
        self.assertEqual(commands(result['source']), [
            'REQUIRE_TOOL vacuum-reference', 'SPEED 10', 'TOOL VACUUM_OFF', 'TOOL BLOW_OFF',
            'MOVEL 350,0,500,180,0,90', 'MOVEL 400,-350,150,180,0,90',
            'MOVEL 400,-350,50,180,0,90', 'TOOL BLOW_OFF', 'TOOL VACUUM_ON', 'WAIT 0.2',
            'SIM_PICK piece-1', 'MOVEL 400,-350,150,180,0,90', 'MOVEL 400,350,160,180,0,90',
            'MOVEL 400,350,60,180,0,90', 'TOOL VACUUM_OFF', 'TOOL BLOW_ON', 'WAIT 0.2',
            'SIM_PLACE piece-1', 'TOOL BLOW_OFF', 'MOVEL 400,350,160,180,0,90',
            'MOVEL 350,0,500,180,0,90'])
        self.assertIn('# 仿真吸取放置程序', result['source'])
        self.assertNotRegex(result['source'], r'(?m)^(DOUT|MOVEJ|TOOL ATTACH|TOOL DETACH)\b')

    def test_wait_point_and_a18_b18_tables_for_nine_items(self):
        result = generate_pick_place(fixture(9))
        points = result['points']
        self.assertEqual(len(points), 37)
        self.assertEqual(points[0], {'name': 'WAIT', 'pose': fixture()['wait'], 'role': 'wait'})
        self.assertEqual(len([p for p in points if p['name'].startswith('A')]), 18)
        self.assertEqual(len([p for p in points if p['name'].startswith('B')]), 18)
        self.assertEqual(len({p['name'] for p in points}), 37)
        for index in range(9):
            rows = points[1 + 4 * index:5 + 4 * index]
            self.assertEqual([p['role'] for p in rows], ['pick_up', 'pick', 'place_up', 'place'])
            self.assertTrue(all(p['object_id'] == f'piece-{index + 1}' for p in rows))
            self.assertEqual(rows[0]['pose'][2] - rows[1]['pose'][2], 100)
            self.assertEqual(rows[2]['pose'][2] - rows[3]['pose'][2], 100)
            self.assertEqual(rows[0]['pose'][3:], rows[1]['pose'][3:])

    def test_maximum_27_cycles_are_bounded_and_return_to_wait_each_time(self):
        result = generate_pick_place(fixture(27))
        steps = commands(result['source'])
        self.assertEqual(result['cycles'], 27)
        self.assertEqual(len(result['points']), 109)
        self.assertEqual(len(steps), 4 + 17 * 27)
        self.assertEqual(sum(s.startswith('SIM_PICK ') for s in steps), 27)
        self.assertEqual(sum(s.startswith('SIM_PLACE ') for s in steps), 27)
        for index in range(27):
            cycle = steps[4 + 17 * index:4 + 17 * (index + 1)]
            self.assertEqual(cycle[0], steps[-1])
            self.assertEqual(cycle[-1], steps[-1])
            self.assertEqual(cycle[6], f'SIM_PICK piece-{index + 1}')
            self.assertEqual(cycle[13], f'SIM_PLACE piece-{index + 1}')
        self.assertLess(len(result['source']), 100000)

    def test_defaults_and_every_option_are_applied(self):
        data = fixture()
        minimal = {key: data[key] for key in ('items', 'wait')}
        self.assertEqual(generate_pick_place(data), generate_pick_place(minimal))
        data.update(clearance=250, speed=12.5, pick_wait=.125, blow_wait=1.5, tool_id='Vacuum_ref-2')
        result = generate_pick_place(data)
        steps = commands(result['source'])
        self.assertEqual(steps[:2], ['REQUIRE_TOOL Vacuum_ref-2', 'SPEED 12.5'])
        self.assertIn('WAIT 0.125', steps); self.assertIn('WAIT 1.5', steps)
        self.assertEqual(result['points'][1]['pose'][2], 300)
        self.assertEqual(result['points'][3]['pose'][2], 310)

    def test_compensated_plate_tcp_is_preserved_and_first_motion_is_wait_movel(self):
        data = fixture()
        data['wait'] = [400, 0, 450, 180, 0, 0]
        data['items'][0]['pick'] = [432.5, -350, 145.5, 180, 0, 0]
        data['items'][0]['place'] = [432.5, 350, 155.5, 180, 0, 0]
        result = generate_pick_place(data)
        rows = {point['role']: point['pose'] for point in result['points']}
        self.assertEqual(rows['pick'], data['items'][0]['pick'])
        self.assertEqual(rows['place'], data['items'][0]['place'])
        for role, original in (('pick_up', data['items'][0]['pick']), ('place_up', data['items'][0]['place'])):
            expected = original.copy(); expected[2] += 100
            self.assertEqual(rows[role], expected, 'must not apply a second suction cup compensation')
        moves = [line for line in commands(result['source']) if line.startswith('MOVEL ')]
        self.assertEqual(moves[0], 'MOVEL 400,0,450,180,0,0')
        self.assertEqual(moves[-1], moves[0])

    def test_output_is_detached_deterministic_and_input_is_not_modified(self):
        data = fixture(2); before = copy.deepcopy(data)
        first = generate_pick_place(data); second = generate_pick_place(data)
        self.assertEqual(first, second); self.assertEqual(data, before)
        first['points'][0]['pose'][0] = -900
        first['points'][2]['pose'][0] = -800
        self.assertEqual(data, before)
        self.assertEqual(second, generate_pick_place(data))
        self.assertNotEqual(first['points'][1]['pose'][0], first['points'][2]['pose'][0])

    def test_small_numeric_literals_never_use_exponents_and_match_table(self):
        data = fixture(); data['pick_wait'] = 1e-9; data['blow_wait'] = 1e-8
        data['items'][0]['pick'][0] = 1e-20
        data['items'][0]['place'][4] = -0.
        result = generate_pick_place(data)
        for step in commands(result['source']):
            if step.startswith(('MOVEL ', 'WAIT ', 'SPEED ')):
                self.assertNotRegex(step.split(' ', 1)[1], r'[eE]')
        self.assertIn('WAIT 0.000000001', commands(result['source']))
        rows = {tuple(p['pose']) for p in result['points']}
        for step in commands(result['source']):
            if step.startswith('MOVEL '):
                self.assertIn(tuple(map(float, step[6:].split(','))), rows)

    def test_unknown_missing_or_wrong_container_fields_are_rejected(self):
        for data in (None, [], {}, {'items': []}, {'wait': [0] * 6},
                     {**fixture(), 'extra': True}, {**fixture(), 'items': ()},
                     {**fixture(), 'wait': (0,) * 6}):
            with self.subTest(data_type=type(data).__name__), self.assertRaises(ValueError):
                generate_pick_place(data)
        for item in (None, [], {}, {**fixture()['items'][0], 'slot': 0},
                     {'id': 'piece', 'pick': [0] * 6}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                generate_pick_place({**fixture(), 'items': [item]})

    def test_counts_empty_duplicate_and_excess_are_rejected(self):
        for data in (fixture(0), fixture(28), {**fixture(), 'items': fixture()['items'] * 2}):
            with self.assertRaises(ValueError): generate_pick_place(data)

    def test_identifier_whitelist_blocks_script_injection_and_unicode(self):
        invalid = ['', 'x\nTOOL VACUUM_ON', 'x\rSIM_PICK y', 'x y', 'x\tY', 'x#comment',
                   'x;DOUT', '../x', 'x/y', 'x\\y', 'x\x00', '中文', 'Ａ', 'x\u2028Y',
                   'x' * 81, True, None, 12, {}, '_leading', '-leading']
        for field in ('id', 'tool_id'):
            for value in invalid:
                data = fixture()
                if field == 'id': data['items'][0]['id'] = value
                else: data[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    generate_pick_place(data)
        data = fixture(); data['items'][0]['id'] = 'a' * 80
        self.assertIn('SIM_PICK ' + 'a' * 80, generate_pick_place(data)['source'])

    def test_numeric_options_reject_bool_nan_inf_strings_and_out_of_bounds(self):
        limits = {'clearance': (1, 1000), 'speed': (1, 100), 'pick_wait': (0, 60), 'blow_wait': (0, 60)}
        for field, (low, high) in limits.items():
            for value in (True, False, None, '10', [], float('nan'), float('inf'), -float('inf'), low - .01, high + .01, 10**1000):
                with self.subTest(field=field, value_type=type(value).__name__), self.assertRaises(ValueError):
                    generate_pick_place({**fixture(), field: value})
            for value in (low, high):
                self.assertEqual(generate_pick_place({**fixture(), field: value})['cycles'], 1)

    def test_all_pose_axes_are_strict_and_generated_lift_is_revalidated(self):
        for target in ('wait', 'pick', 'place'):
            for value in (None, [], [0] * 5, [0] * 7, '0,0,0,0,0,0'):
                data = fixture()
                if target == 'wait': data[target] = value
                else: data['items'][0][target] = value
                with self.subTest(target=target), self.assertRaises(ValueError):generate_pick_place(data)
            for axis in range(6):
                limit = 10000 if axis < 3 else 360
                for value in (True, None, '0', float('nan'), float('inf'), limit + .01, -limit - .01, 10**1000):
                    data = fixture()
                    pose = data['wait'] if target == 'wait' else data['items'][0][target]
                    pose[axis] = value
                    with self.subTest(target=target, axis=axis, value_type=type(value).__name__), self.assertRaises(ValueError):
                        generate_pick_place(data)
        for target in ('pick', 'place'):
            data = fixture(); data['items'][0][target][2] = 9950
            with self.assertRaisesRegex(ValueError, '上方点'):generate_pick_place(data)

    def test_module_has_no_world_storage_camera_or_execution_dependencies(self):
        import ast
        import backend.core.pick_place_program as module
        tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom): imports.append(node.module)
        self.assertEqual(set(imports), {'__future__', 'decimal', 'math', 're'})
        self.assertNotIn('backend.core.world', sys.modules)
        self.assertNotIn('backend.main', sys.modules)

    def test_generated_program_uses_the_extended_execution_parser(self):
        from backend.core.program import parse
        for count in (1, 9, 27):
            data = fixture(count)
            data['speed'] = 12.5
            data['pick_wait'] = 1e-9
            generated = generate_pick_place(data)
            steps = parse(generated['source'], lambda _key: None)
            self.assertEqual(len(steps), len(commands(generated['source'])))
            self.assertEqual(sum(step['kind'] == 'movel' for step in steps), count * 8)
            self.assertEqual(sum(step['kind'] == 'wait' for step in steps), count * 2)
            self.assertEqual(sum(step.get('src', '').startswith('SIM_PICK ') for step in steps), count)
            self.assertEqual(sum(step.get('src', '').startswith('SIM_PLACE ') for step in steps), count)


if __name__ == '__main__':
    unittest.main(verbosity=2)
