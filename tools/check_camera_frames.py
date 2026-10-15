"""Metadata, stale data and SDK projection contracts, without fabricating UI frames."""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sim.backend.core.cameras import CameraManager

class Frames(unittest.TestCase):
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
if __name__ == '__main__': unittest.main()
