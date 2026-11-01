"""API truthfulness and explicit IO addressing against production routes."""
from _offline_guard import require_offline_copy
require_offline_copy()

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from backend.main import app, world
from backend.core import program
from backend.core.ai_intent import intent
from backend.domain.tool_actions import TOOL_ACTION_DEFINITIONS, TOOL_ACTIONS
from backend.schemas import ToolAction


class APITruthTests(unittest.IsolatedAsyncioTestCase):
    def test_tool_registry_is_shared_by_schema_parser_and_local_intent(self):
        resources = {'mode':'sim','tool':{'id':'test','type':'suction',
                     'actions':{name:'unmapped' for name in TOOL_ACTIONS}},
                     'mounted':{'tool_id':'test','state':'manual_confirmed'}}
        for definition in TOOL_ACTION_DEFINITIONS:
            name = definition['id']
            self.assertEqual(ToolAction(action=name).action, name)
            self.assertIsNotNone(program.TOOL_RE.fullmatch('TOOL '+name.upper()))
            self.assertEqual(program.normalize_command('工具 '+definition['zh']), 'TOOL '+name.upper())
            response = intent('TOOL '+name.upper(), [], resources)
            self.assertEqual(response['kind'], 'plan', (name, response))
            self.assertTrue(any(op.get('action') == name for op in response['plan']))

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_unbound_history_never_qualifies_current_device(self):
        response = await self.client.get('/api/capabilities')
        self.assertEqual(response.status_code, 200)
        commands = response.json()['commands']
        self.assertTrue(any(c['historical_hardware_evidence'] for c in commands))
        self.assertTrue(all(c['level'] == 'documented' and c['current_device_qualified'] is False for c in commands))

    async def test_old_full_query_cannot_claim_unredacted_export(self):
        baseline = await self.client.get('/api/diag')
        full = await self.client.get('/api/diag?full=1')
        self.assertEqual(full.status_code, 200)
        self.assertNotIn('含设备标识完整值', full.text)
        self.assertEqual(set(baseline.json()), set(full.json()))

    async def test_missing_or_coerced_io_address_never_reaches_output(self):
        with patch.object(world, 'io_probe', new_callable=AsyncMock) as probe:
            for body in ({}, {'group':'end','val':1}, {'group':'end','bit':True,'val':1},
                         {'group':'end','bit':0,'val':'1'}, {'group':'all','bit':0,'val':1}):
                response = await self.client.post('/api/io/probe', json=body)
                self.assertEqual(response.status_code, 422, body)
            probe.assert_not_awaited()

    async def test_selected_io_address_is_forwarded_exactly_once(self):
        with patch.object(world, 'io_probe', new_callable=AsyncMock, return_value={'ok':True}) as probe:
            response = await self.client.post('/api/io/probe', json={'group':'box','bit':7,'val':0})
            self.assertEqual(response.status_code, 200)
            probe.assert_awaited_once_with('box', 7, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
