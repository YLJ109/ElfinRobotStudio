"""相机状态不加载原生SDK；无实际设备和世界实例。"""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('camera_snapshot_under_test', Path(__file__).parents[2] / 'backend/core/cameras.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CameraSnapshotTests(unittest.TestCase):
    def test_status_does_not_import_native_sdks(self):
        camera = module.CameraManager()
        with patch.object(camera, '_import_rs', side_effect=AssertionError('不应加载RealSense')), \
                patch.object(camera, '_import_cv2', side_effect=AssertionError('不应加载OpenCV')), \
                patch.object(module, 'find_spec', side_effect=lambda name: object() if name == 'cv2' else None):
            state = camera.snapshot()
            self.assertEqual(state['sdk'], {'realsense': False, 'opencv': True})
            self.assertIsNone(state['active'])


if __name__ == '__main__':
    unittest.main()
