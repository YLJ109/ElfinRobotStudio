"""Power regressions: run only through run_offline_checks.py's temporary copy."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("HUYAN_OFFLINE_ROOT") != str(ROOT):
    raise SystemExit("Use tools/run_offline_checks.py check_power_regressions.py (isolated copy required)")
os.environ["HUYAN_NO_REAL"] = "1"
sys.path.insert(0, str(ROOT))

import asyncio
import time
import unittest
from unittest.mock import patch

from backend.core import config, controls
from backend.core.robot_real import RealLink, Reply
from backend.core.world import World


class FakePowerController(RealLink):
    def __init__(self, fsm=7, enabled=0):
        super().__init__("offline.invalid")
        self.stream_up = self.cmd_up = True
        self.calls = []
        self.tasks = []
        self.feedback = True
        self.hook = None
        self.result = None
        self.publish(fsm, enabled)
        # The real state channel continues at 20 Hz independently of command
        # ACKs. One delayed command frame can share a Windows clock tick with
        # the previous frame and is not a substitute for that live stream.
        self.tasks.append(asyncio.create_task(self._feedback_loop()))

    async def _feedback_loop(self):
        while True:
            await asyncio.sleep(.05)
            # feedback=False deliberately models a stopped state stream. Do not
            # refresh its timestamp/epoch behind the negative tests' backs.
            if self.feedback:
                self.publish()

    def publish(self, fsm=None, enabled=None):
        self.frame_seq += 1
        self.snap.update(joints=[0.] * 6, moving=0, inpos=1, err_code=0,
                         stamp=time.monotonic(), connection_epoch=self.epoch,
                         frame_seq=self.frame_seq)
        self.snap.setdefault("override", .05)
        if self.snap.get("override") is None:
            self.snap["override"] = .05
        if fsm is not None:
            self.snap["fsm"] = fsm
        if enabled is not None:
            self.snap["enabled"] = enabled

    async def ask(self, name, *params, **kwargs):
        # A closed allowlist also catches free-drive and IO, not only Move/Jog.
        if name not in {"Electrify", "StartMaster", "ConnectToBox", "GrpEnable",
                        "GrpDisable", "GrpStop", "GrpReset", "SetOverride"}:
            raise AssertionError(f"Forbidden command in offline power regression: {name}")
        self.calls.append(name)
        if self.hook:
            await self.hook(name)
        result = self.result or Reply(name, True)
        if self.feedback and result.ok:
            if name == "SetOverride":
                async def override_later():
                    await asyncio.sleep(.005)
                    self.snap["override"] = float(params[1])
                    self.publish()
                self.tasks.append(asyncio.create_task(override_later()))
            target = {"Electrify": (14, 0), "StartMaster": (24, 0),
                      "ConnectToBox": (24, 0), "GrpEnable": (33, 1), "GrpDisable": (24, 0),
                      "GrpStop": (self.snap.get('fsm'), self.snap.get('enabled'))}.get(name)
            if target:
                async def later():
                    await asyncio.sleep(.005)
                    self.publish(*target)
                self.tasks.append(asyncio.create_task(later()))
        return result


class PowerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_ack_intermediate_23_is_not_failure_or_early_success(self):
        w = self.world(24,0);link = w.real
        w.READY_TIMEOUT_S=.3
        async def enable_transition(name):
            if name!='GrpEnable':return
            link.feedback=False;link.publish(23,0)
            async def complete():
                await asyncio.sleep(.04);link.publish(33,1)
            link.tasks.append(asyncio.create_task(complete()))
        link.hook=enable_transition
        result=await w.power('enable')
        self.assertTrue(result['ok'],result)
        self.assertEqual(link.snap['fsm'],33)
        self.assertEqual(link.calls,['GrpEnable'])

    async def test_power_ack_and_unknown_transition_wait_for_fresh_target_without_stop(self):
        w = self.world(7, 0);link = w.real
        w.READY_TIMEOUT_S = .3
        async def during_power(name):
            if name != 'Electrify':return
            link.feedback = False
            link.snap['stamp'] = time.monotonic() - 1
            epoch = link.epoch
            w._tick(.02, time.monotonic())
            self.assertEqual(link.epoch, epoch)
            async def finish():
                await asyncio.sleep(.03);link.publish(8, 0)
                await asyncio.sleep(.03);link.publish(14, 0)
            link.tasks.append(asyncio.create_task(finish()))
        link.hook = during_power
        result = await w.power('electrify')
        self.assertTrue(result['ok'],result)
        self.assertEqual(link.snap['fsm'],14)
        self.assertEqual(link.calls,['Electrify'])
        self.assertIsNone(w._power_observation)

    async def test_power_observation_cannot_advance_on_unknown_state_or_enable(self):
        w = self.world(7, 0);link = w.real
        async def remain_unknown(name):
            link.feedback = False;link.publish(8,0)
        link.hook = remain_unknown
        result = await w.power('electrify')
        self.assertFalse(result['ok'])
        self.assertEqual(link.calls,['Electrify'])
        self.assertIsNone(w._power_observation)
        link.snap['enabled'] = 1
        w._power_observation=(link,link.epoch,link.command_session,time.monotonic()+5)
        self.assertFalse(w._power_observation_valid(link,link.epoch))

    async def test_cancel_keeps_power_then_ready_only_enables(self):
        w = self.world(33, 1)
        result = await w.cancel_ready()
        self.assertTrue(result['ok'], result)
        self.assertEqual(w.live_fsm(), 24)
        self.assertEqual(w.real.snap['enabled'], 0)
        self.assertEqual(w.real.calls, ['GrpStop','GrpDisable'])
        self.assertNotIn('BlackOut', w.real.calls)
        w.real.calls.clear()
        await w.ready()
        await w._ready_task
        self.assertEqual(w.real.calls, ['GrpEnable'])
        self.assertEqual(w.live_fsm(), 33)

    async def test_ready_applies_saved_default_speed_after_enable_and_waits_for_readback(self):
        w = self.world()
        w.real.snap['override'] = .01
        self.assertEqual(w.settings['real_speed_pct'], 5)
        await w.ready()
        await w._ready_task
        self.assertEqual(w.real.calls, ['Electrify','StartMaster','GrpEnable','SetOverride'])
        self.assertEqual(w.real.snap['override'], .05)
        self.assertTrue(w.speed_ack['ok'])
        self.assertEqual(w.speed_ack['outcome'], 'confirmed')
        self.assertEqual(w.ready_state['why'], '')
    async def asyncSetUp(self):
        self.worlds = []
        self.controllers = []
        self.no_network = patch("asyncio.open_connection", side_effect=AssertionError("No sockets in power tests"))
        self.no_network.start()

    async def asyncTearDown(self):
        tasks = [task for link in self.controllers for task in link.tasks]
        for w in self.worlds:
            if w._ready_task:
                tasks.append(w._ready_task)
            if w._speed_task:
                tasks.append(w._speed_task)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.no_network.stop()

    def world(self, fsm=7, enabled=0):
        config.save_settings({"mode": "sim", "auto_connect": False, "real_speed_pct": 5})
        w = World()
        w.settings = dict(w.settings, mode="real")
        w.real = FakePowerController(fsm, enabled)
        self.controllers.append(w.real)
        w.READY_TIMEOUT_S = .09
        w.READY_POLL_S = .002
        self.worlds.append(w)
        return w

    async def test_cancel_already_disabled_preserves_existing_power_stage(self):
        for fsm in (7, 14, 24):
            with self.subTest(fsm=fsm):
                w = self.world(fsm, 0)
                result = await w.cancel_ready()
                self.assertTrue(result["ok"], result)
                self.assertEqual(w.real.snap["fsm"], fsm)
                self.assertEqual(w.real.snap["enabled"], 0)
                self.assertEqual(w.real.calls, [])
                self.assertFalse(w.stop_result['acknowledged'])
                self.assertEqual(w.stop_result['proof'], 'fresh_disabled_stationary_frames')

    async def test_disabled_idle_proof_rejects_drift_or_an_inflight_transaction(self):
        for kind in ('drift', 'inflight', 'enabled', 'session'):
            with self.subTest(kind=kind):
                w = self.world(24, 0)
                link = w.real
                link.feedback = False
                async def change():
                    await asyncio.sleep(.005)
                    link.publish()
                    await asyncio.sleep(.025)
                    if kind == 'drift':link.snap['joints'] = [1.] * 6
                    elif kind == 'inflight':link._inflight = 123
                    elif kind == 'enabled':link.snap['enabled'] = 1
                    else:link._command_connection += 1
                    link.frame_seq += 1
                    link.snap['stamp'] = time.monotonic()
                mutation = asyncio.create_task(change())
                self.assertFalse(await w._confirm_disabled_idle(link,link.command_session))
                await mutation

    async def test_cancel_rejection_never_falls_back_to_blackout_or_cold_start(self):
        w = self.world(33, 1)
        async def reject_disable(name):
            if name == "GrpDisable":
                w.real.result = Reply(name, False, ["20018", "disable rejected"], 20018)
        w.real.hook = reject_disable
        result = await w.cancel_ready()
        self.assertFalse(result["ok"], result)
        self.assertIn("disable rejected", result["why"])
        self.assertEqual(w.real.calls, ["GrpStop", "GrpDisable"])
        self.assertEqual(w.real.snap["enabled"], 1)

    async def test_cancel_does_not_accept_still_enabled_new_frame_as_disabled(self):
        w = self.world(33, 1)
        original_wait = w._wait_power_state
        async def short_wait(link, connection, want, after_seq, timeout=None, **options):
            return await original_wait(link, connection, want, after_seq, timeout=.04, **options)
        w._wait_power_state = short_wait
        async def remain_enabled(name):
            if name == "GrpDisable":
                w.real.feedback = False
                async def later():
                    await asyncio.sleep(.005)
                    w.real.publish(33, 1)
                w.real.tasks.append(asyncio.create_task(later()))
        w.real.hook = remain_enabled
        result = await w.cancel_ready()
        self.assertFalse(result["ok"], "Disable ACK plus FSM=33/enabled=1 must remain unconfirmed")
        self.assertEqual(w.real.calls, ["GrpStop", "GrpDisable"])

    async def test_cancel_revokes_pending_ready_before_any_later_enable(self):
        w = self.world()
        started = asyncio.Event()
        async def block_electrify(name):
            if name == "Electrify":
                started.set()
                await asyncio.Event().wait()
        w.real.hook = block_electrify
        await w.ready()
        await asyncio.wait_for(started.wait(), .5)
        pending_ready = w._ready_task
        result = await w.cancel_ready()
        await asyncio.gather(pending_ready, return_exceptions=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(w.real.calls, ["Electrify", "GrpStop"])
        self.assertNotIn("GrpEnable", w.real.calls)

    async def test_prepare_without_enable_does_not_write_override(self):
        w = self.world()
        w.real.snap["override"] = .01
        self.assertTrue((await w.ready(then_enable=False))["ok"])
        await w._ready_task
        self.assertEqual(w.real.calls, ["Electrify", "StartMaster"])
        self.assertEqual(w.real.snap["override"], .01)
        self.assertEqual(w.real.snap["enabled"], 0)

    async def test_speed_ack_and_pre_ack_echo_require_a_following_periodic_frame(self):
        w = self.world(33, 1)
        w.real.snap["override"] = .01
        w.real.feedback = False
        sent = asyncio.Event()
        async def echo_before_ack(name):
            self.assertEqual(name, "SetOverride")
            w.real.snap["override"] = .05
            w.real.publish()
            sent.set()
        w.real.hook = echo_before_ack
        await w.ready()
        await asyncio.wait_for(sent.wait(), .5)
        self.assertFalse(w._ready_task.done())
        self.assertIsNone(w.speed_ack["ok"])
        w.real.publish()
        await w._ready_task
        self.assertTrue(w.speed_ack["ok"])
        self.assertEqual(w.speed_ack["outcome"], "confirmed")
        self.assertEqual(w.real.calls, ["SetOverride"])

    async def test_speed_rejection_is_visible_without_motion_or_retries(self):
        w = self.world(33, 1)
        w.real.snap["override"] = .01
        w.real.result = Reply("SetOverride", False, ["20018", "override rejected"], 20018)
        await w.ready()
        await w._ready_task
        self.assertFalse(w.speed_ack["ok"])
        self.assertIn("override rejected", w.ready_state["why"])
        self.assertEqual(w.real.snap["override"], .01)
        self.assertEqual(w.real.calls, ["SetOverride"])

    async def test_speed_readback_from_reconnected_controller_cannot_confirm_ready(self):
        w = self.world(33, 1)
        w.real.snap["override"] = .01
        w.real.feedback = False
        sent = asyncio.Event()
        async def note_send(name):
            sent.set()
        w.real.hook = note_send
        await w.ready()
        await asyncio.wait_for(sent.wait(), .5)
        w.real.epoch += 1
        w.real.snap["override"] = .05
        w.real.publish()
        await w._ready_task
        self.assertFalse(w.speed_ack["ok"])
        self.assertTrue(w.ready_state["why"])
        self.assertEqual(w.real.calls, ["SetOverride"])

    async def test_fsm_is_an_enum_not_an_ordered_number(self):
        for fsm, enabled in ((34, 1), (55, 1), (33, 0), (33, "0"), ("33", 1), (None, 1)):
            self.assertFalse(controls.motion_granted(fsm, enabled))
        self.assertTrue(controls.motion_granted(33, 1))
        self.assertFalse(controls.step_reached(33, 33, 0))

    async def test_control_grant_requires_both_channels(self):
        for stream, cmd in ((False, False), (False, True), (True, False)):
            c = controls.derive("real", {"stream_up": stream, "cmd_up": cmd},
                                {"fsm": 33, "enabled": 1}, {})
            self.assertFalse(c["motion_granted"])

    async def test_missing_error_or_enabled_stays_unknown_and_blocks_advance(self):
        w = self.world(24, 0)
        w.real.snap["err_code"] = None
        self.assertFalse(w.ctrl()["primary"]["enabled"])
        result = await w.power("enable")
        self.assertFalse(result["ok"])
        self.assertIn("未知", result["why"])
        self.assertFalse(w.real.calls)
        c = controls.derive("real", {"stream_up": True, "cmd_up": True},
                            {"fsm": 24, "enabled": None, "err_code": None}, {})
        self.assertIsNone(c["enabled"])
        self.assertFalse(c["primary"]["enabled"])

    async def test_first_world_bad_action_returns_error_without_primary_lookup_failure(self):
        config.save_settings({"mode": "sim", "auto_connect": False})
        w = World()
        self.worlds.append(w)
        result = await w.act("badkey")
        self.assertFalse(result["ok"])
        self.assertIn("badkey", result["why"])

    async def test_world_control_and_snapshot_agree_on_stale_state(self):
        w = self.world(33, 1)
        w.real.snap["stamp"] -= 1
        self.assertFalse(w.ctrl()["motion_granted"])
        self.assertFalse(w.motion_ok())
        self.assertFalse(w.snapshot()["motion_granted"])

    async def test_real_without_link_keeps_unknown_and_raw_pose_absent(self):
        w = self.world()
        w.sim.q = [9.] * 6
        w.real = None
        self.assertIsNone(w.live_state()["fsm"])
        self.assertIsNone(w.live_state()["enabled"])
        self.assertIsNone(w.live_state()["err_code"])
        s = w.snapshot(force_tele=True)
        self.assertIsNone(s["real"]["joints"])
        self.assertIsNone(s["tcp"])
        self.assertFalse(s["joints"]["known"])
        self.assertNotEqual(s["joints"]["q"], w.sim.q)

    async def test_snapshot_exposes_raw_real_joints_including_real_zero(self):
        w = self.world()
        self.assertEqual(w.snapshot()["real"]["joints"], [0.] * 6)
        self.assertTrue(w.joints_known())

    async def test_ready_rejects_unknown_fsm_and_unconfirmed_enable(self):
        for fsm, enabled in ((5, 0), (34, 1), (33, 0)):
            w = self.world(fsm, enabled)
            self.assertFalse((await w.ready())["ok"])
            self.assertFalse(w.real.calls)

    async def test_manual_advance_rejects_stale_or_wrong_source(self):
        for action in ("electrify", "start_master", "enable", "standby"):
            w = self.world(5, 0)
            self.assertFalse((await w.power(action))["ok"])
            self.assertFalse(w.real.calls)
        w = self.world(24, 0)
        w.real.snap["stamp"] -= 1
        self.assertFalse((await w.power("enable"))["ok"])
        self.assertFalse(w.real.calls)

    async def test_each_manual_step_requires_new_confirming_frame(self):
        for action, fsm, target in (("electrify", 7, 14), ("start_master", 14, 24), ("enable", 24, 33)):
            w = self.world(fsm, 0)
            result = await w.power(action)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["outcome"], "confirmed")
            self.assertEqual(w.live_fsm(), target)
            self.assertEqual(len(w.real.calls), 1)

    async def test_ack_without_new_frame_is_not_success(self):
        w = self.world(24, 0)
        w.real.feedback = False
        async def old_state(_name):
            w.real.publish(33, 1)  # Arrives before ACK; cannot itself confirm the ACK.
        w.real.hook = old_state
        result = await w.power("enable")
        self.assertFalse(result["ok"])
        self.assertTrue(result["reply"]["ok"])
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(w.real.calls, ["GrpEnable"])

    async def test_new_fsm33_frame_without_enabled_is_not_success(self):
        w = self.world(24, 0)
        w.real.feedback = False
        task = asyncio.create_task(w.power("enable"))
        await asyncio.sleep(.005)
        w.real.publish(33, 0)
        await asyncio.sleep(.005)
        self.assertFalse(task.done())
        w.real.publish(33, 1)
        self.assertTrue((await task)["ok"])

    async def test_reconnect_or_mode_change_cannot_confirm_old_operation(self):
        for change in ("epoch", "link", "mode"):
            w = self.world(24, 0)
            original = w.real
            original.feedback = False
            task = asyncio.create_task(w.power("enable"))
            await asyncio.sleep(.005)
            if change == "epoch":
                original.epoch += 1
                original.publish(33, 1)
            elif change == "link":
                w.real = FakePowerController(33, 1)
                self.controllers.append(w.real)
            else:
                w.settings["mode"] = "sim"
            result = await task
            self.assertFalse(result["ok"], change)
            self.assertEqual(original.calls, ["GrpEnable"])
            if change == "link":
                self.assertFalse(w.real.calls)

    async def test_rejection_preserves_controller_text_without_retry(self):
        for code, text in ((20018, "This command is prohibited in the current robot state"),
                           (20904, "Emergency stop reset timeout")):
            w = self.world()
            w.real.result = Reply("Electrify", False, [str(code), "0", text], code)
            result = await w.power("electrify")
            self.assertFalse(result["ok"])
            self.assertIn(text, result["why"])
            self.assertEqual(w.real.calls, ["Electrify"])

    async def test_reset_rejection_survives_telemetry_epoch_change(self):
        w = self.world(5, 0)
        async def stale(_name):
            w.real.epoch += 1
            w.real.publish(5, 0)
        w.real.hook = stale
        w.real.result = Reply("GrpReset", False, ["20904", "Emergency stop reset timeout"], 20904)
        result = await w.power("reset")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reply"]["err_code"], 20904)
        self.assertIn("Emergency stop reset timeout", result["why"])

    async def test_connect_box_ack_is_not_master_initialization(self):
        w = self.world(14, 0)
        w.real.feedback = False
        result = await w.power("init")
        self.assertTrue(result["ok"], result)
        self.assertEqual(w.real.calls, ["ConnectToBox"])
        self.assertEqual(w.real.snap["fsm"], 14)
        self.assertNotIn("初始化", result["label"])

    async def test_ready_runs_verified_steps_and_confirms_enable(self):
        w = self.world()
        self.assertTrue((await w.ready())["ok"])
        await w._ready_task
        self.assertEqual(w.real.calls, ["Electrify", "StartMaster", "GrpEnable"])
        self.assertTrue(w.motion_ok())
        self.assertEqual(w.ready_state["why"], "")

    async def test_ready_stops_after_rejection_or_new_connection(self):
        w = self.world()
        w.real.result = Reply("Electrify", False, ["20018", "blocked by current state"], 20018)
        await w.ready()
        await w._ready_task
        self.assertEqual(w.real.calls, ["Electrify"])
        self.assertIn("blocked by current state", w.ready_state["why"])
        w = self.world()
        async def reconnect(_name):
            w.real.epoch += 1
            w.real.publish(14, 0)
        w.real.hook = reconnect
        await w.ready()
        await w._ready_task
        self.assertEqual(w.real.calls, ["Electrify"])
        self.assertTrue(w.ready_state["why"])

    async def test_cancel_ready_task_cannot_progress_to_enable(self):
        w = self.world()
        w.real.feedback = False
        await w.ready()
        await asyncio.sleep(.005)
        w._ready_task.cancel()
        await asyncio.gather(w._ready_task, return_exceptions=True)
        self.assertEqual(w.real.calls, ["Electrify"])
        self.assertFalse(w.ready_state["running"])

    async def test_simulation_power_sequence_and_motion_policy_are_preserved(self):
        w = self.world()
        w.settings["mode"] = "sim"
        for action, target in (("electrify", 14), ("start_master", 24), ("enable", 33)):
            self.assertTrue((await w.power(action))["ok"])
            self.assertEqual(w.sim_state["fsm"], target)
        w.sim_state.update(fsm=7, enabled=0)
        self.assertTrue(w.motion_ok())
        self.assertTrue((await w.ready())["ok"])
        await w._ready_task
        self.assertEqual(w.sim_state["fsm"], 33)
        self.assertEqual(w.sim_state["enabled"], 1)
        self.assertFalse(w.real.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
