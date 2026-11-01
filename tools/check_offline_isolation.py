"""Exercise the runner's data and physical I/O boundaries before backend checks."""
from _offline_guard import require_offline_copy
require_offline_copy()

import os
import asyncio
from pathlib import Path
import socket
import unittest
from unittest.mock import patch


class Isolation(unittest.TestCase):
    def test_asyncio_internal_wakeup_pipe_remains_usable(self):
        async def probe():
            await asyncio.sleep(0)
            return 'ready'
        self.assertEqual(asyncio.run(probe()), 'ready')

    def test_data_is_disposable_and_rejects_inherited_workstation_path(self):
        root = Path(os.environ['HUYAN_OFFLINE_ROOT']).resolve()
        data = Path(os.environ['ELFIN_DATA_DIR']).resolve()
        self.assertTrue(data.is_relative_to(root))
        data.mkdir(parents=True, exist_ok=True)
        (data / 'runner-probe.txt').write_text('isolated', encoding='utf-8')
        with patch.dict(os.environ, ELFIN_DATA_DIR=str(root.parent / 'workstation-data')):
            with self.assertRaises(SystemExit):
                require_offline_copy()

    def test_all_network_paths_reject_even_loopback(self):
        with socket.socket() as wire:
            for address in [('127.0.0.1', 9), ('192.0.2.1', 9)]:
                with self.assertRaisesRegex(RuntimeError, 'Offline checks'):
                    wire.connect(address)
                with self.assertRaisesRegex(RuntimeError, 'Offline checks'):
                    wire.connect_ex(address)
        with socket.socket(type=socket.SOCK_DGRAM) as wire:
            with self.assertRaisesRegex(RuntimeError, 'Offline checks'):
                wire.sendto(b'blocked', ('127.0.0.1', 9))

    def test_camera_capture_rejected(self):
        import cv2
        with self.assertRaisesRegex(RuntimeError, 'Offline checks'):
            cv2.VideoCapture(0)


if __name__ == '__main__':
    unittest.main()
