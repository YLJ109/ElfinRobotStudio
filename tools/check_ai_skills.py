"""AI composite tasks: isolated data, fake model, actual Drawing/Runner."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio
import copy
import time
import unittest
from unittest.mock import patch
import check_drawing as fixtures
from backend.core import config
from backend.application import ai_skills

class AiDrawing(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.DrawingTests.setUp
    asyncTearDown=fixtures.DrawingTests.asyncTearDown
    pump=fixtures.DrawingTests.pump

    async def proposal(self,text='P1到起点,然后绘制爱心'):
        with patch.object(config,'zhipu_key',return_value=''):
            return await self.w.ai(text,[],'isolated-skill')

    async def driven(self,coroutine,seconds=90):
        """The live World ticks every frame; the isolated fixture must drive it
        while a composed task moves, otherwise the executor never advances."""
        task=asyncio.ensure_future(coroutine);deadline=time.monotonic()+seconds
        while not task.done():
            self.w._tick(.02,time.monotonic());await asyncio.sleep(.001)
            if time.monotonic()>deadline:
                task.cancel();self.fail('组合任务在 %s 秒内未返回'%seconds)
        return await task

    async def test_full_start_point_then_heart_uses_one_checked_runner_and_real_ink(self):
        point=self.w.points.add('演示起点',[0,5,-10,0,5,0]);self.assertEqual(point['code'],'P1')
        self.w._tick(.01,time.monotonic());before=self.w.current_joints()[:]
        r=await self.proposal();self.assertEqual(r['kind'],'plan',r);self.assertEqual(r['code'],'')
        self.assertEqual(r['skill_plan'],{'kind':'drawing','shape':'heart'})
        self.assertEqual(self.w.current_joints(),before);self.assertEqual(self.w.runner.state,'idle')
        started=await self.driven(self.w.ai_run(r['plan'],r['plan_id'],drawing=self.d))
        self.assertTrue(started.get('ok'),started);self.assertEqual(started['drawing_plan_id'],self.d.plan['id'])
        # The prefix point goes through the one plan/executor entry, so the
        # Runner only ever holds this drawing plan's own checked steps.
        self.assertEqual(len(self.w.runner.steps),len(self.d.plan['steps']))
        self.assertTrue(all(step['drawing_plan_id']==self.d.plan['id'] for step in self.w.runner.steps))
        self.assertFalse(self.w.runner.steps[0]['drawing_trace'])
        self.assertEqual(self.w.settings['sim_tool']['kind'],'pen')
        self.assertEqual(self.d.plan['start'],point['joints'])
        self.assertTrue(self.d.plan['config']['shape']=='heart')
        await self.pump(lambda:self.d.state()['phase']=='completed',60)
        self.assertEqual(self.w.runner.completed_index,len(self.w.runner.steps));self.assertTrue(self.d.state()['actual_samples'])
        self.assertFalse((await self.driven(self.w.ai_run(r['plan'],r['plan_id'],drawing=self.d)))['ok'],'the whole proposal is one-shot')

    async def test_unknown_point_unsupported_suffix_and_real_mode_never_publish_partial_motion(self):
        self.w.points.add('起点',[0]*6);before=self.w.current_joints()[:]
        for text in ['P9到起点,然后绘制爱心','P1到起点,然后绘制爱心再分拣']:
            r=await self.proposal(text);self.assertEqual(r['plan'],[]);self.assertNotIn('plan_id',r)
        self.w.settings['mode']='real'
        r=await self.proposal();self.assertEqual(r['plan'],[]);self.assertNotIn('plan_id',r)
        self.w.settings['mode']='sim';self.assertEqual(self.w.current_joints(),before)

    async def test_rejected_outline_never_moves_to_the_prefix_point(self):
        self.w.points.add('起点',[0,5,-10,0,5,0]);self.w._tick(.01,time.monotonic())
        r=await self.proposal();before=self.w.current_joints()[:]
        with patch.object(self.d,'preview',side_effect=ValueError('后续图案不可达')):
            result=await self.driven(self.w.ai_run(r['plan'],r['plan_id'],drawing=self.d))
        self.assertFalse(result['ok']);self.assertIn('不可达',result['why'])
        self.assertEqual(self.w.current_joints(),before);self.assertEqual(self.w.runner.state,'idle')

if __name__=='__main__':unittest.main()
