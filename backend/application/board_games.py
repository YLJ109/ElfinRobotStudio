"""2D 棋类规则引擎：井字棋、五子棋、围棋、国际象棋、中国象棋。

本模块只维护内存中的棋局状态，不读写场景、不调用机器人运动、不碰相机或 IO。
所有对外状态都从这里生成，前端只渲染、不在前端复制第二套规则。

统一约定：
* `rows` 行从 0 开始，0 是棋盘最上方；`cols` 列从 0 开始，0 是最左侧。
* `move` 的坐标一律写作 `[row, col]`；落子类玩法 `from` 为 `None`。
* 成功响应 `{ok: True, state: {...}}`；规则拒绝 `{ok: False, why: '...'}`。
"""
from __future__ import annotations

import copy
import random
import time
from typing import Any

BLACK = 1
WHITE = 2

LEVELS = {
    'beginner': '入门',
    'easy': '简单',
    'normal': '一般',
    'hard': '困难',
    'impossible': '不可战胜',
}
DEFAULT_LEVEL = 'normal'

# ---------------------------------------------------------------------------- 公共小工具


def _coord(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if type(value[0]) is not int or type(value[1]) is not int:
        return None
    return int(value[0]), int(value[1])


class BoardGameError(ValueError):
    """规则拒绝：调用方应当原样展示 `str(error)`。"""


class _SearchExpired(Exception):
    pass


class _SearchBudget:
    def __init__(self,seconds,nodes):
        self.deadline=time.monotonic()+seconds
        self.remaining=nodes

    def visit(self):
        if self.remaining<=0 or time.monotonic()>=self.deadline:
            raise _SearchExpired
        self.remaining-=1


def _bounded_best_move(game,side,depth,budget_s=None,node_budget=None):
    """Complete shallow search first; deeper unfinished work never changes it."""
    depth=max(1,min(3,int(depth)))
    budget=_SearchBudget(budget_s if budget_s is not None else {1:.03,2:.08,3:.14}[depth],
                         node_budget if node_budget is not None else {1:512,2:1500,3:3000}[depth])
    moves=game._ordered(game.legal_moves(side))
    if not moves:return None
    # Ordering prefers legal captures/promotions; even an exhausted budget
    # returns a legal tactical candidate, never an unchecked coordinate.
    best=moves[0]
    other='b' if side in ('w','r') else game.own_side
    for current_depth in range(1,depth+1):
        candidate=None;value_best=-float('inf')
        try:
            for move in moves:
                budget.visit()
                saved=game._save_light()
                try:
                    game._apply(move,side,record=False)
                    value=-game._search(other,current_depth-1,-99999,99999,budget)
                finally:game._restore_light(saved)
                if value>value_best:candidate,value_best=move,value
        except _SearchExpired:
            break
        if candidate is not None:
            best=candidate
            moves=[best]+[move for move in moves if move is not best]
    return best


# ---------------------------------------------------------------------------- 井字棋


class TicTacToe:
    key = 'ttt'
    title = '井字棋'
    rows = cols = 3
    layout = 'cells'
    drop = True
    own_side = 'O'
    other_side = 'X'
    lines = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))

    def __init__(self, opponent='robot', level=DEFAULT_LEVEL):
        self.opponent = 'human' if opponent == 'human' else 'robot'
        self.level = level if level in LEVELS else DEFAULT_LEVEL
        self.reset()

    def reset(self):
        self.board = [''] * 9
        self.turn = self.own_side
        self.history: list[dict] = []
        self.last_move = None
        self.result = None
        self.applied: list[dict] = []

    # -- 规则 ---------------------------------------------------------------
    def outcome(self, board=None):
        board = self.board if board is None else board
        for a, b, c in self.lines:
            if board[a] and board[a] == board[b] == board[c]:
                return board[a]
        return 'draw' if all(board) else None

    def _minimax(self, board, side, depth, cache=None):
        if cache is None:cache={}
        key=(tuple(board),side,depth)
        if key in cache:return cache[key]
        winner = self.outcome(board)
        if winner == self.own_side:
            return 10 - depth
        if winner == self.other_side:
            return depth - 10
        if winner == 'draw':
            return 0
        other = self.other_side if side == self.own_side else self.own_side
        values = []
        for slot in range(9):
            if board[slot]:
                continue
            board[slot] = side
            values.append(self._minimax(board, other, depth + 1,cache))
            board[slot] = ''
        if not values:
            return 0
        value=max(values) if side == self.own_side else min(values)
        cache[key]=value
        return value

    def best_slot(self, side=None):
        side = side or self.turn
        board = self.board[:]
        best, best_value = None, None
        cache={}
        for slot in range(9):
            if board[slot]:
                continue
            board[slot] = side
            value = self._minimax(board, self.other_side if side == self.own_side else self.own_side, 1,cache)
            board[slot] = ''
            better = value > best_value if best_value is not None and side == self.own_side \
                else value < best_value if best_value is not None else True
            if better:
                best, best_value = slot, value
        return best

    def legal_moves(self):
        return [{'from': None, 'to': [slot // 3, slot % 3]} for slot in range(9) if not self.board[slot]]

    def suggest(self):
        if self.result:
            return None
        slot = self.best_slot()
        if slot is None:
            return None
        reason = '先手占中' if slot == 4 else '堵住对方的两连' if self._opponent_threat() else '稳住局面'
        return {'from': None, 'to': [slot // 3, slot % 3], 'reason': reason}

    def _opponent_threat(self):
        other = self.other_side if self.turn == self.own_side else self.own_side
        board = self.board[:]
        for slot in range(9):
            if board[slot]:
                continue
            board[slot] = other
            if self.outcome(board) == other:
                return True
            board[slot] = ''
        return False

    def _place(self, slot, side, record=True):
        before = {'board': self.board[:], 'turn': self.turn, 'result': self.result}
        self.board[slot] = side
        self.last_move = {'from': None, 'to': [slot // 3, slot % 3]}
        if record:
            self.history.append({'side': side, 'move': {'from': None, 'to': [slot // 3, slot % 3]},
                                 'text': f"{side} → 第{slot // 3 + 1}行第{slot % 3 + 1}列", 'before': before})
            self.applied.append({'from': None, 'to': [slot // 3, slot % 3], 'code': side,
                                 'side': 'human' if side == self.own_side else 'robot'})
        self.result = self.outcome()
        self.turn = self.other_side if side == self.own_side else self.own_side

    def play(self, move, auto_reply=True):
        self.applied = []
        r, c = self._target(move)
        slot = r * 3 + c
        if self.result:
            raise BoardGameError('棋局已结束，请重新开始')
        if self.board[slot]:
            raise BoardGameError('这一格已经有棋子了')
        if self._turn_owner() == 'robot' and self.opponent == 'robot':
            raise BoardGameError('现在轮到电脑落子')
        self._place(slot, self.turn)
        if auto_reply and self.opponent == 'robot' and not self.result:
            self.robot_reply(reset=False)
        return self.snapshot()

    def robot_reply(self, reset=True):
        """只走电脑这一手；实体执行按手拆开时由外部显式调用。"""
        if reset:
            self.applied = []
        if self.opponent != 'robot' or self.result or self.turn != self.other_side:
            return self.snapshot()
        reply = self._robot_slot()
        if reply is not None:
            self._place(reply, self.turn)
        return self.snapshot()

    def _robot_slot(self):
        empty = [slot for slot in range(9) if not self.board[slot]]
        if not empty:
            return None
        if self.level == 'impossible':
            return self.best_slot(self.turn)
        chances = {'beginner': 0, 'easy': .3, 'normal': .7, 'hard': .92}
        if random.random() < chances.get(self.level, .7):
            return self.best_slot(self.turn)
        return random.choice(empty)

    def undo(self):
        steps = 2 if self.opponent == 'robot' else 1
        removed = 0
        while self.history and removed < steps:
            entry = self.history.pop()
            before = entry['before']
            self.board = before['board']
            self.turn = before['turn']
            self.result = before['result']
            self.last_move = {k: v for k, v in self.history[-1]['move'].items()} if self.history else None
            removed += 1
        if not removed:
            raise BoardGameError('还没有可撤销的落子')
        return self.snapshot()

    # -- 状态 ---------------------------------------------------------------
    def _target(self, move):
        if not isinstance(move, dict):
            raise BoardGameError('落子参数无效')
        target = _coord(move.get('to'))
        if target is None:
            raise BoardGameError('落子位置无效')
        r, c = target
        if not (0 <= r < 3 and 0 <= c < 3):
            raise BoardGameError('落子位置超出棋盘')
        return r, c

    def _turn_owner(self):
        return 'human' if self.turn == self.own_side else 'robot'

    def _status_text(self):
        if self.opponent == 'human':
            return f'{self.turn} 方走棋（双人本地对局）'
        return '轮到你了（O）' if self.turn == self.own_side else '电脑走棋（X）'

    def snapshot(self):
        cells = []
        for slot, value in enumerate(self.board):
            if value:
                cells.append({'r': slot // 3, 'c': slot % 3,
                              'piece': {'code': value, 'glyph': value, 'side': 'human' if value == self.own_side else 'robot'}})
        winner = self.result
        if winner == 'draw':
            status, winner_side = '平局', None
        elif winner:
            status = (f'{winner} 获胜（双人本地对局）' if self.opponent == 'human'
                      else '你赢了' if winner == self.own_side else '电脑获胜')
            winner_side = 'human' if winner == self.own_side else 'robot'
        else:
            status = self._status_text()
            winner_side = None
        return {
            'game': self.key, 'title': self.title, 'rows': 3, 'cols': 3, 'layout': 'cells',
            'cells': cells, 'drop': True, 'moves': [],
            'turn': self.turn, 'turn_owner': self._turn_owner(),
            'turn_label': status,
            'over': bool(self.result), 'status': status, 'winner': winner_side,
            'last_move': self.last_move,
            'legal': self.legal_moves() if not self.result else [],
            'illegal': [], 'suggest': None,
            'history': [entry['text'] for entry in self.history][-8:],
            'can_undo': bool(self.history),
            'opponent': self.opponent,
            'level': self.level, 'level_label': LEVELS[self.level],
            'sides': {'human': 'O', 'robot': 'X'},
            'note': '',
        }


# ---------------------------------------------------------------------------- 五子棋

GOMOKU_SIZE = 15
DIRECTIONS = ((1, 0), (0, 1), (1, 1), (1, -1))


class Gomoku:
    key = 'gomoku'
    title = '五子棋'
    rows = cols = GOMOKU_SIZE
    layout = 'points'
    drop = True
    own_side = BLACK
    other_side = WHITE

    def __init__(self, opponent='robot', level=DEFAULT_LEVEL):
        self.opponent = 'human' if opponent == 'human' else 'robot'
        self.level = level if level in LEVELS else DEFAULT_LEVEL
        self.reset()

    def reset(self):
        self.board = [[0] * GOMOKU_SIZE for _ in range(GOMOKU_SIZE)]
        self.turn = self.own_side
        self.history: list[dict] = []
        self.last_move = None
        self.result = None
        self.winner = None
        self.applied: list[dict] = []

    def _run(self, board, r, c, dr, dc, color):
        count = 1
        for sign in (1, -1):
            rr, cc = r + dr * sign, c + dc * sign
            while 0 <= rr < GOMOKU_SIZE and 0 <= cc < GOMOKU_SIZE and board[rr][cc] == color:
                count += 1
                rr, cc = rr + dr * sign, cc + dc * sign
        return count

    def _wins(self, board, r, c, color):
        return any(self._run(board, r, c, dr, dc, color) >= 5 for dr, dc in DIRECTIONS)

    def _pattern(self, board, r, c, color, dr, dc):
        count, open_ends = 1, 0
        for sign in (1, -1):
            rr, cc = r + dr * sign, c + dc * sign
            while 0 <= rr < GOMOKU_SIZE and 0 <= cc < GOMOKU_SIZE and board[rr][cc] == color:
                count += 1
                rr, cc = rr + dr * sign, cc + dc * sign
            if 0 <= rr < GOMOKU_SIZE and 0 <= cc < GOMOKU_SIZE and board[rr][cc] == 0:
                open_ends += 1
        return count, open_ends

    def _evaluate(self, board, r, c, color):
        score = 0
        for dr, dc in DIRECTIONS:
            count, opens = self._pattern(board, r, c, color, dr, dc)
            if count >= 5:
                score += 100000
            elif count == 4:
                score += 12000 if opens == 2 else 2500 if opens == 1 else 0
            elif count == 3:
                score += 1500 if opens == 2 else 350 if opens == 1 else 0
            elif count == 2:
                score += 120 if opens == 2 else 30
            elif opens == 2:
                score += 8
        center = GOMOKU_SIZE // 2
        score += max(0, 8 - (abs(r - center) + abs(c - center))
                     ) * 2
        return score

    def best_point(self, color):
        board = self.board
        best, best_score = None, -1
        other = WHITE if color == BLACK else BLACK
        for r in range(GOMOKU_SIZE):
            for c in range(GOMOKU_SIZE):
                if board[r][c]:
                    continue
                board[r][c] = color
                win = self._wins(board, r, c, color)
                board[r][c] = 0
                if win:
                    return (r, c)
                board[r][c] = other
                block = self._wins(board, r, c, other)
                board[r][c] = 0
                if block:
                    return (r, c)
                own = self._evaluate(board, r, c, color)
                threat = self._evaluate(board, r, c, other)
                score = own * 2 + threat
                if score > best_score:
                    best, best_score = (r, c), score
        return best

    def legal_moves(self):
        return [{'from': None, 'to': [r, c]} for r in range(GOMOKU_SIZE) for c in range(GOMOKU_SIZE)
                if not self.board[r][c]] if not self.result else []

    def suggest(self):
        if self.result:
            return None
        point = self.best_point(self.turn)
        if point is None:
            return None
        r, c = point
        board = self.board
        board[r][c] = self.turn
        win = self._wins(board, r, c, self.turn)
        board[r][c] = WHITE if self.turn == BLACK else BLACK
        block = self._wins(board, r, c, WHITE if self.turn == BLACK else BLACK)
        board[r][c] = 0
        reason = '这里可以直接连成五子' if win else '这里能封住对方的四连' if block else '扩大自己的连子并保持中间控制'
        return {'from': None, 'to': [r, c], 'reason': reason}

    def play(self, move, auto_reply=True):
        self.applied = []
        r, c = self._target(move)
        if self.result:
            raise BoardGameError(f'棋局已结束（{self._status_text()}），请重新开始')
        if self.board[r][c]:
            raise BoardGameError('这个交叉点已经有棋子了')
        self._place(r, c, self.turn)
        if auto_reply and self.opponent == 'robot' and not self.result:
            self.robot_reply(reset=False)
        return self.snapshot()

    def robot_reply(self, reset=True):
        if reset:
            self.applied = []
        if self.opponent != 'robot' or self.result or self.turn != self.other_side:
            return self.snapshot()
        point = self._robot_point()
        if point is not None:
            self._place(*point, self.turn)
        return self.snapshot()

    def _empty_points(self):
        return [(r, c) for r in range(GOMOKU_SIZE) for c in range(GOMOKU_SIZE) if not self.board[r][c]]

    def _robot_point(self):
        empty = self._empty_points()
        if not empty:
            return None
        if self.level == 'beginner':
            if random.random() < .18:
                return self.best_point(self.turn)
            return random.choice(empty)
        if self.level == 'easy':
            if random.random() < .5:
                return self.best_point(self.turn)
            return random.choice(empty)
        if self.level == 'normal':
            if random.random() < .12:
                return random.choice(empty)
            return self.best_point(self.turn)
        if self.level == 'hard':
            return self.best_point(self.turn)
        return self._strong_point(self.turn)

    def _strong_point(self, color):
        candidates = []
        for r, c in self._empty_points():
            self.board[r][c] = color
            if self._wins(self.board, r, c, color):
                self.board[r][c] = 0
                return (r, c)
            score = self._evaluate(self.board, r, c, color)
            self.board[r][c] = 0
            candidates.append((score, r, c))
        candidates.sort(reverse=True)
        best, best_score = None, -1
        other = WHITE if color == BLACK else BLACK
        for own_score, r, c in candidates[:12]:
            self.board[r][c] = color
            reply = self._opponent_reply_score(other)
            self.board[r][c] = 0
            value = own_score - reply * .9
            if value > best_score:
                best, best_score = (r, c), value
        return best

    def _opponent_reply_score(self, color):
        best = 0
        for r, c in self._empty_points():
            self.board[r][c] = color
            best = max(best, self._evaluate(self.board, r, c, color))
            self.board[r][c] = 0
        return best

    def _place(self, r, c, side):
        before = copy.deepcopy({'board': self.board, 'turn': self.turn, 'result': self.result,
                                'winner': self.winner, 'last_move': self.last_move})
        self.board[r][c] = side
        self.last_move = {'from': None, 'to': [r, c]}
        self.history.append({'side': side, 'move': {'from': None, 'to': [r, c]},
                             'text': f"{'黑' if side == BLACK else '白'} → {_gomoku_label(r, c)}", 'before': before})
        if self._wins(self.board, r, c, side):
            self.result = 'win'
            self.winner = 'human' if side == self.own_side else 'robot'
        elif all(cell for row in self.board for cell in row):
            self.result = 'draw'
            self.winner = None
        self.applied.append({'from': None, 'to': [r, c], 'code': 'black' if side == BLACK else 'white',
                             'side': 'human' if side == self.own_side else 'robot'})
        self.turn = WHITE if side == BLACK else BLACK

    def undo(self):
        steps = 2 if self.opponent == 'robot' else 1
        removed = 0
        while self.history and removed < steps:
            entry = self.history.pop()
            before = entry['before']
            self.board = before['board']
            self.turn = before['turn']
            self.result = before['result']
            self.winner = before['winner']
            self.last_move = before['last_move']
            removed += 1
        if not removed:
            raise BoardGameError('还没有可撤销的落子')
        return self.snapshot()

    def _target(self, move):
        if not isinstance(move, dict):
            raise BoardGameError('落子参数无效')
        target = _coord(move.get('to'))
        if target is None:
            raise BoardGameError('落子位置无效')
        r, c = target
        if not (0 <= r < GOMOKU_SIZE and 0 <= c < GOMOKU_SIZE):
            raise BoardGameError('落子位置超出棋盘')
        return r, c

    def _status_text(self):
        if self.result == 'draw':
            return '棋盘已满，平局'
        if self.result == 'win':
            if self.opponent == 'human':
                return ('黑方获胜' if self.winner == 'human' else '白方获胜') + '（双人本地对局）'
            return '你赢了' if self.winner == 'human' else '电脑获胜'
        if self.opponent == 'human':
            return ('黑方走棋' if self.turn == BLACK else '白方走棋') + '（双人本地对局）'
        return '轮到你走（黑）' if self.turn == self.own_side else '电脑走棋（白）'

    def snapshot(self):
        cells = []
        for r in range(GOMOKU_SIZE):
            for c in range(GOMOKU_SIZE):
                value = self.board[r][c]
                if value:
                    cells.append({'r': r, 'c': c, 'piece': {
                        'code': 'black' if value == BLACK else 'white', 'glyph': '',
                        'side': 'human' if value == self.own_side else 'robot'}})
        owner = 'human' if self.turn == self.own_side else 'robot'
        status = self._status_text()
        return {
            'game': self.key, 'title': self.title, 'rows': GOMOKU_SIZE, 'cols': GOMOKU_SIZE,
            'layout': 'points', 'cells': cells, 'drop': True, 'moves': [],
            'turn': 'black' if self.turn == BLACK else 'white', 'turn_owner': owner,
            'turn_label': (f"{'黑方（你）' if owner == 'human' else '白方（电脑）'}回合") if not self.result else status,
            'over': bool(self.result), 'status': status,
            'winner': self.winner, 'last_move': self.last_move,
            'legal': [], 'illegal': [], 'suggest': None,
            'history': [entry['text'] for entry in self.history][-8:],
            'can_undo': bool(self.history), 'opponent': self.opponent,
            'level': self.level, 'level_label': LEVELS[self.level],
            'sides': {'human': 'black', 'robot': 'white'}, 'note': '',
        }


def _gomoku_label(r, c):
    return f"第{r + 1}行第{c + 1}列"


# ---------------------------------------------------------------------------- 围棋

GO_SIZE = 15
GO_KOMI = 6.5


class Go:
    key = 'go'
    title = '围棋'
    rows = cols = GO_SIZE
    layout = 'points'
    drop = True
    own_side = BLACK
    other_side = WHITE

    def __init__(self, opponent='robot', level=DEFAULT_LEVEL):
        self.opponent = 'human' if opponent == 'human' else 'robot'
        self.level = level if level in LEVELS else DEFAULT_LEVEL
        self.reset()

    def reset(self):
        self.board = [[0] * GO_SIZE for _ in range(GO_SIZE)]
        self.turn = self.own_side
        self.history: list[dict] = []
        self.last_move = None
        self.ko: tuple[int, int] | None = None
        self.passes = 0
        self.captured = {BLACK: 0, WHITE: 0}
        self.result = None
        self.score = None
        self.applied: list[dict] = []

    # -- 气与提子 -----------------------------------------------------------
    def _neighbors(self, r, c):
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < GO_SIZE and 0 <= cc < GO_SIZE:
                yield rr, cc

    def _group(self, board, r, c):
        color = board[r][c]
        stones, liberties = set(), set()
        stack = [(r, c)]
        while stack:
            point = stack.pop()
            if point in stones:
                continue
            stones.add(point)
            for rr, cc in self._neighbors(*point):
                value = board[rr][cc]
                if value == 0:
                    liberties.add((rr, cc))
                elif value == color and (rr, cc) not in stones:
                    stack.append((rr, cc))
        return stones, liberties

    def _simulate(self, r, c, color):
        """返回 (是否合法, 提子数, 新棋盘, 原因, 被提子的位置)。不修改当前状态。"""
        if self.board[r][c]:
            return False, 0, None, '这个交叉点已经有棋子', []
        if self.ko == (r, c):
            return False, 0, None, '劫争：不能立刻回到刚被提子的位置，请先找劫材', []
        board = [row[:] for row in self.board]
        board[r][c] = color
        enemy = WHITE if color == BLACK else BLACK
        captured = 0
        captured_points = []
        for rr, cc in self._neighbors(r, c):
            if board[rr][cc] == enemy:
                stones, liberties = self._group(board, rr, cc)
                if not liberties:
                    for sr, sc in stones:
                        board[sr][sc] = 0
                    captured += len(stones)
                    captured_points.extend(stones)
        _, liberties = self._group(board, r, c)
        if not liberties:
            reason = '提子后自己的棋没有气（自杀），不能落子' if captured else '自杀：落子后自己这块棋没有气'
            return False, 0, None, reason, []
        return True, captured, board, '', captured_points

    def legal_point(self, r, c):
        if not (0 <= r < GO_SIZE and 0 <= c < GO_SIZE):
            return False, '落子位置超出棋盘'
        ok, captured, board, why = self._simulate(r, c, self.turn)[:4]
        return ok, '' if ok else why

    def legal_moves(self):
        if self.result:
            return []
        moves = [{'from': None, 'to': [r, c], 'pass': False}
                 for r in range(GO_SIZE) for c in range(GO_SIZE)
                 if self.legal_point(r, c)[0]]
        moves.append({'from': None, 'to': None, 'pass': True})
        return moves

    def suggest(self):
        if self.result:
            return None
        if all(cell for row in self.board for cell in row):
            return {'from': None, 'to': None, 'pass': True, 'reason': '棋盘已满，只能停一手'}
        best, best_score, best_reason = None, -1, ''
        for r in range(GO_SIZE):
            for c in range(GO_SIZE):
                if self.board[r][c]:
                    continue
                ok, captured, board, _, _points = self._simulate(r, c, self.turn)
                if not ok:
                    continue
                stones, liberties = self._group(board, r, c)
                score = captured * 20 + len(liberties) + len(stones) * 2
                if self.last_move and self.last_move.get('to'):
                    lr, lc = self.last_move['to']
                    score += max(0, 4 - (abs(lr - r) + abs(lc - c)))
                if score > best_score:
                    best, best_score = (r, c), score
                    best_reason = ('可以提掉对方 ' + str(captured) + ' 子') if captured else '靠近上一手，保持联络并扩大气'
        if best is None:
            return {'from': None, 'to': None, 'pass': True, 'reason': '没有合法落点，只能停一手'}
        return {'from': None, 'to': [best[0], best[1]], 'reason': best_reason}

    def play(self, move, auto_reply=True):
        self.applied = []
        if not isinstance(move, dict):
            raise BoardGameError('落子参数无效')
        if self.result:
            raise BoardGameError(f'棋局已结束（{self._status_text()}），请重新开始')
        if move.get('pass'):
            self._do_pass()
            if auto_reply and self.opponent == 'robot' and not self.result:
                self.robot_reply(reset=False)
            return self.snapshot()
        target = _coord(move.get('to'))
        if target is None:
            raise BoardGameError('落子位置无效')
        r, c = target
        ok, captured, board, why, points = self._simulate(r, c, self.turn)
        if not ok:
            raise BoardGameError(why)
        before = copy.deepcopy({'board': self.board, 'turn': self.turn, 'ko': self.ko, 'passes': self.passes,
                                'captured': self.captured, 'result': self.result, 'score': self.score,
                                'last_move': self.last_move})
        self.board = board
        self.captured[self.turn] += captured
        self.ko = points[0] if len(points) == 1 else None
        self.passes = 0
        self.last_move = {'from': None, 'to': [r, c]}
        self.history.append({'side': self.turn, 'move': {'from': None, 'to': [r, c]},
                             'text': f"{'黑' if self.turn == BLACK else '白'} → 第{r + 1}行第{c + 1}列"
                                     + (f"，提{captured}子" if captured else ''), 'before': before})
        self.applied.append({'from': None, 'to': [r, c], 'code': 'black' if self.turn == BLACK else 'white',
                             'side': 'human' if self.turn == self.own_side else 'robot',
                             'captured': [{'code': 'white' if self.turn == BLACK else 'black',
                                           'to': [point[0], point[1]]} for point in points]})
        self.turn = WHITE if self.turn == BLACK else BLACK
        if auto_reply and self.opponent == 'robot' and not self.result:
            self.robot_reply(reset=False)
        return self.snapshot()

    def robot_reply(self, reset=True):
        if reset:
            self.applied = []
        if self.opponent != 'robot' or self.result or self.turn != self.other_side:
            return self.snapshot()
        self._robot_move()
        return self.snapshot()

    def _do_pass(self):
        before = copy.deepcopy({'board': self.board, 'turn': self.turn, 'ko': self.ko, 'passes': self.passes,
                                'captured': self.captured, 'result': self.result, 'score': self.score,
                                'last_move': self.last_move})
        self.history.append({'side': self.turn, 'move': {'from': None, 'to': None, 'pass': True},
                             'text': f"{'黑' if self.turn == BLACK else '白'} 停一手", 'before': before})
        self.applied.append({'from': None, 'to': None, 'pass': True,
                             'code': 'black' if self.turn == BLACK else 'white',
                             'side': 'human' if self.turn == self.own_side else 'robot', 'captured': []})
        self.passes += 1
        self.ko = None
        self.last_move = {'from': None, 'to': None, 'pass': True}
        self.turn = WHITE if self.turn == BLACK else BLACK
        if self.passes >= 2:
            self._finish()

    def _robot_move(self):
        choice = self._robot_choice()
        if not choice:
            return
        if choice.get('pass') or choice.get('to') is None:
            self._do_pass()
            return
        r, c = choice['to']
        ok, captured, board, _, points = self._simulate(r, c, self.turn)
        if not ok:
            return
        before = copy.deepcopy({'board': self.board, 'turn': self.turn, 'ko': self.ko, 'passes': self.passes,
                                'captured': self.captured, 'result': self.result, 'score': self.score,
                                'last_move': self.last_move})
        self.board = board
        self.captured[self.turn] += captured
        self.ko = points[0] if len(points) == 1 else None
        self.passes = 0
        self.last_move = {'from': None, 'to': [r, c]}
        self.history.append({'side': self.turn, 'move': {'from': None, 'to': [r, c]},
                             'text': f"{'黑' if self.turn == BLACK else '白'}（电脑）→ 第{r + 1}行第{c + 1}列"
                                     + (f"，提{captured}子" if captured else ''), 'before': before})
        self.applied.append({'from': None, 'to': [r, c], 'code': 'black' if self.turn == BLACK else 'white',
                             'side': 'robot',
                             'captured': [{'code': 'white' if self.turn == BLACK else 'black',
                                           'to': [point[0], point[1]]} for point in points]})
        self.turn = WHITE if self.turn == BLACK else BLACK

    def _legal_points(self):
        points = []
        for r in range(GO_SIZE):
            for c in range(GO_SIZE):
                if self.legal_point(r, c)[0]:
                    points.append((r, c))
        return points

    def _robot_choice(self):
        points = self._legal_points()
        if self.level == 'beginner':
            if not points:
                return {'from': None, 'to': None, 'pass': True}
            return {'from': None, 'to': list(random.choice(points))}
        if self.level == 'easy':
            if random.random() < .45:
                return self.suggest()
            if not points:
                return {'from': None, 'to': None, 'pass': True}
            return {'from': None, 'to': list(random.choice(points))}
        if self.level == 'normal':
            if random.random() < .12 and points:
                return {'from': None, 'to': list(random.choice(points))}
            return self.suggest()
        if self.level == 'hard':
            return self.suggest()
        return self._strong_choice(points)

    def _strong_choice(self, points):
        if not points:
            return {'from': None, 'to': None, 'pass': True}
        scored = []
        for r, c in points:
            ok, captured, board, _, _ = self._simulate(r, c, self.turn)
            if not ok:
                continue
            stones, liberties = self._group(board, r, c)
            scored.append((captured * 20 + len(liberties) + len(stones) * 2, r, c, board))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return {'from': None, 'to': None, 'pass': True}
        other = WHITE if self.turn == BLACK else BLACK
        best, best_value = None, -1
        for own, r, c, board in scored[:10]:
            saved_board, saved_ko = self.board, self.ko
            self.board, self.ko = board, None
            reply = 0
            for rr, cc in self._legal_points():
                ok2, captured2, board2, _, _ = self._simulate(rr, cc, other)
                if ok2:
                    stones2, liberties2 = self._group(board2, rr, cc)
                    reply = max(reply, captured2 * 20 + len(liberties2) + len(stones2) * 2)
            self.board, self.ko = saved_board, saved_ko
            value = own - reply * .7
            if value > best_value:
                best, best_value = (r, c), value
        if best is None:
            return {'from': None, 'to': None, 'pass': True}
        return {'from': None, 'to': list(best)}

    def undo(self):
        steps = 2 if self.opponent == 'robot' else 1
        removed = 0
        while self.history and removed < steps:
            entry = self.history.pop()
            before = entry['before']
            for key in ('board', 'turn', 'ko', 'passes', 'captured', 'result', 'score', 'last_move'):
                setattr(self, key, before[key])
            removed += 1
        if not removed:
            raise BoardGameError('还没有可撤销的落子')
        if not self.history:
            self.ko = None
        return self.snapshot()

    def _finish(self):
        black = white = 0
        visited = [[False] * GO_SIZE for _ in range(GO_SIZE)]
        for r in range(GO_SIZE):
            for c in range(GO_SIZE):
                if visited[r][c]:
                    continue
                if self.board[r][c] == BLACK:
                    black += 1
                elif self.board[r][c] == WHITE:
                    white += 1
                else:
                    region, borders = [], set()
                    stack = [(r, c)]
                    while stack:
                        rr, cc = stack.pop()
                        if visited[rr][cc]:
                            continue
                        if self.board[rr][cc] != 0:
                            borders.add(self.board[rr][cc])
                            continue
                        visited[rr][cc] = True
                        region.append((rr, cc))
                        for nr, nc in self._neighbors(rr, cc):
                            if not visited[nr][nc]:
                                if self.board[nr][nc] == 0:
                                    stack.append((nr, nc))
                                else:
                                    borders.add(self.board[nr][nc])
                    if borders == {BLACK}:
                        black += len(region)
                    elif borders == {WHITE}:
                        white += len(region)
        white += GO_KOMI
        self.result = 'score'
        self.score = {'black': black, 'white': white, 'komi': GO_KOMI
                      }
        return self.score

    def _status_text(self):
        if self.result == 'score':
            black, white = self.score['black'], self.score['white']
            if black > white:
                return f'终局：黑 {black} 目 vs 白 {white} 目，黑胜'
            return f'终局：黑 {black} 目 vs 白 {white} 目，白胜（含贴目 {GO_KOMI}）'
        if self.passes == 1:
            return '对方停一手，你再停一手即终局'
        if self.opponent == 'human':
            return ('黑方走棋' if self.turn == BLACK else '白方走棋') + '（双人本地对局）'
        return '轮到你走（黑）' if self.turn == self.own_side else '电脑走棋（白）'

    def snapshot(self):
        cells = []
        for r in range(GO_SIZE):
            for c in range(GO_SIZE):
                value = self.board[r][c]
                if value:
                    cells.append({'r': r, 'c': c, 'piece': {
                        'code': 'black' if value == BLACK else 'white', 'glyph': '',
                        'side': 'human' if value == self.own_side else 'robot'}})
        illegal = []
        if not self.result:
            for r in range(GO_SIZE):
                for c in range(GO_SIZE):
                    ok, why = self.legal_point(r, c)
                    if not ok and not self.board[r][c]:
                        illegal.append({'to': [r, c], 'reason': why})
        owner = 'human' if self.turn == self.own_side else 'robot'
        status = self._status_text()
        return {
            'game': self.key, 'title': self.title, 'rows': GO_SIZE, 'cols': GO_SIZE,
            'layout': 'points', 'cells': cells, 'drop': True, 'moves': [],
            'turn': 'black' if self.turn == BLACK else 'white', 'turn_owner': owner,
            'turn_label': status, 'over': bool(self.result), 'status': status,
            'winner': ('human' if self.score['black'] > self.score['white'] else 'robot')
            if self.result == 'score' else None,
            'last_move': self.last_move,
            'legal': [], 'illegal': illegal, 'suggest': None,
            'history': [entry['text'] for entry in self.history][-8:],
            'can_undo': bool(self.history), 'opponent': self.opponent,
            'level': self.level, 'level_label': LEVELS[self.level],
            'sides': {'human': 'black', 'robot': 'white'},
            'captures': {'black': self.captured[BLACK], 'white': self.captured[WHITE]},
            'score': self.score, 'note': '简单劫争；15×15 区域数子并给白棋贴目 6.5',
        }


# ---------------------------------------------------------------------------- 国际象棋

FILES = 'abcdefgh'
WHITE_VALUE = {'P': 100, 'N': 320, 'B': 330, 'R': 500, 'Q': 900, 'K': 0}
CHESS_GLYPH = {'K': '♔', 'Q': '♕', 'R': '♖', 'B': '♗', 'N': '♘', 'P': '♙',
               'k': '♚', 'q': '♛', 'r': '♜', 'b': '♝', 'n': '♞', 'p': '♟'}
KNIGHT_STEPS = ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1))
KING_STEPS = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))
ROOK_DIRS = ((0, 1), (0, -1), (1, 0), (-1, 0))
BISHOP_DIRS = ((1, 1), (1, -1), (-1, 1), (-1, -1))


class Chess:
    key = 'chess'
    title = '国际象棋'
    rows = cols = 8
    layout = 'cells'
    drop = False
    own_side = 'w'
    other_side = 'b'

    def __init__(self, opponent='robot', level=DEFAULT_LEVEL):
        self.opponent = 'human' if opponent == 'human' else 'robot'
        self.level = level if level in LEVELS else DEFAULT_LEVEL
        self.reset()

    def reset(self):
        self.board = [
            list('rnbqkbnr'),
            list('pppppppp'),
            [''] * 8,
            [''] * 8,
            [''] * 8,
            [''] * 8,
            list('PPPPPPPP'),
            list('RNBQKBNR'),
        ]
        self.turn = 'w'
        self.castling = {'K': True, 'Q': True, 'k': True, 'q': True}
        self.en_passant = None
        self.halfmove = 0
        self.fullmove = 1
        self.history: list[dict] = []
        self.last_move = None
        self.result = None
        self.winner = None
        self.positions = [self._position_key()]
        self.applied: list[dict] = []

    @staticmethod
    def _side_of(piece):
        if not piece:
            return None
        return 'w' if piece.isupper() else 'b'

    def _king_pos(self, side):
        target = 'K' if side == 'w' else 'k'
        for r in range(8):
            for c in range(8):
                if self.board[r][c] == target:
                    return r, c
        return None

    def _position_key(self):
        board = '/'.join(''.join(row) or '-' for row in self.board)
        flags = ''.join(key for key, value in self.castling.items() if value) or '-'
        ep = f'{self.en_passant[0]}{self.en_passant[1]}' if self.en_passant else '-'
        return f'{board}|{self.turn}|{flags}|{ep}'

    def _attacked(self, r, c, by_side):
        board = self.board
        pawn = 'P' if by_side == 'w' else 'p'
        pawn_row = r + 1 if by_side == 'w' else r - 1
        if 0 <= pawn_row < 8:
            for cc in (c - 1, c + 1):
                if 0 <= cc < 8 and board[pawn_row][cc] == pawn:
                    return True
        for dr, dc in KNIGHT_STEPS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8:
                piece = board[rr][cc]
                if self._side_of(piece) == by_side and piece.lower() == 'n':
                    return True
        for dr, dc in KING_STEPS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8:
                piece = board[rr][cc]
                if self._side_of(piece) == by_side and piece.lower() == 'k':
                    return True
        for dirs, kinds in ((ROOK_DIRS, 'rq'), (BISHOP_DIRS, 'bq')):
            for dr, dc in dirs:
                rr, cc = r + dr, c + dc
                while 0 <= rr < 8 and 0 <= cc < 8:
                    piece = board[rr][cc]
                    if piece:
                        if self._side_of(piece) == by_side and piece.lower() in kinds:
                            return True
                        break
                    rr, cc = rr + dr, cc + dc
        return False

    def _in_check(self, side):
        pos = self._king_pos(side)
        return bool(pos) and self._attacked(pos[0], pos[1], 'b' if side == 'w' else 'w')

    # -- 走子生成 -----------------------------------------------------------
    def _pseudo(self, r, c, moves):
        board = self.board
        piece = board[r][c]
        side = self._side_of(piece)
        kind = piece.lower()
        if kind == 'p':
            step = -1 if side == 'w' else 1
            start = 6 if side == 'w' else 1
            last = 0 if side == 'w' else 7
            nr = r + step
            if 0 <= nr < 8 and not board[nr][c]:
                self._pawn_target(moves, r, c, nr, c, last, None)
                if r == start and not board[r + 2 * step][c]:
                    moves.append({'from': [r, c], 'to': [r + 2 * step, c], 'capture': False})
            for dc in (-1, 1):
                cc = c + dc
                if not (0 <= cc < 8 and 0 <= nr < 8):
                    continue
                target = board[nr][cc]
                if target and self._side_of(target) != side:
                    self._pawn_target(moves, r, c, nr, cc, last, target)
                elif (nr, cc) == self.en_passant:
                    moves.append({'from': [r, c], 'to': [nr, cc], 'capture': True, 'en_passant': True})
        elif kind == 'n':
            for dr, dc in KNIGHT_STEPS:
                rr, cc = r + dr, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8 and self._side_of(board[rr][cc]) != side:
                    moves.append({'from': [r, c], 'to': [rr, cc], 'capture': bool(board[rr][cc])})
        elif kind == 'k':
            for dr, dc in KING_STEPS:
                rr, cc = r + dr, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8 and self._side_of(board[rr][cc]) != side:
                    moves.append({'from': [r, c], 'to': [rr, cc], 'capture': bool(board[rr][cc])})
            self._castles(r, c, side, moves)
        else:
            dirs = ROOK_DIRS if kind == 'r' else BISHOP_DIRS if kind == 'b' else ROOK_DIRS + BISHOP_DIRS
            for dr, dc in dirs:
                rr, cc = r + dr, c + dc
                while 0 <= rr < 8 and 0 <= cc < 8:
                    target = board[rr][cc]
                    if self._side_of(target) == side:
                        break
                    moves.append({'from': [r, c], 'to': [rr, cc], 'capture': bool(target)})
                    if target:
                        break
                    rr, cc = rr + dr, cc + dc

    def _pawn_target(self, moves, r, c, nr, cc, last, captured):
        if nr == last:
            for promo in 'QRBN':
                moves.append({'from': [r, c], 'to': [nr, cc], 'capture': bool(captured), 'promotion': promo})
        else:
            moves.append({'from': [r, c], 'to': [nr, cc], 'capture': bool(captured)})

    def _castles(self, r, c, side, moves):
        if side == 'w' and r == 7 and c == 4:
            if self.castling.get('K') and self.board[7][7] == 'R' and not self.board[7][5] and not self.board[7][6] \
                    and not self._in_check('w') and not self._attacked(7, 5, 'b') and not self._attacked(7, 6, 'b'):
                moves.append({'from': [7, 4], 'to': [7, 6], 'capture': False, 'castle': 'K'})
            if self.castling.get('Q') and self.board[7][0] == 'R' and not self.board[7][1] \
                    and not self.board[7][2] and not self.board[7][3] \
                    and not self._in_check('w') and not self._attacked(7, 3, 'b') and not self._attacked(7, 2, 'b'):
                moves.append({'from': [7, 4], 'to': [7, 2], 'capture': False, 'castle': 'Q'})
        elif side == 'b' and r == 0 and c == 4:
            if self.castling.get('k') and self.board[0][7] == 'r' and not self.board[0][5] and not self.board[0][6] \
                    and not self._in_check('b') and not self._attacked(0, 5, 'w') and not self._attacked(0, 6, 'w'):
                moves.append({'from': [0, 4], 'to': [0, 6], 'capture': False, 'castle': 'k'})
            if self.castling.get('q') and self.board[0][0] == 'r' and not self.board[0][1] \
                    and not self.board[0][2] and not self.board[0][3] \
                    and not self._in_check('b') and not self._attacked(0, 3, 'w') and not self._attacked(0, 2, 'w'):
                moves.append({'from': [0, 4], 'to': [0, 2], 'capture': False, 'castle': 'q'})

    def legal_moves(self, side=None):
        side = side or self.turn
        moves = []
        for r in range(8):
            for c in range(8):
                if self._side_of(self.board[r][c]) != side:
                    continue
                pseudo: list[dict] = []
                self._pseudo(r, c, pseudo)
                for move in pseudo:
                    if self._apply_safe(move, side):
                        moves.append(move)
        return moves

    def _apply_safe(self, move, side):
        saved = self._save_light()
        self._apply(move, side, record=False)
        ok = not self._in_check(side)
        self._restore_light(saved)
        return ok

    def _save_light(self):
        return ([row[:] for row in self.board], dict(self.castling), self.en_passant, self.turn,
                self.halfmove, self.fullmove)

    def _restore_light(self, saved):
        board, castling, en_passant, turn, halfmove, fullmove = saved
        self.board = [row[:] for row in board]
        self.castling = dict(castling)
        self.en_passant = en_passant
        self.turn = turn
        self.halfmove = halfmove
        self.fullmove = fullmove

    # -- 执行 ---------------------------------------------------------------
    def _apply(self, move, side, record=True):
        fr, fc = move['from']
        tr, tc = move['to']
        piece = self.board[fr][fc]
        captured = self.board[tr][tc]
        capture_square = [fr, tc] if move.get('en_passant') else [tr, tc]
        captured_piece = self.board[capture_square[0]][capture_square[1]] if move.get('en_passant') else captured
        before = None
        if record:
            before = copy.deepcopy({'board': self.board, 'turn': self.turn, 'castling': self.castling,
                                    'en_passant': self.en_passant, 'halfmove': self.halfmove,
                                    'fullmove': self.fullmove, 'result': self.result, 'winner': self.winner,
                                    'last_move': self.last_move, 'positions': self.positions})
        if move.get('en_passant'):
            self.board[fr][tc] = ''
        self.board[tr][tc] = piece
        self.board[fr][fc] = ''
        if move.get('castle'):
            if move['castle'] in ('K', 'k'):
                self.board[tr][5] = self.board[tr][7]
                self.board[tr][7] = ''
            else:
                self.board[tr][3] = self.board[tr][0]
                self.board[tr][0] = ''
        if move.get('promotion'):
            self.board[tr][tc] = move['promotion'] if side == 'w' else move['promotion'].lower()
        # 易位权
        if piece == 'K':
            self.castling['K'] = self.castling['Q'] = False
        if piece == 'k':
            self.castling['k'] = self.castling['q'] = False
        if (fr, fc) == (7, 7) or (tr, tc) == (7, 7):
            self.castling['K'] = False
        if (fr, fc) == (7, 0) or (tr, tc) == (7, 0):
            self.castling['Q'] = False
        if (fr, fc) == (0, 7) or (tr, tc) == (0, 7):
            self.castling['k'] = False
        if (fr, fc) == (0, 0) or (tr, tc) == (0, 0):
            self.castling['q'] = False
        self.en_passant = None
        if piece.lower() == 'p' and abs(tr - fr) == 2:
            self.en_passant = ((fr + tr) // 2, fc)
        self.halfmove = 0 if (piece.lower() == 'p' or captured or move.get('capture')) else self.halfmove + 1
        if side == 'b':
            self.fullmove += 1
        self.turn = 'b' if side == 'w' else 'w'
        if record:
            self.history.append({'side': side, 'move': move, 'before': before,
                                 'text': self._notation(move, piece, captured)})
            self.last_move = {'from': list(move['from']), 'to': list(move['to'])}
            self.positions.append(self._position_key())
            self.applied.append({
                'from': list(move['from']), 'to': list(move['to']), 'code': piece,
                'promotion': move.get('promotion'), 'castle': move.get('castle'),
                'en_passant': bool(move.get('en_passant')),
                'side': 'human' if side == self.own_side else 'robot',
                'captured': ({'code': captured_piece, 'to': capture_square}
                             if captured_piece and captured_piece != piece else None)})
        return captured

    def _notation(self, move, piece, captured):
        fr, fc = move['from']
        tr, tc = move['to']
        text = f"{piece}{FILES[fc]}{8 - fr}{'x' if captured else '-'}{FILES[tc]}{8 - tr}"
        if move.get('castle') in ('K', 'k'):
            text = f"{'白' if piece.isupper() else '黑'}王车易位（短）"
        elif move.get('castle') in ('Q', 'q'):
            text = f"{'白' if piece.isupper() else '黑'}王车易位（长）"
        elif move.get('promotion'):
            text += '=' + move['promotion']
        return text

    def _insufficient_material(self):
        pieces = [(r, c, self.board[r][c]) for r in range(8) for c in range(8)
                  if self.board[r][c] and self.board[r][c].lower() != 'k']
        if not pieces:
            return True
        if len(pieces) == 1 and pieces[0][2].lower() in ('b', 'n'):
            return True
        if len(pieces) == 2 and pieces[0][2].isupper() != pieces[1][2].isupper():
            kinds = {pieces[0][2].lower(), pieces[1][2].lower()}
            # 单象对单象、单马对单马都无法强制将死；混合兵种仍可能有杀。
            if kinds == {'b'} or kinds == {'n'}:
                return True
        return False

    def _finish_if_needed(self):
        moves = self.legal_moves(self.turn)
        if not moves:
            if self._in_check(self.turn):
                self.result = 'checkmate'
                self.winner = 'robot' if self.turn == self.own_side else 'human'
            else:
                self.result = 'stalemate'
                self.winner = None
        elif self.halfmove >= 100:
            self.result = 'fifty'
            self.winner = None
        elif self.positions.count(self.positions[-1]) >= 3:
            self.result = 'repetition'
            self.winner = None
        elif self._insufficient_material():
            self.result = 'insufficient'
            self.winner = None

    def play(self, move, auto_reply=True):
        self.applied = []
        if self.result:
            raise BoardGameError(f'棋局已结束（{self._status_text()}），请重新开始')
        moves = self.legal_moves(self.turn)
        candidate = self._match(moves, move)
        if candidate is None:
            raise BoardGameError(self._reject_reason(move))
        self._apply(candidate, self.turn)
        self._finish_if_needed()
        if auto_reply and self.opponent == 'robot' and not self.result and self.turn == 'b':
            self.robot_reply(reset=False)
        return self.snapshot()

    def robot_reply(self, reset=True):
        if reset:
            self.applied = []
        if self.opponent != 'robot' or self.result or self.turn == self.own_side:
            return self.snapshot()
        reply = self._robot_choice()
        if reply:
            self._apply(reply, 'b')
            self._finish_if_needed()
        return self.snapshot()

    def _robot_choice(self):
        moves = self.legal_moves(self.turn)
        if not moves:
            return None
        if self.level == 'beginner':
            return random.choice(moves)
        if self.level == 'easy':
            if random.random() < .5:
                return random.choice(moves)
            return self.best_move(self.turn, depth=1)
        if self.level == 'normal':
            if random.random() < .08:
                return random.choice(moves)
            return self.best_move(self.turn, depth=1)
        if self.level == 'hard':
            return self.best_move(self.turn, depth=2)
        return self.best_move(self.turn, depth=3)

    def _match(self, moves, move):
        if not isinstance(move, dict):
            raise BoardGameError('走子参数无效')
        fr = _coord(move.get('from'))
        to = _coord(move.get('to'))
        if fr is None or to is None:
            raise BoardGameError('走子坐标无效')
        promo = move.get('promotion') or 'Q'
        for option in moves:
            if tuple(option['from']) == fr and tuple(option['to']) == to \
                    and option.get('promotion', 'Q') == promo:
                return option
        return None

    def _reject_reason(self, move):
        fr = _coord(move.get('from')) if isinstance(move, dict) else None
        piece = self.board[fr[0]][fr[1]] if fr and 0 <= fr[0] < 8 and 0 <= fr[1] < 8 else ''
        if not piece:
            return '这里没有棋子'
        if self._side_of(piece) != self.turn:
            return '现在不是你这一方走棋'
        if self._in_check(self.turn):
            return '被将军：这一步不能解除将军'
        return '这个目标不符合该棋子的走法（或会让己方被将军）'

    def undo(self):
        steps = 2 if self.opponent == 'robot' else 1
        removed = 0
        while self.history and removed < steps:
            entry = self.history.pop()
            before = entry['before']
            for key in ('board', 'turn', 'castling', 'en_passant', 'halfmove', 'fullmove',
                        'result', 'winner', 'last_move', 'positions'):
                setattr(self, key, before[key])
            removed += 1
        if not removed:
            raise BoardGameError('还没有可撤销的走子')
        return self.snapshot()

    # -- 评估与建议 ---------------------------------------------------------
    def _eval(self):
        score = 0
        for row in self.board:
            for piece in row:
                if not piece:
                    continue
                value = WHITE_VALUE[piece.upper()]
                score += value if piece.isupper() else -value
        return score

    def _ordered(self, moves):
        return sorted(moves, key=lambda m: (0 if m.get('capture') else 1, 0 if m.get('promotion') else 1))

    def _search(self, side, depth, alpha, beta, budget=None):
        if budget is not None:budget.visit()
        if depth <= 0:
            value = self._eval()
            return value if side == 'w' else -value
        moves = self._ordered(self.legal_moves(side))
        if not moves:
            return -99999 if self._in_check(side) else 0
        best = -99999
        for move in moves:
            saved = self._save_light()
            try:
                self._apply(move, side, record=False)
                value = -self._search('b' if side == 'w' else 'w', depth - 1, -beta, -alpha,budget)
            finally:self._restore_light(saved)
            best = max(best, value)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return best

    def best_move(self, side=None, depth=2, *, budget_s=None,node_budget=None):
        return _bounded_best_move(self,side or self.turn,depth,budget_s,node_budget)

    def suggest(self):
        if self.result:
            return None
        move = self.best_move(self.turn)
        if not move:
            return None
        text = self._notation(move, self.board[move['from'][0]][move['from'][1]], None)
        return {'from': list(move['from']), 'to': list(move['to']),
                'promotion': move.get('promotion', 'Q'),
                'reason': ('可以吃掉对方棋子：' if move.get('capture') else '改善子力位置：') + text}

    # -- 状态 ---------------------------------------------------------------
    def _status_text(self):
        if self.result == 'checkmate':
            if self.opponent == 'human':
                return '将死：黑方获胜' if self.winner == 'robot' else '将死：白方获胜'
            return '将死：你赢了' if self.winner == 'human' else '将死：电脑获胜'
        if self.result == 'stalemate':
            return '逼和：轮到的一方无子可走，和棋'
        if self.result == 'fifty':
            return '50 回合无吃子无兵动，和棋'
        if self.result == 'repetition':
            return '同一局面出现三次，和棋'
        if self.result == 'insufficient':
            return '子力不足，无法将死，和棋'
        if self.opponent == 'human':
            return ('白方走棋' if self.turn == 'w' else '黑方走棋') + '（双人本地对局）'
        check = '（被将军）' if self._in_check(self.turn) else ''
        return ('轮到你走' if self.turn == 'w' else '电脑走棋') + check

    def snapshot(self):
        cells = []
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if not piece:
                    continue
                cells.append({'r': r, 'c': c, 'piece': {
                    'code': piece, 'glyph': CHESS_GLYPH[piece],
                    'side': 'human' if piece.isupper() else 'robot'}})
        moves = [] if self.result else self.legal_moves(self.turn)
        status = self._status_text()
        return {
            'game': self.key, 'title': self.title, 'rows': 8, 'cols': 8, 'layout': 'cells',
            'cells': cells, 'drop': False,
            'moves': [{'from': m['from'], 'to': m['to'], 'capture': bool(m.get('capture')),
                       'promotion': m.get('promotion')} for m in moves],
            'turn': self.turn, 'turn_owner': 'human' if self.turn == 'w' else 'robot',
            'turn_label': status, 'over': bool(self.result), 'status': status,
            'winner': self.winner, 'last_move': self.last_move,
            'check': list(self._king_pos(self.turn)) if self._in_check(self.turn) else None,
            'legal': [], 'illegal': [], 'suggest': None,
            'history': [entry['text'] for entry in self.history][-8:],
            'can_undo': bool(self.history), 'opponent': self.opponent,
            'level': self.level, 'level_label': LEVELS[self.level],
            'sides': {'human': 'white', 'robot': 'black'},
            'note': '2D 完整规则；实体棋子执行尚未接入',
        }


# ---------------------------------------------------------------------------- 中国象棋

XIANGQI_GLYPH = {'K': '帥', 'A': '仕', 'E': '相', 'H': '傌', 'R': '俥', 'C': '炮', 'P': '兵'}
XIANGQI_GLYPH_BLACK = {'k': '將', 'a': '士', 'e': '象', 'h': '馬', 'r': '車', 'c': '砲', 'p': '卒'}
XIANGQI_VALUE = {}
XIANGQI_FILES = 'abcdefghi'


class Xiangqi:
    key = 'xiangqi'
    title = '中国象棋'
    rows = 10
    cols = 9
    layout = 'points'
    drop = False
    own_side = 'r'
    other_side = 'b'

    def __init__(self, opponent='robot', level=DEFAULT_LEVEL):
        self.opponent = 'human' if opponent == 'human' else 'robot'
        self.level = level if level in LEVELS else DEFAULT_LEVEL
        self.reset()

    def reset(self):
        self.board = [[''] * 9 for _ in range(10)]
        back = list('rheakaehr')
        self.board[0] = list(back)
        self.board[2][1] = 'c'
        self.board[2][7] = 'c'
        for c in (0, 2, 4, 6, 8):
            self.board[3][c] = 'p'
            self.board[6][c] = 'P'
        self.board[7][1] = 'C'
        self.board[7][7] = 'C'
        self.board[9] = list(''.join(back).upper())
        self.turn = 'r'
        self.history: list[dict] = []
        self.last_move = None
        self.result = None
        self.winner = None
        self.applied: list[dict] = []

    @staticmethod
    def _side_of(piece):
        if not piece:
            return None
        return 'r' if piece.isupper() else 'b'

    def _in_palace(self, r, c, side):
        if not 3 <= c <= 5:
            return False
        return 7 <= r <= 9 if side == 'r' else 0 <= r <= 2

    def _crossed(self, r, side):
        return r <= 4 if side == 'r' else r >= 5

    def _king_pos(self, side):
        target = 'K' if side == 'r' else 'k'
        for r in range(10):
            for c in range(9):
                if self.board[r][c] == target:
                    return r, c
        return None

    def _kings_face(self):
        red = self._king_pos('r')
        black = self._king_pos('b')
        if not red or not black or red[1] != black[1]:
            return False
        col = red[1]
        low, high = sorted((red[0], black[0]))
        return all(not self.board[r][col] for r in range(low + 1, high))

    def _attacked(self, r, c, by_side):
        board = self.board
        # 车与炮：沿四条射线扫描
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            screen = 0
            while 0 <= rr < 10 and 0 <= cc < 9:
                piece = board[rr][cc]
                if piece:
                    side = self._side_of(piece)
                    kind = piece.lower()
                    if screen == 0 and side == by_side and kind in ('r', 'k'):
                        if kind == 'r' or abs(rr - r) + abs(cc - c) == 1:
                            return True
                    if screen == 1 and side == by_side and kind == 'c':
                        return True
                    screen += 1
                rr, cc = rr + dr, cc + dc
        # 马：反向找可能的马位置，马腿在两者之间
        for dr, dc in ((2, 1), (2, -1), (-2, 1), (-2, -1), (1, 2), (1, -2), (-1, 2), (-1, -2)):
            hr, hc = r + dr, c + dc
            if not (0 <= hr < 10 and 0 <= hc < 9):
                continue
            piece = board[hr][hc]
            if self._side_of(piece) != by_side or piece.lower() != 'h':
                continue
            leg_r = hr - (1 if dr > 0 else -1) if abs(dr) == 2 else hr
            leg_c = hc - (1 if dc > 0 else -1) if abs(dc) == 2 else hc
            if not board[leg_r][leg_c]:
                return True
        # 兵/卒
        step = -1 if by_side == 'r' else 1
        if 0 <= r - step < 10 and board[r - step][c] == ('P' if by_side == 'r' else 'p'):
            return True
        for dc in (-1, 1):
            cc = c + dc
            if 0 <= cc < 9:
                piece = board[r][cc]
                if self._side_of(piece) == by_side and piece.lower() == 'p' \
                        and self._crossed(r, by_side):
                    return True
        return False

    def _in_check(self, side):
        pos = self._king_pos(side)
        if not pos:
            return True
        return self._attacked(pos[0], pos[1], 'b' if side == 'r' else 'r') or self._kings_face()

    def _pseudo(self, r, c, moves):
        board = self.board
        piece = board[r][c]
        side = self._side_of(piece)
        kind = piece.lower()

        def add(tr, tc, capture=True):
            if 0 <= tr < 10 and 0 <= tc < 9 and self._side_of(board[tr][tc]) != side:
                moves.append({'from': [r, c], 'to': [tr, tc], 'capture': bool(board[tr][tc])})

        if kind == 'k':
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                rr, cc = r + dr, c + dc
                if self._in_palace(rr, cc, side):
                    add(rr, cc)
        elif kind == 'a':
            for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                rr, cc = r + dr, c + dc
                if self._in_palace(rr, cc, side):
                    add(rr, cc)
        elif kind == 'e':
            for dr, dc in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < 10 and 0 <= cc < 9):
                    continue
                if side == 'r' and rr < 5 or side == 'b' and rr > 4:
                    continue
                if not board[r + dr // 2][c + dc // 2]:
                    add(rr, cc)
        elif kind == 'h':
            for dr, dc in ((2, 1), (2, -1), (-2, 1), (-2, -1), (1, 2), (1, -2), (-1, 2), (-1, -2)):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < 10 and 0 <= cc < 9):
                    continue
                leg_r = r + (1 if dr > 0 else -1) if abs(dr) == 2 else r
                leg_c = c + (1 if dc > 0 else -1) if abs(dc) == 2 else c
                if not board[leg_r][leg_c]:
                    add(rr, cc)
        elif kind == 'r':
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                rr, cc = r + dr, c + dc
                while 0 <= rr < 10 and 0 <= cc < 9:
                    target = board[rr][cc]
                    if not target:
                        moves.append({'from': [r, c], 'to': [rr, cc], 'capture': False})
                    else:
                        if self._side_of(target) != side:
                            moves.append({'from': [r, c], 'to': [rr, cc], 'capture': True})
                        break
                    rr, cc = rr + dr, cc + dc
        elif kind == 'c':
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                rr, cc = r + dr, c + dc
                screen = False
                while 0 <= rr < 10 and 0 <= cc < 9:
                    target = board[rr][cc]
                    if not screen:
                        if not target:
                            moves.append({'from': [r, c], 'to': [rr, cc], 'capture': False})
                        else:
                            screen = True
                    else:
                        if target:
                            if self._side_of(target) != side:
                                moves.append({'from': [r, c], 'to': [rr, cc], 'capture': True})
                            break
                    rr, cc = rr + dr, cc + dc
        elif kind == 'p':
            step = -1 if side == 'r' else 1
            add(r + step, c)
            if self._crossed(r, side):
                add(r, c - 1)
                add(r, c + 1)

    def legal_moves(self, side=None):
        side = side or self.turn
        moves = []
        for r in range(10):
            for c in range(9):
                if self._side_of(self.board[r][c]) != side:
                    continue
                pseudo: list[dict] = []
                self._pseudo(r, c, pseudo)
                for move in pseudo:
                    if self._apply_safe(move, side):
                        moves.append(move)
        return moves

    def _apply_safe(self, move, side):
        saved = [row[:] for row in self.board]
        previous_turn = self.turn
        self._apply(move, side, record=False)
        ok = not self._in_check(side)
        self.board = saved
        self.turn = previous_turn
        return ok

    def _apply(self, move, side, record=True):
        fr, fc = move['from']
        tr, tc = move['to']
        piece = self.board[fr][fc]
        captured = self.board[tr][tc]
        before = None
        if record:
            before = copy.deepcopy({'board': self.board, 'turn': self.turn, 'result': self.result,
                                    'winner': self.winner, 'last_move': self.last_move})
        self.board[tr][tc] = piece
        self.board[fr][fc] = ''
        self.turn = 'b' if side == 'r' else 'r'
        if record:
            self.history.append({'side': side, 'move': move, 'before': before,
                                 'text': self._notation(piece, fr, fc, tr, tc)})
            self.last_move = {'from': list(move['from']), 'to': list(move['to'])}
            self.applied.append({'from': list(move['from']), 'to': list(move['to']), 'code': piece,
                                 'promotion': None, 'castle': None, 'en_passant': False,
                                 'side': 'human' if side == self.own_side else 'robot',
                                 'captured': ({'code': captured, 'to': [tr, tc]}
                                              if captured and captured != piece else None)})
        return captured

    def _notation(self, piece, fr, fc, tr, tc):
        glyph = XIANGQI_GLYPH.get(piece, XIANGQI_GLYPH_BLACK.get(piece, '?'))
        return f"{glyph} {XIANGQI_FILES[fc]}{10 - fr}→{XIANGQI_FILES[tc]}{10 - tr}"

    def _finish_if_needed(self):
        if not self.legal_moves(self.turn):
            if self._in_check(self.turn):
                self.result = 'checkmate'
                self.winner = 'robot' if self.turn == self.own_side else 'human'
            else:
                self.result = 'stalemate'
                self.winner = 'robot' if self.turn == self.own_side else 'human'

    def play(self, move, auto_reply=True):
        self.applied = []
        if self.result:
            raise BoardGameError(f'棋局已结束（{self._status_text()}），请重新开始')
        moves = self.legal_moves(self.turn)
        candidate = None
        if isinstance(move, dict):
            fr, to = _coord(move.get('from')), _coord(move.get('to'))
            if fr and to:
                candidate = next((m for m in moves if tuple(m['from']) == fr and tuple(m['to']) == to), None)
        if candidate is None:
            raise BoardGameError(self._reject_reason(move))
        self._apply(candidate, self.turn)
        self._finish_if_needed()
        if auto_reply and self.opponent == 'robot' and not self.result and self.turn == 'b':
            self.robot_reply(reset=False)
        return self.snapshot()

    def robot_reply(self, reset=True):
        if reset:
            self.applied = []
        if self.opponent != 'robot' or self.result or self.turn == self.own_side:
            return self.snapshot()
        reply = self._robot_choice()
        if reply:
            self._apply(reply, 'b')
            self._finish_if_needed()
        return self.snapshot()

    def _robot_choice(self):
        moves = self.legal_moves(self.turn)
        if not moves:
            return None
        if self.level == 'beginner':
            return random.choice(moves)
        if self.level == 'easy':
            if random.random() < .5:
                return random.choice(moves)
            return self.best_move(self.turn, depth=1)
        if self.level == 'normal':
            if random.random() < .08:
                return random.choice(moves)
            return self.best_move(self.turn, depth=1)
        if self.level == 'hard':
            return self.best_move(self.turn, depth=2)
        return self.best_move(self.turn, depth=3)

    def _reject_reason(self, move):
        fr = _coord(move.get('from')) if isinstance(move, dict) else None
        piece = self.board[fr[0]][fr[1]] if fr and 0 <= fr[0] < 10 and 0 <= fr[1] < 9 else ''
        if not piece:
            return '这里没有棋子'
        if self._side_of(piece) != self.turn:
            return '现在不是你这一方走棋'
        if self._in_check(self.turn):
            return '被将军：这一步不能解除将军'
        to = _coord(move.get('to'))
        if fr and to and 0 <= to[0] < 10 and 0 <= to[1] < 9:
            saved = [row[:] for row in self.board]
            previous_turn = self.turn
            self._apply({'from': list(fr), 'to': list(to)}, self.turn, record=False)
            facing = self._kings_face()
            self.board = saved
            self.turn = previous_turn
            if facing:
                return '被将军：这一步会让将帅照面（飞将），不能走'
        return '这个目标不符合该棋子的走法（马腿、象眼、炮架、过河或九宫限制）'

    def undo(self):
        steps = 2 if self.opponent == 'robot' else 1
        removed = 0
        while self.history and removed < steps:
            entry = self.history.pop()
            before = entry['before']
            for key in ('board', 'turn', 'result', 'winner', 'last_move'):
                setattr(self, key, before[key])
            removed += 1
        if not removed:
            raise BoardGameError('还没有可撤销的走子')
        return self.snapshot()

    def _eval(self):
        score = 0
        for row in self.board:
            for piece in row:
                if not piece:
                    continue
                kind = piece.lower()
                value = {'k': 10000, 'r': 900, 'c': 450, 'h': 400, 'a': 200, 'e': 200, 'p': 100}[kind]
                score += value if piece.isupper() else -value
        return score

    def _save_light(self):
        return [row[:] for row in self.board],self.turn

    def _restore_light(self,saved):
        self.board,self.turn=saved

    def _search(self, side, depth, alpha, beta, budget=None):
        if budget is not None:budget.visit()
        if depth <= 0:
            value = self._eval()
            return value if side == 'r' else -value
        moves = self._ordered(self.legal_moves(side))
        if not moves:
            return -99999 if self._in_check(side) else 0
        best = -99999
        for move in moves:
            saved = self._save_light()
            try:
                self._apply(move, side, record=False)
                value = -self._search('b' if side == 'r' else 'r', depth - 1, -beta, -alpha,budget)
            finally:self._restore_light(saved)
            best = max(best, value)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return best

    def _ordered(self, moves):
        return sorted(moves, key=lambda move: 0 if move.get('capture') else 1)

    def best_move(self, side=None, depth=2, *, budget_s=None,node_budget=None):
        return _bounded_best_move(self,side or self.turn,depth,budget_s,node_budget)

    def suggest(self):
        if self.result:
            return None
        move = self.best_move(self.turn)
        if not move:
            return None
        fr, fc = move['from']
        tr, tc = move['to']
        text = self._notation(self.board[fr][fc], fr, fc, tr, tc)
        return {'from': list(move['from']), 'to': list(move['to']),
                'reason': ('可以吃掉对方棋子：' if move.get('capture') else '改善子力位置：') + text}

    def _status_text(self):
        if self.result == 'checkmate':
            if self.opponent == 'human':
                return '将死：黑方获胜' if self.winner == 'robot' else '将死：红方获胜'
            return '将死：你赢了' if self.winner == 'human' else '将死：电脑获胜'
        if self.result == 'stalemate':
            return '困毙：轮到的一方无子可走，判负'
        if self.opponent == 'human':
            return ('红方走棋' if self.turn == 'r' else '黑方走棋') + '（双人本地对局）'
        check = '（被将军）' if self._in_check(self.turn) else ''
        return ('轮到你走' if self.turn == 'r' else '电脑走棋') + check

    def snapshot(self):
        cells = []
        for r in range(10):
            for c in range(9):
                piece = self.board[r][c]
                if not piece:
                    continue
                glyph = XIANGQI_GLYPH.get(piece) or XIANGQI_GLYPH_BLACK.get(piece, '?')
                cells.append({'r': r, 'c': c, 'piece': {
                    'code': piece, 'glyph': glyph,
                    'side': 'human' if piece.isupper() else 'robot'}})
        moves = [] if self.result else self.legal_moves(self.turn)
        status = self._status_text()
        return {
            'game': self.key, 'title': self.title, 'rows': 10, 'cols': 9, 'layout': 'points',
            'cells': cells, 'drop': False,
            'moves': [{'from': m['from'], 'to': m['to'], 'capture': bool(m.get('capture'))} for m in moves],
            'turn': self.turn, 'turn_owner': 'human' if self.turn == 'r' else 'robot',
            'turn_label': status, 'over': bool(self.result), 'status': status,
            'winner': self.winner, 'last_move': self.last_move,
            'check': list(self._king_pos(self.turn)) if self._in_check(self.turn) else None,
            'legal': [], 'illegal': [], 'suggest': None,
            'history': [entry['text'] for entry in self.history][-8:],
            'can_undo': bool(self.history), 'opponent': self.opponent,
            'level': self.level, 'level_label': LEVELS[self.level],
            'sides': {'human': 'red', 'robot': 'black'},
            'note': '2D 完整规则（含飞将/九宫/过河）；实体棋子执行尚未接入',
        }


# ---------------------------------------------------------------------------- 注册表与存储

ENGINES = {
    TicTacToe.key: TicTacToe,
    Gomoku.key: Gomoku,
    Go.key: Go,
    Chess.key: Chess,
    Xiangqi.key: Xiangqi,
}


class GameStore:
    """按棋种保存一局 2D 棋局；单机单用户，不参与机器人运动。"""

    def __init__(self):
        import threading
        self._lock = threading.RLock()
        self._games: dict[str, Any] = {}
        self.last_applied: list[dict] = []

    def _engine(self, key):
        factory = ENGINES.get(key)
        if factory is None:
            raise BoardGameError(f'不支持的棋类：{key}')
        if key not in self._games:
            self._games[key] = factory()
        return self._games[key]

    def snapshot(self, key):
        with self._lock:
            return self._engine(key).snapshot()

    def new(self, key, payload=None):
        payload = payload or {}
        if not isinstance(payload, dict):
            raise BoardGameError('新局参数无效')
        if set(payload) - {'opponent', 'level'}:
            raise BoardGameError('新局参数包含未知字段')
        opponent = payload.get('opponent', 'robot')
        if opponent not in ('robot', 'human'):
            raise BoardGameError('对手只能是 robot 或 human')
        level = payload.get('level', DEFAULT_LEVEL)
        if level not in LEVELS:
            raise BoardGameError('难度必须是入门、简单、一般、困难或不可战胜')
        with self._lock:
            engine = ENGINES[key](opponent, level) if key in ENGINES else None
            if engine is None:
                raise BoardGameError(f'不支持的棋类：{key}')
            self._games[key] = engine
            self.last_applied = []
            return engine.snapshot()

    def move(self, key, payload, auto_reply=True):
        if not isinstance(payload, dict):
            raise BoardGameError('走子参数无效')
        unknown = set(payload) - {'from', 'to', 'promotion', 'pass'}
        if unknown:
            raise BoardGameError('走子参数包含未知字段')
        with self._lock:
            engine = self._engine(key)
            self.last_applied = []
            state = engine.play(payload, auto_reply=auto_reply)
            self.last_applied = list(engine.applied)
            return state

    def reply(self, key):
        with self._lock:
            engine = self._engine(key)
            self.last_applied = []
            state = engine.robot_reply()
            self.last_applied = list(engine.applied)
            return state

    def undo(self, key):
        with self._lock:
            self.last_applied = []
            return self._engine(key).undo()

    def suggest(self, key):
        with self._lock:
            engine = self._engine(key)
            suggestion = engine.suggest()
            state = engine.snapshot()
            state['suggest'] = suggestion
            return state
