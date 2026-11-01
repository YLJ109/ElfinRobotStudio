"""Nonzero accepted start -> real chess preflight, disposable runner only."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio,time,unittest
from backend.core.world import World
from backend.robot_play_routes import RobotPlay
from backend.play_routes import PlayService

RECORDED=[101.629,-10.347,-127.27,-155.56,-29.154,-21.647]
class ChessPreflight(unittest.IsolatedAsyncioTestCase):
 async def test_recorded_pose_preflight_cancel_releases_worker_without_plan_or_motion(self):
  w=World();w._save_preferences({'mode':'sim'});w.sim.q=list(RECORDED)
  play=w.robot_play=RobotPlay(w);group=PlayService(w).setup({'mode':'chess'})['group']
  task=asyncio.create_task(play.generate({'group':group,'action':'chess','symbol':'O','slot':4,'clearance':120,'speed':15}))
  try:
   end=time.monotonic()+5
   while not play.busy and time.monotonic()<end:await asyncio.sleep(.01)
   self.assertTrue(play.busy)
   snapshot=w.snapshot();self.assertTrue(snapshot['robot_play']['planning'])
   self.assertEqual(snapshot['robot_play']['progress'],play.progress)
   self.assertFalse(snapshot['moving'],'planning projection must not invent motion')
   play.cancel_planning()
   with self.assertRaisesRegex(RuntimeError,'取消'):await asyncio.wait_for(task,3)
   self.assertFalse(play.busy);self.assertEqual(play.plans,{})
   self.assertFalse(w.snapshot()['robot_play']['planning'])
   self.assertEqual(w.sim.q,RECORDED);self.assertIsNone(w.sim.traj)
   self.assertIsNone(play.active);self.assertEqual(w.runner.state,'idle')
  finally:
   if not task.done():play.cancel_planning();await asyncio.gather(task,return_exceptions=True)
   await w.stop_execution('isolated cancelled chess preflight done')
 async def test_recorded_manual_pose_then_setup_and_o_centre(self):
  w=World();w._save_preferences({'mode':'sim'});w.sim.q=list(RECORDED);w.executor.epoch=1
  play=w.robot_play=RobotPlay(w);group=PlayService(w).setup({'mode':'chess'})['group'];before=list(w.sim.q)
  began=time.monotonic();task=asyncio.create_task(play.generate({'group':group,'action':'chess','symbol':'O','slot':4,'clearance':120,'speed':15}));trace=[];last=None;beats=0
  try:
   while not task.done():
    state=play.state();stage=(state['progress'].get('step'),state['progress'].get('kind'))
    if stage!=last:trace.append((round(time.monotonic()-began,3),stage));last=stage
    beats+=1;await asyncio.sleep(.02)
   result=await task
   self.assertTrue(result['ok']);self.assertEqual(result['cycles'],1);self.assertEqual(result['slot'],4)
   self.assertEqual(w.sim.q,before);self.assertIsNone(w.sim.traj);self.assertIsNone(play.active);self.assertEqual(w.runner.state,'idle');self.assertFalse(play.busy)
   self.assertGreater(beats,1,'preflight blocked the event loop')
   started=play.run({'plan_id':result['plan_id'],'source':result['source']})
   self.assertTrue(started['ok'],started)
   end=time.monotonic()+60;held=False
   while time.monotonic()<end and w.runner.state!='finished':
    w._tick(.05,time.monotonic());held=held or play.held is not None
    self.assertNotEqual(w.runner.state,'aborted',w.runner.error)
    await asyncio.sleep(.002)
   self.assertEqual(w.runner.state,'finished',w.runner.snapshot());self.assertTrue(held)
   placed=[o for o in w.objects.list() if o.get('task',{}).get('slot')==4]
   self.assertEqual(len(placed),1);self.assertEqual(placed[0]['task']['symbol'],'O');self.assertIsNone(play.held)
  finally:
   print('CHESS_RECORDED_PREFLIGHT',dict(elapsed=time.monotonic()-began,beats=beats,trace=trace),flush=True)
   if not task.done():play.cancel_planning();await asyncio.gather(task,return_exceptions=True)
   await w.stop_execution('isolated chess preflight done')
if __name__=='__main__':unittest.main()
