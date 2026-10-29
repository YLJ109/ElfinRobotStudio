"""来源互斥与会话条件关闭：假相机，不枚举或打开物理设备。"""
from _offline_guard import require_offline_copy
require_offline_copy()
import copy
import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import backend.main as entry
from backend.core.vision_workspace import VisionWorkspace

class Cameras:
    def __init__(self):self.active=None;self.opened=0;self.closed=0;self.entered=threading.Event();self.release=threading.Event();self.release.set()
    def snapshot(self):return {'active':copy.deepcopy(self.active)}
    def open(self,*args):
        self.entered.set();self.release.wait(3)
        self.opened+=1;self.active={'session':'new-session','id':'fake','state':'streaming'}
        return {'ok':True,**self.snapshot()}
    def close(self):self.closed+=1;self.active=None;return {'ok':True,**self.snapshot()}

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.cameras=Cameras();self.engine=VisionWorkspace(self.cameras)
        self.patches=[patch.object(entry,'cameras',self.cameras),patch.object(entry,'vision_workspace',self.engine)]
        for value in self.patches:value.start();self.addCleanup(value.stop)
    def test_virtual_blocks_real_open(self):
        self.engine.configure({'input':'virtual','enabled':True})
        self.assertFalse(entry.cameras_open({'camera_id':'fake'})['ok']);self.assertEqual(self.cameras.opened,0)
    def test_real_blocks_virtual_enable(self):
        self.assertTrue(entry.cameras_open({'camera_id':'fake'})['ok'])
        with self.assertRaises(ValueError):self.engine.configure({'input':'virtual','enabled':True})
        self.assertEqual(self.engine.config['input'],'camera')
    def test_stale_session_never_closes_new_camera(self):
        self.cameras.open()
        self.assertFalse(entry.cameras_close({'session':'old-session'})['ok']);self.assertEqual(self.cameras.closed,0)
        self.assertTrue(entry.cameras_close({'session':'new-session'})['ok']);self.assertEqual(self.cameras.closed,1)
    def test_cross_window_open_config_are_serial(self):
        self.cameras.release.clear();errors=[]
        opener=threading.Thread(target=lambda:entry.cameras_open({'camera_id':'fake'}))
        def configure():
            try:self.engine.configure({'input':'virtual','enabled':True})
            except ValueError as exc:errors.append(str(exc))
        opener.start();self.assertTrue(self.cameras.entered.wait(1))
        virtual=threading.Thread(target=configure);virtual.start()
        self.cameras.release.set();opener.join(3);virtual.join(3)
        self.assertFalse(opener.is_alive());self.assertFalse(virtual.is_alive())
        self.assertEqual(len(errors),1);self.assertEqual(self.engine.config['input'],'camera')
    def test_instance_identity_stable_within_process_unique_across_engines(self):
        self.assertEqual(self.engine.snapshot()['instance_id'],self.engine.snapshot()['instance_id'])
        self.assertNotEqual(self.engine.instance_id,VisionWorkspace(self.cameras).instance_id)

if __name__=='__main__':unittest.main()
