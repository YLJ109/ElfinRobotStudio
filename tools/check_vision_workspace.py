"""工件视觉：一次性副本、合成图像、假相机，无真实设备。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import base64
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2 as cv
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from backend.core.vision_workspace import VisionWorkspace
from backend.vision_workspace_routes import create_workspace_vision_router
from backend.vision_routes import MAX_BODY_BYTES, MAX_IMAGE_BYTES


# Fail closed even if a future implementation tries to open a physical device.
cv.VideoCapture = unittest.mock.Mock(side_effect=AssertionError('Physical cameras forbidden in offline regression'))

def fixture(offset=0):
    image=np.full((480,640,3),90,np.uint8)
    cv.rectangle(image,(100+offset,80),(200+offset,160),(0,0,230),-1)
    cv.circle(image,(360+offset,160),35,(0,220,0),-1)
    cv.fillPoly(image,[np.array([[300,300],[400,300],[350,390]])],(220,0,0))
    return cv.imencode('.png',image)[1].tobytes()

POINTS=[[40,40,-200,-100],[600,40,360,-100],[600,440,360,300],[40,440,-200,300],
        [150,200,-90,60],[400,300,160,160]]

class Camera:
    active=None
    packet=None
    def snapshot(self):return {'active':copy.deepcopy(self.active)}
    def stream_frame(self,stream,session):return self.packet if self.active and self.active['session']==session else None

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.camera=Camera();self.engine=VisionWorkspace(self.camera,Path(self.temp.name)/'vision.json')

    def test_prior_side_hanging_default_migrates_to_plate_mount(self):
        path=Path(self.temp.name)/'old-mount.json'
        config=copy.deepcopy(self.engine.config)
        config['virtual'].update(offset_mm=[69.6,0,-24.25],direction=[0,1000,0])
        path.write_text(json.dumps({'config':config,'camera_model_version':3}),encoding='utf-8')
        restored=VisionWorkspace(self.camera,path)
        self.assertEqual(restored.config['virtual']['offset_mm'],[-58.6,0,-6])
        self.assertEqual(restored.config['virtual']['direction'],[0,0,1000])

    def test_custom_mount_is_preserved_across_plate_model_upgrade(self):
        path=Path(self.temp.name)/'custom-mount.json'
        config=copy.deepcopy(self.engine.config)
        config['virtual'].update(offset_mm=[12,34,56],direction=[-100,0,200])
        path.write_text(json.dumps({'config':config,'camera_model_version':3}),encoding='utf-8')
        restored=VisionWorkspace(self.camera,path)
        self.assertEqual(restored.config['virtual'],config['virtual'])

    def test_colors_shapes_and_pixel_sizes(self):
        values={v['color']:v for v in self.engine.upload(fixture())['result']['objects']}
        self.assertEqual(len(values),3)
        self.assertEqual([values[c]['shape'] for c in ['red','green','blue']],['rectangle','circle','triangle'])
        self.assertAlmostEqual(values['red']['center_px'][0],150,delta=1)
        self.assertEqual(sorted(round(v) for v in values['red']['size_px']),[80,100])
        self.assertTrue(all(v['mapped'] is None for v in values.values()))

    def test_independent_calibration_checks_and_mapping(self):
        self.engine.upload(fixture())
        result=self.engine.calibrate({'points':POINTS,'plane_z':100})
        self.assertLess(max(result['calibration']['check_errors_mm']),1e-6)
        self.engine.process()
        value=next(v for v in self.engine.snapshot()['result']['objects'] if v['color']=='red')
        for actual,expected in zip(value['mapped']['xyz_mm'],[-90,-20,110]):self.assertAlmostEqual(actual,expected,delta=1)
        self.assertTrue(value['mapped']['height_estimated'])
        self.assertEqual(sorted(round(v) for v in value['mapped']['size_mm'][:2]),[80,100])

    def test_bad_calibration_is_rejected(self):
        self.engine.upload(fixture())
        for points in ([POINTS[0]]*6,[*POINTS[:4],[150,200,900,900],POINTS[5]], [*POINTS[:4],POINTS[0],POINTS[5]]):
            with self.subTest(points=points),self.assertRaises(ValueError):self.engine.calibrate({'points':points,'plane_z':0})

    def test_tracks_stable_duplicate_frame_cannot_refresh_age(self):
        self.camera.active={'session':'a','state':'streaming'};self.camera.packet=(1,time.time(),fixture())
        self.engine.process();first=self.engine.snapshot()['result']
        self.camera.packet=(2,time.time(),fixture(3));self.engine.process();second=self.engine.snapshot()['result']
        self.assertEqual({v['color']:v['id'] for v in first['objects']},{v['color']:v['id'] for v in second['objects']})
        self.camera.packet=(2,time.time(),fixture(10));self.engine.process()
        self.assertEqual(self.engine.snapshot()['result']['captured_at'],second['captured_at'])
        self.camera.active=None;self.engine.process();self.assertIsNone(self.engine.snapshot()['result'])

    def test_filters_validation_and_persistence(self):
        self.engine.configure({'colors':['red'],'roi':[.05,.05,.4,.4]})
        self.assertEqual([v['color'] for v in self.engine.upload(fixture())['result']['objects']],['red'])
        self.assertEqual(VisionWorkspace(self.camera,self.engine.path).config['colors'],['red'])
        before=copy.deepcopy(self.engine.config)
        for patch in ({'min_area':True},{'height_mm':float('nan')},{'colors':[]},{'roi':[.9,0,.5,1]},{'input':'url'}):
            with self.assertRaises(ValueError):self.engine.configure(patch)
            self.assertEqual(self.engine.config,before)

    def test_session_resolution_and_expiry(self):
        self.camera.active={'session':'a','state':'streaming'};self.camera.packet=(1,time.time(),fixture())
        self.engine.process();self.engine.calibrate({'points':POINTS,'plane_z':0});self.engine.process()
        self.assertTrue(self.engine.snapshot()['result']['calibrated'])
        self.camera.active['session']='b';self.camera.packet=(1,time.time(),fixture());self.engine.process()
        self.assertFalse(self.engine.snapshot()['result']['calibrated'])
        self.engine.latest['captured_at']=time.time()-2
        state=self.engine.snapshot();self.assertTrue(state['stale'])
        self.assertTrue(all(v['status']=='expired' for v in state['result']['objects']))

    def test_hold_expires_no_per_frame_disk_writes_and_no_queue(self):
        self.engine.upload(fixture());original=copy.deepcopy(self.engine.latest['objects']);stamp=time.time()
        self.engine.track(original,stamp)
        self.assertTrue(all(v['status']=='holding' for v in self.engine.track([],stamp+.1)))
        self.assertEqual(self.engine.track([],stamp+1),[])
        self.assertFalse(self.engine.path.exists())
        self.engine.operation.acquire()
        try:self.engine.process()
        finally:self.engine.operation.release()

    def test_routes_do_not_import_world(self):
        app=FastAPI();app.include_router(create_workspace_vision_router(self.engine))
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/vision/workspace/image',json={'url':'http://example.invalid'}).status_code,422)
            self.assertEqual(client.post('/api/vision/workspace/config',json={'min_area':True}).status_code,422)
            self.assertEqual(client.post('/api/vision/workspace/config',content='null',headers={'content-type':'application/json'}).status_code,422)
            response=client.post('/api/vision/workspace/image',json={'image_base64':base64.b64encode(fixture()).decode()})
            self.assertEqual(response.status_code,200);self.assertEqual(len(response.json()['result']['objects']),3)
            self.assertEqual(client.post('/api/vision/workspace/calibrate',json={'points':POINTS,'plane_z':0}).status_code,200)
            self.engine.process();self.assertTrue(client.get('/api/vision/workspace/state').json()['result']['calibrated'])

    def test_all_eight_colors_use_real_opencv_pixels(self):
        colors=['red','yellow','green','cyan','blue','purple','white','black']
        hsv=np.uint8([[[179,240,230],[25,240,230],[60,240,230],[93,240,230],
                       [115,240,230],[145,240,230],[0,0,240],[0,0,20]]])
        bgr=cv.cvtColor(hsv,cv.COLOR_HSV2BGR)[0]
        image=np.full((480,640,3),90,np.uint8)
        for index,color in enumerate(bgr):
            x=40+(index%4)*150;y=50+(index//4)*220
            cv.rectangle(image,(x,y),(x+65,y+55),tuple(map(int,color)),-1)
        self.engine.configure({'colors':colors})
        result=self.engine.upload(cv.imencode('.png',image)[1].tobytes())['result']
        self.assertEqual({v['color'] for v in result['objects']},set(colors))
        self.assertEqual(len(result['objects']),8)
        self.assertTrue(all(v['shape']=='rectangle' for v in result['objects']))
        for value in result['objects']:
            expected=bgr[colors.index(value['color'])][::-1]
            self.assertEqual(value['rgb'],'#'+''.join(f'{int(v):02x}' for v in expected))

    def test_downsampling_keeps_original_pixel_coordinates(self):
        image=cv.imdecode(np.frombuffer(fixture(),np.uint8),cv.IMREAD_COLOR)
        image=cv.resize(image,(1280,960),interpolation=cv.INTER_NEAREST)
        result=self.engine.upload(cv.imencode('.png',image)[1].tobytes())['result']
        self.assertEqual((result['image']['width'],result['image']['height']),(1280,960))
        red=next(v for v in result['objects'] if v['color']=='red')
        self.assertAlmostEqual(red['center_px'][0],300,delta=3)
        self.assertAlmostEqual(red['center_px'][1],240,delta=3)
        for actual,expected in zip(sorted(red['size_px']),[160,200]):
            self.assertAlmostEqual(actual,expected,delta=3)

    def test_roi_excludes_outside_and_clipped_shapes(self):
        self.engine.configure({'roi':[.45,.15,.25,.3]})
        values=self.engine.upload(fixture())['result']['objects']
        self.assertEqual([v['color'] for v in values],['green'])
        self.assertAlmostEqual(values[0]['center_px'][0],360,delta=1)
        self.engine.configure({'roi':[.2,0,.8,1]})
        values=self.engine.upload(fixture())['result']['objects']
        self.assertEqual({v['color'] for v in values},{'green','blue'})

    def test_failed_independent_check_does_not_replace_good_calibration(self):
        self.engine.upload(fixture());self.engine.calibrate({'points':POINTS,'plane_z':10})
        self.engine.process();before=copy.deepcopy(self.engine.calibration)
        for index in (4,5):
            points=copy.deepcopy(POINTS);points[index][2]+=3
            with self.subTest(check=index),self.assertRaisesRegex(ValueError,'独立复核误差'):
                self.engine.calibrate({'points':points,'plane_z':10,'max_error_mm':2})
            self.assertEqual(self.engine.calibration,before)
        points=copy.deepcopy(POINTS);points[4]=[10,200,-230,60]
        with self.assertRaisesRegex(ValueError,'四边形内'):
            self.engine.calibrate({'points':points,'plane_z':10})

    def test_calibration_resolution_image_source_and_restart_scope(self):
        self.camera.active={'session':'a','state':'streaming'}
        self.camera.packet=(1,time.time(),fixture());self.engine.process()
        self.engine.calibrate({'points':POINTS,'plane_z':0});self.engine.process()
        self.assertTrue(self.engine.snapshot()['result']['calibrated'])
        small=cv.resize(cv.imdecode(np.frombuffer(fixture(),np.uint8),cv.IMREAD_COLOR),(320,240))
        self.camera.packet=(2,time.time(),cv.imencode('.png',small)[1].tobytes());self.engine.process()
        result=self.engine.snapshot()['result']
        self.assertFalse(result['calibrated']);self.assertTrue(all(v['mapped'] is None for v in result['objects']))
        result=self.engine.upload(fixture())['result']
        self.assertFalse(result['calibrated']);self.assertTrue(all(v['mapped'] is None for v in result['objects']))
        self.engine.calibrate({'points':POINTS,'plane_z':0});self.engine.process()
        result=self.engine.upload(fixture(5))['result']
        self.assertFalse(result['calibrated']);self.assertTrue(all(v['mapped'] is None for v in result['objects']))
        self.engine.configure({'mapping':True})
        restarted=VisionWorkspace(self.camera,self.engine.path)
        self.assertIsNone(restarted.calibration);self.assertIsNone(restarted.latest)

    def test_objects_outside_calibration_hull_are_not_mapped(self):
        self.engine.upload(fixture())
        points=[[80,60,80,60],[240,60,240,60],[240,220,240,220],[80,220,80,220],
                [120,100,120,100],[210,180,210,180]]
        self.engine.calibrate({'points':points,'plane_z':0});self.engine.process()
        values={v['color']:v for v in self.engine.latest['objects']}
        self.assertIsNotNone(values['red']['mapped'])
        self.assertIsNone(values['green']['mapped']);self.assertIsNone(values['blue']['mapped'])

    def test_same_frame_does_not_run_opencv_refresh_ttl_or_revision(self):
        self.camera.active={'session':'a','state':'streaming'}
        with patch('backend.core.vision_workspace.time.time',return_value=100):
            self.camera.packet=(1,100,fixture());self.engine.process()
        before=copy.deepcopy(self.engine.snapshot());tracks=copy.deepcopy(self.engine.tracks)
        with patch('backend.core.vision_workspace.time.time',return_value=100.5),patch.object(
                self.engine,'analyze',side_effect=AssertionError('duplicate frame was analyzed')):
            self.camera.packet=(1,100.5,fixture(10));self.engine.process()
        self.assertEqual(self.engine.revision,before['revision'])
        self.assertEqual(self.engine.latest['captured_at'],100)
        self.assertEqual(self.engine.tracks,tracks)

    def test_tracking_matching_is_one_to_one_and_hold_never_extends_ttl(self):
        value=self.engine.upload(fixture())['result']['objects'][0]
        self.engine.tracks.clear()
        first=self.engine.track([copy.deepcopy(value)],100)[0]
        second=copy.deepcopy(value);second['center_px'][0]+=10
        matched=self.engine.track([copy.deepcopy(value),second],100.1)
        self.assertEqual(len({v['id'] for v in matched}),2)
        self.assertEqual(matched[0]['id'],first['id'])
        for stamp in (100.2,100.4,100.7):
            held=self.engine.track([],stamp)
            self.assertEqual(len(held),2)
            self.assertTrue(all(v['status']=='holding' and v['last_seen']==100.1 for v in held))
        self.assertEqual(self.engine.track([],100.76),[])
        new=self.engine.track([copy.deepcopy(value)],101)[0]
        self.assertNotEqual(new['id'],first['id'])
        self.assertEqual(self.engine.track([],100),[],'backwards timestamps must not retain tracks')

    def test_detection_and_retained_tracks_are_bounded(self):
        image=np.full((480,640,3),90,np.uint8)
        for y in range(40,440,40):
            for x in range(40,600,40):cv.rectangle(image,(x,y),(x+20,y+20),(0,0,230),-1)
        values=self.engine.upload(cv.imencode('.png',image)[1].tobytes())['result']['objects']
        self.assertEqual(len(values),50);self.assertEqual(len(self.engine.tracks),50)
        self.assertEqual(len(self.engine.track([],time.time()+.1)),50)
        self.assertEqual(self.engine.track([],time.time()+1),[])

    def test_stale_future_missing_and_closed_camera_frames(self):
        self.camera.active={'session':'a','state':'streaming'}
        for stamp in (time.time()-2,time.time()+2,float('nan'),True):
            with self.subTest(stamp=stamp):
                self.camera.packet=(1,stamp,fixture());self.engine.process()
                self.assertIsNone(self.engine.snapshot()['result'])
        self.camera.packet=(2,time.time(),fixture());self.engine.process()
        self.assertIsNotNone(self.engine.latest)
        self.camera.active['state']='closed';self.engine.process()
        self.assertIsNone(self.engine.latest);self.assertFalse(self.engine.tracks)

    def test_config_change_during_analysis_discards_old_generation(self):
        self.camera.active={'session':'a','state':'streaming'};self.camera.packet=(1,time.time(),fixture())
        analyze=self.engine.analyze
        def changed(*args):
            result=analyze(*args);self.engine.configure({'enabled':False});return result
        with patch.object(self.engine,'analyze',side_effect=changed):self.engine.process()
        self.assertIsNone(self.engine.latest);self.assertFalse(self.engine.tracks)

    def test_camera_closing_during_analysis_cannot_publish_mapped_frame(self):
        self.camera.active={'session':'a','state':'streaming'};self.camera.packet=(1,time.time(),fixture())
        self.engine.process();self.engine.calibrate({'points':POINTS,'plane_z':0})
        analyze=self.engine.analyze
        def closed(*args):
            result=analyze(*args);self.camera.active['state']='closed';return result
        with patch.object(self.engine,'analyze',side_effect=closed):self.engine.process()
        self.assertTrue(self.engine.snapshot()['result'] is None,
            'a camera that closed during native analysis must not publish mapped results')
        self.assertFalse(self.engine.tracks)

    def test_malformed_images_and_route_error_contracts(self):
        app=FastAPI();app.include_router(create_workspace_vision_router(self.engine))
        png=fixture()
        oversized_header=bytearray(png[:33]);oversized_header[16:20]=(4096).to_bytes(4,'big')
        with TestClient(app) as client:
            cases=[(b'',413),(b'not an image',415),(b'\xff\xd8\xff',422),
                   (png[:20],422),(png[:33],422),(bytes(oversized_header),413),
                   (b'x'*(MAX_IMAGE_BYTES+1),413)]
            for data,status in cases:
                with self.subTest(data=data[:16],size=len(data)):
                    response=client.post('/api/vision/workspace/image',json={'image_base64':base64.b64encode(data).decode()})
                    self.assertEqual(response.status_code,status,response.text)
                    self.assertIs(response.json()['ok'],False)
            for payload in ({'image_base64':'!!!'},{'path':'C:/private.png'},{'image_base64':'AAAA','url':'http://invalid'}):
                self.assertEqual(client.post('/api/vision/workspace/image',json=payload).status_code,422)
            for content,status in [('not-json',422),('[]',422),('null',422),(' '* (MAX_BODY_BYTES+1),413)]:
                self.assertEqual(client.post('/api/vision/workspace/config',content=content,
                    headers={'content-type':'application/json'}).status_code,status)
            self.assertEqual(client.post('/api/vision/workspace/config',content='{}').status_code,415)
            for error,status in ((OSError('private path'),503),(ImportError('missing'),503),(RuntimeError('secret details'),500)):
                with patch.object(self.engine,'configure',side_effect=error):
                    response=client.post('/api/vision/workspace/config',json={})
                    self.assertEqual(response.status_code,status);self.assertNotIn(str(error),response.text)
            self.assertEqual(client.post('/api/vision/workspace/config',json={}).status_code,200,'slot leaked after error')
        self.assertNotIn('backend.main',sys.modules)
        self.assertNotIn('backend.core.world',sys.modules)


class VirtualFrameTests(unittest.TestCase):
    """GPU output is represented by PNG bytes; no renderer, World or device is imported."""

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        # 来源互斥允许只读状态；虚拟取帧仍不得打开设备或读取物理帧。
        self.camera=unittest.mock.Mock(spec=['open','snapshot','stream_frame'])
        for name in ('open','stream_frame'):
            getattr(self.camera,name).side_effect=AssertionError('virtual input accessed CameraManager.'+name)
        self.camera.snapshot.return_value={'active':None}
        self.engine=VisionWorkspace(self.camera,Path(self.temp.name)/'vision.json')
        self.mount={'offset_mm':[100,0,50],'direction':[400,0,-1000],'fov':55,'width':640,'fps':15}
        self.now=10000.
        clock=patch('backend.core.vision_workspace.time.time',side_effect=lambda:self.now)
        clock.start();self.addCleanup(clock.stop)
        self.engine.configure({'input':'virtual','virtual':copy.deepcopy(self.mount)})

    def tearDown(self):
        self.camera.open.assert_not_called();self.camera.stream_frame.assert_not_called()
        self.assertTrue(all(call[0]=='snapshot' for call in self.camera.mock_calls))
        self.assertNotIn('backend.main',sys.modules)
        self.assertNotIn('backend.core.world',sys.modules)

    def payload(self,**patches):
        return {'session':self.engine.snapshot()['virtual_session'],
                'image_base64':base64.b64encode(fixture()).decode(),
                'view_key':'end-effector:pose-A','captured_at':self.now,**patches}

    def feed(self,**patches):
        response=self.engine.virtual_frame(self.payload(**patches))
        self.assertIs(response['ok'],True)
        self.engine.process()
        return self.engine.snapshot()['result']

    def test_virtual_settings_round_trip_and_session_state(self):
        state=self.engine.snapshot()
        self.assertEqual(state['config']['input'],'virtual')
        self.assertEqual(state['config']['virtual'],self.mount)
        self.assertIsInstance(state['virtual_session'],str)
        self.assertTrue(state['virtual_session'])
        state['config']['virtual']['offset_mm'][0]=999
        self.assertEqual(self.engine.config['virtual'],self.mount,'snapshot exposed mutable configuration')
        restarted=VisionWorkspace(self.camera,self.engine.path)
        self.assertEqual(restarted.config['virtual'],self.mount)
        self.assertEqual(restarted.config['input'],'virtual')
        self.assertNotEqual(restarted.snapshot()['virtual_session'],self.engine.virtual_session)
        self.assertIsNone(restarted._input(),'virtual frames must not survive restart')

    def test_virtual_settings_reject_invalid_full_mount_atomically(self):
        before=copy.deepcopy(self.engine.snapshot())
        invalid=[None,{}, {'fov':55}, {**self.mount,'unknown':1}]
        for field,values in {
            'offset_mm':([1,2],[0,0,1001],[True,0,0],[float('nan'),0,0],'100,0,50'),
            'direction':([0,0,0],[0,0,10001],[0,float('inf'),1],[0,0]),
            'fov':(True,19.9,100.1,float('nan'),'55'),
            'width':(True,640.,0,641,2048),
            'fps':(True,15.,0,31,'15'),
        }.items():
            invalid.extend({**self.mount,field:value} for value in values)
        for mount in invalid:
            with self.subTest(mount=mount),self.assertRaises(ValueError):
                self.engine.configure({'virtual':mount})
            self.assertEqual(self.engine.snapshot(),before,'bad config changed state/session')
        for width in (320,640,960):
            state=self.engine.configure({'virtual':{**self.mount,'width':width}})
            self.assertEqual(state['config']['virtual']['width'],width)

    def test_virtual_frame_real_opencv_colors_shapes_and_source(self):
        result=self.feed()
        self.assertEqual(result['source'],'virtual')
        self.assertIn(self.engine.virtual_session,result['source_key'])
        self.assertNotIn('end-effector:pose-A',result['source_key'],'view key must be hashed')
        self.assertEqual(result['captured_at'],self.now)
        self.assertEqual((result['image']['width'],result['image']['height']),(640,480))
        values={v['color']:v for v in result['objects']}
        self.assertEqual({c:v['shape'] for c,v in values.items()},
                         {'red':'rectangle','green':'circle','blue':'triangle'})
        self.assertAlmostEqual(values['red']['center_px'][0],150,delta=1)
        self.assertEqual(sorted(round(v) for v in values['red']['size_px']),[80,100])
        self.assertTrue(all(v['mapped'] is None for v in values.values()))
        self.assertFalse(result['calibrated'])

    def test_latest_packet_only_is_analyzed_without_queue_or_per_frame_disk_write(self):
        stamp=self.engine.path.stat().st_mtime_ns
        packets=[self.payload(image_base64=base64.b64encode(fixture(offset)).decode()) for offset in (0,5,12)]
        for payload in packets:self.engine.virtual_frame(payload)
        self.assertIsNone(self.engine.latest,'ingestion should retain a packet for process, not run detection')
        with patch.object(self.engine,'analyze',wraps=self.engine.analyze) as analyze:
            self.engine.process();self.engine.process()
            self.assertEqual(analyze.call_count,1)
            self.assertEqual(analyze.call_args.args[0],fixture(12),'older packets were queued or retained')
        result=self.engine.latest
        self.assertEqual(result['frame_seq'],3)
        self.assertAlmostEqual(next(v for v in result['objects'] if v['color']=='red')['center_px'][0],162,delta=1)
        self.assertEqual(self.engine.path.stat().st_mtime_ns,stamp)

    def test_planar_calibration_is_bound_to_virtual_view(self):
        first=self.feed()
        self.engine.calibrate({'points':POINTS,'plane_z':100});self.engine.process()
        calibrated=self.engine.snapshot()['result']
        self.assertTrue(calibrated['calibrated'])
        red=next(v for v in calibrated['objects'] if v['color']=='red')
        for actual,expected in zip(red['mapped']['xyz_mm'],[-90,-20,110]):
            self.assertAlmostEqual(actual,expected,delta=1)
        same=self.feed(image_base64=base64.b64encode(fixture(2)).decode())
        self.assertEqual(same['source_key'],first['source_key'])
        self.assertTrue(same['calibrated'],'new pixels in unchanged view should retain calibration')
        old_ids={v['id'] for v in same['objects']}
        changed=self.feed(view_key='end-effector:pose-B')
        self.assertNotEqual(changed['source_key'],first['source_key'])
        self.assertFalse(changed['calibrated'])
        self.assertTrue(all(v['mapped'] is None for v in changed['objects']))
        self.assertTrue(old_ids.isdisjoint(v['id'] for v in changed['objects']),'tracks leaked between views')

    def test_mount_fov_and_input_changes_rotate_session_and_reject_old_packets(self):
        for update in ({'offset_mm':[110,0,50]},{'direction':[401,0,-1000]},
                       {'fov':60},{'width':320},{'fps':20}):
            with self.subTest(update=update):
                self.feed();self.engine.calibrate({'points':POINTS,'plane_z':0});self.engine.process()
                old=self.payload();old_key=self.engine.latest['source_key']
                state=self.engine.configure({'virtual':{**self.engine.config['virtual'],**update}})
                self.assertNotEqual(state['virtual_session'],old['session'])
                self.assertIsNone(self.engine._input());self.assertIsNone(state['result'])
                with self.assertRaises(ValueError):self.engine.virtual_frame(old)
                result=self.feed()
                self.assertNotEqual(result['source_key'],old_key)
                self.assertFalse(result['calibrated'])
                self.assertTrue(all(v['mapped'] is None for v in result['objects']))
        old=self.payload();self.engine.configure({'input':'image'})
        with self.assertRaises(ValueError):self.engine.virtual_frame(old)
        self.engine.configure({'input':'virtual'})
        self.assertNotEqual(self.engine.virtual_session,old['session'])

    def test_rejected_metadata_keeps_last_valid_packet(self):
        self.feed();before=copy.deepcopy(self.engine.virtual_data);seq=self.engine.virtual_seq
        bad=[None,[],{}, {**self.payload(),'url':'http://invalid'},
             {key:value for key,value in self.payload().items() if key!='view_key'}]
        for field,values in {
            'session':('obsolete-session',None,True,[]),
            'view_key':('',None,123,'x'*513),
            'captured_at':(True,None,'10000',float('nan'),float('inf'),-1,self.now-1.501,self.now+.001),
            'image_base64':(None,[],123,'!!!'),
        }.items():bad.extend(self.payload(**{field:value}) for value in values)
        for payload in bad:
            with self.subTest(payload_type=type(payload).__name__,fields=list(payload) if isinstance(payload,dict) else None):
                with self.assertRaises((ValueError,TypeError)):self.engine.virtual_frame(payload)
                self.assertEqual(self.engine.virtual_data,before)
                self.assertEqual(self.engine.virtual_seq,seq)

    def test_expiry_at_ingress_input_and_snapshot_cannot_refresh_old_frame(self):
        self.feed();self.now+=1.501
        stale=self.engine.snapshot()
        self.assertTrue(stale['stale'])
        self.assertTrue(all(v['status']=='expired' for v in stale['result']['objects']))
        with self.assertRaises(ValueError):self.engine.virtual_frame(self.payload(captured_at=self.now-1.501))
        self.assertIsNone(self.engine._input())
        with patch.object(self.engine,'analyze',side_effect=AssertionError('expired frame was analyzed')):
            self.engine.process()
        self.assertIsNone(self.engine.latest);self.assertFalse(self.engine.tracks)

    def test_disabled_virtual_source_rejects_frames_and_clears_output(self):
        self.feed();self.engine.configure({'enabled':False})
        with self.assertRaises(ValueError):self.engine.virtual_frame(self.payload())
        self.engine.process();self.assertIsNone(self.engine.latest)

    def test_native_analysis_rechecks_view_and_capture_expiry(self):
        analyze=self.engine.analyze
        for reason in ('view','expired','mount'):
            with self.subTest(reason=reason):
                self.feed();self.engine.calibrate({'points':POINTS,'plane_z':0})
                def changed(*args):
                    result=analyze(*args)
                    if reason=='view':self.engine.virtual_frame(self.payload(view_key='moving-view'))
                    elif reason=='expired':self.now+=1.501
                    else:self.engine.configure({'virtual':{**self.mount,'fov':65}})
                    return result
                with patch.object(self.engine,'analyze',side_effect=changed):self.engine.process()
                self.assertTrue(self.engine.snapshot()['result'] is None,
                                'invalid virtual view/frame completed analysis and was published')
                self.assertFalse(self.engine.tracks)

    def test_virtual_route_contract_limits_and_real_detection(self):
        app=FastAPI();app.include_router(create_workspace_vision_router(self.engine))
        route='/api/vision/workspace/virtual'
        with TestClient(app) as client:
            state=client.get('/api/vision/workspace/state').json()
            self.assertEqual(state['virtual_session'],self.engine.virtual_session)
            response=client.post(route,json=self.payload())
            self.assertEqual(response.status_code,200);self.assertIs(response.json()['ok'],True)
            self.engine.process()
            result=client.get('/api/vision/workspace/state').json()['result']
            self.assertEqual(result['source'],'virtual');self.assertEqual(len(result['objects']),3)
            self.assertEqual({v['shape'] for v in result['objects']},{'rectangle','circle','triangle'})
            for payload in (self.payload(session='old'),self.payload(captured_at=self.now-2),
                            self.payload(view_key=''),{**self.payload(),'path':'private.png'},
                            self.payload(image_base64='!!!')):
                response=client.post(route,json=payload)
                self.assertEqual(response.status_code,422);self.assertIs(response.json()['ok'],False)
            oversized=bytearray(fixture()[:33]);oversized[16:20]=(4096).to_bytes(4,'big')
            for data,status in ((b'',413),(b'not-an-image',415),(b'\xff\xd8\xff',422),
                                (bytes(oversized),413),(b'x'*(MAX_IMAGE_BYTES+1),413)):
                with self.subTest(image_size=len(data)):
                    response=client.post(route,json=self.payload(image_base64=base64.b64encode(data).decode()))
                    self.assertEqual(response.status_code,status,response.text)
                    self.assertIs(response.json()['ok'],False)
            response=client.post(route,content=' '*(MAX_BODY_BYTES+1),headers={'content-type':'application/json'})
            self.assertEqual(response.status_code,413)
            self.assertEqual(client.post(route,content='{}').status_code,415)
            self.assertEqual(client.post(route,content='null',headers={'content-type':'application/json'}).status_code,422)
            self.assertEqual(client.post(route,json=self.payload()).status_code,200,'invalid frames leaked route slot')


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():await asyncio.sleep(.005)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_waits_for_native_worker_and_is_idempotent(self):
        camera=Camera();camera.active={'session':'offline','state':'streaming'}
        camera.packet=(1,time.time(),fixture())
        engine=VisionWorkspace(camera)
        entered,release,finished=threading.Event(),threading.Event(),threading.Event()
        analyze=engine.analyze
        def slow(*args):
            entered.set()
            try:
                if not release.wait(3):raise AssertionError('test worker release timed out')
                return analyze(*args)
            finally:finished.set()
        with patch.object(engine,'analyze',side_effect=slow):
            close=None
            try:
                await engine.start();task=engine.task;await engine.start()
                self.assertIs(engine.task,task)
                await wait_until(entered.is_set)
                close=asyncio.create_task(engine.close());await asyncio.sleep(.03)
                self.assertFalse(close.done(),'close returned while native work was still running')
                self.assertTrue(engine.operation.locked())
                release.set();await asyncio.wait_for(close,3)
                self.assertTrue(finished.is_set());self.assertFalse(engine.operation.locked())
                self.assertIsNone(engine.task);await engine.close()
            finally:
                release.set()
                if close:await asyncio.wait_for(close,3)
                await engine.close()

    async def test_cancelled_http_request_keeps_slot_until_worker_really_finishes(self):
        engine=VisionWorkspace(Camera())
        entered,release,finished=threading.Event(),threading.Event(),threading.Event()
        def slow(value):
            entered.set()
            try:
                if not release.wait(3):raise AssertionError('test worker release timed out')
                return {'ok':True}
            finally:finished.set()
        with patch.object(engine,'configure',side_effect=slow):
            app=FastAPI();app.include_router(create_workspace_vision_router(engine))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                request=asyncio.create_task(client.post('/api/vision/workspace/config',json={}))
                try:
                    await wait_until(entered.is_set);request.cancel()
                    with self.assertRaises(asyncio.CancelledError):await request
                    response=await client.post('/api/vision/workspace/config',json={})
                    self.assertEqual(response.status_code,429,'cancel released the native worker slot early')
                    self.assertEqual((await client.get('/api/vision/workspace/state')).status_code,200)
                    release.set();await wait_until(finished.is_set)
                    # The worker completion callback, not cancellation, releases the slot.
                    for _ in range(100):
                        response=await client.post('/api/vision/workspace/config',json={})
                        if response.status_code!=429:break
                        await asyncio.sleep(.005)
                    self.assertEqual(response.status_code,200)
                finally:
                    release.set()
                    if not request.done():
                        request.cancel()
                        try:await request
                        except asyncio.CancelledError:pass
                    await wait_until(finished.is_set)

if __name__=='__main__':unittest.main(verbosity=2)
