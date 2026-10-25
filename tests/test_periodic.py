"""Issue #24 acceptance checks against synthetic stores and controlled clocks."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from blackbox.runtime import Monitor, DEFAULT_OPTIONS, next_periodic, request
from blackbox.store import Store


class PeriodicTest(unittest.TestCase):
    def setUp(self):
        self.old_tz = os.environ.get('TZ')
        os.environ['TZ'] = 'Asia/Tokyo'
        time.tzset()
        self.addCleanup(self.restore_tz)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.data.chmod(0o700)
        Store(self.data, create=True).close()
        self.monitor = Monitor(self.data, {'L2': ['/synthetic/l2'], 'L3': ['/synthetic/l3']})
        self.addCleanup(lambda: self.monitor.close())
        self.store = self.monitor.store

    def restore_tz(self):
        if self.old_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = self.old_tz
        time.tzset()

    def at(self, value):
        return time.mktime(time.strptime(value, '%Y-%m-%d %H:%M:%S'))

    def tick(self, value):
        now = self.at(value)
        with patch('blackbox.runtime.observe') as observe, patch.object(self.monitor, 'dispatch'), patch('blackbox.runtime.time.time', return_value=now):
            self.monitor.tick(now)
        return observe

    def rows(self):
        return self.store.rows('SELECT * FROM scans ORDER BY created,id')

    def due(self, layer):
        return float(self.store.rows('SELECT value FROM meta WHERE key=?', ('due_' + layer,))[0]['value'])

    def test_fixed_slots_initialization_l1_and_sleep_coalescing(self):
        self.tick('2026-10-25 06:59:00').assert_called_once()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.due('L2'), self.at('2026-10-25 07:00:00'))
        self.assertEqual(self.due('L3'), self.at('2026-10-25 07:10:00'))
        self.tick('2026-10-25 06:59:59').assert_not_called()
        self.tick('2026-10-25 07:00:00').assert_called_once()
        self.tick('2026-10-25 07:10:00')
        self.assertEqual([(r['layer'], r['state']) for r in self.rows()], [('L2', 'pending'), ('L3', 'pending')])
        self.tick('2026-10-28 09:45:00')
        self.assertEqual(len(self.rows()), 4)  # one duplicate record per layer, no catch-up backlog
        self.assertEqual([r['state'] for r in self.rows()][2:], ['skipped', 'skipped'])
        self.assertEqual(self.due('L2'), self.at('2026-10-29 07:00:00'))
        self.assertEqual(self.due('L3'), self.at('2026-10-28 10:10:00'))
        self.tick('2026-10-28 09:45:01')
        self.assertEqual(len(self.rows()), 4)

    def test_running_periodic_allows_one_scheduled_pending_but_manual_suppresses(self):
        self.tick('2026-10-25 06:59:00')
        first = self.monitor.request_periodic_l2()['scan_id']
        self.store.execute("UPDATE scans SET state='running',started=? WHERE id=?", (self.at('2026-10-25 06:59:00'), first))
        self.assertTrue(self.monitor.request_periodic_l2()['suppressed'])
        self.tick('2026-10-25 07:00:00')
        self.assertCountEqual([r['state'] for r in self.rows()], ['running', 'pending'])
        self.tick('2026-10-26 07:00:00')
        self.assertEqual(len([r for r in self.rows() if r['layer'] == 'L2' and r['state'] == 'pending']), 1)
        self.assertEqual(next(r for r in self.rows() if r['id'] == first)['state'], 'running')

    def test_manual_periodic_targets_options_trigger_and_deadlines(self):
        self.tick('2026-10-25 06:59:00')
        deadlines = self.store.rows('SELECT * FROM meta WHERE key LIKE \'due_%\' ORDER BY key')
        first = self.monitor.request_periodic_l2()['scan_id']
        row = self.store.rows('SELECT * FROM scans WHERE id=?', (first,))[0]
        self.assertEqual((row['trigger'], json.loads(row['targets']), json.loads(row['options'])),
                         ('periodic', ['/synthetic/l2'], DEFAULT_OPTIONS))
        self.assertEqual(self.monitor.request_periodic_l2(), dict(scan_id=first, suppressed=True))
        self.assertEqual(self.store.rows('SELECT * FROM meta WHERE key LIKE \'due_%\' ORDER BY key'), deadlines)
        self.store.execute("UPDATE scans SET state='succeeded' WHERE id=?", (first,))
        self.assertNotEqual(self.monitor.request_periodic_l2()['scan_id'], first)
        self.monitor.targets['L2'] = []
        with self.assertRaisesRegex(ValueError, 'no periodic L2 targets'):
            self.monitor.request_periodic_l2()

    def test_manual_dedup_normalized_target_set_layer_and_terminal_states(self):
        for layer in ('L2', 'L3'):
            for state in ('pending', 'running', 'succeeded', 'partial', 'failed', 'cancelled', 'skipped'):
                with self.subTest(layer=layer, state=state):
                    self.store.execute('DELETE FROM scans')
                    ident = request(self.store, layer, ['/synthetic/a', '/synthetic/b'])['scan_id']
                    self.store.execute('UPDATE scans SET state=? WHERE id=?', (state, ident))
                    result = request(self.store, layer, ['/synthetic/b', '/synthetic/a', '/synthetic/a'], {'threshold': 1})
                    self.assertEqual(result['scan_id'] == ident, state in ('pending', 'running'))
        request(self.store, 'L2', ['/synthetic/a'])
        self.assertNotIn('suppressed', request(self.store, 'L3', ['/synthetic/a']))
        self.assertNotIn('suppressed', request(self.store, 'L2', ['/synthetic/a/*']))

    def test_concurrent_manual_requests_have_only_one_pending(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        def submit():
            store = Store(self.data)
            try:
                barrier.wait(timeout=5)
                results.append(request(store, 'L2', ['/synthetic/a']))
            except BaseException as exc:
                errors.append(exc)
            finally:
                store.close()
        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(len({r['scan_id'] for r in results}), 1)
        self.assertEqual(len(self.rows()), 1)

    def test_registration_failure_rolls_back_deadline_and_recovers(self):
        self.tick('2026-10-25 06:59:00')
        due = self.due('L2')
        create_scan = self.store.create_scan
        def fail_after_insert(*args, **kwargs):
            create_scan(*args, **kwargs)
            raise sqlite3.OperationalError('offline')
        with patch.object(self.store, 'create_scan', side_effect=fail_after_insert):
            with self.assertRaises(sqlite3.OperationalError):
                self.tick('2026-10-25 07:00:00')
        self.assertEqual(self.due('L2'), due)
        self.assertEqual(self.rows(), [])
        self.tick('2026-10-25 07:03:00')
        self.assertEqual(self.rows()[0]['state'], 'pending')
        self.assertIsNone(self.monitor.recording_error)
        self.assertEqual(self.due('L2'), self.at('2026-10-26 07:00:00'))

    def test_restart_retains_pending_deadlines_and_migrates_legacy_deadlines(self):
        self.tick('2026-10-25 07:00:00')
        ident = self.rows()[0]['id']
        self.monitor.close()
        self.monitor = Monitor(self.data, {'L2': ['/synthetic/l2'], 'L3': ['/synthetic/l3']})
        self.store = self.monitor.store
        self.tick('2026-10-25 07:01:00')
        self.assertEqual(self.rows()[0]['id'], ident)
        self.assertEqual(self.due('L2'), self.at('2026-10-26 07:00:00'))
        self.store.execute("INSERT OR REPLACE INTO meta VALUES('due_L3',?)", (str(self.at('2026-10-25 08:37:00')),))
        self.tick('2026-10-25 07:02:00')
        self.assertEqual(self.due('L3'), self.at('2026-10-25 07:10:00'))
        self.store.execute("INSERT OR REPLACE INTO meta VALUES('due_L3',?)", (str(self.at('2026-10-25 06:37:00')),))
        self.tick('2026-10-25 07:03:00')
        self.assertEqual(len([r for r in self.rows() if r['layer'] == 'L3']), 1)

    def test_dispatch_preserves_fifo_incident_and_periodic_across_midnight(self):
        created = self.at('2026-10-25 23:59:00')
        with patch('blackbox.runtime.time.time', return_value=created):
            first = self.store.create_scan('L3', ['/synthetic/incident'], DEFAULT_OPTIONS, 'incident')
        with patch('blackbox.runtime.time.time', return_value=created + 1):
            periodic = self.monitor.request_periodic_l2()['scan_id']
        request(self.store, 'L9', ['/synthetic/l9'])
        started = self.at('2026-10-26 00:01:00')
        with patch('blackbox.runtime.subprocess.Popen') as popen, patch('blackbox.runtime.time.time', return_value=started):
            popen.return_value.pid = 123
            popen.return_value.poll.return_value = None
            self.monitor.dispatch()
            l9 = self.monitor.active_id
            self.assertEqual(self.store.rows('SELECT layer FROM scans WHERE id=?', (l9,))[0]['layer'], 'L9')
            self.monitor.dispatch()
            self.assertEqual(popen.call_count, 1)  # no preemption
            for expected in (first, periodic):
                self.store.execute("UPDATE scans SET state='succeeded' WHERE id=?", (self.monitor.active_id,))
                popen.return_value.poll.return_value = 0
                self.monitor.reap()
                popen.return_value.poll.return_value = None
                self.monitor.dispatch()
                self.assertEqual(self.monitor.active_id, expected)
            row = self.store.rows('SELECT * FROM scans WHERE id=?', (periodic,))[0]
            self.assertEqual((row['created'], row['started']), (created + 1, started))
            self.assertEqual(self.due_if_any(), [])  # dispatch does not change schedule
            self.store.execute("UPDATE scans SET state='succeeded' WHERE id=?", (periodic,))
            popen.return_value.poll.return_value = 0
            self.monitor.reap()

    def due_if_any(self):
        return self.store.rows("SELECT * FROM meta WHERE key IN ('due_L2','due_L3')")

    def test_fixed_clock_uses_local_time_and_dst(self):
        self.assertEqual(next_periodic('L2', self.at('2026-10-25 07:00:00')), self.at('2026-10-26 07:00:00'))
        self.assertEqual(next_periodic('L3', self.at('2026-10-25 07:10:00')), self.at('2026-10-25 08:10:00'))
        os.environ['TZ'] = 'America/New_York'
        time.tzset()
        before = self.at('2026-03-08 01:10:00')
        self.assertEqual(time.strftime('%H:%M', time.localtime(next_periodic('L3', before))), '03:10')
        before = self.at('2026-03-07 07:00:00')
        self.assertEqual(next_periodic('L2', before) - before, 23 * 3600)
