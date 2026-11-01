"""棋类 2D 规则 API；接入实体棋盘时按手拆开 2D 记录与机械臂取放。"""
from __future__ import annotations
import copy

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from .application.board_games import BoardGameError, ENGINES, LEVELS, GameStore


def _failure_body(error: Exception):
    why = getattr(error, 'detail', None) or str(error) or type(error).__name__
    return {'ok': False, 'why': str(why)}


def _reject(error: Exception):
    return JSONResponse(_failure_body(error), status_code=200)


def create_game_router(store: GameStore | None = None, board=None):
    games = store or GameStore()
    router = APIRouter(prefix='/api/games')

    def known(game: str):
        if game not in ENGINES:
            raise HTTPException(status_code=404, detail=f'不支持的棋类：{game}')

    def board_moving():
        if board is None:
            return False
        play = board.play
        if board.preparing or getattr(board.world.executor, 'busy', False):
            return True
        if play is not None and (play.busy or play.active or play.held):
            return True
        return board.world.runner.state in ('running', 'waiting', 'paused')

    def physical_state(game):
        return board.status(game) if board else None

    def state_value(game, *, actual=False):
        matching = board is not None and board.game == game
        pending = matching and bool(board.blocked or board.queue or board.current or board.preparing)
        value = board.actual_state() if matching and (board.blocked or actual and pending) else games.snapshot(game)
        if board:
            from .application.board_robot import layout_for
            value['physics_layout'] = layout_for(game)
            value['note'] = '机械臂实体落子'
        return value

    def token(payload):
        if not isinstance(payload, dict):
            raise BoardGameError('棋局参数无效')
        value = payload.get('session_id')
        if value is not None and not isinstance(value, str):
            raise BoardGameError('棋局编号无效')
        return value

    @router.get('')
    @router.get('/')
    def list_games():
        return {'ok': True, 'items': [{'key': key, 'title': ENGINES[key].title,
                                       'rows': ENGINES[key].rows, 'cols': ENGINES[key].cols,
                                       'layout': ENGINES[key].layout} for key in ENGINES],
                'levels': [{'id': key, 'label': label} for key, label in LEVELS.items()]}

    @router.get('/{game}/state')
    def state(game: str):
        known(game)
        try:
            return {'ok': True, 'state': state_value(game, actual=True), 'physical': physical_state(game)}
        except BoardGameError as error:
            return _reject(error)

    @router.get('/{game}/physical')
    def physical(game: str):
        known(game)
        if board is None:
            return {'ok': False, 'why': '实体棋盘未启用'}
        return {'ok': True, 'physical': physical_state(game)}

    @router.post('/{game}/new')
    async def new_game(game: str, payload: dict = Body(default={})):
        known(game)
        if board_moving():
            return _reject(ValueError('机械臂正在执行落子，请先停止再开新局'))
        if board is None:
            try:
                return {'ok': True, 'state': games.new(game, payload), 'physical': None}
            except BoardGameError as error:
                return _reject(error)
        # A failed scene build must not silently replace the existing 2D game.
        built = False
        try:
            with board._lock, games._lock:
                old = copy.deepcopy(games._games.get(game))
                old_applied = copy.deepcopy(games.last_applied)
                games.new(game, payload)
                try:
                    board.ensure(game, force=True)
                    built = True
                except Exception:
                    if old is None:
                        games._games.pop(game, None)
                    else:
                        games._games[game] = old
                    games.last_applied = old_applied
                    raise
            started = await board.start(game)
            if not started.get('ok'):
                return {**started, 'state': state_value(game), 'physical': physical_state(game)}
            return {'ok': True, 'running': bool(started.get('running')), 'state': state_value(game), 'physical': physical_state(game)}
        except Exception as error:
            if built and board.game == game:
                board.fail(str(error))
            return _reject(error)

    @router.post('/{game}/move')
    def move(game: str, payload: dict = Body(...)):
        known(game)
        if board is not None:
            try:
                with board._lock, games._lock:
                    board.require_session(game, token(payload))
                    settled = board.settle()
                    if not settled.get('ok'):
                        raise ValueError(str(settled.get('why') or '上一手尚未完成'))
                    if board.queue:
                        raise ValueError('上一回合尚未完成')
                    old = copy.deepcopy(games._games.get(game))
                    old_applied = copy.deepcopy(games.last_applied)
                    try:
                        games.move(game, {k: v for k, v in payload.items() if k != 'session_id'}, auto_reply=False)
                        board.note(game)
                    except Exception:
                        if old is not None:
                            games._games[game] = old
                        games.last_applied = old_applied
                        raise
            except Exception as error:
                return _reject(error)
            return {'ok': True, 'state': state_value(game), 'physical': physical_state(game)}
        try:
            return {'ok': True, 'state': games.move(game, payload)}
        except BoardGameError as error:
            return _reject(error)

    @router.post('/{game}/step')
    async def step(game: str, payload: dict = Body(default={})):
        known(game)
        if board is None:
            return _reject(ValueError('实体棋盘未启用'))
        try:
            result = await board.step(game, token(payload))
            return {**result, 'ok': bool(result.get('ok', True)),
                    'state': state_value(game), 'physical': physical_state(game)}
        except BoardGameError as error:
            return {**_failure_body(error), 'state': state_value(game, actual=True), 'physical': physical_state(game)}
        except Exception as error:
            return {**_failure_body(error), 'state': state_value(game, actual=True), 'physical': physical_state(game)}

    @router.post('/{game}/abort')
    async def abort(game: str, payload: dict = Body(default={})):
        known(game)
        if board is None:
            return _reject(ValueError('实体棋盘未启用'))
        try:
            result = await board.abort(game, token(payload))
            return {**result, 'state': state_value(game), 'physical': physical_state(game), 'message': board.message}
        except Exception as error:
            return _reject(error)

    @router.post('/{game}/pause')
    async def pause(game: str, payload: dict = Body(default={})):
        known(game)
        if board is None:
            return _reject(ValueError('实体棋盘未启用'))
        try:
            result = await board.pause(game, token(payload))
            return {**result, 'state': state_value(game), 'physical': physical_state(game)}
        except Exception as error:
            return _reject(error)

    @router.post('/{game}/resume')
    async def resume(game: str, payload: dict = Body(default={})):
        known(game)
        if board is None:
            return _reject(ValueError('实体棋盘未启用'))
        try:
            result = await board.resume(game, token(payload))
            return {**result, 'state': state_value(game), 'physical': physical_state(game)}
        except Exception as error:
            return _reject(error)

    @router.post('/{game}/undo')
    def undo(game: str):
        known(game)
        if board is not None:
            return _reject(ValueError('实体对局暂不支持悔棋；请开新局让机械臂重新摆棋'))
        try:
            return {'ok': True, 'state': games.undo(game)}
        except BoardGameError as error:
            return _reject(error)

    @router.post('/{game}/suggest')
    def suggest(game: str):
        known(game)
        try:
            return {'ok': True, 'state': games.suggest(game)}
        except BoardGameError as error:
            return _reject(error)

    router.store = games
    router.board = board
    return router
