"""AI acceptance; disposable runner only, fake HTTP/model, no hardware."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import ai_plan, config, program
from backend.core.kinematics import RobotModel
from backend.core.world import World


class IntentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.model = RobotModel()
        self.points = [{'id': 47, 'code': 'P3', 'name': '放置台', 'joints': [1, 2, 3, 4, 5, 6]}]

    async def ask(self, text, history=None, resources=None):
        return await ai_plan.make_plan(text, history or [], self.points, self.model,
                                       dict(config.DEFAULTS), '', None, resources=resources)

    async def test_greeting_no_model_no_plan_even_after_history(self):
        with patch.object(ai_plan.httpx, 'AsyncClient', side_effect=AssertionError('no model')):
            r = await self.ask('你好', [{'role': 'assistant', 'content': 'speed50 P1 DO1'}])
        self.assertEqual(r['kind'], 'chat'); self.assertEqual(r['plan'], [])
        self.assertNotIn('plan_id', r)

    async def test_process_request_clarifies_no_guessed_io(self):
        r = await self.ask('吸取物体放P3回起点')
        self.assertEqual(r['kind'], 'clarify'); self.assertEqual(r['plan'], [])
        self.assertGreaterEqual(len(r['questions']), 3)

    async def test_combined_drawing_is_recognized_but_not_silently_reduced_to_a_point_move(self):
        self.points.append({'id':48,'code':'P1','name':'起点','joints':[0]*6})
        with patch.object(ai_plan.httpx,'AsyncClient',side_effect=AssertionError('external AI forbidden')):
            r=await ai_plan.make_plan('P1到起点,然后绘制爱心',[],self.points,self.model,
                                      dict(config.DEFAULTS),'fake-configured-key',allow_model=False)
        # 绘图组合已接入：结果是"先到起点再画爱心"，而不是被悄悄降级成一次点动。
        self.assertEqual(r['kind'],'plan')
        self.assertEqual(r['skill_plan'],{'kind':'drawing','shape':'heart'})
        self.assertEqual([step['op'] for step in r['plan']],['movej','drawing'])
        self.assertEqual(r['plan'][0]['point'],'P1')
        self.assertEqual(r['plan'][1]['shape'],'heart')
        self.assertEqual(r['source_kind'],'local_rules');self.assertNotIn('model_request',r)

    async def test_task_entry_registry_does_not_invent_executable_ai_skills(self):
        for text,skill,entry in [('去P3后写字','writing','有趣 → 写字'),('去P3后分拣','sorting','分拣'),
                                 ('去P3然后下棋','chess','有趣 → 井字棋'),('去P3再手掌跟随','following','有趣 → 跟随')]:
            r=await self.ask(text)
            self.assertEqual(r['plan'],[]);self.assertEqual(r['code'],'')
            self.assertEqual(r['unhandled_skills'][0]['skill'],skill);self.assertIn(entry,r['summary'])

    async def model_reply(self,text,obj):
        fake=AsyncMock();response=fake.post.return_value;response.raise_for_status=lambda:None
        response.json=lambda:{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj,ensure_ascii=False)}}]}
        with patch.object(ai_plan.httpx,'AsyncClient') as client:
            client.return_value.__aenter__.return_value=fake
            reply=await ai_plan.make_plan(text,[],self.points,self.model,dict(config.DEFAULTS),'offline-fake-key',allow_model=True)
        self.assertEqual(fake.post.call_count,1)
        sent=fake.post.call_args.kwargs['json']
        self.assertNotIn('放置台',str(sent));self.assertNotIn('offline-fake-key',str(sent));self.assertNotIn('joints',json.dumps(sent['messages'][1:]))
        return reply

    async def test_authorized_model_interprets_local_clarification_but_only_returns_parser_checked_proposal(self):
        text='让机械臂走向P3'
        r=await self.model_reply(text,{'kind':'plan','summary':'前往P3','current_request':text,'unhandled':[],
                                     'plan':[{'op':'movej','point':'P3'}]})
        self.assertEqual(r['kind'],'plan');self.assertEqual(r['source_kind'],'model')
        self.assertEqual(r['model_request']['status'],'success');self.assertTrue(r['model_request']['attempted'])
        self.assertNotIn('plan_id',r)
        steps=program.parse(r['code'],lambda _:None);self.assertEqual(len(steps),1)
        self.assertEqual(steps[0]['joints'],self.points[0]['joints'])

    async def test_model_cannot_skip_drawing_suffix_and_reports_actual_model_attempt(self):
        text='P3到起点,然后绘制爱心'
        # 模型只回了一个 movej、漏掉绘制步骤：必须整份拒绝，不能发布部分计划。
        r=await self.model_reply(text,{'kind':'plan','summary':'移动P3','current_request':text,'unhandled':[],
                                     'plan':[{'op':'movej','point':'P3'}]})
        self.assertFalse(r['ok']);self.assertEqual(r['plan'],[]);self.assertEqual(r['code'],'')
        self.assertEqual(r['model_request']['status'],'invalid_response');self.assertTrue(r['model_request']['attempted'])
        # 模型只做解释、不给动作：可以接受为对话，但同样不发布任何计划。
        r=await self.model_reply(text,{'kind':'chat','summary':'已理解先到P3再绘制爱心。','questions':[]})
        self.assertEqual(r['source_kind'],'model');self.assertEqual(r['plan'],[]);self.assertEqual(r['code'],'')
        self.assertIn('绘制',r['summary'])

    async def test_model_plan_rejects_incomplete_coverage_unknown_points_extra_fields_and_added_io(self):
        text='让机械臂走向P3'
        base={'kind':'plan','summary':'前往P3','current_request':text,'unhandled':[], 'plan':[{'op':'movej','point':'P3'}]}
        for patch in [{'current_request':'P3'},{'unhandled':['其余要求']},{'plan':[{'op':'movej','point':'P9'}]},
                      {'plan':[{'op':'movej','point':'P3','joints':[0]*6}]},
                      {'plan':[{'op':'movej','point':'P3','extra':'ignored'}]},
                      {'plan':[{'op':'dout','bit':0,'val':1}]},{'plan':[{'op':'dout','bit':'0','val':1}]},
                      {'plan':[{'op':'speed','pct':3}]},{'plan':[{'op':'wait','sec':3}]},
                      {'plan':[{'op':'wait','sec':2}]},{'extra':'unconsumed'}]:
            r=await self.model_reply(text,{**base,**patch})
            self.assertFalse(r['ok'],patch);self.assertEqual(r['plan'],[]);self.assertNotIn('plan_id',r)

    async def test_missing_unknown_empty_unsupported_and_history_not_authority(self):
        for text in ('去P9', '等待1秒，然后循环三遍', '执行上一条', '', 'DO4打开', '速度999%'):
            r = await self.ask(text)
            self.assertEqual(r['kind'], 'clarify', (text, r)); self.assertEqual(r['plan'], [])

    async def test_explicit_plan_code_resolves_code_not_display_name(self):
        r = await self.ask('速度8%，去P3，然后等待0.2秒，把DO1断掉')
        self.assertEqual(r['kind'], 'plan', r)
        self.assertEqual(r['plan'][1]['point_id'], 47)
        self.assertEqual(r['plan'][1]['point'], 'P3')
        steps = program.parse(r['code'], lambda _: None)
        self.assertEqual([s['kind'] for s in steps], ['speed', 'movej', 'wait', 'dout'])
        self.assertEqual(steps[1]['joints'], self.points[0]['joints'])
        self.assertEqual(steps[-1]['bit'], 0); self.assertEqual(steps[-1]['val'], 0)
        self.assertIn('不代表', r['warnings'][0])
        self.assertNotIn('放置台', r['code'])

    async def test_question_negation_does_not_make_plan(self):
        for text in ('解释去P3是什么意思', '不要去P3', '可以去P3吗？'):
            r = await self.ask(text)
            self.assertNotEqual(r.get('kind'), 'plan'); self.assertEqual(r['plan'], [])

    async def test_speed_order_only_explicit_trailing_modifier_can_move(self):
        self.points += [{'id': 48, 'code': 'P1', 'name': '取物台', 'joints': [0]*6}]
        for text, expected in [('去P1，速度20%，再去P3', ['movej', 'speed', 'movej']),
                               ('去P1再去P3，以20%速度', ['speed', 'movej', 'movej'])]:
            r = await self.ask(text)
            self.assertEqual(r['kind'], 'plan', r)
            self.assertEqual([op['op'] for op in r['plan']], expected)

    def resources(self, mode='sim'):
        return {'mode': mode, 'tool': {'id': 'vacuum-reference', 'type': 'suction',
                                      'actions': {'vacuum_on': 'unmapped', 'vacuum_off': 'unmapped'}},
                'mounted': {'tool_id': 'vacuum-reference', 'state': 'simulated' if mode == 'sim' else 'manual_confirmed'},
                'mapped_actions': []}

    async def test_complete_known_tool_process_is_semantic_no_dout_and_parser_equivalent(self):
        self.points += [{'id': 50+i, 'code': f'P{i}', 'name': f'位置{i}', 'joints': [i]*6} for i in (1, 2, 4)]
        r = await self.ask('经接近点P2到P1吸取物体，经撤离点P2和接近点P4到P3释放物体，经撤离点P4返回P1', resources=self.resources())
        self.assertEqual(r['kind'], 'plan', r)
        self.assertEqual([op.get('action') for op in r['plan'] if op['op'] == 'tool'], ['vacuum_on', 'vacuum_off'])
        self.assertFalse(any(op['op'] == 'dout' for op in r['plan']))
        self.assertEqual(r['plan'][0], {'op': 'require_tool', 'tool_id': 'vacuum-reference'})
        self.assertIn('不表示物体已附着', r['warnings'][0])
        parsed = program.parse(r['code'], lambda _: None)
        self.assertEqual(parsed[0]['kind'], 'require_tool')
        self.assertEqual([o['action'] for o in parsed if o['kind'] == 'tool_action'], ['vacuum_on', 'vacuum_off'])
        self.assertEqual([o['joints'] for o in parsed if o['kind'] == 'movej'],
                         [o['joints'] for o in r['plan'] if o['op'] == 'movej'])

    async def test_missing_tool_mapping_approach_contact_or_withdraw_no_partial_plan(self):
        self.points += [{'id': 50+i, 'code': f'P{i}', 'name': f'位置{i}', 'joints': [i]*6} for i in (1, 2, 4)]
        for text, resource in [
            ('工具吸附', None), ('工具吸附', self.resources('real')),
            ('在P1吸附到P3释放返回P1', self.resources()),
            ('经接近点P2到P1吸附', self.resources()),
            ('吸取物体', self.resources()),
        ]:
            r = await self.ask(text, resources=resource)
            self.assertEqual(r['kind'], 'clarify', (text, r)); self.assertEqual(r['plan'], [])
        real = self.resources('real'); real['mapped_actions'] = ['vacuum_on']
        r = await self.ask('工具吸附', resources=real)
        self.assertEqual(r['kind'], 'plan', r)
        self.assertEqual(r['plan'][1]['op'], 'tool')

    async def test_model_finish_and_schema_no_actions_and_no_resources(self):
        fake = AsyncMock()
        response = fake.post.return_value
        response.raise_for_status = lambda: None
        for choice in [
            {'finish_reason': 'length', 'message': {'content': '{"kind":"chat","summary":"x"}'}},
            {'finish_reason': 'stop', 'message': {'content': '{"kind":"plan","plan":[{"op":"wait","sec":1}]}'}},
            {'finish_reason': 'stop', 'message': {'content': '{"kind":"chat","summary":"关节运动按关节角插补。"}'}},
        ]:
            response.json = lambda: {'choices': [choice]}
            with patch.object(ai_plan.httpx, 'AsyncClient') as client:
                client.return_value.__aenter__.return_value = fake
                r = await ai_plan.make_plan('解释关节运动', [], self.points, self.model,
                                            dict(config.DEFAULTS), 'fake-key', allow_model=True)
            self.assertEqual(r.get('plan'), [])
            self.assertNotIn('plan_id', r)
            self.assertEqual(r['ok'], choice['message']['content'].endswith('插补。"}'))
            sent = fake.post.call_args.kwargs['json']
            self.assertEqual(sent['model'], config.DEFAULTS['ai_model'])
            self.assertNotIn('放置台', str(sent)); self.assertNotIn('fake-key', str(sent))


class WorldTests(unittest.IsolatedAsyncioTestCase):
    def make_world(self):
        w = World()
        w.update_settings({'mode': 'sim', 'ground_enabled': False, 'self_collision': False})
        return w

    async def proposal(self, w, plan=None):
        fake = {'ok': True, 'kind': 'plan', 'plan': plan or [{'op': 'wait', 'sec': 0}], 'summary': 'test'}
        with patch.object(ai_plan, 'make_plan', return_value=copy.deepcopy(fake)):
            return await w.ai('test', [])

    async def test_new_greeting_failure_and_empty_revoke_previous(self):
        w = self.make_world(); first = await self.proposal(w)
        await w.ai('你好', [])
        self.assertFalse((await w.ai_run([{'op': 'wait', 'sec': 0}], first['plan_id']))['ok'])
        for fake in ({'ok': False, 'error': 'test'}, {'ok': True, 'kind': 'plan', 'plan': []}):
            first = await self.proposal(w)
            with patch.object(ai_plan, 'make_plan', return_value=fake):
                r = await w.ai('test', [])
            self.assertNotIn('plan_id', r); self.assertNotIn(first['plan_id'], w.plans)

    async def test_context_session_stop_tamper_and_one_shot(self):
        for change in ('mode', 'session', 'stop', 'tamper', 'success'):
            w = self.make_world(); p = await self.proposal(w)
            plan = [{'op': 'wait', 'sec': 0}]
            if change == 'mode': w.settings['mode'] = 'real'
            if change == 'session': w.session_id = 'changed'
            if change == 'stop': w.executor.epoch += 1
            if change == 'tamper': plan[0]['sec'] = 1
            with patch.object(w.runner, 'start') as start:
                r = await w.ai_run(plan, p['plan_id'])
            self.assertEqual(r['ok'], change == 'success', (change, r))
            self.assertEqual(start.call_count, int(change == 'success'))
            self.assertFalse((await w.ai_run(plan, p['plan_id']))['ok'])

    async def test_midpath_risk_blocks_whole_plan_not_just_endpoint(self):
        w = self.make_world()
        plan = [{'op': 'dout', 'bit': 0, 'val': 1}, {'op': 'movej', 'joints': [0, 0, 0, 0, 0, 1]}]
        p = await self.proposal(w, plan)
        with patch('backend.core.world.inspect_path', return_value={'risk_detected': True, 'issues': [{'sample': 1}]}) as inspect, patch.object(w.runner, 'start') as start:
            r = await w.ai_run(plan, p['plan_id'])
        self.assertFalse(r['ok']); self.assertEqual(inspect.call_count, 1); start.assert_not_called()

    async def test_preflight_has_private_fk_cache_not_live_status_cache(self):
        w = self.make_world()
        live = w.motion_model()
        live.fk(w.current_joints())
        original_key, original_value = live._fk_key, live._fk_value
        plan = [{'op': 'movej', 'joints': list(w.current_joints())}]
        proposal = await self.proposal(w, plan)
        def inspect(model, *args, **kwargs):
            self.assertIsNot(model, live)
            self.assertIsNone(model._fk_key)
            self.assertIsNone(model._fk_value)
            model.fk([5] * 6)
            return {'risk_detected': False}
        with patch('backend.core.world.inspect_path', side_effect=inspect), patch.object(w.runner, 'start'):
            result = await w.ai_run(plan, proposal['plan_id'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(live._fk_key, original_key)
        self.assertIs(live._fk_value, original_value)

    async def test_discard_revokes_exact_request_without_killing_new_turn(self):
        w = self.make_world(); old = await self.proposal(w)
        old_request = old['request_id']
        w.ai_revoke(old_request, old['plan_id'])
        self.assertNotIn(old['plan_id'], w.plans)
        new = await self.proposal(w)
        w.ai_revoke(old_request, old['plan_id'])
        self.assertIn(new['plan_id'], w.plans)
        with patch.object(w.runner, 'start'):
            self.assertTrue((await w.ai_run(new['plan'], new['plan_id']))['ok'])

    async def test_cancel_inflight_model_never_publishes_token(self):
        w = self.make_world(); gate, entered = asyncio.Event(), asyncio.Event()
        async def model(*a, **k):
            entered.set(); await gate.wait()
            return {'ok': True, 'kind': 'plan', 'plan': [{'op': 'wait', 'sec': 0}]}
        with patch.object(ai_plan, 'make_plan', side_effect=model):
            task = asyncio.create_task(w.ai('test', [], 'cancel-me'))
            await entered.wait(); w.ai_revoke('cancel-me'); gate.set()
            r = await task
        self.assertFalse(r['ok']); self.assertNotIn('plan_id', r)

    async def test_mode_roundtrip_and_safety_unknown_do_not_reactivate(self):
        for change in ('roundtrip', 'unknown', 'red'):
            w = self.make_world(); p = await self.proposal(w)
            if change == 'roundtrip':
                w.update_settings({'mode': 'real'}); w.update_settings({'mode': 'sim'})
            else:
                w.safety['level'] = change
            with patch.object(w.runner, 'start') as start:
                self.assertFalse((await w.ai_run(p['plan'], p['plan_id']))['ok'])
                start.assert_not_called()

    async def test_owners_reject_without_stopping_follow_or_replacing_debug_program(self):
        for owner in ('palm', 'mouse', 'real_follow', 'debug', 'waiting', 'queue', 'free', 'stop_pending', 'tool_pending'):
            w = self.make_world(); p = await self.proposal(w)
            pending = None
            if owner in ('palm', 'mouse'): w.hand_follow.active = True
            if owner == 'real_follow': w.real_jog_follow.phase = 'following'
            if owner == 'debug': w.runner.state = 'paused'; w.runner.debug_enabled = True
            if owner == 'waiting': w.runner.state = 'waiting'
            if owner == 'queue': w.queue_state = 'paused'
            if owner == 'free': w.free['on'] = True
            if owner in ('stop_pending', 'tool_pending'):
                pending = asyncio.get_running_loop().create_future()
                if owner == 'stop_pending': w._stop_task = pending
                else: w.executor.task = pending
            original = (w.hand_follow.active, w.real_jog_follow.active, w.runner.state, w.queue_state)
            try:
                with patch.object(w, '_request_real_jog_stop') as stop, patch.object(w.runner, 'load') as load, patch.object(w.runner, 'start') as start:
                    r = await w.ai_run(p['plan'], p['plan_id'])
                    self.assertFalse(r['ok'], (owner, r)); self.assertTrue(r['why'])
                    stop.assert_not_called(); load.assert_not_called(); start.assert_not_called()
                self.assertEqual(original, (w.hand_follow.active, w.real_jog_follow.active, w.runner.state, w.queue_state))
            finally:
                if pending: pending.cancel()

    async def test_owner_starts_during_preflight_is_not_preempted(self):
        w = self.make_world(); p = await self.proposal(w)
        async def thread(fn):
            w.hand_follow.active = True
            return []
        with patch('backend.core.world.asyncio.to_thread', side_effect=thread), patch.object(w.runner, 'start') as start:
            r = await w.ai_run(p['plan'], p['plan_id'])
        self.assertFalse(r['ok']); self.assertTrue(w.hand_follow.active); start.assert_not_called()

    async def test_semantic_tool_uses_existing_runner_and_sim_never_attaches_material(self):
        w = self.make_world()
        before = copy.deepcopy(w.objects.list())
        p = await w.ai('工具吸附', [])
        self.assertEqual(p['kind'], 'plan', p)
        self.assertEqual([o['op'] for o in p['plan']], ['require_tool', 'tool'])
        with patch.object(w.runner, 'start'):
            r = await w.ai_run(p['plan'], p['plan_id'])
        self.assertTrue(r['ok'], r)
        self.assertEqual([s['kind'] for s in w.runner.steps], ['require_tool', 'tool_action'])
        self.assertEqual(w.runner.steps[-1]['action'], 'vacuum_on')
        response = await w.tool_action('vacuum_on', internal=True)
        self.assertTrue(response['sim']); self.assertEqual(w._sim_tool_action['action_state'], 'active')
        self.assertEqual(w.objects.list(), before)

    async def test_context_during_preflight_and_duplicate_never_start(self):
        w = self.make_world(); plan = [{'op': 'movej', 'joints': [0, 0, 0, 0, 0, 1]}]
        p = await self.proposal(w, plan)
        gate, entered = asyncio.Event(), asyncio.Event()
        async def thread(fn):
            entered.set(); await gate.wait(); return []
        with patch('backend.core.world.asyncio.to_thread', side_effect=thread), patch.object(w.runner, 'start') as start:
            task = asyncio.create_task(w.ai_run(plan, p['plan_id']))
            await entered.wait()
            self.assertFalse((await w.ai_run(plan, p['plan_id']))['ok'])
            w.session_id = 'new'; gate.set()
            self.assertFalse((await task)['ok']); start.assert_not_called()


if __name__ == '__main__': unittest.main(verbosity=2)
