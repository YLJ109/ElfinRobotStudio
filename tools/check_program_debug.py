"""Pure runner/executor and fake API checks. Never import World or backend.main."""
import sys
import unittest
import ast
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.program import Runner, parse
from backend.core.motion import MotionExecutor
from backend.debug_routes import create_debug_router
from fastapi import HTTPException


def immediate(coroutine):
    """These route handlers are deliberately atomic: no event loop or sockets."""
    try:
        unexpected = coroutine.send(None)
        raise AssertionError(f'Unexpected asynchronous boundary: {unexpected}')
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()


class RouteClient:
    def __init__(self, router): self.router = router
    def get(self, path): return self.request('GET', path)
    def post(self, path, json): return self.request('POST', path, json)
    def request(self, method, path, payload=None):
        for route in self.router.routes:
            if method not in route.methods: continue
            if route.path == path:
                args = {} if payload is None else {'payload': payload}
            elif route.path.endswith('/{action}') and path.startswith(route.path[:-8]):
                args = {'action': path.rsplit('/', 1)[1], 'payload': payload}
            else: continue
            try:
                value = immediate(route.endpoint(**args))
                return SimpleNamespace(status_code=200, text=str(value), json=lambda: value)
            except HTTPException as error:
                return SimpleNamespace(status_code=error.status_code, text=str(error.detail))
        raise AssertionError('Missing route: ' + path)


def runner(source, breaks=()):
    r = Runner()
    r.load(parse(source, lambda _: None))
    r.debug_guard = lambda: True
    r.debug_breakpoints = set(breaks)
    assert r.start(debug=True)
    r.pause(0)
    return r


class RunnerTests(unittest.TestCase):
    def test_default_movej_movel_parse_and_dispatch_are_unchanged(self):
        source = 'MOVEJ 1,2,3,4,5,6\nMOVEL 100,200,300,0,90,0'
        steps = parse(source, lambda _: None)
        self.assertEqual([step['kind'] for step in steps], ['movej', 'movel'])
        self.assertEqual(steps[0]['joints'], [1,2,3,4,5,6])
        self.assertEqual(steps[1]['pose'], [100,200,300,0,90,0])
        r = Runner(); r.load(steps)
        r.debug_guard = lambda: False
        r.debug_breakpoints = {1, 2}
        self.assertTrue(r.start())
        self.assertIs(r.advance(0), steps[0]); r.complete()
        self.assertIs(r.advance(1), steps[1]); r.complete()
        self.assertIsNone(r.advance(2)); self.assertEqual(r.state, 'finished')

    def test_accepted_motion_pauses_only_after_executor_completion(self):
        r = runner('MOVEJ 1,2,3,4,5,6\nSPEED 20')
        executor = MotionExecutor(lambda *args: None)
        class Settlement:
            def __await__(self):
                yield 'accepted-motion-not-settled'
        issued = []
        async def motion(epoch):
            issued.append('movej')
            await Settlement()
        captured = []
        def schedule(coro):
            captured.append(coro)
            return SimpleNamespace(done=lambda: False)
        r.debug_continue(single=True, now=0)
        self.assertEqual(r.advance(0)['kind'], 'movej')
        with patch('backend.core.motion.asyncio.create_task', schedule):
            executor.submit('move', motion, r.complete)
        execution = captured[0]
        try:
            self.assertEqual(execution.send(None), 'accepted-motion-not-settled')
            for now in (1, 2, 3):
                r.busy = executor.busy
                self.assertIsNone(r.advance(now))
                self.assertEqual(r.state, 'running')
            with self.assertRaises(StopIteration): execution.send(None)
        finally: execution.close()
        self.assertEqual(r.state, 'paused')
        r.busy = False
        self.assertIsNone(r.advance(4))
        self.assertEqual(issued, ['movej'])
        self.assertEqual(r.i, 1)
        r.debug_continue(single=True, now=5)
        self.assertEqual(r.advance(5)['kind'], 'speed')

    def test_wait_and_zero_wait_finish_before_pause_without_next_dispatch(self):
        for seconds in (0, 2):
            r = runner(f'WAIT {seconds}\nSPEED 20')
            r.debug_continue(single=True, now=10)
            self.assertIsNone(r.advance(10))
            self.assertEqual(r.state, 'waiting')
            if seconds:
                self.assertIsNone(r.advance(11))
                self.assertEqual(r.state, 'waiting')
            self.assertIsNone(r.advance(10 + seconds))
            self.assertEqual((r.state, r.i, r.completed_index), ('paused', 1, 1))
            self.assertIsNone(r.advance(50))

    def test_default_wait_pause_retains_remaining_time(self):
        r = Runner(); r.load(parse('WAIT 5\nSPEED 20', lambda _: None)); r.start()
        r.advance(10); r.pause(12); r.resume(30)
        self.assertIsNone(r.advance(32))
        self.assertEqual(r.advance(33)['kind'], 'speed')

    def test_legacy_breakpoint_repeats_in_loop(self):
        r = runner('LOOP 2\nSPEED 20\nEND', (2,))
        r.debug_continue(now=0)
        action = r.advance(0); self.assertEqual(action['kind'], 'speed'); r.complete()
        self.assertIsNone(r.advance(1)); self.assertEqual(r.state, 'paused')
        self.assertEqual(r.debug_reason, 'breakpoint')
        r.debug_continue(now=1)
        self.assertEqual(r.advance(1)['kind'], 'speed')

    def test_python_assignment_step_variables_and_pre_execution_breakpoint(self):
        r = runner('x = 1\nx += 2\nSPEED(x)', (3,))
        r.debug_continue(single=True, now=0)
        for t in range(10):
            self.assertIsNone(r.advance(t))
            if r.state == 'paused': break
        self.assertEqual(r.debug_line, 2)
        self.assertEqual(r.debug_snapshot()['variables'][0]['value'], 1)
        r.debug_continue(now=10)
        for t in range(10, 20):
            self.assertIsNone(r.advance(t))
            if r.state == 'paused': break
        self.assertEqual(r.debug_line, 3)
        self.assertEqual(r.debug_snapshot()['variables'][0]['value'], 3)
        self.assertEqual(r.steps, [])
        r.debug_continue(single=True, now=20)
        self.assertEqual(r.advance(20)['pct'], 3)
        r.complete(); self.assertEqual(r.state, 'paused')

    def test_python_consecutive_commands_step_without_empty_stop(self):
        r = runner('SPEED(10)\nWAIT(1)\nSPEED(20)')
        r.debug_continue(single=True, now=0)
        self.assertIsNone(r.advance(0))  # before first statement
        self.assertEqual(r.advance(0)['pct'], 10)
        r.complete(); r.debug_continue(single=True, now=1)
        self.assertIsNone(r.advance(1))  # next checkpoint allowed
        self.assertIsNone(r.advance(1)); self.assertEqual(r.state, 'waiting')
        self.assertIsNone(r.advance(2)); self.assertEqual(r.state, 'paused')
        self.assertEqual(r.i, 2)

    def test_guard_revocation_and_normal_restart(self):
        r = runner('SPEED 10\nSPEED 20')
        r.debug_continue(single=True, now=0)
        r.debug_guard = lambda: False
        self.assertIsNone(r.advance(0)); self.assertEqual(r.state, 'aborted')
        self.assertTrue(r.start())
        self.assertFalse(r.debug_enabled)
        self.assertEqual(r.advance(0)['pct'], 10)
        r.complete(); self.assertEqual(r.advance(1)['pct'], 20)

    def test_local_variables_are_data_only_and_bounded(self):
        r = runner('def f(arg):\n    local = [arg] * 100\n    SPEED(arg)\nf(10)', (3,))
        r.debug_continue(now=0)
        for t in range(20):
            self.assertIsNone(r.advance(t))
            if r.state == 'paused': break
        values = {v['name']: v for v in r.debug_snapshot()['variables']}
        self.assertEqual(values['arg']['value'], 10)
        self.assertEqual(len(values['local']['value']), 21)
        values['local']['value'][0] = 'changed'
        self.assertEqual(r.interpreter.debug_scope['local'][0], 10)

    def test_pause_request_during_wait_and_python_loop_breakpoints(self):
        r = runner('WAIT 2\nSPEED 20')
        r.debug_continue(now=0); r.advance(0)
        r.debug_single = True  # same boundary request as debug /pause
        self.assertIsNone(r.advance(1)); self.assertEqual(r.state, 'waiting')
        self.assertIsNone(r.advance(2)); self.assertEqual(r.state, 'paused')
        r = runner('for i in range(2):\n    SPEED(i + 1)', (2,))
        r.debug_continue(now=0)
        for t in range(10):
            self.assertIsNone(r.advance(t))
            if r.state == 'paused': break
        self.assertEqual(r.interpreter.debug_scope['i'], 0)
        r.debug_continue(now=10)
        self.assertEqual(r.advance(10)['pct'], 1); r.complete()
        self.assertIsNone(r.advance(11)); self.assertEqual(r.state, 'paused')
        self.assertEqual(r.interpreter.debug_scope['i'], 1)

    def test_failed_executor_does_not_report_step_complete(self):
        r = runner('SPEED 10\nSPEED 20')
        r.debug_continue(single=True, now=0); r.advance(0)
        executor = MotionExecutor(lambda *args: None)
        async def fail(epoch): raise RuntimeError('rejected')
        captured = []
        with patch('backend.core.motion.asyncio.create_task', lambda coro: captured.append(coro)):
            executor.submit('rejected', fail, r.complete)
        immediate(captured[0])
        self.assertEqual(executor.state, 'failed')
        self.assertEqual(r.completed_index, 0)
        # Mirrors World._drive_executor's failure gate before advance.
        r.stop(executor.error)
        self.assertIsNone(r.advance(1))
        self.assertEqual(r.i, 1)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.context = {'objects': [{'position': [0, 0, 0]}],
                        'points': [{'joints': [0] * 6}], 'toolframe': {'translation': [0, 0, 0]}}
        self.w = SimpleNamespace(settings={'mode': 'sim'}, real=None, _cfg_rev=1,
            runner=Runner(), executor=MotionExecutor(lambda *args: None),
            sim=SimpleNamespace(traj=None, jog=None, paused=False, stop=lambda: None),
            hand_follow=SimpleNamespace(active=False), queue_state='idle', free={'on': False},
            MOTION=1, _need=lambda *args: True, last_deny='', safety={'level': 'green'},
            resolve_point=lambda _: None, point_items=lambda: [], plan_context=lambda: self.context)
        self.client = RouteClient(create_debug_router(self.w))

    def load(self, src='SPEED 10\nWAIT 2'):
        response = self.client.post('/api/program/debug/load', json={'src': src})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['session']

    def test_load_never_executes_and_stale_session_rejected(self):
        token = self.load('x = DI("input")\nSPEED(x)')
        self.assertEqual(self.w.runner.interpreter.operations, 0)
        self.assertEqual(self.w.runner.state, 'paused')
        self.assertEqual(self.client.post('/api/program/debug/step', json={'session': 'old'}).status_code, 409)
        self.assertEqual(self.client.post('/api/program/debug/step', json={'session': token}).status_code, 200)

    def test_every_endpoint_rejects_real_mode_or_link(self):
        token = self.load()
        for mode, link in [('real', None), ('sim', object())]:
            self.w.settings['mode'], self.w.real = mode, link
            self.assertEqual(self.client.get('/api/program/debug').status_code, 409)
            for action in ('load', 'breakpoints', 'step', 'continue', 'pause', 'stop'):
                self.assertEqual(self.client.post('/api/program/debug/'+action,
                    json={'src': 'SPEED 10', 'session': token}).status_code, 409)

    def test_safety_config_change_and_stop(self):
        token = self.load()
        self.w.safety['level'] = 'red'
        self.assertEqual(self.client.post('/api/program/debug/step', json={'session': token}).status_code, 409)
        self.w.safety['level'] = 'green'
        self.w.settings['ground_enabled'] = False; self.w._cfg_rev += 1
        self.assertEqual(self.client.post('/api/program/debug/continue', json={'session': token}).status_code, 409)
        self.assertEqual(self.client.post('/api/program/debug/stop', json={'session': token}).status_code, 200)
        self.assertEqual(self.w.runner.state, 'aborted')

    def test_actual_world_set_speed_does_not_invalidate_debug_guard(self):
        # Compile ONLY this method from source, never import/construct live World.
        source = (Path(__file__).resolve().parents[1] / 'backend/core/world.py').read_text(encoding='utf-8')
        world_class = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'World')
        method = next(n for n in world_class.body if isinstance(n, ast.FunctionDef) and n.name == 'set_speed')
        config = SimpleNamespace(speed_key=lambda mode: mode + '_speed_pct',
                                 save_settings=lambda patch: None)
        namespace = {'config': config}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<isolated World.set_speed>', 'exec'), namespace)
        w = self.w
        w.settings.update(sim_speed_pct=10.0, real_speed_pct=5.0,
                          fence={'enabled': True, 'x': [-1200,1200]})
        w._real_jog_conflict = lambda what: False
        w.log = SimpleNamespace(add=lambda *args: None)
        token = self.load('SPEED 20\nWAIT 0\nSPEED 20')
        request = {'session': token}
        for index in range(3):
            self.assertEqual(self.client.post('/api/program/debug/step', json=request).status_code, 200)
            action = w.runner.advance(index)
            if index != 1:
                self.assertEqual(action['kind'], 'speed')
                revision = w._cfg_rev
                namespace['set_speed'](w, action['pct'], schedule=False)
                self.assertEqual(w._cfg_rev, revision + 1)
                w.runner.complete()
            else:
                self.assertIsNone(action)
                w.runner.advance(index)
            self.assertEqual(w.runner.state, 'paused')
            self.assertTrue(w.runner.debug_guard())
        # A nested safety change remains invalid even if speed changes too.
        w.settings['fence']['enabled'] = False
        namespace['set_speed'](w, 30, schedule=False)
        self.assertFalse(w.runner.debug_guard())
        self.assertEqual(self.client.post('/api/program/debug/continue', json=request).status_code, 409)

    def test_plan_context_changes_invalidate_with_or_without_config_revision(self):
        for key in ('objects', 'points', 'toolframe'):
            for bump_revision in (False, True):
                with self.subTest(key=key, bump_revision=bump_revision):
                    self.setUp()
                    token = self.load()
                    # Mutate the same nested object returned at load time. This
                    # also verifies the captured plan context is a deep copy.
                    if key == 'objects': self.context[key][0]['position'][0] = 1
                    elif key == 'points': self.context[key][0]['joints'][0] = 1
                    else: self.context[key]['translation'][0] = 1
                    if bump_revision: self.w._cfg_rev += 1
                    self.assertFalse(self.w.runner.debug_guard())
                    self.assertEqual(self.client.post('/api/program/debug/step',
                        json={'session': token}).status_code, 409)

    def test_context_change_revokes_running_runner_before_next_dispatch(self):
        token = self.load()
        self.client.post('/api/program/debug/continue', json={'session': token})
        self.context['points'][0]['joints'][0] = 1
        self.assertIsNone(self.w.runner.advance(0))
        self.assertEqual(self.w.runner.state, 'aborted')
        self.assertEqual(self.w.runner.i, 0)

    def test_settings_change_without_revision_is_not_ignored(self):
        token = self.load()
        self.w.settings['ground_enabled'] = False
        self.assertFalse(self.w.runner.debug_guard())
        self.assertEqual(self.client.post('/api/program/debug/continue',
            json={'session': token}).status_code, 409)

    def test_pause_is_boundary_request_and_motion_prevents_extra_step(self):
        token = self.load()
        request = {'session': token}
        self.assertEqual(self.client.post('/api/program/debug/continue', json=request).status_code, 200)
        self.w.runner.advance(0)  # SPEED dispatched, completion still pending
        self.w.sim.traj = object()
        self.assertEqual(self.client.post('/api/program/debug/step', json=request).status_code, 409)
        self.assertEqual(self.client.post('/api/program/debug/pause', json=request).status_code, 200)
        self.assertEqual(self.w.runner.state, 'running')
        self.w.runner.complete()
        self.assertEqual(self.w.runner.state, 'paused')
        self.assertEqual(self.w.runner.i, 1)

    def test_bad_input_and_normal_reload_invalidates_session(self):
        self.assertEqual(self.client.post('/api/program/debug/load', json={'src': 'import os'}).status_code, 422)
        self.assertEqual(self.client.post('/api/program/debug/load', json={'src': 'WAIT 0', 'breakpoints': [True]}).status_code, 422)
        token = self.load()
        self.w.runner.stop(); self.w.runner.load(parse('WAIT 0', lambda _: None))
        self.assertEqual(self.client.post('/api/program/debug/step', json={'session': token}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
