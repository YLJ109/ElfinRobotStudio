"""A/B料区玩法：只在临时副本使用假世界，不读取硬件。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from backend.core.objects import ObjectStore, half_height
from backend.play_routes import PlayService, create_play_router, computer_move, outcome
from backend.robot_play_routes import RobotPlay
from staged_play_test_support import pixel_vision

def fake_world(path):
    return NS(objects=ObjectStore(path),settings={'mode':'sim'},real=None,executor=NS(busy=False),runner=NS(state='idle'),
        queue_state='idle',sim=NS(traj=None,jog=None,paused=False),hand_follow=NS(active=False),
        real_jog_follow=NS(busy=False),free={'on':False},ready_state={'running':False},_stop_interlocked=lambda:False)

class PlayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.world=fake_world(Path(self.temp.name)/'objects.json');self.play=PlayService(self.world)
    def setup(self,mode='chess',**kw):return self.play.setup({'mode':mode,**kw})
    def pieces(self):return [item for item in self.world.objects.list() if item.get('task',{}).get('role')=='piece']
    def test_setup_preserves_ordinary_and_creates_5_o_4_x(self):
        ordinary=self.world.objects.add({'name':'用户原有物体','xyz':[0,0,100]})
        value=self.setup();pieces=self.pieces()
        self.assertEqual(len(value['objects']),12);self.assertEqual(len(pieces),9)
        self.assertEqual(sum(p['task']['symbol']=='O' for p in pieces),5)
        self.assertEqual(sum(p['task']['symbol']=='X' for p in pieces),4)
        self.assertEqual([p for p in value['objects'] if p['id']==ordinary['id']],[ordinary])
        self.assertTrue(all(p['xyz'][2]-half_height(p)>=4 for p in pieces))
        again=self.setup('sorting');self.assertEqual(len(again['objects']),12)
        self.assertFalse(any(p.get('task',{}).get('group')==value['group'] for p in again['objects']))
    def test_chess_one_transaction_moves_two_actual_scene_pieces(self):
        group=self.setup()['group'];value=self.play.chess({'group':group,'slot':0})
        self.assertEqual(value['board'].count('O'),1);self.assertEqual(value['board'].count('X'),1)
        moved=[p for p in self.pieces() if p['task']['slot']>=0]
        self.assertEqual(len(moved),2);self.assertTrue(all(200<=p['xyz'][1]<=500 for p in moved))
        with self.assertRaises(HTTPException):self.play.chess({'group':group,'slot':0})
        restored=ObjectStore(self.world.objects.path).list()
        self.assertEqual(restored,self.world.objects.list())
    def test_full_chess_game_never_places_more_than_available(self):
        group=self.setup()['group'];board=[None]*9
        for _ in range(5):
            slot=next(index for index,value in enumerate(board) if not value)
            state=self.play.chess({'group':group,'slot':slot});board=state['board']
            self.assertLessEqual(board.count('O'),5);self.assertLessEqual(board.count('X'),4)
            if state['result']:break
        self.assertIsNotNone(outcome(board))
        with self.assertRaises(HTTPException):self.play.chess({'group':group,'slot':0})
    def test_sort_3_each_into_b_and_reset_to_a(self):
        group=self.setup('sorting')['group'];before=self.world.objects.list();disk=self.world.objects.path.read_bytes()
        with self.assertRaises(HTTPException) as error:self.play.sort({'group':group,'criterion':'color'})
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(self.world.objects.list(),before);self.assertEqual(self.world.objects.path.read_bytes(),disk)
        engine,detected=pixel_vision(self.pieces())
        targets=RobotPlay(self.world,engine).targets({'group':group,'action':'sorting','criterion':'color'})
        self.assertEqual(len(targets),9)
        self.assertTrue(all(185<=xyz[1]<=515 for _,xyz,_ in targets))
        bands={color:[xyz[0] for piece,xyz,_ in targets if detected[piece['id']]==color] for color in set(detected.values())}
        self.assertEqual(len(bands),3)
        intervals=sorted((min(xs),max(xs)) for xs in bands.values())
        self.assertTrue(all(intervals[index][1]+72<=intervals[index+1][0] for index in range(2)))
        self.play.reset({'group':group})
        self.assertTrue(all(p['xyz'][1]<0 and p['task']['slot']==-1 for p in self.pieces()))
    def test_max_27_have_disjoint_envelopes_inside_zone(self):
        group=self.setup('sorting',counts={'red':9,'green':9,'blue':9})['group']
        before=self.world.objects.list()
        with self.assertRaises(HTTPException):self.play.sort({'group':group,'criterion':'color'})
        self.assertEqual(self.world.objects.list(),before)
        pieces=self.pieces();self.assertEqual(len(pieces),27)
        for i,p in enumerate(pieces):
            self.assertLessEqual(abs(p['xyz'][0]-450)+19,165)
            self.assertLessEqual(abs(p['xyz'][1]+350)+19,165)
            for q in pieces[i+1:]:
                self.assertTrue(abs(p['xyz'][0]-q['xyz'][0])>=38 or abs(p['xyz'][1]-q['xyz'][1])>=38)
    def test_manual_b_zone_move_requires_board_reset(self):
        group=self.setup()['group'];self.play.chess({'group':group,'slot':0})
        b=next(p for p in self.world.objects.list() if p['task']['role']=='zone_b')
        self.world.objects.update(b['id'],{'xyz':[700,500,2]})
        with self.assertRaises(HTTPException):self.play.chess({'group':group,'slot':1})
        self.play.reset({'group':group});self.play.chess({'group':group,'slot':1})
    def test_missing_piece_and_stale_group_fail_without_publish(self):
        group=self.setup()['group'];self.world.objects.remove(self.pieces()[0]['id']);before=self.world.objects.list()
        with self.assertRaises(HTTPException):self.play.chess({'group':group,'slot':0})
        self.assertEqual(before,self.world.objects.list())
        with self.assertRaises(HTTPException):self.play.reset({'group':'a'*32})
    def test_disk_error_is_atomic(self):
        group=self.setup()['group'];before=self.world.objects.list();disk=self.world.objects.path.read_bytes()
        with patch('backend.play_routes.write_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.play.chess({'group':group,'slot':0})
        self.assertEqual(before,self.world.objects.list());self.assertEqual(disk,self.world.objects.path.read_bytes())
    def test_no_hardware_or_busy_changes(self):
        for setup in (lambda:setattr(self.world,'real',object()),lambda:setattr(self.world.executor,'busy',True),lambda:setattr(self.world.hand_follow,'active',True)):
            self.world=fake_world(self.world.objects.path);self.play=PlayService(self.world);setup()
            with self.assertRaises(HTTPException):self.setup()
    def test_invalid_input_and_metadata(self):
        for payload in ({'mode':'unknown'},{'mode':'sorting','counts':{'red':True,'green':3,'blue':3}},
                        {'mode':'sorting','counts':{'red':0,'green':0,'blue':0}},{'mode':'chess','unknown':1}):
            with self.assertRaises(HTTPException):self.play.setup(payload)
        for task in (True,{}, {'group':'bad'}):
            with self.assertRaises(ValueError):ObjectStore._sanitize({'task':task})
    def test_routes_real_guards_and_save_error(self):
        app=FastAPI();app.include_router(create_play_router(self.world))
        with TestClient(app) as client:
            value=client.post('/api/play/setup',json={'mode':'chess'});self.assertEqual(value.status_code,200)
            group=value.json()['group']
            # 旧HTTP瞬移入口已禁用；连合法格位也必须走机器人程序预检。
            self.assertEqual(client.post('/api/play/chess',json={'group':group,'slot':True}).status_code,409)
            self.assertEqual(client.post('/api/play/chess',json={'group':group,'slot':0}).status_code,409)
            self.world.real=object()
            self.assertEqual(client.post('/api/play/reset',json={'group':group}).status_code,409)
            self.world.real=None
            with patch('backend.play_routes.write_json',side_effect=OSError()):
                self.assertEqual(client.post('/api/play/reset',json={'group':group}).status_code,503)

if __name__=='__main__':unittest.main()
