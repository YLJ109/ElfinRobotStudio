"""Handling challenge scoring and lifecycle; fake adapter plus actual simulation."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from backend.core import config
from backend.core.world import World
from backend.core.objects import ObjectStore
from backend.play_routes import PlayService
from backend.application.handling_challenge import HandlingChallenge


class FakePlay:
    def __init__(self,world):
        self.world=world;self.scene=PlayService(world);self.plans={};self.active=None;self.held=None;self.busy=False
    async def generate(self,payload,*,bound_targets=None):
        self.targets=bound_targets;self.plans={'fake':{'id':'fake'}}
        return {'ok':True,'plan_id':'fake','source':'fake checked source','cycles':len(bound_targets)}
    def run(self,payload):
        self.active=self.plans.pop(payload['plan_id']);self.world.runner.state='running';return {'ok':True}
    def cancel_planning(self):self.busy=False;return {'ok':True}
    def emit(self,event,**values):self.event_observer(event,self.active,**values)
    def context(self,controlled):
        from backend.robot_play_routes import RobotPlay
        return RobotPlay.context(self,controlled)


class Challenge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state=patch.object(config,'_settings',deepcopy(config.DEFAULTS));state.start();self.addCleanup(state.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.world=World();self.world.objects=ObjectStore(Path(self.temp.name)/'objects.json')
        self.play=self.world.robot_play=FakePlay(self.world);self.now=10.
        self.challenge=HandlingChallenge(self.world,clock=lambda:self.now)

    async def ready(self,level=1):
        self.challenge.setup({'level':level})
        result=await self.challenge.inspect({'challenge_id':self.challenge.challenge['id']})
        self.assertTrue(result['ok'])
        return result

    def start(self):
        return self.challenge.run({'challenge_id':self.challenge.challenge['id'],'plan_id':self.challenge.plan['plan_id']})

    def commit(self,index=0):
        piece,goal,slot=self.play.targets[index]
        changed=self.world.objects.update(piece['id'],{'xyz':goal})
        with self.world.objects._lock:
            next(o for o in self.world.objects.items if o['id']==piece['id'])['task']['slot']=slot
        self.play.emit('placed',object_id=piece['id'],xyz=goal,slot=slot,stamp=self.now)

    async def test_setup_has_finite_levels_locked_obstacles_and_does_not_start(self):
        result=self.challenge.setup({'level':3})
        self.assertEqual(self.world.runner.state,'idle')
        rows=self.world.objects.list()
        self.assertEqual(len([o for o in rows if o.get('task',{}).get('role')=='piece']),3)
        obstacles=[o for o in rows if o.get('task',{}).get('role')=='obstacle']
        self.assertEqual(len(obstacles),2);self.assertTrue(all(o['locked'] and o['collide'] for o in obstacles))
        with self.assertRaises(Exception):self.challenge.setup({'level':99})

    async def test_success_needs_actual_placement_event_and_runner_terminal(self):
        await self.ready();self.now=100.
        self.start();self.assertEqual(self.challenge.state()['elapsed_s'],0)
        self.now=105.;self.commit()
        self.assertNotEqual(self.challenge.state()['phase'],'completed')
        self.world.runner.state='finished';self.play.held=None
        self.play.emit('terminal',state='finished',stamp=self.now)
        result=self.challenge.state();self.assertEqual(result['phase'],'completed')
        self.assertEqual(result['score'],100);self.assertEqual(result['elapsed_s'],5)

    async def test_finished_runner_without_placement_and_external_teleport_never_scores_success(self):
        await self.ready();self.start()
        piece,goal,_=self.play.targets[0];self.world.objects.update(piece['id'],{'xyz':goal})
        self.world.runner.state='finished';self.play.emit('terminal',state='finished',stamp=self.now)
        self.assertEqual(self.challenge.state()['completed'],0)
        self.assertEqual(self.challenge.state()['phase'],'failed')

    async def test_partial_stop_is_separate_and_restart_never_reuses_old_goals_or_scores(self):
        await self.ready(2);self.start();self.now+=2;self.commit(0)
        self.play.emit('terminal',state='aborted',stamp=self.now)
        result=self.challenge.state();self.assertEqual(result['completed'],1)
        self.assertEqual(result['phase'],'cancelled');self.assertLess(result['score'],100)
        old_group=result['group'];self.play.active=None;self.world.runner.state='aborted'
        self.challenge.setup({'level':1});new=self.challenge.state()
        self.assertNotEqual(old_group,new['group']);self.assertEqual(new['completed'],0);self.assertEqual(new['score'],0)

    async def test_stale_ticket_scene_change_and_unrelated_terminal_are_rejected(self):
        await self.ready();self.world.objects.add({'name':'changed','kind':'box'})
        with self.assertRaises(Exception):self.start()
        self.assertNotEqual(self.world.runner.state,'running')
        self.challenge.event('terminal',{'challenge_id':'old'},state='finished',stamp=self.now)
        self.assertNotEqual(self.challenge.state()['phase'],'completed')

    async def test_old_run_ticket_and_mode_change_do_not_start(self):
        await self.ready()
        with self.assertRaises(Exception):self.challenge.run({'challenge_id':'old','plan_id':'fake'})
        self.world.settings['mode']='real'
        with self.assertRaises(Exception):self.start()
        self.assertEqual(self.world.runner.state,'idle')


class ActualSimulation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        state=patch.object(config,'_settings',deepcopy(config.DEFAULTS));state.start();self.addCleanup(state.stop)
        from backend.robot_play_routes import RobotPlay
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.world=World();self.world.objects=ObjectStore(Path(self.temp.name)/'actual-objects.json');self.world.real=None
        self.play=self.world.robot_play=RobotPlay(self.world)
        self.challenge=HandlingChallenge(self.world)

    async def asyncTearDown(self):
        await self.world.stop_execution('离线闯关检查结束')

    async def pump(self,predicate,timeout=35):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            self.world._tick(.05,time.monotonic())
            if predicate():return
            if self.world.runner.state in ('error','aborted'):
                self.fail('Actual runner failed: '+str(self.world.runner.snapshot()))
            await asyncio.sleep(.002)
        self.fail('Actual simulation timeout: '+str(self.world.runner.snapshot()))

    async def test_real_production_preflight_runner_placement_and_terminal_score(self):
        state=self.challenge.setup({'level':1})
        result=await self.challenge.inspect({'challenge_id':state['challenge_id']})
        self.assertTrue(result['ok'])
        state=self.challenge.run({'challenge_id':state['challenge_id'],'plan_id':result['plan_id']})
        self.assertEqual(state['completed'],0)
        await self.pump(lambda:self.challenge.phase in ('completed','failed','cancelled'))
        state=self.challenge.state()
        self.assertEqual(state['phase'],'completed',state)
        self.assertEqual(state['completed'],1);self.assertEqual(state['score'],100)
        self.assertGreater(state['elapsed_s'],0)
        self.assertIsNone(self.play.held)

    async def test_actual_stop_while_held_keeps_piece_and_does_not_grant_completion(self):
        state=self.challenge.setup({'level':1})
        result=await self.challenge.inspect({'challenge_id':state['challenge_id']})
        self.challenge.run({'challenge_id':state['challenge_id'],'plan_id':result['plan_id']})
        await self.pump(lambda:self.play.held is not None)
        await self.challenge.cancel()
        state=self.challenge.state()
        self.assertEqual(state['phase'],'cancelled')
        self.assertLess(state['score'],100);self.assertIsNotNone(self.play.held)
        with self.assertRaises(Exception):self.challenge.setup({'level':1})
        self.assertTrue((await self.world.tool_action('vacuum_off',side='left'))['ok'])
        self.assertIsNone(self.play.held)
        self.assertEqual(self.challenge.setup({'level':1})['completed'],0)


if __name__=='__main__':unittest.main()
