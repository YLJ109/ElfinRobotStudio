"""日志清空和历史配置回归：仅运行于禁用硬件的临时副本。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import config
from backend.core.eventlog import EventLog


class EventLogSettingsTests(unittest.TestCase):
    def test_unchanged_log_sequence_never_scans_or_copies_history(self):
        class NoScan(deque):
            def __iter__(self):
                raise AssertionError('无增量不能遍历历史日志')
        log = EventLog(100000)
        log.seq = 100000
        log.buf = NoScan({'seq': i, 'kind': 'event'} for i in range(1, 100001))
        self.assertEqual(log.since(100000), {'rows': [], 'gap': False, 'seq': 100000})

    def test_delta_gap_and_new_row_order_remain_exact(self):
        log = EventLog(3)
        log.seq = 5
        log.buf = deque([{'seq': i} for i in (3, 4, 5)], maxlen=3)
        self.assertEqual(log.since(4), {'rows': [{'seq': 5}], 'gap': False, 'seq': 5})
        self.assertEqual(log.since(0), {'rows': [{'seq': i} for i in (3, 4, 5)], 'gap': True, 'seq': 5})

    def test_snapshot_tail_reads_only_requested_suffix_and_preserves_order_and_filter(self):
        class TailOnly(deque):
            def __iter__(self):
                raise AssertionError('快照不得遍历整个日志缓冲')
        log=EventLog(100000)
        log.buf=TailOnly({'seq':i,'kind':'alarm' if i%2 else 'event'} for i in range(100000))
        self.assertEqual([r['seq'] for r in log.tail(3)],[99997,99998,99999])
        self.assertEqual([r['seq'] for r in log.tail(2,'alarm')],[99997,99999])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = patch.object(config, 'DATA_DIR', self.root)
        self.cache = patch.object(config, '_settings', None)
        self.data.start()
        self.cache.start()

    def tearDown(self):
        self.cache.stop()
        self.data.stop()
        self.temp.cleanup()

    def load_history(self, value):
        path = self.root / 'settings.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        original = path.read_bytes()
        config._settings = None
        result = copy.deepcopy(config.get_settings())
        self.assertEqual(path.read_bytes(), original, '读取不能改写历史文件')
        return result

    def test_invalid_root_types_fall_back_without_writes(self):
        for value in (None, [], [1], True, 5, 'settings'):
            with self.subTest(value=value):
                loaded = self.load_history(value)
                self.assertEqual(loaded['fence'], config.DEFAULTS['fence'])
                self.assertEqual(EventLog().keep, config.DEFAULTS['log_keep'])

    def test_invalid_historical_keep_falls_back(self):
        for value in (2.5, 2000.0, True, False, None, '2000', 0, -1, 100001, float('nan'), float('inf')):
            with self.subTest(value=value):
                loaded = self.load_history({'log_keep': value, 'trail_len': 42})
                self.assertEqual(type(loaded['log_keep']), int)
                self.assertEqual(loaded['log_keep'], config.DEFAULTS['log_keep'])
                self.assertEqual(EventLog().keep, config.DEFAULTS['log_keep'])
                self.assertEqual(loaded['trail_len'], 42)

    def test_valid_keep_including_limits_is_preserved(self):
        for value in (1, 2000, 100000):
            self.assertEqual(self.load_history({'log_keep': value})['log_keep'], value)
            self.assertEqual(EventLog().keep, value)

    def test_explicit_invalid_keep_is_rejected_not_coerced(self):
        for value in (0, -1, True, False, 2.5, '5', 100001, float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): EventLog(keep=value)

    def test_bad_fence_z_falls_back_preserving_other_fields(self):
        for value in ([], [0], [0, 1, 2], None, 'bad', [0, None], [0, float('nan')],
                      [0, float('inf')], [True, 3], [5, 2], [0, -1], [0, 10**500]):
            with self.subTest(value=value):
                loaded = self.load_history({'fence': {'x': [-300, 300], 'y': [-400, 400], 'z': value, 'enabled': False}})
                self.assertEqual(loaded['fence']['z'], config.DEFAULTS['fence']['z'])
                self.assertEqual(loaded['fence']['x'], [-300, 300])
                self.assertFalse(loaded['fence']['enabled'])

    def test_legacy_fence_migration_and_valid_z_are_preserved(self):
        result = self.load_history({'fence': {'x': [-1200, 1200], 'y': [-1200, 1200], 'z': []}, 'speed_pct': 33})
        self.assertEqual(result['fence']['x'], [-900, 900])
        self.assertEqual(result['fence']['z'], [0, 1400])
        self.assertEqual(result['sim_speed_pct'], 33)
        result = self.load_history({'fence': {'z': [-100, 1700]}})
        self.assertEqual(result['fence']['z'], [0, 1700])

    def test_invalid_fence_container_falls_back(self):
        for value in (None, [], 'bad', 4):
            with self.subTest(value=value):
                self.assertEqual(self.load_history({'fence': value})['fence'], config.DEFAULTS['fence'])

    def test_clear_write_failure_propagates_and_preserves_memory(self):
        log = EventLog()
        log.add('event', '旧日志')
        original = log.path.read_bytes()
        with patch.object(Path, 'write_text', side_effect=OSError('磁盘写失败')):
            with self.assertRaises(OSError): log.clear_file()
        self.assertEqual(log.tail()[0]['text'], '旧日志')
        self.assertEqual(log.path.read_bytes(), original)
        self.assertEqual(log.seq, 1)

    def test_successful_clear_keeps_sequence_monotonic(self):
        log = EventLog()
        log.add('event', '旧日志')
        log.clear_file()
        self.assertEqual(log.tail(), [])
        self.assertEqual(log.path.read_text(encoding='utf-8'), '')
        self.assertEqual(log.add('event', '新日志')['seq'], 2)

    def test_clear_holds_lock_through_disk_write_and_preserves_later_append(self):
        log = EventLog()
        log.add('event', '旧日志')
        entered, release, attempted, done = (threading.Event() for _ in range(4))
        errors, lock_states = [], []
        original_write = Path.write_text

        def blocked_write(path, *args, **kwargs):
            entered.set()
            if not release.wait(3): raise RuntimeError('清空测试等待超时')
            return original_write(path, *args, **kwargs)

        def clear():
            try: log.clear_file()
            except BaseException as exc: errors.append(exc)

        def append():
            try:
                acquired = log._lock.acquire(blocking=False)
                lock_states.append(acquired)
                if acquired: log._lock.release()
                attempted.set()
                log.add('event', '清空后的新日志')
                done.set()
            except BaseException as exc: errors.append(exc)

        clear_thread = threading.Thread(target=clear)
        append_thread = threading.Thread(target=append)
        with patch.object(Path, 'write_text', blocked_write):
            try:
                clear_thread.start()
                self.assertTrue(entered.wait(3))
                self.assertEqual([row['text'] for row in log.buf], ['旧日志'], '磁盘写成功前不能清内存')
                append_thread.start()
                self.assertTrue(attempted.wait(3))
                self.assertEqual(lock_states, [False], '磁盘清空过程中必须持有日志锁')
                self.assertFalse(done.is_set())
            finally:
                release.set()
                clear_thread.join(3)
                if append_thread.ident is not None: append_thread.join(3)
        self.assertFalse(clear_thread.is_alive())
        self.assertFalse(append_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual([r['text'] for r in log.tail()], ['清空后的新日志'])
        rows = [json.loads(line) for line in log.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['text'] for r in rows], ['清空后的新日志'])


if __name__ == '__main__':
    unittest.main()
