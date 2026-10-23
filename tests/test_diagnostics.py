import errno
import json
import signal
import sqlite3
import subprocess
import unittest
from unittest.mock import Mock, patch

from blackbox import diagnostics, runtime, service

IDENT = 'a' * 32


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.runner = patch('blackbox.diagnostics.subprocess.run').start()
        self.addCleanup(patch.stopall)
        self.runner.return_value.returncode = 0

    def payload(self):
        return json.loads(self.runner.call_args.args[0][-1])

    def test_public_payload_excludes_private_exception_and_fields(self):
        exc = OSError(errno.EACCES, 'private@example.org', '/Users/private/secret')
        self.assertTrue(diagnostics.emit('recording_suspended', error=exc,
                        scan_id=IDENT, path='/Users/private', reason='secret', stage='tick'))
        payload = self.payload()
        self.assertEqual(payload['errno_name'], 'EACCES')
        self.assertEqual(payload['scan_id'], IDENT)
        self.assertEqual(payload['exception_type'], 'OSError')
        encoded = str(self.runner.call_args)
        for secret in ('private@example.org', '/Users/private', 'secret'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(self.runner.call_args.kwargs['timeout'], 1)
        self.assertIn(diagnostics.SUBSYSTEM, self.runner.call_args.args[0])

    def test_sqlite_code_is_preserved_without_message(self):
        try:
            sqlite3.connect(':memory:').execute('SELECT * FROM secret_table')
        except sqlite3.Error as exc:
            diagnostics.emit('recording_suspended', error=exc)
        self.assertEqual(self.payload()['sqlite_errorname'], 'SQLITE_ERROR')
        self.assertNotIn('secret_table', str(self.runner.call_args))

    def test_unknown_exception_class_and_invalid_id_not_exposed(self):
        secret_type = type('private_account', (Exception,), {})
        diagnostics.emit('worker_finalize_failed', error=secret_type('/secret'), scan_id='/secret')
        self.assertEqual(self.payload()['exception_type'], 'Exception')
        self.assertNotIn('scan_id', self.payload())
        self.assertNotIn('private_account', str(self.runner.call_args))

    def test_logging_failures_are_contained(self):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired('log', 1), OSError()):
            self.runner.side_effect = failure
            self.assertFalse(diagnostics.emit('recording_suspended'))
            self.assertFalse(diagnostics.available())
        self.runner.side_effect = None
        self.runner.return_value.returncode = 64
        self.assertFalse(diagnostics.emit('recording_suspended'))

    def test_unknown_event_is_not_sent(self):
        self.assertFalse(diagnostics.emit('/private/path'))
        self.runner.assert_not_called()

    def test_recovery_uses_default_level_and_origin_metadata(self):
        diagnostics.emit('recording_resumed')
        self.assertIn('default', self.runner.call_args.args[0])
        self.assertIn('origin_pid', self.payload())
        self.assertIn('occurred_at', self.payload())


class RuntimeDiagnosticsTests(unittest.TestCase):
    def monitor(self):
        m = runtime.Monitor.__new__(runtime.Monitor)
        m.recording_error = None
        m.active_id = IDENT
        m.child = Mock(pid=123)
        m.child.poll.return_value = None
        m.notify = Mock()
        m.store = Mock()
        m._tick = Mock(side_effect=sqlite3.OperationalError('database is locked'))
        return m

    @patch('blackbox.runtime.diagnostic')
    def test_exception_interrupt_recovery_and_no_repeat(self, log):
        m = self.monitor()
        with self.assertRaises(sqlite3.Error):
            m.tick()
        self.assertEqual([c.args[0] for c in log.call_args_list],
                         ['recording_suspended', 'worker_interrupt_requested'])
        m.child.terminate.assert_called_once()
        with self.assertRaises(sqlite3.Error):
            m.tick()
        self.assertEqual(log.call_count, 2)
        m._tick.side_effect = None
        m.tick()
        self.assertEqual(log.call_args.args[0], 'recording_resumed')
        self.assertIsNone(m.recording_error)
        m.store.reconnect.assert_called()

    @patch('blackbox.runtime.diagnostic')
    def test_termination_failure_keeps_original_behavior_and_diagnostic(self, log):
        m = self.monitor()
        m.child.terminate.side_effect = ProcessLookupError()
        with self.assertRaises(ProcessLookupError):
            m.tick()
        self.assertEqual(log.call_args.args[0], 'worker_interrupt_failed')

    @patch('blackbox.runtime.diagnostic')
    @patch('blackbox.runtime.os.close')
    @patch('blackbox.runtime.disk_io', return_value=None)
    @patch('blackbox.runtime.DuScanner')
    @patch('blackbox.runtime.Store')
    @patch('blackbox.runtime.signal.signal')
    def test_worker_signal_and_user_cancel_distinguished(self, signals, store, scanner, io, close, log):
        for signalled in (False, True):
            log.reset_mock()
            db = store.return_value
            db.rows.side_effect = lambda sql, args=(): ([{'targets': '["/synthetic"]', 'layer': 'L2', 'options': '{}'}]
                if 'SELECT * FROM scans' in sql else [{'cancel': 0}])
            def scan(*args):
                if signalled:
                    signals.call_args_list[-1].args[1](signal.SIGTERM, None)
                raise runtime.Cancelled()
            scanner.return_value.run.side_effect = scan
            runtime.worker('/synthetic', IDENT, 7)
            event = log.call_args
            self.assertEqual(event.kwargs['state'], 'cancelled')
            self.assertEqual(event.kwargs['reason'], 'signal' if signalled else 'user_cancel')
            self.assertEqual(event.kwargs['signal'], signal.SIGTERM if signalled else None)

    @patch('blackbox.diagnostics.subprocess.run', side_effect=FileNotFoundError())
    def test_backend_failure_does_not_change_monitor_error_or_termination(self, runner):
        m = self.monitor()
        with self.assertRaisesRegex(sqlite3.OperationalError, 'database is locked'):
            m.tick()
        m.child.terminate.assert_called_once()
        self.assertEqual(m.recording_error, 'database is locked')

    @patch('blackbox.runtime.diagnostic')
    @patch('blackbox.runtime.disk_io', return_value=None)
    @patch('blackbox.runtime.Store', side_effect=OSError(errno.EIO, 'private'))
    def test_worker_start_failure_is_logged_and_propagated(self, store, io, log):
        with self.assertRaises(OSError):
            runtime.worker('/synthetic', IDENT, 7)
        self.assertEqual(log.call_args.args[0], 'worker_start_failed')
        self.assertEqual(log.call_args.kwargs['scan_id'], IDENT)

    @patch('blackbox.runtime.diagnostic')
    @patch('blackbox.runtime.os.close')
    @patch('blackbox.runtime.disk_io', return_value=None)
    @patch('blackbox.runtime.DuScanner')
    @patch('blackbox.runtime.Store')
    @patch('blackbox.runtime.signal.signal')
    def test_worker_finalize_failure_is_logged_even_when_db_unwritable(self, signals, store, scanner, io, close, log):
        db = store.return_value
        db.rows.side_effect = [[{'targets': '["/synthetic"]', 'layer': 'L2', 'options': '{}'}], [{'cancel': 0}], [{'cancel': 0}], []]
        db.execute.side_effect = sqlite3.OperationalError('database is locked')
        with self.assertRaises(sqlite3.Error):
            runtime.worker('/synthetic', IDENT, 7)
        self.assertEqual(log.call_args.args[0], 'worker_finalize_failed')
        db.close.assert_called_once()
        close.assert_called_once_with(7)

    @patch('blackbox.service.diagnostic')
    @patch('blackbox.service.Monitor', side_effect=OSError('private path'))
    def test_service_initialization_failure_is_logged_once(self, monitor, log):
        s = service.Service.__new__(service.Service)
        s.monitor = None
        s.error = None
        s.retry = 0
        s.config = dict(storage={'path': '/synthetic'}, targets={}, intervals={}, l1_path='/synthetic', retention={}, incident_thresholds={})
        with patch('builtins.print'):
            s.tick()
            s.retry = 0
            s.tick()
        log.assert_called_once()
        self.assertEqual(log.call_args.args[0], 'service_initialization_failed')


if __name__ == '__main__':
    unittest.main()
