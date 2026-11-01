"""Import purity and startup failure cleanup, without devices or servers."""
from _offline_guard import require_offline_copy
require_offline_copy()

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


class Bootstrap(unittest.IsolatedAsyncioTestCase):
    def test_import_does_not_modify_existing_real_mode_or_event_history(self):
        import json
        with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
            data=Path(directory)
            (data/'settings.json').write_text(json.dumps({'mode':'real','auto_connect':False}),encoding='utf-8')
            (data/'events.jsonl').write_text('existing history\n',encoding='utf-8')
            before={p.name:p.read_bytes() for p in data.iterdir()}
            process=subprocess.run([sys.executable,'-B','-c','import backend.main'],
                env=dict(os.environ,ELFIN_DATA_DIR=str(data)),capture_output=True,text=True,timeout=30)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual({p.name:p.read_bytes() for p in data.iterdir()},before)
    def test_import_main_creates_no_workstation_files(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HUYAN_OFFLINE_ROOT']) as directory:
            data = Path(directory) / 'not-created'
            env = dict(os.environ, ELFIN_DATA_DIR=str(data))
            process = subprocess.run([sys.executable, '-B', '-c', 'import backend.main'],
                                     env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertFalse(data.exists(), list(data.rglob('*')) if data.exists() else [])

    async def test_partial_vision_start_failure_releases_every_resource(self):
        import backend.main as main
        events = []
        async def stop_world(): events.append('world')
        async def close_vision(): events.append('vision')
        world = SimpleNamespace(start=AsyncMock(), stop=stop_world)
        vision = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError('vision failed')), close=close_vision)
        camera = SimpleNamespace(close=lambda: events.append('camera'))
        lease = SimpleNamespace(acquire=Mock(), release=lambda: events.append('lease'))
        from backend.bootstrap import ApplicationResources
        resources=ApplicationResources(world=world,vision=vision,cameras=camera,lease=lease)
        with self.assertRaisesRegex(RuntimeError, 'vision failed'):
            await resources.start()
        self.assertEqual(events, ['world', 'vision', 'camera', 'lease'])

    async def test_partial_journal_start_failure_still_releases_owned_resources(self):
        from backend.bootstrap import ApplicationResources
        events=[]
        async def world_stop():events.append('world')
        async def journal_close():events.append('journal')
        async def vision_close():events.append('vision')
        journal=SimpleNamespace(start=AsyncMock(side_effect=RuntimeError('journal failed')),close=journal_close)
        world=SimpleNamespace(start=AsyncMock(),stop=world_stop,run_journal=journal)
        vision=SimpleNamespace(start=AsyncMock(),close=vision_close)
        camera=SimpleNamespace(close=lambda:events.append('camera'))
        lease=SimpleNamespace(acquire=lambda:None,release=lambda:events.append('lease'))
        resources=ApplicationResources(world=world,vision=vision,cameras=camera,lease=lease)
        with self.assertRaisesRegex(RuntimeError,'journal failed'):await resources.start()
        world.start.assert_not_awaited()
        self.assertEqual(events,['world','journal','vision','camera','lease'])


if __name__ == '__main__': unittest.main()
