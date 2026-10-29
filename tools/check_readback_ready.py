"""Fake-controller regression; never connect hardware or use production data."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.robot_real import RealLink
from backend.core.world import World
from backend.core import robot_real

def fixture():
    link=RealLink('offline.invalid');link.stream_up=link.cmd_up=True
    link.snap.update(joints=[1.,2.,3.,4.,5.,6.],stamp=time.monotonic(),connection_epoch=link.epoch,
                     fsm=7,enabled=0,moving=0,inpos=1)
    link.frame_seq=1
    w=World.__new__(World);w.real=link;w._stop_task=None;w._stop_unknown=True
    w.executor=NS(busy=False);w.real_jog_follow=NS(busy=False);w.runner=NS(state='idle')
    w._ready_task=None;w.log=NS(add=lambda *args:None)
    return w,link

class ReadbackTests(unittest.TestCase):
    def test_encoder_display_does_not_require_fsm_enable_or_command_channel(self):
        w,link=fixture();link.cmd_up=False;link.snap.update(fsm=None,enabled=None,moving=None,inpos=None)
        self.assertTrue(link.pose_valid());self.assertFalse(link.state_valid())
        w.settings={'mode':'real'};self.assertTrue(w.joints_known())
    def test_invalid_stale_or_wrong_epoch_encoder_data_stays_unknown(self):
        for change in ({'joints':[1,2,3,4,5,float('nan')]},{'stamp':time.monotonic()-1},{'connection_epoch':-1}):
            with self.subTest(change=change):
                _,link=fixture();link.snap.update(change);self.assertFalse(link.pose_valid())
    def test_idle_cold_controller_and_first_connection_are_passive(self):
        w,link=fixture();self.assertTrue(w._passive_real_read())
        link.frame_seq=0;link.snap.update(fsm=None,enabled=None,moving=None)
        self.assertTrue(w._passive_real_read())
    def test_active_task_or_enabled_robot_cannot_skip_motion_loss_protection(self):
        w,link=fixture();w.executor.busy=True;self.assertFalse(w._passive_real_read())
        w.executor.busy=False;link.snap.update(fsm=33,enabled=1);self.assertFalse(w._passive_real_read())

class ReadyRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def advance(self,link,**changes):
        await asyncio.sleep(.01);link.frame_seq+=1;link.snap.update(stamp=time.monotonic(),**changes)
    async def test_powered_off_two_fresh_frames_clear_old_latch_without_commands(self):
        w,link=fixture();task=asyncio.create_task(self.advance(link))
        self.assertTrue(await w._recover_powered_off_stop());await task
        self.assertFalse(w._stop_unknown);self.assertTrue(w.stop_result['settled'])
        self.assertFalse(w.stop_result['acknowledged']);self.assertEqual(link._seq,0)
    async def test_power_on_moving_or_enabled_cannot_clear_old_latch(self):
        for change in ({'fsm':24},{'enabled':1},{'moving':1},{'inpos':0}):
            w,link=fixture();link.snap.update(change)
            self.assertFalse(await w._recover_powered_off_stop());self.assertTrue(w._stop_unknown)
    async def test_repeated_old_frame_does_not_reconcile(self):
        w,_=fixture();self.assertFalse(await w._recover_powered_off_stop());self.assertTrue(w._stop_unknown)
    async def test_new_connection_or_joint_change_invalidates_recovery(self):
        for kind in ('epoch','joints'):
            w,link=fixture()
            async def mutate():
                await asyncio.sleep(.01);link.frame_seq+=1
                if kind=='epoch':link.epoch+=1
                else:link.snap['joints']=[10.]*6
            task=asyncio.create_task(mutate());self.assertFalse(await w._recover_powered_off_stop());await task
            self.assertTrue(w._stop_unknown)

class PowerReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_power_ack_uses_own_bounded_budget_but_motion_does_not(self):
        for name, succeeds in (('Electrify',True),('StartMaster',True),('MoveRelJ',False),('GrpStop',False)):
            with self.subTest(name=name):
                link=RealLink('offline.invalid');link._dead=False
                async def reply_later():
                    await asyncio.sleep(.04)
                    link.ascii=(name+',OK,;').encode();link._data.set()
                tasks=[]
                def write(_):tasks.append(asyncio.create_task(reply_later()))
                async def drain():pass
                link._writer=NS(write=write,drain=drain)
                with patch.object(robot_real,'REPLY_TIMEOUT_S',.01),patch.dict(robot_real.POWER_REPLY_TIMEOUTS,Electrify=.15,StartMaster=.15):
                    try:
                        if succeeds:self.assertTrue((await link._roundtrip(name+',;',name)).ok)
                        else:
                            with self.assertRaises(robot_real.Desync):await link._roundtrip(name+',;',name)
                    finally:
                        for task in tasks:task.cancel()
                        await asyncio.gather(*tasks,return_exceptions=True)

    async def test_explicit_short_deadline_still_overrides_power_exception(self):
        link=RealLink('offline.invalid');link._dead=False
        async def drain():pass
        link._writer=NS(write=lambda _:None,drain=drain)
        with self.assertRaisesRegex(robot_real.Desync,'0.01s'):
            await link._roundtrip('Electrify,;','Electrify',reply_timeout=.01)

if __name__=='__main__':unittest.main()
