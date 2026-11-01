"""Controlled clarification is context-bound and never an execution permit."""
from _offline_guard import require_offline_copy
require_offline_copy()
import asyncio,copy,time,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from backend.core.world import World
from backend.core import ai_plan

class Clarification(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w=World();self.w._save_preferences({'mode':'sim'})
    async def ask(self,text,continuation=None,request='turn'):
        with patch.object(ai_plan.httpx,'AsyncClient',side_effect=AssertionError('external AI forbidden')):
            return await self.w.ai(text,[],request,clarification=continuation)
    async def pending(self):
        r=await self.ask('等待多久')
        self.assertEqual(r['kind'],'clarify');self.assertFalse(r['plan']);self.assertNotIn('plan_id',r)
        self.assertTrue(r['clarification']['id']);return r['clarification']
    async def test_sources_do_not_equate_configuration_or_rules_with_model_health(self):
        local=await self.ask('等待0.2秒')
        self.assertEqual(local['source_kind'],'local_rules');self.assertNotIn('model_request',local)
        with patch('backend.core.config.zhipu_key',return_value=''):
            absent=await self.w.ai('解释变量作用域',[],'no-model')
        self.assertEqual(absent['source_kind'],'not_generated');self.assertEqual(absent['model_request']['status'],'not_configured')
        self.assertFalse(absent['model_request']['attempted'])

    async def test_actual_request_outcomes_attest_health_without_external_calls(self):
        import httpx,json
        client=httpx.AsyncClient
        settings={**self.w.settings,'ai_model':'offline-fake','ai_base_url':'https://offline.invalid'}
        async def call(handler):
            with patch.object(ai_plan.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(handler),**kw)):
                return await ai_plan.make_plan('解释变量作用域',[],[],self.w.model,settings,'fake',allow_model=True)
        for code,status in ((429,'rate_limited'),(503,'connection_failed')):
            r=await call(lambda req:httpx.Response(code,request=req))
            self.assertFalse(r['ok']);self.assertEqual(r['source_kind'],'not_generated');self.assertEqual(r['model_request']['status'],status);self.assertTrue(r['model_request']['attempted'])
        for content in ('not json',{'unexpected':'not text'},json.dumps({'kind':'plan','plan':[{'op':'wait','sec':1}]})):
            r=await call(lambda req:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':content}}]},request=req))
            self.assertEqual(r['model_request']['status'],'invalid_response');self.assertNotIn('plan_id',r)
        r=await call(lambda req:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'kind':'chat','summary':'仅离线回答','questions':[],'source_kind':'local_rules'})}}]},request=req))
        self.assertTrue(r['ok']);self.assertEqual(r['source_kind'],'model');self.assertEqual(r['model_request']['status'],'success');self.assertFalse(r['plan'])
        response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'kind':'chat','summary':'仅离线回答','questions':[]})}}]}
        with patch('backend.core.config.zhipu_key',return_value='fake'),patch.object(ai_plan.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=response,request=req)),**kw)):
            actual=await self.w.ai('解释变量作用域',[],'authorized-offline',allow_model=True)
        self.assertEqual(actual['source_kind'],'model');self.assertEqual(actual['model_config_rev'],self.w._cfg_rev);self.assertNotIn('plan_id',actual)


    async def test_world_requires_per_turn_external_context_authorization(self):
        with patch('backend.core.config.zhipu_key',return_value='fake'),patch.object(ai_plan.httpx,'AsyncClient',side_effect=AssertionError('external AI forbidden')):
            for flag in (False,None,'true',1):
                r=await self.w.ai('解释变量作用域',[],'permission-'+str(flag),allow_model=flag)
                self.assertFalse(r['ok']);self.assertEqual(r['source_kind'],'not_generated');self.assertNotIn('model_request',r)
                self.assertIn('未授权',r['error'])

    async def test_explicit_supplement_creates_new_complete_plan_without_execution(self):
        token=await self.pending();r=await self.ask('等待0.2秒',token,'supplement')
        self.assertTrue(r['ok'],r);self.assertEqual(r['kind'],'plan');self.assertTrue(r['plan_id']);self.assertEqual(r['plan'],[{'op':'wait','sec':.2}]);self.assertNotEqual(self.w.runner.state,'running')
        with self.assertRaises(KeyError):_ = r['clarification']
    async def test_confirm_history_or_wrong_revision_cannot_authorize(self):
        token=await self.pending()
        for body in [{**token,'revision':token['revision']+1},{**token,'id':'forged'}]:
            r=await self.ask('等待0.2秒',body);self.assertFalse(r['ok']);self.assertNotIn('plan_id',r)
        r=await self.ask('确认',token);self.assertNotEqual(r.get('kind'),'plan');self.assertNotIn('plan_id',r)
    async def test_cancel_expire_session_and_scene_tool_changes_refuse_old_pending(self):
        for change in ('cancel','expire','session','scene','scene_restore','tool'):
            self.w=World();token=await self.pending()
            if change=='cancel':self.w.ai_revoke('turn')
            if change=='expire':self.w._ai_clarifications.pending['expires']=time.monotonic()-1
            if change=='session':self.w.session_id='new-session'
            # 摆放物必须落在机器人连杆之外，否则"落位即拦截"会先拒绝；
            # 两个用例的物体还必须彼此分离，否则"物体相互重叠"会先拒绝。
            if change=='scene':self.w.objects.add({'name':'变化','kind':'box','xyz':[400,0,100]})
            if change=='scene_restore':
                row=self.w.objects.add({'name':'撤销变化','kind':'box','xyz':[600,0,100]});self.w.objects.remove(row['id'])
            if change=='tool':self.w._cfg_rev+=1
            r=await self.ask('等待0.2秒',token);self.assertFalse(r['ok'],(change,r));self.assertNotIn('plan_id',r)
    async def test_revision_consumed_once_and_context_only_history_never_reuses_plan(self):
        token=await self.pending();first=await self.ask('等待0.2秒',token,'complete')
        self.assertEqual(first['kind'],'plan');second=await self.ask('等待0.2秒',token,'replay')
        self.assertFalse(second['ok']);self.assertNotIn(first['plan_id'],self.w.plans)
    async def test_generic_ambiguous_request_needs_explicit_full_restatement(self):
        r=await self.ask('吸取物体放P9回起点');token=r['clarification']
        no=await self.ask('等待0.2秒',token,'partial');self.assertEqual(no['kind'],'clarify');self.assertFalse(no['plan'])
        yes=await self.ask('完整要求：等待0.2秒',no['clarification'],'complete');self.assertEqual(yes['kind'],'plan')
    async def test_concurrent_new_turn_invalidates_delayed_old_result(self):
        token=await self.pending();entered=asyncio.Event();release=asyncio.Event();real=ai_plan.make_plan
        async def held(*a,**kw):entered.set();await release.wait();return await real(*a,**kw)
        with patch.object(ai_plan,'make_plan',held):
            task=asyncio.create_task(self.ask('等待0.2秒',token,'held'));await entered.wait()
            r=await self.w.ai('你好',[],'new');self.assertFalse(r['ok']);release.set();result=await task
        self.assertFalse(result['ok']);self.assertFalse(self.w.plans)
    async def test_known_actions_are_preserved_and_turn_count_and_ttl_remain_bounded(self):
        r=await self.ask('速度10%，等待多久');token=r['clarification']
        result=await self.ask('等待0.2秒',token,'complete')
        self.assertEqual(result['plan'],[{'op':'speed','pct':10.},{'op':'wait','sec':.2}])
        token=await self.pending();expires=self.w._ai_clarifications.pending['expires']
        for i in range(4):
            answer=await self.ask('确认',token,'bounded'+str(i));self.assertEqual(answer['kind'],'clarify');token=answer['clarification']
            self.assertEqual(self.w._ai_clarifications.pending['expires'],expires)
        no=await self.ask('等待0.2秒',token,'too-many');self.assertFalse(no['ok']);self.assertNotIn('plan_id',no)
    async def test_cancel_during_generation_cannot_publish_a_pending_or_plan(self):
        entered=asyncio.Event();release=asyncio.Event();real=ai_plan.make_plan
        async def held(*a,**kw):entered.set();await release.wait();return await real(*a,**kw)
        with patch.object(ai_plan,'make_plan',held):
            task=asyncio.create_task(self.ask('等待多久',request='cancel-during'));await entered.wait()
            self.w.ai_revoke('cancel-during');release.set();result=await task
        self.assertFalse(result['ok']);self.assertIsNone(self.w._ai_clarifications.pending);self.assertFalse(self.w.plans)
    async def test_actual_api_transports_pending_revision_and_creates_only_a_new_proposal(self):
        import httpx
        from backend import main
        with tempfile.TemporaryDirectory() as temp:
            app=main.create_app(main.build_dependencies(data_dir=Path(temp)))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                with patch.object(ai_plan.httpx,'AsyncClient',side_effect=AssertionError('no external model')):
                    first=(await client.post('/api/ai/chat',json={'text':'等待多久','request_id':'http-first'})).json()
                    self.assertEqual(first['kind'],'clarify');self.assertNotIn('plan_id',first)
                    second=(await client.post('/api/ai/chat',json={'text':'等待0.2秒','request_id':'http-second','clarification':first['clarification']})).json()
                self.assertEqual(second['kind'],'plan');self.assertTrue(second['plan_id']);self.assertEqual(app.state.resources.world.runner.state,'idle')
                stale=(await client.post('/api/ai/chat',json={'text':'确认','request_id':'http-old','clarification':first['clarification']})).json()
                self.assertFalse(stale['ok']);self.assertNotIn('plan_id',stale)

if __name__=='__main__':unittest.main()
