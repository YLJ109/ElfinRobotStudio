"""Immutable Cartesian endpoint and same-feedback arrival evidence; no hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()

import unittest
import tempfile
import time
from unittest.mock import AsyncMock,patch
from pathlib import Path
from dataclasses import FrozenInstanceError
from backend.domain.motion_target import MotionTarget, ArrivalEvidence
from backend.main import build_dependencies
from backend.main import create_app
import httpx


class TargetContract(unittest.TestCase):
    def target(self):
        return MotionTarget.create(requested=[0,0,0,0,0,180], achieved=[0,0,0,0,0,-180],
            joints=[0]*6, start=[1]*6, context='context-A', session='session-A',
            connection=7, frame_seq=12, created_at=1., method='relative-joint-compatible')

    def test_plan_is_immutable_and_public_copy_cannot_change_branch(self):
        plan=self.target()
        with self.assertRaises(FrozenInstanceError):plan.context='changed'
        public=plan.snapshot();public['joints'][0]=99
        self.assertEqual(plan.joints,(0.,)*6)
        self.assertNotEqual(plan.plan_id,self.target().plan_id)

    def test_repeated_or_pre_ack_frames_cannot_complete_hold(self):
        proof=ArrivalEvidence(self.target(),hold_s=.5)
        sample=dict(connection=7,frame_seq=12,captured_at=1.,now=1.01,
                    joints=[0]*6,pose=[0,0,0,0,0,-180],settled=True,fresh=True)
        self.assertFalse(proof.observe(**sample)['reached'])
        sample.update(frame_seq=13,captured_at=1.1,now=1.11)
        self.assertFalse(proof.observe(**sample)['reached'])
        sample['now']=2.
        self.assertFalse(proof.observe(**sample)['reached'])

    def test_new_fresh_frames_and_so3_equivalence_confirm_after_hold(self):
        proof=ArrivalEvidence(self.target(),hold_s=.5)
        for seq,stamp in ((13,1.1),(14,1.3),(15,1.5),(16,1.7)):
            result=proof.observe(connection=7,frame_seq=seq,captured_at=stamp,now=stamp+.01,
                joints=[0]*6,pose=[0,0,0,0,0,180],settled=True,fresh=True)
        self.assertTrue(result['reached'])
        self.assertLess(result['tcp_rotation_error_deg'],1e-6)

    def test_fresh_packets_separated_by_feedback_gap_cannot_prove_continuous_hold(self):
        proof=ArrivalEvidence(self.target(),hold_s=.5)
        for seq,stamp in ((13,1.1),(14,1.8)):
            result=proof.observe(connection=7,frame_seq=seq,captured_at=stamp,now=stamp+.01,
                joints=[0]*6,pose=[0,0,0,0,0,180],settled=True,fresh=True)
        self.assertFalse(result['reached'])

    def test_unknown_tcp_cross_connection_stale_and_midstop_never_reach(self):
        for patch in ({'pose':None},{'connection':8},{'fresh':False},{'joints':[2]*6},
                      {'pose':[5,0,0,0,0,180]},{'settled':False}):
            proof=ArrivalEvidence(self.target(),hold_s=.5)
            for seq,stamp in ((13,1.1),(14,1.8)):
                sample=dict(connection=7,frame_seq=seq,captured_at=stamp,now=stamp+.01,
                    joints=[0]*6,pose=[0,0,0,0,0,180],settled=True,fresh=True)
                sample.update(patch);result=proof.observe(**sample)
            self.assertFalse(result['reached'],patch)


class WorldTarget(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.world=build_dependencies(data_dir=Path(self.directory.name)).world

    def bind(self):
        world=self.world;q=world.current_joints();pose=world.motion_model().pose_vec(q)
        binding=world.register_motion_target({'ok':True,'requested':pose,'achieved':pose,'joints':q},
            start=q,context=world.plan_context())
        self.assertTrue(binding['ok'])
        self.assertTrue(world.confirm_motion_target(q,binding['plan_id'])['ok'])
        return q,binding['plan_id']

    async def test_new_simulation_program_revokes_old_target_ghost_only_after_start(self):
        self.world.set_preview([1, 0, 0, 0, 0, 0])
        with patch.object(self.world, '_need', return_value=True), patch.object(self.world.runner, 'start', return_value=False):
            self.assertFalse(self.world.start_program()['ok'])
            self.assertEqual(self.world.preview_q, [1, 0, 0, 0, 0, 0])
        with patch.object(self.world, '_need', return_value=True), patch.object(self.world.runner, 'start', return_value=True):
            self.assertTrue(self.world.start_program()['ok'])
            self.assertIsNone(self.world.preview_q)
            self.assertIsNone(self.world._preview_target)

    async def test_context_change_and_branch_change_never_submit(self):
        q,identity=self.bind()
        with patch.object(self.world,'move_joints',new_callable=AsyncMock) as submit:
            wrong=q.copy();wrong[2]+=.01
            self.assertFalse((await self.world.execute_motion_target(wrong,identity))['ok'])
            self.world.settings['fence']={'changed':True}
            self.assertFalse((await self.world.execute_motion_target(q,identity))['ok'])
            submit.assert_not_awaited()

    async def test_same_target_new_preview_revokes_old_plan_and_consumption_is_single_use(self):
        q,old=self.bind();q,new=self.bind()
        with patch.object(self.world,'move_joints',new_callable=AsyncMock,return_value={'ok':True}) as submit:
            self.assertFalse((await self.world.execute_motion_target(q,old))['ok'])
            self.assertTrue((await self.world.execute_motion_target(q,new))['ok'])
            self.assertFalse((await self.world.execute_motion_target(q,new))['ok'])
            self.assertEqual(submit.await_count,1)

    async def test_browsing_never_completes_and_accepted_simulation_uses_shared_hold(self):
        q,identity=self.bind();now=time.monotonic()
        self.world._tick_preview(False,now+1)
        self.assertIsNotNone(self.world.preview_q)
        with patch.object(self.world,'move_joints',new_callable=AsyncMock,return_value={'ok':True}):
            self.assertTrue((await self.world.execute_motion_target(q,identity))['ok'])
        accepted=self.world._arrival_evidence.target.created_at
        self.world._tick_preview(False,accepted+.01)
        self.assertFalse(self.world._target_evidence['reached'])
        self.assertIsNotNone(self.world.preview_q)
        for offset in (.2,.4,.6):self.world._tick_preview(False,accepted+offset)
        self.assertTrue(self.world._target_evidence['reached'])
        self.assertIsNone(self.world.preview_q)

    async def test_http_ik_preview_and_execute_preserve_one_branch_and_one_use(self):
        resources=build_dependencies(data_dir=Path(self.directory.name)/'http')
        world=resources.world;q=world.current_joints();pose=world.motion_model().pose_vec(q)
        solved={'ok':True,'requested':pose,'achieved':pose,'joints':q}
        app=create_app(resources)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
            self.assertFalse((await client.post('/api/movej',json={'joints':q})).json()['ok'])
            self.assertFalse((await client.post('/api/movej',json={'joints':q,'allowed_axes':[5]})).json()['ok'])
            with patch.object(resources.ik_previews,'solve',new_callable=AsyncMock,return_value=solved):
                result=(await client.post('/api/ik',json={'target':pose})).json()
            self.assertTrue(result['ok']);identity=result['plan_id']
            bad=q.copy();bad[0]+=.01
            self.assertFalse((await client.post('/api/preview',json={'joints':bad,'plan_id':identity})).json()['ok'])
            self.assertTrue((await client.post('/api/preview',json={'joints':q,'plan_id':identity})).json()['ok'])
            with patch.object(world,'move_joints',new_callable=AsyncMock,return_value={'ok':True}) as move:
                self.assertFalse((await client.post('/api/movej',json={'joints':q})).json()['ok'])
                self.assertFalse((await client.post('/api/movej',json={'joints':q,'plan_id':'forged'})).json()['ok'])
                self.assertTrue((await client.post('/api/movej',json={'joints':q,'plan_id':identity})).json()['ok'])
                self.assertFalse((await client.post('/api/movej',json={'joints':q,'plan_id':identity})).json()['ok'])
                self.assertEqual(move.await_count,1)

    async def test_http_axis_plan_preserves_restriction_all_the_way_to_owner(self):
        resources=build_dependencies(data_dir=Path(self.directory.name)/'axes-http')
        world=resources.world;q=world.current_joints()
        app=create_app(resources)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
            for invalid in ([],[5,5],[True],[5.0],['5'],[6]):
                self.assertEqual((await client.post('/api/preview',json={'joints':q,'allowed_axes':invalid})).status_code,422)
            preview=(await client.post('/api/preview',json={'joints':q,'allowed_axes':[5]})).json()
            self.assertTrue(preview['ok']);identity=preview['plan_id']
            self.assertEqual(preview['target_plan']['allowed_axes'],[5])
            with patch.object(world,'move_joints',new_callable=AsyncMock,return_value={'ok':True}) as move:
                for mask in (None,[0,5],[0,1,2,3,4,5]):
                    refused=(await client.post('/api/movej',json={'joints':q,'plan_id':identity,'allowed_axes':mask})).json()
                    self.assertFalse(refused['ok'])
                move.assert_not_awaited()
                result=(await client.post('/api/movej',json={'joints':q,'plan_id':identity,'allowed_axes':[5]})).json()
                self.assertTrue(result['ok'])
                move.assert_awaited_once_with(q,allowed_axes=(5,))

    async def test_cancelled_or_superseded_execution_never_completes_old_plan(self):
        for change in ('cancel','replace'):
            q,identity=self.bind()
            with patch.object(self.world,'move_joints',new_callable=AsyncMock,return_value={'ok':True}):
                self.assertTrue((await self.world.execute_motion_target(q,identity))['ok'])
            accepted=self.world._arrival_evidence.target.created_at
            if change=='cancel':self.world.executor.revoke('test cancellation')
            else:self.world.executor.started=12345
            self.world._tick_preview(False,accepted+.1)
            self.world._tick_preview(False,accepted+.8)
            self.assertFalse(self.world._target_evidence['reached'])
            self.assertIsNotNone(self.world.preview_q)

    async def test_axis_restriction_is_immutable_and_cannot_be_dropped_or_widened(self):
        world=self.world;q=world.current_joints();pose=world.motion_model().pose_vec(q)
        result=world.register_motion_target({'ok':True,'requested':pose,'achieved':pose,'joints':q},
            start=q,context=world.plan_context(),allowed_axes=[5])
        identity=result['plan_id']
        self.assertEqual(result['target_plan']['allowed_axes'],[5])
        self.assertTrue(world.confirm_motion_target(q,identity,allowed_axes=[5])['ok'])
        with patch.object(world,'move_joints',new_callable=AsyncMock,return_value={'ok':True}) as move:
            self.assertFalse((await world.execute_motion_target(q,identity))['ok'])
            self.assertFalse((await world.execute_motion_target(q,identity,allowed_axes=[0,5]))['ok'])
            move.assert_not_awaited()
            self.assertTrue((await world.execute_motion_target(q,identity,allowed_axes=[5]))['ok'])
            move.assert_awaited_once_with(q,allowed_axes=(5,))


if __name__=='__main__':unittest.main(verbosity=2)
