"""Opt-in simulation debugger. Integration: app.include_router(create_debug_router(world)).

Import this factory before the static catch-all; it never imports/constructs World.
"""
import secrets
import time
from copy import deepcopy

from fastapi import APIRouter, Body, HTTPException
from .core.program import parse, ProgramError


def create_debug_router(world):
    router = APIRouter(prefix='/api/program/debug', tags=['simulation-debug'])
    session = None
    owned_runner = None

    def require_sim():
        if world.settings.get('mode') != 'sim' or world.real is not None:
            raise HTTPException(409, '程序调试仅限未连接真机的仿真')

    def current(payload=None):
        require_sim()
        if (owned_runner is not world.runner or not world.runner.debug_enabled
                or session is None or (payload is not None and payload.get('session') != session)):
            raise HTTPException(409, '调试会话不存在或已失效，请重新载入')
        return world.runner

    def response():
        return {'ok': True, 'session': session, **world.runner.debug_snapshot()}

    def breakpoints(payload):
        lines = payload.get('breakpoints', [])
        if (type(lines) is not list or len(lines) > 200
                or any(type(n) is not int or not 1 <= n <= 200000 for n in lines)):
            raise HTTPException(422, '断点需为最多200个正整数行号')
        return set(lines)

    @router.get('')
    async def status():
        require_sim()
        if owned_runner is not world.runner or not world.runner.debug_enabled:
            return {'ok': True, 'enabled': False, 'state': 'idle', 'variables': []}
        return response()

    @router.post('/load')
    async def load(payload: dict = Body(...)):
        nonlocal session, owned_runner
        require_sim()
        w = world
        if (w.executor.busy or w.runner.state in ('running', 'waiting', 'paused')
                or w.sim.traj is not None or w.sim.jog is not None or w.hand_follow.active
                or w.queue_state in ('running', 'paused') or w.free.get('on')):
            raise HTTPException(409, '请先停止当前程序、运动、跟随或手拖')
        if not w._need(w.MOTION, '载入仿真调试'):
            raise HTTPException(409, w.last_deny)
        lines = breakpoints(payload)
        try:
            steps = parse(payload.get('src', ''), w.resolve_point,
                          params=payload.get('params'), point_items=w.point_items())
        except ProgramError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not steps:
            raise HTTPException(422, '程序为空')
        # SPEED uses World.set_speed(), which increments the same revision as
        # safety/tool edits. Ignore only sim speed, retaining every other setting.
        settings = deepcopy({k: v for k, v in w.settings.items() if k != 'sim_speed_pct'})
        context = deepcopy(w.plan_context())
        def debug_guard():
            if w.settings.get('mode') != 'sim' or w.real is not None:
                return False
            # Scene/point/tool frame edits need not increment _cfg_rev. Check
            # both snapshots every time, never infer a speed edit from revision.
            return ({k: v for k, v in w.settings.items() if k != 'sim_speed_pct'} == settings
                    and w.plan_context() == context)
        w.runner.load(steps)
        w.runner.debug_guard = debug_guard
        w.runner.debug_breakpoints = lines
        if not w.runner.start(debug=True):
            raise HTTPException(409, '程序无法启动调试')
        # No advance here: loading must never execute even an assignment or DI.
        w.runner.pause()
        w.runner.debug_reason = 'entry'
        w.runner.debug_line = 1 if w.runner.script_spec else steps[0]['line']
        session = secrets.token_urlsafe(24)
        owned_runner = w.runner
        return response()

    @router.post('/breakpoints')
    async def set_breakpoints(payload: dict = Body(...)):
        runner = current(payload)
        runner.debug_breakpoints = breakpoints(payload)
        return response()

    @router.post('/{action}')
    async def control(action: str, payload: dict = Body(...)):
        runner = current(payload)
        if action == 'stop':
            # Synchronous sim-only cancellation: no await window may turn this
            # endpoint into World.stop_execution's real controller stop path.
            world.executor.revoke('中止仿真调试')
            runner.stop('中止仿真调试')
            world.sim.stop()
            world.queue_state = 'aborted'
            return response()
        if action not in ('step', 'continue', 'pause'):
            raise HTTPException(404, '未知调试操作')
        if not runner.debug_guard():
            raise HTTPException(409, '调试配置已变化，请中止后重新载入')
        if action == 'pause':
            # Request a boundary pause; never freeze an accepted motion/WAIT.
            if runner.state not in ('running', 'waiting'):
                raise HTTPException(409, '程序未运行')
            runner.debug_single = True
            runner.debug_reason = 'pause_pending'
        else:
            if world.executor.busy or world.sim.traj is not None or world.sim.jog is not None:
                raise HTTPException(409, '当前运动尚未完成')
            if not world._need(world.MOTION, '仿真调试推进') or world.safety['level'] in ('red', 'alarm'):
                raise HTTPException(409, world.last_deny or '安全状态不允许推进')
            if not runner.debug_continue(single=action == 'step', now=time.monotonic()):
                raise HTTPException(409, '请先在调试边界暂停')
            world.sim.paused = False
        return response()

    return router
