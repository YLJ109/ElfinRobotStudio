"""Shared full-course acceptance, real-time tick, no production modification."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio,copy,json,math,tempfile,time
from pathlib import Path
from backend.core.world import World
from backend.robot_play_routes import RobotPlay
from backend.application.handling_challenge import HandlingChallenge
from backend.domain.handling_challenge import level_by_id

async def verify_course(test,level):
    with tempfile.TemporaryDirectory(prefix='actual-course-') as folder:
        world=World(data_dir=Path(folder));world.real=None
        play=world.robot_play=RobotPlay(world);challenge=HandlingChallenge(world)
        try:
            setup=challenge.setup({'level':level});rule=level_by_id(level)
            obstacles=copy.deepcopy([o for o in world.objects.list() if o.get('task',{}).get('role')=='obstacle'])
            test.assertEqual(len(obstacles),len(rule['obstacles']))
            test.assertTrue(all(o['collide'] and o['locked'] for o in obstacles))
            print(json.dumps(dict(level=level,stage='preflight',pieces=rule['pieces'],clearance=rule['clearance'],speed=world.settings['sim_speed_pct'])),flush=True)
            checked=await challenge.inspect({'challenge_id':setup['challenge_id']})
            test.assertEqual(checked['phase'],'checked')
            # 生成的任务跟随顶栏实时速度，所以程序文本里不再带 SPEED 行；
            # 只有调用方显式提供的 source 才能带用户 SPEED 指令。
            test.assertNotIn('SPEED ',checked['program'])
            plan_id=checked['plan_id'];run=challenge.run({'challenge_id':setup['challenge_id'],'plan_id':plan_id})
            test.assertEqual(run['phase'],'running');test.assertEqual(world.runner.run_id,plan_id)
            started=time.monotonic();previous=started;deadline=started+450
            picked=set();moving_frames=0
            while challenge.phase=='running' and time.monotonic()<deadline:
                now=time.monotonic();dt=now-previous;previous=now
                # Advance by actual elapsed time only. Never accelerate the clock
                # or override the generated program speed to shorten acceptance.
                world._tick(dt,now)
                if play.held and play.held['id'] not in picked:
                    picked.add(play.held['id']);print(json.dumps(dict(level=level,stage='picked',count=len(picked),elapsed_s=round(now-started,3))),flush=True)
                if world.sim.traj is not None:moving_frames+=1
                if world.runner.state in ('error','aborted'):
                    test.fail('Production runner failed: '+json.dumps(world.runner.snapshot(),ensure_ascii=False))
                await asyncio.sleep(.02)
            state=challenge.state();test.assertEqual(state['phase'],'completed',(state,world.runner.snapshot()))
            test.assertEqual(world.runner.state,'finished');test.assertFalse(world.executor.busy)
            test.assertIsNone(play.active);test.assertIsNone(play.held)
            test.assertEqual(dict(world._sim_vacuum),{'vacuum':False,'blow':False})
            test.assertEqual(state['completed'],rule['pieces']);test.assertEqual(state['total'],rule['pieces'])
            test.assertEqual(state['score'],100);test.assertGreater(state['elapsed_s'],0)
            test.assertEqual(state['run_id'],plan_id);test.assertEqual(picked,set(state['goals']))
            test.assertEqual(challenge.confirmed,set(state['goals']));test.assertGreater(moving_frames,0)
            objects={o['id']:o for o in world.objects.list()};actual=[]
            for oid,goal in state['goals'].items():
                item=objects[oid];distance=math.dist(item['xyz'],goal['xyz'])
                test.assertEqual(item['task']['slot'],goal['slot']);test.assertEqual(item['task']['group'],state['group'])
                test.assertLessEqual(distance,.5)
                actual.append(dict(id=oid,slot=item['task']['slot'],xyz=item['xyz'],goal=goal['xyz'],error_mm=distance))
            test.assertEqual([o for o in world.objects.list() if o.get('task',{}).get('role')=='obstacle'],obstacles)
            journal=world.run_journal;record=journal.get(plan_id)
            test.assertEqual(record['phase'],'finished');test.assertEqual(record['context']['plan_id'],plan_id)
            test.assertEqual(record['context']['action'],'handling');test.assertIsNotNone(record['last_confirmed'])
            journal.flush();test.assertIsNone(journal.error)
            print(json.dumps(dict(level=level,stage='completed',run_id=plan_id,score=state['score'],completed=state['completed'],
                                  elapsed_s=state['elapsed_s'],actual_wall_s=round(time.monotonic()-started,3),
                                  moving_frames=moving_frames,confirmed_steps=record['confirmed_steps'],goals=actual,
                                  obstacles_unchanged=True,held=None,runner=world.runner.state)),flush=True)
        finally:
            await world.stop_execution('完整关卡隔离验收结束')
            await world.run_journal.close()
