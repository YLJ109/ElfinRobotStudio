"""棋类 2D 规则用例：只测纯内存规则，不启动服务、不读写现场数据。

与 `check_board_games.py` 分开是为了让用例可以被离线检查入口导入，
同时也能在本机直接 `python tools/_board_game_cases.py` 快速回归。
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.application.board_games import (  # noqa: E402
    BLACK, WHITE, BoardGameError, Chess, GameStore, Go, Gomoku, LEVELS, TicTacToe, Xiangqi,
)
from backend.game_routes import create_game_router  # noqa: E402


class TicTacToeCases(unittest.TestCase):
    def test_human_turn_and_rejections(self):
        game = TicTacToe('human')
        state = game.snapshot()
        self.assertEqual(state['turn'], 'O')
        self.assertEqual(state['cells'], [])
        game.play({'to': [1, 1]})
        self.assertEqual(game.board[4], 'O')
        with self.assertRaises(BoardGameError):
            game.play({'to': [1, 1]})
        with self.assertRaises(BoardGameError):
            game.play({'to': [3, 0]})
        with self.assertRaises(BoardGameError):
            game.play({'to': 'bad'})

    def test_three_in_a_row_wins(self):
        game = TicTacToe('human')
        for target in ([0, 0], [1, 0], [0, 1], [1, 1], [0, 2]):
            game.play({'to': target})
        state = game.snapshot()
        self.assertEqual(state['winner'], 'human')
        self.assertTrue(state['over'])
        self.assertEqual(state['status'], 'O 获胜（双人本地对局）')
        with self.assertRaises(BoardGameError):
            game.play({'to': [2, 2]})

    def test_undo_removes_robot_reply(self):
        game = TicTacToe('robot')
        game.play({'to': [1, 1]})
        self.assertEqual(len(game.history), 2)
        state = game.undo()
        self.assertEqual(game.board, [''] * 9)
        self.assertEqual(state['turn'], 'O')
        self.assertFalse(state['can_undo'])

    def test_suggest_returns_legal_slot(self):
        game = TicTacToe('robot')
        suggestion = game.suggest()
        self.assertIsNotNone(suggestion)
        r, c = suggestion['to']
        self.assertEqual(game.board[r * 3 + c], '')


class GomokuCases(unittest.TestCase):
    def test_five_in_a_row(self):
        game = Gomoku('human')
        for target in ([7, 0], [5, 0], [7, 1], [5, 1], [7, 2], [5, 2], [7, 3], [5, 3], [7, 4]):
            game.play({'to': target})
        state = game.snapshot()
        self.assertEqual(state['winner'], 'human')
        self.assertTrue(state['over'])
        self.assertEqual(len([cell for cell in state['cells'] if cell['piece']['side'] == 'human']), 5)

    def test_occupied_point_rejected(self):
        game = Gomoku('human')
        game.play({'to': [7, 7]})
        with self.assertRaises(BoardGameError):
            game.play({'to': [7, 7]})
        with self.assertRaises(BoardGameError):
            game.play({'to': [99, 0]})

    def test_robot_replies_once_per_move(self):
        game = Gomoku('robot')
        game.play({'to': [7, 7]})
        enemy = [(cell['r'], cell['c']) for cell in game.snapshot()['cells'] if cell['piece']['side'] == 'robot']
        self.assertEqual(len(enemy), 1)


class GoCases(unittest.TestCase):
    def test_single_capture_sets_ko_point(self):
        game = Go('human')
        for target in ([5, 5], [1, 1], [0, 1], [5, 6], [2, 1], [5, 7], [1, 0], [5, 8], [1, 2]):
            game.play({'to': target})
        self.assertEqual(game.captured[BLACK], 1)
        self.assertEqual(game.board[1][1], 0)
        self.assertEqual(game.ko, (1, 1))
        with self.assertRaises(BoardGameError) as error:
            game.play({'to': [1, 1]})
        self.assertIn('劫争', str(error.exception))

    def test_suicide_rejected(self):
        game = Go('human')
        for r, c in ((2, 1), (1, 2), (3, 2), (2, 3)):
            game.board[r][c] = BLACK
        game.turn = WHITE
        with self.assertRaises(BoardGameError) as error:
            game.play({'to': [2, 2]})
        self.assertIn('自杀', str(error.exception))

    def test_two_passes_finish_with_area_score(self):
        game = Go('human')
        game.play({'pass': True})
        state = game.play({'pass': True})
        self.assertTrue(state['over'])
        self.assertEqual(state['result'] if 'result' in state else game.result, 'score')
        self.assertIn('komi', game.score)

    def test_undo_restores_board_and_turn(self):
        game = Go('human')
        game.play({'to': [4, 4]})
        game.play({'to': [3, 3]})
        game.undo()
        self.assertEqual(game.board[3][3], 0)
        self.assertEqual(game.turn, WHITE)
        self.assertEqual(game.board[4][4], BLACK)


class ChessCases(unittest.TestCase):
    def test_initial_legal_moves(self):
        game = Chess('human')
        self.assertEqual(len(game.legal_moves()), 20)
        self.assertEqual(game.board[0], list('rnbqkbnr'))
        self.assertEqual(game.board[6], list('PPPPPPPP'))

    def test_en_passant(self):
        game = Chess('human')
        for move in ({'from': [6, 4], 'to': [4, 4]}, {'from': [1, 0], 'to': [2, 0]},
                     {'from': [4, 4], 'to': [3, 4]}, {'from': [1, 3], 'to': [3, 3]},
                     {'from': [3, 4], 'to': [2, 3]}):
            game.play(move)
        self.assertEqual(game.board[3][3], '')
        self.assertEqual(game.board[2][3], 'P')

    def test_castling_moves_rook(self):
        game = Chess('human')
        for move in ({'from': [6, 4], 'to': [4, 4]}, {'from': [1, 4], 'to': [3, 4]},
                     {'from': [7, 6], 'to': [5, 5]}, {'from': [0, 1], 'to': [2, 2]},
                     {'from': [7, 5], 'to': [6, 4]}, {'from': [0, 6], 'to': [2, 5]},
                     {'from': [7, 4], 'to': [7, 6]}):
            game.play(move)
        self.assertEqual(game.board[7][6], 'K')
        self.assertEqual(game.board[7][5], 'R')
        self.assertEqual(game.board[7][7], '')

    def test_promotion_to_queen(self):
        game = Chess('human')
        game.board = [[''] * 8 for _ in range(8)]
        game.board[7][4] = 'K'
        game.board[0][7] = 'k'
        game.board[1][0] = 'P'
        game.castling = {'K': False, 'Q': False, 'k': False, 'q': False}
        game.turn = 'w'
        game.play({'from': [1, 0], 'to': [0, 0], 'promotion': 'Q'})
        self.assertEqual(game.board[0][0], 'Q')

    def test_fools_mate_is_checkmate(self):
        game = Chess('human')
        for move in ({'from': [6, 5], 'to': [5, 5]}, {'from': [1, 4], 'to': [3, 4]},
                     {'from': [6, 6], 'to': [4, 6]}, {'from': [0, 3], 'to': [4, 7]}):
            game.play(move)
        state = game.snapshot()
        self.assertTrue(state['over'])
        self.assertIn('黑方获胜', state['status'])
        self.assertEqual(len(state['moves']), 0)

    def test_move_that_ignores_check_is_rejected(self):
        game = Chess('human')
        game.board = [[''] * 8 for _ in range(8)]
        game.board[7][4] = 'K'
        game.board[0][0] = 'k'
        game.board[0][4] = 'r'
        game.board[6][0] = 'P'
        game.castling = {'K': False, 'Q': False, 'k': False, 'q': False}
        game.turn = 'w'
        with self.assertRaises(BoardGameError) as error:
            game.play({'from': [6, 0], 'to': [5, 0]})
        self.assertIn('将军', str(error.exception))

    def test_undo_after_robot_reply(self):
        game = Chess('robot')
        game.play({'from': [6, 4], 'to': [4, 4]})
        self.assertEqual(game.turn, 'w')
        game.undo()
        self.assertEqual(game.board[6][4], 'P')
        self.assertEqual(game.board[4][4], '')
        self.assertEqual(len(game.history), 0)

    def test_split_move_and_reply_keep_applied_records(self):
        store = GameStore()
        state = store.move('chess', {'from': [6, 4], 'to': [4, 4]}, auto_reply=False)
        self.assertEqual(state['turn'], 'b')
        self.assertEqual(len(store.last_applied), 1)
        self.assertEqual(store.last_applied[0]['from'], [6, 4])
        reply = store.reply('chess')
        self.assertEqual(reply['turn'], 'w')
        self.assertEqual(len(store.last_applied), 1)
        self.assertEqual(store.last_applied[0]['side'], 'robot')

    def test_capture_record_uses_captured_square(self):
        game = Chess('human')
        game.board = [[''] * 8 for _ in range(8)]
        game.board[4][4] = 'P'
        game.board[3][3] = 'p'
        game.board[7][4] = 'K'
        game.board[0][4] = 'k'
        game.castling = {'K': False, 'Q': False, 'k': False, 'q': False}
        game.turn = 'w'
        game.play({'from': [4, 4], 'to': [3, 3]})
        record = game.applied[0]
        self.assertIsNotNone(record['captured'])
        self.assertEqual(record['captured']['code'], 'p')
        self.assertEqual(record['captured']['to'], [3, 3])


class XiangqiCases(unittest.TestCase):
    def test_initial_legal_moves(self):
        game = Xiangqi('human')
        self.assertEqual(len(game.legal_moves()), 44)
        self.assertEqual(game.board[9], list('RHEAKAEHR'))
        self.assertEqual(game.board[0], list('rheakaehr'))

    def test_horse_leg_blocks_move(self):
        game = Xiangqi('human')
        moves = game.legal_moves('r')
        self.assertTrue([m for m in moves if m['from'] == [9, 1] and m['to'] == [7, 0]])
        game.board[8][1] = 'P'
        moves = game.legal_moves('r')
        self.assertFalse([m for m in moves if m['from'] == [9, 1] and m['to'] in ([7, 0], [7, 2])])

    def test_flying_general_rejects_exposing_kings(self):
        game = Xiangqi('human')
        game.board = [[''] * 9 for _ in range(10)]
        game.board[9][3] = 'K'
        game.board[0][3] = 'k'
        game.board[5][3] = 'R'
        game.turn = 'r'
        with self.assertRaises(BoardGameError) as error:
            game.play({'from': [5, 3], 'to': [5, 4]})
        self.assertIn('将军', str(error.exception))

    def test_cannon_needs_exactly_one_screen(self):
        game = Xiangqi('human')
        game.board = [[''] * 9 for _ in range(10)]
        game.board[9][3] = 'K'
        game.board[0][3] = 'k'
        game.board[5][3] = 'P'
        game.board[7][1] = 'C'
        game.board[4][1] = 'P'
        game.board[2][1] = 'p'
        game.turn = 'r'
        game.play({'from': [7, 1], 'to': [2, 1]})
        self.assertEqual(game.board[2][1], 'C')
        self.assertEqual(game.board[4][1], 'P')

    def test_king_in_check_must_be_answered(self):
        game = Xiangqi('human')
        game.board = [[''] * 9 for _ in range(10)]
        game.board[9][3] = 'K'
        game.board[0][3] = 'k'
        game.board[0][5] = 'r'
        game.board[7][1] = 'C'
        game.board[4][1] = 'P'
        game.board[2][1] = 'p'
        game.turn = 'r'
        with self.assertRaises(BoardGameError) as error:
            game.play({'from': [7, 1], 'to': [2, 1]})
        self.assertIn('将军', str(error.exception))

    def test_undo_restores_position(self):
        game = Xiangqi('human')
        game.play({'from': [9, 1], 'to': [7, 2]})
        game.undo()
        self.assertEqual(game.board[9][1], 'H')
        self.assertEqual(game.board[7][2], '')
        self.assertEqual(game.turn, 'r')


class GameRouteCases(unittest.TestCase):
    def setUp(self):
        self.store = GameStore()
        app = FastAPI()
        app.include_router(create_game_router(self.store))
        self.client = TestClient(app)

    def test_state_new_move_undo_suggest(self):
        self.assertEqual(self.client.get('/api/games').json()['ok'], True)
        state = self.client.get('/api/games/ttt/state').json()
        self.assertTrue(state['ok'])
        self.assertEqual(state['state']['turn'], 'O')
        reset = self.client.post('/api/games/ttt/new', json={'opponent': 'human'}).json()
        self.assertTrue(reset['ok'])
        self.assertEqual(reset['state']['opponent'], 'human')
        moved = self.client.post('/api/games/ttt/move', json={'to': [1, 1]}).json()
        self.assertTrue(moved['ok'])
        suggested = self.client.post('/api/games/ttt/suggest').json()
        self.assertTrue(suggested['ok'])
        self.assertIsNotNone(suggested['state']['suggest'])
        undone = self.client.post('/api/games/ttt/undo').json()
        self.assertTrue(undone['ok'])

    def test_domain_rejection_is_200_with_reason(self):
        self.client.post('/api/games/ttt/move', json={'to': [1, 1]})
        result = self.client.post('/api/games/ttt/move', json={'to': [1, 1]}).json()
        self.assertFalse(result['ok'])
        self.assertIn('棋子', result['why'])
        self.assertEqual(self.client.get('/api/games/nope/state').status_code, 404)

    def test_new_rejects_unknown_fields(self):
        result = self.client.post('/api/games/ttt/new', json={'level': 'hard', 'level_extra': 3}).json()
        self.assertFalse(result['ok'])
        self.assertIn('未知字段', result['why'])
        hard = self.client.post('/api/games/ttt/new', json={'level': 'hard'}).json()
        self.assertTrue(hard['ok'])
        self.assertEqual(hard['state']['level_label'], '困难')

    def test_each_game_has_own_store_state(self):
        for game in ('gomoku', 'go', 'chess', 'xiangqi'):
            state = self.client.get(f'/api/games/{game}/state').json()
            self.assertTrue(state['ok'], game)
            self.assertEqual(state['state']['game'], game)


class LevelCases(unittest.TestCase):
    def test_every_game_exposes_five_levels(self):
        self.assertEqual(list(LEVELS), ['beginner', 'easy', 'normal', 'hard', 'impossible'])
        for cls in (TicTacToe, Gomoku, Go, Chess, Xiangqi):
            for level, label in LEVELS.items():
                state = cls('robot', level).snapshot()
                self.assertEqual(state['level'], level)
                self.assertEqual(state['level_label'], label)

    def test_invalid_level_is_rejected(self):
        store = GameStore()
        with self.assertRaises(BoardGameError):
            store.new('ttt', {'level': 'god'})

    def test_impossible_tic_tac_toe_blocks_center_opening(self):
        game = TicTacToe('robot', 'impossible')
        game.play({'to': [1, 1]})
        self.assertTrue(any(game.board[slot] == 'X' for slot in (0, 2, 6, 8)))

    def test_new_game_accepts_level_and_keeps_robot_opponent(self):
        store = GameStore()
        state = store.new('chess', {'level': 'hard'})
        self.assertEqual(state['level'], 'hard')
        self.assertEqual(state['opponent'], 'robot')


class FastReplyCases(unittest.TestCase):
    def test_normal_chess_and_xiangqi_use_immediate_legal_depth_one(self):
        from unittest.mock import patch
        for cls in (Chess,Xiangqi):
            game=cls('robot','normal')
            legal=game.legal_moves(game.turn)
            with patch.object(game,'best_move',return_value=legal[0]) as search,patch('random.random',return_value=1.):
                self.assertEqual(game._robot_choice(),legal[0])
            self.assertEqual(search.call_args.kwargs['depth'],1)

    def test_node_limit_restores_all_rule_state_and_returns_a_legal_move(self):
        import copy
        for cls in (Chess,Xiangqi):
            game=cls('robot','impossible')
            before=copy.deepcopy(game.__dict__)
            legal=game.legal_moves(game.turn)
            move=game.best_move(depth=3,node_budget=3,budget_s=1.)
            self.assertIn(move,legal)
            self.assertEqual(game.__dict__,before,'timeout must restore board, turn, castling and history')

    def test_unfinished_deeper_iteration_keeps_last_complete_result(self):
        for cls in (Chess,Xiangqi):
            game=cls('robot','impossible')
            depth_one=game.best_move(depth=1,node_budget=10000,budget_s=1.)
            count=len(game.legal_moves(game.turn))
            interrupted=game.best_move(depth=3,node_budget=2*count+2,budget_s=1.)
            self.assertEqual(interrupted,depth_one)

    def test_clock_deadline_and_node_cap_are_independent(self):
        from unittest.mock import patch
        from backend.application.board_games import _SearchBudget,_SearchExpired
        with patch('backend.application.board_games.time.monotonic',side_effect=[10.,10.1]):
            budget=_SearchBudget(.05,1000)
            with self.assertRaises(_SearchExpired):budget.visit()
        with patch('backend.application.board_games.time.monotonic',return_value=10.):
            budget=_SearchBudget(1.,1);budget.visit()
            with self.assertRaises(_SearchExpired):budget.visit()

    def test_robot_turn_has_direct_move_status_without_fake_thinking(self):
        samples=((TicTacToe,{'to':[1,1]}),(Gomoku,{'to':[7,7]}),(Go,{'to':[7,7]}),
                 (Chess,{'from':[6,4],'to':[4,4]}),(Xiangqi,{'from':[9,1],'to':[7,2]}))
        for cls,move in samples:
            game=cls('robot','normal');game.play(move,auto_reply=False)
            self.assertIn('电脑走棋',game.snapshot()['status'])
            self.assertEqual(len(game.history),1)


class GameAssetCases(unittest.TestCase):
    def test_chess_piece_model_is_self_contained_glb(self):
        path = ROOT / 'web' / 'public' / 'models' / 'games' / 'chess_pieces.glb'
        self.assertTrue(path.exists(), '缺少国际象棋棋子模型')
        data = path.read_bytes()
        self.assertEqual(data[:4], b'glTF')
        self.assertLess(len(data), 8 * 1024 * 1024)
        # 只保留棋子：不含贴图与小场景，避免把整套 42MB 资源带进桌面包。
        self.assertNotIn(b'images', data[:65536])
        self.assertIn(b'Pawn_Body_W1', data[:131072])


if __name__ == '__main__':
    unittest.main()
