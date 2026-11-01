"""实体棋盘：把 2D 走法翻译成机械臂取放，并维护与棋盘同位的场景工件。

机器人正面为基座 -X；观众从 -X 朝 +X 看，屏幕左侧为 +Y。所有棋盘、
托盘与 2D 行列共用 layout_for 契约，前端不得独立猜测位置。这里只做仿真场景
与预检编排，不连接真机、不碰相机，也不替代 2D 规则引擎。
"""
from __future__ import annotations

import math
import asyncio
import copy
import threading
import time
import uuid

from ..core.objects import half_height
from ..core.safety import _object_signed_gap
from ..core import sim_tool
from .board_tools import ensure_board_tool, tool_id_for, wait_pose

BOARD_TOP = 180.0
TRAY_TOP = 160.0
DROP_GAMES = ('ttt', 'gomoku', 'go')

LAYOUTS = {
    'ttt': {'rows': 3, 'cols': 3, 'span': .36, 'points': False},
    'chess': {'rows': 8, 'cols': 8, 'span': .36, 'points': False},
    'gomoku': {'rows': 15, 'cols': 15, 'span': .38, 'points': True},
    'go': {'rows': 15, 'cols': 15, 'span': .38, 'points': True},
    'xiangqi': {'rows': 10, 'cols': 9, 'span': .40, 'points': True},
}
TRAYS = {
    'ttt': {'cols': 3, 'rows': 3, 'pitch': 100, 'size': [330, 330, 4]},
    'gomoku': {'cols': 11, 'rows': 11, 'pitch': 26, 'size': [320, 320, 4]},
    'go': {'cols': 15, 'rows': 15, 'pitch': 23, 'size': [380, 380, 4]},
    'chess': {'cols': 8, 'rows': 4, 'pitch': 45, 'size': [400, 220, 4]},
    'xiangqi': {'cols': 8, 'rows': 4, 'pitch': 45, 'size': [400, 220, 4]},
}
# 供子盘容量：总量不超过盘面格子数，保证一局最多用到的实体子数。
SUPPLY = {'ttt': {'human': 5, 'robot': 4},
          'gomoku': {'human': 60, 'robot': 60},
          'go': {'human': 113, 'robot': 112}}
CHESS_HEIGHT = {'K': 52, 'Q': 48, 'R': 44, 'B': 46, 'N': 46, 'P': 34}
CHESS_COLOR = {'human': '#f1e7d4', 'robot': '#20252c'}


class BoardRobotError(ValueError):
    """实体棋盘拒绝：调用方原样展示。"""


def layout_for(game):
    """Millimetre, base-frame contract; rows go toward the observer, columns right."""
    spec, tray = LAYOUTS[game], TRAYS[game]
    cell = spec['span'] * 1000 / max(spec['rows'], spec['cols'])
    rows, cols = spec['rows'], spec['cols']
    extra = .2 if spec['points'] else 0
    # Border is outside the playing grid; tray coordinates leave space for rims.
    depth = (rows + extra) * cell + 24
    width = (cols + extra) * cell + 24
    drop = game in DROP_GAMES
    return {'game': game, 'center_mm': [-500., 260. if drop else 0., BOARD_TOP],
            'axis_col': [0., -1., 0.], 'axis_row': [-1., 0., 0.],
            'span_mm': spec['span'] * 1000, 'rows': rows, 'cols': cols,
            'points': spec['points'], 'board_top_mm': BOARD_TOP,
            'board_size_mm': [depth, width, BOARD_TOP],
            'tray_center_mm': [-500. if drop else -480., -260. if drop else -420., TRAY_TOP],
            'tray_top_mm': TRAY_TOP, 'tray_size_mm': [tray['size'][1], tray['size'][0], TRAY_TOP],
            'tray_cols': tray['cols'], 'tray_rows': tray['rows'], 'tray_pitch_mm': tray['pitch'],
            'rim_height_mm': 8., 'rim_width_mm': 6.}


def cell_center(game, r, c):
    layout = layout_for(game)
    rows, cols = layout['rows'], layout['cols']
    cell = layout['span_mm'] / max(rows, cols)
    along_col = (c - (cols - 1) / 2) * cell
    along_row = (r - (rows - 1) / 2) * cell
    return tuple(layout['center_mm'][i] + layout['axis_col'][i] * along_col
                 + layout['axis_row'][i] * along_row for i in range(2))


def tray_center(game, index):
    tray = TRAYS[game]
    cols = tray['cols']
    col, row = index % cols, index // cols
    layout = layout_for(game)
    x = layout['tray_center_mm'][0] - (row - (tray['rows'] - 1) / 2) * tray['pitch']
    y = layout['tray_center_mm'][1] - (col - (cols - 1) / 2) * tray['pitch']
    return x, y


def cell_index(game, r, c):
    return r * LAYOUTS[game]['cols'] + c


def piece_shape(game, code, side):
    """实体棋子只作为可抓取代理；外观由 3D 棋层绘制。"""
    if game == 'chess':
        return {'kind': 'cylinder', 'size': [14, 14, CHESS_HEIGHT.get(code.upper(), 40)],
                'color': CHESS_COLOR[side], 'preset': 'cylinder', 'surface': 'plastic'}
    if game == 'xiangqi':
        # 30mm discs leave 10mm between neighbours on a 40mm grid, enough
        # for the real 6.35mm Robotiq pad and 3mm pre-open clearance.
        return {'kind': 'cylinder', 'size': [15, 15, 7], 'color': '#dcc9a1',
                'preset': 'cylinder', 'surface': 'wood'}
    if game == 'ttt':
        preset = 'token_o' if code == 'O' else 'token_x'
        return {'kind': 'box', 'size': [82, 82, 18],
                'color': '#30cadb' if code == 'O' else '#e0a33b',
                'preset': preset, 'surface': 'plastic'}
    stone = {'kind': 'cylinder', 'size': [10, 10, 8] if game == 'go' else [11, 11, 8],
             'color': '#171b1f' if code == 'black' else '#e9e4d8',
             'preset': 'cylinder', 'surface': 'wood'}
    return stone


def vacuum_yaw_candidates(descriptor, piece, place, objects):
    """Check real double-cup footprint at both contacts, before any IK work.

    Keep one yaw throughout a carry. Both cups and the connecting plate remain
    solid; only the selected piece is an allowed contact. Full robot/path checks
    still run after this cheap geometric selection.
    """
    d = descriptor['dimensions']
    distance = d['length'] - 2 * d['edge']
    results = []
    for yaw in (0., 45., 315., 90., 270., 135., 225., 180.):
        cosine, sine = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        gap = math.inf
        for xyz in (piece['xyz'], place):
            z = xyz[2] + half_height(piece)
            second = [xyz[0] + distance * cosine, xyz[1] + distance * sine]
            caps = [{'a': [xy[0], xy[1], z], 'b': [xy[0], xy[1], z+d['cupHeight']],
                     'r_mm': d['cupDiameter']/2} for xy in (xyz, second)]
            plate_z = z + d['cupHeight'] + d['thickness']/2
            caps.append({'a': [xyz[0]-d['edge']*cosine, xyz[1]-d['edge']*sine, plate_z],
                         'b': [second[0]+d['edge']*cosine, second[1]+d['edge']*sine, plate_z],
                         'r_mm': math.hypot(d['width']/2, d['thickness']/2)})
            for other in objects:
                if other['id'] == piece['id'] or not other.get('collide', True):
                    continue
                for cap in caps:
                    gap = min(gap, _object_signed_gap(cap, other))
                    if gap < .1:
                        break
                if gap < .1:
                    break
            if gap < .1:
                break
        if gap >= .1:
            results.append(yaw)
    return results


class BoardExecutor:
    """管理一个实体棋盘场景；同一时刻只保留当前棋种。"""

    def __init__(self, world, games, play=None):
        self.world = world
        self.games = games
        self.play = play
        self._lock = threading.RLock()
        self.game = None
        self.group = None
        self.queue = []
        self.current = None
        self.message = ''
        self.blocked = None
        self.active = False
        self.paused = False
        self.epoch = 0
        self.preparing = False
        self.ready_pose = None
        self.ready_pending = False
        self.confirmed_state = None

    # ---------------------------------------------------------------- 场景构建
    def _ensure_idle(self):
        play = self.play
        if play is not None and (play.busy or play.active or play.held):
            raise BoardRobotError('机械臂正在执行，暂时不能重建实体棋盘')
        if self.world.runner.state in ('running', 'waiting', 'paused'):
            raise BoardRobotError('程序正在运行，暂时不能重建实体棋盘')
        if getattr(self.world.executor, 'busy', False) or self.preparing:
            raise BoardRobotError('机械臂正在执行，请先终止当前任务')

    def _clear(self, game, force=False):
        self._ensure_idle()
        if not force and self.game == game and self.group:
            return
        snapshot = self.games.snapshot(game)
        layout = layout_for(game)
        group = uuid.uuid4().hex
        items = []
        tray = TRAYS[game]

        def support(code, name, center, size):
            items.append({'id': uuid.uuid4().hex[:12], 'name': name, 'kind': 'box', 'preset': 'box',
                          'size': size, 'xyz': center, 'color': '#313a43', 'surface': 'plastic',
                          'gravity': False, 'floating': True, 'floor': True, 'collide': True,
                          'task': {'group': group, 'mode': 'board', 'role': 'obstacle', 'symbol': '',
                                   'slot': -1, 'home': 0, 'game': game, 'code': code, 'side': ''}})

        bc, tc = layout['center_mm'], layout['tray_center_mm']
        support('board', '棋盘', [bc[0], bc[1], BOARD_TOP / 2], layout['board_size_mm'])
        support('tray', '供子/提子盘', [tc[0], tc[1], TRAY_TOP / 2], layout['tray_size_mm'])
        dx, dy, _ = layout['tray_size_mm']
        rim, rh = layout['rim_width_mm'], layout['rim_height_mm']
        for sign in (-1, 1):
            support('tray-rim', '托盘边沿', [tc[0] + sign * (dx-rim)/2, tc[1], TRAY_TOP+rh/2], [rim, dy, rh])
            support('tray-rim', '托盘边沿', [tc[0], tc[1] + sign * (dy-rim)/2, TRAY_TOP+rh/2], [dx-2*rim, rim, rh])
        home = [0]

        def add_piece(code, side, slot, xyz):
            shape = piece_shape(game, code, side)
            height = half_height(shape)
            items.append({
                'id': uuid.uuid4().hex[:12], 'name': f'{game}-{code}-{side}', 'kind': shape['kind'],
                'preset': shape['preset'], 'size': shape['size'], 'color': shape['color'],
                'surface': shape['surface'], 'xyz': [xyz[0], xyz[1], xyz[2] + height],
                'gravity': False, 'floating': True, 'floor': True, 'collide': True,
                'task': {'group': group, 'mode': 'board', 'role': 'piece', 'symbol': '', 'slot': slot,
                         'home': home[0], 'game': game, 'code': code, 'side': side},
            })
            home[0] += 1

        for cell in snapshot.get('cells') or []:
            r, c = cell['r'], cell['c']
            code = cell['piece']['code']
            side = cell['piece']['side']
            x, y = cell_center(game, r, c)
            add_piece(code, side, cell_index(game, r, c), (x, y, BOARD_TOP))
        if game in DROP_GAMES:
            on_board = {}
            for cell in snapshot.get('cells') or []:
                key = (cell['piece']['code'], cell['piece']['side'])
                on_board[key] = on_board.get(key, 0) + 1
            capacity = tray['cols'] * tray['rows']
            default_code = {'ttt': {'human': 'O', 'robot': 'X'},
                            'gomoku': {'human': 'black', 'robot': 'white'},
                            'go': {'human': 'black', 'robot': 'white'}}[game]
            for side in ('human', 'robot'):
                code = next((cell['piece']['code'] for cell in (snapshot.get('cells') or [])
                             if cell['piece']['side'] == side), default_code[side])
                need = max(0, SUPPLY[game][side] - on_board.get((code, side), 0))
                for _ in range(need):
                    if home[0] >= capacity:
                        break
                    x, y = tray_center(game, home[0])
                    add_piece(code, side, -1, (x, y, TRAY_TOP))
        clean = []
        for item in self.world.objects.list():
            if item.get('task', {}).get('mode') != 'board':
                clean.append(item)
        store = self.world.objects
        # Same placement/contact checks as user scene edits; never silently
        # replace a colliding scene or publish a partially built board.
        store.replace([*clean, *items], validate_all=False)
        self.game = game
        self.group = group
        self.queue = []
        self.current = None
        self.epoch += 1
        self.active = bool(force)
        self.paused = False
        self.preparing = False
        self.ready_pose = None
        self.ready_pending = bool(force)
        self.blocked = None
        self.confirmed_state = copy.deepcopy(snapshot)
        self.message = '棋盘已就绪'

    def ensure(self, game, force=False):
        with self._lock:
            self._ensure_idle()
            if force or self.game != game or not self.group:
                ensure_board_tool(self.world, game)
            self._clear(game, force)
            return self.status()

    # ---------------------------------------------------------------- 动作生成
    def _members(self):
        return [p for p in self.world.objects.list()
                if p.get('task', {}).get('group') == self.group and p['task']['role'] == 'piece']

    def _at(self, members, game, r, c):
        slot = cell_index(game, r, c)
        return next((p for p in members if p['task']['slot'] == slot), None)

    def _supply(self, members, code, side):
        return next((p for p in sorted(members, key=lambda p: p['task']['home'])
                     if p['task']['slot'] == -1 and p['task']['code'] == code
                     and p['task']['side'] == side), None)

    def _tray_slot(self, piece, game):
        return -1 if game in DROP_GAMES else -2

    def _place_for(self, game, piece, slot):
        if slot >= 0:
            r, c = divmod(slot, LAYOUTS[game]['cols'])
            x, y = cell_center(game, r, c)
            return [x, y, BOARD_TOP + half_height(piece)]
        x, y = tray_center(game, piece['task']['home'])
        return [x, y, TRAY_TOP + half_height(piece)]

    def _item(self, game, piece, slot, *, code=None, label='', side=None):
        return {
            'object_id': piece['id'], 'code': code or piece['task']['code'],
            'side': side or piece['task']['side'],
            'from': self._cell_of(piece, game), 'to': self._cell_of_slot(game, slot),
            'slot': slot, 'place': self._place_for(game, piece, slot), 'label': label,
        }

    @staticmethod
    def _cell_of_slot(game, slot):
        cols = LAYOUTS[game]['cols']
        return [slot // cols, slot % cols] if slot >= 0 else None

    def _cell_of(self, piece, game):
        slot = piece['task']['slot']
        if slot < 0:
            return None
        cols = LAYOUTS[game]['cols']
        return [slot // cols, slot % cols]

    def _actions(self, game, move):
        members = self._members()
        actions = []
        captures = move.get('captured') or []
        if isinstance(captures, dict):
            captures = [captures]
        for cap in captures:
            piece = self._at(members, game, cap['to'][0], cap['to'][1])
            if piece is None:
                raise BoardRobotError('实体棋子与 2D 局面不一致，请开新局重建')
            actions.append({'kind': 'capture', 'game': game, 'label': f'提子 {piece["task"]["code"]}',
                            'items': [self._item(game, piece, self._tray_slot(piece, game), label='提子')]})
        if move.get('pass') or move.get('to') is None:
            return actions
        to = move['to']
        if game in DROP_GAMES:
            piece = self._supply(members, move['code'], move['side'])
            if piece is None:
                raise BoardRobotError('供子盘已没有可用棋子，请开新局')
        else:
            piece = self._at(members, game, move['from'][0], move['from'][1])
            if piece is None:
                raise BoardRobotError('实体棋盘上找不到这枚棋子，请开新局重建')
        final_code = piece['task']['code']
        if move.get('promotion'):
            final_code = move['promotion'] if move['side'] == 'human' else move['promotion'].lower()
        actions.append({'kind': 'move', 'game': game,
                        'label': f'{piece["task"]["code"]} → {move["to"]}',
                        'items': [self._item(game, piece, cell_index(game, to[0], to[1]),
                                             code=final_code, side=move['side'], label='落子')]})
        castle = move.get('castle')
        if castle:
            back = to[0]
            rook_from = [back, 7 if castle in ('K', 'k') else 0]
            rook_to = [back, 5 if castle in ('K', 'k') else 3]
            rook = self._at(members, game, rook_from[0], rook_from[1])
            if rook is None:
                raise BoardRobotError('易位的车不在实体棋盘上，请开新局重建')
            actions.append({'kind': 'rook', 'game': game, 'label': '易位走车',
                            'items': [self._item(game, rook, cell_index(game, rook_to[0], rook_to[1])), ]})
        return actions

    def note(self, game):
        with self._lock:
            if self.game != game:
                raise BoardRobotError('实体棋盘尚未切换到当前棋种')
            actions = []
            for move in self.games.last_applied or []:
                actions.extend(self._actions(game, move))
            self.queue.extend(actions)
            return {'queued': len(self.queue), 'actions': actions}

    # ---------------------------------------------------------------- 执行
    def _wait_pose(self, piece, clearance=50):
        pose = wait_pose(self.world, piece, clearance)
        if not all(math.isfinite(v) for v in pose):
            raise BoardRobotError('当前工具尺寸无效，无法生成落子等待位')
        return pose

    def status(self, game=None):
        with self._lock:
            return self._status_unlocked(game)

    def _status_unlocked(self, game=None):
        play = self.play
        state = play.state() if play is not None else {}
        matching = game is None or game == self.game
        lifecycle = 'failed' if self.blocked else 'paused' if self.paused else 'active' if self.active else 'stopped'
        result = {'game': self.game, 'group': self.group, 'session_id': self.group,
                'active': self.active if matching else False, 'paused': self.paused if matching else False,
                'lifecycle': lifecycle if matching else 'stopped', 'message': self.message if matching else '',
                'queued': len(self.queue) if matching else 0,
                'current': self.current['label'] if matching and self.current else None,
                'running': matching and (bool(self.current) or bool(state.get('active'))),
                'planning': matching and (self.preparing or bool(state.get('planning'))),
                'held': state.get('held') if matching else None,
                'execution': state.get('execution') if matching else None}
        result['physical_pending'] = matching and bool(self.blocked or self.queue or self.current or self.preparing)
        result['confirmed_state'] = copy.deepcopy(self.confirmed_state) if matching else None
        result['actual_state'] = self.actual_state() if matching and self.game else None
        if game or self.game:
            result['layout'] = layout_for(game or self.game)
            result['layout']['wait_pose'] = self.ready_pose
        return result

    def actual_state(self):
        """Read actual released pieces, never the requested-but-unexecuted move."""
        requested = self.games.snapshot(self.game)
        snapshot = copy.deepcopy(self.confirmed_state) if self.confirmed_state else copy.deepcopy(requested)
        glyphs = {cell['piece']['code']: cell['piece'].get('glyph', '')
                  for cell in [*(requested.get('cells') or []), *(snapshot.get('cells') or [])]}
        held_id = self.play.held['id'] if self.play and self.play.held else None
        cells = []
        for piece in self._members():
            slot = piece['task']['slot']
            if slot < 0 or piece['id'] == held_id:
                continue
            r, c = divmod(slot, LAYOUTS[self.game]['cols'])
            expected = [*cell_center(self.game, r, c), BOARD_TOP + half_height(piece)]
            if math.dist(piece['xyz'], expected) > 1.:
                continue
            code = piece['task']['code']
            cells.append({'r': r, 'c': c, 'piece': {'code': code, 'side': piece['task']['side'],
                                                  'glyph': glyphs.get(code, code if self.game == 'ttt' else '')}})
        snapshot['cells'] = cells
        snapshot['physics_layout'] = layout_for(self.game)
        if self.blocked:
            snapshot.update(over=False, winner=None, status='落子未完成', turn_label='落子未完成',
                            can_undo=False, legal=[], moves=[], suggest=None)
        return snapshot

    def fail(self, message):
        with self._lock:
            self.blocked = str(message)
            self.active = False
            self.paused = False
            self.preparing = False
            self.ready_pending = False
            self.current = None
            self.queue = []
            self.epoch += 1
            self.message = '落子未完成，请重新开始游戏'

    def require_session(self, game, session_id=None, *, allow_paused=False, allow_inactive=False):
        if self.game != game or not self.active and not allow_inactive:
            raise BoardRobotError('请先开始游戏')
        if session_id is not None and session_id != self.group:
            raise BoardRobotError('棋局已更换，请重新操作')
        if self.paused and not allow_paused:
            raise BoardRobotError('游戏已暂停')

    async def abort(self, game, session_id=None):
        with self._lock:
            self.require_session(game, session_id, allow_paused=True, allow_inactive=True)
            owns_motion = self.current is not None
            self.epoch += 1
            self.active = False
            self.paused = False
            self.preparing = False
            self.ready_pending = False
            self.queue = []
            self.current = None
            play = self.play
        if play is not None and (play.active or play.busy or play.held):
            play.stop()
        if owns_motion:
            result = await self.world.stop_execution('棋局已终止')
            if not result.get('ok'):
                self.message = '停止尚未确认'
                return result
        self.message = '游戏已终止'
        return {'ok': True}

    async def pause(self, game, session_id=None):
        with self._lock:
            self.require_session(game, session_id, allow_paused=True)
            if self.paused:
                return {'ok': True}
            self.paused = True
            moving = self.current is not None and self.world.runner.state in ('running', 'waiting')
        result = await self.world.pause_program() if moving else {'ok': True}
        self.message = '游戏已暂停' if result.get('ok') else str(result.get('why') or '暂停失败')
        return result

    async def resume(self, game, session_id=None):
        with self._lock:
            self.require_session(game, session_id, allow_paused=True)
            if not self.paused:
                return {'ok': True}
            moving = self.current is not None and self.world.runner.state == 'paused'
        result = await self.world.resume_program() if moving else {'ok': True}
        if result.get('ok'):
            self.paused = False
            self.message = '游戏继续'
            if self.ready_pending and not self.current and not self.preparing:
                return await self.start(game)
        return result

    async def start(self, game):
        """Move once to a game-specific clear waiting pose using the shared Runner."""
        with self._lock:
            self.require_session(game)
            epoch, group = self.epoch, self.group
            self.preparing = True
            layout = layout_for(game)
            piece = {'kind': 'cylinder', 'size': [14., 14., 8.],
                     'xyz': [layout['center_mm'][0] + 40, layout['center_mm'][1], BOARD_TOP + 4.]}
            pose = self._wait_pose(piece, 190.)
            model = copy.copy(self.world.motion_model())
            lo, hi = self.world.limits()
            start = list(self.world.current_joints())
            revision = self.world._cfg_rev
            generation = self.world.objects.persisted_generation
        try:
            def solve():
                for seed in (start, [180., -45., 90., 0., 45., 0.], [180., 45., -90., 0., 90., 0.]):
                    q, _, _, ok = model.ik(seed, pose, lo, hi, iters=160, max_restarts=2)
                    if ok:
                        return q
                raise BoardRobotError('起点不可达，请检查工具与棋盘位置')
            q = await asyncio.to_thread(solve)
            with self._lock:
                if epoch != self.epoch or group != self.group or not self.active:
                    return {'ok': False, 'why': '棋局已终止'}
                if (self.world.settings.get('mode') != 'sim' or self.world.real is not None
                        or revision != self.world._cfg_rev or generation != self.world.objects.persisted_generation
                        or any(abs(a-b) > .05 for a, b in zip(start, self.world.current_joints()))):
                    raise BoardRobotError('起点或场景已变化，请重新开始游戏')
                self.ready_pose = pose
                if self.paused:
                    return {'ok': True, 'paused': True, 'running': False}
                self.current = {'kind': 'ready', 'label': '回到起点', 'game': game}
            loaded = await self.world.load_program('MOVEJ ' + ','.join(str(v) for v in q))
            if not loaded.get('ok'):
                raise BoardRobotError(loaded.get('why') or loaded.get('error') or '起点载入失败')
            with self._lock:
                if epoch != self.epoch or group != self.group or not self.active:
                    return {'ok': False, 'why': '棋局已终止'}
                result = self.world.start_program()
                if not result.get('ok'):
                    raise BoardRobotError(result.get('why') or '起点启动失败')
                self.message = '回到起点'
                self.ready_pending = False
            return {'ok': True, 'running': True}
        finally:
            with self._lock:
                if epoch == self.epoch:
                    self.preparing = False

    def settle(self):
        """收起已经结束但尚未被 /step 确认的上一手，避免刷新后一直判忙。"""
        with self._lock:
            if not self.current:
                if self.preparing:
                    return {'ok': False, 'why': '正在准备落子'}
                return {'ok': True}
            play = self.play
            if play is not None and (play.active or play.busy or play.held):
                return {'ok': False, 'why': '机械臂正在执行上一手'}
            state = self.world.runner.state
            self.current = None
            if state == 'finished':
                if not self.queue:
                    self.confirmed_state = self.games.snapshot(self.game)
                return {'ok': True}
            self.fail(state)
            self.message = '上一手实体执行未完成'
            return {'ok': False, 'why': f'上一手实体执行未完成（{state}），请开新局重新摆棋'}

    async def step(self, game, session_id=None):
        with self._lock:
            self.require_session(game, session_id, allow_paused=True)
            if self.paused:
                return {'ok': True, 'running': bool(self.current), 'paused': True,
                        'done': False, 'state': self.games.snapshot(game), 'physical': self.status(game)}
            play = self.play
            if play is None:
                raise BoardRobotError('机器人取放服务未启动，不能执行实体落子')
            if self.current:
                state = self.world.runner.state
                if play.active or play.busy or play.held or state in ('running', 'waiting', 'paused'):
                    return {'ok': True, 'running': True, 'done': False,
                            'action': self.current, 'state': self.games.snapshot(game),
                            'remaining': len(self.queue), 'message': '机械臂正在执行上一手'}
                if state != 'finished':
                    self.fail(state)
                    raise BoardRobotError(f'实体取放未完成（{state}），2D 已记录；请重试本手或开新局')
                self.current = None
                if not self.queue:
                    self.confirmed_state = self.games.snapshot(game)
            if self.preparing:
                return {'ok': True, 'running': True, 'planning': True, 'done': False,
                        'state': self.games.snapshot(game), 'remaining': len(self.queue)}
            if not self.queue:
                snapshot = self.games.snapshot(game)
                if snapshot['over'] or snapshot['turn_owner'] != 'robot':
                    if snapshot['over']:
                        self.active = False
                        self.message = snapshot['status']
                    return {'ok': True, 'done': True, 'running': False, 'state': snapshot,
                            'remaining': 0, 'message': '本回合结束'}
                with self.games._lock:
                    old = copy.deepcopy(self.games._games[game])
                    old_applied = copy.deepcopy(self.games.last_applied)
                    try:
                        self.games.reply(game)
                        reply_actions = []
                        for move in self.games.last_applied or []:
                            reply_actions.extend(self._actions(game, move))
                    except Exception:
                        self.games._games[game] = old
                        self.games.last_applied = old_applied
                        raise
                    self.queue.extend(reply_actions)
                if not self.queue:
                    return {'ok': True, 'done': True, 'running': False,
                            'state': self.games.snapshot(game), 'remaining': 0, 'message': '本回合结束'}
            action = self.queue[0]
            values = []
            for item in action['items']:
                piece = next((p for p in self.world.objects.list() if p['id'] == item['object_id']), None)
                if piece is None:
                    raise BoardRobotError('实体棋子已不存在，请开新局重建')
                values.append((piece, item['place'], item['slot'], {'code': item['code']}))
            group = self.group
            epoch = self.epoch
            self.preparing = True
            # Keep the carried piece above all board/tray pieces, including kings.
            tallest = max((p['xyz'][2] + half_height(p) for p in self._members()), default=BOARD_TOP)
            pick_top = values[0][0]['xyz'][2] + half_height(values[0][0])
            minimum_clearance = max(35., tallest - min(pick_top, values[0][1][2] + half_height(values[0][0])) + 35.)
            clearance = max(70., minimum_clearance)
            payload = {'group': group, 'action': 'board', 'clearance': clearance,
                       'tool_id': tool_id_for(game),
                       'wait': self.ready_pose or self._wait_pose(values[0][0], clearance),
                       'trim_return': True, 'keep_final_wait': len(self.queue) == 1}
        try:
            ensure_board_tool(self.world, game)
            if game in DROP_GAMES:
                from .board_tools import probe_vacuum_yaw
                descriptor = sim_tool.normalize(self.world.settings.get('sim_tool', sim_tool.DEFAULT_TOOL))
                options = vacuum_yaw_candidates(descriptor, values[0][0], values[0][1], self.world.objects.list())
                if not options:
                    raise BoardRobotError('吸盘另一端没有可用空间，无法完成本手取放')
                deadline = time.monotonic() + 30.
                reasons = []
                for yaw in options:
                    if epoch != self.epoch or not self.active:
                        raise BoardRobotError('棋局已终止')
                    verdict = await asyncio.to_thread(probe_vacuum_yaw, self.world, values[0][0], values[0][1],
                                                     yaw, clearance, cancelled=lambda: epoch != self.epoch or not self.active,
                                                     deadline=deadline)
                    if verdict.get('ok'):
                        payload['vacuum_yaw'] = yaw
                        break
                    reasons.append(str(verdict.get('why') or '取放点不可达'))
                else:
                    raise BoardRobotError('当前吸盘姿态无法避开棋盘棋子：' + (reasons[-1] if reasons else '请重新开始游戏'))
            else:
                from .board_tools import probe_gripper_yaw
                # Turn the actual fingers in free space above the pieces, not
                # while descending through a crowded back rank.
                deadline=time.monotonic()+20.
                reasons=[];selected=False
                heights=(clearance,) if minimum_clearance>=clearance-.01 else (clearance,minimum_clearance)
                for yaw in (90.,0.,45.,135.,270.,180.,315.,225.):
                    for height in heights:
                        if epoch!=self.epoch or not self.active:
                            raise BoardRobotError('棋局已终止')
                        # A fixed extra 70mm can overextend at a distant tray.
                        # The alternative retains the existing highest-piece
                        # plus 35mm floor; it never lowers the collision gate.
                        verdict=await asyncio.to_thread(probe_gripper_yaw,self.world,values[0][0],values[0][1],
                            yaw,height,cancelled=lambda:epoch!=self.epoch or not self.active,deadline=deadline)
                        if verdict.get('ok'):
                            payload['gripper_yaw']=yaw
                            payload['clearance']=height
                            selected=True
                            break
                        reasons.append(str(verdict.get('why') or '取放点不可达'))
                    if selected:break
                if not selected:
                    raise BoardRobotError('当前夹爪姿态无法避开邻子：'+(reasons[-1] if reasons else '无可用朝向'))
            if epoch != self.epoch or not self.active:
                raise BoardRobotError('棋局已终止')
            generated = await play.generate(payload, bound_targets=values)
        except Exception as exc:
            with self._lock:
                if epoch == self.epoch:
                    self.fail(str(getattr(exc, 'detail', None) or exc))
            detail = getattr(exc, 'detail', None) or str(exc) or type(exc).__name__
            raise BoardRobotError(f'实体落子预检失败：{detail}') from exc
        source = str(generated.get('source') or '')
        with self._lock:
            if epoch == self.epoch:
                self.preparing = False
            if epoch != self.epoch or group != self.group or not self.active:
                play.plans.pop(generated.get('plan_id'), None)
                raise BoardRobotError('棋局已终止')
            if not source.strip():
                self.fail('实体落子程序为空')
                raise BoardRobotError('实体落子程序为空，已停止')
            if self.paused:
                play.plans.pop(generated.get('plan_id'), None)
                return {'ok': True, 'paused': True, 'done': False, 'running': False,
                        'state': self.games.snapshot(game), 'physical': self.status(game)}
            try:
                started = play.run({'plan_id': generated['plan_id'], 'source': source})
            except Exception as exc:
                self.fail(str(getattr(exc, 'detail', None) or exc))
                raise
            if not started.get('ok'):
                self.fail(str(started.get('why') or '机械臂未能启动'))
                raise BoardRobotError(str(started.get('why') or '机械臂未能启动'))
            self.current = action
            self.queue.pop(0)
            self.message = action['label']
            return {'ok': True, 'running': True, 'done': False, 'action': action,
                    'state': self.games.snapshot(game), 'remaining': len(self.queue),
                    'message': action['label']}
