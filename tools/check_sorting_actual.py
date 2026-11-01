"""One actual production circle pick/place, isolated simulation only."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio,tempfile,time,unittest
from pathlib import Path
from backend.core.world import World
from backend.robot_play_routes import RobotPlay
from check_sorting_challenge import pixel_shapes

class Actual(unittest.IsolatedAsyncioTestCase):
    async def test_observed_circle_runs_actual_runner_and_scores_only_after_place(self):
        with tempfile.TemporaryDirectory() as folder:
            world=World(data_dir=Path(folder));play=world.robot_play=RobotPlay(world)
            try:
                group=play.scene.setup({'mode':'sorting','criterion':'shape','shape_counts':{'rectangle':0,'circle':1}})['group']
                pieces,_,_=play.scene.group(world.objects.list(),group)
                engine,expected=pixel_shapes(pieces);play.vision_workspace=engine
                self.assertEqual(expected[pieces[0]['id']],'circle')
                checked=await play.generate({'group':group,'action':'sorting','criterion':'shape','clearance':120,'speed':15})
                self.assertTrue(checked['ok']);self.assertIsNone(play.state()['sorting_score'])
                self.assertTrue(play.run({'plan_id':checked['plan_id'],'source':checked['source']})['ok'])
                self.assertEqual(play.state()['sorting_score']['completed'],0)
                deadline=time.monotonic()+60
                while play.active and time.monotonic()<deadline:
                    world._tick(.05,time.monotonic());await asyncio.sleep(.002)
                value=play.state()['sorting_score']
                self.assertEqual(value['status'],'completed',(value,world.runner.snapshot()))
                self.assertEqual(value['completed'],1);self.assertEqual(value['correct'],1)
                self.assertEqual(value['accuracy_pct'],100.);self.assertEqual(value['score'],100)
                self.assertGreater(value['elapsed_s'],0);self.assertIsNone(play.held)
                self.assertEqual(world.objects.persisted_disk,world.objects.disk_signature())
                play.scene.reset({'group':group});self.assertIsNone(play.state()['sorting_score'])
            finally:await world.stop_execution('形状分拣隔离检查结束')

if __name__=='__main__':unittest.main()
