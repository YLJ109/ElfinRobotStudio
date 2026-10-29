"""Automatic version lookup: fake command channel only, no real hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.robot_real import RealLink, Reply, Desync, IDENTITY_REPLY_TIMEOUT_S


class Identity(unittest.IsolatedAsyncioTestCase):
    def ready(self):
        link=RealLink('offline.invalid')
        link.cmd_up=True
        link.snap.update(enabled=0,moving=0,free_drive=0)
        link._begin_controller_identity()
        return link

    async def test_cached_status_never_connects_or_sends(self):
        link=RealLink('offline.invalid')
        with patch('asyncio.open_connection',side_effect=AssertionError('No hardware')):
            self.assertIsNone(link.controller_info()['version'])
            self.assertEqual(link.controller_info()['status'],'disconnected')
        self.assertTrue(link.q.empty())

    async def test_valid_reply_is_cached_once_without_motion_enable(self):
        link=self.ready()
        link.last_command={'name':'GrpDisable','ok':True}
        with patch.object(link,'state_valid',return_value=True),patch.object(link,'_roundtrip',
                AsyncMock(return_value=Reply('PackageVersion',True,['6.5.19.20261001']))) as query:
            self.assertTrue(await link._probe_controller_identity())
            self.assertFalse(await link._probe_controller_identity())
            query.assert_awaited_once_with('PackageVersion,;','PackageVersion',reply_timeout=IDENTITY_REPLY_TIMEOUT_S)
        self.assertEqual(link.controller_info()['version'],'6.5.19.20261001')
        self.assertFalse(link.controller_info()['motion_qualified'])
        self.assertEqual(link.last_command,{'name':'GrpDisable','ok':True})
        self.assertEqual(link.snap['enabled'],0)

    async def test_enabled_moving_jogging_or_unknown_states_defer_query(self):
        for key,value in [('enabled',1),('moving',1),('free_drive',1),('enabled',None),('free_drive',None)]:
            with self.subTest(key=key,value=value):
                link=self.ready();link.snap[key]=value
                with patch.object(link,'state_valid',return_value=True),patch.object(link,'_roundtrip',AsyncMock()) as query:
                    self.assertFalse(await link._probe_controller_identity());query.assert_not_called()
                self.assertTrue(link._identity_pending)
        link=self.ready();link.jogging=(0,1)
        with patch.object(link,'state_valid',return_value=True),patch.object(link,'_roundtrip',AsyncMock()) as query:
            self.assertFalse(await link._probe_controller_identity());query.assert_not_called()

    async def test_queued_commands_take_precedence_and_stale_feedback_defers(self):
        link=self.ready();link.q.put_nowait((0,1,'GrpStop,0,;','GrpStop'))
        with patch.object(link,'state_valid',return_value=True),patch.object(link,'_roundtrip',AsyncMock()) as query:
            self.assertFalse(await link._probe_controller_identity());query.assert_not_called()
        link.q.get_nowait()
        with patch.object(link,'state_valid',return_value=False),patch.object(link,'_roundtrip',AsyncMock()) as query:
            self.assertFalse(await link._probe_controller_identity());query.assert_not_called()

    async def test_invalid_replies_remain_unknown_and_do_not_repeat_after_reconnect(self):
        for reply in [Reply('PackageVersion',False,['20099']),Reply('Other',True,['6.5.19']),
                      Reply('PackageVersion',True,[]),Reply('PackageVersion',True,['a','b']),
                      Reply('PackageVersion',True,['']),Reply('PackageVersion',True,['6.5\n19'])]:
            with self.subTest(fields=reply.fields):
                link=self.ready()
                with patch.object(link,'state_valid',return_value=True),patch.object(link,'_roundtrip',AsyncMock(return_value=reply)) as query:
                    self.assertFalse(await link._probe_controller_identity())
                    link._begin_controller_identity()
                    self.assertFalse(await link._probe_controller_identity())
                    self.assertEqual(query.await_count,1)
                self.assertEqual(link.controller_info()['status'],'unknown')
                self.assertIsNone(link.controller_info()['version'])

    async def test_timeout_uses_normal_desync_recovery_without_infinite_query_loop(self):
        link=self.ready()
        with patch.object(link,'state_valid',return_value=True),patch.object(link,'_roundtrip',AsyncMock(side_effect=Desync('fake timeout'))) as query:
            with self.assertRaises(Desync):await link._probe_controller_identity()
            link._begin_controller_identity()
            self.assertFalse(await link._probe_controller_identity())
            self.assertEqual(query.await_count,1)
        self.assertIsNone(link.controller_info()['version'])

    async def test_old_connection_reply_cannot_populate_new_identity(self):
        link=self.ready()
        async def changed(*args,**kwargs):
            link._begin_controller_identity()
            return Reply('PackageVersion',True,['old-version'])
        with patch.object(link,'state_valid',return_value=True),patch.object(link,'_roundtrip',changed):
            self.assertFalse(await link._probe_controller_identity())
        self.assertIsNone(link.controller_info()['version'])
        self.assertEqual(link.controller_info()['status'],'pending')

    async def test_disconnect_hides_cached_version(self):
        link=self.ready()
        with patch.object(link,'state_valid',return_value=True),patch.object(link,'_roundtrip',AsyncMock(return_value=Reply('PackageVersion',True,['6.5.19']))):
            self.assertTrue(await link._probe_controller_identity())
        link.cmd_up=False
        self.assertIsNone(link.controller_info()['version'])
        self.assertEqual(link.controller_info()['status'],'disconnected')

    async def test_command_loop_automatically_queries_only_readonly_version(self):
        link=RealLink('offline.invalid')
        reader=asyncio.StreamReader()
        packets=[]
        class Writer:
            def write(self,packet):packets.append(packet)
            async def drain(self):reader.feed_data(b'PackageVersion,OK,6.5.19.20261001,;')
            def close(self):pass
        async def connect(*args,**kwargs):
            link.stream_up=True
            link.snap.update(connection_epoch=link.epoch+1,stamp=time.monotonic(),joints=[0.]*6,
                             fsm=7,enabled=0,moving=0,inpos=1,free_drive=0)
            return reader,Writer()
        with patch('asyncio.open_connection',connect):
            task=asyncio.create_task(link._cmd_loop())
            try:
                async def wait():
                    while link.controller_info()['status']!='known':await asyncio.sleep(.001)
                await asyncio.wait_for(wait(),.5)
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
                await link.stop()
        self.assertEqual(packets,[b'PackageVersion,;'])
        self.assertEqual(link.snap['enabled'],0)


if __name__=='__main__':unittest.main()
