"""Handling HTTP safety contracts; no running simulation loop or preflight."""
from _offline_guard import require_offline_copy
require_offline_copy()
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from backend.core import config
from backend.core.world import World
from backend.core.objects import ObjectStore
from backend.robot_play_routes import create_robot_play_router
from backend.handling_challenge_routes import create_handling_challenge_router


class API(unittest.IsolatedAsyncioTestCase):
    async def test_public_generation_has_no_arbitrary_bound_target_and_stale_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(config,'_settings',deepcopy(config.DEFAULTS)):
            world=World();world.objects=ObjectStore(Path(temp)/'objects.json')
            app=FastAPI()
            app.add_exception_handler(ValueError,lambda request,exc:JSONResponse({'ok':False,'why':str(exc)},status_code=422))
            app.include_router(create_robot_play_router(world));challenge_router=create_handling_challenge_router(world);app.include_router(challenge_router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                response=await client.post('/api/play/program',json={'group':'a'*32,'action':'handling','bound_targets':[]})
                self.assertEqual(response.status_code,422)
                self.assertEqual((await client.post('/api/challenge/handling/setup',json={'level':0})).status_code,422)
                self.assertEqual((await client.post('/api/challenge/handling/run',json={'challenge_id':'old','plan_id':'old'})).status_code,409)
                setup=(await client.post('/api/challenge/handling/setup',json={'level':1})).json()
                self.assertTrue(setup['ok']);self.assertEqual(setup['completed'],0)
                self.assertEqual((await client.post('/api/challenge/handling/inspect',json={'challenge_id':'old'})).status_code,409)
                challenge_router.challenge.phase='checked';challenge_router.challenge.plan={'plan_id':'fake-checked','source':'not executable'}
                world.robot_play.plans['fake-checked']={'id':'fake-checked'}
                result=(await client.post('/api/challenge/handling/cancel',json={'challenge_id':setup['challenge_id']})).json()
                self.assertEqual(result['phase'],'cancelled');self.assertEqual(result['score'],0)
                self.assertNotIn('fake-checked',world.robot_play.plans)
                self.assertEqual(world.runner.state,'idle');self.assertIsNone(world.robot_play.active)


if __name__=='__main__':unittest.main()
