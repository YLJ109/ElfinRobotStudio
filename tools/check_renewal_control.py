"""Renewal regressions against production modules; synthetic devices only."""
from _offline_guard import require_offline_copy
require_offline_copy()

import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import check_real_palm_pipeline as palm
import check_real_servo as servo
import check_real_follow_host as host
import check_real_link_integration as link
from backend.core import ai_plan
from backend.core import config, telemetry
from backend.core.world import World


class Palm(palm.Bridge):
    async def test_fresh_center_and_micro_jitter_hold_but_old_frame_expires(self):
        for index, offset in enumerate((0., .000001, -.000001, 0.)):
            self.now += .02
            self.assertTrue(self.sample(u=.5+offset, v=.5, opening=.5))
            self.assertTrue(await self.pipeline.publish_latest([0.]*6))
            self.assertEqual(len(self.paths), index+1)
        self.now += .31
        self.assertFalse(await self.pipeline.publish_latest([0.]*6))
        self.assertFalse(self.sample(captured_at=self.now-.31))


class Servo(servo.Servo):
    async def test_reply_latency_does_not_accumulate_into_control_period(self):
        def delay(name):
            if name == 'PushServoJ': self.clock.now += .005
        self.transport.hook = delay
        self.assertTrue(await self.driver.start())
        for seq in range(1, 5):
            self.clock.now = self.driver.next_due
            self.feedback.update(stamp=self.clock(), frame_seq=self.feedback['frame_seq']+1)
            self.assertTrue(self.publish(seq=seq))
            self.assertTrue(await self.driver.tick(), self.driver.reason)
        times = self.transport.packet_times[1:]
        for a,b in zip(times,times[1:]): self.assertAlmostEqual(b-a,.02)
        await self.driver.stop()


class Stop(host.Host):
    async def test_waiting_stop_proof_survives_withdrawn_start_permissions(self):
        self.assertTrue(await self.begin())
        self.feedback.update(enabled=False, motion_allowed=False, safety_ok=False)
        self.assertTrue((await self.host.stop())['stop_confirmed'])


class Link(link.Integration):
    async def test_proven_idle_reconnect_rebuilds_arbiter_without_replay(self):
        self.link.cmd_up = False
        self.link._writer = link.Writer(self.link, self.feedback)
        self.link._command_connection += 1
        self.link.cmd_up = True
        replacement = self.link.servo_transport()
        self.assertIsNot(replacement, self.transport)
        self.assertIsNone(replacement.owner)
        self.assertEqual(self.link._writer.packets, [])

    async def test_quarantined_connection_never_rebuilds(self):
        self.transport._fault('unknown')
        self.link._writer = link.Writer(self.link, self.feedback)
        self.link._command_connection += 1
        self.assertIs(self.link.servo_transport(), self.transport)


class Advice(unittest.IsolatedAsyncioTestCase):
    async def reply(self, value, finish='stop'):
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {'choices':[{'finish_reason':finish,'message':{'content':value}}]}
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def post(self,*args,**kwargs): return Response()
        with patch.object(ai_plan.httpx,'AsyncClient',return_value=Client()):
            return await ai_plan.explain({'code':42}, 'synthetic-key')

    async def test_malformed_schema_is_failure_not_success_diagnosis(self):
        valid={'likely':[{'cause':'原因','check':'检查','fix':'处置'}], 'risk':'风险','need_vendor':'厂家'}
        invalid=[[], None, {'likely':1}, {**valid,'likely':[1]}, {**valid,'risk':2},
                 {**valid,'likely':[{'cause':None,'check':'c','fix':'f'}]},
                 {**valid,'risk':'x'*201}, {**valid,'likely':valid['likely']*5}]
        for obj in invalid:
            with self.subTest(obj=obj):
                result=await self.reply(json.dumps(obj))
                self.assertFalse(result['ok'])
                self.assertEqual(result['likely'],[])
        for text in ('oops', '{"likely":', ''):
            self.assertFalse((await self.reply(text))['ok'])
        self.assertFalse((await self.reply(json.dumps(valid), 'length'))['ok'])
        self.assertTrue((await self.reply(json.dumps(valid)))['ok'])
        self.assertFalse((await ai_plan.explain({}, None))['ok'])


class Settings(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = patch.object(config, '_settings', deepcopy(config.DEFAULTS))
        self.state.start()
        self.addCleanup(self.state.stop)
        self.world = World()

    async def test_unknown_and_stale_error_readings_never_claim_no_fault(self):
        world = self.world
        world.settings['mode'] = 'real'
        for code, fresh in ((None,True), (0,False), (12,False), (False,True)):
            world.real=SimpleNamespace(snap={'err_code':code, 'stamp':0.},
                                       state_valid=lambda: fresh, cmd_up=True)
            with patch.object(ai_plan, 'explain', side_effect=AssertionError('no model')):
                result=await world.explain_error()
            self.assertFalse(result['ok'])
            self.assertNotIn('没有故障码', result['why'])
            self.assertIn('age_s',result)
        world.real=SimpleNamespace(snap={'err_code':0},state_valid=lambda:True,cmd_up=True)
        result=await world.explain_error()
        self.assertTrue(result['ok'])
        self.assertEqual(result['code'],0)

    async def test_log_retention_applies_live_without_resetting_sequence(self):
        for i in range(8): self.world.log.add('event',str(i))
        before=self.world.log.seq
        self.world.update_settings({'log_keep':3})
        self.assertEqual(self.world.log.keep,3)
        self.assertEqual(len(self.world.log.buf),3)
        self.assertGreater(self.world.log.seq,before)
        self.world.update_settings({'log_keep':20})
        self.assertEqual(self.world.log.buf.maxlen,20)
        self.assertGreaterEqual(self.world.log.buf[0]['seq'],before-1)

    async def test_display_preferences_do_not_revoke_control_but_limits_do(self):
        world=self.world
        with patch.object(world,'_request_real_jog_stop') as stop:
            revision=world._cfg_rev
            world.update_settings({'ghost_opacity':.42,'tts':False,'log_keep':40,'play_len_s':2})
            self.assertEqual(world._cfg_rev,revision)
            stop.assert_not_called()
            world.update_settings({'ground_enabled':False})
            self.assertEqual(world._cfg_rev,revision+1)
            stop.assert_called_once()
        self.assertEqual(world.playback.duration_s,2)

    async def test_playback_duration_prunes_frames_and_marks_and_keeps_cap(self):
        playback=telemetry.Playback(cap=4)
        playback.set_duration(2)
        playback.mark(0,'old')
        for t in (0,1,2,3,4): playback.push(t,[0.]*6,'green')
        self.assertEqual([row[0] for row in playback.items],[2,3,4])
        self.assertEqual(playback.marks,[])
        playback.set_duration(1)
        self.assertEqual([row[0] for row in playback.items],[3,4])
        playback.set_duration(3600)
        for t in range(5,20): playback.push(t,[0.]*6,'green')
        self.assertLessEqual(len(playback.items),4)
        for value in (0,True,float('nan'),3601):
            with self.assertRaises(ValueError): playback.set_duration(value)


if __name__ == '__main__': unittest.main()
