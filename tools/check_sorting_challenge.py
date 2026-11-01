from _offline_guard import require_offline_copy
require_offline_copy()
import unittest
import tempfile,time,copy
from pathlib import Path
from backend.core.world import World
from backend.core.play_vision import bind_observations
from backend.robot_play_routes import RobotPlay
from backend.play_routes import PlayService
from backend.core.vision_workspace import VisionWorkspace,settings
from fastapi import HTTPException
from backend.application.sorting_score import SortingScore
class Score(unittest.TestCase):
    def test_partial_never_full_score(self):
        score=SortingScore(clock=lambda:10.)
        score.begin({'id':'run','group':'group','criterion':'shape','bindings':{'a':{'xyz':[1,2,3],'slot':0},'b':{'xyz':[4,5,6],'slot':1}}})
        score.placed('run','a',[1,2,3],0)
        score.finish('run','aborted',False)
        value=score.snapshot()
        self.assertEqual(value['completed'],1);self.assertLess(value['score'],100)
    def test_actual_confirmation_required_and_wrong_old_duplicate_events_do_not_score(self):
        now=[100.];score=SortingScore(clock=lambda:now[0])
        plan={'id':'run','group':'group','criterion':'shape','bindings':{'a':{'xyz':[1,2,3],'slot':0}}}
        score.begin(plan);now[0]+=2
        score.placed('old','a',[1,2,3],0)
        score.finish('run','finished',True)
        self.assertEqual(score.snapshot()['score'],0)
        self.assertEqual(score.snapshot()['status'],'failed')
        score.begin(plan);now[0]+=3
        score.placed('run','a',[1,2,30],0);score.placed('run','a',[1,2,3],0);score.placed('run','a',[1,2,3],0)
        score.finish('run','finished',True)
        value=score.snapshot();self.assertEqual(value['completed'],1);self.assertEqual(value['score'],100)
        self.assertEqual(value['elapsed_s'],3.)
        score.reset('group');self.assertIsNone(score.snapshot())


def pixel_shapes(pieces):
    import cv2 as cv
    import numpy as np
    engine=VisionWorkspace(None);engine.virtual_session='shape-fixture'
    engine.config=settings({'input':'virtual','mapping':True,'height_mm':24,'roi':[0,0,1,1],'colors':['red','green','blue']})
    image=np.full((640,640,3),120,dtype=np.uint8)
    expected={}
    for index,piece in enumerate(pieces):
        x=round((piece['xyz'][0]-290)*2);y=round((piece['xyz'][1]+510)*2)
        # Purposefully derive labels from pixels, not task color or kind.
        shape='circle' if index%2==0 else 'rectangle';expected[piece['id']]=shape
        if shape=='circle':cv.circle(image,(x,y),60,(255,0,0),-1)
        else:cv.rectangle(image,(x-60,y-60),(x+60,y+60),(0,0,255),-1)
    ok,encoded=cv.imencode('.png',image);assert ok
    key='virtual:shape-fixture:fixed-view'
    engine.latest=engine.analyze(encoded.tobytes(),engine.config,None,key)
    engine.latest.update(source='virtual',source_key=key,captured_at=time.time(),frame_seq=1)
    rows=[[x,y,290+x*.5,-510+y*.5] for x,y in [(0,0),(639,0),(639,639),(0,639),(200,200),(440,440)]]
    engine.calibrate({'points':rows,'plane_z':4,'max_error_mm':2})
    result=engine.analyze(encoded.tobytes(),engine.config,engine.calibration,key)
    now=time.time();result['objects']=engine.track(result['objects'],now)
    result.update(source='virtual',source_key=key,captured_at=now,frame_seq=2)
    engine.latest=result;engine.source_key=key
    return engine,expected

class ShapeEvidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.world=World(data_dir=Path(self.temp.name));self.play=self.world.robot_play=RobotPlay(self.world)
        self.group=self.play.scene.setup({'mode':'sorting','criterion':'shape','shape_counts':{'rectangle':1,'circle':1}})['group']
        self.pieces,_,self.zone=self.play.scene.group(self.world.objects.list(),self.group)
    def test_actual_rgb_contours_calibration_and_shape_bands(self):
        engine,expected=pixel_shapes(self.pieces);self.play.vision_workspace=engine
        values=self.play.targets({'group':self.group,'action':'sorting','criterion':'shape'})
        by_id={piece['id']:xyz for piece,xyz,slot in values}
        left=next(oid for oid,label in expected.items() if label=='circle')
        right=next(oid for oid,label in expected.items() if label=='rectangle')
        self.assertLess(by_id[left][0],by_id[right][0])
        evidence=bind_observations(engine.snapshot(),self.pieces,time.time(),criterion='shape')
        self.assertEqual({oid:v['shape'] for oid,v in evidence['bindings'].items()},expected)
    def test_unknown_missing_stale_shape_and_criterion_rejected(self):
        engine,_=pixel_shapes(self.pieces);snapshot=engine.snapshot()
        for shape in (None,'irregular','triangle'):
            value=copy.deepcopy(snapshot);value['result']['objects'][0]['shape']=shape
            with self.assertRaises(ValueError):bind_observations(value,self.pieces,time.time(),criterion='shape')
        value=copy.deepcopy(snapshot);value['stale']=True
        with self.assertRaises(ValueError):bind_observations(value,self.pieces,time.time(),criterion='shape')
        with self.assertRaises(HTTPException):self.play.targets({'group':self.group,'action':'sorting','criterion':'guess'})
    def test_production_commit_then_stop_preserves_partial_result_without_full_score(self):
        bindings={piece['id']:{'xyz':[300+index*100,350,16],'slot':piece['task']['home']} for index,piece in enumerate(self.pieces)}
        plan={'id':'actual-stop','group':self.group,'action':'sorting','criterion':'shape',
              'bindings':bindings,'expected':{piece['id']:piece['xyz'][:] for piece in self.pieces}}
        self.play.active=plan;self.play.sorting_score.begin(plan)
        first=self.pieces[0];goal=bindings[first['id']]
        self.play._commit(first['id'],goal['xyz'],goal['slot'])
        self.play.stop()
        value=self.play.state()['sorting_score']
        self.assertEqual(value['status'],'cancelled');self.assertEqual(value['completed'],1)
        self.assertEqual(value['correct'],1);self.assertLess(value['score'],100)
        actual=next(v for v in self.world.objects.list() if v['id']==first['id'])
        self.assertEqual(actual['xyz'],goal['xyz'])
        self.assertEqual(actual['task']['slot'],goal['slot'])
        self.play.scene.reset({'group':self.group});self.assertIsNone(self.play.state()['sorting_score'])

    def test_task_setup_commit_and_reset_use_same_durable_identity(self):
        store=self.world.objects;self.assertEqual(store.persisted_disk,store.disk_signature())
        before=store.persisted_generation
        piece=self.pieces[0];self.play.active={'id':'fake','bindings':{},'expected':{}}
        self.play._commit(piece['id'],piece['xyz'],0)
        self.assertEqual(store.persisted_generation,before+1);self.assertEqual(store.persisted_disk,store.disk_signature())
        self.play.active=None;self.play.scene.reset({'group':self.group})
        self.assertEqual(store.persisted_generation,before+2)

if __name__=='__main__':unittest.main()
