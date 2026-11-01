"""Actual local CPU model against safe synthetic RGB; no camera or network."""
from _offline_guard import require_offline_copy
require_offline_copy()

import os
from pathlib import Path
import time
import unittest
import numpy as np
from backend.infrastructure.vision.process_detector import ProcessHandDetector


class LocalModel(unittest.TestCase):
    def test_local_model_on_blank_synthetic_frame(self):
        python=os.environ.get('ELFIN_VISION_PYTHON')
        worker=os.environ.get('ELFIN_VISION_WORKER')
        self.assertTrue((worker and Path(worker).is_file()) or (python and Path(python).is_file()),
                        'Configure the isolated vision interpreter or packaged worker')
        began=time.monotonic()
        detector=ProcessHandDetector(python=python)
        try:
            load=time.monotonic()-began
            samples=[]
            for brightness in (0,127,255):
                started=time.monotonic()
                result=detector.detect(np.full((240,320,3),brightness,dtype=np.uint8))
                samples.append(time.monotonic()-started)
                self.assertEqual(result['observations'],[],'blank synthetic frames must not fabricate a palm')
                self.assertEqual(result['model']['name'],'mediapipe-hand-landmarker')
                self.assertEqual(result['model']['running_mode'],'video')
                self.assertEqual(len(result['model']['sha256']),64)
            print('VISION_MODEL',dict(load_s=load,inference_ms=[round(v*1000,2) for v in samples],
                                      model=result['model']),flush=True)
        finally:detector.close()
        self.assertIsNotNone(detector.process.poll())


if __name__=='__main__':unittest.main(verbosity=2)
