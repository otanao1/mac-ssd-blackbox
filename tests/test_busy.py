import errno
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from blackbox.runtime import Monitor, observe
from blackbox.service import Service
from blackbox.store import Store, is_busy


def busy(code=sqlite3.SQLITE_BUSY):
    exc = sqlite3.OperationalError('synthetic conflict')
    exc.sqlite_errorcode = code
    return exc


class BusyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        store = Store(self.data, create=True)
        store.close()
        self.log = patch('blackbox.runtime.diagnostic').start()
        self.addCleanup(patch.stopall)

    def monitor(self):
        m = Monitor(self.data, l1_path=str(self.data), notify=Mock())
        def close():
            m.child = None
            m.close()
        self.addCleanup(close)
        return m

    def test_classification_uses_primary_code_not_text(self):
        self.assertTrue(is_busy(busy()))
        self.assertTrue(is_busy(busy(sqlite3.SQLITE_BUSY | (2 << 8))))
        self.assertFalse(is_busy(busy(sqlite3.SQLITE_LOCKED)))
        self.assertFalse(is_busy(sqlite3.OperationalError('database is locked')))
        self.assertFalse(is_busy(OSError(errno.EIO, 'SQLITE_BUSY')))

    def test_real_write_lock_preserves_child_and_due_then_resumes(self):
        m = self.monitor()
        ident = m.store.create_scan('L2', ['/synthetic'], {}, state='running')
        m.active_id = ident
        m.child = Mock(pid=123)
        m.child.poll.return_value = None
        for key, value in [('due_L1', '0'), ('due_L2', str(time.time()+86400)),
                           ('due_L3', str(time.time()+3600)), ('due_retention', str(time.time()+86400))]:
            m.store.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, value))
        locker = sqlite3.connect(self.data/'blackbox.sqlite3')
        try:
            locker.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError) as caught:
                m.tick()
            self.assertTrue(is_busy(caught.exception))
            self.assertTrue(m.database_busy)
            self.assertIsNone(m.recording_error)
            m.child.terminate.assert_not_called()
            self.assertEqual(m.store.rows("SELECT value FROM meta WHERE key='due_L1'")[0]['value'], '0')
            self.assertFalse(m.store.rows('SELECT * FROM l1'))
            locker.rollback()
            m.tick()
            self.assertFalse(m.database_busy)
            self.assertEqual(len(m.store.rows('SELECT * FROM l1')), 1)
            self.assertGreater(float(m.store.rows("SELECT value FROM meta WHERE key='due_L1'")[0]['value']), 0)
            self.assertEqual(m.store.rows('SELECT state FROM scans WHERE id=?', (ident,))[0]['state'], 'running')
            self.assertEqual([c.args[0] for c in self.log.call_args_list], ['database_contended', 'database_resumed'])
        finally:
            locker.close()

    def test_l1_record_and_deadline_rollback_together(self):
        m = self.monitor()
        m.store.execute("INSERT INTO meta VALUES('due_L1','0')")
        def authorize(action, arg1, arg2, database, source):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and arg1 == 'meta' else sqlite3.SQLITE_OK
        m.store.db.set_authorizer(authorize)
        with self.assertRaises(sqlite3.DatabaseError):
            observe(m.store, str(self.data), time.time()+60)
        m.store.db.set_authorizer(None)
        self.assertFalse(m.store.rows('SELECT * FROM l1'))
        self.assertEqual(m.store.rows("SELECT value FROM meta WHERE key='due_L1'")[0]['value'], '0')

    def test_periodic_request_and_deadline_rollback_together(self):
        m = self.monitor()
        m.targets = {'L2': ['/synthetic']}
        m.child = Mock()
        m.child.poll.return_value = None
        m.active_id = m.store.create_scan('L2', ['/active'], {}, state='running')
        m.store.execute("INSERT INTO meta VALUES('due_L1',?)", (str(time.time()+60),))
        def authorize(action, arg1, arg2, database, source):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and arg1 == 'meta' else sqlite3.SQLITE_OK
        m.store.db.set_authorizer(authorize)
        with self.assertRaises(sqlite3.DatabaseError):
            m._tick()
        m.store.db.set_authorizer(None)
        self.assertEqual(len(m.store.rows('SELECT * FROM scans')), 1)
        self.assertFalse(m.store.rows("SELECT value FROM meta WHERE key='due_L2'"))

    def test_repeated_busy_is_suppressed_and_does_not_reconnect(self):
        m = self.monitor()
        m._tick = Mock(side_effect=busy())
        with patch.object(m.store, 'reconnect') as reconnect:
            for _ in range(2):
                with self.assertRaises(sqlite3.Error):
                    m.tick()
            reconnect.assert_not_called()
        self.log.assert_called_once()
        self.assertIsNone(m.recording_error)

    def test_incident_marker_rolls_back_on_busy_and_retries(self):
        m = self.monitor()
        now = time.time()
        m.store.execute('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)',
                        (now, '/synthetic', 100*1024**3, 80*1024**3, 20*1024**3, 20*1024**3, 'synthetic', None))
        for key in ('due_L1', 'due_L2', 'due_L3', 'due_retention'):
            m.store.execute('INSERT INTO meta VALUES(?,?)', (key, str(now+86400)))
        connection = m.store.db
        class Conflict:
            def __getattr__(self, name):
                return getattr(connection, name)
            def __enter__(self):
                connection.__enter__()
                return self
            def __exit__(self, *args):
                return connection.__exit__(*args)
            def execute(self, sql, args=()):
                if sql.startswith('SELECT * FROM l1 WHERE path='):
                    raise busy()
                return connection.execute(sql, args)
        with patch.object(m.store, 'db', Conflict()):
            with self.assertRaises(sqlite3.Error):
                m.tick()
        self.assertFalse(m.store.rows("SELECT * FROM meta WHERE key='incident_l1_time'"))
        m.tick()
        self.assertEqual(float(m.store.rows("SELECT value FROM meta WHERE key='incident_l1_time'")[0]['value']), now)
        self.assertFalse(m.database_busy)
        self.assertFalse(m.store.rows('SELECT * FROM incidents'))

    def test_busy_cannot_mask_destination_failure(self):
        m = self.monitor()
        m.child = Mock(pid=123)
        m.child.poll.return_value = None
        m._tick = Mock(side_effect=busy())
        with patch.object(m.store, 'check', side_effect=OSError(errno.EIO, 'unavailable')):
            with self.assertRaises(OSError):
                m.tick()
        m.child.terminate.assert_called_once()
        self.assertIsNotNone(m.recording_error)
        self.assertFalse(m.database_busy)

    def test_busy_then_io_failure_preserves_safety(self):
        m = self.monitor()
        m.child = Mock(pid=123)
        m.child.poll.return_value = None
        m._tick = Mock(side_effect=[busy(), OSError(errno.EIO, 'unavailable')])
        with self.assertRaises(sqlite3.Error):
            m.tick()
        with self.assertRaises(OSError):
            m.tick()
        m.child.terminate.assert_called_once()
        self.assertFalse(m.database_busy)

    def service(self, m):
        s = Service.__new__(Service)
        s.monitor = m
        s.error = None
        s.retry = 0
        s.database_busy = False
        s.started = time.time()
        s.config = {'sources': {}}
        return s

    def test_service_retry_and_status_distinguish_busy(self):
        m = self.monitor()
        m._tick = Mock(side_effect=[busy(), None])
        s = self.service(m)
        s.tick()
        self.assertTrue(s.database_busy)
        self.assertIsNone(s.error)
        with patch.object(m.store, 'rows', side_effect=busy()):
            status = s.handle({'command': 'status'})
        self.assertTrue(status['database_busy'])
        self.assertIsNone(status['recording_error'])
        calls = m._tick.call_count
        s.tick()
        self.assertEqual(m._tick.call_count, calls)
        s.retry = 0
        s.tick()
        self.assertFalse(s.database_busy)
        self.assertFalse(m.database_busy)
        self.assertIsNone(s.error)
