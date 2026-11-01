"""Exercise the real clear route body without starting a service or hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class SceneClearCases(unittest.IsolatedAsyncioTestCase):
    def make(self):
        tree=ast.parse(Path('backend/main.py').read_text(encoding='utf-8-sig'))
        route=next(node for node in ast.walk(tree) if isinstance(node,ast.AsyncFunctionDef) and node.name=='objects_clear')
        route.decorator_list=[]
        events=[]
        board=SimpleNamespace(game='ttt',group='session',preparing=False,current=None,active=True)
        async def abort(game,session):
            events.append(('abort',game,session))
            board.active=False
            return {'ok':True}
        board.abort=abort
        def clear():
            events.append(('clear',))
            return 12
        world=SimpleNamespace(executor=SimpleNamespace(busy=False),runner=SimpleNamespace(state='idle'),
            objects=SimpleNamespace(clear=clear),log=SimpleNamespace(add=lambda *args:events.append(('log',))),
            _request_real_jog_stop=lambda reason:events.append(('jog-stop',)))
        env={'world':world,'game_router':SimpleNamespace(board=board),'_play_scene_busy':lambda:False}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[route],type_ignores=[])),str(Path('backend/main.py')),'exec'),env)
        return env['objects_clear'],env,world,board,events

    async def test_clear_invalidates_waiting_game_before_deleting_only_scene_objects(self):
        clear,env,world,board,events=self.make()
        result=await clear()
        self.assertEqual(result,{'ok':True,'removed':12})
        self.assertFalse(board.active)
        self.assertEqual(events[:3],[('abort','ttt','session'),('jog-stop',),('clear',)])

    async def test_busy_program_planning_or_held_piece_never_partially_clears(self):
        for kind in ('play','runner','executor','planning','current'):
            with self.subTest(kind=kind):
                clear,env,world,board,events=self.make()
                if kind=='play':env['_play_scene_busy']=lambda:True
                elif kind=='runner':world.runner.state='paused'
                elif kind=='executor':world.executor.busy=True
                elif kind=='planning':board.preparing=True
                else:board.current={'piece':'active'}
                self.assertFalse((await clear())['ok'])
                self.assertEqual(events,[])
                self.assertTrue(board.active)

    async def test_failed_abort_preserves_objects(self):
        clear,env,world,board,events=self.make()
        async def failed(*args):return {'ok':False,'why':'stop unknown'}
        board.abort=failed
        self.assertEqual(await clear(),{'ok':False,'why':'stop unknown'})
        self.assertEqual(events,[])

    async def test_empty_non_game_scene_uses_same_clear_without_new_game(self):
        clear,env,world,board,events=self.make()
        board.game=None;board.group=None
        self.assertTrue((await clear())['ok'])
        self.assertEqual(events,[('jog-stop',),('clear',),('log',)])


if __name__=='__main__':unittest.main()
