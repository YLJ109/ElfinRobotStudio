"""Failed board requests show released pieces and confirmed history, no devices."""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy
from types import SimpleNamespace
import unittest
import httpx
from fastapi import FastAPI
from backend.application.board_games import GameStore
from backend.application.board_robot import BoardExecutor, BoardRobotError, cell_center, BOARD_TOP
from backend.game_routes import create_game_router


class FailureState(unittest.IsolatedAsyncioTestCase):
    def make(self):
        games=GameStore();games.new('chess',{'opponent':'human'})
        world=SimpleNamespace(objects=SimpleNamespace(list=lambda:pieces),runner=SimpleNamespace(state='idle'),executor=SimpleNamespace(busy=False))
        board=BoardExecutor(world,games)
        board.game='chess';board.group='test-board';board.active=True
        board.confirmed_state=copy.deepcopy(games.snapshot('chess'))
        pieces=[]
        for cell in board.confirmed_state['cells']:
            r,c=cell['r'],cell['c']
            pieces.append(dict(id=f'piece-{r}-{c}',kind='cylinder',size=[10,10,20],xyz=[*cell_center('chess',r,c),BOARD_TOP+10],
                task=dict(group=board.group,role='piece',slot=r*8+c,code=cell['piece']['code'],side=cell['piece']['side'])))
        games.move('chess',{'from':[6,4],'to':[4,4]},auto_reply=False)
        return games,board,pieces

    async def test_failed_step_returns_actual_cells_and_confirmed_history_in_same_response(self):
        games,board,pieces=self.make()
        async def step(*_args):
            board.fail('collision with adjacent bishop')
            raise BoardRobotError('collision with adjacent bishop')
        board.step=step
        app=FastAPI();app.include_router(create_game_router(games,board))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
            result=(await client.post('/api/games/chess/step',json={'session_id':board.group})).json()
            self.assertFalse(result['ok'])
            self.assertEqual(result['physical']['lifecycle'],'failed')
            for state in (result['state'],result['physical']['actual_state']):
                self.assertEqual(state['history'],[])
                self.assertIsNone(state['last_move'])
                self.assertEqual(state['moves'],[])
                self.assertTrue(any(p['r']==6 and p['c']==4 for p in state['cells']))
                self.assertFalse(any(p['r']==4 and p['c']==4 for p in state['cells']))
            refreshed=(await client.get('/api/games/chess/state')).json()
            self.assertEqual(refreshed['state']['cells'],result['state']['cells'])
            self.assertEqual(refreshed['state']['history'],[])
            self.assertEqual(games.snapshot('chess')['history'],['Pe2-e4'],'display must not rewrite rule engine')

    async def test_pending_refresh_uses_actual_pieces_but_partial_release_is_not_rolled_back(self):
        games,board,pieces=self.make();board.queue=[{'kind':'move'}]
        app=FastAPI();app.include_router(create_game_router(games,board))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
            state=(await client.get('/api/games/chess/state')).json()['state']
            self.assertEqual(state['history'],[])
            piece=next(p for p in pieces if p['task']['slot']==52)
            piece['task']['slot']=36;piece['xyz']=[*cell_center('chess',4,4),BOARD_TOP+10]
            board.fail('later action failed')
            actual=(await client.get('/api/games/chess/state')).json()['state']
            self.assertTrue(any(p['r']==4 and p['c']==4 for p in actual['cells']))
            self.assertFalse(any(p['r']==6 and p['c']==4 for p in actual['cells']))


if __name__=='__main__':unittest.main(verbosity=2)
