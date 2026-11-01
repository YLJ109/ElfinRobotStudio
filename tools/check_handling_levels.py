"""Advanced handling courses against actual production collision preflight."""
from _offline_guard import require_offline_copy
require_offline_copy()
import unittest
from copy import deepcopy
from pathlib import Path
import tempfile
from unittest.mock import patch
from backend.core import config
from backend.core.world import World
from backend.core.objects import ObjectStore
from backend.robot_play_routes import RobotPlay
from backend.application.handling_challenge import HandlingChallenge


class Advanced(unittest.IsolatedAsyncioTestCase):
    async def test_second_and_third_course_preflight_keep_fixed_obstacle_checks(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(config,'_settings',deepcopy(config.DEFAULTS)):
            world=World();world.objects=ObjectStore(Path(temp)/'objects.json');world.real=None
            world.robot_play=RobotPlay(world);challenge=HandlingChallenge(world)
            for level in (2,3):
                with self.subTest(level=level):
                    setup=challenge.setup({'level':level})
                    obstacles=[item for item in world.objects.list() if item.get('task',{}).get('role')=='obstacle']
                    before=deepcopy(obstacles)
                    inspected=await challenge.inspect({'challenge_id':setup['challenge_id']})
                    self.assertEqual(inspected['phase'],'checked')
                    self.assertTrue(all(item['collide'] and item['locked'] for item in obstacles))
                    self.assertEqual([item for item in world.objects.list() if item.get('task',{}).get('role')=='obstacle'],before)
                    self.assertEqual(world.runner.state,'idle')


if __name__=='__main__':unittest.main()
