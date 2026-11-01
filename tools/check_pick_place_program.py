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


class BoardToolChecks(unittest.TestCase):
    def test_owned_piece_only_exempts_contact_pads_not_body_arm_or_real_mode(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from backend.core.planning import inspect_path
        piece={'id':'piece','name':'piece','kind':'box','size':[10,10,10],'xyz':[0,0,100]}
        model=SimpleNamespace(flange='end',base='base',vmax=[180]*6,
            jacobian=lambda _q:[[float(i==j) for j in range(6)] for i in range(6)],
            flange_pose=lambda _q:{'xyz_mm':[0,0,100]})
        settings={'mode':'sim','vel_source':'urdf','sim_speed_pct':100,'fence':{'enabled':False},'ground_enabled':False}
        cap={'link':'end','a':[0,0,100],'b':[0,0,100],'r_mm':2,'contact_allowed':True}
        green={'max_pct':0,'collisions':[],'level':'green','reasons':[]}
        with patch('backend.core.planning.evaluate',return_value=green):
            for link,allowed,mode,risk in (('end',True,'sim',False),('end',False,'sim',True),('wrist',False,'sim',True),('end',True,'real',True)):
                model.capsules=lambda *_args,**_kw:[{**cap,'link':link,'contact_allowed':allowed}]
                report=inspect_path(model,[0]*6,[0]*6,{**settings,'mode':mode},[-360]*6,[360]*6,[piece],contact_ids={'piece'})
                self.assertEqual(report['risk_detected'],risk,(link,allowed,mode))

    def test_official_2f85_parts_cover_actual_source_vertices_across_closure(self):
        import numpy as np
        from tools.build_tool_assets import animation_source,animation_matrices
        from backend.core.tool_contact_geometry import asset,capsules
        from backend.core.kinematics import rpy_to_mat
        from backend.core.objects import rotation_matrix
        parts,rig=animation_source('robotiq-2f85')
        original=animation_matrices(rig,0.)
        rotation=rpy_to_mat(.3,-.6,1.1)
        flange=np.asarray(rotation);flange[:3,3]=[.13,-.24,.48]
        for closure in (0.,.3,.7,1.):
            caps=capsules(flange.tolist(),np.eye(4).tolist(),closure,'end')
            self.assertEqual(len(caps),len(parts))
            target=animation_matrices(rig,closure)
            for part,cap in zip(parts,caps):
                matrix=flange@target[part['link']]@np.linalg.inv(original[part['link']])
                points=part['triangles'].reshape(-1,3)@matrix[:3,:3].T+matrix[:3,3]
                proxy=cap['convex_proxy'];r=np.asarray(rotation_matrix(proxy))
                local=(points*1000-np.asarray(proxy['xyz']))@r
                self.assertTrue(np.all(np.abs(local)<=np.asarray(proxy['size'])/2+.0001))
                self.assertEqual(cap['contact_allowed'],part['link'].endswith('finger_pad'))

    def test_broken_2f85_asset_fails_closed_and_real_width_changes_narrow_phase(self):
        import hashlib
        from backend.core.tool_contact_geometry import inspect_asset,capsules
        from backend.core import tool_library
        from backend.core.objects import capsule_object_gap
        from backend.core.kinematics import _mat_identity
        entry=copy.deepcopy(tool_library.get('robotiq-2f85'))
        blob=(tool_library.CATALOG.parent/'robotiq-2f85.glb').read_bytes()
        for broken in (blob[:-1],b'broken'):
            with self.assertRaises(ValueError):inspect_asset(broken,entry)
        modified=bytearray(blob);modified[-10]^=1
        with self.assertRaises(ValueError):inspect_asset(bytes(modified),entry)
        # A point in the empty open jaw is not a collision with a giant sphere.
        item={'id':'probe','name':'probe','kind':'box','size':[4,4,4],'xyz':[0,0,143]}
        open_caps=capsules(_mat_identity(),_mat_identity(),0.,'end')
        self.assertTrue(all(capsule_object_gap(cap,item)>=0 for cap in open_caps))
        closed_caps=capsules(_mat_identity(),_mat_identity(),1.,'end')
        self.assertTrue(any(capsule_object_gap(cap,item)<0 for cap in closed_caps))

    def test_gripper_script_uses_close_open_not_vacuum(self):
        data=fixture();data.update(tool_id='robotiq-2f85',pick_wait=0,blow_wait=0)
        result=generate_pick_place(data)
        steps=commands(result['source'])
        self.assertEqual(steps[:3],['REQUIRE_TOOL robotiq-2f85','SPEED 10','TOOL OPEN'])
        self.assertIn('TOOL CLOSE',steps)
        self.assertNotRegex(result['source'],r'TOOL (VACUUM|BLOW)')
        self.assertLess(steps.index('TOOL CLOSE'),steps.index('SIM_PICK piece-1'))
        self.assertEqual(steps[steps.index('SIM_PLACE piece-1')-1],'TOOL OPEN')

    def test_each_game_chooses_real_asset_contract_not_fake_claw(self):
        from backend.application.board_tools import tool_id_for,ensure_board_tool
        from types import SimpleNamespace
        calls=[]
        w=SimpleNamespace(settings={'mode':'sim'},real=None,
            set_sim_tool=lambda value:calls.append(value) or {'ok':True},
            ensure_sim_task_tool=lambda name:calls.append(name) or {'ok':True})
        for game in ('chess','xiangqi'):
            self.assertEqual(tool_id_for(game),'robotiq-2f85');ensure_board_tool(w,game)
            self.assertEqual(calls[-1],{'kind':'library','id':'robotiq-2f85','rotation':[0,0,0]})
        for game in ('ttt','gomoku','go'):
            self.assertEqual(tool_id_for(game),'vacuum-reference');ensure_board_tool(w,game)
            self.assertEqual(calls[-2],'chess')
            self.assertEqual(calls[-1]['kind'],'vacuum-reference')
            self.assertEqual(calls[-1]['dimensions']['mountHeight'],40)
            self.assertEqual(calls[-1]['dimensions']['cupHeight'],30)
        custom=copy.deepcopy(calls[-1]);custom['dimensions']['length']=280
        w.settings['sim_tool']=custom
        ensure_board_tool(w,'ttt')
        self.assertEqual(calls[-1],'chess','board auto-tool must preserve customized physical dimensions')
        before=calls[:];w.settings['mode']='real'
        with self.assertRaisesRegex(ValueError,'标定'):ensure_board_tool(w,'chess')
        self.assertEqual(calls,before,'real hardware must never be changed by board auto-tool')

    def test_gripper_width_matches_actual_pad_fk_and_stays_above_support(self):
        from backend.application.board_tools import grasp_geometry,contact_pose,_pad_geometry
        descriptor={'kind':'library','id':'robotiq-2f85','rotation':[0,0,0]}
        for radius,height in ((14,34),(14,52),(19,7)):
            piece={'kind':'cylinder','size':[radius,radius,height],'xyz':[-500,0,180+height/2]}
            geometry=grasp_geometry(descriptor,piece)
            gap,tip=_pad_geometry(geometry['closure'])
            self.assertAlmostEqual(gap,radius*2,delta=.0001)
            self.assertAlmostEqual(_pad_geometry(geometry['open_closure'])[0],radius*2+3,delta=.0001)
            pose=contact_pose(descriptor,piece)
            lower_pad_z=pose[2]-(tip-130.324)
            self.assertGreater(lower_pad_z,180)
            self.assertLess(lower_pad_z,180+height,'pad must actually overlap upper part of piece')
        with self.assertRaisesRegex(ValueError,'开口'):
            grasp_geometry(descriptor,{'kind':'cylinder','size':[100,100,20],'xyz':[0,0,20]})

    def test_bound_opening_sweeps_between_dense_pieces_without_hiding_them(self):
        from backend.application.board_tools import contact_pose,grasp_geometry
        from backend.core.tool_contact_geometry import capsules
        from backend.core.kinematics import rpy_to_mat
        from backend.core.objects import capsule_object_gap
        import math
        descriptor={'kind':'library','id':'robotiq-2f85','rotation':[0,0,0]}
        for radius,height,pitch in ((14,34,45),(15,7,40)):
            piece={'kind':'cylinder','size':[radius,radius,height],'xyz':[-500,0,180+height/2]}
            pose=contact_pose(descriptor,piece);grasp=grasp_geometry(descriptor,piece)
            matrix=rpy_to_mat(math.pi,0,0)
            for i in range(3):matrix[i][3]=(pose[i]-matrix[i][2]*130.324)/1000
            neighbours=[{**piece,'xyz':[-500+dx,dy,180+h/2],'size':[radius,radius,h]}
                        for dx,dy,h in ((0,pitch,height),(0,-pitch,height),(-pitch,0,52 if height>10 else height))]
            for step in range(6):
                closure=grasp['open_closure']+(grasp['closure']-grasp['open_closure'])*step/5
                for cap in capsules(matrix,rpy_to_mat(0,0,0),closure,'end'):
                    self.assertTrue(all(capsule_object_gap(cap,other)>=0 for other in neighbours),
                                    (radius,step,cap['part']))

    def test_left_cup_for_front_board_does_not_reuse_far_right_contact(self):
        from backend.application.board_tools import contact_pose
        from backend.core import sim_tool
        descriptor=sim_tool.normalize(sim_tool.DEFAULT_TOOL)
        piece={'kind':'cylinder','size':[10,10,8],'xyz':[-500,260,184]}
        left=contact_pose(descriptor,piece,cup_side='left')
        right=contact_pose(descriptor,piece,cup_side='right')
        self.assertGreater(left[0],-500);self.assertLess(right[0],-500)
        self.assertEqual(left[1:],right[1:])

    def test_vacuum_board_yaw_preserves_real_selected_cup_contact_at_both_ends(self):
        from backend.application.board_tools import contact_pose,grasp_geometry
        from backend.core import sim_tool
        from backend.core.kinematics import rpy_to_mat
        import math
        descriptor=sim_tool.normalize(sim_tool.DEFAULT_TOOL)
        piece={'kind':'box','size':[82,82,18],'xyz':[-480,380,189]}
        offset=grasp_geometry(descriptor,piece,cup_side='left')['offset']
        for yaw in range(0,361,45):
            for xyz in (piece['xyz'],[-420,-260,169]):
                pose=contact_pose(descriptor,piece,xyz,cup_side='left',yaw=yaw)
                rotation=rpy_to_mat(math.pi,0,math.radians(yaw))
                actual=[pose[i]+sum(rotation[i][j]*offset[j] for j in range(3)) for i in range(3)]
                for i in range(3):self.assertAlmostEqual(actual[i],xyz[i]+(9 if i==2 else 0),places=8)
                self.assertEqual(pose[3:],[180,0,yaw])
        for invalid in (True,float('nan'),float('inf'),-1,361,'90'):
            with self.assertRaises(ValueError):contact_pose(descriptor,piece,yaw=invalid)

    def test_vacuum_yaw_probe_checks_all_four_endpoints_and_refuses_wrist_hits_or_cancel(self):
        from backend.application.board_tools import probe_vacuum_yaw
        from backend.core import sim_tool
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        piece={'id':'target','kind':'box','size':[82,82,18],'xyz':[-480,380,189]}
        other={'id':'other','kind':'box','size':[82,82,18],'xyz':[-480,260,189]}
        model=SimpleNamespace(ik=Mock(return_value=([0]*6,0.,0.,True)))
        w=SimpleNamespace(settings={'mode':'sim','sim_tool':sim_tool.DEFAULT_TOOL},real=None,
            objects=SimpleNamespace(list=lambda:[piece,other]),motion_model=lambda:model,
            limits=lambda:([-360]*6,[360]*6),current_joints=lambda:[0]*6)
        with patch('backend.core.planning.inspect_path',return_value={'risk_detected':False}) as inspect:
            result=probe_vacuum_yaw(w,piece,[-420,-260,169],90)
            self.assertTrue(result['ok']);self.assertEqual(inspect.call_count,4)
            for index,call in enumerate(inspect.call_args_list):
                self.assertEqual(len(call.args[6]),2,'all non-target pieces must remain obstacles')
                self.assertEqual(call.kwargs['contact_ids'],{'target'})
                self.assertEqual(call.args[6][0]['xyz'],piece['xyz'] if index<2 else [-420,-260,169])
            model.ik.reset_mock()
            self.assertFalse(probe_vacuum_yaw(w,piece,[-420,-260,169],90,cancelled=lambda:True)['ok'])
            model.ik.assert_not_called()
        hit={'risk_detected':True,'issues':[{'collisions':[{'type':'object','link':'elfin_link5','object':'other'}]}]}
        with patch('backend.core.planning.inspect_path',return_value=hit):
            self.assertFalse(probe_vacuum_yaw(w,piece,[-420,-260,169],0)['ok'],'wrist collisions must reject a cup-clear candidate')


if __name__ == '__main__':
    unittest.main(verbosity=2)
