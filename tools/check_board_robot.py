"""Isolated board layout, lifecycle, contact and actual RobotPlay execution."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import math
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.application.board_games import GameStore
from backend.application.board_robot import BoardExecutor, BoardRobotError, LAYOUTS, layout_for, cell_center, tray_center, piece_shape
from backend.application.board_tools import tool_id_for
from backend.core.objects import half_height
from backend.core.world import World
from backend.robot_play_routes import RobotPlay
from backend.game_routes import create_game_router
from fastapi import FastAPI
import httpx


class BoardLayoutCases(unittest.TestCase):
    def test_front_rows_and_columns_match_an_observer_on_negative_x(self):
        for game in LAYOUTS:
            with self.subTest(game=game):
                layout = layout_for(game)
                first = cell_center(game, 0, 0)
                lower = cell_center(game, layout['rows'] - 1, 0)
                right = cell_center(game, 0, layout['cols'] - 1)
                self.assertLess(lower[0], first[0])
                self.assertLess(right[1], first[1])
                self.assertEqual(lower[1], first[1])
                self.assertEqual(right[0], first[0])
                self.assertEqual(layout['axis_col'], [0., -1., 0.])
                self.assertEqual(layout['axis_row'], [-1., 0., 0.])
                self.assertLess(layout['center_mm'][0], 0)
                self.assertGreater(layout['board_top_mm'], 120)
                self.assertGreater(layout['tray_top_mm'], 90)

    def test_centered_chess_and_separated_drop_boards(self):
        for game in LAYOUTS:
            layout = layout_for(game)
            if game in ('chess', 'xiangqi'):
                self.assertEqual(layout['center_mm'][1], 0)
            else:
                self.assertGreater(layout['center_mm'][1], 0)
                self.assertLess(layout['tray_center_mm'][1], 0)
            self.assertLess(layout['tray_center_mm'][1] + layout['tray_size_mm'][1] / 2,
                            layout['center_mm'][1] - layout['board_size_mm'][1] / 2)

    def test_every_supply_slot_stays_inside_the_physical_rim(self):
        for game in LAYOUTS:
            layout = layout_for(game)
            for index in range(layout['tray_cols'] * layout['tray_rows']):
                xyz = tray_center(game, index)
                code = 'O' if game == 'ttt' else 'K' if game == 'chess' else 'P' if game == 'xiangqi' else 'black'
                shape = piece_shape(game, code, 'human')
                extent = shape['size'][0] if shape['kind'] == 'cylinder' else shape['size'][0] / 2
                for axis in range(2):
                    self.assertLessEqual(abs(xyz[axis] - layout['tray_center_mm'][axis]) + extent,
                                         layout['tray_size_mm'][axis] / 2 - layout['rim_width_mm'])


class BoardRobotCases(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = World(data_dir=Path(self.temp.name))
        self.w.objects.clear()
        self.w.settings['mode'] = 'sim'
        self.w.real = None
        self.w.set_speed(100, schedule=False)
        self.play = RobotPlay(self.w)
        self.w.robot_play = self.play
        self.games = GameStore()
        self.board = BoardExecutor(self.w, self.games, self.play)

    async def asyncTearDown(self):
        await self.w.stop_execution('隔离棋类检查结束')

    async def pump(self, predicate, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.w._tick(.05, time.monotonic())
            if predicate():
                return
            if self.w.runner.state == 'aborted':
                self.fail('棋类执行中止：' + self.w.runner.error)
            await asyncio.sleep(.002)
        self.fail('棋类执行超时：' + str(self.w.runner.snapshot()))

    async def begin(self, game):
        self.games.new(game, {'opponent': 'human'})
        self.board.ensure(game, force=True)
        started = await self.board.start(game)
        self.assertTrue(started['ok'], started)
        await self.pump(lambda: self.w.runner.state == 'finished')
        done = await self.board.step(game, self.board.group)
        self.assertTrue(done['done'], done)
        self.assertTrue(self.board.active)

    async def test_build_all_five_real_collision_boards_and_matching_tools(self):
        for game in LAYOUTS:
            with self.subTest(game=game):
                self.games.new(game)
                self.board.ensure(game, force=True)
                objects = self.w.objects.list()
                self.assertTrue(all(p['collide'] for p in objects))
                supports = [p for p in objects if p['task']['role'] == 'obstacle']
                self.assertEqual(len(supports), 6)
                self.assertEqual({p['task']['code'] for p in supports}, {'board', 'tray', 'tray-rim'})
                for p in self.board._members():
                    self.assertEqual(p['xyz'][2] - half_height(p),
                                     layout_for(game)['board_top_mm'] if p['task']['slot'] >= 0 else layout_for(game)['tray_top_mm'])
                descriptor = self.w.settings['sim_tool']
                self.assertEqual(descriptor.get('id') if descriptor['kind'] == 'library' else 'vacuum-reference', tool_id_for(game))
                self.assertEqual(self.w.settings['sim_speed_pct'], 100)

    async def test_pause_waiting_user_resume_and_abort_revokes_old_session(self):
        await self.begin('ttt')
        session = self.board.group
        self.assertTrue((await self.board.pause('ttt', session))['ok'])
        self.assertTrue(self.board.status('ttt')['paused'])
        self.assertFalse(self.board.status('chess')['active'])
        with self.assertRaises(BoardRobotError):
            self.board.require_session('ttt', session)
        self.assertTrue((await self.board.resume('ttt', session))['ok'])
        self.assertTrue((await self.board.abort('ttt', session))['ok'])
        with self.assertRaises(BoardRobotError):
            await self.board.step('ttt', session)
        self.games.new('ttt')
        self.board.ensure('ttt', force=True)
        with self.assertRaises(BoardRobotError):
            await self.board.abort('ttt', session)
        self.assertTrue(self.board.active)

    async def test_physical_scene_rejects_existing_obstacle_without_erasing_it(self):
        obstacle = self.w.objects.add({'name': '棋盘占用', 'kind': 'box', 'size': [20, 20, 20],
                                       'xyz': [-500, 260, 50], 'floating': True})
        before = self.w.objects.list()
        with self.assertRaises(ValueError):
            self.board.ensure('ttt', force=True)
        self.assertEqual(self.w.objects.list(), before)
        self.assertIsNotNone(obstacle)

    async def test_late_preparation_after_abort_cannot_start_motion(self):
        self.games.new('ttt', {'opponent': 'human'})
        self.board.ensure('ttt', force=True)
        self.games.move('ttt', {'to': [1, 1]}, auto_reply=False)
        self.board.note('ttt')
        entered, release = asyncio.Event(), asyncio.Event()

        async def late_generate(*_args, **_kwargs):
            self.play.busy = True
            entered.set()
            await release.wait()
            self.play.busy = False
            self.play.plans['late'] = {}
            return {'plan_id': 'late', 'source': 'WAIT 0'}

        with patch('backend.application.board_tools.probe_vacuum_yaw', return_value={'ok': True}), \
                patch.object(self.play, 'generate', late_generate), patch.object(self.play, 'run') as run:
            job = asyncio.create_task(self.board.step('ttt', self.board.group))
            await asyncio.wait_for(entered.wait(), 20)
            await self.board.abort('ttt', self.board.group)
            release.set()
            with self.assertRaises(BoardRobotError):
                await job
            run.assert_not_called()
        self.assertFalse(self.board.preparing)
        self.assertNotIn('late', self.play.plans)
        self.assertFalse(self.board.active)

    async def test_pause_during_preparation_preserves_action_without_running(self):
        self.games.new('ttt', {'opponent': 'human'})
        self.board.ensure('ttt', force=True)
        self.games.move('ttt', {'to': [1, 1]}, auto_reply=False)
        self.board.note('ttt')
        entered, release = asyncio.Event(), asyncio.Event()

        async def late_generate(*_args, **_kwargs):
            entered.set()
            await release.wait()
            self.play.plans['paused'] = {}
            return {'plan_id': 'paused', 'source': 'WAIT 0'}

        with patch('backend.application.board_tools.probe_vacuum_yaw', return_value={'ok': True}), \
                patch.object(self.play, 'generate', late_generate), patch.object(self.play, 'run') as run:
            job = asyncio.create_task(self.board.step('ttt', self.board.group))
            await asyncio.wait_for(entered.wait(), 20)
            self.assertTrue((await self.board.pause('ttt', self.board.group))['ok'])
            release.set()
            self.assertTrue((await job)['paused'])
            run.assert_not_called()
        self.assertEqual(len(self.board.queue), 1)
        self.assertNotIn('paused', self.play.plans)
        self.assertTrue((await self.board.resume('ttt', self.board.group))['ok'])

    async def test_failed_scene_new_and_missing_piece_move_do_not_change_2d_game(self):
        app = FastAPI()
        app.include_router(create_game_router(self.games, self.board))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://offline') as client:
            self.games.new('ttt', {'opponent': 'human'})
            self.games.move('ttt', {'to': [0, 0]}, auto_reply=False)
            before = self.games.snapshot('ttt')
            self.w.objects.add({'kind': 'box', 'size': [20, 20, 20], 'xyz': [-500, 260, 50]})
            result = (await client.post('/api/games/ttt/new', json={'opponent': 'human'})).json()
            self.assertFalse(result['ok'])
            self.assertEqual(before, self.games.snapshot('ttt'))
            self.w.objects.clear()
            self.games.new('ttt', {'opponent': 'human'})
            self.board.ensure('ttt', force=True)
            self.w.objects.replace([p for p in self.w.objects.list() if p['task']['code'] != 'O'])
            before = self.games.snapshot('ttt')
            result = (await client.post('/api/games/ttt/move', json={'to': [1, 1], 'session_id': self.board.group})).json()
            self.assertFalse(result['ok'])
            self.assertEqual(before, self.games.snapshot('ttt'))
            self.assertEqual(self.board.queue, [])

    async def test_pause_during_actual_carry_then_resume_finishes_same_piece(self):
        await self.begin('ttt')
        self.games.move('ttt', {'to': [1, 1]}, auto_reply=False)
        self.board.note('ttt')
        await self.board.step('ttt', self.board.group)
        await self.pump(lambda: self.play.held is not None)
        oid = self.play.held['id']
        self.assertTrue((await self.board.pause('ttt', self.board.group))['ok'])
        q = self.w.current_joints()[:]
        for _ in range(15):
            self.w._tick(.05, time.monotonic())
            await asyncio.sleep(.002)
        self.assertEqual(q, self.w.current_joints())
        self.assertEqual(self.play.held['id'], oid)
        self.assertTrue((await self.board.step('ttt', self.board.group))['paused'])
        self.assertTrue((await self.board.resume('ttt', self.board.group))['ok'])
        await self.pump(lambda: self.w.runner.state == 'finished')
        self.assertTrue((await self.board.step('ttt', self.board.group))['done'])
        self.assertEqual(next(p for p in self.w.objects.list() if p['id'] == oid)['task']['slot'], 4)

    async def test_actual_first_move_in_every_game_has_real_pick_and_release(self):
        moves = {'ttt': {'to': [1, 1]}, 'gomoku': {'to': [7, 7]}, 'go': {'to': [7, 7]},
                 'chess': {'from': [6, 4], 'to': [4, 4]}, 'xiangqi': {'from': [9, 1], 'to': [7, 2]}}
        for game, move in moves.items():
            with self.subTest(game=game):
                await self.begin(game)
                self.games.move(game, move, auto_reply=False)
                self.board.note(game)
                action = await self.board.step(game, self.board.group)
                self.assertTrue(action['running'], action)
                await self.pump(lambda: self.play.held is not None)
                oid = self.play.held['id']
                self.assertEqual(self.w.settings['sim_speed_pct'], 100)
                await self.pump(lambda: self.w.runner.state == 'finished')
                done = await self.board.step(game, self.board.group)
                self.assertTrue(done['done'], done)
                placed = next(p for p in self.w.objects.list() if p['id'] == oid)
                self.assertEqual(placed['task']['slot'], move['to'][0] * LAYOUTS[game]['cols'] + move['to'][1])
                self.assertLess(math.dist(placed['xyz'][:2], cell_center(game, *move['to'])), .5)
                self.assertIsNone(self.play.held)

    async def test_chess_capture_recycles_opponent_before_moving_attacker(self):
        await self.begin('chess')
        captured_id = next(p['id'] for p in self.board._members() if p['task']['slot'] == 11)
        for move in ({'from': [6, 4], 'to': [4, 4]}, {'from': [1, 3], 'to': [3, 3]},
                     {'from': [4, 4], 'to': [3, 3]}):
            self.games.move('chess', move, auto_reply=False)
            self.board.note('chess')
            if move['from'] == [4, 4]:
                self.assertEqual([action['kind'] for action in self.board.queue], ['capture', 'move'])
            while self.board.queue or self.board.current:
                result = await self.board.step('chess', self.board.group)
                if result.get('running'):
                    await self.pump(lambda: self.w.runner.state == 'finished')
        captured = next(p for p in self.w.objects.list() if p['id'] == captured_id)
        self.assertEqual(captured['task']['slot'], -2)
        self.assertLess(math.dist(captured['xyz'][:2], tray_center('chess', captured['task']['home'])), .5)
        occupying = [p for p in self.board._members() if p['task']['slot'] == 27]
        self.assertEqual(len(occupying), 1)
        self.assertEqual(occupying[0]['task']['code'], 'P')
        self.assertIsNone(self.play.held)

    async def test_full_tic_tac_toe_game_places_all_nine_pieces_without_inactive_cup_collision(self):
        await self.begin('ttt')
        # A legal drawn game fills every row and column, including the user's
        # reported lower-left failure after several pieces are already placed.
        for target in ([0, 0], [1, 1], [0, 2], [0, 1], [2, 1], [1, 2], [1, 0], [2, 0], [2, 2]):
            self.games.move('ttt', {'to': target}, auto_reply=False)
            self.board.note('ttt')
            result = await self.board.step('ttt', self.board.group)
            self.assertTrue(result['running'], result)
            await self.pump(lambda: self.w.runner.state == 'finished')
            done = await self.board.step('ttt', self.board.group)
            self.assertTrue(done['done'], done)
            actual = self.board.actual_state()['cells']
            self.assertEqual(len(actual), len(self.games._games['ttt'].history))
        self.assertTrue(self.games.snapshot('ttt')['over'])
        self.assertEqual(len(self.board.actual_state()['cells']), 9)
        self.assertFalse(self.board.active)

    async def test_generation_failure_preserves_actual_cells_and_blocks_replay(self):
        self.games.new('ttt', {'opponent': 'human'})
        self.board.ensure('ttt', force=True)
        self.games.move('ttt', {'to': [1, 1]}, auto_reply=False)
        self.board.note('ttt')

        async def failure(*_args, **_kwargs):
            raise RuntimeError('真实几何碰撞')

        with patch('backend.application.board_tools.probe_vacuum_yaw', return_value={'ok': True}), \
                patch.object(self.play, 'generate', failure):
            with self.assertRaises(BoardRobotError):
                await self.board.step('ttt', self.board.group)
        state = self.board.status('ttt')
        self.assertFalse(state['active'])
        self.assertEqual(state['lifecycle'], 'failed')
        self.assertTrue(state['physical_pending'])
        self.assertEqual(state['actual_state']['cells'], [])
        self.assertEqual(state['confirmed_state']['cells'], [])
        self.assertFalse(state['actual_state']['over'])
        with self.assertRaises(BoardRobotError):
            await self.board.step('ttt', self.board.group)

    async def test_go_and_gomoku_board_edges_and_both_supply_rows_execute(self):
        edges = ([0, 0], [14, 14], [0, 14], [14, 0], [7, 0], [7, 14], [0, 7], [14, 7])
        for game in ('go', 'gomoku'):
            with self.subTest(game=game):
                await self.begin(game)
                for target in edges:
                    self.games.move(game, {'to': target}, auto_reply=False)
                    self.board.note(game)
                    action = await self.board.step(game, self.board.group)
                    self.assertTrue(action['running'])
                    await self.pump(lambda: self.w.runner.state == 'finished')
                    self.assertTrue((await self.board.step(game, self.board.group))['done'])
                self.assertEqual(len(self.board.actual_state()['cells']), len(edges))

    async def test_both_chess_games_move_from_rear_corner_after_first_turn(self):
        moves = {'chess': ({'from': [6, 4], 'to': [4, 4]}, {'from': [0, 1], 'to': [2, 2]}),
                 'xiangqi': ({'from': [9, 1], 'to': [7, 2]}, {'from': [0, 0], 'to': [1, 0]})}
        for game, sequence in moves.items():
            with self.subTest(game=game):
                await self.begin(game)
                for move in sequence:
                    self.games.move(game, move, auto_reply=False)
                    self.board.note(game)
                    action = await self.board.step(game, self.board.group)
                    self.assertTrue(action['running'])
                    await self.pump(lambda: self.w.runner.state == 'finished')
                    self.assertTrue((await self.board.step(game, self.board.group))['done'])
                    piece = next(p for p in self.board._members() if p['task']['slot'] == move['to'][0] * LAYOUTS[game]['cols'] + move['to'][1])
                    self.assertLess(math.dist(piece['xyz'][:2], cell_center(game, *move['to'])), .5)

    async def test_castling_queues_both_real_pieces(self):
        self.games.new('chess', {'opponent': 'human'})
        engine = self.games._games['chess']
        engine.board[7][5] = ''
        engine.board[7][6] = ''
        self.board.ensure('chess', force=True)
        self.games.move('chess', {'from': [7, 4], 'to': [7, 6]}, auto_reply=False)
        self.board.note('chess')
        self.assertEqual([a['kind'] for a in self.board.queue], ['move', 'rook'])
        self.assertEqual([a['items'][0]['slot'] for a in self.board.queue], [62, 61])


if __name__ == '__main__':
    unittest.main()
