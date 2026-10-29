"""Metadata, stale data and SDK projection contracts, without fabricating UI frames."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.cameras import CameraManager

class Frames(unittest.TestCase):
    def test_starting_timeout_is_visible_without_claiming_capture_stopped(self):
        m = CameraManager()
        m._active = {'session':'session', 'state':'starting', 'opened_at':time.time()-20}
        state = m.snapshot()['active']
        self.assertEqual(state['state'], 'error')
        self.assertIn('10', state['error'])
        self.assertEqual(m._active['state'], 'starting')
    def manager(self):
        m = CameraManager()
        m._active = {'session':'session', 'state':'streaming', 'last_frame_at':time.time()}
        m._images = {'color':b'frame'}
        m._image_stamps = {'color': time.time()}
        m._depth_at = time.time()
        return m
    def test_wrong_session_and_stale_frames_are_unavailable(self):
        m = self.manager()
        self.assertEqual(m.image('color','session'),b'frame')
        self.assertIsNone(m.image('color','wrong'))
        m._image_stamps['color'] -= 10
        self.assertIsNone(m.image('color','session'))
    def test_error_state_never_serves_last_picture(self):
        m = self.manager(); m._active['state']='error'
        self.assertIsNone(m.image('color','session'))
    def test_pointcloud_returns_independent_data_and_rejects_stale_data(self):
        m = self.manager(); m._cloud = {'xyz_m':[[0,0,1]],'timestamp':time.time()}
        r = m.pointcloud('session'); r['xyz_m'][0][2]=9
        self.assertEqual(m._cloud['xyz_m'][0][2],1)
        self.assertFalse(m.pointcloud('wrong')['ok'])
        m._cloud['timestamp']-=10; self.assertFalse(m.pointcloud('session')['ok'])
    def test_close_discards_data_and_session(self):
        m=self.manager(); self.assertTrue(m.close()['ok']); self.assertIsNone(m.image('color','session'))
        self.assertFalse(m.pointcloud('session')['ok'])
    def test_depth_deprojection_uses_sdk_intrinsics_and_scale(self):
        import numpy as np
        import pyrealsense2 as rs
        m=self.manager(); intr = rs.intrinsics(); intr.width=2; intr.height=2; intr.fx=1; intr.fy=1; intr.ppx=0; intr.ppy=0; intr.model=rs.distortion.none; intr.coeffs=[0]*5
        m._intrinsics=intr; m._scale=.001; m._depth=np.array([[1000,0],[1000,1000]],dtype=np.uint16)
        r=m.measure(1,1,'session'); self.assertTrue(r['ok']); self.assertEqual(r['xyz_mm'],[1000,1000,1000]); self.assertEqual(r['distance_mm'],1000)
        self.assertFalse(m.measure(1,0,'session')['ok']); self.assertFalse(m.measure(2,2,'session')['ok']); self.assertFalse(m.measure(0,0,'wrong')['ok'])
        m._depth_at-=10; self.assertFalse(m.measure(0,0,'session')['ok'])
    def test_empty_stream_request_does_not_open_hardware(self):
        m=CameraManager(); m._last_scan=[{'id':'usb:test','kind':'usb','streams':['color']}]
        with patch.object(m,'close',side_effect=AssertionError('Should not open')):
            self.assertFalse(m.open('usb:test',[])['ok'])
            self.assertFalse(m.open('usb:test',['depth'])['ok'])

    def test_target_validation_never_opens_or_scans_devices(self):
        m = CameraManager()
        with patch.object(m, 'scan', side_effect=AssertionError('hardware scan forbidden')):
            for fps in (0, 61, True, '60', 30.5, None):
                self.assertFalse(m.open('missing', ['color'], fps)['ok'])

    def test_sdk_selection_uses_only_advertised_profiles_and_bgr_rgb(self):
        rows = [dict(stream='color', index=0, fps=fps, format='format.' + fmt, width=w, height=h)
                for fps, fmt, w, h in [(60,'yuyv',640,480), (30,'rgb8',640,480),
                                      (6,'bgr8',424,240), (60,'bgr8',640,480)]]
        d = {'usb': '3.2', 'profiles': rows}
        self.assertEqual(CameraManager._select_profiles(d, ['color'], 60)[0], rows[-1])
        self.assertEqual(CameraManager._select_profiles(d, ['color'], 25)[0], rows[2])
        d['usb'] = '2.1'
        self.assertEqual(CameraManager._select_profiles(d, ['color'], 60)[0], rows[2])
        self.assertTrue(all(p in rows for p in CameraManager._select_profiles(d, ['color'], 1)))
        with self.assertRaises(ValueError): CameraManager._select_profiles(d, ['depth'], 60)
        with self.assertRaises(ValueError): CameraManager._select_profiles({'profiles': rows[:1]}, ['color'], 60)

    def test_installed_sdk_accepts_explicit_video_and_motion_overloads_without_hardware(self):
        import pyrealsense2 as rs
        cfg = rs.config()
        cfg.enable_stream(rs.stream.color,0,424,240,rs.format.bgr8,6)
        cfg.enable_stream(rs.stream.gyro,0,rs.format.motion_xyz32f,200)

    def test_alias_requires_unique_physical_parent_not_name_or_vid(self):
        def sdk(n):
            return dict(id='sdk:' + n, kind='realsense', name='D435i', physical_port='sdkport:' + n,
                        streams=['color','depth'], profiles=[])
        def uvc(n):
            return dict(id='uvc:' + n, kind='usb', name='D435i Depth', path='uvcpath:' + n,
                        vid=0x8086, streams=['color'])
        devices = [sdk('a'), sdk('b'), uvc('a'), uvc('b'), uvc('unknown')]
        mapping = {'sdkport:a':'physical:a', 'sdkport:b':'physical:b',
                   'uvcpath:a':'physical:a', 'uvcpath:b':'physical:b'}
        CameraManager._link_sdk_aliases(devices, mapping)
        self.assertEqual(devices[2]['alias_of'], 'sdk:a')
        self.assertEqual(devices[3]['alias_of'], 'sdk:b')
        self.assertIn('depth', devices[2]['streams'])
        self.assertNotIn('alias_of', devices[4])
        self.assertIn('open_error', devices[4])
        ambiguous = [sdk('a'), sdk('b'), uvc('a')]
        CameraManager._link_sdk_aliases(ambiguous, {**mapping, 'sdkport:b':'physical:a'})
        self.assertNotIn('alias_of', ambiguous[2])
        self.assertIn('open_error', ambiguous[2])

    def test_windows_identity_preserves_interface_and_complete_instance(self):
        path = r'\\?\usb#vid_8086&pid_0b3a&mi_03#7&abc&0&0003#{guid}\global'
        self.assertEqual(CameraManager._windows_instance(path), r'usb\vid_8086&pid_0b3a&mi_03\7&abc&0&0003')
        self.assertIsNone(CameraManager._windows_instance('D435i RGB'))

    def test_missing_video_errors_even_if_imu_keeps_arriving(self):
        with self.assertRaisesRegex(RuntimeError, 'depth'):
            CameraManager._check_stream_timeout({'gyro','color','depth'}, {'gyro':6,'color':6}, 0, 6)

    def test_actual_fps_counts_hardware_arrivals_not_driver_target_or_encoding(self):
        m = self.manager()
        m._active.update(negotiated_fps={'color':60, 'depth':6}, target_fps=60)
        with patch('backend.core.cameras.time.monotonic', return_value=10):
            for t in (9,9.25,9.5,9.75,10): m._record_frames(['color'], t)
            active = m.snapshot()['active']
            self.assertEqual(active['actual_fps'], 4)
            self.assertEqual(active['actual_fps_by_stream']['depth'], 0)
            self.assertEqual(active['frame_counts']['color'], 5)
        with patch('backend.core.cameras.time.monotonic', return_value=13):
            self.assertEqual(m.snapshot()['active']['actual_fps'], 0)

    def test_cache_reuses_same_bytes_and_sequence_until_new_encoded_frame(self):
        m = self.manager(); m._image_seq['color'] = 3
        a = m.stream_frame('color','session'); b = m.stream_frame('color','session')
        self.assertEqual(a[0], 3); self.assertIs(a[2], b[2])
        m._image_stamps['color'] -= 4
        self.assertIsNone(m.stream_frame('color','session'))

    def test_pointcloud_is_requested_only_by_valid_depth_consumer(self):
        m = self.manager(); m._active['streams'] = ['color','depth']
        self.assertEqual(m._cloud_requested_until, 0)
        m.pointcloud('wrong'); self.assertEqual(m._cloud_requested_until, 0)
        self.assertFalse(m.pointcloud('session')['ok'])
        self.assertGreater(m._cloud_requested_until, time.monotonic())
        m.close(); self.assertEqual(m._cloud_requested_until, 0)

    def test_capture_encodes_above_ten_fps_once_for_all_consumers(self):
        import numpy as np
        m = self.manager(); stop = threading.Event(); m._stop = stop
        m._active.update(target_fps=60, streams=['color'], images={}, imu={}, seq=0,
                         negotiated_fps={'color':None})
        count = [0]
        def read():
            count[0] += 1
            if count[0] > 12: stop.set()
            return True, np.zeros((2,3,3),dtype=np.uint8)
        handle = SimpleNamespace(read=read, release=MagicMock())
        cv = SimpleNamespace(IMWRITE_JPEG_QUALITY=1,
            imencode=MagicMock(return_value=(True, np.array([1,2,3],dtype=np.uint8))))
        with patch.object(m, '_import_cv2', return_value=cv), patch.object(m, '_import_rs', return_value=None), \
             patch('backend.core.cameras.time.monotonic', side_effect=lambda: count[0]/50):
            m._capture({'kind':'usb'}, handle, ['color'], stop)
        self.assertEqual(m._frame_counts['color'], 12)
        self.assertGreaterEqual(cv.imencode.call_count, 12)
        self.assertEqual(m._active['state'], 'streaming')
        handle.release.assert_called_once()

    def test_usb_driver_fps_is_not_claimed_as_negotiated_or_measured(self):
        m = CameraManager()
        m._last_scan = [dict(id='usb:test',kind='usb',name='test',number=1,index=0,
                            capture_backend=1,streams=['color'])]
        handle = SimpleNamespace(isOpened=lambda: True, get=lambda _:60, release=MagicMock())
        cv = SimpleNamespace(VideoCapture=MagicMock(return_value=handle), CAP_PROP_FPS=5)
        with patch.object(m, '_import_cv2', return_value=cv), patch.object(m, '_capture'), \
             patch('backend.core.cameras.threading.Thread'):
            r = m.open('usb:test', ['color'], 60)
        self.assertTrue(r['ok']); active = r['active']
        self.assertEqual(active['driver_reported_fps'], 60)
        self.assertIsNone(active['negotiated_fps']['color'])
        self.assertEqual(active['actual_fps'], 0)

    def test_usb_requests_mjpg_and_fps_without_claiming_hardware_success(self):
        handle = SimpleNamespace(set=MagicMock(return_value=True),
            get=lambda key: {5:30,6:1196444237,3:640,4:480}.get(key,0))
        cv = SimpleNamespace(CAP_PROP_FPS=5,CAP_PROP_FOURCC=6,CAP_PROP_FRAME_WIDTH=3,
            CAP_PROP_FRAME_HEIGHT=4,CAP_PROP_BUFFERSIZE=38,
            VideoWriter_fourcc=lambda *chars: sum(ord(c) << (8*i) for i,c in enumerate(chars)))
        result = CameraManager._configure_usb(handle,cv,60)
        handle.set.assert_any_call(5,60)
        handle.set.assert_any_call(6,cv.VideoWriter_fourcc(*'MJPG'))
        self.assertNotIn(3,[call.args[0] for call in handle.set.call_args_list])
        self.assertNotIn(4,[call.args[0] for call in handle.set.call_args_list])
        self.assertEqual(result['driver_reported_fps'],30)
        self.assertEqual(result['requested_fps'],60)
        self.assertTrue(result['fps_request_accepted'])
        self.assertIsNone(result['negotiated_fps']['color'])

    def test_usb_unsupported_requests_do_not_abort_existing_camera(self):
        handle = SimpleNamespace(set=MagicMock(side_effect=RuntimeError('unsupported')),get=lambda _:0)
        cv = SimpleNamespace(CAP_PROP_FPS=5,CAP_PROP_FOURCC=6,VideoWriter_fourcc=lambda *_:1196444237)
        result = CameraManager._configure_usb(handle,cv,60)
        self.assertFalse(result['fps_request_accepted'])
        self.assertFalse(result['format_request_accepted'])
        self.assertIsNone(result['driver_reported_fps'])

    def test_usb_sixty_rejected_can_request_thirty_without_claiming_sixty(self):
        handle = SimpleNamespace(set=lambda prop,value: prop == 5 and value == 30,get=lambda _:30)
        result = CameraManager._configure_usb(handle,SimpleNamespace(CAP_PROP_FPS=5),60)
        self.assertEqual(result['requested_fps'],60)
        self.assertFalse(result['fps_request_accepted'])
        self.assertEqual(result['fallback_fps_request'],30)
        self.assertEqual(result['driver_reported_fps'],30)
        self.assertIsNone(result['negotiated_fps']['color'])

    def test_dshow_fps_change_cannot_silently_reset_the_requested_format(self):
        mjpg = 1196444237
        props = {6:mjpg,5:30}
        def set_property(key,value):
            props[key] = value
            if key == 5:
                props[6] = 844715353  # Driver reset to YUY2 after FPS update.
            return True
        handle = SimpleNamespace(set=set_property,get=lambda key:props.get(key,0))
        cv = SimpleNamespace(CAP_PROP_FPS=5,CAP_PROP_FOURCC=6,VideoWriter_fourcc=lambda *_:mjpg)
        result = CameraManager._configure_usb(handle,cv,60)
        self.assertEqual(result['driver_reported_format'],'MJPG')
        self.assertEqual(result['driver_reported_fps'],60)
        self.assertIsNone(result['negotiated_fps']['color'])

    def test_delivered_fps_and_capture_cost_are_separate_from_sensor_fps(self):
        from collections import deque
        m = self.manager()
        m._active.update(negotiated_fps={'color':60})
        m._frame_times['color'] = deque([9,9.5,10])
        m._image_times['color'] = deque([9,10])
        m._capture_costs.extend([(40,2),(50,4)])
        with patch('backend.core.cameras.time.monotonic',return_value=10):
            active = m.snapshot()['active']
        self.assertEqual(active['actual_fps'],2)
        self.assertEqual(active['preview_fps_by_stream']['color'],1)
        self.assertEqual(active['capture_timing_ms'],{'read_mean':45,'encode_mean':3})

    def test_sdk_open_alias_uses_exact_advertised_profile_and_serial(self):
        m = CameraManager()
        row = dict(stream='color',index=0,format='format.bgr8',fps=6,width=424,height=240)
        sdk = dict(id='sdk:a',kind='realsense',name='D435i',number=1,serial='serial-a',
                   streams=['color'],profiles=[row],usb='2.1')
        alias = dict(id='usb:rgb',kind='usb',name='D435i RGB',number=2,
                     alias_of='sdk:a',streams=['color'])
        m._last_scan = [sdk,alias]
        video = SimpleNamespace(width=lambda:424,height=lambda:240)
        p = SimpleNamespace(stream_type=lambda:'stream.color',fps=lambda:6,stream_index=lambda:0,
                            format=lambda:'format.bgr8',is_video_stream_profile=lambda:True,
                            as_video_stream_profile=lambda:video)
        profile = SimpleNamespace(get_streams=lambda:[p])
        cfg = MagicMock(); handle = MagicMock(); handle.start.return_value = profile
        rs = SimpleNamespace(pipeline=lambda:handle,config=lambda:cfg,
                             stream=SimpleNamespace(color='COLOR'),format=SimpleNamespace(bgr8='BGR8'))
        with patch.object(m,'_import_rs',return_value=rs), patch.object(m,'_import_cv2',return_value=MagicMock()), \
             patch('backend.core.cameras.threading.Thread'):
            result = m.open('usb:rgb',['color'],60)
        self.assertTrue(result['ok'])
        cfg.enable_device.assert_called_once_with('serial-a')
        cfg.enable_stream.assert_called_once_with('COLOR',0,424,240,'BGR8',6)
        self.assertEqual(result['active']['id'],'usb:rgb')
        self.assertEqual(result['active']['physical_device_id'],'sdk:a')
        self.assertEqual(result['active']['negotiated_fps'],{'color':6})

    def test_sdk_capture_deduplicates_frame_numbers_and_never_builds_unused_cloud(self):
        import numpy as np
        m = self.manager(); stop = threading.Event(); m._stop = stop
        m._active.update(target_fps=60,streams=['depth'],images={},imu={},seq=0,
                         negotiated_fps={'depth':30},depth_scale_m=.001)
        vp = SimpleNamespace(get_intrinsics=lambda:None)
        frame = SimpleNamespace(get_frame_number=lambda:1,get_timestamp=lambda:100,
            get_data=lambda:np.ones((2,3),dtype=np.uint16),
            profile=SimpleNamespace(as_video_stream_profile=lambda:vp))
        class FrameSet:
            def __iter__(self): return iter(())
            def get_color_frame(self): return None
            def get_depth_frame(self): return frame
            def get_infrared_frame(self, channel): return None
        count = [0]
        def poll():
            count[0] += 1
            if count[0] > 5: stop.set()
            return FrameSet()
        handle = SimpleNamespace(poll_for_frames=poll,stop=MagicMock())
        colorizer = SimpleNamespace(colorize=lambda _:SimpleNamespace(get_data=lambda:np.zeros((2,3,3),dtype=np.uint8)))
        rs = SimpleNamespace(colorizer=lambda:colorizer,pointcloud=MagicMock())
        cv = SimpleNamespace(COLOR_RGB2BGR=1,IMWRITE_JPEG_QUALITY=1,cvtColor=lambda v,_:v,
             imencode=MagicMock(return_value=(True,np.array([1,2],dtype=np.uint8))))
        with patch.object(m,'_import_rs',return_value=rs), patch.object(m,'_import_cv2',return_value=cv):
            m._capture({'kind':'realsense'},handle,['depth'],stop)
        self.assertEqual(m._frame_counts,{'depth':1})
        cv.imencode.assert_called_once(); rs.pointcloud.assert_not_called()
        handle.stop.assert_called_once()

    def test_encoding_failure_clears_cached_data_and_reports_error(self):
        import numpy as np
        m = self.manager(); stop = threading.Event(); m._stop = stop
        m._active.update(target_fps=60,streams=['color'],images={},imu={},seq=0,
                         negotiated_fps={'color':None})
        cv = SimpleNamespace(IMWRITE_JPEG_QUALITY=1,imencode=lambda *a:(False,None))
        handle = SimpleNamespace(read=lambda:(True,np.zeros((2,3,3),dtype=np.uint8)),release=MagicMock())
        with patch.object(m,'_import_cv2',return_value=cv),patch.object(m,'_import_rs',return_value=None):
            m._capture({'kind':'usb'},handle,['color'],stop)
        self.assertEqual(m._active['state'],'error')
        self.assertIn('编码失败',m._active['error'])
        self.assertFalse(m._images)
        self.assertIsNone(m.image('color','session'))

    def test_encoded_frame_timestamp_includes_processing_latency_not_publication_time(self):
        import numpy as np
        m = self.manager(); stop = threading.Event(); m._stop = stop
        m._active.update(target_fps=60, streams=['color'], images={}, imu={}, seq=0,
                         negotiated_fps={'color': None})
        clock, count = [1000.0], [0]
        def read():
            count[0] += 1
            clock[0] += .06
            if count[0] > 1: stop.set()
            return True, np.zeros((2, 3, 3), dtype=np.uint8)
        def encode(*args):
            clock[0] += .4
            return True, np.array([1, 2, 3], dtype=np.uint8)
        cv = SimpleNamespace(IMWRITE_JPEG_QUALITY=1, imencode=encode)
        handle = SimpleNamespace(read=read, release=MagicMock())
        with patch.object(m, '_import_cv2', return_value=cv), patch.object(m, '_import_rs', return_value=None), \
             patch('backend.core.cameras.time.time', side_effect=lambda: clock[0]), \
             patch('backend.core.cameras.time.monotonic', side_effect=lambda: clock[0]), \
             patch('backend.core.cameras.time.perf_counter', side_effect=lambda: clock[0]):
            m._capture({'kind': 'usb'}, handle, ['color'], stop)
        self.assertAlmostEqual(m._image_stamps['color'], 1000.06)
        self.assertGreater(clock[0] - m._image_stamps['color'], .3,
                           'A slow JPEG encoder must not make an old observation newly fresh')
        self.assertEqual(m._image_seq['color'], 1)
        handle.release.assert_called_once()


class Mjpeg(unittest.IsolatedAsyncioTestCase):
    async def test_multipart_latest_frame_no_duplicates_and_session_end(self):
        from backend import main
        m = CameraManager(); m._active = {'state':'streaming','session':'test'}
        m._images['color'] = b'first'; m._image_stamps['color'] = time.time(); m._image_seq['color'] = 1
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        with patch.object(main, 'cameras', m):
            response = await main.camera_stream(request, 'color', 'test', 60)
            self.assertEqual(response.headers['content-encoding'], 'identity')
            self.assertIn('boundary=frame', response.media_type)
            first = await anext(response.body_iterator)
            self.assertIn(b'X-Frame-Seq: 1\r\n', first)
            self.assertTrue(first.endswith(b'first\r\n'))
            pending = asyncio.create_task(anext(response.body_iterator))
            await asyncio.sleep(.04)
            self.assertFalse(pending.done(), 'must not repeat cached frames')
            # The slow consumer sees the latest value, never the overwritten frame 2.
            m._images['color'] = b'skipped'; m._image_seq['color'] = 2
            m._images['color'] = b'latest'; m._image_seq['color'] = 3
            m._image_stamps['color'] = time.time()
            latest = await asyncio.wait_for(pending, .5)
            self.assertIn(b'X-Frame-Seq: 3\r\n', latest)
            self.assertTrue(latest.endswith(b'latest\r\n'))
            m._active['session'] = 'new-session'
            with self.assertRaises(StopAsyncIteration): await anext(response.body_iterator)

    async def test_mjpeg_rejects_stale_wrong_session_and_terminates_on_close(self):
        from backend import main
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        m = Frames().manager()
        with patch.object(main, 'cameras', m):
            self.assertEqual((await main.camera_stream(request,'color','wrong',60)).status_code, 503)
            m._image_stamps['color'] -= 4
            self.assertEqual((await main.camera_stream(request,'color','session',60)).status_code, 503)
            m._image_stamps['color'] = time.time()
            response = await main.camera_stream(request,'color','session',60)
            await anext(response.body_iterator)
            m.close()
            with self.assertRaises(StopAsyncIteration): await anext(response.body_iterator)

    async def test_http_contract_parameter_validation_and_jpeg_compatibility(self):
        import httpx
        from backend import main
        m = Frames().manager()
        with patch.object(main, 'cameras', m):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1:8710') as client:
                for fps in (-1,1001,'invalid'):
                    r = await client.get(f'/api/cameras/stream.mjpg?session=session&stream=color&fps={fps}')
                    self.assertEqual(r.status_code, 422)
                r = await client.get('/api/cameras/frame.jpg?session=session&stream=color')
                self.assertEqual(r.content,b'frame'); self.assertEqual(r.headers['content-type'],'image/jpeg')
                packet = m.stream_frame('color','session')
                with patch.object(m,'stream_frame',side_effect=[packet,packet,None]):
                    r = await client.get('/api/cameras/stream.mjpg?session=session&stream=color&fps=60',
                                         headers={'Accept-Encoding':'gzip'})
                self.assertEqual(r.status_code,200)
                self.assertIn('boundary=frame',r.headers['content-type'])
                self.assertEqual(r.headers['content-encoding'],'identity')
                self.assertEqual(r.content.count(b'Content-Type: image/jpeg'),1)
                self.assertIn(b'\r\n\r\nframe\r\n',r.content)
                r = await client.post('/api/cameras/open',json={'camera_id':'none','streams':['color','invalid']})
                self.assertFalse(r.json()['ok'])

    async def test_automatic_delivery_does_not_throttle_120_new_frames_or_repeat_old(self):
        from backend import main
        request=SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        m=Frames().manager()
        with patch.object(main,'cameras',m):
            response=await main.camera_stream(request,'color','session',0)
            for seq in range(1,121):
                m._images['color']=b'new';m._image_seq['color']=seq
                m._image_stamps['color']=time.time()
                payload=await asyncio.wait_for(anext(response.body_iterator),.1)
                self.assertIn(f'X-Frame-Seq: {seq}\r\n'.encode(),payload)
            await response.body_iterator.aclose()

    async def test_http_zero_and_default_are_unlimited_but_optional_positive_throttle_remains(self):
        import httpx
        from backend import main
        m=Frames().manager()
        with patch.object(main,'cameras',m):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://127.0.0.1:8710') as client:
                packet=m.stream_frame('color','session')
                for query in ('','&fps=0','&fps=120'):
                    with patch.object(m,'stream_frame',side_effect=[packet,packet,None]):
                        response=await client.get('/api/cameras/stream.mjpg?session=session&stream=color'+query)
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.content.count(b'Content-Type: image/jpeg'),1)
if __name__ == '__main__': unittest.main()
