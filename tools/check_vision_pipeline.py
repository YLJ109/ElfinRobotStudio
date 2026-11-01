"""Unified vision worker contracts; synthetic RGB and fake detectors only."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import threading
import time
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import unittest
import numpy as np
from backend.application.vision_pipeline import VisionPipeline
from backend.core.cameras import CameraManager


IMAGE = np.zeros((8,12,3),dtype=np.uint8)


class Detector:
    def __init__(self, delay=0.): self.delay,self.calls,self.closed=delay,0,False
    def detect(self, image):
        self.calls += 1
        time.sleep(self.delay)
        return {'observations':[{'kind':'hand','landmarks':[]}], 'model':{'name':'fake','version':'1'}}
    def close(self): self.closed=True


class Pipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.detector=Detector()
        self.camera=SimpleNamespace(snapshot=lambda:{'active':{'session':'local-A','state':'streaming'}},
                                    raw_frame_copy=lambda session:None)
        self.pipeline=VisionPipeline(self.camera,lambda **_:self.detector)
        await self.pipeline.start()
        self.addAsyncCleanup(self.pipeline.close)

    def enable(self,source='local',session='local-A'):
        return self.pipeline.configure(enabled=True,source=source,session=session)['epoch']

    def submit(self, seq, *, epoch=None, source='local',session='local-A',captured=None):
        return self.pipeline.submit(IMAGE,source=source,session=session,
            epoch=self.pipeline.epoch if epoch is None else epoch,seq=seq,
            captured_at=time.monotonic() if captured is None else captured,
            clock_domain='server' if source=='local' else 'remote')

    async def result(self,seq=None):
        for _ in range(200):
            result=self.pipeline.snapshot()['result']
            if result and (seq is None or result['seq']==seq): return result
            await asyncio.sleep(.005)
        self.fail('worker did not publish expected result')

    async def test_disabled_zero_inference_and_no_capture_open(self):
        await asyncio.sleep(.03)
        self.assertEqual(self.detector.calls,0)
        with self.assertRaises(ValueError):self.submit(1)
        self.assertIsNone(self.pipeline.snapshot()['result'])

    async def test_explicit_starting_camera_warms_model_before_first_frame_without_inference(self):
        ready=threading.Event()
        self.camera.snapshot=lambda:{'active':{'session':'local-A','state':'starting'}}
        def factory(**_):ready.set();return self.detector
        self.pipeline.detector_factory=factory
        self.enable()
        for _ in range(100):
            if ready.is_set():break
            await asyncio.sleep(.005)
        self.assertTrue(ready.is_set())
        self.assertEqual(self.detector.calls,0)
        self.assertEqual(self.pipeline.stats['inferred'],0)
        self.assertIsNone(self.pipeline.snapshot()['result'])
        self.submit(1)
        self.assertEqual((await self.result(1))['seq'],1)

    async def test_short_disable_reuses_only_same_session_model_without_capture_or_inference(self):
        created=[]
        def factory(**_):
            detector=Detector();created.append(detector);return detector
        self.pipeline.detector_factory=factory
        self.enable();self.submit(1)
        await self.result(1)
        self.pipeline.configure(enabled=False)
        snapshot=self.pipeline.snapshot()
        self.assertIsNone(snapshot['result'])
        self.assertIsNone(snapshot['session'])
        self.assertFalse(snapshot['pending'])
        count=created[0].calls
        await asyncio.sleep(.03)
        self.assertEqual(created[0].calls,count)
        self.assertFalse(created[0].closed)
        with self.assertRaises(ValueError):self.submit(2)
        self.enable();self.submit(3)
        await self.result(3)
        self.assertEqual(len(created),1)

    async def test_idle_model_expires_without_frames_and_close_releases_it(self):
        self.pipeline.IDLE_MODEL_SECONDS=.04
        self.enable();self.submit(1);await self.result(1)
        self.pipeline.configure(enabled=False)
        for _ in range(100):
            if self.detector.closed:break
            await asyncio.sleep(.005)
        self.assertTrue(self.detector.closed)
        self.assertIsNone(self.pipeline.snapshot()['result'])
        other=Detector()
        self.pipeline.detector_factory=lambda **_:other
        self.enable();self.submit(2);await self.result(2)
        self.pipeline.configure(enabled=False)
        await self.pipeline.close()
        self.assertTrue(other.closed)

    async def test_model_tracker_never_reused_across_camera_or_source_identity(self):
        created=[]
        def factory(**_):
            detector=Detector();created.append(detector);return detector
        self.pipeline.detector_factory=factory
        self.enable();self.submit(1);await self.result(1)
        self.pipeline.configure(enabled=False)
        self.enable(source='remote',session='phone-B')
        self.submit(2,source='remote',session='phone-B')
        result=await self.result(2)
        self.assertEqual(len(created),2)
        self.assertTrue(created[0].closed)
        self.assertFalse(result['control_valid'])

    async def test_unopened_or_foreign_starting_camera_cannot_warm_model(self):
        for active in (None,{'session':'other','state':'starting'},{'session':'local-A','state':'closed'}):
            self.camera.snapshot=lambda active=active:{'active':active}
            with self.assertRaises(ValueError):self.enable()
        self.assertIsNone(self.pipeline._thread)

    async def test_latest_slot_bounded_rejects_epoch_replay_and_old_sequence(self):
        self.detector.delay=.04
        epoch=self.enable()
        for seq in range(1,30):self.submit(seq)
        result=await self.result(29)
        self.assertTrue(result['control_valid'])
        self.assertGreater(self.pipeline.stats['replaced'],0)
        self.assertLess(self.detector.calls,5)
        with self.assertRaises(ValueError):self.submit(28)
        self.enable()
        self.assertIsNone(self.pipeline.snapshot()['result'])
        with self.assertRaises(ValueError):self.submit(30,epoch=epoch)

    async def test_old_source_inflight_result_never_publishes(self):
        entered,release=threading.Event(),threading.Event()
        original=self.detector.detect
        def held(image): entered.set();release.wait(1.);return original(image)
        self.detector.detect=held
        self.enable();self.submit(1)
        self.assertTrue(await asyncio.to_thread(entered.wait,1.))
        self.enable(source='remote',session='phone-B')
        release.set()
        await asyncio.sleep(.05)
        self.assertIsNone(self.pipeline.snapshot()['result'])
        self.assertGreaterEqual(self.pipeline.stats['discarded'],1)

    async def test_detector_failure_clears_old_observations(self):
        self.enable();self.submit(1)
        self.assertTrue((await self.result(1))['control_valid'])
        def failed(image):raise RuntimeError('synthetic inference failure')
        self.detector.detect=failed
        self.submit(2)
        result=await self.result(2)
        self.assertFalse(result['valid']);self.assertFalse(result['control_valid'])
        self.assertEqual(result['observations'],[])
        self.assertIn('synthetic',result['error'])

    async def test_failed_worker_closes_and_requires_explicit_new_epoch(self):
        first,second=Detector(),Detector()
        factories=[];attempts=[]
        def fail(image):
            attempts.append(1)
            raise RuntimeError('synthetic worker lost')
        first.detect=fail
        def factory(**options):
            factories.append(1)
            return first if len(factories)==1 else second
        self.pipeline.detector_factory=factory
        self.enable();self.submit(1)
        self.assertFalse((await self.result(1))['valid'])
        self.assertTrue(first.closed,'failed worker must release its owned resources immediately')
        self.submit(2)
        self.assertFalse((await self.result(2))['valid'])
        self.assertEqual(len(factories),1,'same epoch must not create a retry storm')
        self.assertEqual(len(attempts),1,'same failed worker must not be called on every arriving frame')
        self.enable();self.submit(3)
        self.assertTrue((await self.result(3))['valid'])
        self.assertEqual(len(factories),2,'explicit new epoch must allow a fresh worker')
        self.assertEqual(second.calls,1)

    async def test_display_retention_does_not_extend_control_expiry(self):
        self.enable();self.submit(1)
        result=await self.result(1)
        self.assertTrue(result['control_valid'])
        self.pipeline.clock=lambda:result['valid_until']+.001
        retained=self.pipeline.snapshot()['result']
        self.assertTrue(retained['valid']);self.assertFalse(retained['control_valid'])
        self.assertEqual(result['valid_until'],retained['valid_until'])

    async def test_unmapped_remote_clock_never_grants_control(self):
        self.enable(source='remote',session='phone-B')
        self.submit(1,source='remote',session='phone-B',captured=999999999.)
        result=await self.result(1)
        self.assertTrue(result['valid']);self.assertFalse(result['control_valid'])
        self.assertEqual(result['clock_domain'],'remote')

    async def test_decode_delay_cannot_refresh_received_frame_age(self):
        self.enable(source='remote',session='phone-B')
        with self.assertRaisesRegex(ValueError,'过期'):
            self.pipeline.submit(IMAGE,source='remote',session='phone-B',epoch=self.pipeline.epoch,
                                 seq=1,captured_at=12.,received_at=time.monotonic()-.31)
        self.assertEqual(self.pipeline.stats['submitted'],0)

    async def test_capture_60hz_inference_20hz_still_publishes_during_capture(self):
        self.detector.delay=.05
        self.enable()
        published=[]
        for seq in range(1,40):
            self.submit(seq)
            await asyncio.sleep(1/60)
            result=self.pipeline.snapshot()['result']
            if result:published.append(result['seq'])
        self.assertGreater(len(set(published)),3,'fresh inference must not starve behind arriving capture frames')
        self.assertEqual(published,sorted(published),'published sequence must never rewind')

    async def test_publication_rejects_regression_expiry_and_old_epoch(self):
        self.enable();self.submit(10)
        result=await self.result(10)
        self.assertFalse(self.pipeline._publish_result(dict(result,seq=9),dict(result,seq=9)))
        with self.pipeline._condition:self.pipeline._seq=11
        self.assertFalse(self.pipeline._publish_result(dict(result,seq=11),dict(result,seq=11,valid_until=0.)))
        self.assertEqual(self.pipeline.snapshot()['result']['seq'],10)
        self.enable()
        self.assertFalse(self.pipeline._publish_result(dict(result,seq=12),dict(result,seq=12)))
        self.assertIsNone(self.pipeline.snapshot()['result'])

    async def test_raw_frame_copy_never_opens_device_and_cannot_alias_capture(self):
        manager=CameraManager()
        manager._active={'session':'raw','state':'streaming'}
        pixels=IMAGE.copy()
        manager._raw_color=('raw',7,time.monotonic(),pixels)
        result=manager.raw_frame_copy('raw')
        self.assertEqual(result['seq'],7)
        self.assertFalse(result['image'].flags.writeable)
        pixels[0,0,0]=255
        self.assertEqual(int(result['image'][0,0,0]),0)
        self.assertIsNone(manager.raw_frame_copy('different'))
        manager._raw_color=('raw',8,time.monotonic()-1.,pixels)
        self.assertIsNone(manager.raw_frame_copy('raw'))

    async def test_close_during_loading_releases_owned_child_before_return(self):
        from backend.infrastructure.vision import process_detector as adapter
        children=[]
        original=adapter.subprocess.Popen
        def spawn(*args,**kwargs):
            child=original(*args,**kwargs);children.append(child);return child
        with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
            script=Path(directory)/'slow-worker.py'
            script.write_text('import time\ntime.sleep(30)\n',encoding='utf-8')
            model=Path(directory)/'fake.task';model.write_bytes(b'fake')
            self.pipeline.detector_factory=lambda **options:adapter.ProcessHandDetector(
                python=sys.executable,model_path=model,**options)
            with patch.dict(os.environ,ELFIN_VISION_WORKER=''),patch.object(adapter,'WORKER_PATH',script), \
                 patch.object(adapter.subprocess,'Popen',side_effect=spawn):
                self.enable();self.submit(1)
                for _ in range(200):
                    if children:break
                    await asyncio.sleep(.005)
                self.assertTrue(children,'fixture did not enter loading subprocess')
                began=time.monotonic()
                await self.pipeline.close()
                self.assertLess(time.monotonic()-began,3.5)
                self.assertFalse(self.pipeline._thread.is_alive())
                self.assertTrue(all(child.poll() is not None for child in children))


if __name__=='__main__':unittest.main(verbosity=2)
