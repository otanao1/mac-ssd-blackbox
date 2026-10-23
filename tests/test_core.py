import json
import os
from pathlib import Path
import sqlite3
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from blackbox.scan import Scanner, Cancelled, normalize
from blackbox.store import Store
from blackbox.runtime import Monitor, request, lock, DEFAULT_OPTIONS


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        self.tree = self.root / 'tree'
        self.tree.mkdir()
        (self.tree / '.hidden').write_bytes(b'x'*100)
        (self.tree / 'sub').mkdir()
        (self.tree / 'sub' / 'large').write_bytes(b'x'*20000)
        self.store = Store(self.data, create=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def scan(self, targets, layer='L2', options=None, **kwargs):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        rows = []
        scanner = Scanner(db, device_check=lambda p: os.stat(p).st_dev, **kwargs)
        scanner.run(targets, layer, options or DEFAULT_OPTIONS, rows.append)
        return rows, db

    def test_path_notation(self):
        self.assertTrue(normalize('~/*')[1])
        for invalid in ('relative', '/*/x', '/a?b'):
            with self.assertRaises(ValueError):
                normalize(invalid)

    def test_shared_walk_hidden_and_parent_totals(self):
        roots = [str(self.tree)+'/*', str(self.tree/'sub')+'/*']
        rows, db = self.scan(roots)
        bypath = {r['path']: r for r in rows}
        self.assertIn(str(self.tree/'.hidden'), bypath)
        self.assertGreater(bypath[str(self.tree)]['bytes'], 20000)
        self.assertEqual(db.execute('SELECT count(*) FROM nodes').fetchone()[0], 4)
        self.assertEqual(len(bypath), len(rows))

    def test_symlink_is_not_followed(self):
        outside = self.root/'outside'
        outside.mkdir()
        (outside/'secret').write_text('do not traverse')
        (self.tree/'link').symlink_to(outside, target_is_directory=True)
        rows, db = self.scan([str(self.tree)+'/*'])
        self.assertEqual(next(r for r in rows if r['path'].endswith('/link'))['kind'], 'symlink')
        self.assertIsNone(db.execute('SELECT * FROM nodes WHERE path LIKE ?', ('%/secret',)).fetchone())

    def test_mount_boundary_and_explicit_external_denial(self):
        real = os.path.ismount
        with patch('blackbox.scan.os.path.ismount', side_effect=lambda p: p == str(self.tree/'sub') or real(p)):
            rows, db = self.scan([str(self.tree)+'/*'])
        self.assertEqual(next(r for r in rows if r['path'].endswith('/sub'))['status'], 'excluded')
        self.assertFalse(any(r['path'].endswith('/large') and r['status']=='ok' for r in rows))
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        scanner = Scanner(db, device_check=lambda p: (_ for _ in ()).throw(ValueError('external')))
        result = []
        scanner.run([str(self.tree)], 'L2', {}, result.append)
        self.assertEqual(result[0]['status'], 'excluded')

    def test_explicit_internal_mount_is_separate_root(self):
        real = os.path.ismount
        with patch('blackbox.scan.os.path.ismount', side_effect=lambda p: p == str(self.tree/'sub') or real(p)):
            rows, db = self.scan([str(self.tree)+'/*', str(self.tree/'sub')+'/*'])
        self.assertTrue(any(r['path'].endswith('/large') and r['status']=='ok' for r in rows))
        self.assertEqual(db.execute('SELECT count(*) FROM nodes').fetchone()[0], 4)

    def test_hardlink_counted_once(self):
        os.link(self.tree/'sub'/'large', self.tree/'hardlink')
        rows, db = self.scan([str(self.tree)+'/*'])
        self.assertEqual(db.execute("SELECT count(*) FROM nodes WHERE status='deduplicated'").fetchone()[0], 1)

    def test_l9_threshold_depth_and_small_file_stats(self):
        (self.tree/'tiny').write_bytes(b'')
        rows, _ = self.scan([str(self.tree)+'/*'], 'L9', {'threshold': 20000, 'max_depth': 1})
        self.assertTrue(any(r['kind']=='small_files' and r['count'] >= 1 for r in rows))
        self.assertFalse(any(r['path'].endswith('/large') for r in rows))
        rows, _ = self.scan([str(self.tree)+'/*'], 'L9', {'threshold': 20000, 'max_depth': 7})
        self.assertTrue(any(r['path'].endswith('/large') for r in rows))
        totals, _ = self.scan([str(self.tree)], 'L9')
        self.assertEqual(len(totals), 1)

    def test_missing_and_permission_error(self):
        rows, _ = self.scan([str(self.tree/'missing')])
        self.assertEqual(rows[0]['status'], 'missing')
        with patch('blackbox.scan.os.open', side_effect=PermissionError('denied')):
            rows, _ = self.scan([str(self.tree)])
        self.assertEqual(rows[0]['status'], 'error')

    def test_cancel_during_walk(self):
        with self.assertRaises(Cancelled):
            self.scan([str(self.tree)], cancelled=lambda: True)

    def test_waiting_dedup_and_cooldown(self):
        target = str(self.tree)
        request(self.store, 'L9', [target, target])
        request(self.store, 'L9', [target])
        self.assertEqual(len(self.store.rows('SELECT * FROM waiting')), 1)
        self.store.execute('DELETE FROM waiting')
        ident = self.store.create_scan('L9', [target], DEFAULT_OPTIONS)
        self.store.execute("UPDATE scans SET state='succeeded',started=? WHERE id=?", (time.time(), ident))
        self.assertEqual(request(self.store, 'L9', [target])['suppressed'], [target])
        self.assertEqual(request(self.store, 'L9', [target], {'threshold': 1})['waiting'], [target])

    def test_single_monitor_periodic_skip_and_l1_independence(self):
        monitor = Monitor(self.data, {'L2':[str(self.tree)], 'L3':[str(self.tree)]})
        self.addCleanup(monitor.close)
        with self.assertRaises(BlockingIOError):
            Monitor(self.data)
        with patch.object(monitor, 'dispatch'), patch('blackbox.runtime.observe') as observe:
            monitor.tick(now=100)
            observe.assert_called_once()
            scans = self.store.rows('SELECT * FROM scans ORDER BY created')
            self.assertEqual([r['state'] for r in scans], ['pending','skipped'])
            monitor.tick(now=161)
            self.assertEqual(observe.call_count, 2)
            self.assertEqual(len(self.store.rows('SELECT * FROM scans')), 2)

    def wait_child(self, monitor):
        deadline = time.monotonic()+10
        while monitor.child and time.monotonic() < deadline:
            monitor.reap()
            time.sleep(.02)
        self.assertIsNone(monitor.child)

    def test_periodic_loses_dispatch_race_without_queuing(self):
        monitor = Monitor(self.data)
        self.addCleanup(monitor.close)
        periodic = self.store.create_scan('L2', [str(self.tree/'missing')], DEFAULT_OPTIONS, 'periodic')
        request(self.store, 'L9', [str(self.tree/'missing')])
        monitor.dispatch()
        self.wait_child(monitor)
        self.assertEqual(self.store.rows('SELECT state FROM scans WHERE id=?', (periodic,))[0]['state'], 'skipped')
        self.assertFalse(self.store.rows("SELECT * FROM scans WHERE state='pending'"))

    def test_real_child_history_failure_and_recovery(self):
        monitor = Monitor(self.data)
        self.addCleanup(monitor.close)
        ident = request(self.store, 'L2', [str(self.tree/'missing')])['scan_id']
        monitor.dispatch()
        self.assertNotEqual(monitor.child.pid, os.getpid())
        self.wait_child(monitor)
        self.assertEqual(self.store.rows('SELECT state FROM scans WHERE id=?',(ident,))[0]['state'], 'partial')
        self.assertEqual(self.store.rows('SELECT status FROM results WHERE scan_id=?',(ident,))[0]['status'], 'missing')
        orphan = self.store.create_scan('L2', [str(self.tree)], DEFAULT_OPTIONS, state='running')
        monitor.dispatch()
        self.assertEqual(self.store.rows('SELECT state FROM scans WHERE id=?',(orphan,))[0]['state'], 'failed')

    def test_active_list_immutable_and_next_waiting(self):
        monitor = Monitor(self.data)
        self.addCleanup(monitor.close)
        request(self.store, 'L9', [str(self.tree/'missing')])
        monitor.dispatch()
        active = self.store.rows("SELECT * FROM scans WHERE state='running'")[0]
        request(self.store, 'L9', [str(self.tree/'other')])
        self.assertEqual(len(self.store.rows('SELECT * FROM waiting')), 1)
        self.assertEqual(self.store.rows('SELECT targets FROM scans WHERE id=?',(active['id'],))[0]['targets'], active['targets'])
        self.wait_child(monitor)
        monitor.dispatch()
        self.wait_child(monitor)
        self.assertEqual(len(self.store.rows("SELECT * FROM scans WHERE layer='L9'")), 2)

    def test_pid_recording_failure_keeps_child_supervised(self):
        monitor = Monitor(self.data)
        self.addCleanup(monitor.close)
        request(self.store, 'L2', [str(self.tree/'missing')])
        real = monitor.store.execute
        def execute(sql, args=()):
            if sql.startswith('UPDATE scans SET pid='):
                raise sqlite3.OperationalError('simulated storage failure')
            return real(sql, args)
        with patch.object(monitor.store, 'execute', side_effect=execute):
            with self.assertRaises(sqlite3.OperationalError):
                monitor.dispatch()
        self.assertIsNotNone(monitor.child)
        self.assertIsNotNone(monitor.scan_lock)
        self.wait_child(monitor)
        self.assertEqual(self.store.rows('SELECT state FROM scans')[0]['state'], 'partial')

    def test_running_child_cancel_and_abnormal_exit(self):
        monitor = Monitor(self.data)
        self.addCleanup(monitor.close)
        ident = request(self.store, 'L2', [str(self.tree/'missing')])['scan_id']
        monitor.dispatch()
        monitor.child.send_signal(signal.SIGSTOP)
        self.store.execute('UPDATE scans SET cancel=1 WHERE id=?', (ident,))
        monitor.child.send_signal(signal.SIGCONT)
        self.wait_child(monitor)
        self.assertEqual(self.store.rows('SELECT state FROM scans WHERE id=?', (ident,))[0]['state'], 'cancelled')
        ident = request(self.store, 'L2', [str(self.tree/'missing')])['scan_id']
        monitor.dispatch()
        monitor.child.kill()
        self.wait_child(monitor)
        self.assertEqual(self.store.rows('SELECT state FROM scans WHERE id=?', (ident,))[0]['state'], 'failed')

    def test_lock_is_inherited_by_surviving_process(self):
        monitor = Monitor(self.data)
        self.addCleanup(monitor.close)
        fd = lock(self.data, 'scan.lock')
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'], pass_fds=(fd,))
        os.close(fd)
        try:
            request(self.store, 'L2', [str(self.tree/'missing')])
            monitor.dispatch()
            self.assertIsNone(monitor.child)
        finally:
            child.terminate()
            child.wait(timeout=5)
        monitor.dispatch()
        self.wait_child(monitor)

    def test_pending_cancel_and_child_start_failure(self):
        monitor = Monitor(self.data)
        self.addCleanup(monitor.close)
        ident = request(self.store, 'L2', [str(self.tree)])['scan_id']
        self.store.execute('UPDATE scans SET cancel=1 WHERE id=?',(ident,))
        monitor.dispatch()
        self.assertEqual(self.store.rows('SELECT state FROM scans WHERE id=?',(ident,))[0]['state'], 'cancelled')
        ident = request(self.store, 'L2', [str(self.tree)])['scan_id']
        with patch('blackbox.runtime.subprocess.Popen', side_effect=OSError('failed to start')):
            with self.assertRaises(OSError):
                monitor.dispatch()
        self.assertEqual(self.store.rows('SELECT state FROM scans WHERE id=?',(ident,))[0]['state'], 'failed')

    def test_scan_lock_surviving_worker_blocks_new_dispatch(self):
        monitor = Monitor(self.data)
        self.addCleanup(monitor.close)
        fd = lock(self.data, 'scan.lock')
        try:
            ident = request(self.store, 'L2', [str(self.tree)])['scan_id']
            monitor.dispatch()
            self.assertIsNone(monitor.child)
            self.assertEqual(self.store.rows('SELECT state FROM scans WHERE id=?',(ident,))[0]['state'], 'pending')
        finally:
            os.close(fd)

    def test_terminal_harness(self):
        def command(*args):
            result = subprocess.run([sys.executable, '-m', 'blackbox', '--data', str(self.data), *args],
                                    check=True, capture_output=True, text=True, timeout=5)
            return json.loads(result.stdout)
        ident = command('scan', 'L2', str(self.tree/'missing'))['scan_id']
        self.assertEqual(command('status')['scans'][0]['id'], ident)
        self.assertEqual(command('cancel', ident)['updated'], 1)
        command('scan', 'L9', str(self.tree))
        self.assertEqual(len(command('status')['waiting']), 1)
        self.assertEqual(command('clear-waiting')['removed'], 1)
        self.assertEqual(command('results', ident), [])
        self.assertEqual(command('history')['scans'][0]['id'], ident)

    def test_missing_database_does_not_create_fallback(self):
        empty = self.root/'empty'
        empty.mkdir()
        with self.assertRaises(sqlite3.OperationalError):
            Store(empty)
        self.assertFalse((empty/'blackbox.sqlite3').exists())

    def test_data_destination_replacement_stops_writes(self):
        self.data.rename(self.root/'old')
        self.data.mkdir()
        with self.assertRaises(OSError):
            self.store.rows('SELECT * FROM scans')

if __name__ == '__main__':
    unittest.main()
